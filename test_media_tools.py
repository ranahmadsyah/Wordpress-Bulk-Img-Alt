import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from media_tools import fetch_usage, fetch_featured_media, fetch_all_media


class MediaTests(unittest.TestCase):
    @patch('media_tools.requests.Session')
    def test_cms_featured_relation_without_native_field(self, session_class):
        session = session_class.return_value.__enter__.return_value
        posts, images = Mock(), Mock()
        posts.headers = images.headers = {'X-WP-TotalPages': '1'}
        posts.json.return_value = [{'id': 41926, 'title': {'rendered': 'Post CMS'},
                                   'featured_image_url': 'https://site/image.jpg',
                                   '_links': {'wp:featuredmedia': [{'href': 'https://site/id/wp-json/wp/v2/media/41937'}]}}]
        images.json.return_value = [{'id': 41937, 'mime_type': 'image/jpeg'}]
        session.get.side_effect = [posts, images]
        media = fetch_featured_media('https://site/id/', ('u', 'p'))
        self.assertEqual([m['id'] for m in media], [41937])
        self.assertEqual(media[0]['_featured_in'][0]['title'], 'Post CMS')
        self.assertEqual(media[0]['_featured_in'][0]['link'], 'https://site/id/wp-admin/post.php?post=41926&action=edit')
        self.assertIn('_links', session.get.call_args_list[0].kwargs['params']['_fields'])

    @patch('media_tools.requests.Session')
    def test_missing_featured_relation_is_not_silent_empty(self, session_class):
        session = session_class.return_value.__enter__.return_value
        response = Mock(); response.headers = {'X-WP-TotalPages': '1'}
        response.json.return_value = [{'id': 1, 'featured_image_url': 'https://site/image.jpg'}]
        session.get.return_value = response
        with self.assertRaisesRegex(ValueError, 'ID/relasi'):
            fetch_featured_media('https://site', ('u', 'p'))

    @patch('media_tools.requests.Session')
    def test_featured_only_and_deduplicated(self, session_class):
        session = session_class.return_value.__enter__.return_value
        def response(items, pages=1):
            r = Mock(); r.json.return_value = items
            r.headers = {'X-WP-TotalPages': str(pages)}
            return r
        session.get.side_effect = [
            response([{'featured_media': 7, 'title': {'rendered': 'ABC'}, 'link': 'https://site/abc'}], 2),
            response([{'featured_media': 7}, {'featured_media': 8}, {'featured_media': 0}], 2),
            response([{'id': 7, 'mime_type': 'image/jpeg'}, {'id': 8, 'mime_type': 'image/png'}, {'id': 99, 'mime_type': 'image/png'}]),
        ]
        media = fetch_featured_media('https://site', ('u', 'p'))
        self.assertEqual([m['id'] for m in media], [7, 8])
        self.assertEqual(len(media[0]['_featured_in']), 2)
        params = session.get.call_args.kwargs['params']
        self.assertEqual(params['include'], '7,8')
        self.assertEqual(params['per_page'], 100)
        self.assertTrue(all('/pages' not in call.args[0] for call in session.get.call_args_list))

    @patch('media_tools.requests.Session')
    def test_no_featured_images(self, session_class):
        session = session_class.return_value.__enter__.return_value
        response = Mock(); response.json.return_value = []
        response.headers = {'X-WP-TotalPages': '0'}
        session.get.return_value = response
        self.assertEqual(fetch_featured_media('https://site', ('u', 'p')), [])
        self.assertEqual(session.get.call_count, 1)

    @patch('media_tools.requests.Session')
    def test_all_media_pagination(self, session_class):
        session = session_class.return_value.__enter__.return_value
        first, second = Mock(), Mock()
        first.json.return_value = [{'id': 1}, {'id': 2}]
        second.json.return_value = [{'id': 3}]
        first.headers = second.headers = {'X-WP-TotalPages': '2'}
        session.get.side_effect = [first, second]
        self.assertEqual(fetch_all_media('https://site', ('u', 'p')), [{'id': 1}, {'id': 2}, {'id': 3}])
        self.assertEqual(session.get.call_args.kwargs['params'], {'per_page': 100, 'page': 2})

    @patch('media_tools.fetch_all_media', return_value=[])
    @patch('media_tools.fetch_featured_media', return_value=[])
    def test_fetch_buttons_use_separate_sources(self, featured, all_media):
        app = AppTest.from_file('app.py', default_timeout=20).run()
        next(b for b in app.button if 'Fetch Featured Image Post' in b.label).click().run()
        featured.assert_called_once()
        all_media.assert_not_called()
        next(b for b in app.button if 'Fetch All Media' in b.label).click().run()
        all_media.assert_called_once()
        self.assertEqual(featured.call_count, 1)
        self.assertEqual(app.session_state.fetch_scope, 'Semua media')
        self.assertFalse(app.exception)

    @patch('media_tools.requests.get')
    def test_usage_pagination_and_exact_matching(self, get):
        def response(items, pages=1):
            r = Mock(); r.json.return_value = items
            r.headers = {'X-WP-TotalPages': str(pages)}
            return r
        get.side_effect = [
            response([{'featured_media': 1, 'title': {'rendered': 'ABC &amp; DEF'}, 'content': {'rendered': '<img class="wp-image-1" src="https://site/uploads/a.jpg">'}, 'link': 'https://site/abc'}], 2),
            response([{'content': {'rendered': '<img srcset="https://site/uploads/a-300x200.jpg 300w">'}}], 2),
            response([{'content': {'rendered': '<img src="https://site/uploads/a.jpg-extra">'}}]),
        ]
        media = [{'id': 1, 'source_url': 'https://site/uploads/a.jpg', 'media_details': {'sizes': {'medium': {'source_url': 'https://site/uploads/a-300x200.jpg'}}}}]
        usage, failures = fetch_usage('https://site', ('u', 'p'), media)
        self.assertEqual(failures, [])
        self.assertEqual(len(usage[1]), 2)
        self.assertEqual(usage[1][0]['title'], 'ABC & DEF')
        self.assertEqual(get.call_count, 3)

    def test_app(self):
        app = AppTest.from_file('app.py', default_timeout=20).run()
        self.assertFalse(app.exception)
        app.session_state.media_list = [{'id': 1, 'source_url': 'https://site/a.jpg', 'mime_type': 'image/jpeg', 'title': {'rendered': 'A'}, 'alt_text': ''}]
        app.session_state.media_usage = {1: [{'title': 'ABC', 'link': 'https://site/abc', 'featured': True}]}
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Digunakan di 1' in x.value for x in app.success))
        self.assertFalse(any('Buat / kompres' in e.label for e in app.expander))
        self.assertFalse(any(b.label == 'Generate & kompres' for b in app.button))
        self.assertEqual([tab.label for tab in app.tabs], ['Fetch Image', 'Image Resize & AI Generator'])
        app.button(key='suffix_1').click().run()
        self.assertFalse(app.exception)
        self.assertIn('www.integrity-indonesia.com', app.session_state.edits[1]['alt'])


if __name__ == '__main__':
    unittest.main()
