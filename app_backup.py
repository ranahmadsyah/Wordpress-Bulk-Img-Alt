import os
import streamlit as st
import requests
import base64
import re
import time
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ─── Credentials & Config ────────────────────────────────────────────────────
WORDPRESS_URL = "https://www.formulajelita.com/"
USERNAME      = "admin_fji"
APP_PASSWORD  = "aKYZ IHYK kChG slWE XoYz H22g"

API_KEY   = os.getenv("OPENAI_API_KEY")
API_MODEL = os.getenv("OPENAI_API_MODEL", "gpt-4o")

ALT_SUFFIX = "| www.formulajelita.com"
PAGE_SIZE  = 100   # cards shown per page in the UI

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="WP Media Alt Manager",
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

def get_wp_media(per_page: int = 100) -> list:
    creds   = base64.b64encode(f"{USERNAME}:{APP_PASSWORD}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}"}
    base    = WORDPRESS_URL.rstrip("/") + "/wp-json/wp/v2/media"
    all_media, page = [], 1
    while True:
        resp = requests.get(base, headers=headers,
                            params={"per_page": per_page, "page": page}, timeout=30)
        if resp.status_code == 400:
            break
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_media.extend(batch)
        if page >= int(resp.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return all_media

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

def ai_generate_alt(image_url: str, filename: str) -> str:
    if not API_KEY:
        return f"[Error: OPENAI_API_KEY not set] {ALT_SUFFIX}"
    try:
        client     = OpenAI(api_key=API_KEY)
        clean_name = re.sub(r'[-_]+', ' ', re.sub(r'\.[^.]+$', '', filename)).strip()
        response   = client.chat.completions.create(
            model=API_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are an SEO and accessibility expert. "
                            f"Filename hint: '{clean_name}'. "
                            "Carefully describe what you see in this image. "
                            "Write a concise, natural alt text under 100 characters. "
                            "Be specific about the main subject. "
                            "Do NOT include website names or URLs. "
                            "Return ONLY the alt text — no quotes, no explanation, no punctuation at the end."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                ],
            }],
            max_tokens=120,
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
    per_page = st.number_input("Items per WP API page", min_value=10, max_value=100, value=100)

    st.markdown("---")
    st.markdown("## 🔍 Filters")
    filter_options = ["All Media", "Missing Alt Text", "Has Alt Text",
                      "Missing Suffix", "Random Filename", "AI Suggested"]
    show_filter = st.selectbox(
        "Show",
        filter_options,
        index=filter_options.index(st.session_state.active_filter),
        key="sidebar_filter",
    )
    if show_filter != st.session_state.active_filter:
        st.session_state.active_filter = show_filter
        st.session_state.ui_page = 0
    mime_filter = st.multiselect(
        "File Types",
        ["image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"],
        default=["image/jpeg", "image/png", "image/webp"],
    )

    st.markdown("---")
    fetch_btn = st.button("🔄 Fetch All Media", type="primary", use_container_width=True)


# ─── Main Header ─────────────────────────────────────────────────────────────
st.markdown("""
<div class="app-header">
  <h1>🖼️ WP Media Alt Manager</h1>
  <p>Bulk manage WordPress image alt texts — powered by GPT-4o Vision</p>
</div>
""", unsafe_allow_html=True)


# ─── Fetch Media ─────────────────────────────────────────────────────────────
if fetch_btn:
    with st.spinner("Connecting to WordPress…"):
        try:
            media = get_wp_media(int(per_page))
            st.session_state.media_list  = media
            st.session_state.edits       = {}
            st.session_state.title_edits = {}
            st.session_state.connected   = True
            st.session_state.ui_page     = 0
            st.session_state.edit_gen   += 1
            st.success(f"✅ Fetched **{len(media)}** media items.")
        except requests.exceptions.ConnectionError:
            st.error("❌ Could not connect. Check WORDPRESS_URL.")
        except requests.exceptions.HTTPError as e:
            st.error(f"❌ HTTP Error: {e}")
        except Exception as e:
            st.error(f"❌ Error: {e}")


# ─── Stats + Main UI ─────────────────────────────────────────────────────────
media_list = st.session_state.media_list

