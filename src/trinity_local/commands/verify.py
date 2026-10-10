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
    p = subparsers.add_parser("verify", help="Before you call a change done: runs your tests, then the other labs read the diff blind. STOP (a test is red) or READ (a person still reads)")
    p.add_argument("--diff", required=True, help="unified diff of the change")
    p.add_argument("--criteria", required=True,
                   help="JSON file: a list of {id, kind: test|judgment, statement, command?, blocking}")
    p.add_argument("--context", help="file with the symptom / task the change addresses")
    p.add_argument("--cwd", default=".", help="workdir where test commands run (default: .)")
    p.add_argument("--members", default=",".join(DEFAULT_PANEL), help="comma-separated panel")
    p.add_argument("--exclude-lab", help="never read from this lab (e.g. the lab that authored the change)")
    p.add_argument("--no-panel", action="store_true", help="kernel only")
    p.add_argument("--no-tests", action="store_true", help="panel only")
    p.add_argument("--no-quality", action="store_true",
                   help="skip the advisory reuse / root-cause / dead-code questions the panel also answers")
    p.add_argument("--no-differential", action="store_true",
                   help="skip rerunning green tests with the change reverted (relevance stays declared)")
    p.add_argument("--effort", help="panel effort for claude/codex (low|medium|high|xhigh); default is each provider's config")
    p.add_argument("--json", action="store_true",
                   help="raw result object instead of the review card (for scripts)")
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
                     run_tests=not args.no_tests, effort=args.effort,
                     differential=False if getattr(args, "no_differential", False) else None,
                     quality=False if getattr(args, "no_quality", False) else None)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    # getattr, not args.json: argparse always sets this, but programmatic
    # callers build a Namespace by hand and a new flag must not break them.
    # Six tests broke on `args.json` the moment the flag was added, and each
    # one was a caller that never asked for the card.
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
    else:
        print(render_card(out))
    return 0 if out["triage"] != "STOP" else 2


