"""Provider dispatch error classification.

The Trinity v1.5 killer flow: when Claude (or whatever the user's harness is)
hits a rate limit, Trinity routes the next subtask to a different provider
seamlessly. To do that we need to recognize WHY a dispatch failed — not just
"it failed."

Each provider CLI has its own stderr shape on the same logical failure. This
module classifies the stderr text into a structured DispatchErrorKind so the
caller can decide:
  - rate_limited / billing_exceeded → try a different provider
  - auth_failed → surface to user; can't auto-recover
  - model_deprecated → fall back to a different model alias
  - timeout → maybe retry once, then escalate
  - unknown → bail with the raw stderr

No regexes-as-source-of-truth; the patterns are scoped to known-shape error
markers (HTTP status codes, billing keywords, specific CLI exit messages) so
we don't accidentally over-match.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DispatchErrorKind(str, Enum):
    """Classified outcomes. The names map 1:1 to recovery strategies in
    `ask.run_ask` and the documentation Claude reads to decide whether to
    retry or escalate.
    """

    RATE_LIMITED = "rate_limited"
    BILLING_EXCEEDED = "billing_exceeded"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class DispatchFailure:
    """A classified dispatch failure. `retry_with_other_provider` tells the
    caller whether automatic provider-fallback is sensible for this failure.
    """

    kind: DispatchErrorKind
    provider: str
    raw_stderr_excerpt: str
    retry_with_other_provider: bool

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "provider": self.provider,
            "retry_with_other_provider": self.retry_with_other_provider,
            "excerpt": self.raw_stderr_excerpt[:200],
        }


# Pattern markers — case-insensitive substring matches. Ordered by recovery
# strategy specificity so multi-cause stderr (rare) classifies to the most
# actionable kind.
_PATTERNS_RATE_LIMITED = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "429 ",  # HTTP status — trailing space stops accidental match in "4290..."
    "429,",
    "request limit",
    "throttle",
    "usage limit reached",
)

_PATTERNS_BILLING = (
    "billing",
    "out of credits",
    "credit balance",
    "insufficient_quota",
    "quota exceeded",
    "payment required",
    "402 ",
    "402,",
    "subscription expired",
)

_PATTERNS_AUTH = (
    "401 ",
    "401,",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",  # snake_case variant — common in SDK error codes
    "invalid token",
    "authentication failed",
    "auth failed",
    "not authenticated",
    "please log in",
    "session expired",
)

_PATTERNS_MODEL_NOT_FOUND = (
    "model not found",
    "model_not_found",
    "unknown model",
    "model does not exist",
    "no such model",
    "model has been deprecated",
)

_PATTERNS_TIMEOUT = (
    "timed out after",
    "timeout",
    "deadline exceeded",
    "request canceled",
)


def classify_dispatch_failure(
    *,
    provider: str,
    returncode: int,
    stderr: str,
    timed_out: bool = False,
) -> DispatchFailure:
    """Classify a provider dispatch failure. Returns DispatchFailure even on
    success-returncode-but-non-empty-stderr — caller decides what to do with
    UNKNOWN if returncode is 0.

    Args:
        provider: name from config (claude / codex / antigravity / ollama / mlx)
        returncode: subprocess exit code
        stderr: captured stderr text from the CLI
        timed_out: True if the caller hit our wall-clock timeout (we don't
            see CLI stderr in that case)

    Strategy: prefer the most-actionable classification when multiple
    patterns match — rate-limit > billing > auth > model > timeout.
    """
    haystack = (stderr or "").lower()

    if timed_out:
        return DispatchFailure(
            kind=DispatchErrorKind.TIMEOUT,
            provider=provider,
            raw_stderr_excerpt=stderr,
            retry_with_other_provider=True,
        )

    if _matches_any(haystack, _PATTERNS_RATE_LIMITED):
        return DispatchFailure(
            kind=DispatchErrorKind.RATE_LIMITED,
            provider=provider,
            raw_stderr_excerpt=stderr,
            retry_with_other_provider=True,
        )

    if _matches_any(haystack, _PATTERNS_BILLING):
        return DispatchFailure(
            kind=DispatchErrorKind.BILLING_EXCEEDED,
            provider=provider,
            raw_stderr_excerpt=stderr,
            retry_with_other_provider=True,
        )

    if _matches_any(haystack, _PATTERNS_AUTH):
        return DispatchFailure(
            kind=DispatchErrorKind.AUTH_FAILED,
            provider=provider,
            raw_stderr_excerpt=stderr,
            # Auth failure on one provider doesn't tell you anything about
            # the others; retry with a different provider is sensible.
            retry_with_other_provider=True,
        )

    if _matches_any(haystack, _PATTERNS_MODEL_NOT_FOUND):
        return DispatchFailure(
            kind=DispatchErrorKind.MODEL_NOT_FOUND,
            provider=provider,
            raw_stderr_excerpt=stderr,
            # Model deprecation is a config issue, not a transient one. The
            # operator needs to fix the model alias. Don't auto-retry.
            retry_with_other_provider=False,
        )

    if _matches_any(haystack, _PATTERNS_TIMEOUT):
        return DispatchFailure(
            kind=DispatchErrorKind.TIMEOUT,
            provider=provider,
            raw_stderr_excerpt=stderr,
            retry_with_other_provider=True,
        )

    return DispatchFailure(
        kind=DispatchErrorKind.UNKNOWN,
        provider=provider,
        raw_stderr_excerpt=stderr,
        # Don't auto-retry on unknown failures — could be a content-policy
        # rejection, a deterministic bug in the CLI, etc. Surface to the
        # operator. Caller can override if they're confident.
        retry_with_other_provider=False,
    )


def _matches_any(haystack: str, patterns: tuple[str, ...]) -> bool:
    return any(p in haystack for p in patterns)
