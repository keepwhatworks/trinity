"""Browser guard: the recent-councils RAIL must not overclaim a winner on a
SOLO (1-responder) council.

A council where only one model responded still carries a chairman-emitted
`winner_provider` — the chairman runs regardless of member count. The share card
(Iter 57) and the live council page (Iter 74) both learned to SUPPRESS the winner
framing on such a council ("One model — no council; there's no winner"). The
recent-councils rail was the unfixed sibling: it rendered the bare winner brand
("Claude · Jun 18") in the meta line, which reads as "this model won a contest"
when no contest happened — a solo model can't win against nobody.

This guard seeds a REAL solo council + a REAL 2-member council on disk (so the
WHOLE pipeline runs: `_load_recent_councils` computes the member count from the
on-disk outcome AND `build_recent_sidebar_html` renders it), renders the real
file:// launchpad, and reads the RENDERED rail meta (geometry/text-content, NOT a
source string check):
  • the SOLO row's winner token reads "Solo" — NOT the winner brand;
  • the 2-member row STILL reads its winner brand (so this isn't a blanket
    suppression that would hide every winner).

Mutation-proven: reverting either layer (the `latest_member_count` capture in
_load_recent_councils OR the `is_solo` branch in build_recent_sidebar_html)
re-introduces the "Claude" winner brand on the solo row and REDS this guard.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _seed_outcome(cid: str, members: list[str], winner: str, created: str) -> None:
    """Write a schema-correct council outcome to council_outcomes/."""
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    member_results = [
        # >= 200 chars so it's a "substantive" responder (not that the rail
        # cares — the rail counts member rows, mirroring council_review's
        # `_solo = len(member_results) <= 1` — but it keeps the seed honest).
        CouncilMemberResult(provider=p, output_text=f"Answer from {p}. " + ("x" * 220))
        for p in members
    ]
    label = CouncilRoutingLabel(
        winner=winner,
        confidence="high",
        task_type="code",
        agreed_claims=["Ship it now", "Tests are green"],
        disagreed_claims=[],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=cid,
            bundle_id=cid + "_b",
            task_cluster_id="tc",
            primary_provider=members[0],
            winner_provider=winner,
            member_results=member_results,
            synthesis_output="synthesized verdict",
            routing_label=label,
            created_at=created,
            metadata={"task_text": f"Question for {cid}"},
        )
    )


def test_rail_solo_council_shows_Solo_not_a_winner_brand(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # SOLO council (only claude responded) — newest, so it's the top rail row.
    _seed_outcome("council_solo01", ["claude"], "claude", "2026-06-18T10:00:00")
    # 2-member control (a real contest, codex won) — the positive control.
    _seed_outcome("council_duo01", ["claude", "codex"], "codex", "2026-06-18T09:00:00")

    import trinity_local.launchpad_page as lp

    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            # Read the rendered rail rows keyed by their (deterministic) title.
            rows = page.evaluate(
                "() => [...document.querySelectorAll('.council-rail .rail-council')]"
                ".map(r => ({"
                "  title: (r.querySelector('.rail-council-title')||{}).textContent || '',"
                "  winner: (r.querySelector('.rail-council-winner')||{}).textContent || '',"
                "  meta: (r.querySelector('.rail-council-meta')||{}).textContent || '',"
                "}))"
            )
            by_title = {r["title"]: r for r in rows}
            solo = by_title.get("Question for council_solo01")
            duo = by_title.get("Question for council_duo01")
            assert solo is not None, f"solo council row missing from rail: {rows}"
            assert duo is not None, f"duo council row missing from rail: {rows}"

            # THE BUG: a solo council rendered the winner brand ("Claude"),
            # claiming a contest win where only one model answered.
            assert solo["winner"] == "Solo", (
                "the recent-councils RAIL overclaims a winner on a SOLO "
                "(1-responder) council: the meta line reads "
                f"{solo['winner']!r} (a model brand) instead of 'Solo'. A solo "
                "council can't win against nobody — this is the share-card "
                "(Iter 57) / live-page (Iter 74) overclaim, unfixed on the rail."
            )
            assert "Claude" not in solo["meta"], (
                "the solo rail meta still leaks the winner brand 'Claude': "
                f"{solo['meta']!r}"
            )

            # Positive control: a REAL 2-member contest MUST still show its
            # winner brand — so the solo fix isn't a blanket suppression.
            assert duo["winner"] == "GPT", (
                "a real 2-member council lost its winner brand in the rail — "
                f"the solo guard over-suppressed: {duo['winner']!r}, meta {duo['meta']!r}"
            )

            assert not errs, f"JS errors rendering the rail: {errs[:3]}"
        finally:
            browser.close()


def test_rail_all_same_provider_council_shows_Solo_not_a_winner_brand(tmp_path, monkeypatch):
    """The DEGENERATE same-provider case (Iter 111): a council whose members are
    ALL the same provider (claude·claude·claude) has 3 responders but ONE
    distinct voice — the chairman's winner is its own runner-up. The old rail
    gate counted raw member rows (3 > 1 → NOT solo), so it rendered the winner
    brand "Claude" as if a contest happened. The gate must count DISTINCT
    provider slugs, so the rail collapses to the honest "Solo" marker. Same
    pipeline as the 1-responder guard above (real on-disk outcome →
    _load_recent_councils distinct-count → build_recent_sidebar_html → rendered
    rail DOM), so reverting either layer reds this guard."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # ALL-SAME-PROVIDER council (3 claude) — newest, top rail row. 3 responders,
    # one distinct voice → must read "Solo", NOT "Claude".
    _seed_outcome("council_same01", ["claude", "claude", "claude"], "claude", "2026-06-18T10:00:00")
    # 3-DISTINCT control (a real 3-way contest, antigravity/Gemini won).
    _seed_outcome("council_trio01", ["claude", "codex", "antigravity"], "antigravity", "2026-06-18T09:00:00")

    import trinity_local.launchpad_page as lp

    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            rows = page.evaluate(
                "() => [...document.querySelectorAll('.council-rail .rail-council')]"
                ".map(r => ({"
                "  title: (r.querySelector('.rail-council-title')||{}).textContent || '',"
                "  winner: (r.querySelector('.rail-council-winner')||{}).textContent || '',"
                "  meta: (r.querySelector('.rail-council-meta')||{}).textContent || '',"
                "}))"
            )
            by_title = {r["title"]: r for r in rows}
            same = by_title.get("Question for council_same01")
            trio = by_title.get("Question for council_trio01")
            assert same is not None, f"same-provider council row missing from rail: {rows}"
            assert trio is not None, f"trio council row missing from rail: {rows}"

            # THE BUG: an all-same-provider council rendered the winner brand
            # ("Claude"), claiming a contest win between three identical voices.
            assert same["winner"] == "Solo", (
                "the recent-councils RAIL overclaims a winner on an ALL-SAME-"
                "PROVIDER (claude·claude·claude) council: the winner token reads "
                f"{same['winner']!r} (a model brand) instead of 'Solo'. Three "
                "identical voices are not a contest — the winner is its own "
                "runner-up; the gate must count DISTINCT providers (Iter 111)."
            )
            assert "Claude" not in same["meta"], (
                "the same-provider rail meta still leaks the winner brand "
                f"'Claude': {same['meta']!r}"
            )

            # Positive control: a REAL 3-distinct contest MUST still show its
            # winner brand — so the same-provider fix isn't a blanket suppression.
            assert trio["winner"] == "Gemini", (
                "a real 3-distinct council lost its winner brand in the rail — "
                f"the same-provider guard over-suppressed: {trio['winner']!r}, "
                f"meta {trio['meta']!r}"
            )

            assert not errs, f"JS errors rendering the rail: {errs[:3]}"
        finally:
            browser.close()
