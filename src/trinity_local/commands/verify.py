"""Handler for the `verify` command — the pre-deploy gate.

    trinity-local verify --diff change.patch --criteria acceptance.json [--context symptom.txt]

Runs the acceptance block's tests in --cwd, gets three blinded reads on its
judgment criteria, applies the rule (STOP / SKIP / READ), prints JSON. The
engine is `trinity_local.verify`; the rule is `trinity_local.verify_rule`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..verify import DEFAULT_PANEL, verify


def register(subparsers):
    p = subparsers.add_parser("verify", help="Pre-deploy gate: run the tests, three blinded reads, one of STOP / SKIP / READ")
    p.add_argument("--diff", required=True, help="unified diff of the change")
    p.add_argument("--criteria", required=True,
                   help="JSON file: a list of {id, kind: test|judgment, statement, command?, blocking}")
    p.add_argument("--context", help="file with the symptom / task the change addresses")
    p.add_argument("--cwd", default=".", help="workdir where test commands run (default: .)")
    p.add_argument("--members", default=",".join(DEFAULT_PANEL), help="comma-separated panel")
    p.add_argument("--exclude-lab", help="never read from this lab (e.g. the lab that authored the change)")
    p.add_argument("--no-panel", action="store_true", help="kernel only")
    p.add_argument("--no-tests", action="store_true", help="panel only")
    p.add_argument("--effort", help="panel effort for claude/codex (low|medium|high|xhigh); default is each provider's config")
    p.set_defaults(handler=handle_verify)


def handle_verify(args) -> int:
    diff = Path(args.diff).read_text(encoding="utf-8", errors="replace")
    raw = json.loads(Path(args.criteria).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("acceptance", raw.get("criteria"))
    if not isinstance(raw, list):
        print("refused: --criteria must be a JSON list, or an object with an 'acceptance' list",
              file=sys.stderr)
        return 1
    context = Path(args.context).read_text(encoding="utf-8", errors="replace") if args.context else ""
    try:
        out = verify(raw, diff, context, Path(args.cwd),
                     providers=tuple(m.strip() for m in args.members.split(",") if m.strip()),
                     exclude_lab=args.exclude_lab, run_panel=not args.no_panel,
                     run_tests=not args.no_tests, effort=args.effort)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0 if out["triage"] != "STOP" else 2
