"""An agent is told when a council lost a lab.

The status payload carried `member_count` alone. A council that lost Claude
returned member_count: 2 with nothing distinguishing it from a two-member
council someone asked for. Measured 2026-09-28: 70% of the last 30 days'
councils were degraded, and the calling agent was told about none of them.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from trinity_local import provider_quota


def _council(home, monkeypatch, *, claude_out):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import ProviderResult

    chairman = ("Agreed claims\n- one\n\n```json\n"
                '{"winner":"codex","confidence":"medium","task_type":"comparison"}\n```\n')

    class FakeProvider:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            if "synthesizer" in prompt.lower():
                return ProviderResult(provider=self.name, stdout=chairman, stderr="", returncode=0)
            if self.name == "claude":
                return ProviderResult(provider="claude", stdout=claude_out, stderr="", returncode=0)
            return ProviderResult(provider=self.name, stdout=f"{self.name} answer",
                                  stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider",
                        lambda cfg: FakeProvider(cfg.name))
    provider_quota.clear()
    r = run_council(config=load_config(),
                    bundle=create_prompt_bundle(task_cluster_id="c_disclose",
                                                task_text="pick one", goal="decide"),
                    member_providers=["claude", "codex"], primary_provider="codex",
                    cwd=Path(home))
    provider_quota.clear()
    return r


def _status(council_id):
    from trinity_local import mcp_server
    out = asyncio.run(mcp_server._get_council_status({"council_run_id": council_id}))
    return json.loads(out[0]["text"])["outcome"]


def test_a_lost_lab_is_named_to_the_caller(patch_trinity_home, monkeypatch):
    r = _council(patch_trinity_home, monkeypatch,
                 claude_out="You're out of usage credits. Switch to another model, or manage "
                            "usage credits at claude.ai/settings/usage, to continue.")
    o = _status(r.outcome.council_run_id)
    assert o["degraded"] is True
    assert {"provider": "claude", "reason": "quota_banner_as_answer"} in o["failed_members"]
    assert o["degraded_notice"].startswith("DEGRADED: 1 of 2 labs answered")


def test_a_healthy_council_says_so(patch_trinity_home, monkeypatch):
    r = _council(patch_trinity_home, monkeypatch, claude_out="claude answer")
    o = _status(r.outcome.council_run_id)
    assert o["degraded"] is False and "failed_members" not in o


def _council_seq(home, monkeypatch, gemini_seq):
    """antigravity returns successive (stdout, stderr) pairs; counts dispatches."""
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import ProviderResult

    chairman = ("Agreed claims\n- one\n\n```json\n"
                '{"winner":"codex","confidence":"medium","task_type":"comparison"}\n```\n')
    calls = {"antigravity": 0}
    seq = list(gemini_seq)

    class FakeProvider:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            if "synthesizer" in prompt.lower():
                return ProviderResult(provider=self.name, stdout=chairman, stderr="", returncode=0)
            if self.name == "antigravity":
                calls["antigravity"] += 1
                out, err = seq.pop(0) if seq else ("late answer", "")
                return ProviderResult(provider="antigravity", stdout=out, stderr=err, returncode=0)
            return ProviderResult(provider=self.name, stdout="codex answer", stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider",
                        lambda cfg: FakeProvider(cfg.name))
    provider_quota.clear()
    r = run_council(config=load_config(),
                    bundle=create_prompt_bundle(task_cluster_id="c_retry",
                                                task_text="pick one", goal="decide"),
                    member_providers=["antigravity", "codex"], primary_provider="codex",
                    cwd=Path(home))
    provider_quota.clear()
    return r, calls


TIMEOUT = "[agy] print timeout after 5m0s with turn in progress; returning partial output"
BANNER = "You've hit your session limit · resets 2:10pm (America/New_York)"


def test_a_transient_failure_is_retried_once_and_recovers(patch_trinity_home, monkeypatch):
    r, calls = _council_seq(patch_trinity_home, monkeypatch, [("", TIMEOUT), ("Gemini's view", "")])
    assert calls["antigravity"] == 2
    md = r.outcome.metadata
    assert "antigravity" not in md["failed_members"]
    m = next(x for x in r.outcome.member_results if x.provider == "antigravity")
    assert m.metadata.get("retried_after") == "empty_answer", "a recovered member must be visible"


def test_a_second_failure_is_final_and_labelled(patch_trinity_home, monkeypatch):
    r, calls = _council_seq(patch_trinity_home, monkeypatch, [("", TIMEOUT), ("", TIMEOUT)])
    assert calls["antigravity"] == 2, "exactly ONE retry, never more"
    f = next(x for x in r.outcome.metadata["member_failures"] if x["provider"] == "antigravity")
    assert f["attempts"] == 2 and f["retried_after"] == "empty_answer"


def test_a_usage_wall_is_never_retried(patch_trinity_home, monkeypatch):
    """It would fail identically and spend quota doing it."""
    r, calls = _council_seq(patch_trinity_home, monkeypatch, [(BANNER, ""), ("x", "")])
    assert calls["antigravity"] == 1
    assert "antigravity" in r.outcome.metadata["failed_members"]


def test_a_missing_binary_is_never_retried():
    from trinity_local.council_runner import _RETRYABLE_MEMBER_FAILURES
    assert "exception" not in _RETRYABLE_MEMBER_FAILURES, (
        "the exception this runner sees is 'Provider binary not found' -- permanent")


def test_every_attempt_is_timed_failed_ones_included(patch_trinity_home, monkeypatch):
    """amd_0267: where a degraded council's time went must be on the record,
    including the attempt that failed and the retry that followed."""
    denial = ("", "Error: no output produced. Headless mode cannot prompt for permission.")
    r, _ = _council_seq(patch_trinity_home, monkeypatch, [denial, denial])
    o = r.outcome
    f = next(x for x in o.metadata["member_failures"] if x["provider"] == "antigravity")
    assert [t["ended"] for t in f["timings"]] == ["headless_tool_denied", "headless_tool_denied"]
    assert all(isinstance(t["seconds"], float) and t["started_at"] for t in f["timings"])
    ok = next(m for m in o.member_results if m.provider == "codex")
    assert [t["ended"] for t in ok.metadata["timings"]] == ["ok"]
    t = o.metadata["timings"]
    assert set(t) == {"members_seconds", "chairman_seconds"} and all(v >= 0 for v in t.values())


def test_a_recovered_member_keeps_both_attempts(patch_trinity_home, monkeypatch):
    denial = ("", "Error: no output produced. Headless mode cannot prompt for permission.")
    r, _ = _council_seq(patch_trinity_home, monkeypatch, [denial, ("gemini answer", "")])
    g = next(m for m in r.outcome.member_results if m.provider == "antigravity")
    assert [t["ended"] for t in g.metadata["timings"]] == ["headless_tool_denied", "ok"]
