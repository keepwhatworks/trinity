"""Schema versioning + forward migration runner (#183).

`~/.trinity/` had no version marker; schema growth was additive-only and
relied on luck. This records a single monotonic `schema_version` for the
whole state dir (one integer — per-shape versioning is over-engineering for
an additive-only history; revisit only if a shape needs an independent
cadence) in ``~/.trinity/.trinity-version``, and walks registered migrations
forward on the first launch under a new binary. A missing marker is treated
as **v0** (every pre-versioning install).

Invoked once at process start (CLI ``main()`` covers both the bare CLI and
the ``--mcp`` server it spawns). Cheap when current: one tiny file read + an
int compare, then return. Idempotent — re-running at the same version is a
no-op. Best-effort + fail-safe: a migration that raises stops the walk at the
last success and the marker is left there, so the next launch retries from
that point (the marker never advances past a failed step), and the runner
itself never raises — a migration bug must not brick startup.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

# Bump this when you append a migration. The runner walks the recorded
# version forward to SCHEMA_VERSION, applying each contiguous migration.
SCHEMA_VERSION = 1


def _version_path():
    from .state_paths import trinity_home

    return trinity_home() / ".trinity-version"


def current_schema_version() -> int:
    """The recorded schema version, or 0 for a pre-versioning install
    (missing or malformed marker)."""
    p = _version_path()
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    v = data.get("schema_version") if isinstance(data, dict) else None
    return v if isinstance(v, int) else 0


def _write_version(v: int) -> None:
    from .utils import atomic_write_text, now_iso

    p = _version_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        p, json.dumps({"schema_version": v, "migrated_at": now_iso()}) + "\n"
    )


@dataclass(frozen=True)
class Migration:
    """One forward step. `apply` must be idempotent — the runner only calls
    it when the marker is below `to_version`, but a partial prior run (marker
    not yet advanced) means it can be re-entered, so it must add-only."""

    from_version: int
    to_version: int
    description: str
    apply: Callable[[], None]


def _migrate_v0_to_v1() -> None:
    # Seed the unified preference-act ledger from any legacy
    # rejections.jsonl / decisions.jsonl. The #209 legacy-split retirement
    # shipped no migration; this makes the recovery a first-class, run-once
    # schema step (it's idempotent — adds only missing-by-id acts — so the
    # belt-and-suspenders inline calls in lens-build/lens-resync/eval-build
    # stay harmless no-ops once this has run).
    from .me.preference_acts import _migrate_legacy_preference_stores

    _migrate_legacy_preference_stores()


MIGRATIONS: list[Migration] = [
    Migration(
        0, 1,
        "Seed unified preference_acts.jsonl ledger from legacy "
        "rejections/decisions stores (#209/#183)",
        _migrate_v0_to_v1,
    ),
]


def run_migrations() -> dict:
    """Walk registered migrations forward from the recorded version to
    SCHEMA_VERSION. Returns a small report ({from, to, applied, ok, error})
    for status/telemetry. Never raises."""
    start = current_schema_version()
    if start >= SCHEMA_VERSION:
        return {"from": start, "to": start, "applied": [], "ok": True, "error": None}

    version = start
    applied: list[int] = []
    error: str | None = None
    for m in sorted(MIGRATIONS, key=lambda x: x.from_version):
        if m.from_version != version:
            continue  # not contiguous from here yet
        try:
            m.apply()
        except Exception as exc:  # never brick startup on a migration bug
            error = f"migration v{m.from_version}→v{m.to_version} failed: {exc}"
            break
        version = m.to_version
        applied.append(version)
        if version >= SCHEMA_VERSION:
            break

    if version > start:
        try:
            _write_version(version)
        except OSError as exc:
            error = error or f"could not persist schema version: {exc}"

    return {"from": start, "to": version, "applied": applied, "ok": error is None, "error": error}
