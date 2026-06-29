"""Tests for consensus-iteration chain mode (#35)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trinity_local.council_runtime import (
    chairman_says_converged,
    create_council_outcome,
    create_prompt_bundle,
    render_consensus_round_prompt,
    save_council_outcome,
    save_prompt_bundle,
)
from trinity_local.council_schema import (
    CouncilMemberResult,
    CouncilOutcome,
    CouncilRoutingLabel,
    PromptBundle,
)


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


class TestChairmanConvergence:
    def test_no_label_means_not_converged(self):
        assert chairman_says_converged(None) is False

    def test_low_confidence_not_converged(self):
        label = CouncilRoutingLabel(
            winner="claude", confidence="low",
            agreed_claims=["x", "y"], disagreed_claims=[],
        )
        assert chairman_says_converged(label) is False

    def test_medium_confidence_not_converged(self):
        label = CouncilRoutingLabel(
            winner="claude", confidence="medium",
            agreed_claims=["x", "y"], disagreed_claims=[],
        )
        assert chairman_says_converged(label) is False

    def test_disagreement_means_not_converged(self):
        label = CouncilRoutingLabel(
            winner="claude", confidence="high",
            agreed_claims=["x"],
            disagreed_claims=[{"claim": "z", "providers_for": ["claude"], "providers_against": ["antigravity"]}],
        )
        assert chairman_says_converged(label) is False

    def test_no_agreed_claims_not_converged(self):
        label = CouncilRoutingLabel(
            winner="claude", confidence="high",
            agreed_claims=[], disagreed_claims=[],
        )
        assert chairman_says_converged(label) is False

    def test_high_confidence_with_agreement_converged(self):
        label = CouncilRoutingLabel(
            winner="claude", confidence="high",
            agreed_claims=["x", "y", "z"], disagreed_claims=[],
        )
        assert chairman_says_converged(label) is True


class TestRenderConsensusRoundPrompt:
    def _bundle(self) -> PromptBundle:
        return PromptBundle(
            bundle_id="b1",
            task_cluster_id="tc1",
            task_text="Design a model router for a council product.",
            goal="Find the strongest answer.",
        )

    def test_includes_own_prior_and_others(self):
        prompt = render_consensus_round_prompt(
            self._bundle(),
            round_index=1,
            own_provider="claude",
            own_prior_output="Claude's first answer.",
            other_outputs=[("antigravity", "Gemini's first answer."), ("codex", "Codex's first answer.")],
        )
        assert "round 2" in prompt
        assert "Your prior-round answer:" in prompt
        assert "Claude's first answer." in prompt
        assert "[antigravity] said:" in prompt
        assert "[codex] said:" in prompt

    def test_user_refinement_appears_as_directive(self):
        prompt = render_consensus_round_prompt(
            self._bundle(),
            round_index=2,
            own_provider="claude",
            own_prior_output="x",
            other_outputs=[("antigravity", "y")],
            user_refinement="Make the answer shorter and more specific.",
        )
        assert "ADDITIONAL USER DIRECTIVE" in prompt
        assert "Make the answer shorter and more specific." in prompt

    def test_no_user_refinement_omits_directive_block(self):
        prompt = render_consensus_round_prompt(
            self._bundle(),
            round_index=1,
            own_provider="claude",
            own_prior_output="x",
            other_outputs=[("antigravity", "y")],
        )
        assert "ADDITIONAL USER DIRECTIVE" not in prompt


def _make_parent_outcome(home: Path) -> CouncilOutcome:
    bundle = create_prompt_bundle(
        task_cluster_id="tc1",
        task_text="Compare model routing approaches.",
        goal="Find the strongest answer.",
        origin_provider="test",
    )
    save_prompt_bundle(bundle)
    members = [
        CouncilMemberResult(provider="claude", model="claude-x", output_text="Claude says use heuristic + k-NN."),
        CouncilMemberResult(provider="antigravity", model="gemini-x", output_text="Gemini says use embeddings only."),
    ]
    label = CouncilRoutingLabel(
        winner="claude", confidence="medium", task_type="system_design",
        agreed_claims=["both prefer hybrid"],
        disagreed_claims=[{"claim": "k-NN size", "providers_for": ["claude"], "providers_against": ["antigravity"]}],
        provider_scores={"claude": {"overall": 7.0}, "antigravity": {"overall": 6.0}},
    )
    outcome = create_council_outcome(
        bundle=bundle,
        primary_provider="claude",
        member_results=members,
        primary_model="claude-x",
        winner_provider="claude",
        synthesis_output="memo",
        routing_label=label,
        metadata={"round_number": 1, "chain_root_id": None},
    )
    save_council_outcome(outcome)
    return outcome


class TestRunConsensusRound:
    def test_round_increments_and_links_to_parent(self, home: Path, monkeypatch):
        from trinity_local.council_runner import run_consensus_round

        parent = _make_parent_outcome(home)
        round2_chairman_synthesis = """## Winner
