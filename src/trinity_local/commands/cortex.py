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
    cp.add_argument(
        "--prune-orphans",
        action="store_true",
        help=(
            "Only drop picks entries whose basin no longer exists in topics.json. "
            "No re-tally, no embedder, no council scan — the cheap repair for the "
            "window between a lens rebuild and the next consolidate"
        ),
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
    from ..cortex import load_routing_patterns, save_routing_patterns
    from ..lens_routing import consolidate_via_lens_basins

    if getattr(args, "prune_orphans", False):
        return _handle_prune_orphans(args)

    existing_ids = set(load_routing_patterns())
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
        # A full consolidate rewrites the whole store from the CURRENT topology,
        # so any rule keyed to a basin that has since been re-drawn disappears
        # here. Report it rather than letting it vanish silently: the count is
        # the visible signal that the lens was rebuilt without a re-consolidate.
        "orphans_dropped": sorted(existing_ids - set(routing)),
        "path": str(_routing_patterns_path()),
        **({"routing_frozen": routing_summary} if routing_summary is not None else {}),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _handle_prune_orphans(args) -> int:
    """`consolidate --prune-orphans` — drop dead basin keys, tally untouched.

    A full consolidate needs the embedder and a scan of every council outcome.
    This does neither: it compares picks.json's keys against topics.json's live
    ids and deletes the ones that cannot be reached. That is the whole repair
    for the failure mode the ids actually have (see `lens_routing`'s
    orphan-pruning note): the lens rebuilds, ids are re-drawn by position, and
    picks.json keeps pointing at the old ones until someone re-consolidates.

    Refuses loudly rather than guessing — `ok:false` with the reason — whenever
    the live topology is missing or too small to arbitrate, or when so many
    rules would go that the id scheme itself must have changed."""
    from ..cortex import load_routing_patterns, save_routing_patterns
    from ..lens_routing import live_basin_ids, prune_orphan_rules

    rules = load_routing_patterns()
    basin_ids = live_basin_ids()
    kept, dropped, reason = prune_orphan_rules(rules, basin_ids)

    payload = {
        "ok": bool(dropped) or "no orphans" in reason,
        "reason": reason,
        "live_basins": len(basin_ids),
        "rules_before": len(rules),
        "rules_after": len(kept),
        "orphans_dropped": dropped,
        "path": str(_routing_patterns_path()),
    }
    if not dropped:
        # Nothing written — either clean already or refused. `ok` distinguishes
        # them; `reason` says which, in words.
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1
    if getattr(args, "dry_run", False):
        payload["mode"] = "dry-run"
        print(json.dumps(payload, indent=2))
        return 0
    try:
        # allow_shrink: a prune is a deliberate, bounded shrink — the #194
        # clobber guard exists to catch an ACCIDENTAL cliff-drop from a
        # degenerate re-tally, and prune_orphan_rules has already refused
        # anything bigger than MAX_ORPHAN_DROP_FRACTION.
        save_routing_patterns(kept, allow_shrink=True)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1
    print(json.dumps(payload, indent=2))
    return 0


def _routing_patterns_path():
    from ..state_paths import cortex_routing_patterns_path
    return cortex_routing_patterns_path()
