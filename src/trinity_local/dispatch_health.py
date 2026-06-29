"""Per-provider failure-mode tracking — read recent dispatch outcomes and
deprioritize providers in active failure states.

When a provider is currently rate-limited, billing-exceeded, or auth-failed,
routing decisions should treat it as last-resort even if its long-term
trust score says otherwise. This module reads the dispatch_outcomes.jsonl
log (written by ask.run_ask) and surfaces a set of "currently unhealthy"
providers for the routing layer to demote.

Design choices:

- **In-process cache, short TTL.** Reading the JSONL every call is cheap
  (the file is appended-only and small) but unnecessary on bursts of asks.
  Cache for 30s.
- **Time-bounded failure windows.** A rate-limit at 9am tells you nothing
  at 11am. Each failure kind has its own decay:
    rate_limited: 10 min  (most providers reset within minutes)
    billing_exceeded: 24 hr (manual fix usually)
    auth_failed: 24 hr (manual re-auth usually)
    timeout: 5 min (transient infra)
    model_not_found: forever (config bug)
- **Threshold to call something unhealthy.** A single rate-limit hit might
  be a glitch; 3+ in the window is a sustained signal. Configurable.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass


# Cache TTL — short enough that the routing layer responds to fresh failures.
_TTL_SECONDS = 30
_cache: dict[str, tuple[float, object]] = {}


# Decay windows by failure kind: how long after a failure of that kind does
# it still count against the provider? Calibrated for the most common shapes
# of these failures across the three CLIs.
_DECAY_MINUTES: dict[str, int] = {
    "rate_limited": 10,
    "timeout": 5,
    "billing_exceeded": 24 * 60,
    "auth_failed": 24 * 60,
    "model_not_found": 7 * 24 * 60,  # essentially "until config is fixed"
    "unknown": 5,
}


@dataclass
class ProviderHealth:
    """Per-provider health snapshot from recent dispatch outcomes."""

    provider: str
    recent_failures: int  # how many failures within the relevant window
    last_failure_kind: str | None
    last_failure_at_iso: str | None
    is_unhealthy: bool  # true → routing should deprioritize

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "recent_failures": self.recent_failures,
            "last_failure_kind": self.last_failure_kind,
            "last_failure_at": self.last_failure_at_iso,
            "is_unhealthy": self.is_unhealthy,
        }


def compute_health(
    *,
    min_failures_for_unhealthy: int = 1,
    now: datetime | None = None,
) -> dict[str, ProviderHealth]:
    """Read dispatch_outcomes.jsonl, return per-provider health within the
    decay windows.

    `min_failures_for_unhealthy=1` means a single in-window failure flips the
    provider to unhealthy. For tighter SLA contexts pass a higher threshold.

    `now` injectable for tests; defaults to wall-clock UTC.
    """
    cached = _cache.get("health")
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]  # type: ignore[return-value]

    from .state_paths import dispatch_outcomes_path

    path = dispatch_outcomes_path()
    if not path.exists():
        _cache["health"] = (time.monotonic(), {})
        return {}

    now = now or datetime.now(timezone.utc)

    # Walk every entry once; aggregate per-provider. Most users will have
    # bounded log size (10s/day), so this stays cheap. If it grows we can
    # add a sidecar that maintains a sliding window.
    per_provider: dict[str, list[dict]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):  # guard_shape_not_just_parse: .get crashes on a non-dict line
                continue
            if not entry.get("failure_kind"):
                continue  # successful dispatch — not a failure record
            # The primary attempt is the one whose failure_kind got recorded.
            # If retry succeeded on a different provider, the primary is the
            # one we want to track as having had the failure.
            primary = entry.get("primary")
            if not primary:
                continue
            per_provider.setdefault(primary, []).append(entry)

    result: dict[str, ProviderHealth] = {}
    for provider, entries in per_provider.items():
        # Filter to entries within the decay window for THAT kind.
        in_window: list[dict] = []
        for e in entries:
            kind = e.get("failure_kind") or "unknown"
            window_min = _DECAY_MINUTES.get(kind, 5)
            try:
                ts = datetime.fromisoformat(str(e["ts"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if now - ts <= timedelta(minutes=window_min):
                in_window.append(e)
        if not in_window:
            continue
        # Latest first for "last failure" attributes. Tie-break on failure_kind
        # so the "last failure" attributes are deterministic when two failures
        # share a timestamp (events carry no unique id); without it which
        # same-ts event's kind is surfaced would flip on log order.
        in_window.sort(key=lambda e: (str(e.get("ts") or ""), str(e.get("failure_kind") or "")), reverse=True)
        latest = in_window[0]
        result[provider] = ProviderHealth(
            provider=provider,
            recent_failures=len(in_window),
            last_failure_kind=latest.get("failure_kind"),
            last_failure_at_iso=latest.get("ts"),
            is_unhealthy=len(in_window) >= min_failures_for_unhealthy,
        )

    _cache["health"] = (time.monotonic(), result)
    return result


def unhealthy_providers(*, min_failures: int = 1) -> set[str]:
    """Convenience: just the set of unhealthy provider names. The pool
    composition uses this to demote (not exclude) — providers stay in the
    pool but move to the end so the routing decision sees them last.
    """
    return {
        name for name, h in compute_health(min_failures_for_unhealthy=min_failures).items()
        if h.is_unhealthy
    }


def clear_health_cache() -> None:
    """Test affordance."""
    _cache.clear()


def log_member_failure(
    *,
    provider: str,
    council_run_id: str,
    failure_kind: str,
    stderr_excerpt: str = "",
) -> None:
    """Append one council-member dispatch failure to dispatch_outcomes.jsonl
    so dispatch_health.compute_health() picks it up.

    100-persona audit P46 fix: before this, council_runner caught member
    dispatch exceptions but never wrote to the outcomes log — so a rate-
    limited Codex in a council never demoted the provider, and the next
    ask routed back to the same rate-limited provider. ask.py logs
    via _log_dispatch_outcome with a `query` shape; this is the
    council-shape twin (`council_run_id` instead of query).

    Same write contract: failures swallowed (observability MUST NOT
    crash the dispatch path), same on-disk JSONL the health-reader
    expects.
    """
    try:
        from datetime import datetime, timezone

        from .state_paths import dispatch_outcomes_path

        path = dispatch_outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source": "council_member",
            "council_run_id": council_run_id,
            "primary": provider,
            "succeeded_on": None,
            "retries": 0,
            "rate_limit_save": False,
            "failure_kind": failure_kind,
            "stderr_excerpt": (stderr_excerpt or "")[:240],
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
