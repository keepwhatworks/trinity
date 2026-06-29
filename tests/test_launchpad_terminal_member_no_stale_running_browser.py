"""A STOPPED/FAILED council must not show a member still "Running" or "Queued".

Founder-symptom lineage (the live-council twin, fixed earlier): when a council is
stopped (Stop clicked) or fails while one member already finished and the rest are
still in flight, the council runner's `finalize_council_run_state()` flips ONLY the
top-level status — it never rewrites the per-member statuses (council_status.py).
So the status file's member map stays frozen at 'done'/'running'/'pending'. The
live council page already normalizes this (council_review.py `memberRowsFor`:
`terminal && (pending|running) -> 'didnt-run'/'stopped'`), but the LAUNCHPAD running
card — its parallel surface rendering the SAME member map — had drifted: it mapped
those stale statuses straight to "Running"/"Queued", so the card read
"Council stopped" / "Council failed" ABOVE rows reading "Running" and "Queued" —
a flat self-contradiction (a member can't still be running on a dead council).

The fix mirrors the live page in `providerStatusRows`: on a terminal council a
never-finished member reads "Stopped" (canceled) / "Didn't run" (failed), with the
muted 'pending' badge style (a never-ran member is not an error). A member that DID
finish ('done'/'failed') is kept as-is.

This DRIVES the real petite-vue launchpad: seeds a RUNNING council (one member done,
one running, one pending), mounts, then swaps the status sidecar to a terminal status
with the member map frozen — exactly what the poller picks up in production — and
asserts the member rows. A pure render-string check can't catch it (the bug is in
a computed getter on a poller-driven state); only the live engine reaches it.

Mutation-provable: drop the `terminal && (pending|running) -> didnt-run/stopped`
rewrite and the stopped council shows "Running"/"Queued" member badges again -> reds.

Slow + browser; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOKEN = "launch_terminalmember"


def test_terminal_member_normalization_is_wired():
    """CI-runnable canary: the terminal-member rewrite must exist in the launchpad
    `providerStatusRows` getter (fails fast in plain pytest if it's deleted)."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    # The rewrite that turns a frozen pending/running member into didnt-run/stopped.
    assert "'failed-council'" in src and "'canceled-council'" in src, (
        "launchpad providerStatusRows lost its terminal-council member normalization"
    )
    assert "status = terminal === 'failed-council' ? 'didnt-run' : 'stopped'" in src, (
        "launchpad member rows no longer rewrite a frozen pending/running member on a "
        "dead council -> they'd read 'Running'/'Queued' under 'Council stopped/failed'"
    )
    assert "status === 'didnt-run' ? \"Didn't run\"" in src, (
        "launchpad statusLabel ternary lost its 'didnt-run' branch"
    )
    assert "status === 'stopped' ? 'Stopped'" in src, (
        "launchpad statusLabel ternary lost its 'stopped' branch"
    )


pytestmark_browser = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _write_sidecar(status_dir: Path, payload: dict) -> None:
    js = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(TOKEN)}] = {json.dumps(payload)};\n"
    )
    (status_dir / f"council_status_{TOKEN}.js").write_text(js, encoding="utf-8")


# A council that's mid-flight: claude answered, antigravity still running, codex
# still pending. The runner writes metadata.kind == "council" (council_runner.py),
# which is what the launchpad gates the running grid on.
_MEMBERS = {
    "claude": {"status": "done", "response_text": "Use a write-through LRU cache.", "model": "opus-4-8"},
    "antigravity": {"status": "running", "reasoning_summary": "Weighing the tradeoffs"},
    "codex": {"status": "pending"},
}


