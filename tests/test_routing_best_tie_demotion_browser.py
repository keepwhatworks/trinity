"""Green-gate #35: the routing "Best" column must DEMOTE a tie / coin-flip.

Found 2026-06-17 by driving the REAL /stats cheat-sheet at low n: a task type
where the chairman split his picks evenly — `debug` 2-2 (Claude/Codex), `strategy`
1-1-1 (Claude/Codex/Gemini) — cleared MIN_BEST_SAMPLES (>=3 councils) and rendered
an IDENTICAL confident "Best: GPT · picked 1 of 3" chip to a genuine 4-of-5
plurality. The tie was broken arbitrarily by mean overall (8.3 vs 8.2 — a 0.1 gap)
and presented as "who the chairman picks most often." A 33%/50% split is a
coin-flip, not a pattern — the same overclaim the cortex picks surface already
guards against with "Lean X · near-tie" below WINNER_MARGIN_FLOOR.

This is Trinity's #1 bug shape (a green passing while the data is degenerate): the
"Best" chip is the green; a tie has no margin to support it. The fix emits a
per-task-type `pick_is_tie` flag from personal_routing.aggregate_routing_table
(True iff the best provider has NO strict chairman-win lead over the runner-up, or
there's no chairman supervision at all). BOTH render surfaces consume it:
  * the /stats cheat-sheet → "Lean X · no clear pick" + "tied N of M" (no chip),
  * the routing.json memory viewer → "X · tied" (no bold winner cell).

A clear plurality (4-of-5) keeps the confident chip — the demotion must REFUSE on
real signal, or it cries wolf on every legitimate pick.

Three layers, mutation-proven both directions:
  1. FAST data layer — aggregate_routing_table marks ties, not clear winners.
  2. REAL browser, cheat-sheet — clear row keeps the .suggestion-chip; tie rows
     have NO chip + read "Lean … · no clear pick".
  3. REAL browser, routing.json viewer — tie rows read "· tied" + no .best cell.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. FAST data-layer guard (no browser) — the pick_is_tie computation itself.
# ---------------------------------------------------------------------------
def _council(task_type: str, winner: str, scores: dict[str, float]) -> dict:
    return {
        "task_type": task_type,
        "chairman_winner": winner,
        "routing_label": {
            "task_type": task_type,
            "winner": winner,
            "provider_scores": {p: {"overall": v} for p, v in scores.items()},
        },
    }


def test_data_layer_clear_plurality_is_not_a_tie():
    from trinity_local.personal_routing import aggregate_routing_table

    councils = [_council("code_refactor", "codex", {"codex": 8.6, "claude": 8.1}) for _ in range(4)]
    councils.append(_council("code_refactor", "claude", {"codex": 8.6, "claude": 8.1}))
    t = aggregate_routing_table(councils)
    assert t["best_per_task_type"]["code_refactor"] == "codex"
    assert "code_refactor" not in t["pick_is_tie"], (
        "a 4-of-5 chairman plurality is a CLEAR pick — must NOT be flagged a tie "
        "(else the demotion cries wolf on real signal)"
    )


def test_data_layer_two_two_split_is_a_tie():
    from trinity_local.personal_routing import aggregate_routing_table

    councils = [_council("debug", "claude", {"claude": 8.2, "codex": 8.3}) for _ in range(2)]
    councils += [_council("debug", "codex", {"claude": 8.2, "codex": 8.3}) for _ in range(2)]
    t = aggregate_routing_table(councils)
    # best is still populated (tie-broken by overall) so the chip has a name,
    assert t["best_per_task_type"].get("debug")
    # but it is flagged a tie so the surface demotes it.
    assert t["pick_is_tie"].get("debug") is True, (
        "a 2-2 chairman split is a coin-flip — pick_is_tie must flag it so the "
        "cheat-sheet doesn't paint a confident 'Best' over a 50/50 tie"
    )


def test_data_layer_three_way_even_split_is_a_tie():
    from trinity_local.personal_routing import aggregate_routing_table

    councils = [
        _council("strategy", "claude", {"claude": 8.0, "codex": 8.1, "antigravity": 7.9}),
        _council("strategy", "codex", {"claude": 8.0, "codex": 8.1, "antigravity": 7.9}),
        _council("strategy", "antigravity", {"claude": 8.0, "codex": 8.1, "antigravity": 7.9}),
    ]
    t = aggregate_routing_table(councils)
    assert t["pick_is_tie"].get("strategy") is True, (
        "a 1-1-1 chairman split is a three-way coin-flip — must be flagged a tie"
    )


def _walkover(task_type: str, winner: str) -> dict:
    """A WALKOVER council: the chairman has a winner, but only ONE provider gave
    a substantive answer (substantive_members=1), so the "win" was by default —
    nobody else ran. This is the scan-record shape `_scan_outcomes` emits."""
    return {
        "task_type": task_type,
        "chairman_winner": winner,
        "winner_provider": winner,
        "substantive_members": 1,
        "routing_label": {
            "task_type": task_type,
            "winner": winner,
            "provider_scores": {winner: {"overall": 8.4}},
        },
    }


def test_data_layer_pure_walkover_best_is_demoted_to_no_clear_pick():
    """SOLO-OVERCLAIM #35 (the walkover sibling of the tie demotion): a task_type
    backed ONLY by walkovers — the lone provider was the sole substantive voice
    every council, so the chairman "picked" it by default, not on quality — must
    NOT crown a confident "Best: X". It "won" only because nobody else ran. This
    is the un-fixed sibling of council_value_proof's `_is_real_contest` gate: the
    value-proof restricts its headline to real contests, but the routing
    cheat-sheet + routing.json reader's "Best" column never inherited it."""
    from trinity_local.personal_routing import aggregate_routing_table

    # 4 walkover councils, claude the sole voice every time → strict 4-0 win lead,
    # clears MIN_BEST_SAMPLES, but ZERO real contests.
    councils = [_walkover("refactor", "claude") for _ in range(4)]
    t = aggregate_routing_table(councils)
    # best still carries a name (so the demoted chip can say "Lean Claude"),
    assert t["best_per_task_type"].get("refactor") == "claude"
    # but it's flagged so the surface demotes the confident chip.
    assert t["pick_is_tie"].get("refactor") is True, (
        "a task_type backed ONLY by WALKOVERS (1 substantive voice each) gave a "
        "confident 'Best: Claude · picked 4 of 4' — Claude 'won' only because "
        "nobody else ran (the council-card solo-overclaim shape #35). pick_is_tie "
        "must flag it so the cheat-sheet demotes to 'Lean … · no clear pick'"
    )


