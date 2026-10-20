import base64
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from resize_pipeline import (apply_logo, generate_gemini_image, generate_openrouter_image, image_info,
                             open_image, output_filename, process_image, resize_image)


def sample(size=(180, 100), color='navy'):
    out = BytesIO()
    Image.new('RGBA', size, color).save(out, 'PNG')
    return out.getvalue()


class PipelineTests(unittest.TestCase):
    @patch('resize_pipeline.requests.post')
    def test_openrouter_request_and_original_response(self, post):
        post.return_value.json.return_value = {'data': [{'b64_json': base64.b64encode(sample()).decode()}]}
        data = generate_openrouter_image('fake-key', ' Landscape ', 'openai/gpt-image-1')
        self.assertEqual(data, sample())
        self.assertEqual(post.call_args.args[0], 'https://openrouter.ai/api/v1/images')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer fake-key')
        self.assertEqual(post.call_args.kwargs['json']['model'], 'openai/gpt-image-1')
        self.assertEqual(post.call_args.kwargs['json']['prompt'], 'Landscape')

    @patch('resize_pipeline.requests.post')
    def test_openrouter_invalid_and_empty_image(self, post):
        for response in [{'data': []}, {'data': [{'b64_json': base64.b64encode(b'invalid image').decode()}]}]:
            post.return_value.json.return_value = response
            with self.assertRaises(ValueError):
                generate_openrouter_image('fake-key', 'A landscape', 'openai/gpt-image-1')

    def test_presets_and_modes_preserve_original(self):
        original = sample((180, 300))
        before = bytes(original)
        for size in ((1080, 608), (1080, 1080), (1280, 720), (123, 321)):
            for mode in ('Crop to Fit', 'Contain', 'Stretch'):
                result = process_image(original, *size, mode, 'JPEG', 120)
                self.assertEqual(Image.open(BytesIO(result['data'])).size, size)
                self.assertLessEqual(len(result['data']), 120_000)
        self.assertEqual(original, before)
        contain = resize_image(open_image(original), 200, 100, 'Contain')
        self.assertEqual(contain.getpixel((0, 0)), (255, 255, 255, 255))

    def test_logo_positions_transparency_and_proportions(self):
        image = Image.new('RGBA', (200, 100), 'blue')
        logo = sample((40, 20), (255, 0, 0, 128))
        points = {'Top Left': (5, 5), 'Top Right': (155, 5), 'Bottom Left': (5, 75),
                  'Bottom Right': (155, 75), 'Center': (80, 40)}
        for position, point in points.items():
            result = apply_logo(image, logo, position, 20, 5)
            red, _, blue, _ = result.getpixel(point)
            self.assertTrue(120 <= red <= 130 and 120 <= blue <= 130)
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 255, 255))
        self.assertEqual(image.getpixel((5, 5)), (0, 0, 255, 255))
        with self.assertRaises(ValueError):
            apply_logo(image, logo, 'Center', 20, 100)

    def test_adaptive_compression_and_metadata(self):
        image = Image.effect_noise((1080, 608), 90).convert('RGB')
        out = BytesIO(); image.save(out, 'PNG')
        for fmt in ('JPEG', 'WebP', 'PNG'):
            # Random pixel noise may be incompressible even at WebP quality 1.
            source = sample() if fmt == 'WebP' else out.getvalue()
            result = process_image(source, 1080, 608, 'Crop to Fit', fmt, 120,
                                   title='Judul café 日本', filename='../my image.jpeg', alt='Alt gambar', caption='Caption contoh')
            self.assertLessEqual(len(result['data']), 120_000)
            with Image.open(BytesIO(result['data'])) as final:
                if fmt == 'PNG':
                    metadata = {key: final.info[key] for key in ('Title', 'Filename', 'AltText', 'Caption')}
                else:
                    metadata = json.loads(final.getexif()[37510][8:])
                self.assertEqual(metadata, result['metadata'])
                self.assertEqual(metadata['Title'], 'Judul café 日本')
                self.assertEqual(metadata['Filename'], result['filename'])
        self.assertEqual(output_filename('a\r\n.png', 'JPEG'), 'a.jpg')

    def test_impossible_budget_never_returns_oversize(self):
        with self.assertRaises(ValueError):
            process_image(sample(), 1080, 608, 'Crop to Fit', 'JPEG', 1, caption='x' * 4000)
        with self.assertRaises(ValueError):
            process_image(b'invalid', 100, 100, 'Stretch', 'PNG', 120)

    def test_gemini_response_and_no_image(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(thought=False, inline_data=SimpleNamespace(mime_type='image/png', data=sample()))]))])
        original = generate_gemini_image('fake-key', 'A landscape', 'gemini-2.5-flash-image', client)
        self.assertEqual(image_info(original)['width'], 180)
        client.models.generate_content.assert_called_once()
        client.models.generate_content.return_value = SimpleNamespace(candidates=[])
        with self.assertRaises(ValueError):
            generate_gemini_image('fake-key', 'A landscape', 'gemini-2.5-flash-image', client)


