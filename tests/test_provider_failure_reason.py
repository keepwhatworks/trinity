"""`describe_provider_failure` — honest degradation: a failed dispatch (rc!=0 /
empty stdout) is turned into ONE clean human reason instead of a raw multi-line
CLI banner.

The live example (2026-06-09): codex's usage cap. `codex exec` prints a 10-line
startup banner to stderr THEN the real error — so a council member's failure detail
or an eval judge's score reason used to read as that whole blob (or a generic
'rc=1'). This names the cause: 'usage limit reached — resets Jun 12th, 2026 11:25 PM'.
"""
from __future__ import annotations

from unittest.mock import patch

# A representative codex usage-cap output (real banner shape, sanitized: the
# workdir + session id are placeholders). The cause is buried under the banner.
CODEX_USAGE_LIMIT = (
    "Reading additional input from stdin...\n"
    "OpenAI Codex v0.133.0\n"
    "--------\n"
    "workdir: /Users/you/projects/trinity-local\n"
    "model: gpt-5.5\n"
    "provider: openai\n"
    "approval: never\n"
    "sandbox: workspace-write [workdir, /tmp, $TMPDIR]\n"
    "reasoning effort: xhigh\n"
    "session id: 00000000-0000-7000-8000-000000000000\n"
    "--------\n"
    "user\n"
    "Reply with exactly: OK\n"
    "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again "
    "at Jun 12th, 2026 11:25 PM."
)


class TestDescribeProviderFailure:
    def test_codex_usage_limit_names_cause_and_reset(self):
        from trinity_local.providers import describe_provider_failure
        msg = describe_provider_failure("", CODEX_USAGE_LIMIT, 1, provider="codex")
        assert "usage limit reached" in msg
        assert "Jun 12th, 2026 11:25 PM" in msg
        assert msg.startswith("codex ")
        # the banner noise must NOT leak into the reason
        assert "session id" not in msg
        assert "workdir" not in msg
        assert "OpenAI Codex" not in msg

    def test_rate_limit_phrasing_also_recognized(self):
        from trinity_local.providers import describe_provider_failure
        msg = describe_provider_failure("", "Error: rate limit exceeded, retry later", 1)
        assert "usage limit reached" in msg

    def test_auth_failure_recognized(self):
        from trinity_local.providers import describe_provider_failure
        msg = describe_provider_failure("", "Error: not logged in. Please run `claude login`.", 1)
        assert "not authenticated" in msg

    def test_generic_error_line_surfaced_over_banner(self):
        from trinity_local.providers import describe_provider_failure
        out = "OpenAI Codex v0.133.0\nworkdir: /\nERROR: model overloaded, try later"
        msg = describe_provider_failure("", out, 1)
        assert "model overloaded" in msg
        assert "OpenAI Codex" not in msg

    def test_empty_output_falls_back_to_exit_code(self):
        from trinity_local.providers import describe_provider_failure
        msg = describe_provider_failure("", "", 137, provider="agy")
        assert "137" in msg


class TestScorerSurfacesCause:
    def test_judge_usage_limit_reason_keeps_degenerate_prefix(self, patch_trinity_home):
        """A quota-capped judge must STILL be suppressed (#246 — the reason starts
        with a _DEGENERATE_REASONS prefix) AND name WHY (usage limit + reset)."""
        from trinity_local.evals.runner import EvalRunResult, EvalItemRun
        from trinity_local.evals.scorer import score_run, _DEGENERATE_REASONS

        run = EvalRunResult(
            eval_id="e", target_provider="claude", target_model="claude-fable-5",
            started_at="2026-06-09T00:00:00", completed_at="2026-06-09T00:00:00",
            items_total=1, items_completed=1, items_failed=0,
            items=[EvalItemRun(
                eval_item_id="i1", rejection_type="REFRAME", prompt="p",
                rejected_response="r", user_substitute="u", rubric_signal="s",
                basin_id=None, target_response="answer", target_error=None,
                elapsed_seconds=0.1,
            )],
        )

        class QuotaCappedJudge:
            def run(self, prompt, cwd):
                from trinity_local.providers import ProviderResult
                return ProviderResult(
                    provider="codex", stdout="", stderr=CODEX_USAGE_LIMIT,
                    returncode=1, elapsed_seconds=0.1,
                )

        def _cfg(name):
            from trinity_local.config import ProviderConfig
            return ProviderConfig(name=name, type="cli", enabled=True, label=name,
                                  command=[name], args=[], task_types=set(), model="m")

        with patch("trinity_local.evals.scorer.make_provider", return_value=QuotaCappedJudge()):
            score_run(run, "lens", "codex", {"codex": _cfg("codex")})

        reason = run.items[0].score_reason
        assert reason is not None
        assert reason.startswith(_DEGENERATE_REASONS)          # still suppressed (#246)
        assert "usage limit reached" in reason                  # but says WHY
        assert "Jun 12th, 2026 11:25 PM" in reason
        assert run.scoring_degraded is True
        assert run.aggregate_score is None
