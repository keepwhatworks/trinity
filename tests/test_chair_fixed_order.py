"""The chair is Claude, then Codex; a quota banner is never a synthesis.

Coherence review 2026-10-15 (council_34633698184fa40b put this batch third):

- Installed builds chaired coding councils with Gemini. The pick blended global
  benchmarks read from `data/reference_evals.json`, which ships only in a source
  checkout; the wheel fell back to a table ranking Gemini first, and the repo's
  own test asserted Codex because it ran from a checkout.
- The fallback chain followed config insertion order, so Gemini came second, and
  a provider that had just hit its usage wall as a member was tried first.
- `claude -p` reports a credit wall as `is_error` with exit 0; the flag was
  discarded, and the synthesis-only path saved such banners as completed councils.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from trinity_local import provider_quota
from trinity_local.providers import (
    CLIProvider, ProviderResult, _result_is_usable, chairman_fallback_order,
)
from trinity_local.ranker.chairman_picker import predict_strongest_chairman

TRIO = ["antigravity", "codex", "claude"]
BANNER = "You've hit your usage limit. Your limit resets at 4:12 AM."


@pytest.fixture(autouse=True)
def _clean():
    provider_quota.clear()
    yield
    provider_quota.clear()


class TestThePick:
    @pytest.mark.parametrize("task", ["fix this failing test", "why does this crash",
                                      "research the literature", "write a blog post", "hi"])
    def test_installed_build_chairs_claude_not_gemini(self, task, monkeypatch):
        """The wheel has no data/reference_evals.json. Reproduce that and pick."""
        from trinity_local import global_benchmarks
        monkeypatch.setattr(global_benchmarks, "_reference_evals_path",
                            lambda: Path("/nonexistent/reference_evals.json"))
        assert predict_strongest_chairman(task, available_providers=TRIO) == "claude"

    def test_codex_chairs_without_claude(self):
        assert predict_strongest_chairman("fix it", available_providers=["antigravity", "codex"]) == "codex"

    def test_gemini_chairs_only_as_the_last_resort(self):
        assert predict_strongest_chairman("fix it", available_providers=["antigravity"]) == "antigravity"
        assert predict_strongest_chairman("fix it", available_providers=[]) == ""


def _config(order):
    from trinity_local.config import load_config
    cfg = load_config()
    providers = {n: cfg.providers[n] for n in order}
    providers.update({n: p for n, p in cfg.providers.items() if n not in providers})
    cfg.providers.clear()
    cfg.providers.update(providers)
    return cfg


class TestTheFallbackChain:
    def test_config_order_no_longer_puts_gemini_second(self, patch_trinity_home):
        order = chairman_fallback_order(_config(["claude", "antigravity", "codex"]), "claude")
        assert order[:2] == ["claude", "codex"], order

    def test_a_walled_provider_is_tried_last_even_as_primary(self, patch_trinity_home):
        provider_quota.mark_exhausted("claude", kind="usage_limit")
        order = chairman_fallback_order(_config(["claude", "codex", "antigravity"]), "claude")
        assert order == ["codex", "antigravity", "claude"], order


class TestBannersAreNotSyntheses:
    def test_an_exit_zero_banner_is_unusable(self):
        assert not _result_is_usable(ProviderResult(provider="claude", stdout=BANNER,
                                                    stderr="", returncode=0))

    def test_a_real_answer_is_usable(self):
        assert _result_is_usable(ProviderResult(provider="claude", stdout="Pick B: it is reversible.",
                                                stderr="", returncode=0))

    def _claude_run(self, monkeypatch, envelope):
        from trinity_local import providers as P
        monkeypatch.setattr(P, "run_with_runtime_env", lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout=json.dumps(envelope), stderr=""))
        monkeypatch.setattr(CLIProvider, "_ensure_binary", lambda self: None)
        from trinity_local.config import ProviderConfig
        cfg = ProviderConfig(name="claude", type="cli", enabled=True, label="Claude",
                             command=["claude", "-p"], args=[], task_types=set())
        return CLIProvider(cfg)._run_command(["claude", "-p", "x"], Path("."))

    def test_claude_is_error_is_a_failed_run(self, monkeypatch):
        res = self._claude_run(monkeypatch, {"type": "result", "is_error": True,
                                             "result": "Credit balance is too low"})
        assert res.returncode != 0 and res.stdout == ""
        assert "Credit balance is too low" in res.stderr

    def test_an_empty_result_is_not_the_raw_envelope(self, monkeypatch):
        res = self._claude_run(monkeypatch, {"type": "result", "is_error": False, "result": ""})
        assert res.stdout == "", "the raw JSON envelope must never stand in for an answer"


CHAIR_OK = ('{"winner": "codex", "runner_up": "claude", "confidence": "high", '
            '"agreed_claims": [], "disagreed_claims": []}')


def _synthesize(monkeypatch, behaviors):
    """run_council(responses=...) through the real MCP handler; each chair's
    reply comes from `behaviors`, keyed by provider."""
    import asyncio

    import trinity_local.providers as providers_mod
    from trinity_local.mcp_server import handle_call_tool
    called = []

    class Fake:
        def __init__(self, name):
            self.name = name

        def run(self, prompt, cwd=None):
            called.append(self.name)
            out, rc = behaviors[self.name]
            return ProviderResult(provider=self.name, stdout=out, stderr="", returncode=rc)
    monkeypatch.setattr(providers_mod, "make_provider", lambda cfg: Fake(cfg.name))
    res = asyncio.run(handle_call_tool("run_council", {
        "task": "pick one", "primary_provider": "claude",
        "responses": [{"provider": "codex", "content": "A"}, {"provider": "gemini", "content": "B"}]}))
    first = res[0]
    return json.loads(first["text"] if isinstance(first, dict) else first.text), called


class TestSynthesisOnlyFallsBack:
    def test_a_banner_chair_falls_back_to_the_next(self, patch_trinity_home, monkeypatch):
        out, called = _synthesize(monkeypatch, {"claude": (BANNER, 0), "codex": (CHAIR_OK, 0),
                                                "antigravity": (CHAIR_OK, 0)})
        assert out.get("ok") is True, out
        assert called[:2] == ["claude", "codex"]
        assert out["outcome"]["primary_provider"] == "codex"

    def test_no_usable_chair_saves_nothing(self, patch_trinity_home, monkeypatch):
        from trinity_local.state_paths import council_outcomes_dir
        before = set(council_outcomes_dir().glob("*.json")) if council_outcomes_dir().exists() else set()
        out, _ = _synthesize(monkeypatch, {"claude": (BANNER, 0), "codex": ("", 1),
                                           "antigravity": ("", -1)})
        assert out.get("ok") is False and "No chairman" in out.get("error", "")
        after = set(council_outcomes_dir().glob("*.json")) if council_outcomes_dir().exists() else set()
        assert after == before, "a failed synthesis must not be saved as a council"
