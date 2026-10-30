"""Helpers the browser tests share: a local static server, the production page layout, and the
WCAG contrast ratio.

Not a test module (no test_ prefix), so pytest does not collect it. Import it as
`from tests.browser_harness import serve_directory`.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path


def serve_directory(directory: Path | str) -> tuple[http.server.ThreadingHTTPServer, int]:
    """Serve `directory` over http on 127.0.0.1 at a free port from a daemon thread.

    Returns (server, port); the caller calls server.shutdown() when done.
    """
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def write_prod_layout(html: str, serve_root: Path) -> str:
    """Lay the launchpad out as production does: launchpad.html in portal_pages/, the vendor files
    in portal_pages/vendor/. The shared CSS's @font-face uses `../portal_pages/vendor/...` and the
    launchpad's own JS `./vendor/...`; both resolve only from the portal_pages/ depth. Returns
    the page's path relative to `serve_root`."""
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return "portal_pages/launchpad.html"


def relative_luminance(rgb) -> float:
    """WCAG 2 relative luminance of an sRGB (r, g, b) tuple in 0-255."""
    lin = [c / 255 for c in rgb]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in lin]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(fg, bg) -> float:
    """WCAG 2 contrast ratio of two sRGB tuples, from 1.0 to 21.0."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)
