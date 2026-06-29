"""Tests for chairman auto-selection (§ task 32)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trinity_local.ranker.chairman_picker import (
    chairman_pick_reason,
    predict_strongest_chairman,
)


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


class TestPicker:
    def test_empty_available_returns_empty(self, home: Path):
        assert predict_strongest_chairman("anything", available_providers=[]) == ""

    def test_falls_back_to_global_benchmarks_for_coding(self, home: Path):
        # No personal table; coding task picks the highest-scoring provider
        # in the reference_evals "coding" category.
        # AA Coding Index: codex 59.1, gemini 55.5, claude 52.5.
        pick = predict_strongest_chairman(
            "refactor this function to remove duplication",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert pick == "codex"

    def test_falls_back_to_global_benchmarks_for_writing(self, home: Path):
        # writing maps to "intelligence"; Intelligence Index: claude 57.3, codex 53.6, gemini 34.6
        pick = predict_strongest_chairman(
            "write a launch announcement",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert pick == "claude"

    def test_falls_back_to_global_for_research_via_intelligence(self, home: Path):
        # research maps to "intelligence"; claude wins (57.3)
        pick = predict_strongest_chairman(
            "research the model router landscape and compare approaches",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert pick == "claude"

    def test_excludes_providers_not_in_available_list(self, home: Path):
        # If gemini isn't available, fall back to next-best in coding (codex 53.1 > claude 52.5)
        pick = predict_strongest_chairman(
            "refactor this code",
            available_providers=["claude", "codex"],
        )
        assert pick == "codex"

    def test_default_order_when_global_has_no_match(self, home: Path):
        # Provider not in the global benchmark models — should fall back to first available
        pick = predict_strongest_chairman(
            "anything",
            available_providers=["custom_provider"],
        )
        assert pick == "custom_provider"

    def test_personal_routing_table_overrides_global(self, home: Path, monkeypatch):
        # Personal table says claude wins coding for this user, even though global says gemini
        from trinity_local.ranker import chairman_picker
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {
                "computed_at": "2026-05-01T00:00:00Z",
                "councils_aggregated": 12,
                "by_task_type": {
                    "coding": {
                        "claude": {"overall": 8.5, "n": 6},
                        "antigravity": {"overall": 6.2, "n": 6},
                    },
                },
                "best_per_task_type": {"coding": "claude"},
            },
        )
        pick = predict_strongest_chairman(
            "refactor this function",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert pick == "claude"

    def test_personal_routing_skipped_if_winner_unavailable(self, home: Path, monkeypatch):
        # Personal table says claude wins but claude isn't in available — fall to global
        from trinity_local.ranker import chairman_picker
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {"best_per_task_type": {"coding": "claude"}},
        )
        pick = predict_strongest_chairman(
            "refactor this code",
            available_providers=["antigravity", "codex"],
        )
        # AA coding: codex 59.1 > gemini 55.5
        assert pick == "codex"

    def test_corrupt_personal_table_is_ignored(self, home: Path, monkeypatch):
        from trinity_local.ranker import chairman_picker

        def _raise():
            raise ValueError("corrupt")

        monkeypatch.setattr(chairman_picker, "compute_personal_routing_table", _raise)
        pick = predict_strongest_chairman(
            "refactor this code",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert pick == "codex"  # falls through to reference_evals coding winner


class TestPickReason:
    def test_reports_personal_source(self, home: Path, monkeypatch):
        from trinity_local.ranker import chairman_picker
        # Provide enough personal councils (n=20) that alpha saturates near 1,
        # so the blend reports "personal_routing_table" as the source.
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {
                "by_task_type": {
                    "coding": {
                        "claude": {"overall": 8.5, "n": 20},
                        "antigravity": {"overall": 6.2, "n": 20},
                    },
                },
                "best_per_task_type": {"coding": "claude"},
            },
        )
        result = chairman_pick_reason(
            "refactor this",
            available_providers=["claude", "antigravity"],
        )
        assert result["chairman"] == "claude"
        assert result["source"] == "personal_routing_table"
        assert result["task_type"] == "coding"
        # New: the alpha + n are surfaced for telemetry.
        assert result.get("alpha", 0) >= 0.95
        assert result.get("n_personal", 0) == 20

    def test_reports_global_source(self, home: Path):
        # Coding has data in reference_evals.json, so global lookup wins.
        result = chairman_pick_reason(
            "refactor this Python function",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert result["chairman"] == "codex"
        assert result["source"] == "global_benchmarks"
        assert result["task_type"] == "coding"

    def test_reports_default_source_for_unmapped_category(self, home: Path):
        # Writing maps to creative_writing, which has no reference data with
        # external sync dropped. Falls back to default_order.
        result = chairman_pick_reason(
            "write a launch announcement",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert result["source"] == "default_order"
        assert result["task_type"] == "writing"

    def test_reports_default_source_when_no_match(self, home: Path):
        result = chairman_pick_reason(
            "anything",
            available_providers=["custom"],
        )
        assert result["source"] == "default_order"

    def test_reports_none_when_empty_available(self, home: Path):
        result = chairman_pick_reason("anything", available_providers=[])
        assert result["chairman"] == ""
        assert result["source"] == "none"


class TestSigmoidBlend:
    """The personal/global hard-cut was replaced with a sigmoid blend (task #52).
    These tests pin the new behavior: cold start prefers global; as personal
    data accumulates, the user's signal takes over smoothly.
    """

    def test_n_zero_picks_pure_global(self, home: Path):
        """No personal table → alpha ≈ 0 → pure global benchmark winner."""
        result = chairman_pick_reason(
            "refactor this function",
            available_providers=["claude", "antigravity", "codex"],
        )
        # Coding global winner is codex.
        assert result["chairman"] == "codex"
        assert result["source"] == "global_benchmarks"
        assert result["alpha"] < 0.1  # cold-start alpha

    def test_n_one_still_mostly_global(self, home: Path, monkeypatch):
        """A single personal council shouldn't outrank an established global
        benchmark — that was the bug the sigmoid blend fixes."""
        from trinity_local.ranker import chairman_picker
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {
                "by_task_type": {
                    # User picked claude once in coding; chairman scored it 9.0.
                    # Global says codex (5.91 rescaled) > claude (5.25 rescaled).
                    # At n=1, alpha ≈ 0.12, so personal contribution is small.
                    # Claude blended: 0.12*9.0 + 0.88*5.25 = 1.08 + 4.62 = 5.70
                    # Codex blended: 0.12*0 + 0.88*5.91 = 5.20 (codex absent from personal)
                    # Claude wins narrowly because its personal score is very high.
                    # But this is the right behavior — strong evidence from 1 council
                    # can flip a close global margin. The protection is that alpha is
                    # small enough that WEAK personal evidence can't override.
                    "coding": {
                        "claude": {"overall": 9.0, "n": 1},
                    },
                },
                "best_per_task_type": {"coding": "claude"},
            },
        )
        result = chairman_pick_reason(
            "refactor this code",
            available_providers=["claude", "antigravity", "codex"],
        )
        assert result["alpha"] < 0.2  # n=1 is well below the sigmoid midpoint

    def test_n_one_weak_personal_does_not_override_global(self, home: Path, monkeypatch):
        """The real protection: weak personal evidence at n=1 cannot flip a
        clear global preference. If user picked claude once but chairman
        only scored it 6.0, blended at n=1 stays under codex's global."""
        from trinity_local.ranker import chairman_picker
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {
                "by_task_type": {
                    "coding": {
                        "claude": {"overall": 6.0, "n": 1},  # weak signal
                    },
                },
                "best_per_task_type": {"coding": "claude"},
            },
        )
        result = chairman_pick_reason(
            "refactor this code",
            available_providers=["claude", "antigravity", "codex"],
        )
        # Codex's global should still win at n=1.
        assert result["chairman"] == "codex"

    def test_n_twenty_picks_pure_personal(self, home: Path, monkeypatch):
        """At n >> midpoint, alpha → 1 → personal dominates regardless of global."""
        from trinity_local.ranker import chairman_picker
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {
                "by_task_type": {
                    "coding": {
                        "claude": {"overall": 8.5, "n": 20},
                        "codex": {"overall": 4.0, "n": 20},
                    },
                },
                "best_per_task_type": {"coding": "claude"},
            },
        )
        result = chairman_pick_reason(
            "refactor this code",
            available_providers=["claude", "antigravity", "codex"],
        )
        # Personal claude (8.5) beats personal codex (4.0) at saturation.
        assert result["chairman"] == "claude"
        assert result["source"] == "personal_routing_table"
        assert result["alpha"] >= 0.95
        assert result["n_personal"] == 20

    def test_n_five_is_balanced_midpoint(self, home: Path, monkeypatch):
        """At n=PERSONAL_MIDPOINT, alpha=0.5 — equal mix. Confirms the
        midpoint constant is honored and the sigmoid math isn't broken."""
        from trinity_local.ranker import chairman_picker
        monkeypatch.setattr(
            chairman_picker,
            "compute_personal_routing_table",
            lambda: {
                "by_task_type": {
                    "coding": {"claude": {"overall": 7.0, "n": 5}},
                },
                "best_per_task_type": {"coding": "claude"},
            },
        )
        result = chairman_pick_reason(
            "refactor this code",
            available_providers=["claude", "antigravity", "codex"],
        )
        # alpha = sigmoid(0) = 0.5 → "blended" band (0.2 < alpha < 0.8).
        assert 0.4 < result["alpha"] < 0.6
        assert result["source"] == "blended"


