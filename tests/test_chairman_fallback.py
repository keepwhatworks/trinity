"""Chairman fallback: when the primary chair (Claude) is rate-limited /
token-exhausted, synthesis falls through to the next enabled provider so a
council still returns a verdict instead of failing."""
from __future__ import annotations

import types


from trinity_local.config import ProviderConfig
from trinity_local.providers import ProviderResult


def _cfg(name, typ="cli"):
    return ProviderConfig(name=name, type=typ, enabled=True, label=name,
                          command=[name], args=[], task_types=set())


def _config():
    return types.SimpleNamespace(providers={
        "claude": _cfg("claude"),
        "codex": _cfg("codex", "codex"),
        "antigravity": _cfg("antigravity"),
    })


class _Stub:
    def __init__(self, behavior):
        self.behavior = behavior

    def run(self, prompt, cwd):
        b = self.behavior
        if b == "raise":
            raise RuntimeError("usage limit reached")
        if b == "empty":
            return ProviderResult(provider="x", stdout="", stderr="rate limited", returncode=1)
        return ProviderResult(provider="x", stdout="WINNER: codex\nsynthesis ok", stderr="", returncode=0)


def _patch(monkeypatch, behaviors):
    import trinity_local.providers as P
    monkeypatch.setattr(P, "make_provider", lambda cfg: _Stub(behaviors[cfg.name]))


def test_fallback_when_primary_raises(monkeypatch):
    from trinity_local.providers import run_with_chairman_fallback
    _patch(monkeypatch, {"claude": "raise", "codex": "ok", "antigravity": "ok"})
    res, used, err = run_with_chairman_fallback("p", _config(), "claude", None)
    assert used == "codex" and err is None and res.stdout.startswith("WINNER")


def test_fallback_when_primary_empty(monkeypatch):
    from trinity_local.providers import run_with_chairman_fallback
    _patch(monkeypatch, {"claude": "empty", "codex": "ok", "antigravity": "ok"})
    res, used, err = run_with_chairman_fallback("p", _config(), "claude", None)
    assert used == "codex" and res is not None


def test_no_fallback_when_primary_ok(monkeypatch):
    from trinity_local.providers import run_with_chairman_fallback
    _patch(monkeypatch, {"claude": "ok", "codex": "ok", "antigravity": "ok"})
    res, used, err = run_with_chairman_fallback("p", _config(), "claude", None)
    assert used == "claude"


def test_all_fail_returns_error(monkeypatch):
    from trinity_local.providers import run_with_chairman_fallback
    _patch(monkeypatch, {"claude": "raise", "codex": "empty", "antigravity": "raise"})
    res, used, err = run_with_chairman_fallback("p", _config(), "claude", None)
    assert res is None and used is None and err


def test_records_fallback_chair(monkeypatch):
    """on_fallback fires for each skipped chair (telemetry/honesty hook)."""
    from trinity_local.providers import run_with_chairman_fallback
    _patch(monkeypatch, {"claude": "raise", "codex": "ok", "antigravity": "ok"})
    skipped = []
    run_with_chairman_fallback("p", _config(), "claude", None,
                               on_fallback=lambda n, why: skipped.append(n))
    assert skipped == ["claude"]
