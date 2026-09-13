"""The recorded reasoning effort must match what the dispatch enforces.

The model stamp got its ladder after a window of Flash councils filed under
3.1 Pro. Effort has the same failure mode and the founder has hit it: a council
that recorded "high" and ran xhigh.

Checked here for each seat, against the real argv rather than against a list:

  claude / codex  a flag on the command line enforces the level.
  antigravity     agy exposes no effort flag, but Trinity now injects --model
                  and the level lives INSIDE the SKU (gemini-3.8-flash-high),
                  so argv pins it after all. The ladder read `configured` until
                  2026-09-11 — stale in exactly the way injects_model_flag was
                  stale for codex.

The case that was unguarded entirely: an effort FIELD disagreeing with the SKU
beside it. `effort="low"` on a `-high` model is not a weaker guarantee, it is a
row that records low for a dispatch that runs high.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trinity_local import providers as P
from trinity_local.config import ProviderConfig

_TYPE = {"claude": "cli", "codex": "codex", "antigravity": "cli"}
_MODEL = {"claude": "claude-opus-5", "codex": "gpt-6-astra",
          "antigravity": "gemini-3.8-flash-high"}
_EFFORT = {"claude": "xhigh", "codex": "medium", "antigravity": "high"}


def _cfg(name: str, **over) -> ProviderConfig:
    base = dict(
        name=name, type=_TYPE[name], enabled=True, label=name,
        command={"claude": ["claude", "-p"], "codex": ["codex", "exec"],
                 "antigravity": ["agy", "-p"]}[name],
        args=[], model=_MODEL[name], effort=_EFFORT[name], task_types={"general"})
    base.update(over)
    return ProviderConfig(**base)


def _argv(cfg, monkeypatch) -> list[str]:
    prov = P.make_provider(cfg)
    seen: list[list[str]] = []

    def _cap(self, command, cwd, **kw):
        seen.append(list(command))
        raise RuntimeError("captured")

    monkeypatch.setattr(type(prov), "_run_command", _cap, raising=True)
    try:
        prov.run("hello", Path(tempfile.mkdtemp()))
    except Exception:
        pass
    assert seen, "the provider never reached _run_command; the seam moved"
    return seen[0]


class TestTheLevelIsOnTheWire:
    @pytest.mark.parametrize("name", ["claude", "codex"])
    def test_a_flag_seat_puts_its_effort_on_the_command_line(self, name, monkeypatch):
        argv = " ".join(str(a) for a in _argv(_cfg(name), monkeypatch))
        assert _EFFORT[name] in argv, f"{name}: effort not on argv: {argv}"

    def test_antigravity_carries_its_level_in_the_model_sku(self, monkeypatch):
        """No flag, and that is correct — the level is the model."""
        argv = [str(a) for a in _argv(_cfg("antigravity"), monkeypatch)]
        assert not any("effort" in a.lower() for a in argv), (
            f"agy has no effort flag; something added one: {argv}")
        assert "gemini-3.8-flash-high" in argv


class TestTheStampMatchesIt:
    @pytest.mark.parametrize("name", ["claude", "codex", "antigravity"])
    def test_every_seat_reads_pinned(self, name):
        assert P.effort_provenance(_cfg(name)) == "pinned"

    def test_a_field_disagreeing_with_the_sku_is_a_mismatch(self):
        """The case nothing guarded: the row would record `low` for a dispatch
        that runs high. Louder than `configured`, on purpose."""
        assert P.effort_provenance(_cfg("antigravity", effort="low")) == "mismatch"

    def test_a_sku_with_no_level_is_only_configured(self):
        """`gemini-3.8-flash` pins the model and says nothing about effort, so
        the effort string is Trinity's label and must not claim enforcement."""
        cfg = _cfg("antigravity", model="gemini-3.8-flash")
        assert P.sku_effort(cfg.model) is None
        assert P.effort_provenance(cfg) == "configured"

    def test_no_effort_reads_unknown(self):
        assert P.effort_provenance(_cfg("claude", effort=None)) == "unknown"

    def test_none_config_is_handled(self):
        assert P.effort_provenance(None) == "unknown"


class TestTheSkuParserDoesNotGuess:
    @pytest.mark.parametrize("model,level", [
        ("gemini-3.8-flash-high", "high"),
        ("gemini-3.8-flash-low", "low"),
        ("gemini-3.8-flash-xhigh", "xhigh"),
        ("gemini-3.8-flash", None),
        ("gemini-3.1-pro", None),
        ("claude-opus-5", None),
        ("", None),
        (None, None),
    ])
    def test_only_a_real_trailing_level_counts(self, model, level):
        assert P.sku_effort(model) == level

    def test_a_strange_suffix_is_not_invented_into_a_level(self):
        """`flash` is not an effort. Guessing here would stamp a level nobody set."""
        assert P.sku_effort("gemini-3.8-turbo") is None


class TestTheLiveConfigIsConsistent:
    def test_the_real_seats_do_not_mislabel_their_effort(self):
        """The whole point, against the config that actually ships."""
        from trinity_local.config import load_config
        for name, p in load_config().providers.items():
            prov = P.effort_provenance(p)
            assert prov != "mismatch", (
                f"{name} records effort={p.effort!r} beside model={p.model!r}, "
                f"whose SKU says {P.sku_effort(p.model)!r}")
