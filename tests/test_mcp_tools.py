"""Tests for the 8 MCP tools (canonical 4 + v1.5 pair + in-protocol provider loop + lens-lift) and chain-mode council.

Canonical 4: route, run_council (subsumes judge via responses=[...]),
get_persona, get_council_status. (record_outcome retired 2026-05-21.)
v1.5 pair: ask (get_picks removed 2026-08-11 with the routing layer).
In-protocol provider loop: import_provider_memory.
Lens-lift: lens_generators.
(handoff retired 2026-05-26 — 0 usage events; lens flows via MCP Resources.
mark_pick_wrong retired 2026-06-05 — the user-pick layer was removed.)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _call_tool_sync(name: str, arguments: dict) -> dict:
    """Invoke an MCP tool and return the parsed text payload."""
    from trinity_local.mcp_server import handle_call_tool

    results = asyncio.run(handle_call_tool(name, arguments))
    assert results, "tool returned no results"
    first = results[0]
    text = first["text"] if isinstance(first, dict) else getattr(first, "text", str(first))
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"raw": text}


class TestToolList:
    def test_canonical_tools_present(self):
        from trinity_local.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        names = {t.name for t in tools}
        # Canonical 4: route, run_council (subsumes judge via responses=[...]),
        # get_persona, get_council_status.
        # v1.5 adds: `ask` (single-call routing). `get_picks` was removed
        # 2026-08-11 with the lens-basin router it introspected.
        # (introspection for the agent into the user's extracted routing
        # patterns).
        # In-protocol provider loop: `import_provider_memory` (agent
        # pipes extracted lens tensions / rejection signals into Trinity).
        # Lens-lift: `lens_generators` (cross-domain generating invariants).
        # (`get_eval_summary` retired 2026-05-18 in commit `1fed7fc`;
        # `record_outcome` retired 2026-05-21 — chairman pick is the
        # supervision signal now, not user verdicts.
        # `handoff` retired 2026-05-26 — 0 usage events in production;
        # the lens flows via MCP Resources instead.
        # `mark_pick_wrong` retired 2026-06-05 — the user-pick/veto layer
        # was removed; the chairman's pick stands alone.)
        # `route` removed in the loop-primitive surface cut (8→7) — `ask(mode='route')`
        # is the one routing entrypoint; the standalone tool was reinventing what ask owns.
        # `run_eval` added (7→8) — score a model against the user's taste in-session
        # so the judge rides MCP sampling instead of `claude -p` (the CLI eval-run gap).
        assert names == {
            "trust", "run_council", "verify", "get_council_status",
        }, f"unexpected tool list: {names}"

    def test_old_tools_dropped_from_public_surface(self):
        from trinity_local.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        names = {t.name for t in tools}
        for legacy in (
            "get_status", "get_elo", "get_recent_councils", "watch_once",
            "get_recommendation", "judge",
            "record_outcome",  # retired 2026-05-21 (rating UX sunset)
            "handoff",  # retired 2026-05-26 (0 usage; lens flows via MCP Resources)
            "mark_pick_wrong",  # retired 2026-06-05 (user-pick/veto layer removed)
            "route",  # removed in the loop-primitive cut — ask(mode='route') subsumes it
            # Soft-demoted off the MCP surface 2026-07-18 (CLI verbs + engines stay):
            "run_eval", "choose", "lens_generators",
        ):
            assert legacy not in names, f"legacy tool {legacy!r} still exposed"

    def test_run_council_schema_includes_responses_param(self):
        from trinity_local.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        run_council = next(t for t in tools if t.name == "run_council")
        # responses param is what makes run_council subsume judge.
        assert "responses" in run_council.inputSchema["properties"]

    def test_escalation_reflex_lives_in_tool_descriptions(self):
        """The "reach for Trinity on a hard/contested call" reflex must live in the
        TOOL DESCRIPTIONS — every harness reads those at handshake — not only in
        this repo's CLAUDE.md (which no other agent sees). Founder direction
        2026-06-06 ("Council = the escalation reflex"). Pins:
          - run_council LEADS with the reflex + the one-model-vs-panel framing;
          - ask(mode='route') frames the output as an ESCALATION decision;
          - get_persona points to the ambient SKILL.md alternative (lens-skill)."""
        from trinity_local.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        d = {t.name: t.description for t in tools}

        rc = d["run_council"]
        assert "WHEN TO REACH FOR THIS" in rc, "run_council lost its leading reflex trigger"
        assert "ONE model" in rc and "panel of independent" in rc, (
            "run_council lost the 'you alone are one opinion; this is a panel' framing "
            "— the reason an agent should escalate instead of guessing solo"
        )
        # the existing structured-verdict contract must survive the rewrite
        assert "agreed_claims" in rc and "disagreed_claims" in rc

        # `ask` left the surface 2026-09-08. `verify` carries the pre-deploy
        # half of the reflex now: it must say what it emits and why a green
        # test is required for a skip, or an agent cannot act on its answer.
        v = d["verify"]
        assert "STOP" in v and "READ" in v, (
            "verify must name its outcomes in its description — an agent reads "
            "this, not the docs")
        assert "9/34" in v and "zero times" in v, (
            "verify must cite the measurement WITH its population: 26% is the "
            "agent-written-fix rate, and on real human fixes the panel's unanimous "
            "approvals were wrong zero times. A rate without its population is the "
            "denominator error hq_110 exists to catch")

        # get_persona left the surface 2026-09-08 with the lens headline; the
        # lens reaches the chairman directly and via MCP Resources, not a tool.
        assert "get_persona" not in d


class TestDeletedToolsAreGone:
    """ask, get_persona and import_provider_memory were soft-demoted 2026-09-08 and deleted
    2026-10-15 with founder approval; a host that still calls one gets a readable not-found."""

    @pytest.mark.parametrize("name", ["ask", "get_persona", "import_provider_memory", "route"])
    def test_calling_a_deleted_tool_says_not_found(self, home: Path, name: str):
        from trinity_local.mcp_server import ToolError
        with pytest.raises(ToolError) as e:
            asyncio.run(__import__("trinity_local.mcp_server", fromlist=["x"]).handle_call_tool(name, {}))
        assert "Tool not found" in str(e.value)


class TestGetCouncilStatus:
    """Same silent-failure shape audit as TestRecordOutcome —
    get_council_status used to swallow load_council_outcome
    exceptions and return `status: completed, outcome: null` with
    no signal of why the outcome was unreadable. The agent would
    show the user a half-rendered status. Now: outcome_load_error
    surfaces the cause."""

    def test_outcome_load_error_surfaces_when_outcome_file_corrupt(self, home: Path):
        from trinity_local.state_paths import council_outcomes_dir

        # Plant a council outcome file with corrupted (un-loadable) JSON
        # AND a matching status payload so the function takes the
        # "outcome_path.exists() so attempt load" branch.
        council_run_id = "council_corrupted_abc"
        outcome_path = council_outcomes_dir() / f"{council_run_id}.json"
        outcome_path.parent.mkdir(parents=True, exist_ok=True)
        outcome_path.write_text("{ this is not valid json ", encoding="utf-8")

        result = _call_tool_sync("get_council_status", {
            "council_run_id": council_run_id,
        })
        # The function still responds (didn't crash).
        # outcome_summary couldn't be built but the error reason is named.
        assert result.get("outcome") is None
        assert "outcome_load_error" in result, (
            "Silent failure regressed: load_council_outcome raised but "
            "the agent has no way to know the outcome JSON is corrupt"
        )
        # The error string mentions the exception class so the agent
        # can distinguish JSONDecodeError (file corrupt) from
        # FileNotFoundError (id wrong) etc.
        assert "Error" in result["outcome_load_error"] or "JSONDecodeError" in result["outcome_load_error"]


# ---------------------------------------------------------------------------
# Chain-mode council
# ---------------------------------------------------------------------------

class TestChainMode:
    def test_routing_label_carries_synthesis_fields(self, home: Path):
        """Verify CouncilRoutingLabel.from_dict accepts agreed_claims/disagreed_claims."""
        from trinity_local.council_schema import CouncilRoutingLabel

        label = CouncilRoutingLabel.from_dict({
            "winner": "claude",
            "confidence": "high",
            "agreed_claims": ["the user wants X", "the answer must be concise"],
            "disagreed_claims": [{
                "claim": "the user wants Y",
                "providers_for": ["claude"],
                "providers_against": ["antigravity"],
                "why_matters": "drives the recommendation",
            }],
        })
        assert label.agreed_claims == ["the user wants X", "the answer must be concise"]
        assert len(label.disagreed_claims) == 1
        assert label.disagreed_claims[0]["claim"] == "the user wants Y"

    def test_chairman_prompt_includes_synthesis_arrays(self, home: Path):
        from trinity_local.council_runtime import render_primary_council_prompt
        from trinity_local.council_schema import CouncilMemberResult, PromptBundle

        bundle = PromptBundle(
            bundle_id="b1",
            task_cluster_id="tc1",
            task_text="anything",
            goal="test",
            created_at="2026-05-03T00:00:00Z",
        )
        members = [
            CouncilMemberResult(provider="claude", output_text="A"),
            CouncilMemberResult(provider="antigravity", output_text="B"),
        ]
        prompt = render_primary_council_prompt(bundle, members)
        assert "agreed_claims" in prompt
        assert "disagreed_claims" in prompt
        assert "why_matters" in prompt

    def test_routing_json_parser_extracts_synthesis_fields(self, home: Path):
        from trinity_local.council_runtime import parse_routing_label

        synthesis = """## Winner
