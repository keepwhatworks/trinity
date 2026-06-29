"""Browser guard for the PRODUCTION council chain action (Continue / Refine).

The live council page (`render_live_council_page`, the page every council link
redirects to via `write_unified_council_page`) lets the user launch another round
from `_startChainAction`. That handler does an OPTIMISTIC segment append — it
`segments.push()` a new round BEFORE dispatch so the thread reads as growing —
then, if the Chrome-extension dispatch fails, it must ROLL THAT SEGMENT BACK
(splice it out), surface a `chainError` ribbon, and restore the typed prompt.

Everything below the push is async petite-vue reactivity over a mutated array.
Unit tests (`test_council_review_refine_dispatch_failure.py`) assert the handler
LOGIC; nothing drove the actual click → optimistic-append → rollback in a real
browser. That's exactly the `e2e_chrome_dogfood` class that has shipped real bugs
green ("optimistic UI never rolls back", launchpad stuck-launch) — a `splice`
that drifts from the token, a `chainBusy` that sticks, or a reactivity miss would
leave a phantom round on screen and the page wedged, while string tests stay green.

This serves an isolated TRINITY_HOME over http (the page reads `?council_id=`,
which file:// can't carry), boots a synthetic 1-round council (no PII), stubs
`window.__TRINITY_DISPATCH__`, and exercises the chain action from the surface:

  * Continue → optimistic 2nd segment appears + the dispatched action is shaped
    `council-iterate` with the right council id;
  * async dispatch failure → the optimistic segment rolls back to 1, the
    "Could not start next round" ribbon shows, the Continue button returns;
  * Refine → on failure the typed directive is restored to the input (retry
    without retyping — the `refinePrompt` restore at council_review.py);
  * no dispatcher loaded → immediate rollback + ribbon (the synchronous path).

Slow-marked (portal render + chromium); skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_chainguard1"


def _seed_outcome():
    """Write a synthetic completed 1-round council (JSONP + thread manifest)."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    outcome = CouncilOutcome(
        council_run_id=_CID,
        bundle_id=_CID,  # chain_root_id falls back to bundle_id → clean single-council thread
        task_cluster_id="cluster_chainguard",
        primary_provider="claude",
        winner_provider="claude",
        metadata={"task_text": "Cache the embedder in-process or per-call?"},
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text="In-process."),
            CouncilMemberResult(provider="codex", model="gpt", output_text="Per-call."),
        ],
        synthesis_prompt="Review the answers.",
        synthesis_output="In-process caching wins for latency.",
        routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
        created_at="2026-06-02T00:00:00+00:00",
    )
    save_council_outcome(outcome)
    write_portal_html()  # ensures portal_pages/vendor/petite-vue.iife.js
    write_live_council_page()


# Stub dispatcher whose dispatch() fails asynchronously — the real
# extension-absent shape the rollback must survive.
_STUB_FAIL = """
window.__TRINITY_DISPATCH__ = {
  dispatch: ({extensionAction, onResult}) => {
    window.__captured = extensionAction;
    setTimeout(() => onResult({ok: false, error: 'simulated dispatch failure'}), 80);
  }
};
"""

_SEG_COUNT = "() => document.querySelectorAll('.chain-segment[data-seg-key]').length"
# Poll-for-condition forms: the optimistic append/rollback is petite-vue REACTIVE
# (the DOM update can be batched to a microtask), so a fixed `wait_for_timeout`
# races under CI load — green local, red CI 2026-06-07. wait_for_function polls.
_SEG_COUNT_IS_2 = "() => document.querySelectorAll('.chain-segment[data-seg-key]').length === 2"
_SEG_COUNT_IS_1 = "() => document.querySelectorAll('.chain-segment[data-seg-key]').length === 1"
_ERR_VISIBLE = (
    "() => { const e=[...document.querySelectorAll('section.card')]"
    ".find(s=>/Could not start next round/.test(s.textContent));"
    " return e ? getComputedStyle(e).display!=='none' : false; }"
)
_CONTINUE_VISIBLE = (
    "() => { const b=[...document.querySelectorAll('button')]"
    ".find(x=>/Continue \\(one round\\)/.test(x.textContent));"
    " return b ? getComputedStyle(b.closest('.chain-actions')).display!=='none' : false; }"
)
_CLICK_CONTINUE = (
    "() => [...document.querySelectorAll('button')]"
    ".find(x=>/Continue \\(one round\\)/.test(x.textContent)).click()"
)