def test_data_layer_one_real_contest_rescues_the_confident_pick():
    """NON-VACUOUS companion: the walkover demotion must REFUSE the instant the
    task_type has even ONE real contest backing the same winner — else it would
    cry wolf on a provider that genuinely beat an opponent. claude wins 4
    councils, ONE of which is a real 2-member contest → confident pick stands."""
    from trinity_local.personal_routing import aggregate_routing_table

    councils = [_walkover("refactor", "claude") for _ in range(3)]
    councils.append({
        "task_type": "refactor",
        "chairman_winner": "claude",
        "winner_provider": "claude",
        "substantive_members": 2,  # a REAL contest — claude beat a live opponent
        "routing_label": {
            "task_type": "refactor",
            "winner": "claude",
            "provider_scores": {"claude": {"overall": 8.4}, "codex": {"overall": 8.0}},
        },
    })
    t = aggregate_routing_table(councils)
    assert t["best_per_task_type"].get("refactor") == "claude"
    assert "refactor" not in t["pick_is_tie"], (
        "one real contest backing the winner makes the pick EARNED — the walkover "
        "demotion must not fire (else it cries wolf on a genuine cross-provider win)"
    )


def test_data_layer_no_chairman_supervision_is_a_tie():
    """All councils lack a chairman winner → 'best' is a bare mean-score lead, not
    a chairman pick. The surface must NOT claim 'the chairman picks X'."""
    from trinity_local.personal_routing import aggregate_routing_table

    councils = [
        {"task_type": "qna", "routing_label": {"task_type": "qna",
            "provider_scores": {"claude": {"overall": 8.0}, "codex": {"overall": 7.5}}}}
        for _ in range(3)
    ]
    t = aggregate_routing_table(councils)
    assert t["best_per_task_type"].get("qna") == "claude"
    assert t["pick_is_tie"].get("qna") is True, (
        "no chairman supervision → the 'best' is mean-score only; flag it a tie so "
        "the surface doesn't claim a chairman pick that never happened"
    )


