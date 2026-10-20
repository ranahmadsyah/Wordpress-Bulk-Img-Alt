import os
import streamlit as st
import requests
import base64
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from media_tools import fetch_usage, fetch_featured_media, fetch_all_media
from resize_studio import render_resize_studio
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ─── Credentials & Config ────────────────────────────────────────────────────
WORDPRESS_URL = os.getenv("WORDPRESS_URL", "https://cms-corporate.integrity-asia.com/id/")
USERNAME      = os.getenv("WORDPRESS_USERNAME", "")
APP_PASSWORD  = os.getenv("WORDPRESS_APP_PASSWORD", "")

API_KEY   = os.getenv("OPENAI_API_KEY")
API_MODEL = os.getenv("OPENAI_API_MODEL", "gpt-4o")

ALT_SUFFIX = "| www.integrity-indonesia.com"
PAGE_SIZE  = 32   # cards shown per page in the UI

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Ran - WP Media Alt Manager",
    page_icon="🖼️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Load External CSS ───────────────────────────────────────────────────────
css_path = Path(__file__).parent / "style.css"
if css_path.exists():
    st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def has_suffix(alt: str) -> bool:
    return ALT_SUFFIX.lower() in alt.lower()

def ensure_suffix(alt: str) -> str:
    alt = alt.strip()
    if not alt:
        return ALT_SUFFIX
    return alt if has_suffix(alt) else f"{alt} {ALT_SUFFIX}"

def is_random_filename(filename: str) -> bool:
    name = re.sub(r'\.[^.]+$', '', filename).lower()
    patterns = [
        r'^[a-f0-9]{8,}$', r'^\d{6,}$', r'^img_?\d{3,}', r'^dsc_?\d{3,}',
        r'^photo_?\d{3,}', r'^screenshot', r'^[a-z0-9]{20,}$', r'^p\d{6,}',
        r'wp[-_]?\d{4,}', r'^[a-zA-Z0-9+/]{20,}={0,2}$',
    ]
    for p in patterns:
        if re.search(p, name):
            return True
    return not [p for p in re.split(r'[-_\s]+', name) if len(p) > 3]

def filename_to_alt(filename: str) -> str:
    name = re.sub(r'\.[^.]+$', '', filename)
    name = re.sub(r'[-_]+', ' ', name)
    return ensure_suffix(re.sub(r'\s+', ' ', name).strip().title())

def get_wp_media(progress=None, featured_only=True) -> list:
    fetch = fetch_featured_media if featured_only else fetch_all_media
    return fetch(WORDPRESS_URL, (USERNAME, APP_PASSWORD), progress)