- Provider: claude
- Confidence: high

```routing-json
{"winner":"claude","runner_up":"antigravity","confidence":"high","task_type":"system_design",
 "agreed_claims":["use hybrid","cap k-NN size","add fallback"],"disagreed_claims":[],
 "provider_scores":{"claude":{"overall":9},"antigravity":{"overall":8}}}
```
"""

        class R:
            def __init__(self, stdout): self.stdout = stdout; self.stderr = ""; self.returncode = 0

        class P:
            def __init__(self, name): self.name = name
            def run(self, prompt, cwd):
                if self.name == "claude" and "synthesizer" in prompt.lower():
                    return R(round2_chairman_synthesis)
                return R("(refined output)")

        monkeypatch.setattr(
            "trinity_local.council_runner.make_provider",
            lambda cfg: P(cfg.name),
        )
        from trinity_local.config import AppConfig, ProviderConfig

        def _pc(name: str, model: str) -> ProviderConfig:
            return ProviderConfig(
                name=name, type="cli", enabled=True, label=name,
                command=["echo"], args=[], task_types=set(),
                model=model,
            )

        config = AppConfig(
            max_turns=10,
            notifications=False,
            providers={"claude": _pc("claude", "claude-x"), "antigravity": _pc("antigravity", "gemini-x")},
            task_preferences={},
        )
        result = run_consensus_round(
            config=config,
            parent_outcome=parent,
            user_refinement=None,
        )
        outcome = result.outcome
        assert outcome.metadata["parent_council_id"] == parent.council_run_id
        assert outcome.metadata["round_number"] == 2
        # chain_root_id is the parent's bundle_id, NOT council_run_id.
        # This makes ?thread_id=bundle_X URLs find the manifest for the
        # whole iteration chain, including consensus rounds that re-derive
        # the same bundle_id from (task_cluster + task_text).
        assert outcome.metadata["chain_root_id"] == parent.bundle_id
        assert outcome.mode == "consensus_round"
        assert outcome.routing_label is not None
        assert chairman_says_converged(outcome.routing_label) is True
        assert {m.provider for m in outcome.member_results} == {"claude", "antigravity"}

    def test_user_refinement_propagates_to_metadata(self, home: Path, monkeypatch):
        from trinity_local.council_runner import run_consensus_round

        parent = _make_parent_outcome(home)

        # iter #106 strict contract: save_council_outcome refuses outcomes
        # without a parsed routing_label, so the chairman synthesis must
        # carry a routing-json block. Match the chairman-stub pattern in
        # test_round_increments_and_links_to_parent: detect the synthesizer
        # prompt and return routing-json; members get a bland refinement.
        chairman_synthesis = """## Winner
- Provider: claude
- Confidence: medium

