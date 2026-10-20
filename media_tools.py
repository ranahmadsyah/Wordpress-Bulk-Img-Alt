"""WordPress media fetch and usage helpers; no Streamlit dependencies."""
import html
import re
from urllib.parse import unquote, urlsplit

import requests

def featured_media_id(post):
    """Support normal WordPress fields and CMS responses that expose only links."""
    value = post.get('featured_media')
    if isinstance(value, int) and value > 0:
        return value
    for relation in post.get('_links', {}).get('wp:featuredmedia', []):
        match = re.search(r'/wp/v2/media/(\d+)/?$', urlsplit(relation.get('href', '')).path)
        if match:
            return int(match.group(1))
    return None


def _post_link(site, post):
    if post.get('link'):
        return post['link']
    if post.get('id'):
        return f"{site.rstrip('/')}/wp-admin/post.php?post={int(post['id'])}&action=edit"
    return ''


def fetch_featured_media(site, auth, progress=None):
    """Fetch only distinct featured images of published posts."""
    base = site.rstrip('/') + '/wp-json/wp/v2'
    featured = {}
    with requests.Session() as session:
        session.auth = auth
        for endpoint in ('posts',):
            page = 1
            while True:
                if progress:
                    progress(f'Memeriksa featured image: {endpoint}, halaman {page}...')
                response = session.get(
                    f'{base}/{endpoint}',
                    params={'per_page': 100, 'page': page, 'status': 'publish',
                            '_fields': 'id,title,link,featured_media,_links,featured_image_url'}, timeout=30)
                response.raise_for_status()
                posts = response.json()
                for post in posts:
                    mid = featured_media_id(post)
                    if not mid and post.get('featured_image_url'):
                        raise ValueError('CMS menyediakan URL featured image tetapi ID/relasi medianya tidak tersedia. Pemeriksaan belum lengkap.')
                    if mid:
                        featured.setdefault(mid, []).append({
                            'title': html.unescape(re.sub(r'<[^>]*>', '', post.get('title', {}).get('rendered', ''))) or '(Tanpa judul)',
                            'link': _post_link(site, post), 'type': endpoint, 'featured': True,
                        })
                if not posts or page >= int(response.headers.get('X-WP-TotalPages', 1)):
                    break
                page += 1
        ids = list(featured)
        media = []
        for start in range(0, len(ids), 100):
            if progress:
                progress(f'Mengambil featured image {start + 1}-{min(start + 100, len(ids))} dari {len(ids)}...')
            response = session.get(
                f'{base}/media', params={'include': ','.join(map(str, ids[start:start + 100])),
                                        'per_page': 100, 'orderby': 'include'}, timeout=30)
            response.raise_for_status()
            for item in response.json():
                if item['id'] in featured and item.get('mime_type', '').startswith('image/'):
                    item['_featured_in'] = featured[item['id']]
                    media.append(item)
        return media


def fetch_all_media(site, auth, progress=None):
    """Fetch the full media collection in batches of 100."""
    media, page = [], 1
    with requests.Session() as session:
        session.auth = auth
        while True:
            if progress:
                progress(f'Mengambil semua media: halaman {page}, {len(media)} item termuat...')
            response = session.get(
                site.rstrip('/') + '/wp-json/wp/v2/media',
                params={'per_page': 100, 'page': page}, timeout=30)
            response.raise_for_status()
            batch = response.json()
            media.extend(batch)
            if not batch or page >= int(response.headers.get('X-WP-TotalPages', 1)):
                break
            page += 1
    return media


def fetch_usage(site, auth, media):
    """Scan published posts/pages; return references and endpoint failures separately."""
    by_id = {m['id']: [] for m in media}
    urls = {}
    for m in media:
        variants = [m.get('source_url', '')]
        details = m.get('media_details', {})
        variants += [s.get('source_url', '') for s in details.get('sizes', {}).values()]
        if details.get('original_image') and m.get('source_url'):
            variants.append(m['source_url'].rsplit('/', 1)[0] + '/' + details['original_image'])
        for url in variants:
            if url:
                urls.setdefault(unquote(urlsplit(url).path), set()).add(m['id'])
    failures = []
    for endpoint in ('posts', 'pages'):
        page = 1
        try:
            while True:
                response = requests.get(
                    f'{site.rstrip("/")}/wp-json/wp/v2/{endpoint}', auth=auth,
                    params={'per_page': 100, 'page': page, 'status': 'publish',
                            '_fields': 'id,title,link,content,featured_media,_links'}, timeout=30)
                response.raise_for_status()
                batch = response.json()
                for post in batch:
                    content = html.unescape(post.get('content', {}).get('rendered', ''))
                    matched = set()
                    featured = featured_media_id(post)
                    if featured in by_id:
                        matched.add(featured)
                    matched.update(int(x) for x in re.findall(r'\bwp-image-(\d+)\b', content))
                    # Exact URL paths avoid matching a.jpg inside another filename.
                    for url in re.findall(r'(?:https?:)?//[^\s<>"\']+|/wp-content/[^\s<>"\']+', content):
                        matched.update(urls.get(unquote(urlsplit(url).path), ()))
                    for gallery in re.findall(r'\[gallery\b[^\]]*\bids=["\']([\d,\s]+)["\']', content):
                        matched.update(int(x) for x in re.findall(r'\d+', gallery))
                    for mid in matched & by_id.keys():
                        by_id[mid].append({
                            'title': html.unescape(re.sub(r'<[^>]*>', '', post.get('title', {}).get('rendered', ''))) or '(Tanpa judul)',
                            'link': _post_link(site, post), 'type': endpoint,
                            'featured': mid == featured,
                        })
                if not batch or page >= int(response.headers.get('X-WP-TotalPages', 1)):
                    break
                page += 1
        except (requests.RequestException, ValueError):
            failures.append(endpoint)
    return by_id, failures