def update_wp_alt(media_id: int, alt: str) -> bool:
    creds   = base64.b64encode(f"{USERNAME}:{APP_PASSWORD}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}
    url     = f"{WORDPRESS_URL.rstrip('/')}/wp-json/wp/v2/media/{media_id}"
    resp    = requests.post(url, headers=headers, json={"alt_text": alt}, timeout=30)
    return resp.status_code in (200, 201)

def update_wp_title(media_id: int, title: str) -> bool:
    creds   = base64.b64encode(f"{USERNAME}:{APP_PASSWORD}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}
    url     = f"{WORDPRESS_URL.rstrip('/')}/wp-json/wp/v2/media/{media_id}"
    resp    = requests.post(url, headers=headers, json={"title": title}, timeout=30)
    return resp.status_code in (200, 201)

def detect_language(text: str) -> str:
    """
    Lightweight heuristic — no external library needed.
    Returns 'id' for Indonesian, 'en' for English/unknown.
    Checks for common Indonesian words and affixes.
    """
    if not text:
        return "en"
    text_lower = text.lower()
    # Common Indonesian words / affixes that rarely appear in English
    id_markers = [
        "cara", "tips", "untuk", "dengan", "manfaat", "kulit", "wajah",
        "rambut", "tubuh", "kecantikan", "kesehatan", "produk", "review",
        "harga", "beli", "murah", "terbaik", "alami", "bahan", "resep",
        "masker", "serum", "pelembab", "vitamin", "obat", "cantik",
        "putih", "cerah", "bersih", "lembut", "ampuh", "mudah",
        "memutihkan", "merawat", "menghilangkan", "menggunakan",
        "adalah", "yang", "dan", "atau", "ini", "itu",
    ]
    score = sum(1 for w in id_markers if w in text_lower)
    return "id" if score >= 1 else "en"


def ai_generate_alt(image_url: str, filename: str, title: str = "") -> str:
    if not API_KEY:
        return f"[Error: OPENAI_API_KEY not set] {ALT_SUFFIX}"
    try:
        client     = OpenAI(api_key=API_KEY)
        clean_name = re.sub(r'[-_]+', ' ', re.sub(r'\.[^.]+$', '', filename)).strip()
        name_is_random = is_random_filename(filename)

        # Strip HTML tags from title
        clean_title = re.sub(r'<[^>]+>', '', title).strip() if title else ""

        # ── Language detection ───────────────────────────────────────────────
        lang_source = clean_title if clean_title else clean_name
        lang        = detect_language(lang_source)
        lang_instruction = (
            "Write the alt text in Bahasa Indonesia."
            if lang == "id" else
            "Write the alt text in English."
        )

        # ── Context clue priority: title > meaningful filename > visual only ─
        if clean_title and len(clean_title) > 3:
            context_instruction = (
                f"The media title is: '{clean_title}'. "
                "Use this title as the primary reference for what the image is about. "
                "Incorporate the title's meaning naturally into the alt text. "
            )
        elif not name_is_random and len(clean_name) > 3:
            context_instruction = (
                f"The filename is: '{clean_name}'. "
                "This filename describes the image — use it as a strong reference "
                "and incorporate its meaning into the alt text. "
            )
        else:
            context_instruction = (
                "The filename is not descriptive, so rely entirely on what you see in the image. "
            )

        response = client.chat.completions.create(
            model=API_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are an SEO and accessibility expert. "
                            + context_instruction
                            + lang_instruction + " "
                            "Write a concise, natural alt text under 100 characters. "
                            "Be specific about the main subject. "
                            "Do NOT include website names or URLs. "
                            "Return ONLY the alt text — no quotes, no explanation, no punctuation at the end."
                        ),
                    },
                    # detail: "low" costs ~85% less than "high" with minimal
                    # quality loss for alt text generation (512px tile only)
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                ],
            }],
            max_tokens=80,  # alt text is short — no need for 120 tokens
        )
        raw = response.choices[0].message.content.strip().rstrip(".,;:")
        return ensure_suffix(raw)
    except Exception as e:
        return f"[AI Error: {e}]"

def get_current_alt(mid: int, m: dict) -> str:
    return st.session_state.edits.get(mid, {}).get("alt", m.get("alt_text", ""))

def get_current_title(mid: int, m: dict) -> str:
    return st.session_state.title_edits.get(mid, m.get("title", {}).get("rendered", ""))


