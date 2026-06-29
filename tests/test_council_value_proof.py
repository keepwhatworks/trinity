"""#236: surface the council value proof from the existing council_outcomes/.

The council-first painkiller in one stat: how often the chairman picked a
DIFFERENT model than the user's default (i.e. how often a single-provider
habit would have shipped the worse answer), plus the per-lab win split.
No new eval, no model calls — pure aggregation over the outcomes ledger.

These guard the math + the confidence threshold + the load-boundary provider
canonicalization (web-capture brand names fold into the canonical slugs), and
that the launchpad/status surfaces self-hide on a thin ledger.
"""
from __future__ import annotations

import trinity_local.personal_routing as pr


def test_scan_outcomes_records_distinct_voices_from_disk(monkeypatch, tmp_path):
    """WIRE-IN guard for the same-family fix: `_scan_outcomes` must derive
    `distinct_substantive_providers` from the actual member_result providers on
    disk (collapsing a claude·claude·claude chain to ONE distinct voice), and
    `_is_real_contest` must then refuse it. The value-proof unit tests above
    monkeypatch `_scan_outcomes`, so they can't catch a regression in the
    distinct-count COMPUTATION itself — this seeds a real same-family chain
    outcome and walks the genuine scan path.
    """
    import trinity_local.state_paths as sp
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.council_runtime import save_council_outcome

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    # state_paths caches trinity_home(); clear any memoization defensively.
    if hasattr(sp, "_HOME_CACHE"):
        monkeypatch.setattr(sp, "_HOME_CACHE", None, raising=False)

    body = "x" * 250  # comfortably substantive
    same_family = CouncilOutcome(
        council_run_id="council_samefamilychain",
        bundle_id="b1",
        task_cluster_id="t1",
        primary_provider="codex",          # user's default — differs from the lone family
        winner_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", session_id=None,
                                output_text=body, metadata={"chain_step_index": i})
            for i in range(3)
        ],
        synthesis_output="syn",
        routing_label=CouncilRoutingLabel(winner="claude", task_type="strategy"),
        mode="chain",
        metadata={"mode": "chain", "task_type": "strategy"},
    )
    sp.council_outcomes_dir().mkdir(parents=True, exist_ok=True)
    save_council_outcome(same_family)

    records, _clean = pr._scan_outcomes()
    assert len(records) == 1, records
    rec = records[0]
    assert rec["substantive_members"] == 3, rec
    assert rec.get("distinct_substantive_providers") == 1, (
        "a claude·claude·claude chain has THREE substantive members but ONE "
        f"distinct voice — _scan_outcomes must record that: {rec}"
    )
    assert pr._is_real_contest(rec) is False, (
        "a same-family chain is NOT a cross-provider contest — _is_real_contest "
        f"must refuse it on the distinct-voice clause: {rec}"
    )


def _records(*triples):
    # (chairman_winner, primary_provider) pairs → scan-record dicts.
    return [
        {"chairman_winner": w, "winner_provider": w, "primary_provider": p}
        for w, p in triples
    ]


def test_changed_pick_and_split(monkeypatch):
    # 3 of 4 councils picked a non-default model; default is always claude.
    recs = _records(
        ("codex", "claude"),
        ("antigravity", "claude"),
        ("claude", "claude"),       # default won — NOT a changed pick
        ("codex", "claude"),
    ) * 5  # 20 councils, clears the n>=10 threshold
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    vp = pr.council_value_proof()
    assert vp["ready"] is True
    assert vp["n"] == 20
    assert vp["comparable"] == 20
    # 3 of every 4 changed → 75%
    assert vp["changed_pct"] == 75
    # codex 10/20=50%, claude 5/20=25%, antigravity 5/20=25%
    assert vp["win_split"]["codex"]["pct"] == 50
    assert vp["win_split"]["claude"]["count"] == 5


