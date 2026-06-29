"""Handler for `trinity-local ingest-recent`.

The watcher CLI (`watch-once`, `watch-loop`) was retired 2026-05-17 —
MCP `ask` fires `ingest_recent()` automatically on every call with a
1s deadline, so the watcher subsystem is redundant on the live product
path. This module survives only to expose the `ingest-recent` CLI
that the Chrome extension's Native Messaging host shells out to via
the `ingest-recent` action allowlist entry.

The file is named `watch.py` for git-history continuity; the public
CLI command it registers is `ingest-recent`.
"""
from __future__ import annotations

import json


def register(subparsers):
    ip = subparsers.add_parser(
        "ingest-recent",
        help="Incremental cursor-based ingest into the prompt index (Chrome ext + MCP ask fire this same path).",
    )
    ip.add_argument("--source", action="append", dest="sources", choices=["claude", "codex", "gemini", "cowork"], default=[])
    ip.add_argument("--deadline", type=float, default=10.0, help="Max seconds to spend (default: 10)")
    ip.set_defaults(handler=handle_ingest_recent)


def handle_ingest_recent(args):
    from ..incremental_ingest import DEFAULT_SOURCES, ingest_recent

    sources = args.sources or list(DEFAULT_SOURCES)
    result = ingest_recent(sources=sources, deadline_s=args.deadline)
    print(json.dumps(result.to_dict(), indent=2))
