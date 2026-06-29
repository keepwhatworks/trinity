"""Tests for provider dispatch error classification.

The killer flow for v1.5: when Claude in the harness hits a rate limit,
Trinity routes the next subtask to a different provider seamlessly. To do
that we need correct classification of CLI stderr — each provider has its
own shape on the same logical failure.

Stderr samples below come from observed CLI behavior. Adding a new provider
or noticing a new failure shape: add the pattern to dispatch_errors._PATTERNS_*
AND add a test here.
"""
from __future__ import annotations

import pytest

from trinity_local.dispatch_errors import (
    DispatchErrorKind,
    classify_dispatch_failure,
)


class TestRateLimitDetection:
    """Rate limit is the killer-flow case — must trigger retry_with_other_provider."""

    @pytest.mark.parametrize("stderr", [
        "Error: Rate limit exceeded. Try again in 30 seconds.",
        "[claude] HTTP 429 Too Many Requests",
        "throttle: backoff for 60s",
        "Daily usage limit reached for your plan",
        'ratelimit_error: "you have hit your message limit"',
        "429, retry-after: 30",
    ])
    def test_recognizes_rate_limit_phrasings(self, stderr):
        failure = classify_dispatch_failure(
            provider="claude", returncode=1, stderr=stderr,
        )
        assert failure.kind == DispatchErrorKind.RATE_LIMITED
        assert failure.retry_with_other_provider is True
        assert failure.provider == "claude"

    def test_case_insensitive(self):
        failure = classify_dispatch_failure(
            provider="codex", returncode=1, stderr="RATE LIMIT REACHED",
        )
        assert failure.kind == DispatchErrorKind.RATE_LIMITED

    def test_does_not_match_unrelated_429_substring(self):
        """A literal 4290 in some error message shouldn't trigger rate-limit.
        The pattern is `429 ` or `429,` (with delimiter)."""
        failure = classify_dispatch_failure(
            provider="claude", returncode=1, stderr="object_4290_not_found",
        )
        assert failure.kind != DispatchErrorKind.RATE_LIMITED


class TestBillingDetection:
    @pytest.mark.parametrize("stderr", [
        "Your credit balance is too low to access this feature.",
        "insufficient_quota: please add credits to continue",
        "402 Payment Required",
        "Subscription expired. Renew at https://...",
        "Out of credits",
    ])
    def test_recognizes_billing_phrasings(self, stderr):
        failure = classify_dispatch_failure(
            provider="codex", returncode=1, stderr=stderr,
        )
        assert failure.kind == DispatchErrorKind.BILLING_EXCEEDED
        assert failure.retry_with_other_provider is True


class TestAuthDetection:
    @pytest.mark.parametrize("stderr", [
        "401 Unauthorized",
        "Error: invalid_api_key — your token has been revoked",
        "Authentication failed: please log in again",
        "Session expired. Run `gemini login`.",
        "Not authenticated. Run `claude login`.",
    ])
    def test_recognizes_auth_phrasings(self, stderr):
        failure = classify_dispatch_failure(
            provider="antigravity", returncode=1, stderr=stderr,
        )
        assert failure.kind == DispatchErrorKind.AUTH_FAILED
        assert failure.retry_with_other_provider is True


class TestModelNotFoundDetection:
    @pytest.mark.parametrize("stderr", [
        "Model not found: claude-3-sonnet-20240229",
        "model_not_found: the model 'gpt-4-vision-preview' has been deprecated",
        "Unknown model: foo-bar-baz",
        "No such model: codex-bare-bones",
        "This model has been deprecated. Use 'gpt-5' instead.",
    ])
    def test_recognizes_model_phrasings(self, stderr):
        failure = classify_dispatch_failure(
            provider="claude", returncode=1, stderr=stderr,
        )
        assert failure.kind == DispatchErrorKind.MODEL_NOT_FOUND
        # NOT retry-with-other-provider — this is a config bug, not transient.
        assert failure.retry_with_other_provider is False


class TestTimeout:
    def test_explicit_timed_out_flag_wins(self):
        # Even if stderr is empty / unrelated, the timed_out flag takes
        # precedence — caller saw the wall-clock timeout fire.
        failure = classify_dispatch_failure(
            provider="claude", returncode=-1, stderr="", timed_out=True,
        )
        assert failure.kind == DispatchErrorKind.TIMEOUT
        assert failure.retry_with_other_provider is True

    def test_stderr_timeout_phrasings(self):
        failure = classify_dispatch_failure(
            provider="codex", returncode=1,
            stderr="Request canceled: deadline exceeded after 120s",
        )
        assert failure.kind == DispatchErrorKind.TIMEOUT


class TestUnknownFailure:
    def test_unrecognized_stderr_classifies_as_unknown(self):
        failure = classify_dispatch_failure(
            provider="antigravity", returncode=2,
            stderr="some weird CLI panic that doesn't fit any bucket",
        )
        assert failure.kind == DispatchErrorKind.UNKNOWN
        # Don't auto-retry on unknown — could be content policy, deterministic
        # bug, etc. Operator/agent decides.
        assert failure.retry_with_other_provider is False

    def test_empty_stderr_classifies_as_unknown(self):
        failure = classify_dispatch_failure(
            provider="claude", returncode=1, stderr="",
        )
        assert failure.kind == DispatchErrorKind.UNKNOWN


class TestSerialization:
    def test_to_dict_is_compact_and_truncates(self):
        long_stderr = "x" * 1000
        failure = classify_dispatch_failure(
            provider="claude", returncode=1, stderr=long_stderr,
        )
        payload = failure.to_dict()
        assert payload["kind"] == "unknown"
        assert payload["provider"] == "claude"
        assert payload["retry_with_other_provider"] is False
        # Excerpt is capped so it doesn't pollute Claude's context window
        # when surfaced through MCP error responses.
        assert len(payload["excerpt"]) <= 200


class TestPriorityOrdering:
    """When stderr matches multiple categories, the most-actionable wins."""

    def test_rate_limit_beats_auth_when_both_present(self):
        # Some CLIs report "rate limit reached. authentication may help" or
        # similar — rate-limit is the more actionable signal (auto-retry
        # with another provider works; auth-fix on the original doesn't).
        failure = classify_dispatch_failure(
            provider="claude", returncode=1,
            stderr="429 rate limit reached, unauthorized retry attempt",
        )
        assert failure.kind == DispatchErrorKind.RATE_LIMITED

    def test_billing_beats_auth(self):
        failure = classify_dispatch_failure(
            provider="codex", returncode=1,
            stderr="402 payment required (also: unauthorized)",
        )
        assert failure.kind == DispatchErrorKind.BILLING_EXCEEDED