class StudioTests(unittest.TestCase):
    def app(self):
        return AppTest.from_file('app.py', default_timeout=20)

    @patch('resize_studio._setting', side_effect=lambda name, default='': 'test-key' if name.endswith('API_KEY') else default)
    @patch('resize_studio.generate_openrouter_image', return_value=sample())
    @patch('resize_studio.generate_gemini_image')
    def test_quota_fallback_and_resize_reuses_image(self, gemini, openai, setting):
        error = RuntimeError('quota'); error.code = 429
        gemini.side_effect = error
        app = self.app().run()
        app.radio(key='irg_source_choice').set_value('Generate Gemini AI').run()
        app.text_area(key='irg_prompt').set_value('A landscape').run()
        app.button(key='irg_generate').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.irg_origin, 'openrouter')
        self.assertEqual(app.session_state.irg_original, sample())
        self.assertTrue(any('OpenRouter' in message.value for message in app.success))
        openai.assert_called_once_with('test-key', 'A landscape', 'openai/gpt-image-1')
        app.button(key='irg_process').click().run()
        app.text_input(key='irg_title').set_value('New title').run()
        app.button(key='irg_process').click().run()
        app.run()
        gemini.assert_called_once()
        openai.assert_called_once()

    @patch('resize_studio._setting', return_value='test-key')
    @patch('resize_studio.generate_openrouter_image')
    @patch('resize_studio.generate_gemini_image')
    def test_other_errors_do_not_trigger_fallback(self, gemini, openai, setting):
        from resize_studio import _generate_with_fallback
        for code in (400, 403, 500):
            error = RuntimeError('provider error'); error.code = code
            gemini.side_effect = error
            with self.assertRaises(RuntimeError):
                _generate_with_fallback('key', 'prompt', 'model')
        openai.assert_not_called()

    @patch('resize_studio._setting', side_effect=lambda name, default='': 'test-key' if name.endswith('API_KEY') else default)
    @patch('resize_studio.generate_openrouter_image', side_effect=RuntimeError('OpenRouter failed'))
    @patch('resize_studio.generate_gemini_image')
    def test_failed_fallback_keeps_previous_original(self, gemini, openai, setting):
        error = RuntimeError('quota'); error.code = 429
        gemini.side_effect = [sample(), error]
        app = self.app().run()
        app.radio(key='irg_source_choice').set_value('Generate Gemini AI').run()
        app.text_area(key='irg_prompt').set_value('A landscape').run()
        app.button(key='irg_generate').click().run()
        openai.assert_not_called()
        app.button(key='irg_generate').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.irg_origin, 'gemini')
        self.assertEqual(app.session_state.irg_original, sample())
        self.assertTrue(any('OpenRouter juga gagal' in message.value for message in app.error))
        openai.assert_called_once()

    @patch('resize_studio.generate_gemini_image')
    def test_manual_upload_resize_metadata_rerun_and_reset(self, generate):
        upload = SimpleNamespace(name='manual.png', getvalue=lambda: sample())
        with patch('resize_studio.st.file_uploader', side_effect=lambda *a, **kw: upload if kw.get('key') == 'irg_upload_0' else None):
            app = self.app().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state.irg_original, sample())
            app.session_state.media_list = [{'id': 7, 'source_url': 'https://site/a.jpg', 'mime_type': 'image/jpeg'}]
            app.session_state.edits = {7: {'alt': 'Pending'}}
            app.text_input(key='irg_title').set_value('New title')
            app.text_input(key='irg_filename').set_value('new image.png')
            app.text_input(key='irg_alt').set_value('New alt')
            app.button(key='irg_process').click().run()
            self.assertFalse(app.exception)
            result = app.session_state.irg_result
            self.assertEqual(result['filename'], 'new-image.webp')
            self.assertEqual(result['metadata']['AltText'], 'New alt')
            app.selectbox(key='irg_preset').set_value('Square 1080 × 1080').run()
            app.button(key='irg_process').click().run()
            self.assertEqual(app.session_state.irg_result['height'], 1080)
            generate.assert_not_called()
            self.assertEqual(app.session_state.edits, {7: {'alt': 'Pending'}})
            app.button(key='irg_reset').click().run()
            self.assertIn('irg_original', app.session_state)
            app.button(key='irg_reset_yes').click().run()
            self.assertFalse(app.exception)
            self.assertNotIn('irg_original', app.session_state)
            self.assertEqual(app.session_state.edits, {7: {'alt': 'Pending'}})

    @patch('resize_studio._setting', side_effect=lambda name, default='': 'test-key' if name == 'GEMINI_API_KEY' else default)
    @patch('resize_studio.generate_gemini_image', return_value=sample())
    def test_gemini_only_called_on_generate(self, generate, setting):
        app = self.app().run()
        app.radio(key='irg_source_choice').set_value('Generate Gemini AI').run()
        app.text_area(key='irg_prompt').set_value('A mountain').run()
        app.button(key='irg_generate').click().run()
        self.assertFalse(app.exception)
        generate.assert_called_once()
        app.button(key='irg_process').click().run()
        app.text_input(key='irg_title').set_value('Changed').run()
        app.button(key='irg_process').click().run()
        app.run()
        generate.assert_called_once()
        self.assertEqual(app.session_state.irg_original, sample())

    @patch('resize_studio._fetch_original', return_value=sample())
    def test_fetched_source_is_a_copy(self, fetch):
        app = self.app().run()
        media = {'id': 7, 'source_url': 'https://site/a.jpg', 'mime_type': 'image/jpeg',
                 'title': {'rendered': 'Title'}, 'alt_text': 'Original alt', 'caption': {'rendered': '<p>Original caption</p>'}}
        app.session_state.media_list = [media]
        app.radio(key='irg_source_choice').set_value('Existing / Fetched Image').run()
        app.button(key='irg_use_existing').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key='irg_alt').value, 'Original alt')
        app.text_input(key='irg_alt').set_value('Edited copy').run()
        app.button(key='irg_process').click().run()
        self.assertEqual(app.session_state.media_list[0]['alt_text'], 'Original alt')

    @patch('resize_studio.generate_openrouter_image')
    @patch('resize_studio._setting', side_effect=lambda name, default='': 'test-key' if name == 'GEMINI_API_KEY' else default)
    @patch('resize_studio.generate_gemini_image')
    def test_quota_error_keeps_previous_original(self, generate, setting, openai):
        error = RuntimeError('quota')
        error.code = 429
        generate.side_effect = [sample(), error]
        app = self.app().run()
        app.radio(key='irg_source_choice').set_value('Generate Gemini AI').run()
        app.text_area(key='irg_prompt').set_value('A landscape').run()
        app.button(key='irg_generate').click().run()
        app.button(key='irg_generate').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.irg_original, sample())
        self.assertTrue(any('429' in message.value for message in app.error))
        self.assertTrue(any('OPENROUTER_API_KEY' in message.value for message in app.error))
        openai.assert_not_called()
        app.button(key='irg_process').click().run()
        self.assertIn('irg_result', app.session_state)

    @patch('requests.post')
    def test_legacy_save_still_works(self, post):
        post.return_value.status_code = 200
        app = self.app().run()
        app.session_state.media_list = [{'id': 7, 'source_url': 'https://site/a.jpg', 'mime_type': 'image/jpeg',
                                         'title': {'rendered': 'Original'}, 'alt_text': 'Original alt'}]
        app.session_state.edits = {7: {'alt': 'Edited alt'}}
        app.session_state.title_edits = {7: 'Edited title'}
        app.run()
        app.text_input(key='irg_title').set_value('Separate workspace').run()
        app.button(key='save_7').click().run()
        self.assertFalse(app.exception)
        self.assertEqual([c.kwargs['json'] for c in post.call_args_list], [{'alt_text': 'Edited alt'}, {'title': 'Edited title'}])
        self.assertEqual(app.session_state.media_list[0]['title']['rendered'], 'Edited title')
        self.assertEqual(app.text_input(key='irg_title').value, 'Separate workspace')


if __name__ == '__main__':
    unittest.main()
