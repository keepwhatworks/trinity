"""A ledger refresh must cost one call per NEW claim, not one per claim in the corpus.

WHY THIS EXISTS. `build_ledger` re-resolved every cross-provider disagreement on
every run. Measured 2026-07-25: 300 disagreements on disk, 286 of them billable, to
add the 39 that were genuinely new. That is why the ledger sat six days stale — the
only way to record today's councils was to pay for every prior day again, so nobody
ran it, so the one behaviour-validated layer in the product silently went out of date.

The rule these guards pin:
  * a SETTLED verdict (followed/contradicted) is carried forward, never re-paid;
  * an `unresolved` verdict is retried ONLY while its evidence window is still open —
    `assemble_evidence` reads the 14 days after the council, so once that window
    closes no new prompt can enter it and the verdict cannot change;
  * `force=True` re-resolves everything, because carried-forward verdicts were
    produced by the OLD prompt and must not be trusted across an instrument change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trinity_local.disagreement_ledger import (
    WINDOW_DAYS,
    DisagreementPattern,
    _needs_resolve,
)


def _pat(cid: str, days_ago: float) -> DisagreementPattern:
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return DisagreementPattern(
        claim_id=cid, council_id="cn1", at=at, claim="c", why_matters="w",
        providers_for=["claude"], providers_against=["codex"],
        chairman_winner="claude", task_excerpt="t",
    )


NOW = datetime.now(timezone.utc)


class TestSettledIsNeverRepaid:
    @pytest.mark.parametrize("verdict", ["followed", "contradicted"])
    def test_decided_claims_are_carried_forward(self, verdict):
        """The expensive half of the bug: 119 settled verdicts were re-bought every run."""
        p = _pat("c1", days_ago=1)  # window WIDE open — still must not re-resolve
        assert _needs_resolve(p, {"c1": (verdict, "q")}, now=NOW) is False, (
            "a settled verdict must be carried forward even when its evidence window "
            "is open; resolution is deterministic given (claim, evidence)"
        )

    def test_a_claim_never_seen_is_always_resolved(self):
        assert _needs_resolve(_pat("new", days_ago=1), {}, now=NOW) is True
        assert _needs_resolve(_pat("new", days_ago=999), {}, now=NOW) is True


class TestUnresolvedRetryIsWindowBounded:
    def test_unresolved_inside_the_window_is_retried(self):
        """Evidence can still accrue, so the verdict can still change."""
        p = _pat("c2", days_ago=WINDOW_DAYS - 2)
        assert _needs_resolve(p, {"c2": ("unresolved", "")}, now=NOW) is True

    def test_unresolved_past_the_window_is_NOT_retried(self):
        """The window is fixed relative to the council date. Once closed, no new
        prompt can enter it, so re-asking buys a guaranteed-identical answer.

        This is the guard that keeps the 142 unresolved rows from re-billing forever.
        """
        p = _pat("c3", days_ago=WINDOW_DAYS + 5)
        assert _needs_resolve(p, {"c3": ("unresolved", "")}, now=NOW) is False

    def test_the_boundary_is_the_window_edge(self):
        """Mutation-proof for the comparison itself: just inside retries, just
        outside does not. A guard that only tested day 1 vs day 999 would survive
        swapping the operator or the constant."""
        inside = _pat("in", days_ago=WINDOW_DAYS - 0.5)
        outside = _pat("out", days_ago=WINDOW_DAYS + 0.5)
        assert _needs_resolve(inside, {"in": ("unresolved", "")}, now=NOW) is True
        assert _needs_resolve(outside, {"out": ("unresolved", "")}, now=NOW) is False

    def test_an_undatable_claim_does_not_retry_forever(self):
        """No timestamp means the window can never be shown to be open. Retrying
        every build would re-bill it on every run for eternity."""
        p = DisagreementPattern(
            claim_id="c4", council_id="cn1", at="", claim="c", why_matters="w",
            providers_for=["claude"], providers_against=["codex"],
            chairman_winner="claude", task_excerpt="t",
        )
        assert _needs_resolve(p, {"c4": ("unresolved", "")}, now=NOW) is False


class TestForceOverride:
    def test_force_path_ignores_prior_entirely(self):
        """`force=True` clears `prior` in build_ledger, so every claim looks new.
        Pinned at the unit that decides it: an empty prior always resolves."""
        for verdict in ("followed", "contradicted", "unresolved"):
            p = _pat("c5", days_ago=999)
            assert _needs_resolve(p, {}, now=NOW) is True, (
                f"with prior cleared (force), a {verdict} claim must re-resolve"
            )


class TestCostShape:
    def test_a_steady_state_refresh_costs_nothing(self):
        """The property that makes the ledger cheap to keep current: with every
        claim settled and every window closed, a refresh bills zero calls."""
        prior = {f"c{i}": ("followed", "q") for i in range(50)}
        pats = [_pat(f"c{i}", days_ago=WINDOW_DAYS + 10) for i in range(50)]
        assert sum(_needs_resolve(p, prior, now=NOW) for p in pats) == 0

    def test_only_the_new_and_the_still_open_are_billed(self):
        prior = {
            "settled": ("followed", "q"),
            "stale_unresolved": ("unresolved", ""),
        }
        pats = [
            _pat("settled", days_ago=1),                     # carried
            _pat("stale_unresolved", days_ago=WINDOW_DAYS + 3),  # window closed
            _pat("open_unresolved", days_ago=1),             # not in prior -> new
            _pat("brand_new", days_ago=0.1),                 # new
        ]
        billed = [p.claim_id for p in pats if _needs_resolve(p, prior, now=NOW)]
        assert billed == ["open_unresolved", "brand_new"]
