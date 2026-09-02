"""Which providers have hit a usage wall, for the life of this process.

WHY THIS EXISTS. Provider quota exhaustion killed three measurement runs in one
week (res_081, res_098, res_112) and each time the loop kept dispatching into a
wall it had already hit. The experiment harness grew a circuit breaker; the
PRODUCT never did. A user on a subscription hits the identical wall, and today
every council re-dispatches to the exhausted provider and reports "member
failed" with a raw stderr excerpt — which reads as a Trinity bug rather than as
"you are out of quota until 4:12 AM".

SCOPE IS THE PROCESS, DELIBERATELY. A CLI council is one-shot, so this changes
nothing there; the long-lived `--mcp` server is where several councils share a
process and where re-dispatching into a known wall actually costs something.
Nothing is persisted: a reset time is short-lived, and a stale skip written to
disk would silently drop a provider that had already recovered.

THE SKIP IS NEVER SILENT. Trinity's recurring defect is a green over degenerate
data, and "quietly consulted two of three models" is that same shape. Every
caller that skips MUST surface the reason; `council_runner` records it in
`metadata.failed_members` + the member failure payload, and `status` prints it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

# How long a provider stays marked when the CLI did not state a reset time.
# Conservative on purpose: too long silently drops a recovered provider, and the
# cost of being wrong the other way is one wasted dispatch.
_BLIND_TTL = timedelta(minutes=45)


@dataclass(frozen=True)
class Exhaustion:
    provider: str
    marked_at: datetime
    retry_after: str | None  # the CLI's own words, e.g. "4:12 AM"; None if unstated
    kind: str                # the DispatchErrorKind that caused it

    def describe(self) -> str:
        when = f" until {self.retry_after}" if self.retry_after else ""
        return f"{self.provider}: usage limit reached{when}"


_LOCK = threading.Lock()
_STATE: dict[str, Exhaustion] = {}


def mark_exhausted(provider: str, *, kind: str, retry_after: str | None = None,
                   now: datetime | None = None) -> Exhaustion:
    """Record that `provider` reported a usage wall. Idempotent per provider."""
    entry = Exhaustion(provider=provider, marked_at=now or datetime.now(),
                       retry_after=retry_after, kind=kind)
    with _LOCK:
        _STATE[provider] = entry
    return entry


def is_exhausted(provider: str, *, now: datetime | None = None) -> bool:
    """True while `provider` should be skipped.

    A stated reset time is not parsed into a datetime — the banner carries no
    date or timezone, so that would be a guess wearing the costume of a fact.
    The blind TTL governs either way; a stated time only makes the DISCLOSURE
    specific.
    """
    now = now or datetime.now()
    with _LOCK:
        entry = _STATE.get(provider)
        if entry is None:
            return False
        if now - entry.marked_at >= _BLIND_TTL:
            del _STATE[provider]
            return False
        return True


def exhausted(*, now: datetime | None = None) -> dict[str, Exhaustion]:
    """Every currently-skipped provider. Expired entries are dropped first, so
    a caller never renders a wall that has already lifted."""
    now = now or datetime.now()
    for name in list(_STATE):
        is_exhausted(name, now=now)
    with _LOCK:
        return dict(_STATE)


def clear(provider: str | None = None) -> None:
    """Forget one provider or all of them. Used by tests and by an explicit
    user retry; never called automatically, because an automatic clear would
    re-dispatch into the wall this module exists to avoid."""
    with _LOCK:
        if provider is None:
            _STATE.clear()
        else:
            _STATE.pop(provider, None)