Claude

```routing-json
{
  "winner": "claude",
  "confidence": "high",
  "agreed_claims": ["X is true", "Y is the constraint"],
  "disagreed_claims": [
    {"claim": "Z is needed", "providers_for": ["claude"], "providers_against": ["antigravity"], "why_matters": "affects the decision"}
  ]
}
```
"""
        label, error = parse_routing_label(synthesis)
        assert error is None
        assert label is not None
        assert label.agreed_claims == ["X is true", "Y is the constraint"]
        assert len(label.disagreed_claims) == 1
        assert label.disagreed_claims[0]["claim"] == "Z is needed"
        assert label.disagreed_claims[0]["why_matters"] == "affects the decision"

    def test_chain_outcome_persists_steps(self, home: Path):
        from trinity_local.council_runtime import (
            create_council_outcome,
            create_prompt_bundle,
            save_council_outcome,
            load_council_outcome,
        )
        from trinity_local.council_schema import (
            CouncilChainStep,
            CouncilMemberResult,
            CouncilRoutingLabel,
        )

        bundle = create_prompt_bundle(
            task_cluster_id="tc1",
            task_text="chain test",
            goal="best",
        )
        steps = [
            CouncilChainStep(
                step_index=0, model_provider="claude",
                model_name="claude-x", input_text="prompt 0", output_text="claude output",
            ),
            CouncilChainStep(
                step_index=1, model_provider="antigravity",
                model_name="gemini-x", input_text="prompt 1", output_text="gemini refinement",
            ),
        ]
        outcome = create_council_outcome(
            bundle=bundle,
            primary_provider="claude",
            member_results=[
                CouncilMemberResult(provider="claude", output_text="claude output"),
                CouncilMemberResult(provider="antigravity", output_text="gemini refinement"),
            ],
            mode="chain",
            chain_steps=steps,
            # iter #106 strict contract: synthesis_output + routing_label required.
            synthesis_output="[test stub]",
            routing_label=CouncilRoutingLabel(winner="antigravity", confidence="medium"),
        )
        path = save_council_outcome(outcome)
        on_disk = json.loads(path.read_text())
        assert on_disk["mode"] == "chain"
        assert len(on_disk["chain_steps"]) == 2
        assert on_disk["chain_steps"][0]["model_provider"] == "claude"

        # Roundtrip
        reloaded = load_council_outcome(outcome.council_run_id)
        assert reloaded.mode == "chain"
        assert len(reloaded.chain_steps) == 2
        assert reloaded.chain_steps[1].model_provider == "antigravity"


# TestRateActionNudge + TestPendingRatingsHint removed 2026-05-21.
# The _build_rate_action and _pending_ratings_hint mechanism was
# retired in the same commit — agents no longer get a "go capture
# the verdict" hint embedded in MCP responses because the chairman
# pick IS the verdict (auto-recorded). Registry entries:
# `rate_action`, `pending_ratings` in retired_names.py.


class TestRunCouncilCanonicalizesProviderSlugs:
    """Web-era brand slugs (chatgpt/claude_ai/gemini) can reach run_council from
    an agent that read a stale `get_picks primary='chatgpt'` (a long-lived --mcp
    server serving pre-canonicalization code), a provider-imported pick, or
    copy-paste from a council page. config.providers only knows the CLI slugs
    (claude/codex/antigravity), so without folding at the LAUNCH boundary the
    member silently vanishes (config.providers.get('chatgpt') -> None) or the
    chairman errors out as 'missing'. council_runtime canonicalizes the
    OUTCOME-LOAD boundary; this guards the symmetric launch-INPUT boundary."""

    def test_canonicalize_member_slugs_folds_dedupes_and_passes_through(self):
        from trinity_local.mcp_server import _canonicalize_member_slugs

        # Web-era brands fold to the CLI slug; the explicit `codex` dedupes
        # against the chatgpt-derived one (order preserved).
        assert _canonicalize_member_slugs(
            ["chatgpt", "claude", "gemini", "codex"]
        ) == ["codex", "claude", "antigravity"]
        # Arbitrary non-provider labels (synthesis "answer_a") pass through —
        # normalize_provider_slug only maps the known brand names.
        assert _canonicalize_member_slugs(["answer_a", "external"]) == [
            "answer_a",
            "external",
        ]
        # Falsy / non-str junk is dropped or passed through, never crashes.
        assert _canonicalize_member_slugs(["claude", "", None]) == ["claude"]
        # dedupe=False: repeats must survive (order- and multiplicity-preserving)
        # — only the brand folds.
        assert _canonicalize_member_slugs(
            ["claude", "chatgpt", "claude"], dedupe=False
        ) == ["claude", "codex", "claude"]

    def test_launch_path_folds_members_and_primary_provider(self, home: Path, monkeypatch):
        """run_council(members=[web-era...], primary_provider='chatgpt') must
        reach handle_council_launch with dispatchable CLI slugs — otherwise the
        'chatgpt' member is silently dropped and the council runs short."""
        captured: dict = {}

        def _stub_handle(args):
            captured["members"] = list(args.members)
            captured["primary_provider"] = getattr(args, "primary_provider", None)
            return {
                "council_run_id": "council_test_canon",
                "task_path": "/tmp/x", "sync_path": "/tmp/y",
                "review_path": "/tmp/z",
            }

        from trinity_local.commands import council as council_cmd
        monkeypatch.setattr(council_cmd, "handle_council_launch", _stub_handle)

        result = _call_tool_sync("run_council", {
            "task": "decide the dispatch layer",
            "members": ["chatgpt", "claude", "gemini", "codex"],
            "primary_provider": "chatgpt",
        })
        assert result.get("ok") is True
        # The lock-in: no web-era slug survives to dispatch.
        assert captured["members"] == ["codex", "claude", "antigravity"]
        assert captured["primary_provider"] == "codex"
        assert "chatgpt" not in captured["members"]

    def test_synthesis_path_canonicalizes_chairman_and_response_labels(self, home: Path, monkeypatch):
        """responses=[...] synthesis: primary_provider='chatgpt' must resolve to
        the codex config (not error as 'missing'), and the response provider
        labels written to the outcome (which the routing table reads) must be
        canonical so a 'chatgpt' label can't poison routing (#249/#260)."""
        class _FakeResult:
            stdout = ('{"winner": "codex", "runner_up": "claude", '
                      '"confidence": "high", "agreed_claims": [], '
                      '"disagreed_claims": []}')
            stderr = ""
            returncode = 0

        ran = {"called": False}

        class _FakeProvider:
            def run(self, prompt, cwd=None):
                ran["called"] = True
                return _FakeResult()

        import trinity_local.providers as providers_mod
        monkeypatch.setattr(providers_mod, "make_provider", lambda cfg: _FakeProvider())

        result = _call_tool_sync("run_council", {
            "task": "synthesize these",
            "primary_provider": "chatgpt",  # → codex; must NOT error as missing
            "responses": [
                {"provider": "chatgpt", "content": "A"},
                {"provider": "gemini", "content": "B"},
            ],
        })
        # Chairman 'chatgpt' resolved to the enabled codex config (no missing error).
        assert result.get("ok") is True, result
        assert "missing or disabled" not in json.dumps(result)
        assert ran["called"] is True

        # Response labels written to the outcome are canonical — load it back.
        from trinity_local.council_runtime import load_council_outcome
        outcome = load_council_outcome(result["council_run_id"])
        member_providers = [m.provider for m in outcome.member_results]
        assert member_providers == ["codex", "antigravity"], member_providers
        assert "chatgpt" not in member_providers and "gemini" not in member_providers


