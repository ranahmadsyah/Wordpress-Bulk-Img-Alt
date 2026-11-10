"""WordPress media fetch and usage helpers; no Streamlit dependencies."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

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


class _ContentImages(HTMLParser):
    """Read only image markup, not navigation, links, or arbitrary content URLs."""
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.urls = set()
        self.in_picture = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'picture':
            self.in_picture = True
        if tag == 'img':
            self.ids.update(int(mid) for mid in re.findall(r'\bwp-image-(\d+)\b', attrs.get('class', '')))
        if tag != 'img' and not (tag == 'source' and self.in_picture):
            return
        for key in ('src', 'data-src', 'data-lazy-src', 'data-original'):
            if attrs.get(key):
                self.urls.add(attrs[key])
        for key in ('srcset', 'data-srcset', 'data-lazy-srcset'):
            for entry in attrs.get(key, '').split(','):
                if entry.strip():
                    self.urls.add(entry.strip().split()[0])

    def handle_endtag(self, tag):
        if tag == 'picture':
            self.in_picture = False


def _image_paths(item):
    urls = [item.get('source_url', '')]
    details = item.get('media_details', {})
    urls += [size.get('source_url', '') for size in details.get('sizes', {}).values()]
    if details.get('original_image') and urls[0]:
        urls.append(urljoin(urls[0], details['original_image']))
    return {unquote(urlsplit(url).path) for url in urls if url}


def fetch_url_media(site, auth, page_url, progress=None):
    """Return this published post/page's library images and unmatched image URLs.

    Query only the configured CMS. The supplied URL is a lookup key, never an
    authenticated request destination. Require a matching permalink to avoid
    selecting a different page with the same final slug.
    """
    page_url = page_url.strip()
    target = urlsplit(page_url)
    if target.scheme not in ('http', 'https') or not target.hostname or target.username or target.password:
        raise ValueError('Masukkan URL artikel/halaman HTTP atau HTTPS yang valid.')
    query = parse_qs(target.query)
    post_id = query.get('p', query.get('page_id', ['']))[0]
    slug = unquote(target.path.rstrip('/').rsplit('/', 1)[-1])
    if not post_id and not slug:
        raise ValueError('Masukkan URL artikel/halaman, bukan alamat beranda.')
    base = site.rstrip('/') + '/wp-json/wp/v2'
    fields = 'id,title,link,content,featured_media,_links,featured_image_url'
    with requests.Session() as session:
        session.auth = auth
        post, endpoint = None, None
        for kind in ('posts', 'pages'):
            page = 1
            while True:
                if progress:
                    progress(f'Mencari URL pada {kind}, halaman {page}...')
                params = {'status': 'publish', '_fields': fields, 'per_page': 100, 'page': page}
                params.update({'include': post_id} if post_id.isdigit() else {'slug': slug})
                response = session.get(f'{base}/{kind}', params=params, timeout=30)
                response.raise_for_status()
                for candidate in response.json():
                    link = urlsplit(candidate.get('link', ''))
                    # CMS and public permalinks may use different hostnames.
                    known_hosts = {link.hostname, urlsplit(site).hostname}
                    host = target.hostname.removeprefix('www.')
                    host_matches = host in {h.removeprefix('www.') for h in known_hosts if h}
                    same_path = unquote(link.path).rstrip('/') == unquote(target.path).rstrip('/')
                    if host_matches and ((post_id.isdigit() and candidate['id'] == int(post_id)) or (not post_id and same_path)):
                        post, endpoint = candidate, kind
                        break
                if post or page >= int(response.headers.get('X-WP-TotalPages', 1)):
                    break
                page += 1
            if post:
                break
        if not post:
            raise ValueError('URL tidak ditemukan pada post/page terpublikasi di WordPress yang terhubung. Periksa URL dan akses akun.')
        parser = _ContentImages()
        content = post.get('content', {}).get('rendered', '')
        parser.feed(content)
        for gallery in re.findall(r'\[gallery\b[^\]]*\bids=["\']([\d,\s]+)["\']', content):
            parser.ids.update(int(mid) for mid in re.findall(r'\d+', gallery))
        featured = featured_media_id(post)
        if featured:
            parser.ids.add(featured)
        elif post.get('featured_image_url'):
            parser.urls.add(post['featured_image_url'])
        media = {}
        ids = sorted(mid for mid in parser.ids if mid > 0)
        for start in range(0, len(ids), 100):
            if progress:
                progress('Mengambil gambar dari konten URL...')
            response = session.get(f'{base}/media', params={
                'include': ','.join(map(str, ids[start:start + 100])), 'per_page': 100}, timeout=30)
            response.raise_for_status()
            media.update((item['id'], item) for item in response.json()
                         if item.get('id') in parser.ids and item.get('mime_type', '').startswith('image/'))
        missing_ids = set(ids) - media.keys()
        if missing_ids:
            raise ValueError('Media pada konten tidak dapat diakses: ' + ', '.join(map(str, sorted(missing_ids))))
        urls = {urljoin(page_url, url) for url in parser.urls
                if urlsplit(urljoin(page_url, url)).scheme in ('http', 'https')}
        paths = set().union(*(_image_paths(item) for item in media.values())) if media else set()
        searches, unmatched = {}, []
        for url in sorted(urls):
            path = unquote(urlsplit(url).path)
            if path in paths:
                continue
            filename = path.rsplit('/', 1)[-1]
            name = re.sub(r'-(?:\d+x\d+|scaled|rotated)(?=\.[^.]+$)', '', filename)
            term = name.rsplit('.', 1)[0]
            if not term:
                unmatched.append(url)
                continue
            if term not in searches:
                searches[term] = []
                page = 1
                while True:
                    if progress:
                        progress(f'Mencocokkan gambar konten: {filename}...')
                    response = session.get(f'{base}/media', params={
                        'search': term, 'media_type': 'image', 'per_page': 100, 'page': page}, timeout=30)
                    response.raise_for_status()
                    searches[term].extend(response.json())
                    if page >= int(response.headers.get('X-WP-TotalPages', 1)):
                        break
                    page += 1
            matches = [item for item in searches[term] if path in _image_paths(item)
                       and item.get('mime_type', '').startswith('image/')]
            if len(matches) == 1:
                item = matches[0]
                media[item['id']] = item
                paths.update(_image_paths(item))
            else:
                unmatched.append(url)
        if post.get('featured_image_url') and not featured:
            featured_path = unquote(urlsplit(urljoin(page_url, post['featured_image_url'])).path)
            featured = next((item['id'] for item in media.values() if featured_path in _image_paths(item)), None)
            if not featured:
                raise ValueError('Featured image tidak dapat dicocokkan dengan Media Library WordPress.')
        for item in media.values():
            item['_url_in'] = [{'title': html.unescape(re.sub(r'<[^>]*>', '', post.get('title', {}).get('rendered', ''))) or '(Tanpa judul)',
                                'link': _post_link(site, post), 'type': endpoint, 'featured': item['id'] == featured}]
        return list(media.values()), unmatched


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
