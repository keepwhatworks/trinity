"""personal_routing.aggregate_routing_table: MIN_BEST_SAMPLES sample guard.

Live trigger 2026-05-25: of 246 task_types in the user's routing table,
89% (219) had their winner declared from n=1 council. That's noise, not
signal — same anti-pattern the per-axis leader suppression fixed at the
display layer. This pins the data-layer guard: best_per_task_type only
includes task_types with ≥3 councils, the raw by_task_type / wins
data stays complete (chairman_picker reads those directly via
sigmoid blend, doesn't need best).
"""
from __future__ import annotations


def _council(task_type: str, winner: str) -> dict:
    """One synthetic council record in the shape aggregate_routing_table reads."""
    return {
        "task_type": task_type,
        "routing_label": {
            "task_type": task_type,
            "winner": winner,
            "provider_scores": {
                # Both providers see scores so by_task_type has data
                "claude": {"overall": 7.5},
                "codex": {"overall": 7.0},
            },
        },
    }


class TestMinSampleSuppression:
    def test_single_council_winner_excluded_from_best(self):
        from trinity_local.personal_routing import aggregate_routing_table
        # One council for "rare_task" → noise, should not declare winner
        result = aggregate_routing_table([_council("rare_task", "claude")])
        assert "rare_task" in result["by_task_type"], "raw data preserved"
        assert "rare_task" in result["wins_per_task_type"], "wins preserved"
        assert "rare_task" not in result["best_per_task_type"], (
            "best_per_task_type must not declare a winner from n=1 council"
        )

    def test_two_councils_winner_excluded(self):
        from trinity_local.personal_routing import aggregate_routing_table
        result = aggregate_routing_table([
            _council("nearly_rare", "claude"),
            _council("nearly_rare", "claude"),
        ])
        # n=2 still below MIN_BEST_SAMPLES=3
        assert "nearly_rare" not in result["best_per_task_type"]

    def test_three_councils_winner_included(self):
        from trinity_local.personal_routing import aggregate_routing_table
        result = aggregate_routing_table([
            _council("at_threshold", "claude"),
            _council("at_threshold", "claude"),
            _council("at_threshold", "claude"),
        ])
        # n=3 hits MIN_BEST_SAMPLES floor
        assert "at_threshold" in result["best_per_task_type"]
        assert result["best_per_task_type"]["at_threshold"] == "claude"

    def test_raw_data_preserved_even_for_excluded(self):
        """The chairman_picker sigmoid-blends from by_task_type/wins
        directly; it never reads best_per_task_type for routing. So
        the raw data must stay complete even when best omits."""
        from trinity_local.personal_routing import aggregate_routing_table
        result = aggregate_routing_table([
            _council("rare_a", "claude"),
            _council("rare_b", "codex"),
            _council("rare_c", "claude"),
        ])
        # 3 distinct task_types each with n=1 → best_per_task_type is empty
        assert len(result["best_per_task_type"]) == 0
        # But raw data has all 3
        assert len(result["by_task_type"]) == 3
        assert len(result["wins_per_task_type"]) == 3
        # Sample counts visible per provider
        assert result["by_task_type"]["rare_a"]["claude"]["n"] == 1

    def test_high_confidence_task_types_survive(self):
        """Confidence threshold is per-task_type, not global. A run with
        a mix of low-n and high-n task_types should pass through the
        high-n ones."""
        from trinity_local.personal_routing import aggregate_routing_table
        result = aggregate_routing_table([
            _council("low_n_kind", "claude"),  # n=1
            # high_n_kind seen 4 times → above threshold
            _council("high_n_kind", "claude"),
            _council("high_n_kind", "claude"),
            _council("high_n_kind", "codex"),
            _council("high_n_kind", "claude"),
        ])
        assert "low_n_kind" not in result["best_per_task_type"]
        assert "high_n_kind" in result["best_per_task_type"]
        # claude wins (3 of 4 councils)
        assert result["best_per_task_type"]["high_n_kind"] == "claude"

    def test_council_count_uses_winner_field_first(self):
        """The sample-count gate uses the wins dict when present (chairman
        winner field). This is the canonical signal — fall back to
        provider_summary.n only when winner is missing."""
        from trinity_local.personal_routing import aggregate_routing_table
        # Two councils that lack the winner field but have provider_scores
        # → both providers get n=1 in provider_summary; falls back to
        # summing provider counts.
        no_winner_councils = [
            {"task_type": "no_winner_kind",
             "routing_label": {
                 "task_type": "no_winner_kind",
                 "provider_scores": {
                     "claude": {"overall": 7.0},
                     "codex": {"overall": 6.0},
                 },
             }},
        ] * 3  # 3 copies = 3 councils
        result = aggregate_routing_table(no_winner_councils)
        # total_n = 3 (claude) + 3 (codex) = 6 across providers → ≥3
        assert "no_winner_kind" in result["best_per_task_type"]


class TestBestProviderDeterministicOnAWinAndScoreTie:
    """The launchpad / memory viewer render the best_per_task_type provider as
    a "Lean X · no clear pick" chip even when pick_is_tie is set — so the
    SPECIFIC slug is user-visible. wins_here is a plain dict whose order
    follows the council-scan order, and `overall` is rounded to 3dp, so two
    providers can tie EXACTLY on (wins, overall): a 2-2 split with identical
    mean scores. Without a stable slug tie-break, `max(wins_here.items(),
    key=(wins, overall))` returns whichever winner the scan surfaced first —
    so the displayed "Lean X" flips on council-file order (and adding ONE new
    hash-named council reorders the scan, flipping the chip). The fix
    tie-breaks on the slug → the lean resolves to the lexically smallest tied
    provider, the same way every render."""

    @staticmethod
    def _tie_council(winner: str) -> dict:
        # codex and antigravity end at identical mean overall (0.800) so the
        # tie is on (wins, overall) BOTH, not just wins.
        return {
            "task_type": "refactor",
            "routing_label": {
                "task_type": "refactor",
                "winner": winner,
                "provider_scores": {
                    "codex": {"overall": 0.8},
                    "antigravity": {"overall": 0.8},
                    "claude": {"overall": 0.5},
                },
            },
        }

    def test_win_and_score_tie_resolves_to_same_slug_under_both_scan_orders(self):
        from trinity_local.personal_routing import aggregate_routing_table
        # A perfect 2-2 tie between codex and antigravity at equal mean overall.
        codex_first = [
            self._tie_council("codex"),
            self._tie_council("codex"),
            self._tie_council("antigravity"),
            self._tie_council("antigravity"),
        ]
        anti_first = list(reversed(codex_first))

        best_cf = aggregate_routing_table(codex_first)["best_per_task_type"]["refactor"]
        best_af = aggregate_routing_table(anti_first)["best_per_task_type"]["refactor"]

        # Both orders must agree (deterministic) AND resolve to the lexically
        # smallest tied slug (antigravity < codex), not the first-scanned one.
        assert best_cf == best_af, (
            "the rendered 'Lean X' provider FLIPPED on council-scan order — a "
            "(wins, overall) tie leaked dict iteration order into the chip "
            f"(codex_first={best_cf!r} vs anti_first={best_af!r})"
        )
        assert best_cf == "antigravity", (
            "a (wins, overall) tie must resolve to the lexically smallest slug "
            f"(antigravity < codex); got {best_cf!r}"
        )
        # The win-count tie is still flagged so the chip stays demoted.
        assert aggregate_routing_table(codex_first)["pick_is_tie"].get("refactor") is True