# ---------------------------------------------------------------------------
# Shared seeder: write real council outcomes so the FULL pipeline
# (personal_routing → launchpad_data → render) produces pick_is_tie.
# ---------------------------------------------------------------------------
def _seed_outcomes(home: Path) -> None:
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    os.environ["TRINITY_HOME"] = str(home)
    (home / "council_outcomes").mkdir(parents=True, exist_ok=True)
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local import personal_routing

    plan = [
        # task_type, winner, scores
        *[("code_refactor", "codex", {"codex": 8.6, "claude": 8.1})] * 4,
        ("code_refactor", "claude", {"codex": 8.6, "claude": 8.1}),
        *[("debug", "claude", {"claude": 8.2, "codex": 8.3})] * 2,
        *[("debug", "codex", {"claude": 8.2, "codex": 8.3})] * 2,
        ("strategy", "claude", {"claude": 8.0, "codex": 8.1, "antigravity": 7.9}),
        ("strategy", "codex", {"claude": 8.0, "codex": 8.1, "antigravity": 7.9}),
        ("strategy", "antigravity", {"claude": 8.0, "codex": 8.1, "antigravity": 7.9}),
    ]
    for i, (task, winner, scores) in enumerate(plan):
        cid = f"c{i:03d}"
        members = [
            CouncilMemberResult(provider=p, model="m", output_text="x" * 250)
            for p in scores
        ]
        label = CouncilRoutingLabel(
            winner=winner,
            task_type=task,
            provider_scores={p: {"overall": v} for p, v in scores.items()},
        )
        save_council_outcome(CouncilOutcome(
            council_run_id=cid,
            bundle_id=f"b{i:03d}",
            task_cluster_id="cluster",
            primary_provider="claude",
            winner_provider=winner,
            created_at="2026-06-17T00:00:00",
            member_results=members,
            synthesis_output="Chairman synthesis: " + ("y" * 200),
            routing_label=label,
            metadata={"task_type": task},
        ))
    personal_routing.invalidate_cache()


def _seed_walkover_outcomes(home: Path) -> None:
    """Seed a discriminating ledger for the WALKOVER-overclaim browser guard:
      * `solo_refactor` — 4 WALKOVER councils (only claude gave a substantive
        answer; the other members echoed a 5-char stub → substantive_members=1).
        claude has a strict 4-0 chairman-win lead, clears MIN_BEST_SAMPLES — but
        nobody contested it. Must DEMOTE to "no clear pick".
      * `real_design` — 4 REAL contests (both members substantive); claude wins
        3-1 over a live opponent. Must KEEP its confident chip (non-vacuous).
    """
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    os.environ["TRINITY_HOME"] = str(home)
    (home / "council_outcomes").mkdir(parents=True, exist_ok=True)
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local import personal_routing

    plan = [
        # task, winner, members[(provider, output_text)], scores
        *[("solo_refactor", "claude",
           [("claude", "x" * 250), ("codex", "n/a")],  # codex echoed a stub → not substantive
           {"claude": 8.4, "codex": 8.0})] * 4,
        *[("real_design", "claude",
           [("claude", "x" * 250), ("codex", "y" * 250)],  # both substantive → real contest
           {"claude": 8.4, "codex": 8.0})] * 3,
        ("real_design", "codex",
         [("claude", "x" * 250), ("codex", "y" * 250)],
         {"claude": 8.4, "codex": 8.0}),
    ]
    for i, (task, winner, member_specs, scores) in enumerate(plan):
        cid = f"w{i:03d}"
        members = [
            CouncilMemberResult(provider=p, model="m", output_text=txt)
            for p, txt in member_specs
        ]
        label = CouncilRoutingLabel(
            winner=winner,
            task_type=task,
            provider_scores={p: {"overall": v} for p, v in scores.items()},
        )
        save_council_outcome(CouncilOutcome(
            council_run_id=cid,
            bundle_id=f"wb{i:03d}",
            task_cluster_id="cluster",
            primary_provider="claude",
            winner_provider=winner,
            created_at="2026-06-18T00:00:00",
            member_results=members,
            synthesis_output="Chairman synthesis: " + ("y" * 200),
            routing_label=label,
            metadata={"task_type": task},
        ))
    personal_routing.invalidate_cache()


