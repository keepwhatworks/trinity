"""Persona audit P06 + Theme K #1 regression guard: rendered HTML pages
must reference no third-party CDN. The privacy claim ("never leaves
your machine") is structural; one stray `unpkg.com` import voids it.

Failing this test means someone added a `<script src="https://...">`
or `import ... from 'https://...'` line; replace with a vendored
file under src/trinity_local/data/vendor/ + reference via
./vendor/<name>.
"""
from __future__ import annotations


import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


_CDN_DOMAINS = (
    "unpkg.com",
    "jsdelivr.net",
    "cdnjs.cloudflare.com",
    "esm.sh",
    "skypack.dev",
)


def _assert_no_cdn(html: str, page_name: str) -> None:
    """Surface the offending hit so the test failure points at the regress."""
    for domain in _CDN_DOMAINS:
        hits = [
            line for line in html.splitlines()
            if domain in line and not line.strip().startswith("//")
        ]
        assert not hits, (
            f"{page_name} loads {domain} — re-vendored a CDN reference. "
            f"First hit: {hits[0].strip()[:120]}"
        )


class TestNoCdnReferences:
    def test_launchpad_html(self, isolated_home):
        from trinity_local.launchpad_page import write_portal_html
        path = write_portal_html()
        _assert_no_cdn(path.read_text(), "launchpad.html")

    def test_memory_viewer_html(self, isolated_home):
        from trinity_local.memory_viewer import render_memory_viewer_html
        html = render_memory_viewer_html()
        _assert_no_cdn(html, "memory.html")

    def test_council_review_module_constants(self):
        """Direct check on the module string — catches regress even
        before render. The PETITE_VUE_IIFE constant must point to a
        local path, not a CDN URL.

        Renamed from PETITE_VUE_MODULE (2026-05-19) when the launchpad
        switched from `<script type="module"> + import` to the IIFE
        build because Chrome blocks ES module imports on file://.
        See trinity_local.vendor._wrap_petite_vue_as_iife.
        """
        from trinity_local import council_review, launchpad_template

        for module, name in (
            (council_review, "council_review"),
            (launchpad_template, "launchpad_template"),
        ):
            ptv = getattr(module, "PETITE_VUE_IIFE", "")
            assert ptv, f"{name}.PETITE_VUE_IIFE missing or empty"
            for domain in _CDN_DOMAINS:
                assert domain not in ptv, (
                    f"{name}.PETITE_VUE_IIFE references {domain} — must be ./vendor/"
                )


class TestFontFaceCrossDirectoryPath:
    """The Calm/Teal font (Hanken Grotesk) @font-face in SHARED_CSS must
    reference `../portal_pages/vendor/` — NOT `./vendor/`.

    SHARED_CSS is inlined into BOTH portal_pages/ pages (launchpad, memory viewer)
    AND review_pages/ sub-pages (live council). The font is published only to
    portal_pages/vendor/. `./vendor/` resolves to review_pages/vendor/ on a sub-page
    → 404 (the font silently falls back to system grotesk). `../portal_pages/vendor/`
    resolves to ~/.trinity/portal_pages/vendor/ from BOTH dirs (verified under file://
    + the served smoke). Caught live as a Surface 35 console 404 (v1.7.304); this
    locks it in the fast suite so a `./vendor/` simplification can't silently re-break
    sub-page fonts."""

    def test_font_face_path_is_cross_directory_safe(self):
        from trinity_local.design_system import SHARED_CSS
        assert "@font-face" in SHARED_CSS, "SHARED_CSS lost its @font-face block"
        assert "../portal_pages/vendor/HankenGrotesk" in SHARED_CSS, (
            "Hanken Grotesk @font-face must use ../portal_pages/vendor/ so it resolves "
            "from review_pages/ sub-pages too (./vendor/ 404s the font there)."
        )
        assert "./vendor/HankenGrotesk" not in SHARED_CSS, (
            "@font-face uses ./vendor/HankenGrotesk — that 404s the display font on "
            "review_pages/ sub-pages (live council). Use ../portal_pages/vendor/."
        )


