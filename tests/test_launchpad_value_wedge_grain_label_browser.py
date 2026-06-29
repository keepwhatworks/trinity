"""Guard (#278): the HOME value-proof WEDGE and the /stats "Best by task type"
CHEAT-SHEET name a leader for "the same domain" at DIFFERENT grains — so the
copy on each surface MUST name its grain, or the two read as a contradiction.

The founder-flagged #278 symptom: the wedge says "code→Claude" while the
cheat-sheet's Best column says "code_gen→GPT" for what looks like the same
domain — "grain confusion". This is NOT a math bug. Verified at source:

  * the wedge (council_category_wedge) coarsens the chairman task_type to its
    HEAD TOKEN — code_gen + code_review → family "code" — and tallies the raw
    chairman winner over that whole FAMILY.
  * the cheat-sheet Best column (best_per_task_type) tallies the raw chairman
    winner per FULL task_type — code_gen and code_review are separate rows.

Both read the SAME council_outcomes/ ledger with the SAME raw-chairman-win tally;
they only differ in AGGREGATION GRAIN. So when a family's task types split their
winners — Claude wins code_review, GPT wins code_gen — the family-leader
(Claude, because the family is 7-4 Claude) LEGITIMATELY differs from the
finer-grain code_gen leader (GPT, 4-0). Different-by-design, not a stale/duplicate
computation. The DEFECT is the copy: the wedge used to be labeled "by kind:",
colliding with the cheat-sheet's "per kind of question" framing, so a reader
mapped the wedge's "code" onto the cheat-sheet's "code_gen" and read two correct
numbers as a disagreement. The fix names the grain: the home wedge reads
"by area:" (a broad family rollup, with a tooltip pointing to the cheat-sheet for
the per-task-type winner); the /stats cheat-sheet reads "by task type".

This guard seeds a DISCRIMINATING ledger where the two grains genuinely diverge
and asserts BOTH surfaces, in one rendered launchpad DOM, carry DISTINCT grain
labels — the wedge's "area" and the cheat-sheet's "task type" — so a user can
tell the coarse rollup from the fine breakdown.

Mutation-proven to BITE: revert the wedge label "by area:" → "by kind:" in
launchpad_template.py and this test reds — both surfaces then read "kind", the
exact grain collision #278 named — while the value, brand, and visibility
preconditions pass first (so the bite is the grain LABEL, not a vacuous miss).

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

import trinity_local.personal_routing as pr

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _records():
    """A DISCRIMINATING real-contest ledger where the wedge family-grain leader
    and the cheat-sheet task_type-grain leader genuinely DIVERGE for "code":

      code_review : Claude beats GPT 7-0   (clears MIN_BEST_SAMPLES=3)
      code_gen    : GPT beats Claude 4-0   (clears MIN_BEST_SAMPLES=3)
        → family "code": Claude 7, GPT 4 → margin 3 (clears WEDGE_MIN_MARGIN=3),
          n=11 (clears WEDGE_MIN_CONTESTS=8) → wedge reads code→Claude
        → cheat-sheet Best: code_review→Claude, code_gen→GPT  (SAME word "code",
          a finer grain, a DIFFERENT leader — the #278 divergence, by design)
      plus two more domains so the value-proof headline clears its floors.

    Every record is a real contest (substantive_members == 2). The default
    (primary_provider) is the runner-up each council, so changed_pct is high
    enough to clear the value floor."""
    out = []

    def add(task_type, winner, runner, n):
        for _ in range(n):
            out.append({
                "chairman_winner": winner,
                "winner_provider": winner,
                "primary_provider": runner,  # default != winner → counts as "changed"
                "substantive_members": 2,
                "task_type": task_type,
                # provider_scores is what populates by_task_type → best_per_task_type
                # (the cheat-sheet Best column). Without it aggregate_routing_table
                # skips the task_type entirely and the cheat-sheet stays empty.
                "routing_label": {
                    "task_type": task_type,
                    "winner": winner,
                    "provider_scores": {winner: {"overall": 0.82}, runner: {"overall": 0.61}},
                },
            })

    add("code_review", "claude", "codex", 7)   # Claude 7-0
    add("code_gen", "codex", "claude", 4)        # GPT 4-0  (finer-grain code leader = GPT)
    add("design_arch", "claude", "antigravity", 3)
    add("strategy_plan", "antigravity", "claude", 3)
    return out


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_wedge_and_cheatsheet_name_their_grain_so_they_dont_collide(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(), True))
    pr.invalidate_cache()  # the routing table caches on the (empty) disk signature

    # Source sanity — the two grains genuinely diverge on this seed, or the
    # browser assertion would chase a moving target. Wedge family "code" → Claude;
    # cheat-sheet code_gen → codex (GPT). DIFFERENT leaders, same word "code".
    wedge = pr.council_category_wedge()
    code_fam = next((w for w in wedge if w["family"] == "code"), None)
    assert code_fam is not None and code_fam["leader"] == "claude", (
        f"seed must make the wedge family 'code' lead with claude: {wedge}"
    )
    rt = pr.compute_personal_routing_table()
    assert rt["best_per_task_type"].get("code_gen") == "codex", (
        f"seed must make the cheat-sheet code_gen Best = codex (GPT), diverging from "
        f"the wedge's code→Claude — the #278 grain split: {rt['best_per_task_type']}"
    )
    assert rt["best_per_task_type"].get("code_review") == "claude"

    html = render_launchpad_html()  # both home wedge + stats cheat-sheet in one DOM
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 900, "height": 1600}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stub'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                state = page.evaluate(
                    """() => {
                      const cv = document.querySelector('p.council-value');
                      const metas = cv ? [...cv.querySelectorAll('span.meta')]
                        .map(s => (s.textContent || '').replace(/\\s+/g, ' ').trim()) : [];
                      const wedgeMeta = metas.find(t => /→/.test(t) && /:/.test(t)) || null;
                      // The cheat-sheet "by task type" section: header + the code_gen
                      // Best chip. Both live in the DOM (CSS view-class only toggles
                      // display); read the painted text regardless of which view is on.
                      const heads = [...document.querySelectorAll('th')]
                        .map(t => (t.textContent || '').trim());
                      // The code_gen row Best chip — find the row whose first cell text
                      // contains "Code Gen" (title-cased by the template).
                      let codeGenBest = null;
                      for (const tr of document.querySelectorAll('table.routing-table tbody tr')) {
                        const cells = [...tr.querySelectorAll('td')];
                        const label = cells.length ? (cells[0].textContent || '').replace(/\\s+/g, ' ').trim() : '';
                        if (/code gen/i.test(label)) {
                          codeGenBest = (cells[1] ? cells[1].textContent : '').replace(/\\s+/g, ' ').trim();
                          break;
                        }
                      }
                      return {
                        wedgePresent: !!cv,
                        wedgeVisible: cv ? cv.offsetParent !== null : false,
                        wedgeMeta,
                        cheatHasTaskTypeHeader: heads.some(h => /^task type$/i.test(h)),
                        codeGenBest,
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # BITE preconditions — the wedge must have painted, or the grain-label
    # assertion can't bite.
    assert state["wedgePresent"], (
        "the #236 value-proof card never rendered on a ledger that clears every floor "
        "— the #278 grain-label assertion can't bite"
    )
    assert state["wedgeVisible"], "the value-proof card is in the DOM but not visible"
    assert state["wedgeMeta"], f"the per-area wedge meta line never painted: {state}"

    # ── #278 ROOT: the wedge names its COARSE grain ("area"), NOT "kind". The old
    #    "by kind:" label collided with the cheat-sheet's "per kind of question"
    #    framing — the same word at two grains read as a contradiction. ──────────
    assert state["wedgeMeta"].lower().startswith("by area:"), (
        "#278 grain confusion: the value-proof wedge must label its COARSE family "
        "rollup as 'by area:' so it doesn't collide with the /stats cheat-sheet's "
        "per-task-type grain. A regressed 'by kind:' label makes 'code→Claude' (area) "
        f"read as a contradiction of the cheat-sheet's 'code_gen→GPT' (task type). Saw: {state['wedgeMeta']!r}"
    )
    assert "code→Claude" in state["wedgeMeta"], (
        f"the wedge must still name the family leader code→Claude. Saw: {state['wedgeMeta']!r}"
    )

    # ── The /stats cheat-sheet names the FINER grain ("Task type") and genuinely
    #    DIVERGES from the wedge for the word "code": code_gen's Best is GPT, not
    #    Claude. The two surfaces are now DISTINGUISHABLE by their grain labels. ──
    assert state["cheatHasTaskTypeHeader"], (
        "the /stats cheat-sheet must keep its 'Task type' column header — the finer "
        "grain the wedge's 'area' rolls up. Without it the two surfaces share no "
        f"grain qualifier and #278's collision returns. Saw headers via state: {state}"
    )
    assert state["codeGenBest"] and "GPT" in state["codeGenBest"], (
        "the cheat-sheet's code_gen Best must paint GPT — the finer-grain leader that "
        "LEGITIMATELY differs from the wedge's code→Claude (the family is 7-4 Claude, "
        "but the code_gen task is 4-0 GPT). If this reds, the grains stopped diverging "
        f"and the discriminating fixture no longer exercises #278. Saw: {state['codeGenBest']!r}"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
