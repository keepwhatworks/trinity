"""The Chrome-extension POPUP's council-status poller must give up when the
status file never materializes — the hand-maintained THIRD sibling of the
live_council pollers (council_review.py MAX_MISSING_POLLS, v1.7.194) and the
launchpad poller (launchpad_template.py startOperationPolling).

`launch-council` fires DETACHED: the host returns `{ok:true, detached:true}`
BEFORE the runner has written anything, and the popup commits to
`startPolling(statusToken)`. If that detached process then DIES / is killed /
never reaches its first status write, `get-council-status` returns
`{ok:true, status:null}` (load_council_status finds no file) on EVERY poll, and
`_coerce_stale_running_status` can NEVER fire (it ages out a stale 'running'
record — but there is no record). Before this guard the popup's poller treated a
null status as "no status yet — keep cycling tips" with no counter, so it spun
"Council running" with its rotating witty messages FOREVER: no terminal state, no
Dismiss button, no honest "the dispatch may not have started" banner. The user
can't tell the council died; they just watch it spin.

This is the popup analog of `test_launchpad_status_poll_timeout.py` — but a
SOURCE-STRING assertion there can't see the popup actually escape the spinner, so
this drives the REAL popup with a fake clock: stub launch-council → detached,
stub get-council-status → {ok:true, status:null} forever, fast-forward ~67s of
poll time, and assert the panel reaches the TERMINAL "Council didn't start" card
(Dismiss shown, Stop hidden) instead of a frozen "Council running" spinner.

Mutation-proven: reverting the give-up cap (the null-status branch just
`return`s with no missingPollCount) leaves the panel on "Council running" with a
rotating tip and the Stop button still enabled after 45 fake polls → the terminal
assertions go red with the founder symptom (an infinite spinner on a council that
never started). Found 2026-06-23 auditing the three pollers for the give-up cap
the launchpad + live_council siblings already carry. Synthetic stub only; no PII.
Slow + browser; loads popup.html via file:// with a stubbed chrome.runtime.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
POPUP = REPO / "browser-extension" / "popup.html"

# launch-council resolves DETACHED (so the popup commits to polling); every
# get-council-status poll returns ok:true with status:null — the exact shape of a
# council whose runner died / never started writing (load_council_status → None).
_STUB_NEVER_STARTS = """
window.chrome = { runtime: { id: 'popuppollgiveup00000000000000000', lastError: null,
  sendMessage: (m, cb) => {
    if (m && m.kind === 'launch-council') {
      setTimeout(() => cb({ ok: true, detached: true }), 1); return;
    }
    if (m && m.kind === 'get-council-status') {
      setTimeout(() => cb({ ok: true, status: null }), 1); return;
    }
    setTimeout(() => cb({ ok: true }), 1);
  } } };
"""


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no launchable chromium: {exc}")


def test_popup_poller_gives_up_on_never_written_status():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_context(viewport={"width": 460, "height": 760}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.clock.install()
            page.add_init_script(_STUB_NEVER_STARTS)
            page.goto(f"file://{POPUP}")
            page.wait_for_selector("#compose", state="visible", timeout=5000)

            # Fire a council. The host resolves detached → the popup enters the
            # status panel and starts polling.
            page.fill("#task", "SQLite or DuckDB for the embedded analytics path?")
            page.click("#run-btn")
            page.wait_for_selector("#status-panel", state="visible", timeout=5000)

            # PRECONDITION (discriminating): the panel opens on the optimistic
            # "Council running" with the Stop button live and Dismiss hidden — so a
            # later transition to a terminal card is a REAL state change, not the
            # initial render.
            assert (
                page.evaluate("document.getElementById('panel-title').textContent")
                == "Council running"
            ), "precondition: status panel should open on 'Council running'"
            assert (
                page.evaluate(
                    "getComputedStyle(document.getElementById('panel-dismiss-btn')).display"
                )
                == "none"
            ), "precondition: Dismiss should be hidden while the council is 'running'"

            # Fast-forward ~67s of poll time (the cap is 20 polls @ 1500ms = 30s;
            # 45 ticks clears it with margin). The fake clock advances setInterval
            # AND the stub's setTimeout resolves, so each tick is one real poll.
            for _ in range(45):
                page.clock.fast_forward(1500)
                page.wait_for_timeout(5)  # let the awaited dispatch + handler resolve

            title = page.evaluate("document.getElementById('panel-title').textContent")
            tip = page.evaluate("document.getElementById('panel-tip').textContent")
            dismiss_display = page.evaluate(
                "getComputedStyle(document.getElementById('panel-dismiss-btn')).display"
            )
            stop_display = page.evaluate(
                "getComputedStyle(document.getElementById('panel-stop-btn')).display"
            )

            assert title == "Council didn't start", (
                "INFINITE SPINNER: the popup poller never gave up on a council whose "
                "status file was never written — after 45 polls (~67s) the panel still "
                f"reads {title!r} instead of the terminal 'Council didn’t start' "
                "card. A detached launch-council whose runner died pre-first-write pins "
                "the popup on 'Council running' with rotating tips forever (no terminal "
                "state, no Dismiss). Needs the MAX_MISSING_POLLS give-up the launchpad + "
                "live_council pollers already carry."
            )
            assert "may not have started" in tip, (
                "the give-up must surface the honest 'the dispatch may not have started' "
                f"banner, not a frozen witty tip; got {tip!r}"
            )
            # Terminal-state controls: Dismiss revealed, Stop withdrawn (you can't
            # stop a council that never started).
            assert dismiss_display != "none", (
                "the give-up must reveal the Dismiss button so the user can clear the "
                "dead council"
            )
            assert stop_display == "none", (
                "the give-up must withdraw the Stop button — there's nothing to stop"
            )
            assert not errs, f"popup raised JS errors during the poll give-up: {errs}"
        finally:
            browser.close()


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
