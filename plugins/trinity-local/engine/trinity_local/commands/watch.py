"""Handler for `trinity-local ingest-recent`.

The watcher CLI (`watch-once`, `watch-loop`) was retired 2026-05-17: the
MCP server's usage-gated stale pass (stale_pass.py) and the first-run scan
(cold_start.py) call `ingest_recent()` themselves. This module exposes the
`ingest-recent` CLI that the Chrome extension's Native Messaging host shells
out to via the `ingest-recent` action allowlist entry.

The file is named `watch.py` for git-history continuity; the public
CLI command it registers is `ingest-recent`.
"""
from __future__ import annotations

import json

from ..registry import INGEST_SOURCES


def register(subparsers):
    ip = subparsers.add_parser(
        "ingest-recent",
        help="Incremental cursor-based ingest into the prompt index (the Chrome extension and the MCP server fire this same path).",
    )
    ip.add_argument("--source", action="append", dest="sources", choices=list(INGEST_SOURCES), default=[])
    ip.add_argument("--deadline", type=float, default=10.0, help="Max seconds to spend (default: 10)")
    ip.set_defaults(handler=handle_ingest_recent)


def handle_ingest_recent(args):
    from ..incremental_ingest import DEFAULT_SOURCES, ingest_recent

    sources = args.sources or list(DEFAULT_SOURCES)
    result = ingest_recent(sources=sources, deadline_s=args.deadline)
    out = result.to_dict()
    # Quarantine promotion (council_5ab2854092bcf68f): the corpus just grew —
    # re-verify quarantined provider imports against it and promote the rows
    # whose anchors now resolve. Best-effort; never blocks ingest.
    try:
        from ..me.import_verification import promote_quarantined
        promo = promote_quarantined()
        if any(v.get("promoted") or v.get("pending") for v in promo.values()):
            out["quarantine_promotion"] = promo
    except Exception:
        pass
    print(json.dumps(out, indent=2))