class TestVendorFilesPublished:
    """When refresh_launchpad runs, ~/.trinity/portal_pages/vendor/ gets
    every file declared in vendor.VENDORED_FILES."""

    def test_refresh_publishes_all_files(self, isolated_home):
        from trinity_local.refresh import refresh_launchpad
        from trinity_local.vendor import VENDORED_FILES
        from trinity_local.state_paths import portal_pages_dir

        refresh_launchpad()
        vendor_dir = portal_pages_dir() / "vendor"
        for name in VENDORED_FILES:
            assert (vendor_dir / name).exists(), (
                f"vendor file {name} not published — refresh_launchpad lost the wiring"
            )

    def test_publish_is_idempotent_on_unchanged(self, isolated_home):
        from trinity_local.refresh import refresh_launchpad
        from trinity_local.vendor import publish_vendor_files
        from trinity_local.state_paths import portal_pages_dir
        import time

        refresh_launchpad()
        # Capture mtimes after first publish
        vendor_dir = portal_pages_dir() / "vendor"
        mtimes_before = {p.name: p.stat().st_mtime for p in vendor_dir.iterdir()}
        time.sleep(0.05)
        written = publish_vendor_files(portal_pages_dir())
        assert written == [], "second publish wrote files despite content match"
        mtimes_after = {p.name: p.stat().st_mtime for p in vendor_dir.iterdir()}
        for name in mtimes_before:
            assert mtimes_before[name] == mtimes_after[name], (
                f"vendor/{name} rewritten on idempotent re-publish"
            )


