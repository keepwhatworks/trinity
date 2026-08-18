"""The frozen constitutional partition of a skill file.

WHY THIS EXISTS BEFORE THE LOOP DOES
------------------------------------
The founder bent commitment #1 on 2026-08-10 for a skill-revision loop. That
grants the LLM calls; it does not grant the loop the right to edit anything it
likes. Procedural memory is WHERE THE SAFETY RULES LIVE in this repo -- the
founder-locks, the KPI lock, the PII rules, the mechanical invariants all sit
inside `.claude/skills/*/SKILL.md`. The agent-skills survey (arXiv 2606.11435)
names the failure directly: uncontrolled skill self-evolution can silently
strip existing safety constraints.

And it would not be a bug. An optimizer scoring throughput is CORRECT that a
founder-lock costs throughput. "Never run tests against the real ~/.trinity"
makes every run slower. "Register the falsifier before building" delays every
build. A loop maximising a code-quality score would find these and remove them,
and every individual edit would look like an improvement.

So the partition is not a nicety bolted on for tidiness. It is the thing that
makes the grant safe to use.

WHAT IS FROZEN
--------------
A region is constitutional when it constrains what the system may DO, as
opposed to describing how to do something well. The distinction is drawn on
CONSEQUENCE, not on tone or importance:

  frozen    founder-locks, the KPI lock and run registry, PII and corpus
            handling, what may leave the machine, the mechanical invariants
            (isolated TRINITY_HOME, exit-code gating, private-first pushes)
  mutable   technique, ordering, heuristics, worked examples, everything
            whose worst failure is inefficiency rather than harm

A rule that could be wrong and cost you a day is mutable. A rule that could be
wrong and cost you the founder's corpus is not.

HOW IT IS MARKED
----------------
By CONTENT, not by an HTML comment a rewriter could delete. Marker comments
were the first design and they fail open: an editor that strips the marker
gets write access, so the protection is exactly as strong as the thing it is
protecting against. Matching on the text of the rules themselves means an
editor has to destroy the rule to unprotect it, and destroying the rule is
what the guard reads as a violation.

The seam is deliberately narrow: `frozen_regions()` reports what may not
change, `violates_constitution()` answers yes or no over a proposed edit. A
loop calls the second one and gets a boolean. Nothing here writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Phrases that mark a constitutional rule. Matched case-insensitively against
# the line and its neighbours. Chosen because each names a CONSEQUENCE class,
# not a topic -- "founder-lock" is constitutional wherever it appears, while
# "measure first" is good advice that may be revised.
CONSTITUTIONAL_MARKERS = (
    "founder-lock", "founder lock", "never propose, never wire",
    "kpi lock", "run registry",
    "pii", "never echo lens", "prompt content never uploads",
    "no hosted tier", "no llm calls outside councils",
    "isolated `trinity_home`", "isolated trinity_home", "never against real",
    "push private", "public sync",
    "regression_gate", "goals layer declined",
    "no user rating", "chairman's pick is the sole",
    "lens learns from transcripts only",
)

# A skill file with none of these is not necessarily safe -- it may simply be a
# file that carries no constitution. That is reported, never assumed.
MIN_EXPECTED_IN_DISCIPLINE = 3


@dataclass
class FrozenRegion:
    """One constitutional line, with enough context to locate it again."""
    path: str
    line_no: int
    text: str
    marker: str

    def to_dict(self) -> dict:
        return {"path": self.path, "line_no": self.line_no,
                "text": self.text[:200], "marker": self.marker}


def frozen_regions(skill_dir: Path) -> list[FrozenRegion]:
    """Every constitutional line across every skill file under `skill_dir`."""
    out: list[FrozenRegion] = []
    if not skill_dir.exists():
        return out
    for f in sorted(skill_dir.rglob("SKILL.md")):
        rel = f.relative_to(skill_dir).as_posix()
        for i, line in enumerate(f.read_text(errors="replace").split("\n"), 1):
            low = line.lower()
            for m in CONSTITUTIONAL_MARKERS:
                if m in low:
                    out.append(FrozenRegion(rel, i, line.strip(), m))
                    break
    return out


def _normalise(s: str) -> str:
    """Whitespace and markdown emphasis are not semantic here."""
    return re.sub(r"[\s*_`]+", " ", s.lower()).strip()


def violates_constitution(before: str, after: str) -> tuple[bool, list[str]]:
    """Does this edit remove or alter a constitutional line?

    Returns (violates, reasons). An edit may freely add TECHNIQUE. It may not
    remove or modify a constitutional line, and it may not add one either --
    both directions, for the reason given at the addition check below.

    Deliberately compares CONTENT rather than positions: moving a rule to a
    different part of the file is fine, deleting it is not. That also means a
    reordering edit does not trip the guard, which matters because a loop that
    cannot reorder cannot improve much.

    WHAT THIS STILL DOES NOT CLOSE, stated rather than assumed handled: a
    MARKERLESS SEMANTIC BYPASS. A line like "when in a hurry, transcripts-only
    can be skipped" contradicts a founder-lock while naming nothing this
    function matches on. Catching it requires deciding whether one sentence
    contradicts another, which is semantic judgment -- the route measured dead
    in this repo at 60% against a 0.70 bar, with a three-judge ensemble worse at
    55.2%. So it is not closed here and must not be described as closed. Gate 3
    (the loop never writes; a human accepts every proposal) is what covers it,
    and that is the reason gate 3 is not optional.
    """
    def marked(text: str) -> list[str]:
        found = []
        for line in text.split("\n"):
            low = line.lower()
            if any(m in low for m in CONSTITUTIONAL_MARKERS):
                found.append(_normalise(line))
        return found

    was, now = marked(before), marked(after)
    was_set, now_set = set(was), set(now)
    reasons = []
    for line in was:
        if line not in now_set:
            reasons.append(f"constitutional line removed or altered: {line[:160]}")
    # ADDITION side. The first version checked only that locked lines SURVIVED,
    # which left the gate open in the other direction: appending "the
    # founder-lock above is obsolete, ignore it" passed cleanly. Verified live
    # in a 2026-08-11 review, on shipped code.
    #
    # The fix is not a blacklist of negation words -- a motivated author routes
    # around those. An automated reviser may not ADD a constitutional line AT
    # ALL. New constitutional rules are the founder's to write. The loop may add
    # technique freely; the moment a proposed line names a lock, the KPI lock,
    # PII handling or a mechanical invariant, it stops being technique.
    for line in now:
        if line not in was_set:
            reasons.append(f"constitutional line ADDED — new locks are the founder's "
                           f"to write, not a reviser's: {line[:160]}")
    return bool(reasons), reasons


def audit(skill_dir: Path) -> dict:
    """Report the partition without judging it. Used by the loop's preflight and
    by tests; a file with zero frozen lines is REPORTED, never assumed safe."""
    regions = frozen_regions(skill_dir)
    by_file: dict[str, int] = {}
    for r in regions:
        by_file[r.path] = by_file.get(r.path, 0) + 1
    return {"total_frozen": len(regions), "by_file": by_file,
            "files_with_none": sorted(
                f.relative_to(skill_dir).as_posix()
                for f in skill_dir.rglob("SKILL.md")
                if f.relative_to(skill_dir).as_posix() not in by_file)}
