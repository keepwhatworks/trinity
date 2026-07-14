#!/usr/bin/env python3
"""Delegability audit — the sidekick premise's pre-registered falsifier
(council_4735c76c01f90e3f, amendment amd_0005). HOST-owned, retro-runnable.

PHASE A (this file, --phase-a): the delegability CEILING, free, zero model
calls. Segment each Claude Code session trace into EPISODES and measure D =
the share of lead output that lives in DELEGABLE episodes — the ceiling of a
sidekick's addressable work. This alone can kill the sidekick layer for an
afternoon and zero tokens.

PRE-REGISTERED (locked 2026-07-14 BEFORE running):
  D < 15%          KILL — even a perfect sidekick addresses a rounding error;
                   the workload is serial-judgment-shaped, the "micromanager"
                   profile is correct behavior, the layer dies before build.
  D >= 30%         PROCEED to Phase B (mechanical-oracle replay).
  15% <= D < 30%   INCONCLUSIVE — decide on Phase B quality alone.

DELEGABLE episode criterion (pre-registered, applied without looking at
outputs first):
  (1) terminates in a GREEN verification command (a Bash test/build/lint the
      trace shows exiting 0 — or, absent an exit code in the trace, a
      verification-shaped command not followed by a corrective edit of the
      same files),
  (2) its edits touch <= 3 distinct files,
  (3) NO user turn occurs inside it (a user interruption means it wasn't a
      clean hand-offable unit),
  (4) it is not a re-edit of files a PRIOR episode in the session already
      touched (it wasn't a groping/rework episode).

An EPISODE = a contiguous run of assistant turns containing >=1 Edit/Write,
bounded by a user turn OR a verification event. Output tokens are
approximated by assistant text + tool-input length (the trace doesn't store
token counts; chars/4 is the standard proxy, applied identically to
numerator and denominator so D is a ratio, not an absolute).

Usage:  PYTHONPATH=src scripts/delegability_audit.py --phase-a
Artifacts: internal/experiments/delegability-audit-2026-07-14/
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SESSIONS_DIR = Path.home() / ".claude/projects/-Users-openclaw-projects-trinity-local"
OUT_DIR = Path(__file__).resolve().parent.parent / "internal/experiments/delegability-audit-2026-07-14"

MAX_FILES = 3
D_KILL = 0.15
D_PROCEED = 0.30

# A Bash command is VERIFICATION-shaped if it runs the suite / a build / a lint.
_VERIFY = re.compile(r"\b(pytest|python -m pytest|npm (run )?test|npm run build|"
                     r"bundle_engine|render_docs|find_orphans|pyflakes|ruff|mypy|"
                     r"tsc|cargo (test|build)|go test|make (test|check))\b")
# Edit-shaped tools.
_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}


def _iter_turns(path: Path):
    """Yield (role, tool_uses, text_len) per assistant/user line, in order.
    tool_uses = list of (name, input_dict)."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t not in ("assistant", "user"):
            continue
        m = d.get("message") or {}
        content = m.get("content")
        tool_uses = []
        text_len = 0
        if isinstance(content, str):
            text_len += len(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    tool_uses.append((b.get("name"), b.get("input") or {}))
                    text_len += len(json.dumps(b.get("input") or {}))
                elif b.get("type") == "text":
                    text_len += len(b.get("text") or "")
        yield t, tool_uses, text_len


def _edited_files(inp: dict) -> set[str]:
    f = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    return {f} if isinstance(f, str) and f else set()


def _bash_cmd(inp: dict) -> str:
    return str(inp.get("command") or "")


def segment_session(path: Path) -> list[dict]:
    """Segment one session into episodes with the pre-registered fields."""
    episodes = []
    seen_files: set[str] = set()  # files touched by any PRIOR episode
    cur = None  # {edits:set, output:int, verified:bool, had_user:bool, files:set}

    def close(verified: bool):
        nonlocal cur
        if cur and cur["files"]:
            reworks_prior = bool(cur["files"] & seen_files)
            delegable = (
                verified
                and len(cur["files"]) <= MAX_FILES
                and not cur["had_user_inside"]
                and not reworks_prior
            )
            episodes.append({
                "files": sorted(cur["files"]), "n_files": len(cur["files"]),
                "output_chars": cur["output"], "verified_green": verified,
                "had_user_inside": cur["had_user_inside"],
                "reworks_prior": reworks_prior, "delegable": delegable,
            })
            seen_files.update(cur["files"])
        cur = None

    for role, tools, text_len in _iter_turns(path):
        if role == "user":
            # a user turn bounds any open episode (unverified) then resets
            if cur and cur["files"]:
                cur["had_user_inside"] = True  # user spoke before a green close
                close(verified=False)
            cur = None
            continue
        # assistant turn
        edits = set()
        verified_here = False
        for name, inp in tools:
            if name in _EDIT_TOOLS:
                edits |= _edited_files(inp)
            elif name == "Bash" and _VERIFY.search(_bash_cmd(inp)):
                verified_here = True
        if edits:
            if cur is None:
                cur = {"files": set(), "output": 0, "had_user_inside": False}
            cur["files"] |= edits
        if cur is not None:
            cur["output"] += text_len
            if verified_here and cur["files"]:
                close(verified=True)
    if cur and cur["files"]:
        close(verified=False)
    return episodes


def phase_a() -> None:
    sessions = sorted(SESSIONS_DIR.glob("*.jsonl"))
    total_output = 0
    delegable_output = 0
    n_ep = n_deleg = 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    detail = (OUT_DIR / "episodes.jsonl").open("w", encoding="utf-8")
    for path in sessions:
        try:
            eps = segment_session(path)
        except Exception:
            continue
        for e in eps:
            n_ep += 1
            total_output += e["output_chars"]
            if e["delegable"]:
                n_deleg += 1
                delegable_output += e["output_chars"]
            e["session"] = path.stem
            detail.write(json.dumps(e) + "\n")
    detail.close()

    D = delegable_output / total_output if total_output else 0.0
    verdict = "KILL" if D < D_KILL else "PROCEED" if D >= D_PROCEED else "INCONCLUSIVE"
    summary = {
        "sessions": len(sessions), "episodes": n_ep, "delegable_episodes": n_deleg,
        "D_delegable_output_share": round(D, 4),
        "thresholds": {"kill": D_KILL, "proceed": D_PROCEED},
        "verdict": verdict,
        "delegable_output_chars": delegable_output, "total_output_chars": total_output,
    }
    (OUT_DIR / "phase_a_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"\nPHASE A VERDICT: {verdict}  (D = {D:.1%}; kill <{D_KILL:.0%}, proceed >={D_PROCEED:.0%})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase-a", action="store_true")
    args = ap.parse_args()
    phase_a()  # only phase A exists yet; Phase B is gated on this verdict
