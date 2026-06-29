"""Guard: the public landing page (keepwhatworks.com) has no dead internal links.

GitHub Pages serves `docs/` at the custom domain `keepwhatworks.com` (verified via
`gh api repos/keepwhatworks/trinity/pages`: source `main:/docs`, HTTPS-enforced,
public). `docs/index.html` is the marketing front door — every potential user's
first impression — and it links to 5 essay pages (`articles/*.html`) + assets
(`style.css`, `favicon.png`); the essays link to their own `img/` + `raw/` assets.
Nothing tested any of it: rename or move an essay/asset and the LIVE site serves a
404 on the conversion surface, with the whole pytest suite still green.

This is fast (HTML parse, no browser) so it runs in the DEFAULT CI suite — unlike
the `browser`-marked render guards, link rot is caught on every PR. Same class as
the launchpad deep-link-resolution smoke (Surface 34), but for the public web.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"

_REF_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"')
_EXTERNAL_PREFIXES = ("http://", "https://", "#", "mailto:", "data:", "tel:", "//")


def _internal_refs(html_file: Path) -> list[str]:
    """Every href/src in the file that points at a same-site resource."""
    out = []
    for ref in _REF_RE.findall(html_file.read_text(encoding="utf-8")):
        if ref.startswith(_EXTERNAL_PREFIXES):
            continue
        clean = ref.split("#", 1)[0].split("?", 1)[0]
        if clean:
            out.append(ref)
    return out


def _resolves(html_file: Path, ref: str) -> bool:
    """A ref resolves if it exists relative to the file's dir OR the docs root
    (Pages serves /docs as the site root, so a leading-slash ref is docs-relative)."""
    clean = ref.split("#", 1)[0].split("?", 1)[0]
    cand_filedir = (html_file.parent / clean)
    cand_docsroot = (DOCS / clean.lstrip("/"))
    return cand_filedir.exists() or cand_docsroot.exists()


def _html_files() -> list[Path]:
    return [DOCS / "index.html", *sorted((DOCS / "articles").glob("*.html"))]


def test_landing_page_and_essays_have_no_dead_internal_links():
    """Every internal href/src across the published docs/ HTML must resolve to a
    real file — a dead link here is a 404 on the live public site."""
    broken: list[str] = []
    total = 0
    for hf in _html_files():
        for ref in _internal_refs(hf):
            total += 1
            if not _resolves(hf, ref):
                broken.append(f"{hf.relative_to(DOCS)} → {ref}")
    assert total >= 20, f"suspiciously few internal refs scanned ({total}) — did docs/ move?"
    assert not broken, (
        "dead internal link(s) on the public site (keepwhatworks.com) — these 404 "
        "for real visitors:\n  " + "\n  ".join(broken)
    )


def test_link_scanner_actually_detects_a_dead_ref(tmp_path):
    """Mutation guard: prove the scanner bites. A page referencing a missing file
    must be flagged — otherwise test_..._no_dead_internal_links is vacuously green."""
    fake = tmp_path / "page.html"
    fake.write_text('<a href="does-not-exist-xyz.html">x</a><img src="missing.png">')
    refs = _internal_refs(fake)
    assert "does-not-exist-xyz.html" in refs and "missing.png" in refs
    # Neither resolves relative to a tmp dir with no such files.
    assert not _resolves(fake, "does-not-exist-xyz.html")
    assert not _resolves(fake, "missing.png")


SITE = "https://keepwhatworks.com"


def _meta(html_text: str, *, prop: str | None = None, name: str | None = None) -> str | None:
    attr, val = ("property", prop) if prop else ("name", name)
    m = re.search(rf'<meta\s+{attr}="{re.escape(val or "")}"\s+content="([^"]*)"', html_text)
    return m.group(1) if m else None


def _title(html_text: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html_text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _canonical_url(hf: Path) -> str:
    rel = hf.relative_to(DOCS).as_posix()
    return SITE + "/" if rel == "index.html" else f"{SITE}/{rel}"


def test_public_pages_have_social_share_metadata():
    """Every public page must carry OG + Twitter Card + canonical so a shared link
    renders a PREVIEW (title/description/image), not a bare URL — a real conversion
    factor for a marketing+essays site. og:title/url must MATCH the page's own
    <title>/canonical so they can't silently drift apart."""
    missing: list[str] = []
    for hf in _html_files():
        t = hf.read_text(encoding="utf-8")
        page = hf.relative_to(DOCS)
        for prop in ("og:title", "og:description", "og:url", "og:type"):
            if _meta(t, prop=prop) is None:
                missing.append(f"{page}: no {prop}")
        if _meta(t, name="twitter:card") is None:
            missing.append(f"{page}: no twitter:card")
        if 'rel="canonical"' not in t:
            missing.append(f"{page}: no canonical link")
        ogt = _meta(t, prop="og:title")
        if ogt is not None and ogt != _title(t):
            missing.append(f"{page}: og:title {ogt!r} != <title> {_title(t)!r}")
        ogu = _meta(t, prop="og:url")
        if ogu is not None and ogu != _canonical_url(hf):
            missing.append(f"{page}: og:url {ogu!r} != canonical {_canonical_url(hf)!r}")
    assert not missing, (
        "social-share metadata gaps (shared links render as bare URLs):\n  " + "\n  ".join(missing)
    )


def test_og_image_targets_exist():
    """A social card pointing at a missing image renders a blank/broken preview."""
    broken: list[str] = []
    for hf in _html_files():
        t = hf.read_text(encoding="utf-8")
        for img in re.findall(r'(?:og:image|twitter:image)"\s+content="([^"]+)"', t):
            rel = img[len(SITE) + 1:] if img.startswith(SITE + "/") else img
            if not (DOCS / rel).exists():
                broken.append(f"{hf.relative_to(DOCS)} → {img}")
    assert not broken, "og:image/twitter:image points at a missing file:\n  " + "\n  ".join(broken)


