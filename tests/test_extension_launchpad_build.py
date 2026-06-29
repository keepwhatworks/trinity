"""Guard the in-extension launchpad artifacts (browser-extension/launchpad.html +
launchpad-init.js), generated from the canonical template by
scripts/build_extension_launchpad.py.

These are committed static files an MV3 extension page loads, so they're never
imported by the rest of the suite — a launchpad template change could silently
leave them stale, or a regression could reintroduce an inline <script> (which
MV3 silently refuses to run, breaking the whole page). This pins: they're in
sync with the template, MV3-clean (no inline script body), they fetch page-data
over Native Messaging, and they bake NO user data.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"


def _build():
    # Import the build script lazily + scope the sys.path insert to this call —
    # a module-level sys.path mutation leaks into the whole suite (guarded by
    # test_no_module_level_env_mutation).
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import build_extension_launchpad

    return build_extension_launchpad.build()


def test_committed_extension_launchpad_in_sync():
    """The committed launchpad.html + launchpad-init.js must equal a fresh build
    from the template — else the extension ships a stale UI. Regenerate with
    `python scripts/build_extension_launchpad.py`."""
    html, init_js = _build()
    committed_html = (EXT / "launchpad.html").read_text(encoding="utf-8")
    committed_init = (EXT / "launchpad-init.js").read_text(encoding="utf-8")
    assert committed_html == html, (
        "browser-extension/launchpad.html drifted from the template — run "
        "`python scripts/build_extension_launchpad.py` and commit."
    )
    assert committed_init == init_js, (
        "browser-extension/launchpad-init.js drifted from the template — run "
        "`python scripts/build_extension_launchpad.py` and commit."
    )


def test_no_inline_script_body_mv3():
    """MV3 extension pages refuse inline <script> with a body. The page must use
    only `<script src=...>` — an inline body silently breaks the whole launchpad."""
    html = (EXT / "launchpad.html").read_text(encoding="utf-8")
    inline = [b.strip() for b in re.findall(r"<script>(.*?)</script>", html, re.DOTALL)]
    assert not any(inline), f"MV3 violation: {len(inline)} inline <script> block(s) with a body"
    assert '<script src="./launchpad-init.js"></script>' in html


def test_init_js_fetches_data_and_mounts():
    init = (EXT / "launchpad-init.js").read_text(encoding="utf-8")
    # Pulls page-data from the host (slice 1), defines pageData from it, mounts Vue.
    assert "query_kind: 'launchpad_data'" in init
    assert "const pageData = resp.pageData" in init
    assert "createApp" in init
    # Degrades with an actionable message when the host is unreachable.
    assert "Native Messaging" in init


def test_no_user_data_baked_into_extension_page():
    """page_data={} at build time → an empty blob; the sidebar is a mount, not
    baked council titles. So the committed files carry NO user data."""
    html = (EXT / "launchpad.html").read_text(encoding="utf-8")
    # The two real no-baked-data guarantees: an empty page-data blob (so no
    # council/lens/routing data flows in) and a sidebar MOUNT (not baked titles).
    assert 'id="page-data">{}<' in html, "page-data blob must be empty ({}) — no baked data"
    assert 'id="recent-sidebar-mount"' in html, "sidebar must be a mount, not baked titles"
    # Any real home path would be a leak. The lone allowed absolute-path string is
    # the import-export input's STATIC placeholder; flag anything else.
    allowed_static = "/Users/you/Downloads/Takeout"
    for f in ("launchpad.html", "launchpad-init.js"):
        text = (EXT / f).read_text(encoding="utf-8").replace(allowed_static, "")
        for needle in ("/Users/", "/home/"):
            assert needle not in text, f"real absolute path leaked into {f} (near {needle!r})"


def test_init_js_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable to syntax-check launchpad-init.js")
    r = subprocess.run([node, "--check", str(EXT / "launchpad-init.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"launchpad-init.js has a JS syntax error:\n{r.stderr}"


def test_vendor_assets_published_into_extension():
    vendor = EXT / "vendor"
    assert (vendor / "petite-vue.iife.js").exists()
    assert (vendor / "chart.umd.min.js").exists()


def test_extension_vendor_in_sync_with_publisher(tmp_path):
    """browser-extension/vendor/ is a committed COPY of the canonical vendored
    assets — the one on-disk duplicate that lacked a drift guard. Pin it to what
    `publish_vendor_files` emits (the same call scripts/build_extension_launchpad.py
    makes) so a vendor refresh that forgets the extension can't ship stale JS/fonts
    to extension users. Regenerate: `python scripts/build_extension_launchpad.py`."""
    from trinity_local.vendor import publish_vendor_files

    publish_vendor_files(tmp_path)
    fresh = {p.name: p.read_bytes() for p in (tmp_path / "vendor").iterdir() if p.is_file()}
    committed = {p.name: p.read_bytes() for p in (EXT / "vendor").iterdir() if p.is_file()}
    assert set(committed) == set(fresh), (
        f"extension vendor file set drifted — missing {sorted(set(fresh) - set(committed))}, "
        f"extra {sorted(set(committed) - set(fresh))}"
    )
    drifted = sorted(n for n in fresh if committed[n] != fresh[n])
    assert not drifted, f"committed extension vendor bytes drifted from the publisher: {drifted}"


def test_popup_opens_the_working_launchpad_not_the_broken_extension_page():
    """The popup's 'Open launchpad' must open the WORKING launchpad — the host's
    file:// page (dispatch 'open-launchpad') — NOT chrome-extension://launchpad.html.

    The in-extension launchpad does NOT render: petite-vue evaluates its template
    expressions with new Function(), and MV3 forbids 'unsafe-eval' in extension-page
    CSP (script-src 'self'), so the page shows raw {{ }} (founder report 2026-06-12,
    reproduced as a real chrome-extension:// page). file:// allows eval, so the host
    page renders. Until the launchpad UI is made CSP-safe, opening the in-extension
    page is a bug, not a feature — this guards against re-pointing the popup at it."""
    popup = (EXT / "popup.js").read_text(encoding="utf-8")
    assert 'dispatch("open-launchpad"' in popup, (
        "popup must open the working file:// launchpad via the host's open-launchpad action"
    )
    assert 'chrome.tabs.create({ url: chrome.runtime.getURL("launchpad.html")' not in popup, (
        "popup must NOT open chrome-extension://launchpad.html — it can't render under "
        "MV3 CSP (petite-vue needs unsafe-eval, which extension pages forbid)"
    )
