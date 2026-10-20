"""Isolated Streamlit UI. All widget and data state uses the irg_ prefix."""
import hashlib
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
import streamlit as st

from resize_pipeline import (MAX_INPUT_BYTES, generate_gemini_image, generate_openrouter_image, image_info,
                             output_filename, process_image)

PRESETS = {'WordPress 1080 × 608': (1080, 608), 'Square 1080 × 1080': (1080, 1080),
           'HD 1280 × 720': (1280, 720), 'Custom': None}


def _setting(name, default=''):
    value = os.getenv(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default


def _plain(value):
    return html.unescape(re.sub(r'<[^>]*>', '', value or ''))


class FallbackGenerationError(RuntimeError):
    """A safe, user-facing explanation of an unsuccessful quota fallback."""


def _generate_with_fallback(key, prompt, model):
    try:
        return generate_gemini_image(key, prompt, model), 'gemini'
    except Exception as exc:
        if getattr(exc, 'code', None) != 429:
            raise
        router_key = _setting('OPENROUTER_API_KEY')
        if not router_key:
            raise FallbackGenerationError(
                'Gemini HTTP 429: kuota/rate limit tercapai. Fallback OpenRouter belum aktif: '
                'tambahkan OPENROUTER_API_KEY melalui .env, environment, atau Streamlit secrets.') from exc
        try:
            return generate_openrouter_image(router_key, prompt, _setting('OPENROUTER_IMAGE_MODEL', 'openai/gpt-image-1')), 'openrouter'
        except Exception as fallback_error:
            raise FallbackGenerationError(
                'Gemini HTTP 429, lalu fallback OpenRouter juga gagal. Periksa API key, saldo/kuota, '
                'akses model OpenRouter, dan koneksi. Gambar sebelumnya tetap tersimpan.') from fallback_error


def _set_original(data, name, metadata=None, origin='manual'):
    info = image_info(data)
    st.session_state.irg_original = data
    st.session_state.irg_original_info = info
    st.session_state.irg_original_name = name
    st.session_state.irg_origin = origin
    st.session_state.pop('irg_result', None)
    metadata = metadata or {}
    for key, value in {'title': metadata.get('title', ''), 'filename': name,
                       'alt': metadata.get('alt', ''), 'caption': metadata.get('caption', '')}.items():
        st.session_state['irg_' + key] = value


def _reset():
    epoch = st.session_state.get('irg_epoch', 0) + 1
    for key in list(st.session_state):
        if key.startswith('irg_'):
            del st.session_state[key]
    st.session_state.irg_epoch = epoch


def _fetch_original(url):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('URL gambar tidak valid.')
    # Public source URL only; WordPress credentials are never sent to media hosts.
    with requests.get(url, stream=True, timeout=30) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_INPUT_BYTES:
                raise ValueError('Gambar maksimal 30 MB.')
    return bytes(data)


def render_resize_studio(media_list):
    st.subheader('Image Resize & AI Generator')
    st.caption('Workspace terpisah. Gambar original dan data Fetch Image tetap tersimpan tanpa ditimpa.')
    epoch = st.session_state.get('irg_epoch', 0)
    source = st.radio('Image Source', ['Upload Manual', 'Generate Gemini AI', 'Existing / Fetched Image'],
                      key='irg_source_choice')
    if source == 'Upload Manual':
        upload = st.file_uploader('Upload gambar original', type=['png', 'jpg', 'jpeg', 'webp'],
                                  key=f'irg_upload_{epoch}')
        if upload:
            data = upload.getvalue()
            token = hashlib.sha256(data).hexdigest()
            if st.session_state.get('irg_upload_token') != token or st.session_state.get('irg_origin') != 'manual':
                try:
                    _set_original(data, upload.name)
                    st.session_state.irg_upload_token = token
                except Exception:
                    st.error('Upload tidak valid. Gunakan PNG, JPG, JPEG, atau WebP, maksimal 30 MB / 20 megapiksel.')
    elif source == 'Generate Gemini AI':
        key = _setting('GEMINI_API_KEY')
        model = _setting('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image')
        router_model = _setting('OPENROUTER_IMAGE_MODEL', 'openai/gpt-image-1')
        st.caption(f'Utama: Gemini ({model}). Jika HTTP 429, otomatis memakai OpenRouter ({router_model}). Generate memakai saldo API provider yang digunakan; resize berikutnya tidak memanggil AI.')
        if not _setting('OPENROUTER_API_KEY'):
            st.info('Fallback OpenRouter belum aktif. Tambahkan OPENROUTER_API_KEY melalui .env, environment, atau Streamlit secrets.')
        prompt = st.text_area('Prompt Gemini', key='irg_prompt')
        if not key:
            st.info('Tambahkan GEMINI_API_KEY melalui environment, .env, atau Streamlit secrets.')
        if st.button('Generate Image', key='irg_generate', disabled=not key or not prompt.strip()):
            try:
                with st.spinner('Membuat gambar: Gemini, lalu OpenRouter jika kuota Gemini tercapai…'):
                    data, provider = _generate_with_fallback(key, prompt, model)
                _set_original(data, f'{provider}-image.png', origin=provider)
                if provider == 'openrouter':
                    st.success(f'Gemini memberi HTTP 429. Gambar berhasil dibuat oleh OpenRouter ({router_model}).')
            except FallbackGenerationError as exc:
                st.error(str(exc))
            except ImportError:
                st.error('SDK belum tersedia. Jalankan: python -m pip install -r requirements.txt')
            except Exception:
                st.error('Generate Gemini gagal atau tidak menghasilkan gambar. Periksa prompt, API key, akses model, kuota, dan koneksi.')
    else:
        images = [m for m in media_list if m.get('mime_type', '').startswith('image/')]
        if not images:
            st.info('Fetch gambar dahulu pada tab Fetch Image. Hasilnya bisa dipilih di sini.')
        else:
            lookup = {m['id']: m for m in images}
            selected = st.selectbox('Pilih hasil fetch', list(lookup), key='irg_existing_id',
                                    format_func=lambda mid: f"{mid} · {_plain(lookup[mid].get('title', {}).get('rendered', ''))}")
            if st.button('Gunakan gambar hasil fetch', key='irg_use_existing'):
                item = lookup[selected]
                try:
                    with st.spinner('Mengambil salinan gambar original…'):
                        data = _fetch_original(item['source_url'])
                    name = unquote(urlsplit(item['source_url']).path.rsplit('/', 1)[-1])
                    _set_original(data, name, {'title': _plain(item.get('title', {}).get('rendered', '')),
                                              'alt': item.get('alt_text', ''),
                                              'caption': _plain(item.get('caption', {}).get('rendered', ''))}, 'fetched')
                except Exception:
                    st.error('Gambar tidak dapat diambil. Periksa URL dan koneksi, atau download lalu upload manual.')

    original = st.session_state.get('irg_original')
    if original:
        st.caption('Sumber original aktif: ' + st.session_state.irg_origin)
    left, right = st.columns(2)
    with left:
        st.markdown('**Original**')
        if original:
            info = st.session_state.irg_original_info
            st.image(original)
            st.caption(f"{info['width']} × {info['height']} · {info['bytes'] / 1000:.1f} KB · {st.session_state.irg_original_name}")
        else:
            st.info('Pilih atau buat gambar untuk memulai.')

    st.markdown('**Resize & Compression**')
    first, second = st.columns(2)
    with first:
        preset = st.selectbox('Preset', list(PRESETS), key='irg_preset')
        if PRESETS[preset] is None:
            width = int(st.number_input('Custom width', min_value=1, max_value=4096, value=1080, key='irg_width'))
            height = int(st.number_input('Custom height', min_value=1, max_value=4096, value=608, key='irg_height'))
        else:
            width, height = PRESETS[preset]
        mode = st.selectbox('Resize mode', ['Crop to Fit', 'Contain', 'Stretch'], key='irg_mode')
        st.caption('Crop: potong tengah. Contain: pertahankan rasio dengan margin putih. Stretch: penuhi dimensi tanpa menjaga rasio.')
    with second:
        output_format = st.selectbox('Output format', ['WebP', 'JPEG', 'PNG'], key='irg_format')
        max_kb = int(st.number_input('Maksimal ukuran file (KB)', min_value=1, max_value=10000, value=120, key='irg_max_kb'))
        st.caption('1 KB = 1.000 byte. Batas termasuk metadata. PNG dapat mengurangi jumlah warna agar muat.')

    logo_data = None
    use_logo = st.checkbox('Gunakan Logo', key='irg_use_logo')
    position, percent, margin = 'Bottom Right', 20, 20
    logo_ready = True
    if use_logo:
        logo_file = st.file_uploader('Custom logo', type=['png', 'jpg', 'jpeg', 'webp'], key=f'irg_logo_upload_{epoch}')
        default_logo = Path(__file__).parent / 'logo.png'
        if logo_file:
            logo_data = logo_file.getvalue()
        elif default_logo.is_file():
            logo_data = default_logo.read_bytes()
            st.caption('Menggunakan logo.png dari folder aplikasi.')
        else:
            st.warning('Upload logo custom atau letakkan logo.png di folder aplikasi.')
            logo_ready = False
        one, two = st.columns(2)
        with one:
            position = st.selectbox('Logo Position', ['Top Left', 'Top Right', 'Bottom Left', 'Bottom Right', 'Center'], index=3, key='irg_logo_position')
        with two:
            percent = st.slider('Logo Size (% lebar gambar)', 5, 50, 20, key='irg_logo_percent')
            margin = int(st.number_input('Logo Margin (px)', min_value=0, max_value=2048, value=20, key='irg_logo_margin'))

    st.markdown('**Metadata hasil**')
    title = st.text_input('Image Title', key='irg_title')
    filename = st.text_input('Filename', key='irg_filename')
    alt = st.text_input('Alt Text', key='irg_alt')
    caption = st.text_area('Caption', key='irg_caption')
    st.caption('Metadata disematkan pada file hasil (PNG text / JPEG dan WebP EXIF). Ini tidak mengubah metadata atau alt pada WordPress.')
    final_name = output_filename(filename, output_format)
    st.caption(f'Nama file download: {final_name}')
    settings = {'width': width, 'height': height, 'mode': mode, 'output_format': output_format,
                'max_kb': max_kb, 'title': title, 'filename': filename, 'alt': alt, 'caption': caption,
                'logo': logo_data, 'logo_position': position, 'logo_percent': percent, 'logo_margin': margin}
    signature = hashlib.sha256((original or b'') + json.dumps(
        {k: v for k, v in settings.items() if k != 'logo'}, sort_keys=True).encode() + (logo_data or b'')).hexdigest()
    if st.button('Process / Resize Image', key='irg_process', type='primary', disabled=not original or not logo_ready):
        try:
            with st.spinner('Memproses resize, logo, metadata, dan kompresi…'):
                result = process_image(original, **settings)
            result['signature'] = signature
            st.session_state.irg_result = result
        except ValueError as exc:
            st.session_state.pop('irg_result', None)
            st.error(str(exc))
        except Exception:
            st.session_state.pop('irg_result', None)
            st.error('Gambar/logo tidak dapat diproses. Periksa format gambar dan pengaturan.')
    result = st.session_state.get('irg_result')
    with right:
        st.markdown('**Final**')
        if result:
            st.image(result['data'])
            st.caption(f"{result['width']} × {result['height']} · {len(result['data']) / 1000:.1f} KB · {result['filename']}")
        else:
            st.info('Klik Process / Resize Image untuk melihat hasil.')
        if result:
            stale = result['signature'] != signature or not logo_ready
            if stale:
                st.info('Pengaturan berubah. Klik Process / Resize Image untuk memperbarui hasil dan download.')
            st.download_button('Download Final Image', result['data'], result['filename'], result['mime'],
                               key='irg_download', disabled=stale)
    if st.button('Reset Image', key='irg_reset'):
        st.session_state.irg_confirm_reset = True
    if st.session_state.get('irg_confirm_reset'):
        st.info('Hapus original, hasil, logo, dan metadata dari workspace ini? Data Fetch Image tetap tersimpan.')
        yes, no = st.columns(2)
        yes.button('Ya, reset workspace', key='irg_reset_yes', on_click=_reset)
        if no.button('Batal reset', key='irg_reset_no'):
            st.session_state.irg_confirm_reset = False
            st.rerun()
