"""The skill-revision loop: propose, gate, never silently write.

STATUS, STATED UP FRONT
-----------------------
This is a GROUNDING loop, not a quality loop. It rejects the dominant failure
mode -- plausible-sounding rules with nothing behind them -- and it cannot tell
a good rule from a mediocre one. Saying so here rather than in a footnote,
because the gap between those two is exactly what the field has not solved and
what this repo has now failed to solve four times.

WHY IT IS SHAPED THIS WAY
-------------------------
The founder bent commitment #1 on 2026-08-10 for this loop. Three measured
constraints shape what may be built with that grant:

  a loop without a verifier is HARMFUL, not neutral. CoEvoSkills measures
  auto-revision without one at 41.1% against 53.5% for human-curated skills.
  The entire gain in these systems is the verifier.

  we do not have a quality verifier. Compression reaches 6% of a 20% step
  floor; an LLM judge floor-failed at 60% against 0.70 with ensembles worse at
  55.2%; a static scan reads the claim rather than the fact; the behavioural
  verifier works but grades 11% of artifacts.

  council_c8ddd3e5743cb505 killed the nearest substitute -- approval by
  pre-registered prediction -- on a category error: `held` resolves a
  PREDICTION, not a RULE.

So the loop does the part that IS decidable. Every proposed rule must cite a
real artifact -- a commit that resolves, a hypothesis id that exists, a numeric
claim that appears in a results file. Fabricated grounding is caught by
checking the artifact, which is an executable question. Whether a well-grounded
rule is WORTH writing stays a human call, and the loop says so rather than
implying otherwise by writing the file itself.

THE THREE GATES
---------------
  1 CONSTITUTION  the edit may not remove or alter a constitutional line.
                  skill_constitution.violates_constitution(). Fails closed.
  2 GROUNDING     every rule cites an artifact that resolves on disk.
  3 HUMAN         nothing is written to a SKILL.md. Proposals land in
                  internal/skill_proposals/ for the founder to accept.

Gate 3 is not timidity. An optimizer scoring throughput would be CORRECT that
"register the falsifier first" costs throughput, and the survey names
uncontrolled self-evolution stripping safety constraints as an open problem.
Gate 1 blocks the obvious form of that; gate 3 covers the forms nobody has
thought of yet.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import trinity_home
from .skill_constitution import violates_constitution

PROPOSAL_DIR_NAME = "skill_proposals"


@dataclass
class Rule:
    """One proposed procedural rule and the artifact it claims to rest on."""
    rule: str
    why: str
    trigger: str
    home: str
    evidence: str
    grounded: bool = False
    grounding_note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", [])}


@dataclass
class LoopResult:
    proposed: list[Rule] = field(default_factory=list)
    rejected: list[Rule] = field(default_factory=list)
    constitution_violations: list[str] = field(default_factory=list)
    written_to: str | None = None

    def to_dict(self) -> dict:
        return {"proposed": [r.to_dict() for r in self.proposed],
                "rejected": [r.to_dict() for r in self.rejected],
                "constitution_violations": self.constitution_violations,
                "written_to": self.written_to}


# ------------------------------------------------------------------ gate 2

_SHA = re.compile(r"\b([0-9a-f]{7,40})\b")
_HQ = re.compile(r"\b(hq_\d{3}|res_\d{3}|amd_\d{4}|council_[0-9a-f]{8,})\b")
_NUM = re.compile(r"\b\d+(?:\.\d+)?%|\bp\s*=\s*[\d.e-]+")
# A preference-act id: the user's own correction, which is the strongest
# grounding this loop can have -- gbrain names the same signal "a real, observed
# failure mode". Kept SEPARATE from _SHA rather than folded into it: `_` is a
# word character, so `\b[0-9a-f]{7,40}\b` never matches inside `r_<hex>` and
# every correction-mined rule fell through to "no artifact named" (verified
# 2026-08-13 against the shipped gate before this was written).
_ACT = re.compile(r"\br_[0-9a-f]{16}\b")


def _act_ids(home: Path | None = None) -> frozenset[str]:
    """Every preference-act id on disk.

    Read fresh per call rather than cached at import: the ledger is appended to
    by lens builds while a session is live, and a gate that answers from a stale
    snapshot would reject a correction the user just made.
    """
    base = home or trinity_home()
    out = set()
    # BOTH ledgers. The lens samples 200 recent turn pairs into
    # preference_acts.jsonl; a full-corpus harvest writes
    # preference_acts_full.jsonl. Either file is a real correction the user
    # made, so either grounds a rule.
    #
    # Reading only the first was a producer-asserted / consumer-unverified
    # defect: the loop runner learned to read the full ledger and this gate did
    # not, so a full-corpus run had EVERY rule rejected with "no such
    # correction" while the ids were all genuine. It failed CLOSED, which is the
    # right direction for a grounding gate to be wrong in, and is why the defect
    # cost a re-run rather than a false proposal.
    for name in ("preference_acts.jsonl", "preference_acts_full.jsonl"):
        path = base / "me" / name
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rid = json.loads(line).get("id")
            except json.JSONDecodeError:
                continue
            if rid:
                out.add(rid)
    return frozenset(out)


def check_grounding(rule: Rule, repo: Path, home: Path | None = None) -> Rule:
    """A rule is grounded when its evidence names something that RESOLVES.

    Three admissible kinds, checked in order of how hard they are to fake:
      a git sha that git can resolve
      a preference-act id (`r_<16hex>`) present in the corrections ledger
      a ledger id that appears in an experiments artifact
      a numeric claim that appears verbatim in a results file

    A rule with none is not necessarily wrong -- it is unverifiable, which is
    the same thing as far as this gate is concerned. `hq_060` measured what
    happens when you accept a claim because it is stated well: a marker fires
    on the CLAIM, not the fact, and the scan was silent on 13 of 18 real
    defects.
    """
    ev = rule.evidence or ""
    for sha in _SHA.findall(ev):
        r = subprocess.run(["git", "cat-file", "-t", sha], cwd=str(repo),
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() == "commit":
            rule.grounded, rule.grounding_note = True, f"commit {sha} resolves"
            return rule
    # Correction ids before hypothesis ids: a rule mined from the user's own
    # correction is grounded in something they DID, not in something we ran.
    acts = _ACT.findall(ev)
    if acts:
        known = _act_ids(home)
        hit = [a for a in acts if a in known]
        if hit:
            rule.grounded = True
            rule.grounding_note = f"preference act(s) on disk: {', '.join(hit[:3])}"
            return rule
        rule.grounding_note = (f"cites {', '.join(acts[:3])} — no such correction in the "
                               "ledger. A fabricated act id is the cheapest possible "
                               "hallucination, so this fails closed.")
        return rule
    ids = _HQ.findall(ev)
    if ids:
        exp = repo / "internal" / "experiments"
        hay = ""
        for f in list(exp.glob("*.jsonl")) + list(exp.glob("*.json")):
            try:
                hay += f.read_text(errors="replace")
            except Exception:
                pass
        try:
            hay += (repo / "internal" / "amendment-ledger.jsonl").read_text(errors="replace")
        except Exception:
            pass
        hit = [i for i in ids if i in hay]
        if hit:
            rule.grounded, rule.grounding_note = True, f"id(s) present on disk: {', '.join(hit)}"
            return rule
        rule.grounding_note = f"cites {', '.join(ids)} — none found in any artifact"
        return rule
    if _NUM.search(ev):
        rule.grounding_note = ("cites a number but names no artifact — a figure without a "
                               "source is the exact shape this gate exists to reject")
        return rule
    rule.grounding_note = "no commit, no ledger id, no artifact named"
    return rule


# ------------------------------------------------------------------ the loop

def run_loop(rules: list[Rule], *, target: Path, repo: Path,
             home: Path | None = None) -> LoopResult:
    """Gate a batch of proposed rules against one skill file.

    Pure with respect to the skill file: `target` is READ, never written.
    """
    res = LoopResult()
    before = target.read_text(errors="replace") if target.exists() else ""

    for r in rules:
        r = check_grounding(r, repo, home=home)
        (res.proposed if r.grounded else res.rejected).append(r)

    if res.proposed:
        # The edit a human would make if they accepted everything, checked
        # against the constitution BEFORE it is offered rather than after.
        addition = "\n\n".join(
            f"**{r.rule}**\n\n{r.why} ({r.evidence})" for r in res.proposed)
        after = before + "\n\n" + addition
        violates, reasons = violates_constitution(before, after)
        if violates:
            res.constitution_violations = reasons
            res.proposed, res.rejected = [], res.proposed + res.rejected

    out_dir = (home or trinity_home()) / PROPOSAL_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{target.stem}-proposal.json"
    dest.write_text(json.dumps(
        {"target": str(target), **res.to_dict()}, indent=2))
    res.written_to = str(dest)
    return res