def test_sitemap_and_robots_present_and_complete():
    """A sitemap + robots make the public essays crawlable/indexable."""
    sitemap, robots = DOCS / "sitemap.xml", DOCS / "robots.txt"
    assert sitemap.is_file(), "docs/sitemap.xml missing — hurts crawl/discovery of the essays"
    assert robots.is_file(), "docs/robots.txt missing"
    smap = sitemap.read_text(encoding="utf-8")
    for hf in _html_files():
        assert _canonical_url(hf) in smap, f"sitemap.xml omits {_canonical_url(hf)}"
    assert "sitemap.xml" in robots.read_text(encoding="utf-8").lower(), "robots.txt doesn't reference the sitemap"


def test_landing_install_command_points_at_a_real_script():
    """The hero's one-line install (`curl … /main/scripts/install.sh | bash`) must
    point at a script that actually exists at that repo path — a broken install
    command on the front door is the worst possible first impression."""
    index = (DOCS / "index.html").read_text(encoding="utf-8")
    m = re.search(r"raw\.githubusercontent\.com/[^/]+/[^/]+/main/([^\s|\"<]+)", index)
    assert m, "could not find the raw.githubusercontent.com install URL in the landing page"
    repo_rel = m.group(1)
    assert (REPO / repo_rel).is_file(), (
        f"the landing page's install command curls `{repo_rel}` from main, but that "
        "file does not exist in the repo — the public install one-liner 404s"
    )


# --- Self-hosted / no-third-party-asset guard for the PUBLIC site ----------------
# The hero promises "Your transcripts never leave your machine" and the whole
# product is sold on data sovereignty. A public site that pulls a webfont from
# fonts.gstatic.com, or a script from a CDN, quietly contradicts that promise on
# the very page making it — and the dead-link guard won't catch it (an external
# font <link> isn't a "dead" link). This is the docs/ analog of the app's
# tests/test_no_cdn_in_rendered_html.py. Verified clean in a real browser
# 2026-06-02 (Performance API: offOrigin requests = NONE on both templates).

# Asset-LOADING references only — the browser fetches these at render time.
# <a href> is navigation (GitHub / X links are fine) and is deliberately excluded.
_ASSET_TAG_RE = re.compile(
    r'<(?:link|script|img|source)\b[^>]*?\b(?:href|src|srcset)\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)', re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r'@import\s+(?:url\(\s*)?["\']([^"\']+)["\']', re.IGNORECASE)


def _host_of(url: str) -> str | None:
    """Host for an absolute (or protocol-relative) URL; None if the ref is
    relative (self-hosted) so the caller skips it."""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return None
    return url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0].lower()


def _self_host(h: str) -> bool:
    return h == "keepwhatworks.com" or h.endswith(".keepwhatworks.com")


def _external_asset_loads() -> list[str]:
    """Every render-time asset reference across the served site (HTML + style.css)
    whose host is a third party. Empty == fully self-hosted."""
    offenders: list[str] = []
    sources: list[tuple[str, list[str]]] = []
    for hf in _html_files():
        sources.append((hf.relative_to(DOCS).as_posix(),
                        _ASSET_TAG_RE.findall(hf.read_text(encoding="utf-8"))))
    css = DOCS / "style.css"
    if css.is_file():
        ctext = css.read_text(encoding="utf-8")
        sources.append(("style.css",
                        _CSS_URL_RE.findall(ctext) + _CSS_IMPORT_RE.findall(ctext)))
    for where, refs in sources:
        for ref in refs:
            host = _host_of(ref.strip())
            if host is not None and not _self_host(host):
                offenders.append(f"{where} → {ref}")
    return offenders


def test_public_site_loads_no_third_party_assets():
    """No render-time asset (font / css / js / image) on keepwhatworks.com may load
    from a third-party host — the self-hosting that backs the privacy promise.
    Relative refs and the site's own domain pass; a CDN/Google-Fonts host fails."""
    offenders = _external_asset_loads()
    assert not offenders, (
        "The PUBLIC site loads asset(s) from a third party, contradicting the "
        '"never leaves your machine" promise on the page that makes it. Vendor the '
        "asset under docs/ and reference it relatively:\n  " + "\n  ".join(offenders)
    )


def test_cdn_asset_scanner_actually_bites(tmp_path):
    """Mutation guard: prove the third-party-asset scanner isn't vacuous. A page
    with a Google-Fonts <link> and a CDN <script> must be flagged; a self-domain
    canonical and a relative font url() must NOT be."""
    page = tmp_path / "index.html"
    page.write_text(
        '<link rel="canonical" href="https://keepwhatworks.com/">'        # self — ok
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css?x">'  # CDN — bad
        '<script src="https://cdn.jsdelivr.net/npm/x.js"></script>'          # CDN — bad
        '<a href="https://github.com/vishigondi">gh</a>'                  # nav — ignored
        '<img src="img/local.jpg">',                                     # relative — ok
        encoding="utf-8",
    )
    refs = _ASSET_TAG_RE.findall(page.read_text(encoding="utf-8"))
    bad = [r for r in refs if (h := _host_of(r)) is not None and not _self_host(h)]
    assert "https://fonts.googleapis.com/css?x" in bad
    assert "https://cdn.jsdelivr.net/npm/x.js" in bad
    assert "https://keepwhatworks.com/" not in bad          # self-domain passes
    assert "https://github.com/vishigondi" not in refs      # <a> not an asset tag
    assert "img/local.jpg" not in bad                       # relative passes