```routing-json
{"winner":"claude","confidence":"medium","task_type":"system_design"}
```
"""

        class R:
            def __init__(self, stdout="ok"):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = 0

        class P:
            def __init__(self, name): self.name = name
            def run(self, prompt, cwd):
                if self.name == "claude" and "synthesizer" in prompt.lower():
                    return R(chairman_synthesis)
                return R()
        monkeypatch.setattr("trinity_local.council_runner.make_provider", lambda c: P(c.name))
        from trinity_local.config import AppConfig, ProviderConfig

        def _pc(name: str, model: str) -> ProviderConfig:
            return ProviderConfig(
                name=name, type="cli", enabled=True, label=name,
                command=["echo"], args=[], task_types=set(),
                model=model,
            )

        config = AppConfig(
            max_turns=10,
            notifications=False,
            providers={"claude": _pc("claude", "claude-x"), "antigravity": _pc("antigravity", "gemini-x")},
            task_preferences={},
        )
        result = run_consensus_round(
            config=config,
            parent_outcome=parent,
            user_refinement="Make it shorter.",
        )
        assert result.outcome.metadata["user_refinement"] == "Make it shorter."


class TestAutoChainCouncil:
    def test_stops_when_chairman_already_converged(self, home: Path):
        from trinity_local.council_runner import auto_chain_council

        parent = _make_parent_outcome(home)
        parent.routing_label = CouncilRoutingLabel(
            winner="claude", confidence="high",
            agreed_claims=["x", "y"], disagreed_claims=[],
        )
        results = auto_chain_council(
            config=SimpleNamespace(providers={}),
            initial_outcome=parent,
            max_rounds=3,
        )
        assert results == []

    def test_max_rounds_zero_runs_nothing(self, home: Path):
        from trinity_local.council_runner import auto_chain_council
        parent = _make_parent_outcome(home)
        results = auto_chain_council(
            config=SimpleNamespace(providers={}),
            initial_outcome=parent,
            max_rounds=0,
        )
        assert results == []


class TestDispatchRegistryChainActions:
    def test_council_iterate_canonical_action(self):
        # Iter-3 added `council_iterate` as the canonical action name; the
        # three legacy aliases (continue/refine/auto_chain) stay as shims.
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        action = make_dispatch_action(
            "council_iterate",
            args={"council_id": "abc123", "rounds": 5, "prompt": "tighten the eval"},
        )
        cmd = command_for_dispatch(action)
        assert cmd is not None
        assert "council-iterate" in cmd
        assert "--rounds 5" in cmd
        assert "--prompt" in cmd
        assert "tighten the eval" in cmd

    def test_council_iterate_default_rounds_is_one(self):
        # Default `rounds` for canonical action is 1 — that's the launchpad's
        # "continue" semantic, which iter-2 fixed to skip the convergence gate.
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        action = make_dispatch_action("council_iterate", args={"council_id": "abc123"})
        cmd = command_for_dispatch(action)
        assert cmd is not None
        assert "--rounds 1" in cmd

    def test_council_continue_command(self):
        # Action names are kept stable; the underlying CLI is now `council-iterate`.
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        action = make_dispatch_action("council_continue", args={"council_id": "abc123"})
        cmd = command_for_dispatch(action)
        assert cmd is not None
        assert "council-iterate" in cmd
        assert "--rounds 1" in cmd
        assert "abc123" in cmd
        # Chain dispatches no longer pass --open-browser. The live council
        # page handles completion in-place by polling status_token; auto-opening
        # the review URL would spawn a duplicate tab on top of it.
        assert "--open-browser" not in cmd

    def test_council_refine_command(self):
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        action = make_dispatch_action(
            "council_refine",
            args={"council_id": "abc123", "prompt": "make it shorter"},
        )
        cmd = command_for_dispatch(action)
        assert cmd is not None
        assert "council-iterate" in cmd
        assert "--rounds 1" in cmd
        assert "--prompt" in cmd
        assert "make it shorter" in cmd

    def test_council_auto_chain_command(self):
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        action = make_dispatch_action(
            "council_auto_chain",
            args={"council_id": "abc123", "max_rounds": 5},
        )
        cmd = command_for_dispatch(action)
        assert cmd is not None
        assert "council-iterate" in cmd
        assert "--rounds 5" in cmd
        # No --prompt flag on auto-iterate path.
        assert "--prompt" not in cmd

    def test_council_continue_returns_none_without_id(self):
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        action = make_dispatch_action("council_continue", args={})
        assert command_for_dispatch(action) is None

    def test_council_refine_requires_both_id_and_prompt(self):
        from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
        a1 = make_dispatch_action("council_refine", args={"council_id": "x"})
        assert command_for_dispatch(a1) is None
        a2 = make_dispatch_action("council_refine", args={"prompt": "x"})
        assert command_for_dispatch(a2) is None


# TestAutoChainSettings was removed 2026-05-17 with the auto-chain
# setting retirement. Users click the auto-chain button on the council
# review page; no global setting to toggle.


class TestCouncilReviewChainCard:
    def test_continue_and_auto_chain_buttons_appear(self, home: Path):
        from trinity_local.council_review import write_unified_council_page
        from trinity_local.council_runtime import load_prompt_bundle
        from trinity_local.state_paths import review_pages_dir

        outcome = _make_parent_outcome(home)
        bundle = load_prompt_bundle(outcome.bundle_id)
        write_unified_council_page(bundle, outcome)
        # The per-outcome file is now a redirect; the chain UI lives on the
        # shared unified page that the redirect points at.
        html = (review_pages_dir() / "live_council.html").read_text(encoding="utf-8")
        assert "Continue the thread" in html
        assert "Continue (one round)" in html
        assert "Auto-chain" in html
        assert "Refine" in html
        assert "chain-refine-input" in html
        assert "council_refine" in html