def _render_stats(home: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists()
    return page


def _render_memory(home: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Freeze routing.json to disk so the memory viewer inlines it (it reads the
    # frozen scoreboard, not the lazy in-process table).
    subprocess.run(
        [sys.executable, "-c",
         "from trinity_local import personal_routing;personal_routing.invalidate_cache();"
         "personal_routing.freeze_routing_to_disk()"],
        env=env, capture_output=True, text=True, timeout=120, check=True,
    )
    # portal-html writes BOTH stats.html and memory.html under portal_pages/.
    out = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, f"portal-html failed: {out.stderr[-400:]}"
    page = home / "portal_pages" / "memory.html"
    assert page.exists(), "portal-html didn't write memory.html"
    return page


# ---------------------------------------------------------------------------
# 2. REAL browser — cheat-sheet demotes ties, keeps the clear pick's chip.
# ---------------------------------------------------------------------------
@pytest.mark.slow
@pytest.mark.browser
def test_cheatsheet_demotes_tie_keeps_clear_pick_chip():
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_outcomes(home)
    page_path = _render_stats(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2200})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the cheat-sheet: {errs[:3]}"
            rows = page.evaluate(
                """() => {
                  const out = {};
                  for (const tr of document.querySelectorAll('.routing-table tbody tr')) {
                    const tds = tr.querySelectorAll('td');
                    const task = (tds[0]?.innerText || '').replace(/\\s+/g,' ').trim().toLowerCase();
                    out[task] = {
                      best: (tds[1]?.innerText || '').replace(/\\s+/g,' ').trim(),
                      hasChip: !!tds[1]?.querySelector('.suggestion-chip'),
                    };
                  }
                  return out;
                }"""
            )
        finally:
            browser.close()

    # The cheat-sheet must have rendered all three (n>=2) rows.
    assert any("refactor" in k for k in rows), f"no code_refactor row rendered: {rows}"
    clear = next(v for k, v in rows.items() if "refactor" in k)
    debug = next(v for k, v in rows.items() if k == "debug")
    strategy = next(v for k, v in rows.items() if k == "strategy")

    # CLEAR pick (4 of 5) keeps the confident chip.
    assert clear["hasChip"], (
        "the 4-of-5 clear chairman plurality must keep its confident 'Best' chip — "
        "the tie-demotion must REFUSE on real signal"
    )
    assert "picked 4 of 5" in clear["best"]

    # TIE rows (2-2 and 1-1-1) are DEMOTED: no chip, "no clear pick" wording.
    for name, row in (("debug", debug), ("strategy", strategy)):
        assert not row["hasChip"], (
            f"{name} is a chairman TIE but rendered a confident 'Best' chip — a "
            f"coin-flip painted as a clear pick (green-gate #35). Saw: {row['best']!r}"
        )
        assert "no clear pick" in row["best"].lower(), (
            f"{name} is a tie but the cell does not say 'no clear pick': {row['best']!r}"
        )
        assert "tied" in row["best"].lower(), (
            f"{name} tie count must read 'tied N of M', not 'picked': {row['best']!r}"
        )


# ---------------------------------------------------------------------------
# 2b. REAL browser — cheat-sheet demotes a WALKOVER "Best", keeps a real-contest
#     plurality's chip (the solo-overclaim #35 sibling of the tie demotion).
# ---------------------------------------------------------------------------
@pytest.mark.slow
@pytest.mark.browser
def test_cheatsheet_demotes_walkover_keeps_real_contest_chip():
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_walkover_outcomes(home)
    page_path = _render_stats(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2200})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the cheat-sheet: {errs[:3]}"
            rows = page.evaluate(
                """() => {
                  const out = {};
                  for (const tr of document.querySelectorAll('.routing-table tbody tr')) {
                    const tds = tr.querySelectorAll('td');
                    const task = (tds[0]?.innerText || '').replace(/\\s+/g,' ').trim().toLowerCase();
                    out[task] = {
                      best: (tds[1]?.innerText || '').replace(/\\s+/g,' ').trim(),
                      hasChip: !!tds[1]?.querySelector('.suggestion-chip'),
                    };
                  }
                  return out;
                }"""
            )
        finally:
            browser.close()

    # Both task types cleared the n>=2 cheat-sheet floor and rendered.
    walk = next((v for k, v in rows.items() if "solo refactor" in k or "solo_refactor" in k), None)
    real = next((v for k, v in rows.items() if "real design" in k or "real_design" in k), None)
    assert walk is not None, f"no solo_refactor (walkover) row rendered: {rows}"
    assert real is not None, f"no real_design row rendered: {rows}"

    # WALKOVER row (claude 4-0, but every council was solo): DEMOTED — no chip.
    assert not walk["hasChip"], (
        "a task_type backed ONLY by WALKOVER councils (claude the lone substantive "
        "voice 4 of 4) painted a confident 'Best: Claude' chip — Claude 'won' only "
        f"because nobody else ran (solo-overclaim #35). Saw: {walk['best']!r}"
    )
    assert "no clear pick" in walk["best"].lower(), (
        f"the walkover row must read 'Lean … · no clear pick', saw: {walk['best']!r}"
    )

    # REAL-contest plurality (claude 3-1 over a live opponent): KEEPS its chip.
    assert real["hasChip"], (
        "the 3-of-4 plurality across REAL contests must keep its confident 'Best' "
        "chip — the walkover demotion must REFUSE on a genuine cross-provider win"
    )
    assert "picked 3 of 4" in real["best"], (
        f"the real-contest row must read 'picked 3 of 4', saw: {real['best']!r}"
    )


# ---------------------------------------------------------------------------
# 3. REAL browser — routing.json viewer demotes ties (no bold winner cell).
# ---------------------------------------------------------------------------
@pytest.mark.slow
@pytest.mark.browser
def test_routing_viewer_demotes_tie_keeps_clear_pick():
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_outcomes(home)
    page_path = _render_memory(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}?file=routing.json", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the routing viewer: {errs[:3]}"
            rows = page.evaluate(
                """() => {
                  const out = {};
                  for (const tr of document.querySelectorAll('.routing-table tbody tr')) {
                    const tds = tr.querySelectorAll('td');
                    const task = (tds[0]?.innerText || '').replace(/\\s+/g,' ').trim().toLowerCase();
                    out[task] = {
                      best: (tds[tds.length-1]?.innerText || '').replace(/\\s+/g,' ').trim(),
                      boldWinnerCells: tr.querySelectorAll('td.best').length,
                    };
                  }
                  return out;
                }"""
            )
        finally:
            browser.close()

    clear = next(v for k, v in rows.items() if "refactor" in k)
    debug = next(v for k, v in rows.items() if k == "debug")
    strategy = next(v for k, v in rows.items() if k == "strategy")

    # CLEAR pick: bare provider name + one bold winning cell.
    assert "tied" not in clear["best"].lower(), (
        f"the clear 4-of-5 pick must not be marked a tie: {clear['best']!r}"
    )
    assert clear["boldWinnerCells"] == 1, (
        "the clear pick must keep its highlighted winning score cell"
    )

    # TIE rows: "· tied" wording + NO bold winner cell.
    for name, row in (("debug", debug), ("strategy", strategy)):
        assert "tied" in row["best"].lower(), (
            f"{name} is a chairman TIE but the routing.json viewer's Best cell does "
            f"not say '· tied': {row['best']!r} (green-gate #35)"
        )
        assert row["boldWinnerCells"] == 0, (
            f"{name} is a tie but a winning cell is still bolded — a coin-flip "
            f"highlighted as a clear winner"
        )