def test_value_proof_refuses_same_family_chain_as_a_changed_pick(monkeypatch):
    """SAME-FAMILY INFLATION guard (the value-proof sibling of 00f37adc).

    A chain council `sequence=["claude","claude","claude"]` is NOT deduped by
    `mode="chain"` (it legitimately revisits a provider), so it lands on disk
    with 3 substantive member_results but ONE distinct voice. Before the fix,
    `_is_real_contest` gated only on `substantive_members >= 2`, so every such
    council passed as a "real contest" — and since the lone family (claude)
    differs from the user's default (codex), the value-proof counted it as
    "the chairman picked a DIFFERENT model than your default" and the per-lab
    win split tallied three identical claude voices as a Claude win. That's a
    FABRICATED painkiller stat on the flagship, screenshot-able home surface:
    "Trinity's chairman picked a different model 100% of the time" off a ledger
    that never ran a single cross-provider contest.

    The share card / review page / recent-councils rail already suppress the
    same-family contest on the DISTINCT-voice gate (00f37adc); this is the
    AGGREGATE value-proof that was left on the raw count. The fix records
    `distinct_substantive_providers` in `_scan_outcomes` and requires it to be
    >= 2 in `_is_real_contest`, so same-family councils drop out of the headline
    population entirely (card self-hides) rather than greening a non-contest.

    Discriminating fixture: 12 same-family chain councils (distinct=1) — clears
    the substantive_members>=2 floor AND the n>=10 volume floor, so ONLY the
    distinct-voice clause can refuse them.
    """
    recs = [
        {"chairman_winner": "claude", "winner_provider": "claude",
         "primary_provider": "codex", "substantive_members": 3,
         "distinct_substantive_providers": 1}
        for _ in range(12)
    ]
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    vp = pr.council_value_proof()
    assert vp["ready"] is False, (
        "FABRICATED PAINKILLER: 12 all-claude chain councils (one distinct voice "
        "each) were counted as cross-provider contests — the value-proof rendered "
        f"'chairman picked a DIFFERENT model {vp.get('changed_pct')}% of the time' "
        "with a Claude win split, off a ledger with NO real cross-provider contest. "
        f"_is_real_contest must require >= 2 DISTINCT voices, not >= 2 members. {vp}"
    )
    # And the same-family councils must be EXCLUDED from the contest population,
    # not merely demoted below the changed-pct floor (which a false n would let
    # back in once a few real contests are added).
    assert vp.get("n", 0) == 0, (
        f"every same-family council must drop out of the real-contest count: {vp}"
    )


def test_value_proof_still_counts_real_cross_provider_contests(monkeypatch):
    """Positive control for the distinct-voice gate: a genuine claude-won
    cross-provider contest (3 distinct voices) is STILL a real contest and a
    changed pick when the user's default is codex. Guards against the fix
    over-narrowing into "nothing is ever a contest"."""
    recs = [
        {"chairman_winner": "claude", "winner_provider": "claude",
         "primary_provider": "codex", "substantive_members": 3,
         "distinct_substantive_providers": 3}
        for _ in range(12)
    ]
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    vp = pr.council_value_proof()
    assert vp["ready"] is True, f"a real 3-voice contest must still count: {vp}"
    assert vp["changed_pct"] == 100, vp
    assert vp["win_split"]["claude"]["count"] == 12, vp


def test_value_proof_legacy_records_without_distinct_field_stay_real(monkeypatch):
    """Back-compat: records predating `distinct_substantive_providers` (the
    existing on-disk ledger) must default to "assume real" — the distinct gate
    must not retro-disqualify councils whose member providers were never
    recorded into the scan record."""
    recs = [
        {"chairman_winner": "claude", "winner_provider": "claude",
         "primary_provider": "codex", "substantive_members": 2}
        for _ in range(12)
    ]
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    vp = pr.council_value_proof()
    assert vp["ready"] is True, f"legacy records must not be retro-dropped: {vp}"
    assert vp["changed_pct"] == 100, vp


