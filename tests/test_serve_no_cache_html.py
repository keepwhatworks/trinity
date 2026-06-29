"""Regression: `trinity-local serve` must send Cache-Control: no-store
for .html and .json responses, so the launchpad picks up new code
without the user manually hard-reloading (Cmd+Shift+R).

Symptom this prevents: ship a launchpad fix, user goes back to the
already-open tab, hits ⌘R, Chrome serves the cached HTML, the new
behavior never lands. We chased this for ~5 minutes during the
2026-05-26 stuck-launch e2e — every regen looked like a no-op until
we cache-busted the URL with ?bust=...
"""
from __future__ import annotations

from http.server import HTTPServer
import socket
import threading

import pytest

# Spins up a real HTTPServer + hits it via urllib. The slow marker
# keeps it out of the default `pytest -q` shard so unit tests stay
# under a minute. Run with `pytest -m slow` or `TRINITY_SLOW=1 pytest`.
pytestmark = pytest.mark.slow
import urllib.request


def _get_handler_class():
    from trinity_local.commands.portal import _NoCacheHTMLHandler
    return _NoCacheHTMLHandler


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_html_response_sends_no_store_cache_control(tmp_path):
    handler_cls = _get_handler_class()
    # Drop a tiny HTML file in tmp_path
    (tmp_path / "test.html").write_text("<html></html>")

    handler = lambda *a, **kw: handler_cls(*a, directory=str(tmp_path), **kw)
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/test.html", timeout=2) as resp:
            cache = resp.headers.get("Cache-Control", "")
            pragma = resp.headers.get("Pragma", "")
            expires = resp.headers.get("Expires", "")
        assert "no-store" in cache, f"expected no-store, got: {cache!r}"
        assert "no-cache" in pragma, f"expected no-cache, got: {pragma!r}"
        assert expires == "0", f"expected '0', got: {expires!r}"
    finally:
        server.shutdown()
        server.server_close()


def test_json_response_also_disables_cache(tmp_path):
    """Council status JSON files (~/.trinity/portal_pages/status/*.json)
    are polled while a council runs — they must not be cached either."""
    handler_cls = _get_handler_class()
    (tmp_path / "status.json").write_text('{"state": "running"}')

    handler = lambda *a, **kw: handler_cls(*a, directory=str(tmp_path), **kw)
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=2) as resp:
            cache = resp.headers.get("Cache-Control", "")
        assert "no-store" in cache, f"expected no-store on JSON, got: {cache!r}"
    finally:
        server.shutdown()
        server.server_close()


def test_static_assets_dont_get_no_store(tmp_path):
    """Vendor JS + PNG share cards keep default caching — they don't
    change between regens and pinning them prevents the user's browser
    from re-pulling the petite-vue bundle on every page reload."""
    handler_cls = _get_handler_class()
    (tmp_path / "asset.js").write_text("// vendor")
    (tmp_path / "card.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    handler = lambda *a, **kw: handler_cls(*a, directory=str(tmp_path), **kw)
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/asset.js", timeout=2) as resp:
            cache = resp.headers.get("Cache-Control", "")
        # SimpleHTTPRequestHandler default does not set Cache-Control at all.
        assert "no-store" not in cache, f"static JS should not have no-store: {cache!r}"
    finally:
        server.shutdown()
        server.server_close()


def test_serve_flushes_url_before_blocking(tmp_path):
    """`serve` calls serve_forever() (blocks forever), so the "Launchpad: http://"
    URL — the whole point of the command's output — must be FLUSHED before the
    block. Otherwise, in any NON-tty context (`serve > log &`, piped, or a harness
    capturing output), the URL sits in stdout's block buffer until Ctrl-C and the
    user can't find where Trinity is serving (observed 2026-06-06: a redirected
    serve logged nothing while answering HTTP 200s). Spawn the real verb with a
    PIPED stdout (non-tty — the buffering regime where the bug shows) and assert
    the URL arrives promptly. Mutation: drop the sys.stdout.flush() before
    serve_forever() → this times out and fails."""
    import os
    import socket as _socket
    import subprocess
    import sys
    import threading
    import time
    from pathlib import Path

    # Grab a free port (tiny race, fine for a test).
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = str(s.getsockname()[1])
    s.close()

    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_DISABLE_MLX"] = "1"
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = (
        str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "trinity_local.main", "serve", "--port", port],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    needle = f"Launchpad: http://127.0.0.1:{port}"
    # A blocking reader thread, NOT select+readline: select watches the raw fd
    # while readline buffers in the TextIOWrapper, so after the first line the
    # read-ahead drains the fd and select wrongly reports "nothing more" — the
    # URL line stays stuck in the wrapper's buffer. iter(readline, '') reads each
    # line as it's flushed.
    captured: list[str] = []
    reader = threading.Thread(
        target=lambda: captured.extend(iter(proc.stdout.readline, "")), daemon=True
    )
    reader.start()
    try:
        deadline = time.time() + 25  # refresh_launchpad renders first; generous
        while time.time() < deadline and not any(needle in line for line in captured):
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        assert any(needle in line for line in captured), (
            "the serve URL was not flushed before serve_forever() blocked — a "
            f"non-tty `serve` shows no URL until Ctrl-C. captured so far: {captured!r}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
