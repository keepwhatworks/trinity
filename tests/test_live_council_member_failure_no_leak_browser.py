"""A council MEMBER that crashes must not leak an internal into its failed row.

When a council member's CLI dispatch raises (`provider.run(...)` throws), the
runner records the failure via `update_member_failure(token, provider, str(exc))`
(`council_runner.py` member path L685-686, chain path L364, conductor path
L1116). That text is stored as the member's `reasoning_summary` and painted
VERBATIM as the failed member's row on the live council page
(`council_review.py` L2140 / `live_council.html` L2371) and the launchpad running
card (`launchpad_template.py` L4189).

A `str(exc)` for a real dispatch failure carries an absolute filesystem path, an
`[Errno N]`, and a Python type name — e.g.

    ProviderError: claude binary failed: [Errno 2] No such file or directory:
        '/Users/vishi/.local/bin/claude'

so the failed member's row painted that path to the user — a violation of the
TYPE-only-honest error-copy constraint (#43102d25) and dishonest UX (a
non-technical user can't act on a traceback frame). It is the SAME class as the
popup's "Failed: <error>" capture-host leak; both now flow through the shared
`utils.safe_error_message` choke point. The clean failure detail
(`describe_provider_failure` output like "usage limit reached — resets …") must
SURVIVE intact — only the internals are stripped.

This guard drives the REAL `update_member_failure` sink with a path-bearing
exception (the discriminating input), then serves the status sidecar it produces
and reads the RENDERED live-council member row:

  PRECONDITION — a "Failed" member badge paints (the failure state is live);
  ASSERTION    — the painted failed row + the whole rendered body carry NO
                 absolute path / Errno / Traceback / Python type name, AND the
                 honest residue of the message survives ("No such file or
                 directory").

Mutation proof: drop the `safe_error_message(...)` call in
`council_status.update_member_failure` (store the raw `error_text` again) → the
member row paints `'/Users/vishi/.local/bin/claude'` and this guard reds with
the founder symptom. Found 2026-06-21 driving a crashed-member council.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# The discriminating input: a real path-bearing provider exception — exactly the
# `str(exc)` the runner hands update_member_failure when claude/codex/agy dispatch
# raises on a missing binary / unreadable state file.
_CRASH_EXC = (
    "ProviderError: claude binary failed: [Errno 2] No such file or directory: "
    "'/Users/vishi/.local/bin/claude'"
)
_LEAKS = ("/Users/", "/home/", "/private/", "[Errno", "Traceback",
          "ProviderError", "FileNotFoundError", ".local/bin")


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_via_sink(tmp_path, monkeypatch, token):
    """Build a real council status by driving the update_member_failure SINK with
    a crashed member (path-bearing exception) + a clean usage-limit failure (must
    survive), then publish the live council page + the sidecar the page reads."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import council_status as cs
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    cs.init_council_run_state(
        token, task_text="SQLite or DuckDB?", bundle_id="b_leak",
        members=["claude", "codex", "antigravity"],
    )
    # claude crashes with a path-bearing exception (the leak vector).
    cs.update_member_failure(token, "claude", _CRASH_EXC)
    # codex fails with a CLEAN, already-human reason (describe_provider_failure
    # output) — the negative control: this must survive sanitization intact.
    cs.update_member_failure(token, "codex", "codex usage limit reached — resets Jun 12th, 2026")
    # antigravity responds, so the page renders a normal completed council shell.
    cs.update_synthesis_progress(token, "done", output_text="Synthesis over the survivors.")
    cs.write_council_status(token, status="completed")

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    # The live page reads the council_status_<token>.js sidecar — init/update
    # already wrote it under portal_pages/status; mirror it next to the page so
    # the served http path can fetch it (same as the disclosure guard).
    served_status = portal_pages_dir() / "status"
    src = served_status / f"council_status_{token}.js"
    assert src.exists(), "precondition: the status sidecar must have been written"
    return src.read_text(encoding="utf-8")


def _drive(port, token):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1200}
            ).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/"
                f"live_council.html?status_token={token}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            body = page.evaluate("() => document.body.innerText")
            badges = page.evaluate(
                "() => Array.from(document.querySelectorAll('.provider-status-badge'))"
                ".map(b => b.textContent.trim())"
            )
            return body, badges
        finally:
            browser.close()


def test_crashed_member_row_paints_no_internal_leak(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_member_leak"

    # The sidecar the sink produced — proves at the data layer that the raw exc
    # was sanitized BEFORE it ever reached disk.
    sidecar = _seed_via_sink(tmp_path, monkeypatch, token)
    for marker in _LEAKS:
        assert marker not in sidecar, (
            f"LEAK IN STATUS SIDECAR: update_member_failure stored {marker!r} from a "
            f"crashed member's str(exc) — the failed row's reasoning_summary leaks an "
            f"absolute path / Errno / type name to the live page (#43102d25)."
        )

    httpd, port = _serve(tmp_path)
    try:
        body, badges = _drive(port, token)
    finally:
        httpd.shutdown()

    # PRECONDITION: the failure state is live — a "Failed" member badge painted,
    # so the assertions below bite the rendered failure row, not an absent one.
    assert "Failed" in badges, (
        f"expected a 'Failed' member badge for the crashed member, got {badges!r}"
    )

    # THE FOUNDER SYMPTOM in the rendered pixels: no leaked internal anywhere.
    for marker in _LEAKS:
        assert marker not in body, (
            f"LEAK IN DOM: the live council page painted {marker!r} in a failed "
            f"member's row — a crashed member's str(exc) carried an absolute path / "
            f"Errno / Python type name straight to the user. Body excerpt: "
            f"{body[body.find('No such') - 40 : body.find('No such') + 80]!r}"
        )

    # HONEST: the human residue of the failure survives (not blanked to nothing).
    assert "No such file or directory" in body, (
        "the crashed member's row must still say WHAT failed (the IO error), only "
        f"the path stripped; body excerpt: {body[:600]!r}"
    )
    # NEGATIVE CONTROL: a clean failure reason is preserved verbatim.
    assert "usage limit reached" in body, (
        "the clean 'usage limit reached' failure reason must survive sanitization "
        f"intact (sanitizer must not eat already-human messages); body: {body[:600]!r}"
    )
