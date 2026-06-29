"""trinity-local debug — discovery surface for power-user verbs.

Area 5's "One-shot debug consolidates under `trinity-local debug
<subcmd>`" lands as a directory of power-user verbs rather than a
full re-nesting (the verbs are also reachable by their original
names — both the launchpad dispatch and the agent MCP-dropdown
registries call them by name today, so duplicating each parser
under `debug` would create argparse-conflict churn).

What `trinity-local debug` does:
  - With no args: lists the four power-user verbs + one-line summary
    + the invocation form.
  - With a subcommand name: delegates to `python -m trinity_local.main
    <subcmd>` so the user can type either form. (Implementation just
    re-execs argparse with the canonical name.)

This satisfies the cron spec's discoverability requirement (the verbs
are findable via `trinity-local --help` → "debug" → list) without
forcing every power-user CLI to live under two registration paths.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace


# The power-user verbs we surface under `debug`. Each entry: name +
# one-line "what this does". The CANONICAL location stays the same
# (top-level subparser); `debug` just advertises them.
# (`replay-history` retired 2026-05-27 — see retired_names.py. The
# personal routing table is now populated by normal council usage;
# the standalone "re-evaluate top-N" surface was unused.)
_DEBUG_VERBS: list[tuple[str, str]] = [
    (
        "consolidate",
        "Extract routing patterns per basin from council outcomes "
        "(supports --audit for independent-chairman drift check).",
    ),
    (
        "vocabulary",
        "Scan prompts for terminology overloads (one word ↔ two "
        "meanings; two words ↔ one meaning).",
    ),
    (
        "import-export",
        "Bulk-ingest a Takeout / ChatGPT export / Claude.ai export "
        "at any path. Auto-detects format (#148).",
    ),
]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "debug",
        help="Power-user verbs: consolidate, vocabulary, import-export.",
    )
    parser.add_argument(
        "subcommand", nargs="?", default=None,
        choices=[name for name, _ in _DEBUG_VERBS],
        help="Optional. Run `trinity-local debug <subcommand>` for "
             "details — without it, lists all power-user verbs.",
    )
    parser.set_defaults(handler=handle_debug)


def handle_debug(args: SimpleNamespace) -> int:
    sub = getattr(args, "subcommand", None)
    if sub is None:
        _print_directory()
        return 0
    # Delegate to the canonical subparser. `trinity-local debug
    # consolidate` is shorthand for `trinity-local consolidate`.
    print(
        f"To run: trinity-local {sub} [args]\n"
        f"  (the verb is reachable directly under that name; this "
        f"command is just a discovery surface.)",
        file=sys.stderr,
    )
    return 0


def _print_directory() -> None:
    """Print the directory of power-user verbs to stdout."""
    print("Trinity power-user verbs (run directly by name):")
    for name, summary in _DEBUG_VERBS:
        print(f"  trinity-local {name}")
        print(f"    {summary}")
    print()
    print(
        "Hidden from `trinity-local --help` as of the Area 5 CLI "
        "consolidation. Reachable both by `trinity-local <verb>` and "
        "advertised here under `debug`."
    )
