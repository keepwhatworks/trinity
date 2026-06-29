"""Guard: the council value-proof's real-contest filter must never INFLATE the
public headline (#236 painkiller number on the launchpad).

Found 2026-06-02 by independently recomputing the stat on the real ledger: the
docstring claimed the real-contest filter "doesn't move the headline — 56% before
and after," but on the grown ledger it moves it 56% (all 562) -> 52% (475 real).
True direction: the filter is CONSERVATIVE — it removes walkover councils whose
"winner" won by default, which INFLATE the apparent "chairman changed the pick"
rate. The honest reframe is stronger than the stale "no-op" claim, but it also
means a future "this filter looks like a no-op, drop it" would silently push the
public number UP. This pins the direction so that can't happen unnoticed.

Monkeypatches `_scan_outcomes` (rather than synthesizing CouncilOutcome JSON,
which `_scan_outcomes` only counts when it carries a valid routing_label) so the
test exercises council_value_proof's actual filter (`_is_real_contest`) + the
inline changed_pct computation on controlled post-scan records.
"""
from __future__ import annotations

import trinity_local.personal_routing as pr
from trinity_local.council_schema import normalize_provider_slug


def _changed_pct(records) -> int:
    changed = comparable = 0
    for r in records:
        w = normalize_provider_slug(r.get("chairman_winner") or r.get("winner_provider") or "")
        d = normalize_provider_slug(r.get("primary_provider") or "")
        if w and d:
            comparable += 1
            if w != d:
                changed += 1
    return round(100 * changed / comparable) if comparable else 0


def test_real_contest_filter_never_inflates_headline(monkeypatch):
    # 12 REAL contests (substantive_members=2): half "changed", half not -> 50%
    real = (
        [{"chairman_winner": "codex", "primary_provider": "claude", "substantive_members": 2}] * 6
        + [{"chairman_winner": "claude", "primary_provider": "claude", "substantive_members": 2}] * 6
    )
    # 8 WALKOVERS (substantive_members=1), all "changed" -> they inflate the
    # unfiltered rate well above the real-contest rate
    walkovers = [
        {"chairman_winner": "codex", "primary_provider": "claude", "substantive_members": 1}
    ] * 8
    records = real + walkovers
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (records, True))

    before = _changed_pct(records)                    # no filter — walkover-inflated
    after = pr.council_value_proof()["changed_pct"]   # the public number (filtered)

    assert before == 70, before          # 14 changed / 20 comparable
    assert after == 50, after            # 6 changed / 12 real comparable
    # If the filter were dropped (the "no-op" misreading) after would equal
    # before (70) and this reds — the filter must STRICTLY lower an inflated rate.
    assert after < before, (
        "the real-contest filter must never INFLATE the public headline — "
        f"got after={after}% >= before={before}%, walkovers are leaking in"
    )


def test_thin_comparable_base_suppresses_headline_even_when_n_passes(monkeypatch):
    """Green-gate (principle #35): the `changed_pct` headline rests on `comparable`
    (real contests recording BOTH a chairman winner AND the user's default), but the
    readiness gate historically checked only `n` (all real contests). A ledger of
    councils launched WITHOUT a recorded primary (e.g. launchpad-form dispatches, or
    provider-imported councils) clears `n >= 10` while having almost no comparable
    pairs — so the painkiller % would be computed off a handful of councils and the
    card would still tout it. The gate must rest on the quantity the claim uses.

    16 real contests, but only 3 record a primary_provider → n=16 (clears the old
    n-gate) yet comparable=3 (< MIN). The value proof must report ready=False.
    Mutation: revert the `comparable < MIN` gate → ready flips True on a 3-council
    base → this reds."""
    # 3 comparable (winner + default), 13 with a winner but NO default recorded.
    records = (
        [{"chairman_winner": "codex", "primary_provider": "claude", "substantive_members": 2}] * 3
        + [{"chairman_winner": "claude", "substantive_members": 2}] * 13  # no primary_provider
    )
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (records, True))

    vp = pr.council_value_proof()
    assert len([r for r in records if r.get("substantive_members", 2) >= 2]) == 16  # n clears the old gate
    assert vp["ready"] is False, (
        "value proof reported ready on a thin comparable base — the painkiller % "
        f"would headline off {vp.get('comparable')} councils while n={vp.get('n')} "
        "cleared the gate (green-gate: gate the quantity the claim rests on)"
    )
    assert vp.get("comparable") == 3 and vp.get("n") == 16, vp
