"""TRINITY_EFFORT_ROTATION — the behavioural effort probe (amd_0060's path).

The one property that matters: the rotated config is a SINGLE source for both
dispatch and the identity stamp, so the recorded effort can never diverge from
the dispatched effort. A wrong stamp corrupts the ledger's effort slice worse
than no rotation at all (the 2026-07-03 args-vs-effort stamp bug is the
precedent).

Mutation targets: drop the env guard -> test_off_by_default; use a non-stable
hash -> test_deterministic_per_council; drop the name check ->
test_other_providers_untouched.
"""
from __future__ import annotations

from trinity_local.config import ProviderConfig
from trinity_local.providers import _effective_effort, rotated_effort_config


def _cfg(name="claude", effort="high", args=None):
    return ProviderConfig(
        name=name, type="cli", enabled=True, label=name,
        command=[name], args=args or [], task_types=set(),
        model="claude-opus-5", effort=effort,
    )


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("TRINITY_EFFORT_ROTATION", raising=False)
    cfg = _cfg()
    assert rotated_effort_config(cfg, "council_x") is cfg


def test_deterministic_per_council(monkeypatch):
    monkeypatch.setenv("TRINITY_EFFORT_ROTATION", "claude:high,xhigh")
    cfg = _cfg()
    a = rotated_effort_config(cfg, "council_aaaa").effort
    b = rotated_effort_config(cfg, "council_aaaa").effort
    assert a == b, "same council must always rotate to the same level"
    picks = {rotated_effort_config(cfg, f"council_{i}").effort for i in range(40)}
    assert picks == {"high", "xhigh"}, "both levels must occur across councils"


def test_stamp_equals_dispatch(monkeypatch):
    """The property the whole flag hangs on: _effective_effort of the rotated
    config IS what the runner stamps, and IS what the CLI dispatches."""
    monkeypatch.setenv("TRINITY_EFFORT_ROTATION", "claude:high,xhigh")
    cfg = rotated_effort_config(_cfg(effort="medium"), "council_zz")
    assert _effective_effort(cfg) == cfg.effort
    assert cfg.effort in ("high", "xhigh")


def test_other_providers_untouched(monkeypatch):
    monkeypatch.setenv("TRINITY_EFFORT_ROTATION", "claude:high,xhigh")
    cfg = _cfg(name="codex", effort="xhigh")
    assert rotated_effort_config(cfg, "council_x") is cfg


def test_args_override_stays_consistent(monkeypatch):
    """An explicit model_reasoning_effort in args wins at BOTH dispatch and
    stamp (same resolver), so rotation under an override is ineffective, never
    dishonest."""
    monkeypatch.setenv("TRINITY_EFFORT_ROTATION", "codex:high,xhigh")
    cfg = _cfg(name="codex", effort="high",
               args=["-c", 'model_reasoning_effort="medium"'])
    rotated = rotated_effort_config(cfg, "council_q")
    assert _effective_effort(rotated) == "medium"


def test_malformed_spec_never_breaks_dispatch(monkeypatch):
    for bad in ("claude", "claude:", "claude:high", ":high,xhigh", "::,,"):
        monkeypatch.setenv("TRINITY_EFFORT_ROTATION", bad)
        cfg = _cfg()
        out = rotated_effort_config(cfg, "council_x")
        assert out is cfg or out.effort == cfg.effort


def test_none_config_passes_through(monkeypatch):
    monkeypatch.setenv("TRINITY_EFFORT_ROTATION", "claude:high,xhigh")
    assert rotated_effort_config(None, "council_x") is None
