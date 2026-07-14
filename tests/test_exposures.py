"""Exposure lane (council 2026-07-14): honest denominators, no approval
inference. Mutation targets: drop the MIN_ANSWER_CHARS floor, drop slug
canonicalization, or count unattributed losses → each reds a test."""
from __future__ import annotations

import json

import pytest


def _seed(tmp_path, monkeypatch, nodes, acts):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    p = tmp_path / "prompts"
    p.mkdir(parents=True, exist_ok=True)
    (p / "prompt_nodes.jsonl").write_text(
        "\n".join(json.dumps(n) for n in nodes) + "\n", encoding="utf-8")
    me = tmp_path / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "preference_acts.jsonl").write_text(
        "\n".join(json.dumps(a) for a in acts) + "\n", encoding="utf-8")


LONG = "x" * 120  # a substantive assistant answer
SHORT = "ok."     # scaffolding — not an at-bat


class TestExposureLane:
    def test_rates_join_losses_to_canonical_families(self, tmp_path, monkeypatch):
        from trinity_local.me.exposures import provider_rejection_rates
        nodes = [
            {"id": "n1", "provider": "claude_ai", "preceding_assistant_text": LONG, "text": "next q"},
            {"id": "n2", "provider": "claude", "preceding_assistant_text": LONG, "text": "next q"},
            {"id": "n3", "provider": "gemini", "preceding_assistant_text": LONG, "text": "next q"},
            # scaffolding answer — must NOT count as an exposure
            {"id": "n4", "provider": "claude", "preceding_assistant_text": SHORT, "text": "next q"},
            # unattributed — must not count
            {"id": "n5", "provider": "", "preceding_assistant_text": LONG, "text": "next q"},
        ]
        acts = [
            {"id": "r_1", "trigger": "model_miss", "prompt_id": "n1",
             "privileged": "p", "sacrificed": "s", "kind": "REFRAME"},
            # non-model_miss must not count as a loss
            {"id": "d_1", "trigger": "self_expressed", "prompt_id": "n2",
             "privileged": "p", "sacrificed": "s", "kind": "satisfaction"},
        ]
        _seed(tmp_path, monkeypatch, nodes, acts)
        rates = {r["provider"]: r for r in provider_rejection_rates()}
        # claude_ai canonicalizes into the claude family: 2 exposures, 1 loss
        assert rates["claude"]["exposures"] == 2
        assert rates["claude"]["losses"] == 1
        assert rates["claude"]["rejection_rate"] == pytest.approx(0.5)
        # gemini canonicalizes to antigravity, zero losses
        assert "antigravity" in rates and rates["antigravity"]["losses"] == 0
        # CI is carried on every row
        lo, hi = rates["claude"]["ci"]
        assert 0.0 <= lo < 0.5 < hi <= 1.0

    def test_cowork_folds_into_the_claude_family(self, tmp_path, monkeypatch):
        from trinity_local.me.exposures import provider_exposures
        _seed(tmp_path, monkeypatch,
              [{"id": "n1", "provider": "cowork", "preceding_assistant_text": LONG, "text": "q"},
               {"id": "n2", "provider": "claude", "preceding_assistant_text": LONG, "text": "q"}],
              [])
        assert provider_exposures() == {"claude": 2}

    def test_short_answers_are_not_at_bats(self, tmp_path, monkeypatch):
        from trinity_local.me.exposures import provider_exposures
        _seed(tmp_path, monkeypatch,
              [{"id": "n1", "provider": "claude", "preceding_assistant_text": SHORT, "text": "q"}],
              [])
        assert provider_exposures() == {}

    def test_missing_state_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me.exposures import provider_rejection_rates
        assert provider_rejection_rates() == []
