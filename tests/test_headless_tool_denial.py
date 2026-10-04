"""A headless tool denial is a failed member, not Gemini's answer.

`agy -p` reaches for a tool whenever a prompt mentions a file. Headless mode
cannot prompt for the permission, so the tool is auto-denied: stdout empty, the
reason on stderr, exit 0. The runner read `stdout or stderr` and recorded the
denial as the answer. Measured 2026-09-28: 32 banked member answers, all exit 0,
none marked failed; 8 of 11 councils on 2026-09-27 lost Gemini this way.

Live A/B the same day, one file-mentioning prompt: 0/3 answered without a
no-tools instruction, 3/3 with it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trinity_local import provider_quota
from trinity_local.dispatch_errors import headless_tool_denial

DENIAL = ('jetski: no output produced — a tool required the "command" permission that '
          'headless mode cannot prompt for, so it was auto-denied. Add an allow-rule under '
          'permissions.allow in settings.json (e.g. command(<target>)). Alternatively, re-run '
          'with --dangerously-skip-permissions to auto-approve all tools.')
DENIAL_READ = DENIAL.replace('"command"', '"read_file"')


@pytest.fixture(autouse=True)
def _clean():
    provider_quota.clear()
    yield
    provider_quota.clear()


class TestTheMatcher:
    @pytest.mark.parametrize("stderr", [DENIAL, DENIAL_READ])
    def test_both_captured_denials_match(self, stderr):
        assert headless_tool_denial("", stderr) is True

    def test_a_real_answer_is_never_a_denial(self):
        assert headless_tool_denial("The fix is correct.", DENIAL) is False

    def test_an_ordinary_empty_answer_is_not_this_failure(self):
        assert headless_tool_denial("", "") is False
        assert headless_tool_denial("", "some unrelated warning") is False


def _council(patch_trinity_home, monkeypatch, *, gemini):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import ProviderResult

    chairman = ("Agreed claims\n- one\n\n```json\n"
                '{"winner":"codex","confidence":"medium","task_type":"comparison"}\n```\n')
    seen: dict[str, str] = {}

    class FakeProvider:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            if "synthesizer" in prompt.lower():
                seen["chairman"] = prompt
                return ProviderResult(provider=self.name, stdout=chairman, stderr="", returncode=0)
            seen[self.name] = prompt
            if self.name == "antigravity":
                return ProviderResult(provider="antigravity", stdout=gemini[0],
                                      stderr=gemini[1], returncode=0)
            return ProviderResult(provider=self.name, stdout=f"{self.name} answer",
                                  stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider",
                        lambda cfg: FakeProvider(cfg.name))
    result = run_council(
        config=load_config(),
        bundle=create_prompt_bundle(task_cluster_id="cluster_denial",
                                    task_text="review src/app.py", goal="pick one"),
        member_providers=["antigravity", "codex"],
        primary_provider="codex", cwd=Path(patch_trinity_home))
    return result, seen


class TestTheRealRunner:
    def test_a_denial_is_a_failed_member(self, patch_trinity_home, monkeypatch):
        result, _ = _council(patch_trinity_home, monkeypatch, gemini=("", DENIAL))
        md = result.outcome.metadata
        assert "antigravity" in md["failed_members"]
        f = next(x for x in md["member_failures"] if x["provider"] == "antigravity")
        assert f["reason"] == "headless_tool_denied"

    def test_the_chairman_never_reads_the_denial(self, patch_trinity_home, monkeypatch):
        _, seen = _council(patch_trinity_home, monkeypatch, gemini=("", DENIAL))
        assert "headless mode cannot prompt" not in seen["chairman"]

    def test_a_denial_does_not_trip_the_quota_breaker(self, patch_trinity_home, monkeypatch):
        """Not a usage wall: the next council must try Gemini again."""
        _council(patch_trinity_home, monkeypatch, gemini=("", DENIAL))
        assert "antigravity" not in provider_quota.exhausted()

    def test_gemini_is_told_not_to_use_tools(self, patch_trinity_home, monkeypatch):
        _, seen = _council(patch_trinity_home, monkeypatch, gemini=("an answer", ""))
        assert seen["antigravity"].startswith("Answer from the text of this prompt only.")

    def test_other_members_prompts_are_untouched(self, patch_trinity_home, monkeypatch):
        _, seen = _council(patch_trinity_home, monkeypatch, gemini=("an answer", ""))
        assert "Do not run commands" not in seen["codex"]

    def test_a_real_gemini_answer_still_counts(self, patch_trinity_home, monkeypatch):
        result, seen = _council(patch_trinity_home, monkeypatch, gemini=("Gemini's view", ""))
        assert "antigravity" not in result.outcome.metadata["failed_members"]
        assert "Gemini's view" in seen["chairman"]


class TestHistory:
    def test_a_banked_denial_council_is_excluded_from_routing(self):
        from trinity_local.personal_routing import aggregate_routing_table
        c = {"metadata": {"failed_members": []},
             "routing_label": {"winner": "codex", "task_type": "comparison"},
             "member_results": [{"provider": "antigravity", "output_text": DENIAL},
                                {"provider": "codex", "output_text": "codex answer"}]}
        assert aggregate_routing_table([c])["councils_skipped_degraded"] == 1


TIMEOUT = "[agy] print timeout after 5m0s with turn in progress; returning partial output"


class TestAnEmptyAnswerIsNeverAnAnswer:
    """Over 914 banked member results, every stderr-sourced "answer" was a
    failure: 32 denials and 2 of this timeout, which returned no output."""

    def test_the_timeout_is_a_failed_member(self, patch_trinity_home, monkeypatch):
        result, seen = _council(patch_trinity_home, monkeypatch, gemini=("", TIMEOUT))
        f = next(x for x in result.outcome.metadata["member_failures"]
                 if x["provider"] == "antigravity")
        assert f["reason"] == "empty_answer"
        assert "print timeout" not in seen["chairman"]

    def test_silence_with_no_stderr_is_a_failed_member(self, patch_trinity_home, monkeypatch):
        result, _ = _council(patch_trinity_home, monkeypatch, gemini=("   \n", ""))
        assert "antigravity" in result.outcome.metadata["failed_members"]


def test_routing_excludes_any_recorded_answer_that_did_not_come_from_stdout():
    from trinity_local.personal_routing import aggregate_routing_table
    c = {"metadata": {"failed_members": []},
         "routing_label": {"winner": "codex", "task_type": "comparison"},
         "member_results": [{"provider": "antigravity", "output_text": TIMEOUT,
                             "metadata": {"stdout": "", "stderr": TIMEOUT}},
                            {"provider": "codex", "output_text": "codex answer",
                             "metadata": {"stdout": "codex answer"}}]}
    assert aggregate_routing_table([c])["councils_skipped_degraded"] == 1