class TestRefreshVendorScript:
    """The maintainer-side refresh script must exist and cover every
    file in VENDORED_FILES. If someone adds a new vendored dep without
    extending the script, future-me hits the "TODO: write the refresh
    script" trap the v1.7 audit already closed once.

    Earned 2026-05-16: vendor.py's docstring referenced
    `scripts/refresh-vendor.sh (TODO)` for weeks with no script on
    disk. The fix WAS to write the script — this guard prevents the
    docstring from lying again.
    """

    def test_refresh_script_exists_and_is_executable(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        script = repo / "scripts" / "refresh-vendor.sh"
        assert script.exists(), (
            "scripts/refresh-vendor.sh missing — vendor.py's docstring "
            "promises it. Either restore the script or update the "
            "docstring to point at the new recipe."
        )
        assert script.stat().st_mode & 0o111, (
            "scripts/refresh-vendor.sh exists but isn't executable. "
            "`chmod +x scripts/refresh-vendor.sh` to fix."
        )

    def test_refresh_script_covers_all_vendored_files(self):
        from pathlib import Path
        from trinity_local.vendor import VENDORED_FILES

        repo = Path(__file__).resolve().parent.parent
        script_text = (repo / "scripts" / "refresh-vendor.sh").read_text()
        for name in VENDORED_FILES:
            assert name in script_text, (
                f"refresh-vendor.sh doesn't pin a URL for {name!r}. "
                f"Add a `\"{name} https://...\"` line to the URLS array "
                f"so `bash scripts/refresh-vendor.sh` actually refreshes "
                f"every file ``VENDORED_FILES`` declares."
            )


def test_shared_head_declares_an_inline_favicon():
    """Every served page (launchpad / council / review / memory) builds its <head>
    from design_system.render_html_head. Without a favicon the browser auto-requests
    /favicon.ico and 404s on EVERY load — noise in the HTTP-log health signal the
    browser smoke gate reads, and no brand mark on the cockpit tab. Verified in a
    real browser 2026-06-02. The head carries an inline data-URI SVG favicon (no
    extra file, no extra request); this guards against its silent removal."""
    from trinity_local.design_system import render_html_head
    head = render_html_head("Test")
    assert 'rel="icon"' in head, (
        "render_html_head dropped its favicon — every served page will 404 on "
        "/favicon.ico (HTTP-log health noise)."
    )
    assert "data:image/svg+xml" in head, (
        "the favicon should be an inline data-URI SVG so it needs no extra file or "
        "network request (and can't itself 404)."
    )


def test_every_served_page_renders_the_favicon(isolated_home):
    """v1.7.318: the favicon must be in the RENDERED HTML of EVERY served page, not
    just render_html_head. The memory viewer builds its OWN <head> (it doesn't use
    render_html_head), so the v1.7.317 fix missed it — it kept 404ing on
    /favicon.ico while the launchpad/council had the icon (found by browser-testing
    the topology graph). Render each surface and assert the shared favicon is in it;
    both must use the SAME design_system.FAVICON_LINK constant so a future die-mark
    swap is one edit."""
    from trinity_local.design_system import FAVICON_LINK
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.memory_viewer import render_memory_viewer_html

    launchpad = write_portal_html().read_text(encoding="utf-8")
    memory = render_memory_viewer_html()
    for name, html in (("launchpad", launchpad), ("memory viewer", memory)):
        assert 'rel="icon"' in html, (
            f"{name} HTML declares no favicon — the browser will request /favicon.ico "
            f"and 404 on every load (HTTP-log health noise)."
        )
        assert "data:image/svg+xml" in html, (
            f"{name} favicon should be the inline data-URI SVG (no extra file/request)."
        )
        assert FAVICON_LINK in html, (
            f"{name} must use the shared design_system.FAVICON_LINK (DRY — one place "
            f"to swap the die mark), got a divergent copy."
        )


def test_memory_viewer_is_on_the_brand_fonts(isolated_home):
    """v1.7.320: the memory viewer ('own your taste' surface) builds its OWN
    <head>/<style>, so the teal migration moved the launchpad/council to Hanken +
    JetBrains via SHARED_CSS but LEFT the memory viewer on system fonts (-apple-
    system / ui-monospace) — off-brand on a flagship surface. It now imports the
    shared design_system.FONT_FACE_CSS and uses Hanken (body) + JetBrains (mono).
    Guard against silent regression to system fonts on this divergent-head surface."""
    from trinity_local.memory_viewer import render_memory_viewer_html
    html = render_memory_viewer_html()
    # the shared @font-face must be present so the woff2 actually load
    assert "HankenGrotesk-400.woff2" in html and "JetBrainsMono-400.woff2" in html, (
        "memory viewer dropped the shared @font-face block — it falls back to "
        "system fonts, off-brand vs the launchpad/council."
    )
    assert 'font-family: "Hanken Grotesk"' in html, (
        "memory viewer body must lead with Hanken Grotesk, not -apple-system."
    )
    assert '"JetBrains Mono", ui-monospace' in html, (
        "memory viewer mono (JSON/scores) must lead with JetBrains Mono."
    )
    # the @font-face path must be the cross-dir-safe form (resolves from portal_pages/)
    assert "../portal_pages/vendor/HankenGrotesk" in html, (
        "memory viewer @font-face must use ../portal_pages/vendor/ (resolves under "
        "portal_pages/memory.html); see TestFontFaceCrossDirectoryPath."
    )


def test_every_declared_font_face_is_vendored():
    """Reverse of TestVendorFilesPublished (which covers VENDORED_FILES ->
    published): every woff2 the @font-face block DECLARES in
    design_system.FONT_FACE_CSS must be a real entry in vendor.VENDORED_FILES.
    Otherwise the browser requests a font that was never vendored/published, 404s,
    and silently falls back to system fonts — the same declared-but-not-shipped
    class as the v1.7.310 package-data font bug. Together the two directions
    guarantee the brand fonts the CSS promises actually ship."""
    import re
    from trinity_local.design_system import FONT_FACE_CSS
    from trinity_local.vendor import VENDORED_FILES

    declared = set(re.findall(r"vendor/([A-Za-z0-9._-]+\.woff2)", FONT_FACE_CSS))
    vendored = {f for f in VENDORED_FILES if f.endswith(".woff2")}
    assert declared, "FONT_FACE_CSS declares no woff2 @font-face — did the block move?"
    missing = declared - vendored
    assert not missing, (
        f"@font-face declares woff2 NOT in vendor.VENDORED_FILES: {sorted(missing)}. "
        f"They will 404 (never vendored/published) and the brand font falls back to "
        f"system fonts. Add them to VENDORED_FILES + scripts/refresh-vendor.sh, or "
        f"fix the @font-face url()."
    )
