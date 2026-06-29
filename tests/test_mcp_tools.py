"""Tests for the 8 MCP tools (canonical 4 + v1.5 pair + in-protocol provider loop + lens-lift) and chain-mode council.

Canonical 4: route, run_council (subsumes judge via responses=[...]),
get_persona, get_council_status. (record_outcome retired 2026-05-21.)
v1.5 pair: ask, get_picks.
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
        # v1.5 adds: `ask` (single-call routing), `get_picks`
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
            "ask", "get_picks",
            "run_council", "run_eval",
            "get_persona", "get_council_status",
            "import_provider_memory", "lens_generators",
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

        assert "ESCALATION decision" in d["ask"], (
            "ask(mode='route') must frame routing as an escalation decision, not just "
            "'which provider' (founder direction #3 — reframe routing)"
        )

        assert "lens-skill" in d["get_persona"] and "SKILL.md" in d["get_persona"], (
            "get_persona must point to the ambient SKILL.md alternative so an agent "
            "knows the zero-call option exists"
        )


class TestRoute:
    """The routing-decision behaviors, exercised through `ask(mode='route')` — the
    one routing entrypoint after the standalone `route` tool was cut. The underlying
    `_route` logic is unchanged; these pin its behavior at the surviving surface."""

    @staticmethod
    def _route(task: str, **hints):
        args = {"query": task, "mode": "route"}
        if "available_models" in hints:
            args["available_providers"] = hints.pop("available_models")
        args.update(hints)
        return _call_tool_sync("ask", args)

    def test_route_returns_canonical_payload_shape(self, home: Path):
        result = self._route(
            "refactor this Python function to remove duplication",
            available_models=["claude", "antigravity", "codex"],
        )
        assert "mode" in result
        assert "primary" in result
        assert "confidence" in result
        # Coding task → codex (gpt-5.5 xhigh) wins AA Coding Index (59.1 > gemini 55.5 > claude 52.5)
        assert result["primary"] == "codex"
        assert result["chairman_source"] == "global_benchmarks"

    def test_route_handles_minimal_args(self, home: Path):
        result = self._route("anything")
        assert "primary" in result

    def test_route_honors_ranker_needs_council(self, home: Path):
        # The HeuristicRanker sets needs_council=True for coding/research tasks.
        # Before the fix, _route() looked for a nonexistent `recommended_mode`
        # field and always emitted mode='single'. After: needs_council → council.
        result = self._route(
            "refactor this Python function",
            available_models=["claude", "antigravity", "codex"],
        )
        # Coding is a needs_council task in the heuristic ranker.
        assert result["mode"] == "council"
        assert result["should_auto_council"] is True

    def test_route_normalizes_confidence_to_band(self, home: Path):
        # Schema declares confidence as enum {high, medium, low}; pre-fix it
        # leaked the raw 0..1 float from RoutingDecision.confidence.
        result = self._route("anything")
        assert result["confidence"] in ("high", "medium", "low")

    def test_route_demotes_codex_when_latency_fast(self, home: Path):
        # codex+gpt-5.5 xhigh wins coding on quality but takes 30s+. When the
        # caller asks for latency='fast', route() should pick claude or
        # antigravity (the post-rename fallback set; see mcp_server.py L819).
        result = self._route(
            "refactor this Python function",
            available_models=["claude", "antigravity", "codex"],
            latency="fast",
        )
        assert result["primary"] in ("claude", "antigravity")
        assert "latency=fast" in result["reason"]
        assert result["latency"] == "fast"

    def test_route_returns_challenger_distinct_from_primary(self, home: Path):
        result = self._route(
            "refactor this Python function",
            available_models=["claude", "antigravity", "codex"],
        )
        assert result["challenger"] != result["primary"]
        assert result["challenger"] in ("claude", "antigravity", "codex")


class TestAskRouteMode:
    """`route` merged into `ask` as `mode='route'` (#213 Q4); the standalone `route`
    tool was then REMOVED in the loop-primitive cut — `ask(mode='route')` is the one
    routing entrypoint."""

    def test_ask_mode_route_returns_routing_decision(self, home: Path):
        result = _call_tool_sync("ask", {
            "query": "refactor this Python function to remove duplication",
            "mode": "route",
            "available_providers": ["claude", "antigravity", "codex"],
        })
        # Same payload shape as the route tool — a routing decision, NOT a
        # dispatched answer (no model call in route mode).
        assert "mode" in result and "primary" in result and "confidence" in result
        assert "answer" not in result
        # Deterministic: coding → codex, exactly like the route tool.
        assert result["primary"] == "codex"

    def test_ask_schema_exposes_route_mode(self):
        from trinity_local.mcp_server import handle_list_tools
        tools = asyncio.run(handle_list_tools())
        ask = next(t for t in tools if t.name == "ask")
        mode = ask.inputSchema["properties"].get("mode", {})
        assert mode.get("enum") == ["answer", "route"]
        assert mode.get("default") == "answer"

    def test_route_uses_cortex_basin_when_one_matches(self, home: Path, monkeypatch):
        """ask(mode='route') must surface the learned cortex basin rule, not just
        the heuristic chairman pick — parity with answer-mode ask. Regression
        guard for the gap (found 2026-05-31) where _route() never consulted
        _try_cortex_route, so the routing call the MCP docs tell users to PREFER
        silently bypassed the #277 learned basins the answer path already uses.

        _route() does `from .ask import _try_cortex_route` at call time, so
        patching the attribute on the ask module reaches it."""
        import trinity_local.ask as askmod
        from trinity_local.ask import AskDecision

        def fake_cortex(query, available_providers):
            return AskDecision(
                routed_to="codex",
                trust_score=0.7,
                runner_up="claude",
                vote_counts={"codex": 6},
                evidence_prompt_ids=["bundle_x"],
                reason="cortex rule for basin 'architecture_decision' (centroid match, sim=0.49)",
            )

        monkeypatch.setattr(askmod, "_try_cortex_route", fake_cortex)
        result = _call_tool_sync("ask", {
            "query": "Should I use a monorepo or split repos for the dispatch layer?",
            "mode": "route",
            "available_providers": ["claude", "antigravity", "codex"],
        })
        # The learned basin primary drives the route — primary, reason, source,
        # and challenger must all reflect cortex (not a primary/reason mismatch).
        assert result["primary"] == "codex", (
            "cortex basin primary must drive the route primary — if it's the "
            "heuristic pick instead, the primary_pick line wasn't wired to cortex."
        )
        assert result["chairman_source"] == "cortex"
        assert "cortex rule for basin" in result["reason"]
        assert result["challenger"] == "claude", (
            "challenger should be the basin rule's challenger, not the kNN runner-up"
        )

    def test_route_falls_back_to_heuristic_when_no_basin(self, home: Path, monkeypatch):
        """When _try_cortex_route returns None (no basin matched, trust below
        floor, or no real embedder), route stays on the heuristic path with no
        regression — cortex is an override, never a gate."""
        import trinity_local.ask as askmod
        monkeypatch.setattr(askmod, "_try_cortex_route", lambda q, a: None)
        result = _call_tool_sync("ask", {
            "query": "refactor this Python function to remove duplication",
            "mode": "route",
            "available_providers": ["claude", "antigravity", "codex"],
        })
        assert result["primary"] == "codex"          # heuristic coding pick
        assert result["chairman_source"] != "cortex"  # not the learned path


class TestRunCouncilChainPropagation:
    """The verification council caught that MCP `run_council(mode='chain')` was
    silently dispatching parallel because `_run_council` didn't add `mode`/
    `sequence` to launch_args. Lock that in."""

    def test_chain_mode_threads_through_to_handle_council_launch(self, home: Path, monkeypatch):
        # Capture what handle_council_launch receives.
        captured = {}

        def _stub_handle(args):
            captured["mode"] = getattr(args, "mode", None)
            captured["sequence"] = getattr(args, "sequence", None)
            captured["members"] = list(args.members)
            # Print the JSON that the real handler would print (council_run_id).
            import json
            print(json.dumps({
                "council_run_id": "council_test_chain",
                "task_path": "/tmp/x",
                "sync_path": "/tmp/y",
                "review_path": "/tmp/z",
                "review_action_path": "/tmp/a",
            }))

        from trinity_local.commands import council as council_cmd
        monkeypatch.setattr(council_cmd, "handle_council_launch", _stub_handle)

        result = _call_tool_sync("run_council", {
            "task": "refactor",
            "members": ["claude", "codex", "antigravity"],
            "mode": "chain",
            "sequence": ["claude", "codex", "claude"],
        })
        assert result.get("ok") is True
        assert result.get("mode") == "chain"
        # The lock-in: launch_args.mode and launch_args.sequence are populated
        # so the downstream chain dispatch actually triggers.
        assert captured["mode"] == "chain"
        assert captured["sequence"] == ["claude", "codex", "claude"]


# TestRecordOutcome class removed 2026-05-21. The record_outcome
# MCP tool was retired per "we are sunsetting user ratings. Full
# retirement including MCP." The chairman's pick (routing_label.winner)
# is the supervision signal now (compute_personal_routing_table reads
# it directly from council_outcomes/). CLI council-rate followed
# on 2026-05-22 (task #134) — full rating retirement.


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

    def test_chain_step_prompt_includes_prior_outputs(self):
        from trinity_local.council_runtime import render_chain_step_prompt
        from trinity_local.council_schema import CouncilChainStep, PromptBundle

        bundle = PromptBundle(
            bundle_id="b1",
            task_cluster_id="tc1",
            task_text="design a router",
            goal="best answer",
            created_at="2026-05-03T00:00:00Z",
        )
        first_step = CouncilChainStep(
            step_index=0,
            model_provider="claude",
            output_text="My first attempt at a router design...",
        )

        # Step 0 prompt has no prior outputs
        prompt0 = render_chain_step_prompt(bundle, step_index=0, prior_steps=[])
        assert "first model in a chain" in prompt0
        assert "My first attempt" not in prompt0

        # Step 1 prompt sees claude's output
        prompt1 = render_chain_step_prompt(bundle, step_index=1, prior_steps=[first_step])
        assert "step 2 of a chain" in prompt1
        assert "My first attempt" in prompt1
        assert "from claude" in prompt1

        # Final step gets the final-step framing
        prompt_final = render_chain_step_prompt(
            bundle, step_index=1, prior_steps=[first_step], is_final=True,
        )
        assert "FINAL step" in prompt_final

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
        # dedupe=False (chain sequence): a chain legitimately revisits a provider
        # across rounds, so repeats must survive — only the brand folds.
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
            print(json.dumps({
                "council_run_id": "council_test_canon",
                "task_path": "/tmp/x", "sync_path": "/tmp/y",
                "review_path": "/tmp/z", "review_action_path": "/tmp/a",
            }))

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


class TestHostMemberCouncil:
    """The provider-side loop (flag TRINITY_HOST_CLAUDE_MEMBER): the host agent supplies the
    Claude voice in-session; Trinity dispatches ONLY the other members + synthesizes. The
    MCP-sampling-deprecation-proof path — the Claude member never touches `claude -p`."""

    def test_members_to_dispatch_excludes_host_supplied(self):
        from trinity_local.mcp_server import _members_to_dispatch

        # claude supplied by the host → Trinity dispatches only the other two. Web-era labels
        # fold to canonical slugs, so a 'chatgpt' host answer still excludes 'codex'.
        assert _members_to_dispatch(
            ["claude", "codex", "antigravity"], [{"provider": "claude", "content": "x"}]
        ) == ["codex", "antigravity"]
        assert _members_to_dispatch(
            ["claude", "codex", "antigravity"], [{"provider": "chatgpt", "content": "x"}]
        ) == ["claude", "antigravity"]

    def test_host_responses_refused_when_flag_off(self, home: Path, monkeypatch):
        monkeypatch.delenv("TRINITY_HOST_CLAUDE_MEMBER", raising=False)
        result = _call_tool_sync("run_council", {
            "task": "why is the sky blue?",
            "host_responses": [{"provider": "claude", "content": "Rayleigh scattering."}],
        })
        assert result.get("ok") is False
        assert "flag-gated OFF" in result.get("error", "")

    def test_host_member_council_does_not_dispatch_claude_and_synthesizes(self, home: Path, monkeypatch):
        """Flag ON: the Claude member comes from host_responses (never dispatched), Trinity
        dispatches only codex+antigravity, and the chairman (codex) synthesizes. make_provider
        is NEVER called for claude — the proof the Claude voice rode the session, not `-p`."""
        monkeypatch.setenv("TRINITY_HOST_CLAUDE_MEMBER", "1")

        class _FakeResult:
            stdout = ('{"winner": "claude", "runner_up": "codex", "confidence": "high", '
                      '"agreed_claims": [], "disagreed_claims": []}')
            stderr = ""

        called: list[str] = []

        class _FakeProvider:
            def __init__(self, name):
                self.name = name

            def run(self, prompt, cwd=None):
                called.append(self.name)
                return _FakeResult()

        import trinity_local.providers as providers_mod
        monkeypatch.setattr(providers_mod, "make_provider", lambda cfg: _FakeProvider(cfg.name))

        result = _call_tool_sync("run_council", {
            "task": "why is the sky blue?",
            "members": ["claude", "codex", "antigravity"],
            "primary_provider": "codex",  # non-claude chairman → claude must never be invoked
            "host_responses": [{"provider": "claude", "content": "HOST CLAUDE ANSWER"}],
        })
        assert result.get("ok") is True, result
        # THE proof: claude was never dispatched as a member nor used as chairman.
        assert "claude" not in called, called
        # codex + antigravity were dispatched as members; codex also chaired.
        assert "codex" in called and "antigravity" in called

        # The host's Claude answer is preserved verbatim in the synthesized outcome.
        from trinity_local.council_runtime import load_council_outcome
        outcome = load_council_outcome(result["council_run_id"])
        by_provider = {m.provider: m.output_text for m in outcome.member_results}
        assert by_provider.get("claude") == "HOST CLAUDE ANSWER"
        assert "codex" in by_provider and "antigravity" in by_provider

    def test_dispatch_only_returns_members_for_host_synthesis(self, home: Path, monkeypatch):
        """Flag ON + dispatch_only: Trinity dispatches the non-host members and hands the
        assembled answers + synthesis prompt BACK to the host (no chairman runs here), so the
        host can synthesize on its full plan. The proof: mode=awaiting_host_synthesis and the
        member set is complete (host claude + dispatched codex/antigravity)."""
        monkeypatch.setenv("TRINITY_HOST_CLAUDE_MEMBER", "1")

        class _FakeResult:
            stdout = "MEMBER ANSWER"
            stderr = ""

        called: list[str] = []

        class _FakeProvider:
            def __init__(self, name):
                self.name = name

            def run(self, prompt, cwd=None):
                called.append(self.name)
                return _FakeResult()

        import trinity_local.providers as providers_mod
        monkeypatch.setattr(providers_mod, "make_provider", lambda cfg: _FakeProvider(cfg.name))

        result = _call_tool_sync("run_council", {
            "task": "why is the sky blue?",
            "members": ["claude", "codex", "antigravity"],
            "host_responses": [{"provider": "claude", "content": "HOST CLAUDE ANSWER"}],
            "dispatch_only": True,
        })
        assert result.get("ok") is True, result
        # The dispatch_only branch bit — no chairman synthesis, the host gets the members back.
        assert result.get("mode") == "awaiting_host_synthesis", result
        assert "synthesis_prompt" in result and result["synthesis_prompt"], result
        # claude was never dispatched (host-supplied); codex + antigravity were.
        assert "claude" not in called, called
        assert "codex" in called and "antigravity" in called
        # All three member answers come back for the host to synthesize over.
        by_provider = {r["provider"]: r["content"] for r in result["member_responses"]}
        assert by_provider.get("claude") == "HOST CLAUDE ANSWER"
        assert "codex" in by_provider and "antigravity" in by_provider

    def test_host_synthesis_records_outcome_with_zero_chairman_calls(self, home: Path, monkeypatch):
        """Flag ON: the host already synthesized the verdict in-session, so run_council records
        the outcome with ZERO model calls — make_provider is never invoked at all. This is the
        recording leg of chairman-on-host: the chairman ran on the host's full plan, not `-p`."""
        monkeypatch.setenv("TRINITY_HOST_CLAUDE_MEMBER", "1")

        called: list[str] = []

        class _FakeProvider:
            def __init__(self, name):
                self.name = name

            def run(self, prompt, cwd=None):  # pragma: no cover - must never run
                called.append(self.name)
                raise AssertionError("chairman-on-host must not dispatch any provider")

        import trinity_local.providers as providers_mod
        monkeypatch.setattr(providers_mod, "make_provider", lambda cfg: _FakeProvider(cfg.name))

        verdict = ('{"winner": "claude", "runner_up": "codex", "confidence": "high", '
                   '"agreed_claims": [], "disagreed_claims": []}')
        result = _call_tool_sync("run_council", {
            "task": "why is the sky blue?",
            "responses": [
                {"provider": "claude", "content": "Rayleigh scattering."},
                {"provider": "codex", "content": "Short wavelengths scatter more."},
            ],
            "host_synthesis": verdict,
        })
        assert result.get("ok") is True, result
        assert result.get("mode") == "host_synthesis", result
        # THE proof: not a single provider call — the host did the synthesis.
        assert called == [], called
        assert verdict in result["synthesis_output"]

        from trinity_local.council_runtime import load_council_outcome
        outcome = load_council_outcome(result["council_run_id"])
        # Winner parsed from the host's verdict; chairman attributed to the host's lab.
        assert outcome.winner_provider == "claude", outcome.winner_provider
        assert outcome.primary_provider == "claude"

    def test_host_synthesis_refused_when_flag_off(self, home: Path, monkeypatch):
        monkeypatch.delenv("TRINITY_HOST_CLAUDE_MEMBER", raising=False)
        result = _call_tool_sync("run_council", {
            "task": "why is the sky blue?",
            "responses": [{"provider": "claude", "content": "Rayleigh scattering."}],
            "host_synthesis": '{"winner": "claude"}',
        })
        assert result.get("ok") is False
        assert "flag-gated OFF" in result.get("error", "")


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
