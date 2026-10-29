"""Guard the side panel's launchpad (browser-extension/sandbox/launchpad.html +
launchpad-init.js), transformed from the canonical template by
scripts/build_extension_launchpad.build() and written by build_extension_sidepanel.py.
(The root browser-extension/launchpad.html copy, which nothing opened and MV3's CSP could
not render, was deleted 2026-10-15; tests/test_extension_sidepanel_build.py pins the
committed sandbox files in sync.)

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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
SANDBOX = EXT / "sandbox"


def test_no_inline_script_body_mv3():
    """MV3 extension pages refuse inline <script> with a body. The page must use
    only `<script src=...>` — an inline body silently breaks the whole launchpad."""
    html = (SANDBOX / "launchpad.html").read_text(encoding="utf-8")
    inline = [b.strip() for b in re.findall(r"<script>(.*?)</script>", html, re.DOTALL)]
    assert not any(inline), f"MV3 violation: {len(inline)} inline <script> block(s) with a body"
    assert '<script src="./launchpad-init.js"></script>' in html


def test_init_js_fetches_data_and_mounts():
    init = (SANDBOX / "launchpad-init.js").read_text(encoding="utf-8")
    # Pulls page-data from the host (slice 1), defines pageData from it, mounts Vue.
    assert "query_kind: 'launchpad_data'" in init
    assert "const pageData = resp.pageData" in init
    assert "createApp" in init
    # Degrades with an actionable message when the host is unreachable.
    assert "Native Messaging" in init


def test_no_user_data_baked_into_extension_page():
    """page_data={} at build time → an empty blob; the sidebar is a mount, not
    baked council titles. So the committed files carry NO user data."""
    html = (SANDBOX / "launchpad.html").read_text(encoding="utf-8")
    # The two real no-baked-data guarantees: an empty page-data blob (so no
    # council/lens/routing data flows in) and a sidebar MOUNT (not baked titles).
    assert 'id="page-data">{}<' in html, "page-data blob must be empty ({}) — no baked data"
    assert 'id="recent-sidebar-mount"' in html, "sidebar must be a mount, not baked titles"
    # Any real home path would be a leak. The lone allowed absolute-path string is
    # the import-export input's STATIC placeholder; flag anything else.
    allowed_static = "/Users/you/Downloads/Takeout"
    for f in ("launchpad.html", "launchpad-init.js"):
        text = (SANDBOX / f).read_text(encoding="utf-8").replace(allowed_static, "")
        for needle in ("/Users/", "/home/"):
            assert needle not in text, f"real absolute path leaked into {f} (near {needle!r})"


def test_init_js_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable to syntax-check launchpad-init.js")
    r = subprocess.run([node, "--check", str(SANDBOX / "launchpad-init.js")],
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
    from trinity_local.vendor import EXTENSION_VENDOR_FILES, publish_vendor_files

    publish_vendor_files(tmp_path, names=EXTENSION_VENDOR_FILES)
    fresh = {p.name: p.read_bytes() for p in (tmp_path / "vendor").iterdir() if p.is_file()}
    committed = {p.name: p.read_bytes() for p in (EXT / "vendor").iterdir() if p.is_file()}
    assert set(committed) == set(fresh), (
        f"extension vendor file set drifted — missing {sorted(set(fresh) - set(committed))}, "
        f"extra {sorted(set(committed) - set(fresh))}"
    )
    drifted = sorted(n for n in fresh if committed[n] != fresh[n])
    assert not drifted, f"committed extension vendor bytes drifted from the publisher: {drifted}"


def test_extension_ships_only_the_vendor_files_its_pages_load():
    """Every vendored file the extension ships is one its pages reference, and every one
    they reference ships. The full set (d3, marked, petite-vue's ES build) serves the
    file:// memory viewer, which the extension does not include."""
    import re

    from trinity_local.vendor import EXTENSION_VENDOR_FILES

    pages = [p for p in (EXT / "sandbox").iterdir() if p.suffix in {".html", ".js"}]
    referenced = {m for p in pages for m in re.findall(r"vendor/([A-Za-z0-9_.-]+)", p.read_text(encoding="utf-8"))}
    licenses = {n for n in EXTENSION_VENDOR_FILES if n.endswith("-OFL.txt")}
    assert referenced | licenses == set(EXTENSION_VENDOR_FILES), (
        f"unreferenced: {sorted(set(EXTENSION_VENDOR_FILES) - referenced - licenses)}; "
        f"missing: {sorted(referenced - set(EXTENSION_VENDOR_FILES))}"
    )


def test_the_dead_extension_pages_stay_gone():
    """The toolbar popup (no default_popup could open it) and the root launchpad copy
    (nothing opened it; MV3 CSP could not render it) were deleted 2026-10-15. Neither the
    files nor the manifest entry that exposed popup.html to every site may return."""
    import json
    for name in ("popup.html", "popup.js", "harness-snippets.js", "launchpad.html", "launchpad-init.js"):
        assert not (EXT / name).exists(), f"browser-extension/{name} is back"
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    exposed = [r for w in m.get("web_accessible_resources") or [] for r in w.get("resources") or []]
    assert "popup.html" not in exposed