def test_chain_action_optimistic_append_and_rollback(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_outcome()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}"

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
                page.goto(url)
                page.wait_for_timeout(1200)

                # Boot: exactly one segment, chain action available.
                assert page.evaluate(_SEG_COUNT) == 1, "synthetic council did not boot to 1 segment"
                assert page.evaluate(_CONTINUE_VISIBLE), "Continue action not visible (canChainNext false)"

                # Continue with a failing dispatcher → optimistic append.
                page.evaluate(_STUB_FAIL)
                page.evaluate(_CLICK_CONTINUE)
                page.wait_for_function(_SEG_COUNT_IS_2, timeout=4000)
                assert page.evaluate(_SEG_COUNT) == 2, "optimistic 2nd segment did not appear on Continue"
                captured = page.evaluate("() => window.__captured || null")
                assert captured and captured.get("kind") == "council-iterate", captured
                assert captured.get("council") == _CID, captured

                # Async failure → the optimistic segment must roll back.
                page.wait_for_function(_SEG_COUNT_IS_1, timeout=4000)
                assert page.evaluate(_SEG_COUNT) == 1, (
                    "optimistic segment NOT rolled back after dispatch failure — phantom round left on screen"
                )
                assert page.evaluate(_ERR_VISIBLE), "chainError ribbon did not show after dispatch failure"
                assert page.evaluate(_CONTINUE_VISIBLE), "Continue action did not return after rollback"

                # Refine path: the typed directive must be restored on failure
                # (retry-without-retyping — the refinePrompt restore).
                page.evaluate(
                    "() => { const a=[...document.querySelectorAll('a')].find(x=>x.textContent.trim()==='Dismiss'); if(a) a.click(); }"
                )
                page.fill(".chain-refine-input", "tighten the abstain gate")
                page.evaluate(
                    "() => [...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Refine').click()"
                )
                page.wait_for_function(_SEG_COUNT_IS_2, timeout=4000)
                assert page.evaluate(_SEG_COUNT) == 2, "Refine did not optimistically append"
                refine_captured = page.evaluate("() => window.__captured || null")
                assert (refine_captured or {}).get("prompt") == "tighten the abstain gate", refine_captured
                page.wait_for_function(_SEG_COUNT_IS_1, timeout=4000)
                assert page.evaluate(_SEG_COUNT) == 1, "Refine optimistic segment not rolled back on failure"
                assert (
                    page.evaluate("() => document.querySelector('.chain-refine-input').value")
                    == "tighten the abstain gate"
                ), "typed refine directive was NOT restored after failure (user would have to retype)"

                # No dispatcher loaded → synchronous rollback, no phantom round.
                page.evaluate(
                    "() => { const a=[...document.querySelectorAll('a')].find(x=>x.textContent.trim()==='Dismiss'); if(a) a.click(); delete window.__TRINITY_DISPATCH__; }"
                )
                page.wait_for_timeout(40)
                page.evaluate(_CLICK_CONTINUE)
                page.wait_for_timeout(120)
                assert page.evaluate(_SEG_COUNT) == 1, "no-dispatcher click left a phantom optimistic segment"
                assert page.evaluate(_ERR_VISIBLE), "no-dispatcher path did not surface the dispatcher-not-loaded error"

                assert not errs, f"JS page errors during chain action: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