if media_list:
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
        border_color = "#7c6ff7" if active else "#252b38"
        bg_color     = "rgba(124,111,247,0.1)" if active else "#161923"
        col.markdown(f"""
        <div style="background:{bg_color};border:1.5px solid {border_color};border-radius:12px;
                    padding:1rem 1.2rem;cursor:pointer;transition:all .2s;">
            <div style="font-size:0.78rem;color:#5a6070;margin-bottom:4px">{label}</div>
            <div style="font-size:1.6rem;font-weight:800;color:#f7c948;">{value}</div>
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

    # Sync show_filter from session state (may have been set by a stat card click)
    show_filter = st.session_state.active_filter

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
                ["Current page only", "Random filenames only", "All missing alt", "All images"],
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
                    else:  # All images — full library, user explicitly chose this
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
                            alt = ai_generate_alt(m.get("source_url",""), fname)
                            st.session_state.edits.setdefault(m["id"], {}).update(
                                alt=alt, ai_generated=True
                            )
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
        fname       = m.get("source_url","").split("/")[-1]
        mid         = m["id"]
        current_alt = get_current_alt(mid, m)
        is_ai       = st.session_state.edits.get(mid, {}).get("ai_generated", False)
        sf = st.session_state.active_filter
        if sf == "Missing Alt Text"  and current_alt.strip():             return False
        if sf == "Has Alt Text"      and not current_alt.strip():         return False
        if sf == "Random Filename"   and not is_random_filename(fname):   return False
        if sf == "AI Suggested"      and not is_ai:                       return False
        if sf == "Missing Suffix"    and (not current_alt.strip() or has_suffix(current_alt)): return False
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
                    is_ai_gen = stored.get("ai_generated", False)

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
                    st.markdown(f"""
                    <div class="media-card">
                        <a href="{src}" target="_blank" title="Open full-size image">
                            <img src="{thumb_url}" alt="{cur_alt}" loading="lazy"
                                 onerror="this.style.display='none'"/>
                        </a>
                        <div style="margin:4px 0">{type_badge} {suffix_badge}</div>
                        <div style="font-size:0.72rem;color:#5a6070;font-family:'Space Mono',monospace;
                                    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                             title="{fname}">{fname}</div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Title field
                    new_title = st.text_input(
                        "Title",
                        value=cur_title,
                        key=f"title_{mid}_{gen}",
                        placeholder="Media title…",
                        label_visibility="collapsed",
                    )
                    st.caption("📝 Title")

                    if new_title != cur_title:
                        st.session_state.title_edits[mid] = new_title

                    # Alt text field — key includes gen so it refreshes after AI/suffix
                    new_alt = st.text_area(
                        "Alt text",
                        value=cur_alt,
                        key=f"alt_{mid}_{gen}",
                        height=72,
                        label_visibility="collapsed",
                        placeholder=f"Alt text… {ALT_SUFFIX}",
                    )
                    st.caption("🏷️ Alt text")

                    if new_alt != cur_alt:
                        st.session_state.edits.setdefault(mid, {}).update(
                            alt=new_alt, ai_generated=False
                        )

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
                            st.session_state.edit_gen += 1
                            st.rerun()

                    with b3:
                        if st.button("🤖 AI", key=f"ai_{mid}", use_container_width=True,
                                     help="Generate alt text with AI"):
                            if not API_KEY:
                                st.error("Set `OPENAI_API_KEY`.")
                            else:
                                with st.spinner("Scanning…"):
                                    ai_alt = ai_generate_alt(src, fname)
                                st.session_state.edits.setdefault(mid, {}).update(
                                    alt=ai_alt, ai_generated=True
                                )
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
    👈 Click <strong>Fetch All Media</strong> in the sidebar to load your WordPress media library.
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### 📖 How it works")
    steps = [
        ("1️⃣ Fetch", "Click <strong>Fetch All Media</strong> — credentials are pre-configured."),
        ("2️⃣ Review", "Browse images 12 per page. Click any image to preview it full-size in a new tab."),
        ("3️⃣ Fix & Save", "Edit title/alt manually, auto-fill from filename, run AI Scan, or append the suffix in bulk."),
    ]
    cols = st.columns(3)
    for col, (title, desc) in zip(cols, steps):
        col.markdown(f"""
        <div class="media-card" style="height:100%">
            <h3 style="font-size:1rem;margin:0 0 0.5rem">{title}</h3>
            <p style="font-size:0.82rem;color:#8892a0;margin:0">{desc}</p>
        </div>
        """, unsafe_allow_html=True)

    if not API_KEY:
        st.markdown("""
        <div class="info-box">
        ⚠️ <strong>OpenAI key not detected.</strong> Set <code>OPENAI_API_KEY</code> env var to enable AI scanning.
        Model defaults to <code>gpt-4o</code> unless <code>OPENAI_API_MODEL</code> is also set.
        </div>
        """, unsafe_allow_html=True)