def render_card(out: dict) -> str:
    """What a human reads. The JSON stays behind --json for scripts.

    Council 595c6e34, all three members: "Generic READ output does not justify
    the review burden" and "READ should surface concrete concerns tied to
    code." Until now this command printed the whole result object and the
    reviewers' reasoning was not in it at all -- it survived only as a
    truncated `raw_tail`, so a user was told "a human must read this" and given
    nothing to read.

    NO MANUFACTURED CONCERNS. When nobody doubted anything, this says so
    plainly rather than inventing something to justify the step. An empty card
    is a real answer and the honest one.
    """
    triage = out.get("triage", "?")
    lines = [f"{triage}  —  {out.get('reason', '')}"]

    kernel = out.get("kernel") or {}
    if kernel.get("ran"):
        state = "red" if kernel.get("green") is False else "green"
        rel = "" if kernel.get("relevant") else "  (does NOT exercise the changed files)"
        lines.append(f"  tests: {state}{rel}")
        # Evidence first (arXiv 2610.00972): what was run and what came back, before opinions.
        meas = out.get("differential") or {}
        status = meas.get("status") or {} if meas.get("ran") else {}
        _said = {"detects": "red without the change: it tests it",
                 "no_load": "does not load without the change",
                 "vacuous": "VACUOUS: green without the change too",
                 "not_reproduced": "not checked: the scratch copy did not reproduce its green",
                 "timeout": "not checked: timed out"}
        for run in kernel.get("runs") or []:
            mark = "ok " if run.get("green") else "RED"
            cmd = (run.get("command") or "")
            cmd = cmd if len(cmd) <= 90 else cmd[:87] + "..."
            lines.append(f"    {mark} [{run.get('id')}] {cmd}  ({run.get('seconds', '?')}s)")
            if run.get("id") in status:
                lines.append(f"        {_said.get(status[run['id']], status[run['id']])}")
        if meas and not meas.get("ran"):
            lines.append(f"  relevance: declared (not measured: {meas.get('reason', '')})")
    else:
        lines.append("  tests: none ran")
    untested = out.get("untested") or {}
    if untested.get("ran"):
        miss = untested.get("untested") or []
        lines.append(f"  untested: {len(miss)} of {untested.get('assessed', 0)} changed hunks "
                     "can be reverted with every test still green")
        for h in miss:
            lines.append(f"    - {h['path']} {h['hunk']}")
    elif untested:
        lines.append(f"  untested: not checked ({untested.get('reason', '')})")
    dups = (out.get("duplicates") or {}).get("duplicated") or []
    if dups:
        lines.append(f"  duplicated: {len(dups)} value(s) this change now keeps in more than one place:")
        for d in dups[:5]:
            lines.append(f"    - {d['value'][:60]!r} in {d['added_in']} and {', '.join(d['also_in'][:3])}")
    # Unverified: criteria no test runs. They were only read, so a person reads them.
    crit = {c.get("id"): c for c in (out.get("criteria") or [])}
    unverified = out.get("unverified")
    if unverified is None:
        unverified = [cid for cid, c in crit.items() if c.get("kind") == "judgment"]
    if unverified:
        lines.append(f"  unverified: {len(unverified)} criteria nothing tested against this change (read these):")
        for cid in unverified:
            lines.append(f"    - [{cid}] {(crit.get(cid) or {}).get('statement', '')}")

    panel = out.get("panel") or {}
    reads = panel.get("reads") or []
    if not reads:
        lines.append("  panel: did not run")
        return "\n".join(lines)

    voted = [r for r in reads if not r.get("error")]
    lost = [r for r in reads if r.get("error")]
    lines.append(f"  panel: {len(voted)} of {len(reads)} reviewers voted"
                 + (f"  ({len(lost)} lost: " + ", ".join(
                     f"{r.get('provider')} {r.get('error')}" for r in lost) + ")" if lost else ""))

    # concerns, grouped by the criterion they are about
    by_crit: dict = {}
    for r in voted:
        for cid, ok in (r.get("votes") or {}).items():
            if not ok:
                by_crit.setdefault(cid, []).append(r)
    if not by_crit:
        lines.append("")
        lines.append("  No reviewer raised a concern. Nothing here is a substitute for reading "
                     "the change, but nothing was flagged.")
        return "\n".join(lines)

    statements = {c.get("id"): c.get("statement", "") for c in (out.get("criteria") or [])}
    advisory_ids = {c.get("id") for c in (out.get("criteria") or []) if c.get("advisory")}
    lines.append("")
    lines.append(f"  {len(by_crit)} criterion/criteria drew a concern:")
    for cid, doubters in by_crit.items():
        lines.append("")
        advisory = "  (advisory: does not change the verdict)" if cid in advisory_ids else ""
        lines.append(f"  [{cid}] {statements.get(cid, '')}{advisory}")
        for r in doubters:
            why = (r.get("why") or "").strip()
            lab = r.get("lab") or r.get("provider")
            backing = next((d["status"] for d in (out.get("doubts") or [])
                            if d.get("provider") == r.get("provider") and d.get("criterion") == cid), None)
            label = {"backed": "  [BACKED: its test passes before this change and fails after]",
                     "backed_unquoted": "  [its test passes before and fails after, but cites no pre-change line]",
                     "spec_disagreement": "  [its test fails before the change too: a preference, not a regression]",
                     "not_backed": "  [its test passes: not backed]",
                     "invalid": "  [its test does not load]", "unchecked": "  [its test hung]",
                     "no_test": "  [no test given]"}.get(backing, f"  [{backing}]" if backing else "")
            if why:
                lines.append(f"    - {lab}: {why}{label}")
            else:
                tail = (r.get("raw_tail") or "").strip()
                lines.append(f"    - {lab}: (no reason given)"
                             + (f"  raw: {tail[-120:]}" if tail else ""))
    return "\n".join(lines)
