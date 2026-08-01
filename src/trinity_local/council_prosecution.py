"""Slice (c): the cross-provider PROSECUTION round — adversarial verification of
a council's DISAGREED claims.

After the parallel round, each disputed claim is handed to the provider(s) who
did NOT make it, prompted to try to BREAK it against the evidence. The chairman
then re-synthesizes over what survived. This is Gauntlet's adversarial synthesis
(the multi-agent gain traces to the prosecutor, not the summary) + Zhang's
decomposition (small, in-distribution sub-calls — one claim, not the whole
question) on Trinity's cross-provider substrate: the refuter is a DIFFERENT
provider than the claim-maker, the one form of scrutiny no single-model harness
can copy.

Per-CLAIM sub-calls, NOT full member re-runs — only the 0-5 disagreed claims get
prosecuted, each by one small targeted call, so the round is cheap by design
(the founder's cost constraint: don't re-dispatch the whole question).

FLAG-GATED (TRINITY_PROSECUTE_ROUND, default OFF) and shipped DORMANT pending its
forward falsifier: on ~15-20 real hard questions, three arms —
  (a) today's chairman, (b) the free prosecutorial-prompt chairman (slice b),
  (c) this round.
(c) becomes default ONLY if it beats (b) by a real margin. If (c) ~= (b), the
extra calls are ceremony and it stays off (the founder's null — "just prompt it
better" — is the pre-registered bar). See
internal/experiments/prosecution_round_forward_eval.py.

This module is PURE decomposition + prompt rendering — no dispatch, no LLM calls,
fully unit-testable. The dispatch + re-synthesis wiring lives in council_runner
behind the flag and reuses the existing second-round scaffolding.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

FLAG_ENV = "TRINITY_PROSECUTE_ROUND"


def prosecution_enabled() -> bool:
    """The round is dormant unless the flag is explicitly on. Default OFF — it
    ships dark until the forward eval proves it beats the free prosecutorial
    prompt (slice b)."""
    return os.environ.get(FLAG_ENV, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ProsecutionAssignment:
    """One disputed claim routed to its cross-provider adversaries."""
    claim: str
    makers: list[str] = field(default_factory=list)      # providers who argued FOR it
    refuters: list[str] = field(default_factory=list)    # members who did NOT make it
    why_matters: str = ""


def _norm(provider: object) -> str:
    return str(provider or "").strip().lower()


def plan_prosecution(
    disagreed_claims: list[dict] | None,
    member_providers: list[str],
) -> list[ProsecutionAssignment]:
    """Pure decomposition: one assignment per disputed claim, refuters = the
    council members who did NOT argue FOR the claim (the cross-provider
    adversary). Claims nobody contests (every member is a maker) are skipped —
    you cannot cross-examine a claim with no opposing side. Returns [] for a
    converged council (no disagreements → no second round)."""
    members = []
    for p in member_providers:
        n = _norm(p)
        if n and n not in members:
            members.append(n)

    assignments: list[ProsecutionAssignment] = []
    for dc in disagreed_claims or []:
        if not isinstance(dc, dict):
            continue
        claim = str(dc.get("claim") or "").strip()
        if not claim:
            continue
        makers = [_norm(p) for p in (dc.get("providers_for") or []) if _norm(p)]
        refuters = [p for p in members if p not in makers]
        if not refuters:
            continue  # no cross-provider adversary available → nothing to test
        assignments.append(
            ProsecutionAssignment(
                claim=claim,
                makers=makers,
                refuters=refuters,
                why_matters=str(dc.get("why_matters") or "").strip(),
            )
        )
    return assignments


def render_refutation_prompt(
    task_text: str,
    assignment: ProsecutionAssignment,
    maker_evidence: str = "",
) -> str:
    """A SMALL, in-distribution sub-call: attack ONE claim. The refuter did not
    make the claim; it is asked to break it against the evidence, or concede
    plainly if it survives — a verdict either way, never a summary."""
    makers = ", ".join(assignment.makers) or "another member"
    parts = [
        "You are cross-examining ONE claim from a multi-model council. You did "
        "not make this claim. Try to BREAK it against the evidence — do not be "
        "agreeable, and do not restate the question.",
        f"Original question:\n{task_text.strip()}",
        f"The claim under scrutiny (argued by {makers}):\n{assignment.claim}",
    ]
    if maker_evidence.strip():
        parts.append(f"The arguing member's supporting text:\n{maker_evidence.strip()}")
    parts.append(
        "Respond in 3-5 sentences:\n"
        "- Verdict: is the claim TRUE, PARTLY TRUE, or FALSE for THIS question? "
        "State which.\n"
        "- The single strongest concrete reason, grounded in the question or the "
        "evidence — not vibes.\n"
        "- If you cannot break it, concede plainly that it survives, and why. "
        "Conceding a strong claim is as useful as breaking a weak one."
    )
    return "\n\n".join(parts)
