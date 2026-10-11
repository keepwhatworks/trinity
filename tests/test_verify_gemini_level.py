"""verify reads Gemini at the medium level; councils keep the configured SKU (res_183, res_185).

The Gemini read was verify's slowest branch in 10 of 10 real verifies; at medium the median
verify fell from 77.8 s to 17.7 s with no difference on the planted-gap instrument. agy carries
its level in the SKU suffix, so these tests read the model off the argv verify actually builds.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from trinity_local import providers as P
from trinity_local import verify as V
from trinity_local.config import ProviderConfig


def _cfg(name, model, effort):
    return ProviderConfig(name=name, type="cli", enabled=True, label=name,
                          command=[{"antigravity": "agy"}.get(name, name), "-p"], args=[], task_types=set(), model=model, effort=effort)


CONFIG = SimpleNamespace(providers={
    "antigravity": _cfg("antigravity", "gemini-3.8-flash-high", "high"),
    "claude": _cfg("claude", "claude-opus-5-5", "xhigh"),
})


def _dispatched(name, monkeypatch, tmp_path, config=CONFIG):
    seen = {}

    def spy(cmd, *a, **kw):
        seen.setdefault("argv", list(cmd))

        class R:
            returncode, stdout, stderr = 0, '{"votes": {}}', ""
        return R()

    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setattr(P, "which_on_runtime_path", lambda b: f"/usr/local/bin/{b}")
    _result, pconf = V._dispatch(name, "PROMPT", tmp_path, config)
    argv = seen["argv"]
    return argv[argv.index("--model") + 1], pconf


def test_verify_reads_gemini_at_medium(monkeypatch, tmp_path):
    monkeypatch.delenv(V.GEMINI_LEVEL_ENV, raising=False)
    model, pconf = _dispatched("antigravity", monkeypatch, tmp_path)
    assert model == "gemini-3.8-flash-medium"
    # The read records what ran, and its effort label agrees with the SKU (no "mismatch" stamp).
    assert (pconf.model, pconf.effort) == ("gemini-3.8-flash-medium", "medium")
    assert CONFIG.providers["antigravity"].model == "gemini-3.8-flash-high", "the shared config is untouched"


def test_one_setting_restores_high(monkeypatch, tmp_path):
    monkeypatch.setenv(V.GEMINI_LEVEL_ENV, "high")
    model, _ = _dispatched("antigravity", monkeypatch, tmp_path)
    assert model == "gemini-3.8-flash-high"


def test_other_readers_are_untouched(monkeypatch, tmp_path):
    monkeypatch.delenv(V.GEMINI_LEVEL_ENV, raising=False)
    model, _ = _dispatched("claude", monkeypatch, tmp_path)
    assert model == "claude-opus-5-5"


def test_a_sku_without_a_level_is_left_as_configured(monkeypatch, tmp_path):
    monkeypatch.delenv(V.GEMINI_LEVEL_ENV, raising=False)
    cfg = SimpleNamespace(providers={"antigravity": _cfg("antigravity", "gemini-3.8-flash", None)})
    model, _ = _dispatched("antigravity", monkeypatch, tmp_path, cfg)
    assert model == "gemini-3.8-flash"


def test_an_unknown_level_is_an_error_not_a_silent_default(monkeypatch, tmp_path):
    monkeypatch.setenv(V.GEMINI_LEVEL_ENV, "fast")
    with pytest.raises(ValueError, match="not a Gemini level"):
        _dispatched("antigravity", monkeypatch, tmp_path)