_LEGACY_CID = "council_legacychainguard"
# A COUNCIL-id-shaped chain_root_id — the stale/legacy .js shape: chain_root_id ==
# parent_council_id, from before the bundle-keyed thread migration. Thread
# manifests are written `_thread_<bundle_hash>.js`, so `_thread_council_<id>.js`
# can NEVER exist.
_LEGACY_ROOT = "council_legacyparent0000"


def _seed_legacy_chain_root_outcome():
    """Seed a completed council whose metadata carries a council-id-shaped
    chain_root_id. save_council_outcome serializes that metadata straight into the
    JSONP the live page reads, reproducing the real stale-.js chain council."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    outcome = CouncilOutcome(
        council_run_id=_LEGACY_CID,
        bundle_id="bundle_reallegacyroot",
        task_cluster_id="cluster_legacy",
        primary_provider="claude",
        winner_provider="claude",
        metadata={
            "task_text": "Should the round-2 council reuse the parent's members?",
            "chain_root_id": _LEGACY_ROOT,      # council-id-shaped (legacy)
            "parent_council_id": _LEGACY_ROOT,
        },
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text="Reuse."),
            CouncilMemberResult(provider="codex", model="gpt", output_text="Re-pick."),
        ],
        synthesis_output="Reuse for continuity unless the task kind shifted.",
        routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
        created_at="2026-06-02T00:00:00+00:00",
    )
    save_council_outcome(outcome)
    write_portal_html()
    write_live_council_page()


def test_legacy_council_shaped_chain_root_does_not_probe_dead_thread(tmp_path, monkeypatch):
    """A chain council whose outcome carries a COUNCIL-id-shaped chain_root_id (the
    stale/legacy .js shape == parent_council_id, pre-bundle migration) must NOT
    probe `_thread_council_<id>.js`: that manifest is never written (threads are
    keyed by `bundle_<hash>`), so the probe 404s on the core product page for every
    such chain council. Found 2026-06-05 driving a real chain council whose .js
    still carried the legacy root — a sibling of the v1.7.190 standalone-council
    fix (`|| next.councilId`). The page must render the council normally with ZERO
    `_thread_council_*` 404s."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_legacy_chain_root_outcome()

    # Reproduce the REAL post-migration stale state: the thread manifest was
    # re-keyed to a `bundle_<hash>`, so the council-shaped `_thread_council_<id>.js`
    # the legacy .js points at no longer exists. save_council_outcome auto-wrote one
    # (keyed by the council-shaped root we seeded) — delete it so the probe targets
    # a genuinely-absent file (without this the probe finds the file → no 404 → the
    # test passes vacuously, which it DID until this delete was added).
    dead_manifest = tmp_path / "council_outcomes" / f"_thread_{_LEGACY_ROOT}.js"
    if dead_manifest.exists():
        dead_manifest.unlink()

    # Non-vacuous preconditions: the JSONP carries the council-shaped chain_root_id
    # AND the dead manifest is truly absent (so a probe WOULD 404).
    js_text = (tmp_path / "council_outcomes" / f"{_LEGACY_CID}.js").read_text(encoding="utf-8")
    assert f'"chain_root_id": "{_LEGACY_ROOT}"' in js_text, (
        "seed didn't persist a council-shaped chain_root_id — test would be vacuous"
    )
    assert not dead_manifest.exists(), "dead thread manifest still present — probe wouldn't 404"

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_LEGACY_CID}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context().new_page()
                dead_404s: list[str] = []
                page.on(
                    "response",
                    lambda r: dead_404s.append(r.url)
                    if (r.status == 404 and "_thread_council_" in r.url) else None,
                )
                page.goto(url)
                page.wait_for_timeout(1500)
                # The council still renders (2 member cards) — the guard only skips
                # the dead thread probe, never the council itself.
                assert page.evaluate("document.querySelectorAll('.answers-grid > *').length") == 2, (
                    "legacy-chain-root council did not render its 2 member cards"
                )
                assert not dead_404s, (
                    "the page probed a dead `_thread_council_<id>.js` manifest "
                    f"(can never exist): {[u.split('/')[-1].split('?')[0] for u in dead_404s]} "
                    "— the council-shaped chain_root_id guard regressed"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


_MISNUM_ROOT = "bundle_misnumberguard"


def _seed_misnumbered_thread(n: int):
    """Seed N completed councils sharing ONE chain root, NONE carrying a
    round_number — so update_thread_manifest defaults each to round_number==1,
    reproducing the real all-1 manifest shape (the founder's 5-round thread had
    round_number==1 on every segment + no outcome.metadata.round_number)."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    for i in range(n):
        outcome = CouncilOutcome(
            council_run_id=f"council_misnum_{i}",
            bundle_id=_MISNUM_ROOT,                 # shared chain root → one thread
            task_cluster_id="cluster_misnum",
            primary_provider="claude",
            winner_provider="claude",
            # NO round_number → update_thread_manifest stamps 1 for every segment
            metadata={"task_text": "iterate", "chain_root_id": _MISNUM_ROOT},
            member_results=[
                CouncilMemberResult(provider="claude", model="opus", output_text=f"r{i + 1} claude."),
                CouncilMemberResult(provider="codex", model="gpt", output_text=f"r{i + 1} codex."),
            ],
            synthesis_output=f"Round {i + 1} synthesis.",
            routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
            created_at=f"2026-06-02T00:0{i}:00+00:00",   # ordered → manifest order = round order
        )
        save_council_outcome(outcome)
    write_portal_html()
    write_live_council_page()


def test_thread_view_numbers_rounds_sequentially_on_all_one_manifest(tmp_path, monkeypatch):
    """A multi-round thread whose manifest stamps round_number==1 for EVERY segment
    (the real shape — outcomes carry no round_number, so update_thread_manifest
    defaults each to 1) must STILL render sequential round labels (Round 1..N),
    driven by the manifest POSITION, not the degenerate field. Found 2026-06-05
    driving a real 5-round thread that rendered as 'Round 1 ×5': the template
    fallback `roundNumber || (segIndex+1)` is shadowed because roundNumber is a
    truthy 1. Fixed by deriving the round from `Math.max(round_number, idx+1)`."""
    import json
    import re

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_misnumbered_thread(4)

    # Non-vacuous precondition: the manifest really carries round_number==1 for
    # every segment (else the bug isn't reproduced and the test passes for free).
    man_txt = (tmp_path / "council_outcomes" / f"_thread_{_MISNUM_ROOT}.js").read_text(encoding="utf-8")
    man_match = re.search(r"=\s*(\{.*\});?\s*$", man_txt.strip(), re.S)
    assert man_match, "thread manifest .js not in the expected JSONP shape"
    man = json.loads(man_match.group(1))
    rnums = [s.get("round_number") for s in man.get("segments", [])]
    assert rnums == [1, 1, 1, 1], f"manifest didn't reproduce all-1 round_number: {rnums}"

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/live_council.html?thread_id={_MISNUM_ROOT}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context().new_page()
                page.goto(url)
                page.wait_for_timeout(1800)
                labels = page.evaluate(
                    "() => [...document.querySelectorAll('.eyebrow')]"
                    ".map(e => [...e.childNodes].filter(n => n.nodeType === 3)"
                    ".map(n => n.textContent).join('').trim())"
                    ".filter(t => /Round/.test(t))"
                )
                nums = []
                for t in labels:
                    digit = re.search(r"\d+", t)
                    assert digit, f"round label carries no number: {t!r}"
                    nums.append(int(digit.group()))
                assert nums == [1, 2, 3, 4], (
                    f"thread rounds not sequential — got {labels}. An all-1 manifest "
                    "collapsed the chain to 'Round 1 xN' (the position-derived round "
                    "number regressed)"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
