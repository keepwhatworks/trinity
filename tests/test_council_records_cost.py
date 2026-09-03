"""A council records what it cost, or records nothing — never a zero.

Plan item 1B. Measured 2026-09-03: 0 of 1,286 real council outcomes carried
usage of any kind, so cost per council was unknowable after the fact and the
verifier ledger (2A) had nothing to price against.

claude reports dollars only under `--output-format json`, which moves the answer
into a `result` field. providers.py deferred that switch on purpose ("breaks
every consumer of stdout, on the most-used provider") until it could be made
deliberately. These tests pin the deliberate version: the answer still arrives
as prose, the cost arrives beside it, and a CLI that reports nothing yields an
ABSENT usage rather than a zero that would read as free.
"""
from __future__ import annotations

import json

from trinity_local.providers import parse_claude_json, parse_codex_usage

CLAUDE_JSON = json.dumps({
    "result": "The answer, in prose.",
    "total_cost_usd": 0.0367577,
    "usage": {"input_tokens": 26, "output_tokens": 344,
              "cache_read_input_tokens": 68697, "cache_creation_input_tokens": 14071},
    "modelUsage": {"claude-opus-5": {"canonicalModel": "claude-opus-5"}},
})


class TestClaudeJson:
    def test_the_answer_comes_back_as_prose(self):
        assert parse_claude_json(CLAUDE_JSON)["text"] == "The answer, in prose.", (
            "JSON mode moves the answer into `result`; if it is not unwrapped, every "
            "consumer of stdout gets JSON instead of the member's answer"
        )

    def test_cost_and_tokens_are_captured(self):
        u = parse_claude_json(CLAUDE_JSON)["usage"]
        assert u["cost_usd"] == 0.0367577
        assert u["cache_read_tokens"] == 68697, "cache reads dominate the bill; keep them"
        assert u["source"] == "claude_json"

    def test_the_model_that_answered_is_ground_truth(self):
        assert parse_claude_json(CLAUDE_JSON)["model"] == "claude-opus-5", (
            "claude echoed NO model before this, so every trust-ledger row for it "
            "carried an assumed label with nothing behind it"
        )

    def test_prose_stdout_is_left_alone(self):
        assert parse_claude_json("Just an answer, no JSON here.") is None

    def test_a_format_change_cannot_break_dispatch(self):
        for hostile in ("", "   ", "{not json", "[]", json.dumps({"no_result": 1})):
            assert parse_claude_json(hostile) is None, (
                f"{hostile!r} must yield None so the caller keeps raw stdout"
            )


class TestCodexUsage:
    def test_the_token_total_is_read(self):
        assert parse_codex_usage("tokens used\n8,554")["total_tokens"] == 8554

    def test_no_cost_is_invented(self):
        assert parse_codex_usage("tokens used\n8,554")["cost_usd"] is None, (
            "codex states no price; multiplying tokens by an unmeasured one would "
            "be a number nobody measured"
        )

    def test_silence_is_absent_not_zero(self):
        assert parse_codex_usage("some unrelated stderr") is None
        assert parse_codex_usage(None) is None


class TestTheMemberRecordForReal:
    """Exercise the REAL stamping path, not a hand-built metadata dict.

    The first version of this class built a CouncilMemberResult directly and
    asserted its metadata round-tripped. Deleting the stamp in council_runner
    left it green — a guard that survives its own deletion is decoration. This
    runs the actual runner against a stubbed provider so the assertion fails if
    nothing threads usage from the dispatch into the record.
    """

    def test_a_council_records_what_its_member_cost(self, patch_trinity_home, monkeypatch):
        from pathlib import Path

        from trinity_local.config import load_config
        from trinity_local.council_runner import run_council
        from trinity_local.council_runtime import create_prompt_bundle
        from trinity_local.providers import ProviderError, ProviderResult

        chairman = (
            "Agreed claims\n- one\n\n```json\n"
            '{"winner":"antigravity","confidence":"medium","task_type":"comparison"}\n```\n'
        )
        SPENT = {"cost_usd": 0.164, "output_tokens": 4,
                 "cache_read_tokens": 10230, "source": "claude_json"}

        class FakeProvider:
            def __init__(self, name): self.name = name

            def run(self, prompt, cwd):
                if self.name == "antigravity":
                    # the one member that answers, and it reports a cost
                    return ProviderResult(provider="antigravity", stdout="Gemini answer",
                                          stderr="", returncode=0, usage=SPENT)
                if self.name == "claude" and "synthesizer" in prompt.lower():
                    return ProviderResult(provider="claude", stdout=chairman,
                                          stderr="", returncode=0)
                raise ProviderError(f"not installed: {self.name}")

        monkeypatch.setattr("trinity_local.council_runner.make_provider",
                            lambda cfg: FakeProvider(cfg.name))
        config = load_config()
        bundle = create_prompt_bundle(
            task_cluster_id="cluster_cost_capture",
            task_text="compare two options",
            goal="pick one",
        )
        result = run_council(config=config, bundle=bundle,
                             member_providers=["antigravity"],
                             primary_provider="claude", cwd=Path(patch_trinity_home))

        members = result.outcome.member_results
        assert members, "the answering member must be recorded"
        recorded = members[0].metadata.get("usage")
        assert recorded == SPENT, (
            "the cost the CLI reported must reach the council record; without it "
            "cost per council is unknowable after the fact, which is the gap "
            "measured on 2026-09-03 (0 of 1,286 outcomes carried usage)"
        )

    def test_a_silent_provider_leaves_usage_absent(self, patch_trinity_home, monkeypatch):
        from pathlib import Path

        from trinity_local.config import load_config
        from trinity_local.council_runner import run_council
        from trinity_local.council_runtime import create_prompt_bundle
        from trinity_local.providers import ProviderError, ProviderResult

        chairman = (
            "Agreed claims\n- one\n\n```json\n"
            '{"winner":"antigravity","confidence":"medium","task_type":"comparison"}\n```\n'
        )

        class FakeProvider:
            def __init__(self, name): self.name = name

            def run(self, prompt, cwd):
                if self.name == "antigravity":
                    return ProviderResult(provider="antigravity", stdout="Gemini answer",
                                          stderr="", returncode=0)  # reports nothing
                if self.name == "claude" and "synthesizer" in prompt.lower():
                    return ProviderResult(provider="claude", stdout=chairman,
                                          stderr="", returncode=0)
                raise ProviderError(f"not installed: {self.name}")

        monkeypatch.setattr("trinity_local.council_runner.make_provider",
                            lambda cfg: FakeProvider(cfg.name))
        result = run_council(config=load_config(),
                             bundle=create_prompt_bundle(
                                 task_cluster_id="cluster_cost_absent",
                                 task_text="compare", goal="pick"),
                             member_providers=["antigravity"],
                             primary_provider="claude", cwd=Path(patch_trinity_home))
        assert result.outcome.member_results[0].metadata.get("usage") is None, (
            "agy reports no cost; recording 0.0 would read as 'this council was free'"
        )
