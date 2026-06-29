"""Browser guard: the live council page's status POLLER must tear down on EVERY
terminal transition and must NOT STACK across a chain "Continue" round.

THE LEAK CLASS (the [[live_council_two_pollers]] founder lineage): the live page
(`render_live_council_page` — the page every council link redirects to via
`write_unified_council_page`) drives a `setInterval` poller in `startPolling()`.
When a council finishes it must `clearPolling()` BOTH the status poll AND the
status-rotate interval; when the user clicks "Continue (one round)" to launch the
next chain round, `_startChainAction` must `clearPolling()` the FINISHED segment's
poller BEFORE `startPolling()` opens a fresh one for the new segment. If either
teardown is dropped, the page accumulates intervals that keep `fetch`-ing dead
status tokens forever (the exact "each poller 404-spins running forever" symptom
that already bit THREE independent pollers, capped per-poller v1.7.194/201).

Nothing drove this as a TEMPORAL SEQUENCE in a real browser. The only existing
chain browser test (`test_council_chain_action_browser`) loads a STATIC
`?council_id=` and exercises the dispatch-FAILURE rollback — it never starts a
real `?status_token=` poll, never flips a status file running->completed, and
never counts live intervals. `test_sidepanel_chain_continue_lifecycle_browser`
stubs round-2 to resolve completed immediately and asserts the round HYDRATES, not
that the round-1 poller was torn down or that pollers don't stack. The
`missing_status_timeout` tests are SOURCE-STRING checks against the DEAD
`_pollChainStatus` (render_unified_council_page, #311), not the live page's
`statusPollHandle`. So a poller-leak across the chain Continue would ship green.

This guard SPIES `window.setInterval`/`clearInterval` (installed at document-start)
to track LIVE (uncleared) interval ids, serves an isolated PII-free synthetic
council over http (the page reads `?status_token=`, which file:// can't carry the
poll for reliably), stubs `window.__TRINITY_DISPATCH__` so Continue NEVER reaches a
real extension / real council, and drives:

  seg1 RUNNING (poll live: 2 intervals)
    -> flip status file running->completed
    -> seg1 COMPLETED (clearPolling fired: 0 live intervals; no further fetch of
       seg1's token after a full poll cycle — the poller is genuinely DEAD)
  click "Continue (one round)" (stub succeeds)
    -> seg2 optimistically appended + a FRESH poller for the NEW token
    -> EXACTLY 2 live intervals, NOT 4 (seg1's did not stack)
    -> the live poller fetches seg2's token; seg1's fetch count stays frozen
  flip seg2 status file running->completed
    -> seg2 COMPLETED (0 live intervals again; seg2 poller also dead after a
       full cycle)

Geometry/interaction assertions over a real DOM + a real interval spy, NOT
source-string checks. Mutation-proven: void the body of `clearPolling()` (so no
interval is ever cleared) -> this guard reds at the "intervals must be 0 after
completion" / "must not stack to 4" assertions while the static-render tests stay
green. Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEG1_TOK = "chain_pollerguard_seg1"
_SEG1_CID = "council_pollerguard_one"
_SEG2_CID = "council_pollerguard_two"
_TASK = "Cache the embedder in-process or per-call?"


def _seed():
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import council_status_dir

    for cid, synth in ((_SEG1_CID, "In-process caching wins."),
                       (_SEG2_CID, "Round 2: confirmed in-process.")):
        save_council_outcome(CouncilOutcome(
            council_run_id=cid,
            bundle_id=cid,  # chain_root_id falls back to bundle_id -> clean single thread
            task_cluster_id="cluster_pollerguard",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": _TASK},
            member_results=[
                CouncilMemberResult(provider="claude", model="opus", output_text="Answer A"),
                CouncilMemberResult(provider="codex", model="gpt", output_text="Answer B"),
            ],
            synthesis_prompt="Review.",
            synthesis_output=synth,
            routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
            created_at="2026-06-02T00:00:00+00:00",
        ))
    write_portal_html()
    write_live_council_page()
    council_status_dir().mkdir(parents=True, exist_ok=True)


def _write_status_js(tmp_path, token, payload):
    """Write the council_status_<token>.js JSONP the live poller fetches. Writing
    the .js directly (not via write_council_status) skips the server-side
    staleness coercion, giving us full control of the browser-visible status."""
    from trinity_local.state_paths import council_status_js_path

    payload = {**payload, "status_token": token}
    js = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = "
        f"{json.dumps(payload, separators=(',', ':'), ensure_ascii=True)};\n"
    )
    council_status_js_path(token).write_text(js, encoding="utf-8")


def _running(task):
    return {
        "status": "running", "task_text": task,
        "active_provider": "claude", "active_providers": ["claude"],
        "members": {"claude": {"status": "running"}, "codex": {"status": "pending"}},
        "synthesis": {"status": "pending"},
    }


def _completed(task, cid, synth):
    return {
        "status": "completed", "task_text": task, "council_id": cid,
        "review_path": f"{cid}.html",
        "active_provider": None, "active_providers": [],
        "members": {"claude": {"status": "done", "output_text": "Answer A"},
                    "codex": {"status": "done", "output_text": "Answer B"}},
        "synthesis": {"status": "done", "output_text": synth},
        "winner": "claude", "metadata": {"round_number": 1},
    }


# setInterval/clearInterval spy + status-fetch tracker, installed at document-start.
_SPY = r"""
window.__liveIntervals = new Set();
window.__statusFetches = [];
const _si = window.setInterval.bind(window);
const _ci = window.clearInterval.bind(window);
window.setInterval = function(fn, ms, ...r){ const id=_si(fn,ms,...r); window.__liveIntervals.add(id); return id; };
window.clearInterval = function(id){ window.__liveIntervals.delete(id); return _ci(id); };
const _ce = document.createElement.bind(document);
document.createElement = function(tag){
  const el=_ce(tag);
  if(String(tag).toLowerCase()==='script'){
    const d=Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype,'src');
    Object.defineProperty(el,'src',{configurable:true,get(){return d.get.call(this);},
      set(v){const m=/council_status_([^.?]+)\.js/.exec(String(v)); if(m) window.__statusFetches.push(m[1]); d.set.call(this,v);}});
  }
  return el;
};
"""

# Continue/Refine succeed so seg2 is pushed + polled. We capture the action so we
# can read the new status_token the page generated and drive seg2's status file.
_STUB_OK = """
window.__captured = null;
window.__TRINITY_DISPATCH__ = {
  state: 'ready',
  onStateChange: () => {},
  probe: () => Promise.resolve('ready'),
  dispatch: ({extensionAction, onResult}) => {
    window.__captured = extensionAction;
    setTimeout(() => onResult({ok: true}), 30);
  }
};
"""

_LIVE = "() => window.__liveIntervals ? window.__liveIntervals.size : -1"
_SEG_COUNT = "() => document.querySelectorAll('.chain-segment[data-seg-key]').length"
_SEG_COUNT_IS_2 = "() => document.querySelectorAll('.chain-segment[data-seg-key]').length === 2"


def _fetches_of(page, token):
    return sum(1 for t in page.evaluate("() => window.__statusFetches") if t == token)


def test_chain_poller_tears_down_and_does_not_stack(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed()
    _write_status_js(tmp_path, _SEG1_TOK, _running(_TASK))

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={_SEG1_TOK}"

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context().new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(_SPY)
                page.goto(url)
                page.wait_for_timeout(1700)  # let seg1 poll a couple times

                # --- seg1 RUNNING: poller live (statusPoll + statusRotate = 2). ---
                assert page.evaluate(_SEG_COUNT) == 1, "synthetic council did not boot to 1 segment"
                assert page.evaluate(_LIVE) == 2, (
                    "seg1 running should have EXACTLY 2 live intervals "
                    "(statusPollHandle + statusRotateHandle)"
                )
                assert _fetches_of(page, _SEG1_TOK) >= 1, "seg1 poller never fetched its status token"

                # --- flip seg1 running -> COMPLETED: clearPolling must fire. ---
                _write_status_js(tmp_path, _SEG1_TOK, _completed(_TASK, _SEG1_CID, "In-process caching wins."))
                page.wait_for_function(
                    "() => /In-process caching wins/.test(document.querySelector('#live-council-app').textContent)",
                    timeout=6000,
                )
                page.wait_for_timeout(300)
                assert page.evaluate(_LIVE) == 0, (
                    "seg1 completed but the poller intervals were NOT torn down "
                    "(clearPolling dropped) — the [[live_council_two_pollers]] leak"
                )
                seg1_fetch_at_complete = _fetches_of(page, _SEG1_TOK)

                # Prove the seg1 poller is genuinely DEAD: wait a full poll cycle,
                # the seg1 fetch count must NOT grow (no leaked interval).
                page.wait_for_timeout(2600)
                assert _fetches_of(page, _SEG1_TOK) == seg1_fetch_at_complete, (
                    "a leaked interval kept fetching seg1's DEAD status token after "
                    "completion (poller 404-spins forever — the founder symptom)"
                )
                assert page.evaluate(_LIVE) == 0, "interval reappeared after seg1 completion"

                # --- click Continue (stub succeeds): seg2 + a FRESH poller. ---
                page.evaluate(_STUB_OK)
                page.evaluate(
                    "() => [...document.querySelectorAll('button')]"
                    ".find(x=>/Continue \\(one round\\)/.test(x.textContent)).click()"
                )
                page.wait_for_function(_SEG_COUNT_IS_2, timeout=5000)
                captured = page.evaluate("() => window.__captured")
                assert (captured or {}).get("kind") == "council-iterate", captured
                seg2_tok = (captured or {}).get("status_token")
                assert seg2_tok and seg2_tok != _SEG1_TOK, captured

                # seg2 RUNNING — write its status so the fresh poller streams it.
                _write_status_js(tmp_path, seg2_tok, _running(_TASK))
                page.wait_for_timeout(1700)

                # CRITICAL: exactly 2 live intervals (seg2's), NOT 4. seg1's poller
                # must not have stacked under Continue.
                assert page.evaluate(_LIVE) == 2, (
                    "after Continue there are not EXACTLY 2 live intervals — seg1's "
                    "poller STACKED instead of being cleared before the new "
                    "startPolling (TWO pollers running, the leak class)"
                )
                # The live poller fetches seg2's token; seg1's stays frozen.
                assert _fetches_of(page, seg2_tok) >= 1, "seg2 poller never fetched the new token"
                assert _fetches_of(page, _SEG1_TOK) == seg1_fetch_at_complete, (
                    "seg1's DEAD token was fetched again AFTER Continue — its poller "
                    "leaked through the chain transition"
                )

                # --- flip seg2 running -> COMPLETED: poller torn down again. ---
                _write_status_js(tmp_path, seg2_tok, _completed(_TASK, _SEG2_CID, "Round 2: confirmed in-process."))
                page.wait_for_function(
                    "() => /Round 2: confirmed in-process/.test(document.querySelector('#live-council-app').textContent)",
                    timeout=6000,
                )
                page.wait_for_timeout(300)
                assert page.evaluate(_LIVE) == 0, (
                    "seg2 completed but its poller intervals were NOT torn down "
                    "(the terminal teardown must hold on the SECOND round too)"
                )
                seg2_fetch_at_complete = _fetches_of(page, seg2_tok)
                page.wait_for_timeout(2600)
                assert _fetches_of(page, seg2_tok) == seg2_fetch_at_complete, (
                    "seg2 poller leaked — kept fetching its dead token after completion"
                )

                assert not errs, f"JS page errors during chain poller sequence: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
