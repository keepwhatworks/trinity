#!/usr/bin/env python3
"""scripts/anchor.py — proper-noun anchor extraction per mode.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).

Anchors are the proper-noun phrases (projects, people, frameworks)
that recur across distinct threads in the user's corpus. Recurrence
across N distinct conversations is the load-bearing signal: anchors
are projects/people/frameworks, not single-thread mentions.

Wraps trinity_local.vocabulary.find_anchors for v1.0; v1.1 inverts.

Dual interface:
  - Shebang: `python3 scripts/anchor.py < input.json`
  - Importable: `from scripts.anchor import find_anchors`

CLI Input:
  {"nodes": [{"text": "...", "transcript_id": "..."}, ...],
   "min_threads": 3, "top_n": 50}

CLI Output:
  {"anchors": [{"phrase": "...", "n_threads": int, "n_mentions": int}, ...]}
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from scripts._runtime import (
    audit_log,
    bootstrap_or_continue,
    read_input_json,
    write_output_json,
)


SCRIPT_NAME = "anchor"
REQUIREMENTS: list[str] = []  # pure stdlib + trinity_local


def find_anchors(
    nodes: list[dict],
    *,
    min_threads: int = 3,
    top_n: int = 50,
) -> list[dict]:
    """Rank proper-noun phrases by distinct-thread recurrence.

    Returns: [{"phrase": str, "n_threads": int, "n_mentions": int}, ...]
    sorted by (n_threads DESC, n_mentions DESC).

    Input: list of dicts with "text" and "transcript_id" (or "id").
    """
    from trinity_local.vocabulary import find_anchors as _impl

    # Pip tier accepts objects with attribute access — adapt dicts.
    node_objs = [
        type("Node", (), {
            "text": n.get("text", ""),
            "transcript_id": n.get("transcript_id"),
            "id": n.get("id"),
        })()
        for n in nodes
    ]

    ranked = _impl(node_objs, min_threads=min_threads, top_n=top_n)
    return [
        {"phrase": phrase, "n_threads": int(n_threads),
         "n_mentions": int(n_mentions)}
        for phrase, n_threads, n_mentions in ranked
    ]


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/anchor.py",
        description=(
            "Extract proper-noun anchors — phrases that recur across "
            "≥ min_threads distinct conversations. The load-bearing "
            "signal that a token is a project/person/framework, not a "
            "single-thread mention."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Dependencies: none (pure stdlib).",
    )
    parser.add_argument("input", nargs="?", default="-")
    parser.add_argument("--out", "-o", default="-")
    args = parser.parse_args(argv)

    started_at = time.monotonic()
    payload = read_input_json(args.input if args.input != "-" else None)
    if not isinstance(payload, dict) or "nodes" not in payload:
        print("error: input must be a JSON object with a 'nodes' field",
              file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="find_anchors",
                  outcome="bad_input", detail="missing 'nodes'")
        return 2

    nodes = payload["nodes"]
    if not isinstance(nodes, list):
        print("error: 'nodes' must be a list", file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="find_anchors",
                  outcome="bad_input", detail="'nodes' not a list")
        return 2

    min_threads = int(payload.get("min_threads", 3))
    top_n = int(payload.get("top_n", 50))

    try:
        anchors = find_anchors(nodes, min_threads=min_threads, top_n=top_n)
    except Exception as exc:
        audit_log(
            script=SCRIPT_NAME, operation="find_anchors", outcome="error",
            args={"n_nodes": len(nodes)},
            detail=f"{type(exc).__name__}: {exc}",
        )
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    audit_log(
        script=SCRIPT_NAME, operation="find_anchors",
        args={"n_nodes": len(nodes), "min_threads": min_threads,
              "top_n": top_n, "n_anchors": len(anchors),
              "elapsed_ms": elapsed_ms},
    )
    write_output_json({
        "anchors": anchors,
        "n_anchors": len(anchors),
        "elapsed_ms": elapsed_ms,
    }, args.out if args.out != "-" else None)
    return 0


if __name__ == "__main__":
    bootstrap_or_continue(script_name=SCRIPT_NAME, requirements=REQUIREMENTS)
    sys.exit(_cli_main())