@pytest.mark.parametrize(
    "terminal_status,expected_stale_badge,header_substr",
    [
        ("canceled", "Stopped", "Council stopped"),
        ("failed", "Didn't run", "Council failed"),
    ],
)
@pytest.mark.slow
@pytest.mark.browser
def test_terminal_council_has_no_stale_running_member(
    tmp_path, monkeypatch, terminal_status, expected_stale_badge, header_substr
):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pd = build_launchpad_payload()["pageData"]
    pd["activeOperation"] = {
        "kind": "council",
        "status": "running",
        "statusToken": TOKEN,
        "task_text": "Compare three caching strategies",
        "label": "Compare three caching strategies",
        "memberOrder": ["claude", "antigravity", "codex"],
        "members": _MEMBERS,
        "synthesis": {"status": "pending"},
    }

    pp = tmp_path / "serve" / "portal_pages"
    status_dir = pp / "status"
    status_dir.mkdir(parents=True)
    (pp / "launchpad.html").write_text(render_launchpad_html(page_data=pd), encoding="utf-8")
    publish_vendor_files(pp)

    base_status = {
        "status_token": TOKEN,
        "status": "running",
        "task_text": "Compare three caching strategies",
        "members": _MEMBERS,
        "synthesis": {"status": "pending"},
        "metadata": {"kind": "council", "members": ["claude", "antigravity", "codex"]},
        "updated_at": "2026-06-22T00:00:00",
    }
    _write_sidecar(status_dir, base_status)

    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 393, "height": 900}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__={dispatch:(o)=>{ if(o&&o.onResult) o.onResult({ok:true}); },"
                    " probe:()=>Promise.resolve({ok:true}), subscribe:()=>{}, onStateChange:()=>{}};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )

                # ── Bite precondition A: the running grid IS showing the member rows
                #    (the bug only exists where the rows render). Wait for the poller
                #    to ingest the running sidecar and the 'done' member to paint.
                #    NB: the badge has `text-transform: uppercase`, so read
                #    `textContent` (the un-transformed bound label), not innerText.
                page.wait_for_function(
                    "() => [...document.querySelectorAll('.provider-status-badge')]"
                    ".some(b => b.textContent.trim() === 'Done')",
                    timeout=8000,
                )
                running_badges = page.eval_on_selector_all(
                    ".provider-status-row .provider-status-badge",
                    "els => els.map(el => el.textContent.trim())",
                )
                # Precondition A proven: a mid-flight member reads Running/Queued NOW
                # (so the terminal swap is a real state transition, not a no-op).
                assert "Running" in running_badges, (
                    f"precondition failed: no mid-flight 'Running' member badge to normalize: {running_badges}"
                )

                # ── Drive the terminal transition exactly as the poller does:
                #    swap the sidecar to a terminal status with the SAME (frozen)
                #    member map. finalize_council_run_state only flips the top-level
                #    status in production — mirror that here.
                terminal_payload = dict(base_status)
                terminal_payload["status"] = terminal_status
                terminal_payload["error"] = (
                    "Council stopped." if terminal_status == "canceled"
                    else "Council runner exited before completion."
                )
                _write_sidecar(status_dir, terminal_payload)

                # Wait for the poller (1.5s interval) to finalize the operation.
                page.wait_for_function(
                    "(sub) => { const ls = document.querySelector('.launch-status');"
                    " return ls && ls.innerText.includes(sub); }",
                    arg=header_substr,
                    timeout=8000,
                )
                page.eval_on_selector(".launch-status", "el => el.scrollIntoView({block:'center'})")

                # Read textContent, not innerText — the badge is text-transform:
                # uppercase, so innerText would return "RUNNING"/"DIDN'T RUN".
                member_rows = page.eval_on_selector_all(
                    ".provider-status-row",
                    """els => els.filter(el => {
                         const name = el.querySelector('.provider-status-name');
                         return name && name.textContent.trim() !== 'Analysis';
                       }).map(el => {
                         const badge = el.querySelector('.provider-status-badge');
                         const name = el.querySelector('.provider-status-name');
                         return { name: name ? name.textContent.trim() : '',
                                  badge: badge ? badge.textContent.trim() : '' };
                       })""",
                )
                assert member_rows, "no member rows rendered on the terminal council"

                # Bite precondition B: the grid is STILL shown on the terminal council
                # (claude's landed answer keeps showProviderRows true) — so the rows
                # we assert on actually exist in this state.
                assert any(r["badge"] == "Done" for r in member_rows), (
                    f"the finished member's 'Done' row vanished on the terminal council: {member_rows}"
                )

                # ── THE INVARIANT: no member that never finished still reads
                #    "Running" or "Queued" on a dead council.
                stale = [r for r in member_rows if r["badge"] in ("Running", "Queued")]
                assert not stale, (
                    f"a {terminal_status} council still shows member rows reading "
                    f"'Running'/'Queued' ({stale}) — the launchpad running card "
                    f"contradicts its own '{header_substr}' header (the stale-Queued-"
                    f"on-a-dead-council founder symptom; live page normalizes these "
                    f"to '{expected_stale_badge}')."
                )
                # ── And they read the honest terminal label.
                assert any(r["badge"] == expected_stale_badge for r in member_rows), (
                    f"the never-finished members on a {terminal_status} council should "
                    f"read '{expected_stale_badge}', got: {member_rows}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