class TestRemovedHostParams:
    """The host-member path and its three params were deleted (founder 2026-10-16): it never left
    its off-by-default flag, and its member loop had missed six fixes the main path carries. An
    old caller is refused by name, never given a normal council that also dispatches the member
    it meant to supply."""

    @pytest.mark.parametrize("args", [
        {"host_responses": [{"provider": "claude", "content": "mine"}]},
        {"host_synthesis": '{"winner": "claude"}', "responses": [{"provider": "codex", "content": "b"}]},
        {"dispatch_only": True},
        {"host_responses": []},
        {"host_synthesis": ""},
    ])
    def test_removed_params_are_refused_without_dispatching(self, home: Path, monkeypatch, args):
        import trinity_local.council_runner as CR
        import trinity_local.providers as P

        def _no_dispatch(*a, **k):
            raise AssertionError("no provider may be dispatched here")
        monkeypatch.setattr(CR, "make_provider", _no_dispatch)
        monkeypatch.setattr(P, "make_provider", _no_dispatch)
        result = _call_tool_sync("run_council", {"task": "why is the sky blue?", **args})
        assert result.get("ok") is False and "host-member council path is gone" in result["error"], result

    @pytest.mark.parametrize("args", [{"dispatch_only": False}, {"host_synthesis": None}])
    def test_an_explicit_default_is_not_refused(self, home: Path, monkeypatch, args):
        # A client still sending the old schema default must not be turned away.
        from trinity_local import mcp_server
        from trinity_local.commands import council as C
        launched = []

        def fake_launch(launch_args):
            launched.append(launch_args)
            return {"council_run_id": "council_x", "task_path": "", "sync_path": "", "review_path": ""}
        monkeypatch.setattr(C, "handle_council_launch", fake_launch)
        monkeypatch.setattr(mcp_server, "_lookup_council_status", lambda cid: None, raising=False)
        result = _call_tool_sync("run_council", {"task": "why?", **args})
        assert "host-member council path is gone" not in str(result), result
        assert launched, "a normal council should have launched"

    def test_the_schema_no_longer_offers_them(self):
        import asyncio
        from trinity_local import mcp_server
        tools = {t.name: t for t in asyncio.run(mcp_server.handle_list_tools())}
        props = tools["run_council"].inputSchema["properties"]
        assert not set(mcp_server._REMOVED_COUNCIL_PARAMS) & set(props)


def test_mcp_server_reports_trinity_version_not_mcp_lib():
    """The MCP Server must be constructed with version=APP_VERSION so its
    serverInfo reports TRINITY's version at the handshake. Without an explicit
    version=, the mcp SDK defaults serverInfo.version to the mcp LIBRARY's own
    version (e.g. 1.27.1) — a harness's MCP-server list would then mis-display
    "trinity-local v<mcp-lib-version>". Found 2026-06-01 via a real stdio
    handshake (serverInfo.version came back as the mcp lib version)."""
    from trinity_local.mcp_server import server
    from trinity_local.telemetry import APP_VERSION
    assert server.version == APP_VERSION, (
        f"MCP serverInfo.version must be Trinity's APP_VERSION ({APP_VERSION!r}), "
        f"got {server.version!r} — drop of version= regresses to the mcp lib version"
    )
    assert server.version, "server.version must be a non-empty version string"