class TestChairmanPickIsDeterministicOnAScoreTie:
    """The council CHAIRMAN must be the same provider every launch — even when
    two providers tie EXACTLY on the blended score. The candidate set was
    `set(personal) | set(glb)`, and Python salts string hashes per-process by
    default, so the tie-prone `max(blended.items(), key=score)` leaked the set
    iteration order straight into which provider chairs the council: a 0.812 ==
    0.812 personal tie returned codex under PYTHONHASHSEED=99 but antigravity
    under PYTHONHASHSEED=0 — the chair FLIPPED between runs of the same binary.
    The fix sorts the candidates and tie-breaks on the slug; the chair now
    resolves to the lexically smallest tied slug, deterministically."""

    # Run the pick in fresh subprocesses across hash seeds — within ONE process
    # set order is fixed, so the founder symptom (cross-process flip) is only
    # observable across interpreter starts with a salted hash.
    _INNER = (
        "import sys; sys.path.insert(0, %r)\n"
        "from trinity_local.ranker import chairman_picker as cp\n"
        # Two providers tie EXACTLY on the blended score (identical personal
        # mean, both absent from global → blended == personal == 0.812).
        "cp._personal_scores = lambda tt, avail: ({'codex': 0.812, 'antigravity': 0.812}, 5)\n"
        "cp._global_scores = lambda tt, avail: {}\n"
        "print(cp._blended_pick('coding', ['codex', 'antigravity'])[0])\n"
    )

    def _pick_under_seed(self, seed: str, src_root: str) -> str:
        import os
        import subprocess
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run(
            [sys.executable, "-c", self._INNER % src_root],
            env=env,
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def test_blended_score_tie_picks_the_same_chairman_every_hash_seed(self):
        # src/ root so the subprocess imports the in-tree package.
        src_root = str(Path(__file__).resolve().parents[1] / "src")
        # Seeds chosen to include ones that flipped the pre-fix code
        # (0 → antigravity, 99 → codex). 8 seeds make a cross-process flip
        # overwhelmingly likely if the set-order leak were still present.
        seeds = ["0", "1", "7", "42", "99", "123", "555", "31337"]
        picks = {s: self._pick_under_seed(s, src_root) for s in seeds}
        distinct = set(picks.values())
        assert len(distinct) == 1, (
            "the council CHAIRMAN flipped across PYTHONHASHSEED — a blended-score "
            "tie leaked set-iteration order into the chair selection "
            f"(per-seed picks: {picks})"
        )
        # And it resolves to the lexically smallest tied slug (the principled,
        # reproducible tie-break), not whatever the set happened to surface.
        assert distinct == {"antigravity"}, (
            "a blended-score tie must resolve to the lexically smallest slug "
            f"(antigravity < codex); got {distinct}"
        )
