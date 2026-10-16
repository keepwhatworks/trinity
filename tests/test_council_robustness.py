"""Councils over MCP stay responsive, fail honestly, and stop waiting when they should
(coherence review 2026-10-15).

- A helper waited on the event loop's own thread for a coroutine scheduled onto that loop:
  5.0 s per log, progress and roots call, measured.
- The council ran on the event loop, so for its whole run (minutes) the server could not
  answer get_council_status, verify or trust.
- A failed MCP council never wrote its failure (no status token), so it read "running".
- A member that hit its timeout was retried (8 minutes became 16), its partial output was
  discarded because it arrives as bytes, and failure notices named only a reason code.
- Every MCP reply, verify's included, carried a lens upsell and an extension nag.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from trinity_local import provider_quota


@pytest.fixture(autouse=True)
def _clean():
    provider_quota.clear()
    yield
    provider_quota.clear()


def test_scheduling_from_the_loop_thread_does_not_block_it():
    from trinity_local.mcp_features import _run_on_loop
    ran = []

    async def note():
        ran.append(1)

    async def main():
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        assert _run_on_loop(note(), loop) is None
        elapsed = time.monotonic() - t0
        await asyncio.sleep(0)            # let the scheduled task run
        return elapsed
    elapsed = asyncio.run(main())
    assert elapsed < 0.5, f"blocked the loop for {elapsed:.1f}s"
    assert ran == [1]


def test_a_council_does_not_block_other_tools(monkeypatch, patch_trinity_home):
    """While a council runs, trust answers. It used to wait for the whole council."""
    from trinity_local import mcp_server
    from trinity_local.commands import council as C

    def slow_launch(args):
        time.sleep(1.5)
        return {"council_run_id": "council_x", "task_path": "", "sync_path": "", "review_path": ""}
    monkeypatch.setattr(C, "handle_council_launch", slow_launch)
    monkeypatch.setattr(mcp_server, "_lookup_council_status", lambda cid: None, raising=False)
    finished = {}

    async def council():
        await mcp_server.handle_call_tool("run_council", {"task": "pick one"})
        finished["council"] = time.monotonic()

    async def trust():
        await asyncio.sleep(0.1)
        await mcp_server.handle_call_tool("trust", {})
        finished["trust"] = time.monotonic()

    async def main():
        await asyncio.gather(council(), trust(), return_exceptions=True)
    asyncio.run(main())
    assert finished["trust"] < finished["council"], finished


def test_the_council_handler_runs_off_the_main_thread(patch_trinity_home, monkeypatch):
    """Signal handlers can only be installed on the main thread; the council now runs on a
    worker thread and must not die there."""
    from trinity_local.commands import council as C
    monkeypatch.setattr(C, "run_council", lambda **kw: (_ for _ in ()).throw(RuntimeError("stop")))
    err = []

    def go():
        try:
            C.handle_council_launch(SimpleNamespace(
                task="q", task_positional=None, goal="g", instructions="i", context_file=None,
                project_hint="", members=["claude", "codex"], primary_provider="claude",
                cwd=".", config=None, open_browser=False, status_token=None, quiet=True))
        except Exception as exc:  # noqa: BLE001
            err.append(exc)
    t = threading.Thread(target=go)
    t.start(); t.join(30)
    assert err and str(err[0]) == "stop", err      # reached the council, not a signal error


def test_a_failed_mcp_council_says_failed_not_running(patch_trinity_home, monkeypatch):
    from trinity_local.commands import council as C
    from trinity_local.council_status import load_council_status
    seen = {}
    real_create = C.create_prompt_bundle

    def capture(**kw):
        b = real_create(**kw)
        seen["bundle"] = b.bundle_id
        return b
    monkeypatch.setattr(C, "create_prompt_bundle", capture)
    monkeypatch.setattr(C, "run_council", lambda **kw: (_ for _ in ()).throw(RuntimeError("codex usage limit")))
    with pytest.raises(RuntimeError):
        C.handle_council_launch(SimpleNamespace(
            task="q", task_positional=None, goal="g", instructions="i", context_file=None,
            project_hint="", members=["claude", "codex"], primary_provider="claude",
            cwd=".", config=None, open_browser=False, status_token=None, quiet=True))
    status = load_council_status(seen["bundle"])
    assert status and status.get("status") == "failed" and "usage limit" in status.get("error", "")


def test_a_timeout_keeps_its_partial_output_in_the_detail(monkeypatch):
    from trinity_local import providers as P
    from trinity_local.config import ProviderConfig

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=1, output=b"I think the answer is")
    monkeypatch.setattr(P, "run_with_runtime_env", boom)
    monkeypatch.setattr(P.CodexProvider, "_ensure_binary", lambda self: None)
    cfg = ProviderConfig(name="codex", type="codex", enabled=True, label="Codex",
                         command=["codex", "exec"], args=[], task_types=set())
    res = P.CodexProvider(cfg)._run_command(["codex", "exec", "x"], Path("."))
    assert res.returncode == -1 and res.stdout == ""
    assert "Timed out" in res.stderr and "I think the answer is" in res.stderr


def _council_with(monkeypatch, patch_trinity_home, codex_result):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import BaseProvider, ProviderResult
    calls = []
    chair = ('Agreed claims\n- one\n\n```json\n{"winner":"claude","confidence":"medium",'
             '"task_type":"coding"}\n```\n')

    def fake(self, command, cwd, *, timeout=None):
        calls.append(self.config.name)
        if any("synthesizer" in str(a).lower() for a in command):
            return ProviderResult(provider=self.config.name, stdout=chair, stderr="", returncode=0)
        if self.config.name == "codex":
            return codex_result
        return ProviderResult(provider=self.config.name, stdout="an answer", stderr="", returncode=0)
    monkeypatch.setattr(BaseProvider, "_run_command", fake)
    out = run_council(config=load_config(),
                      bundle=create_prompt_bundle(task_cluster_id="c", task_text="q", goal="g"),
                      member_providers=["claude", "codex"], primary_provider="claude",
                      cwd=Path(patch_trinity_home))
    return out, calls


def test_a_timed_out_member_is_not_retried(monkeypatch, patch_trinity_home):
    from trinity_local.providers import ProviderResult
    timed_out = ProviderResult(provider="codex", stdout="", stderr="Timed out after 480.0s",
                               returncode=-1)
    out, calls = _council_with(monkeypatch, patch_trinity_home, timed_out)
    assert calls.count("codex") == 1, calls
    failures = out.outcome.metadata.get("member_failures") or []
    assert failures and failures[0]["reason"] == "timeout"


def test_the_degraded_notice_says_why(monkeypatch, patch_trinity_home):
    from trinity_local.mcp_server import _outcome_summary
    from trinity_local.providers import ProviderResult
    wall = ProviderResult(provider="codex", stdout="",
                          stderr="You've hit your usage limit. Try again at 4:12 AM.", returncode=1)
    out, _ = _council_with(monkeypatch, patch_trinity_home, wall)
    notice = _outcome_summary(out.outcome)["degraded_notice"]
    assert "usage" in notice.lower(), notice


def test_the_all_failed_error_names_each_reason(monkeypatch, patch_trinity_home):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import BaseProvider, ProviderError, ProviderResult
    monkeypatch.setattr(BaseProvider, "_run_command", lambda self, c, cwd, timeout=None: ProviderResult(
        provider=self.config.name, stdout="", stderr=f"{self.config.name} is signed out", returncode=1))
    with pytest.raises(ProviderError) as e:
        run_council(config=load_config(),
                    bundle=create_prompt_bundle(task_cluster_id="c", task_text="q", goal="g"),
                    member_providers=["claude", "codex"], primary_provider="claude",
                    cwd=Path(patch_trinity_home))
    assert "codex" in str(e.value) and "signed out" in str(e.value)


def test_a_live_council_is_not_called_stale_before_its_timeouts_run_out():
    from trinity_local.council_status import _STALE_RUNNING_SECONDS
    from trinity_local.providers import DEFAULT_PROVIDER_TIMEOUT_SECONDS
    assert _STALE_RUNNING_SECONDS >= 4 * DEFAULT_PROVIDER_TIMEOUT_SECONDS


@pytest.mark.parametrize("payload,hinted", [({"council_run_id": "c1", "ok": True}, True),
                                             ({"triage": "READ"}, False)])
def test_onboarding_hints_ride_council_replies_only(monkeypatch, payload, hinted):
    from trinity_local import mcp_server, tips
    monkeypatch.setattr(mcp_server, "_extension_status_hint", lambda: {"hint": "x"})
    monkeypatch.setattr(mcp_server, "_EXTENSION_HINT_SHOWN", False)
    monkeypatch.setattr(tips, "next_tip", lambda **k: {"key": "create-lens"})
    body = json.loads(mcp_server._text(dict(payload))["text"])
    assert ("tip" in body) is hinted and ("extension_status" in body) is hinted
