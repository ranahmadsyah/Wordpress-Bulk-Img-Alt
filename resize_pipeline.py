"""Independent image pipeline for the Image Resize & AI Generator tab."""
import base64
import json
import re
from io import BytesIO

import requests

from PIL import Image, ImageOps, PngImagePlugin, UnidentifiedImageError

FORMATS = {'WebP': ('WEBP', '.webp', 'image/webp'),
           'JPEG': ('JPEG', '.jpg', 'image/jpeg'),
           'PNG': ('PNG', '.png', 'image/png')}
MAX_INPUT_BYTES = 30_000_000
MAX_PIXELS = 20_000_000


def open_image(data):
    if not data or len(data) > MAX_INPUT_BYTES:
        raise ValueError('Gunakan gambar valid dengan ukuran maksimal 30 MB.')
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError('Format yang didukung: PNG, JPG, JPEG, WebP.')
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Gambar maksimal 20 megapiksel.')
            return ImageOps.exif_transpose(image).convert('RGBA')
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError('Data gambar tidak valid atau rusak.') from exc


def image_info(data):
    image = open_image(data)
    return {'width': image.width, 'height': image.height, 'bytes': len(data)}


def output_filename(name, output_format):
    name = re.split(r'[/\\]', name.strip())[-1]
    name = re.sub(r'\.[^.]+$', '', name)
    name = re.sub(r'[^\w-]+', '-', name, flags=re.UNICODE).strip('-_')[:100]
    return (name or 'image') + FORMATS[output_format][1]


def resize_image(image, width, height, mode):
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise ValueError('Lebar dan tinggi harus antara 1 dan 4096 piksel.')
    size = (width, height)
    if mode == 'Crop to Fit':
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
    if mode == 'Contain':
        resized = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', size, 'white')
        canvas.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
        return canvas
    if mode == 'Stretch':
        return image.resize(size, Image.Resampling.LANCZOS)
    raise ValueError('Mode resize tidak dikenal.')


