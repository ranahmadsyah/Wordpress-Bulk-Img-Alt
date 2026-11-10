import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from media_tools import _fetch_public_images, fetch_url_media


def response(items, pages=1):
    result = Mock()
    result.json.return_value = items
    result.headers = {'X-WP-TotalPages': str(pages)}
    return result


def image(mid, name='image.jpg', **extra):
    return {'id': mid, 'mime_type': 'image/jpeg',
            'source_url': 'https://site/uploads/' + name, **extra}


def post(content='', link='https://site/article/', **extra):
    return {'id': 5, 'title': {'rendered': 'Article &amp; images'}, 'link': link,
            'content': {'rendered': content}, **extra}


def public_response(markup):
    result = Mock()
    result.headers = {'Content-Type': 'text/html; charset=utf-8'}
    result.encoding = 'utf-8'
    result.is_redirect = False
    result.iter_content.return_value = [markup.encode('utf-8')]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class URLMediaTests(unittest.TestCase):
    @patch('media_tools.requests.Session')
    def test_featured_content_gallery_and_duplicates_only(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [
            response([post('<img class="wp-image-7" src="/uploads/image.jpg">'
                           '<img class="wp-image-8">[gallery ids="8,9"]'
                           '<a href="/uploads/logo.jpg">Download</a>'
                           '<video><source src="/uploads/movie.mp4"></video>', featured_media=7)]),
            response([image(7), image(8, 'inline.jpg'), image(9, 'gallery.jpg'), image(99, 'logo.jpg')]),
        ]
        media, unmatched = fetch_url_media('https://site', ('u', 'p'), 'https://site/article/?utm_source=test#content')
        self.assertEqual([m['id'] for m in media], [7, 8, 9])
        self.assertEqual(unmatched, [])
        self.assertEqual(media[0]['_url_in'][0]['title'], 'Article & images')
        self.assertTrue(media[0]['_url_in'][0]['featured'])
        self.assertFalse(media[1]['_url_in'][0]['featured'])
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args.kwargs['params']['include'], '7,8,9')

    @patch('media_tools.requests.Session')
    def test_page_lazy_thumbnail_matching_and_external_report(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [
            response([]),
            response([post('<picture><source srcset="//site/uploads/photo-300x200.jpg 300w">'
                           '<img data-lazy-src="/uploads/photo.jpg" src="data:image/gif;base64,AAA"></picture>'
                           '<img src="https://external/other.jpg">', link='https://site/parent/article/')]),
            response([]),
            response([image(8, 'photo.jpg', media_details={'sizes': {'small': {
                'source_url': 'https://site/uploads/photo-300x200.jpg'}}}), image(99, 'photo-unrelated.jpg')]),
        ]
        media, unmatched = fetch_url_media('https://site', ('u', 'p'), 'https://site/parent/article/')
        self.assertEqual([m['id'] for m in media], [8])
        self.assertEqual(unmatched, ['https://external/other.jpg'])
        self.assertEqual(session.get.call_args.kwargs['params']['search'], 'photo')
        self.assertEqual(media[0]['_url_in'][0]['type'], 'pages')
        self.assertEqual(session.get.call_count, 4)

    @patch('media_tools._fetch_public_images', side_effect=ValueError('URL tidak ditemukan'))
    @patch('media_tools.requests.Session')
    def test_permalink_must_match_not_just_slug(self, session_class, public):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [response([post()]), response([post(link='https://site/other/article/')])]
        with self.assertRaisesRegex(ValueError, 'URL tidak ditemukan'):
            fetch_url_media('https://site', ('u', 'p'), 'https://site/parent/article/')
        self.assertEqual(session.get.call_count, 2)
        public.assert_called_once()

    @patch('media_tools._fetch_public_images', side_effect=ValueError('URL tidak ditemukan'))
    @patch('media_tools.requests.Session')
    def test_foreign_host_cannot_select_same_slug(self, session_class, public):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [response([post()]), response([])]
        with self.assertRaisesRegex(ValueError, 'URL tidak ditemukan'):
            fetch_url_media('https://site', ('u', 'p'), 'https://unrelated/article/')
        self.assertTrue(all(call.args[0].startswith('https://site/wp-json/')
                            for call in session.get.call_args_list))
        public.assert_called_once()

    @patch('media_tools.requests.Session')
    def test_public_permalink_and_cms_alias(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.return_value = response([post(link='https://www.public/article/')])
        for url in ('http://public/article', 'https://cms/article/'):
            self.assertEqual(fetch_url_media('https://cms', ('u', 'p'), url), ([], []))

    @patch('media_tools.requests.Session')
    def test_plain_permalink_id(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [response([]), response([post(link='https://site/parent/article/')])]
        self.assertEqual(fetch_url_media('https://site', ('u', 'p'), 'https://site/?page_id=5'), ([], []))
        self.assertEqual(session.get.call_args.kwargs['params']['include'], '5')

    @patch('media_tools.requests.Session')
    def test_invalid_urls_do_not_request(self, session_class):
        for url in ('', 'site/article', 'file:///article', 'https://site/', 'https://user:password@site/article'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_url_media('https://site', ('u', 'p'), url)
        session_class.assert_not_called()

    @patch('media_tools.requests.Session')
    def test_missing_content_media_is_not_silent(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [response([post('<img class="wp-image-8">')]), response([])]
        with self.assertRaisesRegex(ValueError, 'tidak dapat diakses: 8'):
            fetch_url_media('https://site', ('u', 'p'), 'https://site/article/')

    @patch('media_tools.requests.Session')
    def test_featured_url_without_id_can_resolve(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [response([post(featured_image_url='https://site/uploads/image.jpg')]), response([image(7)])]
        media, unmatched = fetch_url_media('https://site', ('u', 'p'), 'https://site/article/')
        self.assertEqual([m['id'] for m in media], [7])
        self.assertEqual(unmatched, [])
        self.assertTrue(media[0]['_url_in'][0]['featured'])

    @patch('media_tools.requests.Session')
    def test_exact_url_matching_and_search_pagination(self, session_class):
        session = session_class.return_value.__enter__.return_value
        session.get.side_effect = [response([post('<img src="/uploads/image.jpg">')]),
                                   response([image(99, 'image.jpg-extra')], pages=2), response([image(7)], pages=2)]
        media, _ = fetch_url_media('https://site', ('u', 'p'), 'https://site/article/')
        self.assertEqual([m['id'] for m in media], [7])
        self.assertEqual(session.get.call_args.kwargs['params']['page'], 2)

    @patch('media_tools.requests.Session')
    def test_only_referenced_ids_in_batches(self, session_class):
        session = session_class.return_value.__enter__.return_value
        markup = ''.join(f'<img class="wp-image-{mid}">' for mid in range(1, 102))
        session.get.side_effect = [response([post(markup)]), response([image(mid) for mid in range(1, 101)]), response([image(101)])]
        media, _ = fetch_url_media('https://site', ('u', 'p'), 'https://site/article/')
        self.assertEqual(len(media), 101)
        self.assertEqual(session.get.call_args.kwargs['params']['include'], '101')


class PublicPageTests(unittest.TestCase):
    @patch('media_tools.socket.getaddrinfo', return_value=[(None, None, None, None, ('8.8.8.8', 443))])
    @patch('media_tools.requests.get')
    @patch('media_tools.requests.Session')
    def test_localized_service_page_and_proxy_paths(self, session_class, get, dns):
        url = 'https://public/id/layanan/pemeriksaan-latar-belakang-prakerja/'
        get.return_value = public_response('''<html><head><title>Layanan Prakerja</title>
            <meta property="og:image" content="/media/uploads/sites/3/2026/10/featured.webp"></head>
            <body><header><img src="/media/uploads/sites/3/site-logo.webp"></header><main>
            <nav><img src="/media/uploads/sites/3/navigation.webp"></nav>
            <picture><source srcset="/media/uploads/sites/3/inline-300x200.webp 300w">
            <img class="wp-image-999" src="/media/uploads/sites/3/inline.webp"></picture>
            <img src="/media/uploads/sites/3/client.webp" alt="Client Logo">
            <footer><img src="/media/uploads/sites/3/embedded-footer.webp"></footer></main>
            <aside><img src="/media/uploads/sites/3/sidebar.webp"></aside>
            <footer><img src="/media/uploads/sites/3/footer.webp"></footer></body></html>''')
        featured = image(7)
        featured['source_url'] = 'https://cms/id/wp-content/uploads/sites/3/2026/10/featured.webp'
        inline = image(8, media_details={'sizes': {'medium': {
            'source_url': 'https://cms/id/wp-content/uploads/sites/3/inline-300x200.webp'}}})
        inline['source_url'] = 'https://cms/id/wp-content/uploads/sites/3/inline.webp'
        session = session_class.return_value.__enter__.return_value
        # A same-slug CMS page with another route must not contribute its images.
        session.get.side_effect = [response([]), response([post('<img class="wp-image-666">')]),
                                   response([featured, image(100)]), response([inline])]
        media, unmatched = fetch_url_media('https://cms/id/', ('private-user', 'private-password'), url)
        self.assertEqual([item['id'] for item in media], [7, 8])
        self.assertEqual(unmatched, [])
        self.assertEqual(media[0]['_url_in'][0]['link'], url)
        self.assertEqual(media[0]['_url_in'][0]['title'], 'Layanan Prakerja')
        self.assertTrue(media[0]['_url_in'][0]['featured'])
        self.assertEqual(session.get.call_count, 4)
        self.assertTrue(all('include' not in call.kwargs['params'] for call in session.get.call_args_list))
        get.assert_called_once_with(url, timeout=30, stream=True, allow_redirects=False)

    @patch('media_tools.socket.getaddrinfo', return_value=[(None, None, None, None, ('8.8.8.8', 443))])
    @patch('media_tools.requests.get')
    def test_article_scope_and_missing_content_area(self, get, dns):
        get.return_value = public_response('<header><img src="/logo.jpg"></header>'
                                          '<article><img src="/article.jpg"></article>')
        parser, _ = _fetch_public_images('https://public/article/')
        self.assertEqual(parser.urls, {'/article.jpg'})
        get.return_value = public_response('<header><img src="/logo.jpg"></header>')
        with self.assertRaisesRegex(ValueError, 'main/article tidak ditemukan'):
            _fetch_public_images('https://public/article/')

    @patch('media_tools.socket.getaddrinfo', return_value=[(None, None, None, None, ('8.8.8.8', 443))])
    @patch('media_tools.requests.get')
    def test_external_featured_can_be_reported_without_hiding_content(self, get, dns):
        get.return_value = public_response('<meta property="og:image" content="https://external/featured.jpg">'
                                          '<main><img src="/uploads/image.jpg"></main>')
        with patch('media_tools.requests.Session') as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = [response([]), response([]), response([]), response([image(7)])]
            media, unmatched = fetch_url_media('https://site', ('u', 'p'), 'https://public/service/')
        self.assertEqual([item['id'] for item in media], [7])
        self.assertEqual(unmatched, ['https://external/featured.jpg'])

    @patch('media_tools.socket.getaddrinfo', side_effect=[
        [(None, None, None, None, ('8.8.8.8', 443))],
        [(None, None, None, None, ('127.0.0.1', 80))]])
    @patch('media_tools.requests.get')
    def test_redirect_cannot_fetch_local_network(self, get, dns):
        result = public_response('')
        result.is_redirect = True
        result.headers['Location'] = 'http://127.0.0.1/private'
        get.return_value = result
        with self.assertRaisesRegex(ValueError, 'jaringan lokal'):
            _fetch_public_images('https://public/article/')
        get.assert_called_once()

    @patch('media_tools.socket.getaddrinfo', return_value=[(None, None, None, None, ('8.8.8.8', 443))])
    @patch('media_tools.requests.get')
    def test_non_html_and_oversized_response(self, get, dns):
        result = public_response('')
        result.headers['Content-Type'] = 'image/jpeg'
        get.return_value = result
        with self.assertRaisesRegex(ValueError, 'halaman HTML'):
            _fetch_public_images('https://public/article/')
        result.headers['Content-Type'] = 'text/html'
        result.iter_content.return_value = [b'x' * 5_000_001]
        with self.assertRaisesRegex(ValueError, 'terlalu besar'):
            _fetch_public_images('https://public/article/')


class URLAppTests(unittest.TestCase):
    @patch('media_tools.fetch_all_media', return_value=[])
    @patch('media_tools.fetch_featured_media', return_value=[])
    @patch('media_tools.fetch_url_media')
    def test_url_fetch_edit_clear_and_existing_buttons(self, fetch, featured, all_media):
        item = image(7, _url_in=[{'title': 'Article', 'link': 'https://site/article/', 'featured': True}])
        fetch.return_value = ([item], ['https://external/image.jpg'])
        app = AppTest.from_file('app.py', default_timeout=20).run()
        app.session_state.media_list = [image(99)]
        app.session_state.edits = {99: {'alt': 'Old'}}
        app.session_state.irg_title = 'Keep resize workspace'
        app.text_input(key='fetch_page_url').set_value('https://site/article/')
        next(b for b in app.button if 'Fetch dari URL' in b.label).click().run()
        self.assertFalse(app.exception)
        self.assertEqual([m['id'] for m in app.session_state.media_list], [7])
        self.assertEqual(app.session_state.edits, {})
        self.assertEqual(app.session_state.media_usage[7][0]['title'], 'Article')
        self.assertTrue(any('tidak dapat dicocokkan' in msg.value for msg in app.warning))
        featured.assert_not_called()
        all_media.assert_not_called()
        app.button(key='suffix_7').click().run()
        self.assertIn('www.integrity-indonesia.com', app.session_state.edits[7]['alt'])
        # Failed fetch retains loaded images and pending edits.
        fetch.side_effect = requests.HTTPError('403 forbidden')
        next(b for b in app.button if 'Fetch dari URL' in b.label).click().run()
        self.assertEqual([m['id'] for m in app.session_state.media_list], [7])
        self.assertIn(7, app.session_state.edits)
        self.assertTrue(any('403' in msg.value for msg in app.error))
        app.button(key='clear_url_media').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.media_list, [])
        self.assertEqual(app.session_state.edits, {})
        self.assertEqual(app.session_state.irg_title, 'Keep resize workspace')
        next(b for b in app.button if 'Fetch Featured Image Post' in b.label).click().run()
        featured.assert_called_once()
        next(b for b in app.button if 'Fetch All Media' in b.label).click().run()
        all_media.assert_called_once()
        self.assertEqual(app.session_state.fetch_scope, 'Semua media')
        self.assertFalse(app.exception)


if __name__ == '__main__':
    unittest.main()
