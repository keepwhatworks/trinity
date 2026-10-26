"""`eval-replay` — export replay tasks from your own bugs (Harbor-shaped).

Hidden verb (not on the six-verb surface). See src/trinity_local/replay_tasks.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..state_paths import trinity_home


def register(subparsers):
    p = subparsers.add_parser(
        "eval-replay",
        help="Export replay tasks: fix commits whose own test goes red without the fix, "
             "written in Harbor's task layout for release-day model and harness evals.",
    )
    p.add_argument("--repo", default=".", help="git repository to mine (default: .)")
    p.add_argument("--since", default="90 days ago", help="only fix commits after this date")
    p.add_argument("--max", type=int, default=20, help="stop after this many tasks (default 20)")
    p.add_argument("--out", help="output directory (default: <TRINITY_HOME>/evals/replay)")
    p.add_argument("--paths", nargs="*", help="limit each snapshot to these paths (default: all tracked files)")
    p.add_argument("--python", help="interpreter that runs the repo's tests (default: the repo's .venv, else this one)")
    p.add_argument("--agent-network", choices=["allowlist", "no-network", "public"], default="allowlist",
                   help="the agent's network policy in task.toml (default: model APIs only). Docker on macOS "
                        "cannot enforce allowlist/no-network and Harbor refuses such tasks there; use public "
                        "only to check a task with Harbor's oracle/nop agents locally.")
    p.set_defaults(handler=handle_replay)


def handle_replay(args) -> int:
    import subprocess
    import sys
    from ..replay_tasks import export
    repo = Path(args.repo).expanduser()
    # Run outside a git repository (the default --repo is the current directory), git's "not a git
    # repository" escaped as a traceback on every published wheel (crash sweep, 2026-10-16).
    try:
        inside = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
    except FileNotFoundError:
        print("eval-replay: git is not installed or not on PATH.", file=sys.stderr)
        return 2
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        # git's own reason, not a guess: a Command Line Tools stub that is not installed also
        # fails here, and is not "not a git repository" (the 1.7.408 reader sweep).
        why = (inside.stderr.strip().splitlines() or ["not inside a git repository"])[0]
        print(f"eval-replay: {repo.resolve()}: {why}. Pass --repo <path> of a git repository.",
              file=sys.stderr)
        return 2
    out = Path(args.out).expanduser() if args.out else trinity_home() / "evals" / "replay"
    try:
        res = export(repo, out, since=args.since, limit=args.max, paths=args.paths, python=args.python,
                     agent_network=getattr(args, "agent_network", "allowlist"))
    except RuntimeError as exc:      # git failed underneath, e.g. a repository with no commits yet
        print(f"eval-replay: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
    return 0