# ─── Session State Init ───────────────────────────────────────────────────────
for key, default in [
    ("media_list",   []),
    ("edits",        {}),
    ("title_edits",  {}),
    ("connected",    False),
    ("ui_page",      0),
    ("active_filter","All Media"),
    # Incrementing edit_gen changes widget keys, forcing text areas to re-read
    # the value from session state (fixes AI/suffix not appearing in inputs)
    ("edit_gen",     0),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🌐 Connected Site")
    st.markdown(f'<div class="site-badge">🔗 {WORDPRESS_URL}<br>👤 {USERNAME}</div>',
                unsafe_allow_html=True)

    st.markdown("## 🤖 OpenAI")
    if API_KEY:
        st.markdown(f'<div class="site-badge">✅ Key loaded &nbsp;|&nbsp; Model: {API_MODEL}</div>',
                    unsafe_allow_html=True)
    else:
        st.warning("⚠️ `OPENAI_API_KEY` env var not set.")

    st.markdown("---")
    st.caption("Pilih featured image post atau semua media. Data diambil maksimal 100 item per permintaan.")

    st.markdown("---")
    st.markdown("## 🔍 Filters")
    filter_options = ["All Media", "Missing Alt Text", "Has Alt Text",
                      "Missing Suffix", "Random Filename", "AI Suggested"]
    chosen = st.selectbox(
        "Show",
        filter_options,
        index=filter_options.index(st.session_state.active_filter),
    )
    if chosen != st.session_state.active_filter:
        st.session_state.active_filter = chosen
        st.session_state.ui_page = 0
    mime_filter = st.multiselect(
        "File Types",
        ["image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"],
        default=["image/jpeg", "image/png", "image/webp"],
    )

    st.markdown("---")
    fetch_featured_btn = st.button("🔄 Fetch Featured Image Post", type="primary", use_container_width=True)
    fetch_all_btn = st.button("🔄 Fetch All Media", use_container_width=True)


# ─── Main Header ─────────────────────────────────────────────────────────────
fetch_tab, resize_tab = st.tabs(["Fetch Image", "Image Resize & AI Generator"])
with fetch_tab:
    st.markdown("""
    <div class="app-header">
      <h1>🖼️ Ran - WP Media Alt Manager</h1>
      <p>Bulk manage WordPress image alt texts — powered by GPT-4o Vision</p>
    </div>
    """, unsafe_allow_html=True)


    # ─── Fetch Media ─────────────────────────────────────────────────────────────
    if fetch_featured_btn or fetch_all_btn:
        with st.spinner("Connecting to WordPress…"):
            try:
                fetch_status = st.empty()
                media = get_wp_media(fetch_status.caption, featured_only=fetch_featured_btn)
                fetch_status.empty()
                st.session_state.pop("media_usage", None)
                st.session_state.pop("usage_failures", None)
                st.session_state.media_list  = media
                st.session_state.edits       = {}
                st.session_state.title_edits = {}
                st.session_state.connected   = True
                st.session_state.fetch_scope = "Featured image post terpublikasi" if fetch_featured_btn else "Semua media"
                st.session_state.ui_page     = 0
                st.session_state.edit_gen   += 1
                if media:
                    st.success(f"✅ {st.session_state.fetch_scope}: **{len(media)}** item termuat.")
                else:
                    st.warning(f"{st.session_state.fetch_scope}: tidak ada item ditemukan pada sumber ini.")
            except requests.exceptions.ConnectionError:
                st.error("❌ Could not connect. Check WORDPRESS_URL.")
            except requests.exceptions.HTTPError as e:
                st.error(f"❌ HTTP Error: {e}")
            except Exception as e:
                st.error(f"❌ Error: {e}")


    # ─── Stats + Main UI ─────────────────────────────────────────────────────────
    media_list = st.session_state.media_list

    if media_list:
        st.caption("Koleksi aktif: " + st.session_state.get("fetch_scope", "Media yang dimuat"))
        if st.button("Periksa penggunaan di post/page"):
            with st.spinner("Memeriksa post dan page terpublikasi..."):
                usage, failures = fetch_usage(WORDPRESS_URL, (USERNAME, APP_PASSWORD), media_list)
            st.session_state.media_usage = usage
            st.session_state.usage_failures = failures
        st.caption("Penggunaan diperiksa pada post/page terpublikasi yang dapat diakses akun ini. Tema, page builder, dan tipe konten lain mungkin tidak terdeteksi. Periksa ulang setelah mengubah konten WordPress.")
        if st.session_state.get("usage_failures"):
            st.warning("Pemeriksaan belum lengkap. Gagal membaca: " + ", ".join(st.session_state.usage_failures))
        images         = [m for m in media_list if m.get("mime_type","").startswith("image/")]
        total          = len(images)
        no_alt         = sum(1 for m in images if not m.get("alt_text","").strip())
        has_alt        = total - no_alt
        random_fn      = sum(1 for m in images if is_random_filename(m.get("source_url","").split("/")[-1]))
        missing_suffix = sum(1 for m in images
                             if m.get("alt_text","").strip() and not has_suffix(m.get("alt_text","")))
        ai_pending     = sum(1 for v in st.session_state.edits.values() if v.get("ai_generated"))

        # ── Clickable stat cards — click to filter ───────────────────────────────
        def stat_card(col, label, value, filter_name):
            active = st.session_state.active_filter == filter_name
            border_color = "var(--accent)" if active else "var(--border)"
            bg_color     = "rgba(124,111,247,0.1)" if active else "var(--surface)"
            col.markdown(f"""
            <div style="background:{bg_color};border:1.5px solid {border_color};border-radius:12px;
                        padding:1rem 1.2rem;cursor:pointer;transition:all .2s;">
                <div style="font-size:0.78rem;color:var(--muted);margin-bottom:4px">{label}</div>
                <div style="font-size:1.6rem;font-weight:800;color:var(--accent2);">{value}</div>
            </div>
            """, unsafe_allow_html=True)
            if col.button(f"Filter: {label}", key=f"stat_{filter_name}", use_container_width=True,
                          help=f"Click to filter by: {label}"):
                st.session_state.active_filter = filter_name
                st.session_state.ui_page = 0
                st.rerun()

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        stat_card(c1, "All Images",      total,          "All Media")
        stat_card(c2, "Has Alt Text",    has_alt,        "Has Alt Text")
        stat_card(c3, "Missing Alt",     no_alt,         "Missing Alt Text")
        stat_card(c4, "Missing Suffix",  missing_suffix, "Missing Suffix")
        stat_card(c5, "Random Filenames",random_fn,      "Random Filename")
        stat_card(c6, "AI Suggestions",  ai_pending,     "AI Suggested")

        st.markdown("---")

        # ─── Bulk Actions ────────────────────────────────────────────────────────
        with st.expander("⚡ Bulk Actions", expanded=True):
            col_a, col_b, col_c, col_d = st.columns(4)

            with col_a:
                if st.button("📝 Auto-fill from Filename", use_container_width=True):
                    count = 0
                    for m in images:
                        mid = m["id"]
                        if not m.get("alt_text","").strip():
                            fname = m.get("source_url","").split("/")[-1]
                            st.session_state.edits.setdefault(mid, {}).update(
                                alt=filename_to_alt(fname), ai_generated=False
                            )
                            count += 1
                    st.session_state.edit_gen += 1
                    st.success(f"Prefilled {count} items from filename.")

            with col_b:
                if st.button("🏷️ Append Suffix to All", use_container_width=True):
                    count = 0
                    for m in images:
                        mid = m["id"]
                        cur = get_current_alt(mid, m)
                        if cur.strip() and not has_suffix(cur):
                            st.session_state.edits.setdefault(mid, {}).update(
                                alt=ensure_suffix(cur), ai_generated=False
                            )
                            count += 1
                    st.session_state.edit_gen += 1
                    st.success(f"Suffix added to {count} items.")

            with col_c:
                ai_scope = st.selectbox(
                    "AI scope",
                    ["Current page only", "Random filenames only", "All missing alt", "All loaded images"],
                    label_visibility="collapsed",
                )
                if st.button("🤖 AI Scan Scope", use_container_width=True, type="primary"):
                    if not API_KEY:
                        st.error("Set `OPENAI_API_KEY`.")
                    else:
                        # page_items is computed below after filtering — we need to
                        # re-derive it here since bulk actions run before the grid
                        def _get_page_items():
                            def _pf(m):
                                mime = m.get("mime_type","")
                                if mime_filter and mime not in mime_filter: return False
                                fname       = m.get("source_url","").split("/")[-1]
                                mid         = m["id"]
                                current_alt = get_current_alt(mid, m)
                                is_ai       = st.session_state.edits.get(mid, {}).get("ai_generated", False)
                                _sf = st.session_state.active_filter
                                if _sf == "Missing Alt Text"  and current_alt.strip():           return False
                                if _sf == "Has Alt Text"      and not current_alt.strip():       return False
                                if _sf == "Random Filename"   and not is_random_filename(fname): return False
                                if _sf == "AI Suggested"      and not is_ai:                     return False
                                if _sf == "Missing Suffix"    and (not current_alt.strip() or has_suffix(current_alt)): return False
                                return True
                            _filtered = [m for m in images if _pf(m)]
                            _total_pages = max(1, -(-len(_filtered) // PAGE_SIZE))
                            _ui_page     = min(st.session_state.ui_page, _total_pages - 1)
                            return _filtered[_ui_page * PAGE_SIZE : (_ui_page + 1) * PAGE_SIZE]

                        page_images = _get_page_items()

                        if ai_scope == "Current page only":
                            targets = page_images
                        elif ai_scope == "Random filenames only":
                            targets = [m for m in page_images
                                       if is_random_filename(m.get("source_url","").split("/")[-1])]
                        elif ai_scope == "All missing alt":
                            targets = [m for m in page_images if not m.get("alt_text","").strip()]
                        else:  # All images in the currently loaded collection
                            targets = images

                        if not targets:
                            st.info("No images matched the selected scope on this page.")
                        else:
                            prog      = st.progress(0)
                            status_ph = st.empty()
                            for i, m in enumerate(targets):
                                fname = m.get("source_url","").split("/")[-1]
                                status_ph.markdown(f"<small>Scanning `{fname}`…</small>",
                                                   unsafe_allow_html=True)
                                alt = ai_generate_alt(
                                    m.get("source_url",""), fname,
                                    m.get("title", {}).get("rendered", "")
                                )
                                st.session_state.edits.setdefault(m["id"], {}).update(
                                    alt=alt, ai_generated=True
                                )
                                # Mirror to title
                                st.session_state.title_edits[m["id"]] = alt
                                prog.progress((i + 1) / len(targets))
                                time.sleep(0.05)
                            status_ph.empty()
                            st.session_state.edit_gen += 1
                            st.success(f"✅ AI scanned {len(targets)} images.")

            with col_d:
                if st.button("💾 Save ALL to WP", use_container_width=True):
                    if not st.session_state.edits and not st.session_state.title_edits:
                        st.info("No pending changes.")
                    else:
                        all_mids = set(st.session_state.edits) | set(st.session_state.title_edits)
                        prog2    = st.progress(0)
                        success, fail = 0, 0
                        for i, mid in enumerate(all_mids):
                            alt   = st.session_state.edits.get(mid, {}).get("alt")
                            title = st.session_state.title_edits.get(mid)
                            ok    = True
                            if alt   is not None: ok = ok and update_wp_alt(mid, alt)
                            if title is not None: ok = ok and update_wp_title(mid, title)
                            if ok:
                                success += 1
                                for m in st.session_state.media_list:
                                    if m["id"] == mid:
                                        if alt   is not None: m["alt_text"] = alt
                                        if title is not None: m.setdefault("title",{})["rendered"] = title
                            else:
                                fail += 1
                            prog2.progress((i + 1) / len(all_mids))
                        st.session_state.edits       = {}
                        st.session_state.title_edits = {}
                        st.session_state.edit_gen   += 1
                        if fail:
                            st.warning(f"Saved {success}, failed {fail}.")
                        else:
                            st.success(f"✅ Saved {success} items to WordPress.")

        # ─── Filter ──────────────────────────────────────────────────────────────
        st.markdown("### 🗂️ Media Library")

        def passes_filter(m):
            mime = m.get("mime_type","")
            if mime_filter and mime not in mime_filter:
                return False
            fname = m.get("source_url","").split("/")[-1]
            mid   = m["id"]
            # ⚠️ Use SAVED alt text only (not pending edits) so cards don't
            # disappear from the filter view while changes are unsaved
            saved_alt = m.get("alt_text", "")
            is_ai     = st.session_state.edits.get(mid, {}).get("ai_generated", False)
            sf = st.session_state.active_filter
            if sf == "Missing Alt Text"  and saved_alt.strip():             return False
            if sf == "Has Alt Text"      and not saved_alt.strip():         return False
            if sf == "Random Filename"   and not is_random_filename(fname): return False
            if sf == "AI Suggested"      and not is_ai:                     return False
            if sf == "Missing Suffix"    and (not saved_alt.strip() or has_suffix(saved_alt)): return False
            return True

        filtered    = [m for m in images if passes_filter(m)]
        total_pages = max(1, -(-len(filtered) // PAGE_SIZE))   # ceil division
        ui_page     = min(st.session_state.ui_page, total_pages - 1)
        page_start  = ui_page * PAGE_SIZE
        page_items  = filtered[page_start : page_start + PAGE_SIZE]

        # ── Pagination controls (top) ─────────────────────────────────────────────
        cap_col, pag_col = st.columns([3, 2])
        with cap_col:
            st.caption(
                f"Showing {len(filtered)} of {len(images)} images  •  "
                f"Page {ui_page + 1} / {total_pages}"
            )
        with pag_col:
            pc1, pc2, pc3, pc4 = st.columns(4)
            if pc1.button("⏮", use_container_width=True, disabled=(ui_page == 0), key="t_first"):
                st.session_state.ui_page = 0; st.rerun()
            if pc2.button("◀", use_container_width=True, disabled=(ui_page == 0), key="t_prev"):
                st.session_state.ui_page = ui_page - 1; st.rerun()
            if pc3.button("▶", use_container_width=True, disabled=(ui_page >= total_pages - 1), key="t_next"):
                st.session_state.ui_page = ui_page + 1; st.rerun()
            if pc4.button("⏭", use_container_width=True, disabled=(ui_page >= total_pages - 1), key="t_last"):
                st.session_state.ui_page = total_pages - 1; st.rerun()

        if not page_items:
            st.markdown('<div class="info-box">No media items match the current filter.</div>',
                        unsafe_allow_html=True)
        else:
            # edit_gen is included in widget keys — incrementing it forces Streamlit to
            # treat the widget as new, so it reads value= from session state instead of
            # its own stale internal state. This is the fix for AI/suffix not showing.
            gen = st.session_state.edit_gen

            cols_per_row = 3
            for row in [page_items[i:i+cols_per_row] for i in range(0, len(page_items), cols_per_row)]:
                cols = st.columns(cols_per_row)
                for col, m in zip(cols, row):
                    with col:
                        mid       = m["id"]
                        src       = m.get("source_url","")
                        fname     = src.split("/")[-1]
                        is_rnd    = is_random_filename(fname)
                        stored    = st.session_state.edits.get(mid, {})
                        cur_alt   = get_current_alt(mid, m)
                        cur_title = get_current_title(mid, m)
                        is_ai_gen   = stored.get("ai_generated", False)
                        has_pending = (mid in st.session_state.edits or
                                       mid in st.session_state.title_edits)

                        # Card border: orange = unsaved changes, purple = AI generated, default otherwise
                        if has_pending and is_ai_gen:
                            card_border = "#f7a84a"   # orange — AI result not yet saved
                            card_bg     = "rgba(247,168,74,0.06)"
                        elif has_pending:
                            card_border = "#f7c948"   # yellow — manual edit not yet saved
                            card_bg     = "rgba(247,201,72,0.06)"
                        else:
                            card_border = "var(--border)"   # default
                            card_bg     = "var(--surface)"

                        # Status badges
                        if is_ai_gen:
                            type_badge = '<span class="badge badge-ai">🤖 AI</span>'
                        elif is_rnd:
                            type_badge = '<span class="badge badge-random">⚠️ Random</span>'
                        elif cur_alt.strip():
                            type_badge = '<span class="badge badge-ok">✓ Has Alt</span>'
                        else:
                            type_badge = '<span class="badge badge-empty">✗ No Alt</span>'

                        if cur_alt.strip():
                            suffix_badge = (
                                '<span class="badge badge-suffix-ok">✓ Suffix</span>'
                                if has_suffix(cur_alt) else
                                '<span class="badge badge-no-suffix">✗ No Suffix</span>'
                            )
                        else:
                            suffix_badge = ''

                        # Prefer larger sizes for a crisper thumbnail
                        sizes     = m.get("media_details", {}).get("sizes", {})
                        thumb_url = (
                            sizes.get("medium_large", {}).get("source_url")
                            or sizes.get("large",       {}).get("source_url")
                            or sizes.get("medium",      {}).get("source_url")
                            or sizes.get("thumbnail",   {}).get("source_url")
                            or src
                        )

                        # Image card — clicking opens the full-res image in a new tab
                        pending_badge = '<span class="badge badge-pending">● Unsaved</span>' if has_pending else ''
                        st.markdown(f"""
                        <div class="media-card" style="border-color:{card_border};background:{card_bg};
                                                       transition:border-color .2s,background .2s;">
                            <a href="{src}" target="_blank" title="Open full-size image">
                                <img src="{thumb_url}" alt="{cur_alt}" loading="lazy"
                                     onerror="this.style.display='none'"/>
                            </a>
                            <div style="margin:4px 0">{type_badge} {suffix_badge} {pending_badge}</div>
                            <div style="font-size:0.72rem;color:var(--muted);font-family:'Space Mono',monospace;
                                        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                                 title="{fname}">{fname}</div>
                        </div>
                        """, unsafe_allow_html=True)

                        st.text_input("Nama file saat ini", value=unquote(urlsplit(src).path.rsplit("/", 1)[-1]),
                                      disabled=True, key=f"filename_{mid}",
                                      help="Nama file lama tidak dapat diubah melalui API standar WordPress.")
                        usage = st.session_state.get("media_usage")
                        if (usage is None or mid not in usage) and m.get('_featured_in'):
                            st.success(f"Featured image di {len(m['_featured_in'])} post/page")
                            with st.expander("Lihat judul dan lokasi featured image"):
                                for reference in m['_featured_in']:
                                    if reference['link']:
                                        st.link_button(reference['title'], reference['link'], use_container_width=True)
                            st.caption("Penggunaan lain dalam konten belum diperiksa.")
                        elif usage is None or mid not in usage:
                            st.caption("Penggunaan belum diperiksa")
                        elif usage[mid]:
                            st.success(f"Digunakan di {len(usage[mid])} post/page")
                            with st.expander("Lihat judul dan lokasi"):
                                for reference in usage[mid]:
                                    label = reference['title'] + (" - Featured image" if reference['featured'] else " - Konten")
                                    if reference['link']:
                                        st.link_button(label, reference['link'], use_container_width=True)
                                    else:
                                        st.write(label)
                        else:
                            st.caption("Belum ditemukan dalam konten yang diperiksa")

                        # ── Title field ───────────────────────────────────────
                        st.markdown('<p style="font-size:0.78rem;color:var(--muted);margin:4px 0 2px">📝 Title</p>', unsafe_allow_html=True)
                        new_title = st.text_input(
                            "Title",
                            value=cur_title,
                            key=f"title_{mid}_{gen}",
                            placeholder="Media title…",
                            label_visibility="collapsed",
                        )

                        if new_title != cur_title:
                            st.session_state.title_edits[mid] = new_title

                        # ── Alt text field ────────────────────────────────────
                        st.markdown('<p style="font-size:0.78rem;color:var(--muted);margin:4px 0 2px">🏷️ Alt Text</p>', unsafe_allow_html=True)
                        new_alt = st.text_area(
                            "Alt text",
                            value=cur_alt,
                            key=f"alt_{mid}_{gen}",
                            height=72,
                            label_visibility="collapsed",
                            placeholder=f"Alt text… {ALT_SUFFIX}",
                        )

                        if new_alt != cur_alt:
                            st.session_state.edits.setdefault(mid, {}).update(
                                alt=new_alt, ai_generated=False
                            )
                            # Mirror alt text → title whenever alt changes
                            st.session_state.title_edits[mid] = new_alt

                        # Action buttons
                        b1, b2, b3 = st.columns(3)

                        with b1:
                            if st.button("💾 Save", key=f"save_{mid}", use_container_width=True):
                                ok_alt   = update_wp_alt(mid, get_current_alt(mid, m))
                                ok_title = update_wp_title(mid, get_current_title(mid, m))
                                if ok_alt and ok_title:
                                    m["alt_text"] = get_current_alt(mid, m)
                                    m.setdefault("title", {})["rendered"] = get_current_title(mid, m)
                                    st.session_state.edits.pop(mid, None)
                                    st.session_state.title_edits.pop(mid, None)
                                    st.session_state.edit_gen += 1
                                    st.success("Saved!")
                                else:
                                    st.error("Save failed.")

                        with b2:
                            if st.button("🏷️ +Suffix", key=f"suffix_{mid}", use_container_width=True):
                                updated = ensure_suffix(get_current_alt(mid, m))
                                st.session_state.edits.setdefault(mid, {}).update(
                                    alt=updated, ai_generated=False
                                )
                                # Mirror to title
                                st.session_state.title_edits[mid] = updated
                                st.session_state.edit_gen += 1
                                st.rerun()

                        with b3:
                            if st.button("🤖 AI", key=f"ai_{mid}", use_container_width=True,
                                         help="Generate alt text with AI"):
                                if not API_KEY:
                                    st.error("Set `OPENAI_API_KEY`.")
                                else:
                                    with st.spinner("Scanning…"):
                                        ai_alt = ai_generate_alt(
                                            src, fname,
                                            m.get("title", {}).get("rendered", "")
                                        )
                                    st.session_state.edits.setdefault(mid, {}).update(
                                        alt=ai_alt, ai_generated=True
                                    )
                                    # Mirror to title
                                    st.session_state.title_edits[mid] = ai_alt
                                    st.session_state.edit_gen += 1
                                    st.rerun()

        # ── Pagination controls (bottom) ──────────────────────────────────────────
        if total_pages > 1:
            st.markdown("---")
            bc1, bc2, bc3, bc4 = st.columns(4)
            if bc1.button("⏮ First", use_container_width=True, disabled=(ui_page == 0),              key="b_first"):
                st.session_state.ui_page = 0;              st.rerun()
            if bc2.button("◀ Prev",  use_container_width=True, disabled=(ui_page == 0),              key="b_prev"):
                st.session_state.ui_page = ui_page - 1;   st.rerun()
            if bc3.button("Next ▶",  use_container_width=True, disabled=(ui_page >= total_pages - 1), key="b_next"):
                st.session_state.ui_page = ui_page + 1;   st.rerun()
            if bc4.button("Last ⏭",  use_container_width=True, disabled=(ui_page >= total_pages - 1), key="b_last"):
                st.session_state.ui_page = total_pages - 1; st.rerun()

    else:
        st.markdown("""
        <div class="info-box">
        👈 Pilih <strong>Fetch Featured Image Post</strong> untuk featured image post terpublikasi,
        atau <strong>Fetch All Media</strong> untuk seluruh koleksi media WordPress.
        </div>
        """, unsafe_allow_html=True)

        st.markdown("### 📖 How it works")
        steps = [
            ("1️⃣ Fetch", "Pilih <strong>Fetch Featured Image Post</strong> atau <strong>Fetch All Media</strong> di sidebar."),
            ("2️⃣ Review", "Browse images 12 per page. Click any image to preview it full-size in a new tab."),
            ("3️⃣ Fix & Save", "Edit title/alt manually, auto-fill from filename, run AI Scan, or append the suffix in bulk."),
        ]
        cols = st.columns(3)
        for col, (title, desc) in zip(cols, steps):
            col.markdown(f"""
            <div class="media-card" style="height:100%">
                <h3 style="font-size:1rem;margin:0 0 0.5rem">{title}</h3>
                <p style="font-size:0.82rem;color:var(--muted);margin:0">{desc}</p>
            </div>
            """, unsafe_allow_html=True)

        if not API_KEY:
            st.markdown("""
            <div class="info-box">
            ⚠️ <strong>OpenAI key not detected.</strong> Set <code>OPENAI_API_KEY</code> env var to enable AI scanning.
            Model defaults to <code>gpt-4o</code> unless <code>OPENAI_API_MODEL</code> is also set.
            </div>
            """, unsafe_allow_html=True)


with resize_tab:
    render_resize_studio(st.session_state.media_list)