def apply_logo(image, data, position, percent, margin):
    if not 5 <= percent <= 50 or margin < 0:
        raise ValueError('Ukuran logo harus 5–50% dan margin tidak boleh negatif.')
    available = (image.width - 2 * margin, image.height - 2 * margin)
    if min(available) < 1:
        raise ValueError('Margin logo terlalu besar untuk dimensi gambar.')
    logo = open_image(data)
    target_width = max(1, min(round(image.width * percent / 100), available[0]))
    logo = ImageOps.contain(logo, (target_width, available[1]), method=Image.Resampling.LANCZOS)
    positions = {
        'Top Left': (margin, margin),
        'Top Right': (image.width - logo.width - margin, margin),
        'Bottom Left': (margin, image.height - logo.height - margin),
        'Bottom Right': (image.width - logo.width - margin, image.height - logo.height - margin),
        'Center': ((image.width - logo.width) // 2, (image.height - logo.height) // 2),
    }
    result = image.copy()
    result.alpha_composite(logo, positions[position])
    return result


def _metadata_options(metadata, output_format):
    if output_format == 'PNG':
        info = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            info.add_itxt(key, str(value))
        return {'pnginfo': info}
    # Unicode-safe EXIF for JPEG/WebP; UserComment stores all four editable fields.
    exif = Image.Exif()
    exif[40091] = (metadata.get('Title', '') + '\0').encode('utf-16le')
    exif[37510] = b'ASCII\0\0\0' + json.dumps(metadata, ensure_ascii=True).encode('ascii')
    return {'exif': exif.tobytes()}


def encode_image(image, output_format, max_bytes, metadata):
    """Find a fitting encoding without reducing the requested pixel dimensions."""
    if max_bytes < 1000:
        raise ValueError('Batas ukuran minimal 1 KB.')
    fmt = FORMATS[output_format][0]
    options = _metadata_options(metadata, output_format)
    image = image.copy()
    image.info.clear()
    if fmt == 'JPEG':
        background = Image.new('RGBA', image.size, 'white')
        background.alpha_composite(image)
        image = background.convert('RGB')

    def save(candidate, quality=None):
        output = BytesIO()
        kwargs = dict(options)
        if fmt == 'JPEG':
            kwargs.update(quality=quality, optimize=True)
        elif fmt == 'WEBP':
            kwargs.update(quality=quality, method=6)
        else:
            kwargs.update(optimize=True, compress_level=9)
        candidate.save(output, fmt, **kwargs)
        return output.getvalue()

    if fmt == 'PNG':
        data = save(image)
        if len(data) <= max_bytes:
            return data
        for colors in (256, 128, 64, 32, 16, 8, 4, 2):
            candidate = image.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
            data = save(candidate)
            if len(data) <= max_bytes:
                return data
    else:
        low, high, best = 1, 95, None
        while low <= high:
            quality = (low + high) // 2
            data = save(image, quality)
            if len(data) <= max_bytes:
                best, low = data, quality + 1
            else:
                high = quality - 1
        if best is not None:
            return best
    raise ValueError('Batas ukuran belum tercapai. Naikkan batas KB, pilih WebP/JPEG, atau kurangi dimensi. Hasil tidak diunduh melebihi batas.')


def process_image(data, width, height, mode, output_format, max_kb,
                  title='', filename='image', alt='', caption='', logo=None,
                  logo_position='Bottom Right', logo_percent=20, logo_margin=20):
    image = resize_image(open_image(data), width, height, mode)
    if logo:
        image = apply_logo(image, logo, logo_position, logo_percent, logo_margin)
    filename = output_filename(filename, output_format)
    metadata = {'Title': title, 'Filename': filename, 'AltText': alt, 'Caption': caption}
    result = encode_image(image, output_format, int(max_kb * 1000), metadata)
    with Image.open(BytesIO(result)) as check:
        if check.size != (width, height) or len(result) > int(max_kb * 1000):
            raise ValueError('Validasi hasil akhir gagal.')
    return {'data': result, 'filename': filename, 'mime': FORMATS[output_format][2],
            'width': width, 'height': height, 'metadata': metadata}


def generate_gemini_image(api_key, prompt, model, client=None):
    """Called explicitly on Generate only; returns original bytes for reuse."""
    if not api_key or not prompt.strip():
        raise ValueError('API key Gemini dan prompt wajib diisi.')
    owned = client is None
    if owned:
        from google import genai
        client = genai.Client(api_key=api_key, http_options={'timeout': 120_000})
    try:
        response = client.models.generate_content(
            model=model, contents=prompt.strip(),
            config={'response_modalities': ['TEXT', 'IMAGE']})
        for candidate in response.candidates or []:
            content = getattr(candidate, 'content', None)
            for part in getattr(content, 'parts', None) or []:
                inline = getattr(part, 'inline_data', None)
                if inline and (inline.mime_type or '').startswith('image/') and not getattr(part, 'thought', False):
                    data = bytes(inline.data)
                    open_image(data)
                    return data
        raise ValueError('Gemini tidak mengembalikan gambar. Coba prompt lain atau periksa akses model.')
    finally:
        if owned:
            client.close()


def generate_openrouter_image(api_key, prompt, model):
    """Use OpenRouter's dedicated Image API; no credentials go to OpenAI directly."""
    if not api_key or not prompt.strip():
        raise ValueError('API key OpenRouter dan prompt wajib diisi.')
    response = requests.post(
        'https://openrouter.ai/api/v1/images',
        headers={'Authorization': f'Bearer {api_key}'},
        json={'model': model, 'prompt': prompt.strip(), 'aspect_ratio': '3:2',
              'quality': 'medium', 'n': 1}, timeout=(15, 180))
    response.raise_for_status()
    payload = response.json()
    images = payload.get('data') or []
    if not images or not images[0].get('b64_json'):
        raise ValueError('OpenRouter tidak mengembalikan gambar.')
    data = base64.b64decode(images[0]['b64_json'], validate=True)
    open_image(data)
    return data
