"""Canonical model identity — the #239 triple as a first-class primitive
(2026-07-14). The founder's fidelity requirement: model x size(tier) x effort.
Values are hand-derived from real slugs across the three families; the dash-
form version parse is the bug an earlier probe hit (opus-4-8 -> 4.8)."""
from __future__ import annotations

import pytest

from trinity_local.model_identity import UNKNOWN, parse_identity


class TestParseIdentity:
    @pytest.mark.parametrize("model,effort,expected", [
        ("claude-opus-4-8", "xhigh", ("claude", "opus", "4.8", "xhigh")),
        ("claude-opus-4-7", None, ("claude", "opus", "4.7", UNKNOWN)),
        ("claude-sonnet-4-6", "medium", ("claude", "sonnet", "4.6", "medium")),
        ("claude-fable-5", "max", ("claude", "fable", "5", "max")),   # single-int version
        ("gpt-5.6-sol", "xhigh", ("openai", "flagship", "5.6", "xhigh")),
        ("gpt-5.3-codex", None, ("openai", "codex", "5.3", UNKNOWN)),
        ("gpt-5.4-mini", "low", ("openai", "mini", "5.4", "low")),
        ("Gemini 3.1 Pro (high)", None, ("google", "pro", "3.1", "high")),  # effort baked in
        ("Gemini 3.1 Pro", "high", ("google", "pro", "3.1", "high")),        # effort explicit
        ("qwen3.6:35b-a3b", None, ("local", "qwen3.6", "3.6", UNKNOWN)),
        ("something-weird", None, (UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN)),
    ])
    def test_decomposes_the_triple(self, model, effort, expected):
        i = parse_identity(model, effort)
        assert (i.family, i.tier, i.version, i.effort) == expected

    def test_dash_version_form_is_parsed(self):
        # the exact bug: 4-8 must become 4.8, not stay unknown
        assert parse_identity("claude-opus-4-8").version == "4.8"

    def test_explicit_effort_beats_baked(self):
        # an explicit arg wins over a string-baked level
        assert parse_identity("Gemini 3.1 Pro (high)", "xhigh").effort == "xhigh"

    def test_projection_at_requested_fidelity(self):
        i = parse_identity("claude-opus-4-8", "xhigh")
        assert i.project("family") == ("claude",)
        assert i.project("family", "tier") == ("claude", "opus")
        assert i.project("family", "tier", "effort") == ("claude", "opus", "xhigh")

    def test_is_full_requires_every_leg(self):
        assert parse_identity("claude-opus-4-8", "xhigh").is_full
        assert not parse_identity("claude-opus-4-8").is_full  # effort unknown
        assert not parse_identity("claude-opus").is_full       # version unknown

    def test_never_raises_on_garbage(self):
        for bad in (None, "", "   ", 12345 and "12345", "()"):
            i = parse_identity(bad)
            assert i.effort == UNKNOWN
