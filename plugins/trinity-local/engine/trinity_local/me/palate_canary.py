"""The collapse detector: does the palate's live accuracy fall below its floor?

P4 of the compression plan; shape fixed by council_6ac26dafe733d16a. If any
feedback edge is ever wired into lens construction, the prospective palate
registry is the one instrument still independent enough to notice collapse —
PROVIDED the loop cannot reach its labels (amd_0185's one-way diode) and its
pre-edge record is never pooled with post-edge trials (amd_0183).

This module is the read side: a pure function over the trials ledger that
answers "has the canary died?", plus the epoch boundary that makes the question
well-posed. It ships and runs TODAY, with no edge wired, for two reasons:

  * a detector built after the edge would be tuned on the data it must judge;
    this one's bar was frozen while no edge exists and nothing optimises
    against it (the halt bar is the PRE-EDGE Wilson lower bound, 0.773,
    registered in the plan before this file was written);
  * the trials ledger already has seven natural epochs (snapshot_built_at),
    so every code path here is exercised by real data rather than fixtures.

WHAT IT IS NOT. It does not halt anything itself — there is no loop to halt.
It returns a verdict a future loop MUST consult before writing; wiring that
consultation is part of wiring any edge, and tests_pin the contract. It also
does not prove independence: the diode (keeping the loop away from the labels)
is an access-control property of the writer, not something a reader can verify.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from ..state_paths import trinity_home

# The PRE-EDGE floor. Frozen 2026-08-24 while no feedback edge exists: the
# Wilson lower bound of the palate's full pre-edge record (289/354, 81.6%,
# [0.773, 0.853]). A post-edge cohort whose own Wilson lower bound cannot reach
# the pre-edge LOWER bound has degraded beyond what sampling noise explains.
PRE_EDGE_WILSON_LO = 0.773

# Below this many decided post-edge trials the detector refuses a verdict: a
# cohort of 10 can sit below the floor by luck alone (its own CI spans it).
MIN_DECIDED = 50


def _wilson_lo(k: int, n: int, z: float = 1.96) -> float:
    if not n:
        return 0.0
    p = k / n
    denom = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom


@dataclass
class CanaryVerdict:
    status: str          # "alive" | "collapsed" | "insufficient" | "no-boundary"
    reason: str
    decided: int = 0
    correct: int = 0
    accuracy: float | None = None
    wilson_lo: float | None = None
    floor: float = PRE_EDGE_WILSON_LO
    boundary: str | None = None
    epochs_seen: list[str] = field(default_factory=list)

    @property
    def halted(self) -> bool:
        """A loop consulting the canary may write ONLY when this is False.

        'insufficient' halts too: a loop that keeps writing while the canary
        cannot yet speak is a loop grading itself during the one window nobody
        can check it.
        """
        return self.status != "alive"

    def to_dict(self) -> dict[str, Any]:
        d = {"status": self.status, "reason": self.reason, "halted": self.halted,
             "decided": self.decided, "correct": self.correct, "floor": self.floor}
        if self.accuracy is not None:
            d["accuracy"] = round(self.accuracy, 4)
        if self.wilson_lo is not None:
            d["wilson_lo"] = round(self.wilson_lo, 4)
        if self.boundary:
            d["boundary"] = self.boundary
        if self.epochs_seen:
            d["epochs_seen"] = self.epochs_seen
        return d


def _trials_path():
    return trinity_home() / "me" / "palate_trials.jsonl"


def _boundary_path():
    return trinity_home() / "me" / "edge_boundary.json"


def stamp_edge_boundary(*, edge: str, at: str) -> dict:
    """Record the moment a feedback edge goes live. IRREVOCABLE by design.

    amd_0183: the 81.6% pre-edge record may never be pooled with adaptive-lens
    trials, so the boundary must be stamped WHEN the edge is wired, not
    reconstructed later from memory. Refuses to overwrite: moving a boundary
    after the fact is exactly the retro-fitting the amendment forbids.
    """
    p = _boundary_path()
    if p.exists():
        existing = json.loads(p.read_text(errors="replace"))
        # Shape-guard the parse: a corrupt boundary file must not read as
        # "no boundary" and silently re-open a stamped epoch.
        if not isinstance(existing, dict):
            raise ValueError(
                f"edge boundary file is corrupt (parsed as {type(existing).__name__}, "
                f"expected object). Refusing rather than treating an unreadable "
                f"boundary as an absent one.")
        raise ValueError(
            f"edge boundary already stamped at {existing.get('at')} for "
            f"{existing.get('edge')!r} — a boundary moved after the fact is the "
            f"pooling violation amd_0183 exists to prevent. Refusing.")
    rec = {"edge": edge, "at": at, "floor": PRE_EDGE_WILSON_LO}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2))
    return rec


def canary_verdict() -> CanaryVerdict:
    """Score ONLY post-boundary decided trials against the frozen pre-edge floor."""
    bp = _boundary_path()
    if not bp.exists():
        return CanaryVerdict(
            status="no-boundary",
            reason="no feedback edge has been stamped, so there is no post-edge "
                   "cohort to judge. The canary is idle, not alive — a loop that "
                   "consults it in this state must not write.")
    boundary = json.loads(bp.read_text(errors="replace"))
    if not isinstance(boundary, dict) or not boundary.get("at"):
        return CanaryVerdict(
            status="no-boundary",
            reason="the edge boundary file exists but is unreadable or carries no "
                   "timestamp. A canary that cannot locate its epoch must not "
                   "report 'alive' — the loop halts.")
    at = str(boundary.get("at") or "")

    tp = _trials_path()
    decided = correct = 0
    epochs: set[str] = set()
    if tp.exists():
        for line in tp.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            if not isinstance(t, dict):
                continue
            if t.get("verdict") not in ("correct", "incorrect"):
                continue
            if str(t.get("scored_at") or "") <= at:
                continue                       # pre-edge — pooling forbidden
            decided += 1
            correct += t["verdict"] == "correct"
            epochs.add(str(t.get("snapshot_built_at") or "?"))

    if decided < MIN_DECIDED:
        return CanaryVerdict(
            status="insufficient",
            reason=f"only {decided} decided post-edge trials against a floor of "
                   f"{MIN_DECIDED} — a small cohort can sit below the bar by luck "
                   f"alone, and a loop must not write while the canary cannot speak.",
            decided=decided, correct=correct, boundary=at,
            epochs_seen=sorted(epochs))

    lo = _wilson_lo(correct, decided)
    if lo < PRE_EDGE_WILSON_LO:
        return CanaryVerdict(
            status="collapsed",
            reason=f"post-edge Wilson lower bound {lo:.3f} is below the frozen "
                   f"pre-edge bound {PRE_EDGE_WILSON_LO} on {decided} trials — the "
                   f"degradation exceeds what sampling noise explains. HALT.",
            decided=decided, correct=correct, accuracy=correct / decided,
            wilson_lo=lo, boundary=at, epochs_seen=sorted(epochs))

    return CanaryVerdict(
        status="alive",
        reason=f"post-edge Wilson lower bound {lo:.3f} clears the frozen pre-edge "
               f"bound {PRE_EDGE_WILSON_LO} on {decided} trials.",
        decided=decided, correct=correct, accuracy=correct / decided,
        wilson_lo=lo, boundary=at, epochs_seen=sorted(epochs))
