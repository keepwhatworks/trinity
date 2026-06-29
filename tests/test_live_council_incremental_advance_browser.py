"""Browser guard: the LIVE council page (`?status_token=` poll path) must advance
MONOTONICALLY across a real sequence of status frames — the "watch it happen"
moment of the lead product.

WHY THIS GUARD EXISTS (the coverage gap it closes). Every existing live-council
browser test seeds ONE static status frame and reads the rendered DOM exactly once
after the first poll (``test_live_council_running_member_status_browser`` = a single
done/running/pending snapshot; ``test_council_painkiller_browser`` active-state =
one snapshot; the sidepanel autopoll lifecycle drives the LAUNCHPAD's resumed
activeOperation poll, not the live page's per-member streaming). NONE drives the
live page across a temporal SEQUENCE of frames and asserts the member rows climb
pending → running → done one-by-one with the poller re-fetching the sidecar each
tick.

The founder symptom this bites: a council that has already FINISHED on disk but the
live page is STUCK — every member still reads "Queued", the spinner never clears,
the verdict never appears — because the page polled once and froze. A user watching
the council sees it hang forever on a council that's actually done. A single-frame
test passes through this regression (it reads after the FIRST ``check()`` tick,
which still fires); only re-writing the sidecar mid-page-life and re-reading the DOM
across frames catches a dead repeating poll.

WHAT IS ASSERTED, per frame, off RENDERED textContent (not source strings):
  * F0 (init running):     all 3 members Queued, spinner on, no verdict.
  * F1 (claude running):   Claude Running; GPT/Gemini still Queued.
  * F2 (claude done...):   Claude Done; GPT Running; Gemini Queued.
  * F3 (codex done...):    Claude Done; GPT Done; Gemini Running.
  * F4 (all done, synth):  all 3 Done; spinner STILL on; verdict still suppressed.
  * F5 (completed):        spinner CLEARED; verdict + Winner line present.
  * MONOTONICITY across the whole run: a member's status only ever climbs
    pending(0) → running(1) → done(2); it NEVER regresses (no Done→Queued flicker),
    and NEVER reads Done before the frame that marked it done.

The status sidecar is advanced through the REAL ``council_status`` helpers
(start_member_progress / update_member_progress / update_synthesis_progress /
write_council_status), each of which rewrites the JSONP sidecar the live page
re-fetches — i.e. the exact runner→disk→poll path. A LIVE ``runner_pid`` is stamped
so ``load_council_status``'s dead-runner staleness gate doesn't coerce the running
council to 'failed' (the council_runner always writes a live pid).

MUTATION-PROVEN: disabling the repeating ``setInterval(check, 1500)`` poll on the
live page (poll once and freeze) holds every member at "Queued" through F1–F5 and
never clears the spinner / reaches the verdict — this guard REDS on the
advancement + completion assertions while the single-frame
``test_live_council_running_member_status`` test stays GREEN.
"""
from __future__ import annotations

import functools
import http.server
import os
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_TOKEN = "inc_advance_tok"
_MEMBERS = ["claude", "codex", "antigravity"]
_MODELS = {"claude": "claude-opus-4-8", "codex": "gpt-5.5", "antigravity": "gemini-3.1-pro"}
# Provider-label prefixes the live page renders (#275 brand fold).
_BRAND = {"claude": "Claude", "codex": "GPT", "antigravity": "Gemini"}
_RANK = {"Queued": 0, "Running": 1, "Done": 2}


def _seed_running(token: str) -> int:
    """Init a 3-member running council with a LIVE runner pid + publish the page.
    Returns the live pid so the caller can re-stamp it across frames."""
    from trinity_local import vendor, state_paths
    from trinity_local import council_status
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html

    pid = os.getpid()  # this process is alive → not coerced to 'failed' on read
    council_status.init_council_run_state(
        token,
        task_text="Design an idempotency layer for a payments webhook",
        bundle_id="bundle_inc_advance",
        members=_MEMBERS,
        member_models=_MODELS,
        runner_pid=pid,
    )
    write_portal_html()
    write_live_council_page(force=True)
    vendor.publish_vendor_files(state_paths.portal_pages_dir())
    return pid


def _serve(directory):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


