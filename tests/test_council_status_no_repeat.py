"""get_council_status returns each fact once.

Two repeats measured 2026-09-28 in live councils: the chairman's routing-json
fence arrived inside synthesis_output AND as agreed_claims/disagreed_claims,
and lens_cold_open rode every dict response, so a session polling a council
read the same tension on every poll.
"""
from __future__ import annotations

import json


def test_parsed_fence_is_stripped_but_prose_kept():
    from trinity_local.council_runtime import parse_routing_label
    from trinity_local.mcp_server import _synthesis_for_agent

    text = ("Prose verdict.\n\n```routing-json\n"
            '{"winner":"codex","agreed_claims":["a"],"disagreed_claims":[]}\n```\n')
    label, _ = parse_routing_label(text)
    assert label is not None
    out = _synthesis_for_agent(text, label)
    assert out == "Prose verdict."
    assert "routing-json" not in out


def test_unparsed_fence_is_kept_as_the_only_record():
    from trinity_local.mcp_server import _synthesis_for_agent

    text = "Prose.\n\n```routing-json\n{not json\n```"
    assert _synthesis_for_agent(text, None) == text


def test_cold_open_rides_only_the_first_response(monkeypatch):
    from trinity_local import mcp_server

    monkeypatch.setattr(mcp_server, "_COLD_OPEN_SHOWN", False)
    monkeypatch.setattr("trinity_local.cold_start.cold_open_tension", lambda: "one tension")
    first = json.loads(mcp_server._text({"x": 1})["text"])
    second = json.loads(mcp_server._text({"x": 2})["text"])
    assert first.get("lens_cold_open") == "one tension"
    assert "lens_cold_open" not in second


def test_a_cold_install_does_not_spend_the_one_showing(monkeypatch):
    from trinity_local import mcp_server

    monkeypatch.setattr(mcp_server, "_COLD_OPEN_SHOWN", False)
    monkeypatch.setattr("trinity_local.cold_start.cold_open_tension", lambda: None)
    mcp_server._text({"x": 1})
    monkeypatch.setattr("trinity_local.cold_start.cold_open_tension", lambda: "later")
    assert json.loads(mcp_server._text({"x": 2})["text"]).get("lens_cold_open") == "later"


# ---- one builder, every path (council_10e38944ddbdb84c, amd_0266) ----------

import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cold_open(monkeypatch):
    """_COLD_OPEN_SHOWN is process-global; without a reset, test order decides."""
    from trinity_local import mcp_server
    monkeypatch.setattr(mcp_server, "_COLD_OPEN_SHOWN", False)


def _fake_council(monkeypatch):
    from trinity_local import provider_quota
    from trinity_local.providers import ProviderResult

    chairman = ("## Decision\n- Ship it.\n\n```routing-json\n"
                '{"winner":"codex","confidence":"medium","task_type":"comparison",'
                '"agreed_claims":["one"],"disagreed_claims":[]}\n```\n')

    class Fake:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            out = chairman if "synthesizer" in prompt.lower() else f"{self.name} answer"
            return ProviderResult(provider=self.name, stdout=out, stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider", lambda cfg: Fake(cfg.name))
    provider_quota.clear()


def _call(name, args):
    from trinity_local import mcp_server
    fn = {"run_council": mcp_server._run_council, "get_council_status": mcp_server._get_council_status}[name]
    return json.loads(asyncio.run(fn(args))[0]["text"])


def test_the_launch_return_carries_the_verdict(patch_trinity_home, monkeypatch):
    _fake_council(monkeypatch)
    r = _call("run_council", {"task": "pick one", "members": ["claude", "codex"],
                              "primary_provider": "codex"})
    assert r["status"] == "completed", r
    assert r["outcome"]["winner"] == "codex"
    assert r["outcome"]["agreed_claims"] == ["one"]
    assert "routing-json" not in r["outcome"]["synthesis_output"]


def test_every_path_uses_the_one_builder(patch_trinity_home, monkeypatch):
    """Swap the builder for a sentinel: every caller-facing path must show it."""
    from trinity_local import mcp_server
    _fake_council(monkeypatch)
    monkeypatch.setattr(mcp_server, "_outcome_summary", lambda outcome: {"sentinel": True})
    launched = _call("run_council", {"task": "pick one", "members": ["claude", "codex"],
                                     "primary_provider": "codex"})
    assert launched["outcome"] == {"sentinel": True}
    status = _call("get_council_status", {"council_run_id": launched["council_run_id"]})
    assert status["outcome"] == {"sentinel": True}
    monkeypatch.setenv("TRINITY_HOST_CLAUDE_MEMBER", "1")
    synth = _call("run_council", {"task": "why?", "host_synthesis": '{"winner": "claude"}',
                                  "responses": [{"provider": "claude", "content": "a"},
                                                {"provider": "codex", "content": "b"}]})
    assert synth["outcome"] == {"sentinel": True}, synth
