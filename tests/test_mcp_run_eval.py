"""The `run_eval` MCP tool (#P4): score a model against the user's taste
IN-SESSION, so the judge rides MCP sampling instead of `claude -p`.

These tests pin the tool's registration, its input validation, and that the
handler delegates to `_blocking_eval` off the event loop (asyncio.to_thread, so
the active sampling session propagates and the judge doesn't spend `claude -p`).
The heavy run_eval/score_run internals are exercised by tests/test_evals_*; here
we mock `_blocking_eval` to assert the wiring, not re-test the scorer.
"""
from __future__ import annotations

import asyncio
import json

from trinity_local import mcp_server


def _call(args: dict):
    return asyncio.run(mcp_server._run_eval(args))


def _payload(result):
    """Extract the JSON dict from a [_text(...)] tool result. `_text` returns
    `{"type": "text", "text": <json>}` and may inject cold-start/extension hint
    keys, so callers assert on a SUBSET, not exact equality."""
    assert len(result) == 1
    return json.loads(result[0]["text"])


def test_run_eval_registered_in_tool_surface():
    tools = asyncio.run(mcp_server.handle_list_tools())
    names = {t.name for t in tools}
    assert "run_eval" in names
    run_eval = next(t for t in tools if t.name == "run_eval")
    props = run_eval.inputSchema["properties"]
    assert set(props) >= {"target", "judge", "limit", "eval_id"}
    assert run_eval.inputSchema["required"] == ["target"]


def test_missing_target_is_a_400():
    err = _call({})
    assert getattr(err[0], "code", None) == 400


def test_blank_target_is_a_400():
    err = _call({"target": "   "})
    assert getattr(err[0], "code", None) == 400


def test_non_string_judge_is_a_400():
    err = _call({"target": "gemini", "judge": 5})
    assert getattr(err[0], "code", None) == 400


def test_bad_limit_is_a_400():
    assert getattr(_call({"target": "gemini", "limit": "lots"})[0], "code", None) == 400
    assert getattr(_call({"target": "gemini", "limit": 0})[0], "code", None) == 400


def test_handler_delegates_to_blocking_eval_with_parsed_args(monkeypatch):
    seen = {}

    def _fake_blocking(target, judge, limit, eval_id):
        seen.update(target=target, judge=judge, limit=limit, eval_id=eval_id)
        return {"ok": True, "target": target, "aggregate_score": 0.7}

    monkeypatch.setattr(mcp_server, "_blocking_eval", _fake_blocking)
    out = _payload(_call({"target": " gemini ", "judge": "claude", "limit": 3, "eval_id": "eval_abc"}))
    # subset (the _text wrapper may inject cold-start/extension hints).
    assert out["ok"] is True and out["target"] == "gemini" and out["aggregate_score"] == 0.7
    # target is stripped; the rest flow through verbatim.
    assert seen == {"target": "gemini", "judge": "claude", "limit": 3, "eval_id": "eval_abc"}


def test_limit_defaults_to_five(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        mcp_server, "_blocking_eval",
        lambda target, judge, limit, eval_id: captured.update(limit=limit) or {"ok": True},
    )
    _call({"target": "gemini"})
    assert captured["limit"] == 5


def test_blocking_eval_reports_missing_eval_set(monkeypatch, tmp_path):
    """With no eval sets on disk, the tool returns a structured, actionable
    reason instead of raising."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
    # Point evals_dir at an empty dir + give a config with one provider so we
    # reach the eval-set check (not the provider check).
    out = mcp_server._blocking_eval("claude", "codex", 5, None)
    assert out["ok"] is False and "eval-build" in out["reason"]
