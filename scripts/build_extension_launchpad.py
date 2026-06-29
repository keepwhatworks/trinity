#!/usr/bin/env python3
"""Generate the in-extension launchpad (browser-extension/launchpad.html +
launchpad-init.js + vendor/) from the canonical launchpad template.

WHY a build step: MV3 forbids inline <script> in extension pages, but the served
file:// launchpad emits its ~76 KB Vue app logic INLINE. So we render the
template ONCE — with an EMPTY data blob (the extension fetches page-data live
over Native Messaging, capture_host query_kind='launchpad_data', slice 1) and a
sidebar MOUNT instead of baked council titles, so NO user data is written — then
externalize the single inline <script> into launchpad-init.js, wrapping its body
in an async bootstrap that pulls page-data from the host before mounting the app.

The served file:// launchpad (launchpad_page.write_portal_html) is UNTOUCHED:
this only READS the template's output and rewrites it for the extension. The
generated files are committed; tests/test_extension_launchpad_build.py fails if
they drift from the template. Regenerate on any launchpad change:

    .venv/bin/python scripts/build_extension_launchpad.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
EXT = REPO / "browser-extension"

# The single line in the inline app script that reads page-data from the baked
# blob. In the extension there's no baked blob — page-data arrives from the host
# — so we split here and inject the fetch bootstrap in its place.
_PAGEDATA_LINE = "const pageData = JSON.parse(document.getElementById('page-data').textContent);"

# Pulls page-data from the capture host via background.js's query forwarder, then
# defines `pageData` + paints the council rail before the original app body runs.
_FETCH_BOOTSTRAP = """(async () => {
  let resp = null;
  try {
    resp = await chrome.runtime.sendMessage({ type: 'query', query_kind: 'launchpad_data' });
  } catch (e) { resp = null; }
  if (!resp || !resp.ok) {
    // The capture host the side panel reaches over Native Messaging is wired up
    // by `install-extension` (writes the Native Messaging manifest), NOT
    // `install-mcp` (registers the MCP server in the CLI harnesses — a separate
    // subsystem that does nothing for this panel). Naming `install-mcp` here was
    // a wrong-CTA dead-end: a user who hit this fallback and ran it reloaded to
    // the exact same error. Match every sibling host-unavailable message
    // (popup.js / background.js / launchpad-init's dispatch-failure reasons /
    // live_council.html) and auto-fill the extension id so it's copy-pasteable.
    var __extId = (window.chrome && chrome.runtime && chrome.runtime.id) || '&lt;ID&gt;';
    document.body.innerHTML =
      '<div style="max-width:640px;margin:80px auto;padding:24px;'
      + 'font:15px/1.5 system-ui,sans-serif;color:#1a2b2e">'
      + '<h2 style="margin:0 0 8px">Trinity launchpad</h2>'
      + '<p>Couldn\\u2019t reach the local Trinity engine over Native Messaging. '
      + 'Wire up the capture host once (<code>trinity-local install-extension '
      + '--extension-id ' + __extId + '</code>), '
      + 'then reload this page.</p></div>';
    return;
  }
  const __sb = document.getElementById('recent-sidebar-mount');
  if (__sb && resp.recentSidebarHtml) {
    // The host's rail-council links point at ../review_pages/live_council.html (the
    // file:// path). In the SIDE PANEL (sandbox) that resolves to a nonexistent
    // chrome-extension://.../review_pages/... page -> "This page has been blocked by
    // Chrome" (founder-caught). Repoint them at the sandbox's OWN ./live_council.html
    // sibling, which renders the council via the host bridge -- same fix as the
    // liveCouncilUrl computed prop. Gate on __TRINITY_HOST_FETCH__ so it only fires
    // in the sandbox (where ./live_council.html exists), never the file:// launchpad.
    __sb.innerHTML = window.__TRINITY_HOST_FETCH__
      ? resp.recentSidebarHtml.split('../review_pages/live_council.html').join('./live_council.html')
      : resp.recentSidebarHtml;
  }
  const pageData = resp.pageData;
"""

_INLINE_APP_RX = re.compile(
    r"<script>\s*\n\s*const \{ createApp \} = window\.__TRINITY_VUE__;.*?</script>",
    re.DOTALL,
)


def build() -> tuple[str, str]:
    """Return (launchpad.html, launchpad-init.js) for the extension. Pure: no
    filesystem writes, no user data (page_data={} → empty blob)."""
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(
        page_data={},  # template is 100% client-side; {} → an empty <script id=page-data> blob
        recent_sidebar='<div id="recent-sidebar-mount"></div>',  # mount, not baked PII
        title="Trinity · Ask all three",
    )

    m = _INLINE_APP_RX.search(html)
    if not m:
        raise SystemExit(
            "build_extension_launchpad: could not find the inline app <script> in the "
            "rendered launchpad — the template's init structure changed; update this build."
        )
    block = m.group(0)
    body = block[len("<script>"):-len("</script>")]
    if _PAGEDATA_LINE not in body:
        raise SystemExit(
            "build_extension_launchpad: the app script no longer reads pageData from the "
            "blob via the expected line — update _PAGEDATA_LINE."
        )
    preamble, rest = body.split(_PAGEDATA_LINE, 1)
    # preamble = `const { createApp } = window.__TRINITY_VUE__;`; the rest is the
    # whole Vue app, which now runs inside the async fetch IIFE (scope-safe: the
    # template has zero inline handlers and assigns globals via explicit window.*).
    init_js = preamble.strip() + "\n\n" + _FETCH_BOOTSTRAP + rest.rstrip() + "\n})();\n"

    html_ext = html.replace(block, '<script src="./launchpad-init.js"></script>')
    # The shared CSS @font-face URLs point at `../portal_pages/vendor/*.woff2` (the
    # SERVED launchpad's layout). In the extension that path doesn't exist, so the
    # brand fonts 404 and silently fall back to system fonts. Repoint them at the
    # extension's OWN bundled vendor/ so Hanken + JetBrains actually load.
    html_ext = html_ext.replace("../portal_pages/vendor/", "vendor/")
    return html_ext, init_js


def main() -> int:
    from trinity_local.vendor import publish_vendor_files

    html_ext, init_js = build()
    (EXT / "launchpad.html").write_text(html_ext, encoding="utf-8")
    (EXT / "launchpad-init.js").write_text(init_js, encoding="utf-8")
    vendored = publish_vendor_files(EXT)
    print(
        f"wrote {EXT / 'launchpad.html'} ({len(html_ext):,} B) + "
        f"launchpad-init.js ({len(init_js):,} B) + vendor/ ({len(vendored)} files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
