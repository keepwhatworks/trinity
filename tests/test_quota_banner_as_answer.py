"""A usage wall returned as the ANSWER is a failed member, not a weak answer.

`claude -p` does not fail when it is out of credits. It exits 0 and prints the
banner on stdout. Measured 2026-09-25: 34 banked councils carried a banner as a
member's answer, 0 recorded that member as failed, the chairman scored the
banner as a weak answer (overall 0, risk 10), and the degraded-council routing
guard -- which keys on metadata.failed_members -- never fired once.

Every banner here is CAPTURED, not imagined: the matcher's history is a string
("usage limit reached") that no CLI ever printed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trinity_local import provider_quota
from trinity_local.dispatch_errors import DispatchErrorKind, quota_banner_kind

CLAUDE_CREDITS = ("You're out of usage credits. Switch to another model, or manage "
                  "usage credits at claude.ai/settings/usage?from=cc_cli_limit_message, "
                  "to continue.")
CLAUDE_CREDITS_V2 = ("You're out of usage credits. Run /usage-credits to keep using "
                     "Fable 5 or /model to switch models.")
CODEX_LIMIT = ("You've hit your usage limit. Upgrade to Pro "
               "(https://chatgpt.com/explore/pro), visit "
               "https://chatgpt.com/codex/settings/usage to purchase more credits "
               "or try again at 4:12 AM.")
CLAUDE_SESSION = "You've hit your session limit · resets 2:10pm (America/New_York)"


@pytest.fixture(autouse=True)
def _clean_registry():
    provider_quota.clear()
    yield
    provider_quota.clear()


class TestTheMatcher:
    @pytest.mark.parametrize("banner,kind", [
        (CLAUDE_CREDITS, DispatchErrorKind.BILLING_EXCEEDED),
        (CLAUDE_CREDITS_V2, DispatchErrorKind.BILLING_EXCEEDED),
        (CODEX_LIMIT, DispatchErrorKind.RATE_LIMITED),
        (CLAUDE_SESSION, DispatchErrorKind.RATE_LIMITED),
    ])
    def test_every_captured_banner_is_recognised(self, banner, kind):
        assert quota_banner_kind(banner) is kind

    @pytest.mark.parametrize("answer", [
        "Add a rate limit of 10 requests per second at the gateway.",   # short, on topic
        "Check the quota table before the migration.",
        "Yes.",
        "",
        None,
    ])
    def test_a_short_answer_about_limits_is_still_an_answer(self, answer):
        """The stderr patterns include "rate limit" because they only ever see a
        dispatch that already failed. In an answer it is a topic."""
        assert quota_banner_kind(answer) is None

    def test_a_long_answer_quoting_a_banner_is_still_an_answer(self):
        long = ("When claude prints 'You're out of usage credits' on stdout it exits 0, "
                "which is why the runner must classify the answer text. " * 8)
        assert quota_banner_kind(long) is None


def _council(patch_trinity_home, monkeypatch, *, claude_stdout):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import ProviderResult

    chairman = ("Agreed claims\n- one\n\n```json\n"
                '{"winner":"antigravity","confidence":"medium","task_type":"comparison"}\n```\n')
    seen: dict[str, str] = {}

    class FakeProvider:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            if "synthesizer" in prompt.lower():
                seen["chairman_prompt"] = prompt
                return ProviderResult(provider=self.name, stdout=chairman,
                                      stderr="", returncode=0)
            if self.name == "claude":
                return ProviderResult(provider="claude", stdout=claude_stdout,
                                      stderr="", returncode=0)
            return ProviderResult(provider=self.name, stdout=f"{self.name} answer",
                                  stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider",
                        lambda cfg: FakeProvider(cfg.name))
    result = run_council(
        config=load_config(),
        bundle=create_prompt_bundle(task_cluster_id="cluster_banner",
                                    task_text="compare two options", goal="pick one"),
        member_providers=["claude", "antigravity"],
        primary_provider="antigravity", cwd=Path(patch_trinity_home))
    return result, seen


class TestTheRealRunner:
    """Drives run_council, not a hand-built dict. Deleting the guard in
    council_runner reds every test in this class."""

    def test_a_banner_answer_lands_in_failed_members(self, patch_trinity_home, monkeypatch):
        result, _ = _council(patch_trinity_home, monkeypatch, claude_stdout=CLAUDE_CREDITS)
        md = result.outcome.metadata
        assert "claude" in md["failed_members"], (
            "the degraded-council guard in personal_routing keys on failed_members; "
            "a banner missing from it is a 2-member council presented as 3")
        failure = next(f for f in md["member_failures"] if f["provider"] == "claude")
        assert failure["reason"] == "quota_banner_as_answer"
        assert failure["answer_text"] == CLAUDE_CREDITS, "non-destructive: keep the text"

    def test_the_chairman_never_reads_the_banner(self, patch_trinity_home, monkeypatch):
        _, seen = _council(patch_trinity_home, monkeypatch, claude_stdout=CLAUDE_CREDITS)
        assert "out of usage credits" not in seen["chairman_prompt"], (
            "the chairman scored the banner overall 0 / risk 10 and wrote it into "
            "the verdict; a failed member must not reach synthesis")

    def test_the_breaker_trips_so_the_next_council_skips_it(self, patch_trinity_home, monkeypatch):
        _council(patch_trinity_home, monkeypatch, claude_stdout=CLAUDE_CREDITS)
        assert "claude" in provider_quota.exhausted(), (
            "without the breaker every later council pays for a second banner")

    def test_a_session_limit_also_trips_the_breaker(self, patch_trinity_home, monkeypatch):
        _council(patch_trinity_home, monkeypatch, claude_stdout=CLAUDE_SESSION)
        assert "claude" in provider_quota.exhausted()

    def test_a_short_real_answer_is_counted(self, patch_trinity_home, monkeypatch):
        result, seen = _council(patch_trinity_home, monkeypatch,
                                claude_stdout="Add a rate limit of 10 rps.")
        assert "claude" not in result.outcome.metadata["failed_members"]
        assert "Add a rate limit of 10 rps." in seen["chairman_prompt"]
        assert "claude" not in provider_quota.exhausted()


class TestHistoryIsExcludedWithoutRewritingIt:
    """34 banked councils recorded a banner as an answer with failed_members=[].
    The aggregator must exclude them by reading the answers, not by trusting a
    field that was wrong when it was written."""

    def _c(self, claude_text, failed=()):
        return {"metadata": {"failed_members": list(failed)},
                "routing_label": {"winner": "codex", "task_type": "comparison",
                                  "provider_scores": {"codex": {"overall": 8},
                                                      "claude": {"overall": 0}}},
                "member_results": [{"provider": "claude", "output_text": claude_text},
                                   {"provider": "codex", "output_text": "codex answer"}]}

    def test_a_banked_banner_council_is_skipped(self):
        from trinity_local.personal_routing import aggregate_routing_table
        t = aggregate_routing_table([self._c(CLAUDE_CREDITS)])
        assert t["councils_skipped_degraded"] == 1, (
            "this counter read 0 across 1,339 councils while 34 carried a banner")

    def test_a_real_council_still_counts(self):
        from trinity_local.personal_routing import aggregate_routing_table
        t = aggregate_routing_table([self._c("Add a rate limit of 10 rps.")])
        assert t["councils_skipped_degraded"] == 0