def test_win_split_shares_the_displayed_council_denominator(monkeypatch):
    """CROSS-PATH VALUE DIVERGENCE guard: the value-proof card paints ONE council
    count — "Across your {comparable} councils … wins: Claude X% · GPT Y%" — so the
    win-split MUST be tallied over the SAME `comparable` population the count rests
    on, not over the larger `n` (real_contests, which also counts contests with no
    recorded default).

    The bug this bites: the win tally used to count EVERY real contest with a winner
    (denominator `n`), while the card displays `comparable`. On a ledger where some
    real contests lack a recorded default (`comparable < n`), the card read
    "Across your 12 councils … Claude (count 18)" — the win counts summed to 18 while
    the headline said 12. The same underlying value, two code paths, two denominators.

    Discriminating fixture (comparable=12 < n=18): 12 contests with a known default
    (claude×6, codex×4 — all changed picks since default=claude, plus the 6 below),
    and 6 real contests with NO recorded default (counted in n, excluded from
    comparable). A wrong denominator makes the win counts NOT sum to the displayed
    council count.
    """
    recs = (
        # comparable contests (default recorded) — winner != default so they count
        # as changed picks, clearing the changed-pct/changed-count floors:
        _records(*([("claude", "codex")] * 6))   # default codex, winner claude
        + _records(*([("antigravity", "codex")] * 4))  # default codex, winner gemini
        + _records(*([("codex", "claude")] * 2))  # default claude, winner codex
        # real contests with NO recorded default — in `n`, excluded from `comparable`.
        # (primary_provider empty → normalize_provider_slug → falsy → not comparable.)
        + [{"chairman_winner": "claude", "winner_provider": "claude",
            "primary_provider": "", "substantive_members": 2} for _ in range(6)]
    )
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    vp = pr.council_value_proof()
    assert vp["ready"] is True, f"the discriminating ledger must clear the value floor: {vp}"
    assert vp["comparable"] == 12, f"comparable must be the default-recorded count: {vp}"
    assert vp["n"] == 18, f"n must count all 18 real contests: {vp}"
    # THE INVARIANT: every win-split count is drawn from the comparable population,
    # so they sum to the DISPLAYED council count, not to n. A denominator-`n` tally
    # sums to 18 (the 6 no-default contests leak in) — the divergence this guards.
    total_win_count = sum(d["count"] for d in vp["win_split"].values())
    assert total_win_count == vp["comparable"], (
        "CROSS-PATH DIVERGENCE: the value-proof win-split counts must sum to the "
        f"DISPLAYED council count (comparable={vp['comparable']}), but summed to "
        f"{total_win_count} — the win tally leaked contests with no recorded default "
        f"(it counted over n={vp['n']}). The card would read 'Across your "
        f"{vp['comparable']} councils … wins: …(count {total_win_count})' — the same "
        f"value rendered with two denominators. win_split={vp['win_split']}"
    )
    # Percentages must also reconcile against comparable (not n): claude 6/12=50,
    # antigravity 4/12=33, codex 2/12=17. Over n=18 they'd read 33/22/11.
    assert vp["win_split"]["claude"]["pct"] == 50, vp["win_split"]
    assert vp["win_split"]["antigravity"]["pct"] == 33, vp["win_split"]
    assert vp["win_split"]["codex"]["pct"] == 17, vp["win_split"]
    assert sum(d["pct"] for d in vp["win_split"].values()) == 100, (
        f"win-split percentages must sum to ~100 over comparable: {vp['win_split']}"
    )


def test_thin_ledger_not_ready(monkeypatch):
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(("codex", "claude")), True))
    vp = pr.council_value_proof()
    assert vp["ready"] is False
    assert vp["n"] == 1