_PROBE = r"""() => {
  const rows = [...document.querySelectorAll('.provider-status-row')].map(r => {
    const name = ((r.querySelector('.provider-status-name')||{}).textContent || '')
                 .trim().split(' · ')[0];
    // The 'done' state hides the status badge (template v-if statusClass!=='done'),
    // so a row with no NON-lens-pick badge IS done. Read the row's own class +
    // any present status badge text.
    const badges = [...r.querySelectorAll('.provider-status-badge')]
      .map(e => e.textContent.trim());
    const status = badges.find(b => /^(Queued|Running|Done|Failed|Didn't run|Stopped)$/.test(b));
    return { name, status: status || 'Done' };
  });
  const txt = document.body.innerText;
  return {
    rows,
    spinner: !!document.querySelector('.spinner-row'),
    verdict: !!document.querySelector('.winner-verdict, .winner-reveal'),
    winnerLine: /Winner:/i.test(txt),
    rawLeak: /\{\{|\}\}/.test(txt),
    bodyLen: txt.trim().length,
  };
}"""


def _status_of(rows, slug):
    for r in rows:
        if r["name"] == _BRAND[slug]:
            return r["status"]
    return None


def test_live_council_advances_monotonically_across_status_frames(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local import council_status as cs

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    pid = _seed_running(_TOKEN)

    def restamp():
        # Member helpers reload+rewrite the payload; keep the live pid present so the
        # next poll's read doesn't coerce the running council to 'failed'.
        p = cs.load_council_status(_TOKEN)
        if p is not None and p.get("runner_pid") != pid:
            from trinity_local.council_status import _write_status

            p["runner_pid"] = pid
            _write_status(_TOKEN, p)

    httpd, port = _serve(tmp_path)
    history: list[dict] = []  # per-slug status rank seen across frames (monotonicity)
    seen_done_at: dict[str, int] = {}
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1400}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    "dispatch:(a,cb)=>{if(cb)cb({ok:true});},"
                    "probe:()=>Promise.resolve({state:'ready'}),"
                    "subscribe:()=>{},onStateChange:()=>{}};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?status_token={_TOKEN}"
                )
                page.wait_for_timeout(1400)

                ranks_seen: dict[str, list[int]] = {s: [] for s in _MEMBERS}

                def snap(frame_idx: int) -> dict:
                    info = page.evaluate(_PROBE)
                    history.append({"frame": frame_idx, **info})
                    # Record + enforce per-slug monotonicity across all frames so far.
                    for slug in _MEMBERS:
                        st = _status_of(info["rows"], slug)
                        if st in _RANK:
                            ranks_seen[slug].append(_RANK[st])
                        if st == "Done" and slug not in seen_done_at:
                            seen_done_at[slug] = frame_idx
                    return info

                # ---- F0: init running, all queued -----------------------------
                f0 = snap(0)
                assert not errs, f"JS errors on the live page: {errs[:3]}"
                assert f0["bodyLen"] > 150, (
                    "the live page rendered ~blank — petite-vue didn't mount, the "
                    f"advancement bite would be vacuous: {f0!r}"
                )
                assert len(f0["rows"]) == 3, (
                    "the 3 member rows did not render on the running council — the "
                    f"per-member advancement assertions would be vacuous: {f0['rows']!r}"
                )
                # PRECONDITION (non-vacuous): the run STARTS with everyone queued and
                # spinning — there's a real climb to observe, not a pre-finished seed.
                assert all(_status_of(f0["rows"], s) == "Queued" for s in _MEMBERS), (
                    f"F0 should start all-Queued, got {f0['rows']!r}"
                )
                assert f0["spinner"], "F0 running council must show the spinner"
                assert not f0["verdict"], "F0 must not show a verdict (council running)"

                # ---- F1: claude running ---------------------------------------
                cs.start_member_progress(_TOKEN, "claude", model=_MODELS["claude"])
                restamp()
                page.wait_for_timeout(2000)
                f1 = snap(1)
                assert _status_of(f1["rows"], "claude") == "Running", (
                    "STUCK POLLER: claude went Running in the status frame but the live "
                    "page did not advance it past Queued — the council page is frozen on "
                    f"the first frame (the 'watch it happen' moment is dead): {f1['rows']!r}"
                )
                assert _status_of(f1["rows"], "codex") == "Queued"
                assert _status_of(f1["rows"], "antigravity") == "Queued"
                assert not f1["verdict"], "no verdict while members still running"

                # ---- F2: claude done, codex running ---------------------------
                cs.update_member_progress(
                    _TOKEN, "claude",
                    "Use an idempotency-key table keyed on the webhook event id.",
                    model=_MODELS["claude"],
                )
                cs.start_member_progress(_TOKEN, "codex", model=_MODELS["codex"])
                restamp()
                page.wait_for_timeout(2000)
                f2 = snap(2)
                assert _status_of(f2["rows"], "claude") == "Done", (
                    "claude landed an answer but the live row did not flip to Done — "
                    f"the poll did not pick up the second frame: {f2['rows']!r}"
                )
                assert _status_of(f2["rows"], "codex") == "Running"
                assert _status_of(f2["rows"], "antigravity") == "Queued"

                # ---- F3: codex done, antigravity running ----------------------
                cs.update_member_progress(
                    _TOKEN, "codex",
                    "Store a (key, response) row; replay returns the stored response.",
                    model=_MODELS["codex"],
                )
                cs.start_member_progress(_TOKEN, "antigravity", model=_MODELS["antigravity"])
                restamp()
                page.wait_for_timeout(2000)
                f3 = snap(3)
                assert _status_of(f3["rows"], "claude") == "Done"
                assert _status_of(f3["rows"], "codex") == "Done", (
                    f"codex landed but the live row didn't advance to Done: {f3['rows']!r}"
                )
                assert _status_of(f3["rows"], "antigravity") == "Running"

                # ---- F4: all done, synthesis running --------------------------
                cs.update_member_progress(
                    _TOKEN, "antigravity",
                    "Reject duplicate deliveries at the edge via a dedup cache.",
                    model=_MODELS["antigravity"],
                )
                cs.update_synthesis_progress(_TOKEN, "running")
                cs.write_council_status(
                    _TOKEN, status="running",
                    metadata={"chairman_provider": "claude", "chairman_model": "claude-opus-4-8"},
                )
                restamp()
                page.wait_for_timeout(2000)
                f4 = snap(4)
                assert all(_status_of(f4["rows"], s) == "Done" for s in _MEMBERS), (
                    f"all members answered but the live rows aren't all Done: {f4['rows']!r}"
                )
                # The spinner is STILL on (chairman synthesizing) and the verdict is
                # STILL suppressed — a premature verdict here would be a lie.
                assert f4["spinner"], (
                    "the spinner cleared while the chairman was still synthesizing — "
                    f"the live page declared the council done early: {f4!r}"
                )
                assert not f4["verdict"], (
                    "PREMATURE VERDICT: the winner verdict rendered while synthesis was "
                    f"still running (members done, chairman not finished): {f4!r}"
                )

                # ---- F5: completed --------------------------------------------
                cs.update_synthesis_progress(
                    _TOKEN, "done",
                    output_text="The council agrees: an idempotency-key table is the core.",
                    routing_label={"winner": "claude", "task_type": "system-design"},
                )
                cs.write_council_status(
                    _TOKEN, status="completed",
                    synthesis={
                        "status": "done",
                        "response_text": "The council agrees: an idempotency-key table is the core.",
                        "response_html": "<p>The council agrees: an idempotency-key table is the core.</p>",
                        "routing_label": {"winner": "claude", "task_type": "system-design"},
                    },
                    metadata={"chairman_provider": "claude", "chairman_model": "claude-opus-4-8", "round_number": 1},
                    council_id="inc_advance_council",
                )
                page.wait_for_timeout(2800)
                f5 = snap(5)
                assert all(_status_of(f5["rows"], s) == "Done" for s in _MEMBERS), (
                    f"members regressed off Done on completion: {f5['rows']!r}"
                )
                assert not f5["spinner"], (
                    "STUCK SPINNER: the council completed but the live-page spinner never "
                    f"cleared — it spins forever on a finished council: {f5!r}"
                )
                assert f5["verdict"] and f5["winnerLine"], (
                    "the council completed but no winner verdict rendered — the "
                    f"completion transition didn't land: {f5!r}"
                )

                # ---- MONOTONICITY across the whole run ------------------------
                for snapshot in history:
                    assert not snapshot["rawLeak"], (
                        f"raw {{{{ }}}} template leak at frame {snapshot['frame']}"
                    )
                for slug, ranks in ranks_seen.items():
                    assert ranks == sorted(ranks), (
                        f"FLICKER-BACK: {_BRAND[slug]} regressed to an earlier status "
                        f"across frames (rank trail {ranks}) — a member shown Done then "
                        "Queued/Running again is a lying live view"
                    )
                # No member read Done before the frame that marked it done.
                assert seen_done_at.get("claude", 99) >= 2, (
                    f"claude read Done before its frame (first Done at {seen_done_at}): "
                    "a member shown finished before it answered"
                )
                assert seen_done_at.get("codex", 99) >= 3, (
                    f"codex read Done before its frame (first Done at {seen_done_at})"
                )
                assert seen_done_at.get("antigravity", 99) >= 4, (
                    f"antigravity read Done before its frame (first Done at {seen_done_at})"
                )
                assert not errs, f"JS errors across the advancement run: {errs[:3]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
