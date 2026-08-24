"""Effort gets the same provenance ladder the model got, for the same reason.

The model ladder (`model_provenance`) exists because a window of Flash councils
filed under 3.1 Pro — a config label drifted from what ran. Effort had the
identical defect twice on record (the 2026-07-03 args-override incident on
`_effective_effort`, and the founder's feedback doc v3 reporting a council that
recorded "high" and ran xhigh) and no ladder.

The honest classification is per-provider, because enforcement differs:
claude and codex have their effort ENFORCED by argv at dispatch (pinned);
antigravity's is a config string the agy CLI never reads (configured) — the
user's /model selection inside agy decides, and the label can drift silently.
"""
from __future__ import annotations

from trinity_local.providers import ProviderConfig, effort_provenance


def _cfg(name, effort=None, args=None):
    return ProviderConfig(name=name, type="cli", enabled=True, label=name,
                          task_types=[], command=[name], effort=effort,
                          args=args or [])


class TestTheLadder:
    def test_claude_effort_is_pinned_because_argv_enforces_it(self):
        assert effort_provenance(_cfg("claude", effort="high")) == "pinned"

    def test_codex_effort_is_pinned(self):
        assert effort_provenance(_cfg("codex", effort="xhigh")) == "pinned"

    def test_codex_args_override_is_pinned_and_wins(self):
        c = _cfg("codex", effort="high", args=["-c", 'model_reasoning_effort="xhigh"'])
        assert effort_provenance(c) == "pinned"

    def test_antigravity_effort_is_only_configured(self):
        """agy exposes no effort flag; nothing enforces the label at dispatch."""
        assert effort_provenance(_cfg("antigravity", effort="high")) == "configured"

    def test_no_effort_is_unknown_not_a_confident_string(self):
        assert effort_provenance(_cfg("claude")) == "unknown"
        assert effort_provenance(None) == "unknown"


class TestItIsRecordedPerMember:
    def test_council_runner_stamps_effort_source(self):
        import pathlib

        src = (pathlib.Path(__file__).resolve().parent.parent / "src" /
               "trinity_local" / "council_runner.py").read_text()
        assert '"effort_source": _effort_source(' in src, (
            "member metadata records effort with no provenance — the exact gap "
            "the model_source stamping closed for model identity")
