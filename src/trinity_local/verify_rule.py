"""The verify rule: one output per artifact.

    STOP   a relevant test is red. The kernel wins over any consensus.
    SKIP   a relevant test is green AND three labs that cannot see each other
           agree. The only skip.
    READ   everything else. Blocks until a human has read it.

This is the whole of the product's pre-deploy judgment, and it is a pure
function on purpose. Council fd416f421a583a8c (amd_0204): the measured signal
is vote agreement versus split, and a deterministic function consumes it. No
chairman, no synthesis call, nothing that could reintroduce the correlated
self-evaluation a judge is. Founder 2026-09-05 (res_128): one output, not a
state machine; READ blocks.

WHAT COUNTS
-----------
A kernel is RELEVANT when the test exercises the changed files. A green
kernel that is not relevant is not a kernel for this artifact; it is a green
somewhere else. A panel AGREES when at least three votes from three distinct
labs all pass. Two labs agreeing is not consensus — the panel-size curve
(res_126) puts a two-voter miss at 13% against 8% at three, and the point of
the rule is the independence, not the count.

A panel that said fine on a red kernel is a FALSE GREEN. The rule still says
STOP, and it says so in the result, because that event is the independence
series (consensus_decay.py) and must be logged rather than overridden.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Provider slug -> lab. Three labs is the whole design; a fourth member from
# an existing lab does not make a fourth vote.
# One bucket for every provider whose lab is not declared. Three unknown
# slugs are ONE unknown lab -- see Panel.labs().
_UNKNOWN_LAB = "__unknown_lab__"

LAB_OF = {
    "claude": "anthropic",
    "codex": "openai",
    "antigravity": "google",
}
MIN_LABS = 3


@dataclass(frozen=True)
class Kernel:
    """What the artifact's own test said."""
    ran: bool
    relevant: bool = False
    green: bool | None = None

    @classmethod
    def not_run(cls) -> "Kernel":
        return cls(ran=False)


@dataclass(frozen=True)
class Panel:
    """What the independent readers said. votes: provider -> passed."""
    ran: bool
    votes: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def not_run(cls) -> "Panel":
        return cls(ran=False)

    def labs(self) -> set[str]:
        """Distinct LABS behind the votes, not distinct provider slugs.

        This returned the slug itself for anything not in LAB_OF, so three
        aliases pointing at the same lab -- `claude_a`, `claude_b`, `claude_c`,
        or any three locally-named seats -- satisfied "three distinct labs" and
        produced consensus. That inverts the one claim the product rests on:
        that the readers cannot see each other BECAUSE they are different labs.
        Found by an Astra audit 2026-09-16.

        Every unrecognised slug now collapses into ONE bucket. Unknown
        providers can still vote and can still create a split; what they cannot
        do is manufacture independence by being numerous. A genuinely new lab
        earns its own identity by being added to LAB_OF, which is a deliberate
        act rather than a naming accident.
        """
        return {LAB_OF.get(p, _UNKNOWN_LAB) for p in self.votes}

    def consensus_pass(self) -> bool:
        """Three distinct labs, every vote a pass."""
        return (self.ran and len(self.labs()) >= MIN_LABS
                and bool(self.votes) and all(self.votes.values()))

    def split(self) -> bool:
        return self.ran and bool(self.votes) and not all(self.votes.values()) \
            and any(self.votes.values())


@dataclass(frozen=True)
class Triage:
    outcome: str            # STOP | SKIP | READ
    reason: str
    false_green: bool = False   # panel consensus-pass on a red kernel


# SKIP DOES NOT SHIP. Two councils, six models, unanimous both times
# (amd_0217, amd_0226): the evidence does not license an output that
# authorises omitting human review.
#
# The number SKIP would rest on is P(defect | green test AND unanimous
# approval), and it has never been measured. What IS measured is
# P(no panel doubt | known-defective guard-green mutant) = 12/50, on a
# population selected for carrying a defect. Those are different
# quantities and only the second exists.
#
# Re-entry contract, locked before any data (amd_0222): a one-sided 95%
# UPPER bound on defect probability among would-SKIP changes of at most
# 1%, AND a lower bound of at least 20% on the share of eligible changes
# that receive it, so a policy cannot succeed by approving almost
# nothing. At 1% with zero observed defects that needs roughly 299
# INDEPENDENT observations; repeated mutations of one fix are not
# independent. Until then the branch computes, records `would_skip`, and
# returns READ.
SKIP_SHIPS = False


def triage(kernel: Kernel, panel: Panel) -> Triage:
    relevant_kernel = kernel.ran and kernel.relevant
    if relevant_kernel and kernel.green is False:
        return Triage(
            "STOP", "a relevant test is red; the kernel wins over any consensus",
            false_green=panel.consensus_pass())
    if relevant_kernel and kernel.green is True and panel.consensus_pass():
        if SKIP_SHIPS:
            return Triage("SKIP", "relevant test green and three labs agree")
        return Triage(
            "READ",
            "would_skip: a relevant test is green and three labs agree, but SKIP "
            "does not ship — the defect rate under exactly those conditions has "
            "never been measured")
    if not kernel.ran and not panel.ran:
        return Triage("READ", "no test ran and no panel ran; nothing vouched for this")
    if relevant_kernel and kernel.green is True:
        # THREE causes, not two. "No consensus" was read as split-or-not-run,
        # so a panel that ran and AGREED with fewer than three labs -- which is
        # every review with exclude_lab set, the recommended way to review your
        # own change -- printed "(panel not run)" above "2 of 2 reviewers
        # voted". Found on verify's first real use, 2026-09-27.
        if panel.split():
            why = " (panel split)"
        elif not panel.ran:
            why = " (panel not run)"
        elif panel.votes and not any(panel.votes.values()):
            # Unanimous OBJECTION is not agreement to pass. The three-cause fix
            # of 2026-09-27 read every non-split, ran panel as "agreed", so three
            # FAIL votes printed "panel agreed, but only 3 labs read it; consensus
            # needs three". Reported by a peer session the next day.
            n = len(panel.labs())
            why = f" (all {n} reviewing lab{'s' if n != 1 else ''} objected)"
        else:
            n = len(panel.labs())
            why = (f" (panel agreed, but only {n} lab{'s' if n != 1 else ''} read it; "
                   "consensus needs three)")
        return Triage("READ", "test green but no three-lab consensus" + why)
    if kernel.ran and not kernel.relevant:
        return Triage("READ", "a test ran but it does not exercise the changed files")
    return Triage("READ", "no relevant test; "
                  + ("panel split" if panel.split()
                     else "panel agreed, but consensus without a kernel is not a skip"))
