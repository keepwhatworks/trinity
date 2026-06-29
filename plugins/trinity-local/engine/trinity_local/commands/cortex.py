"""`trinity-local consolidate` — rebuild the routing scoreboard (#298 collapse).

A cheap, deterministic, LLM-FREE pass: place each real-contest council into its
nearest lens basin (topics.json's live 768-d centroids) and tally the
recency-weighted chairman winner per basin. Writes
~/.trinity/scoreboard/picks.json (the underlying `cortex_routing_patterns_path()`
is a back-compat alias for `picks_path()` — data lineage was
`cortex/routing_patterns.json` → `memories/picks.json` → `scoreboard/picks.json`,
auto-migrated by `_migrate_legacy_memory_paths` + `_migrate_legacy_scoreboard_paths`).

REPLACED the old v1.5 cortex pass: the flagship-LLM rule extractor, the
6-component trust score, the chairman `--audit` pass, and the SEPARATE cortex
centroids (#277) are all gone. `ask` routes on the result via the same lens
centroids, so the stale-embedding-space failure is structurally impossible.
"""
from __future__ import annotations

import json
import sys


def register(subparsers):
    cp = subparsers.add_parser(
        "consolidate",
        help="Tally per-lens-basin chairman winners from council outcomes into ~/.trinity/scoreboard/picks.json",
    )
    cp.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the routable basins + their winners; don't write to disk",
    )
    cp.set_defaults(handler=handle_consolidate)


def handle_consolidate(args):
    """Rebuild the routing scoreboard from the LENS basins (#298 collapse).

    A cheap, deterministic, LLM-FREE pass: place each real-contest council into
    its nearest lens basin (topics.json's live centroids) and tally the
    recency-weighted chairman winner per basin. Replaces the old flagship-LLM
    extractor + the 6-component trust score + the SEPARATE cortex centroids
    (#277). `ask` routes on the result via the same lens centroids, so the
    stale-embedding-space failure is structurally impossible."""
    from ..cortex import save_routing_patterns
    from ..lens_routing import consolidate_via_lens_basins

    routing = consolidate_via_lens_basins()
    if not routing:
        print(json.dumps({
            "ok": False,
            "reason": "no routable lens basins yet — needs a built lens (topics.json) + "
                      "real-contest councils whose tasks fall in a basin (n>=2)",
        }, indent=2))
        return 0

    if getattr(args, "dry_run", False):
        print(json.dumps({
            "ok": True, "mode": "dry-run", "routable_basins": len(routing),
            "winners": {b: r.get("winner") for b, r in sorted(routing.items())},
        }, indent=2))
        return 0

    try:
        save_routing_patterns(routing)
    except Exception as exc:  # noqa: BLE001 — #194 clobber guard (DegenerateExtractionError) etc.
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1

    for bid, r in sorted(routing.items()):
        print(
            f"  ✓ {bid} → {r.get('winner')} (n={r.get('count')}, margin={r.get('margin', 0):.2f})",
            file=sys.stderr,
        )

    # Freeze the per-task-type routing table too (cheap, deterministic, no LLM;
    # the launchpad routing card reads routing.json).
    routing_summary: dict | None = None
    try:
        from ..personal_routing import freeze_routing_to_disk
        table = freeze_routing_to_disk()
        routing_summary = {"task_types": len((table or {}).get("by_task_type") or {})}
    except Exception as exc:
        routing_summary = {"error": f"{type(exc).__name__}: {exc}"}

    payload = {
        "ok": True,
        "routable_basins": len(routing),
        "path": str(_routing_patterns_path()),
        **({"routing_frozen": routing_summary} if routing_summary is not None else {}),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _routing_patterns_path():
    from ..state_paths import cortex_routing_patterns_path
    return cortex_routing_patterns_path()
