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
    from ..replay_tasks import export
    out = Path(args.out).expanduser() if args.out else trinity_home() / "evals" / "replay"
    res = export(Path(args.repo), out, since=args.since, limit=args.max, paths=args.paths, python=args.python,
                 agent_network=getattr(args, "agent_network", "allowlist"))
    print(json.dumps(res, indent=2))
    return 0
