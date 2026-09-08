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
        return {LAB_OF.get(p, p) for p in self.votes}

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


def triage(kernel: Kernel, panel: Panel) -> Triage:
    relevant_kernel = kernel.ran and kernel.relevant
    if relevant_kernel and kernel.green is False:
        return Triage(
            "STOP", "a relevant test is red; the kernel wins over any consensus",
            false_green=panel.consensus_pass())
    if relevant_kernel and kernel.green is True and panel.consensus_pass():
        return Triage("SKIP", "relevant test green and three labs agree")
    if not kernel.ran and not panel.ran:
        return Triage("READ", "no test ran and no panel ran; nothing vouched for this")
    if relevant_kernel and kernel.green is True:
        return Triage("READ", "test green but no three-lab consensus"
                      + (" (panel split)" if panel.split() else " (panel not run)"))
    if kernel.ran and not kernel.relevant:
        return Triage("READ", "a test ran but it does not exercise the changed files")
    return Triage("READ", "no relevant test; "
                  + ("panel split" if panel.split()
                     else "panel agreed, but consensus without a kernel is not a skip"))