def test_provider_names_canonicalized_at_boundary(monkeypatch):
    # Web-capture brand names must fold into the canonical slugs so the split
    # is per-LAB, not chatgpt-vs-codex double-counted (the v1.7.62 bug class).
    recs = _records(
        ("chatgpt", "claude"),     # → codex
        ("gpt", "claude"),         # → codex
        ("claude_ai", "codex"),    # winner→claude, default→codex (changed)
        ("gemini", "claude"),      # → antigravity
    ) * 5
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    vp = pr.council_value_proof()
    assert set(vp["win_split"]) <= {"codex", "claude", "antigravity"}, (
        "brand names must canonicalize; got " + repr(list(vp["win_split"]))
    )
    assert vp["win_split"]["codex"]["count"] == 10  # chatgpt + gpt


def test_substantive_output_completeness_heuristic():
    # #249: a flat 200-char floor misread Gemini's terse-but-complete answers.
    f = pr._is_substantive_output
    # complete concise answers (real, just terse) → substantive
    assert f("Unfortunately I can't search routes yet, but here's a 17-minute walk to the park (directions).")
    assert f("That's a good approach, but I'd phrase it to put the liability on him.")
    # truncated colon-opener (body never arrived) → NOT substantive even if long-ish
    assert not f("Here are some Indian stores near you that offer keto options:")
    # empty / echo / one-liner → not substantive
    assert not f("OK")
    assert not f("")
    # a long answer without terminal punct (code/table) is still substantive
    assert f("x" * 250)


def test_solo_councils_excluded_from_proof(monkeypatch):
    # A council where only 1 member answered substantively is NOT a real
    # contest — its winner won by default. The proof must exclude it so the
    # number measures answer quality, not dispatch reliability.
    real = [
        {"chairman_winner": "codex", "winner_provider": "codex",
         "primary_provider": "claude", "substantive_members": 2},
    ] * 12
    solo = [
        {"chairman_winner": "claude", "winner_provider": "claude",
         "primary_provider": "claude", "substantive_members": 1},
    ] * 40
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (real + solo, True))
    vp = pr.council_value_proof()
    assert vp["ready"] is True
    assert vp["total"] == 52          # all councils counted in total
    assert vp["real_contests"] == 12  # but only real contests in the headline
    assert vp["n"] == 12
    # all 12 real contests changed the pick (codex winner, claude default)
    assert vp["changed_pct"] == 100


def _wedge_records(family, winner, n, *, members=2):
    return [
        {"chairman_winner": winner, "winner_provider": winner,
         "primary_provider": "claude", "substantive_members": members,
         "routing_label": {"task_type": f"{family}_recommendation"}}
        for _ in range(n)
    ]


def test_category_wedge_names_confident_leaders(monkeypatch):
    # product → codex by a wide margin (clears volume + margin floors);
    # 'market' is a tie (no leader); 'rare' is below the volume floor.
    recs = (
        _wedge_records("product", "codex", 14)
        + _wedge_records("product", "claude", 2)        # product margin = 12
        + _wedge_records("market", "codex", 5)
        + _wedge_records("market", "claude", 5)          # tie → excluded
        + _wedge_records("rare", "claude", 3)            # n=3 < floor → excluded
    )
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    wedge = pr.council_category_wedge()
    families = {w["family"]: w["leader"] for w in wedge}
    assert families == {"product": "codex"}, f"expected only product→codex, got {families}"


def test_category_wedge_excludes_solo_councils(monkeypatch):
    # Solo councils (1 substantive member) must not feed the wedge either.
    recs = _wedge_records("product", "codex", 20, members=1)
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    assert pr.council_category_wedge() == []


def test_launchpad_helper_brands_and_hides(monkeypatch):
    from trinity_local import launchpad_data as ld

    # Ready → brand-mapped wins.
    recs = _records(("codex", "claude"), ("claude", "claude")) * 6
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (recs, True))
    card = ld._council_value_for_launchpad()
    assert card is not None
    labels = [w["label"] for w in card["wins"]]
    assert "GPT" in labels and "Claude" in labels
    assert "codex" not in labels  # slugs never leak to the UI

    # Thin ledger → None so the card self-hides.
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: ([], True))
    assert ld._council_value_for_launchpad() is None
