"""Routing scoreboard I/O — the lens-derived picks store (#298 cortex collapse).

Historically this module owned a v1.5 "cortex" engine: a per-basin
`RoutingPattern` with a 6-component `TrustScore`, a SEPARATE `basin_centroid`
stored in `picks.json`, and an LLM "flagship extractor" (+ optional `--audit`
chairman). That whole engine was inert (0 high-trust basins) and carried a
stale-centroid bug (#277: cortex centroids frozen in an old embedding space go
orthogonal to live queries → routing silently inert).

It was REPLACED (#298, council_0dd6ee69698d620b) by deriving routing from the
LENS basins. The new engine lives in `lens_routing.py`: place each council in
its nearest lens basin (topics.json's live 768-d centroids) and tally the
recency-weighted chairman winner. No trust score, no separate cortex centroids
(so #277 is structurally impossible — the only centroids are the lens's, always
in the live space), no per-basin LLM call.

What survives here is the picks-store I/O: `load_routing_patterns` /
`save_routing_patterns` (read/write `~/.trinity/scoreboard/picks.json`, with the
#194 clobber guard) and the `cortex_routing_patterns_path` alias. The
staleness primitives went with the `consolidate` verb on 2026-08-11 — nothing
can re-consolidate picks.json, so "these picks are stale" had no remedy to
offer. The picks-store schema is the flat lens-basin tally
`{basin_id: {winner, count, margin, n_episodes, evidence}}`, now inert.
"""
from __future__ import annotations

import json

from .state_paths import cortex_routing_patterns_path


def load_routing_patterns() -> dict[str, dict]:
    """Read the lens-derived routing picks from `~/.trinity/scoreboard/picks.json`.
    Empty dict if file doesn't exist.

    POST-COLLAPSE schema (#298): each value is the simple lens-basin tally
    `{winner, count, margin, n_episodes, evidence}` — NOT the old cortex
    RoutingPattern (6-component trust_score + basin_centroid). Placement at query
    time reads the LENS basin centroids (topics.json), so picks.json carries no
    centroids and the #277 stale-space failure is structurally impossible.
    Malformed/legacy entries (old RoutingPattern dicts) load as-is; callers read
    `.get("winner")` so a legacy entry simply yields no winner and is skipped."""
    path = cortex_routing_patterns_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}  # corrupted-but-parseable wrong shape (list/str) → no patterns
    return {bid: raw for bid, raw in data.items() if isinstance(raw, dict)}


def save_routing_patterns(
    patterns: dict[str, dict], *, allow_shrink: bool = False
) -> None:
    """Write the lens-derived routing picks to `~/.trinity/scoreboard/picks.json`
    atomically. (Function name preserved; file path moved during pre-launch
    migrations — see `load_routing_patterns()` for lineage.)

    POST-COLLAPSE (#298): values are plain dicts
    (`{winner, count, margin, n_episodes, evidence}`) from
    `lens_routing.compute_basin_routing` — no `RoutingPattern.to_dict()` step.

    Carries the #194 clobber guard: refuse to overwrite a populated picks
    store with a cliff-drop (empty when >= _CLOBBER_MIN_EXISTING patterns
    exist, or below _CLOBBER_MIN_FRACTION of the existing count). A
    consolidation that produced no patterns (every basin fell below
    min_count, or the lens hasn't been built) would otherwise erase the live
    routing scoreboard — the chairman's accumulated picks. The live file is
    preserved and the would-be result lands in a `.degenerate` sidecar.
    `allow_shrink=True` is the escape hatch."""
    from .utils import atomic_write_text
    from .me.turn_pairs import (
        _CLOBBER_MIN_EXISTING,
        _CLOBBER_MIN_FRACTION,
        DegenerateExtractionError,
    )
    path = cortex_routing_patterns_path()
    serialized = {basin_id: dict(p) for basin_id, p in patterns.items()}
    existing = len(load_routing_patterns())
    floor = max(1, int(existing * _CLOBBER_MIN_FRACTION))
    if not allow_shrink and existing >= _CLOBBER_MIN_EXISTING and len(patterns) < floor:
        sidecar = path.parent / (path.name + ".degenerate")
        try:
            sidecar.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
        except OSError:
            pass
        raise DegenerateExtractionError(
            f"Refusing to overwrite {existing} routing patterns with "
            f"{len(patterns)} (cliff-drop below {floor}). Live picks "
            f"preserved; degenerate result written to "
            f"{sidecar.name}. Pass allow_shrink=True only if the basin set "
            f"genuinely shrank."
        )
    atomic_write_text(path, json.dumps(serialized, indent=2))






