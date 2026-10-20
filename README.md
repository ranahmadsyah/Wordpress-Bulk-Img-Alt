# Run

## Setup on another computer

Install Python and Git, then run these commands in PowerShell:

```powershell
git clone https://github.com/ranahmadsyah/Wordpress-Bulk-Img-Alt.git
cd Wordpress-Bulk-Img-Alt
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit the new `.env` and fill `WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`, and
the Gemini/OpenRouter API keys you want to use. API keys and passwords are local
configuration; they are not included when cloning. Transfer them privately or
create new keys. The optional `OPENAI_API_KEY` is for the existing alt-text feature.

Start the app:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the Local URL printed in the terminal. Both Fetch Image and Image Resize &
AI Generator are part of this same app. GitHub stores the source; the app runs on
the computer where you start Streamlit. On an existing clone, use `git pull` to
receive later updates.

Local `.env`, `.streamlit/secrets.toml`, virtual environments, backups, and test
artifacts are excluded from new commits. `app_backup.py` is a local backup and is
not part of the distributable app. Credentials use environment variables loaded
from `.env`; restart Streamlit after changing them.

## Existing installation

```
streamlit run app.py
```

Install dependencies first: `python -m pip install -r requirements.txt`.

## Media usage

Choose one of the two fetch buttons:

- **Fetch Featured Image Post** loads only distinct featured images of published
  posts (excluding pages).
- **Fetch All Media** loads the entire WordPress media collection, including logos,
  social icons, and other attachments. Image cards still follow the image filters.

For featured images, the app reads lightweight post metadata in batches of 100, then
requests only the referenced media IDs. Logos, social icons, and other media are
excluded unless used as a featured image. Each card immediately lists its featured
image locations. All statistics and bulk actions apply to the loaded collection.
If a CMS hides `featured_media`, the app reads the media ID from the WordPress
`wp:featuredmedia` REST link. When a public post link is omitted, the title links
to the CMS edit screen instead. A featured image URL without a resolvable ID/link
produces a diagnostic instead of silently reporting an empty successful fetch.
The next fetch replaces the collection with the selected source.

For other detected content usage, click **Periksa penggunaan di post/page**. Each card shows its
current filename and matching published post/page titles and links, including
featured images, WordPress image IDs, gallery shortcodes, and media/thumbnail URLs.
This is a snapshot of accessible published content, not proof that an unmatched
image is unused. Drafts, custom post types, themes, and builder metadata are not
scanned. Refresh the check after changing WordPress content. Filename display is
read-only; changing existing physical filenames requires server-side support.

Image creation and compression are available in the separate **Image Resize & AI
Generator** tab. The retired OpenAI image-generation/compression panel has been
removed from **Fetch Image**. Existing media title/alt editing and WordPress saves
remain available there. OpenAI is still used for the existing AI alt-text feature.

## Safe tests

`python -m unittest test_media_tools -v`

Tests use generated sample images and mocked API calls; no real WordPress writes
or paid OpenAI requests are made.

## Image Resize & AI Generator (new tab)

The **Fetch Image** tab keeps the existing fetch, edit, and WordPress save workflows. The new tab is isolated in `resize_studio.py` and
`resize_pipeline.py`; its session keys start with `irg_`. Resetting this workspace
does not clear fetched media or pending edits.
The pre-change snapshot is in `backups/before-image-resize-20261020-112810/`.

1. Open **Image Resize & AI Generator**.
2. Choose **Upload Manual**, **Generate Gemini AI**, or **Existing / Fetched Image**.
   Fetched images are copied from their public source URL with their title, alt,
   and caption prefilled. Nothing is written back to WordPress by this tab.
3. Choose 1080 × 608 (WordPress), 1080 × 1080, 1280 × 720, or custom dimensions
   (1–4096 px). All resizing uses Pillow LANCZOS.
4. Choose Crop to Fit (center crop), Contain (white padding), or Stretch.
5. Choose WebP, JPEG, or PNG and the final file limit (default 120 KB = 120,000 bytes).
6. Optionally enable **Gunakan Logo** and upload a custom logo. Without a custom
   upload, `logo.png` in the project folder is used if present. No default logo is
   bundled. Position, width percentage (5–50%), and pixel margin are configurable.
   Logos are composited locally with their aspect ratio and alpha transparency.
7. Edit title, filename, alt text, and caption. Click **Process / Resize Image**,
   review original/final dimensions and byte sizes, then **Download Final Image**.
   Settings changed afterward require processing again before download.
8. **Reset Image** clears this workspace after inline confirmation.

Gemini setup (environment / `.env`, or equivalent top-level Streamlit secrets):

```dotenv
GEMINI_API_KEY=your-key-here
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image
OPENROUTER_API_KEY=your-openrouter-key-here
OPENROUTER_IMAGE_MODEL=openai/gpt-image-1
```

The only added dependency is `google-genai>=1.30.0`. Install with
`python -m pip install -r requirements.txt`, then start `python -m streamlit run app.py`.
Click **Generate Image** to try Gemini first. Only if Gemini returns HTTP 429
(quota/rate limit), the same prompt is sent once to OpenRouter using `OPENROUTER_API_KEY`
and `OPENROUTER_IMAGE_MODEL` (default `openai/gpt-image-1`). These settings can also be
provided through Streamlit secrets. Each provider uses its own API balance/quota.
Other Gemini errors do not trigger paid fallback. If the OpenRouter key is missing or
the fallback fails, an actionable error is shown and the previous original/result
are retained. OpenRouter image fallback uses the existing requests dependency and its dedicated
`POST https://openrouter.ai/api/v1/images` endpoint. A direct OpenAI API key is not
required for image generation. The separate legacy alt-text feature still uses its
existing OpenAI configuration.

Resizing, logo changes, metadata, downloads, and reruns reuse the stored original
without calling either provider. The active original identifies `gemini` or
`openrouter` as its source. Upload/manual processing works without any AI key or an
installed Gemini SDK. Restart Streamlit after changing environment/API settings.

PNG embeds editable fields as Unicode text. JPEG/WebP embed a Unicode title and a
JSON UserComment in EXIF containing Title, Filename, AltText, and Caption. Viewer
support varies; this embedded AltText does not set HTML alt attributes or update
WordPress. Existing source metadata is not copied wholesale. JPEG transparency is
flattened onto white. PNG may reduce palette colors to meet the file budget.
If even the minimum encoding exceeds the chosen limit, processing stops with a
clear error instead of providing an oversized download. Original bytes are retained.

Run the full offline regression suite:

```powershell
python -m unittest test_media_tools test_resize_studio -v
```

This suite covers both fetch buttons, existing title/alt saves, all
resize presets/modes, manual upload, fetched copies, mocked Gemini responses,
transparent logo placement, file limits, embedded metadata, filenames, reruns,
reset confirmation, and independence from the Fetch Image session state.
