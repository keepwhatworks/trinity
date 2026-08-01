"""Whose dissent is worth hearing — per-model, from council disagreements.

THE QUESTION THIS ANSWERS, and why it is not the one `trust` answers. The
disagreement ledger asks "which model's side did the user's later work take". This
asks something different and complementary: **when a model argues the losing side,
was its point still worth keeping?** A model can be wrong often and still be the one
you want in the room; another can be wrong often and contribute nothing. Those are
different failure modes and only the second is a reason to drop a member.

Founder framing (2026-07-25): "if a model continuously differs on something and it is
overruled that means it's not good. however, if it differs and it is merged, that
means it has something smart to say."

TWO MEASURES, both per model:
  UPHELD RATE      — of the two-sided disputes this model took a side in, how often
                     its side survived prosecution. Low = usually on the wrong side.
  GRAFT-WHEN-LOST  — of the disputes it LOST, how often the chairman merged its claim
                     into the answer anyway. High = loses the argument, still
                     contributes. This is the "smart dissent" signal.

WHAT IT IS NOT. Both are CHAIRMAN JUDGEMENT, not behaviour. "Grafted" means the
chairman judged the claim worth keeping — not that the user's later work endorsed it.
The behaviour-validated per-model signal remains the disagreement ledger. The
behavioural version of graft-value was attempted 2026-07-25 and is not yet testable:
joining grafts to the ledger yields ~30 matched claims total, so per-model cells run
4-17 and nothing approaches significance. It becomes testable as the ledger accrues.

MEASURED AT INTRODUCTION (re-chaired corpus, 634 councils, one chairman). Note these
exclude disputes the chairman left 'unresolved' — an undecided dispute is not evidence
about either side, and counting it as one inflates every rate:
    codex        upheld 61% [56,65] n=512   graft-when-lost 69% [62,75]
    claude       upheld 57% [53,61] n=581   graft-when-lost 56% [50,62]
    antigravity  upheld 23% [19,27] n=363   graft-when-lost 42% [36,47]
Gemini's upheld CI does not overlap either other lab, and independently reproduces its
behavioural ledger number (33%). Codex-vs-Gemini graft-when-lost also does not overlap:
when GPT loses it is still worth keeping ~2/3 of the time; when Gemini loses it is
usually just wrong.

SOURCES. Requires `resolution` on disputed claims, which shipped 2026-07-22 — so
historical `~/.trinity/council_outcomes` mostly lack it and the re-chaired corpus is
the dense source today. Production outcomes accrue it as new councils run. Both are
read; every readout discloses which supplied the rows.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from math import sqrt
from typing import Any, Iterable

from .council_schema import normalize_provider_slug, same_provider

# Below this many two-sided disputes a model gets no verdict. Same stance as the
# trust tally's display floor: a rate over a handful of disputes is not a finding.
MIN_DISPUTES = 25

# GRAFT-WHEN-LOST IS WITHDRAWN (2026-07-28). It was published all week as
# codex 69% / claude 56% / antigravity 42%. Both ways of computing it are wrong:
#
#   COUNCIL-WIDE (as shipped): one graft from a provider credited EVERY claim that
#   provider lost in the same council. Two losses and one graft scored two grafts,
#   so every rate was inflated by an unknown factor. Found by an independent GPT-5.6
#   review of this file.
#
#   PER-CLAIM (the obvious fix): grafts carry their own `claim` text, but the chairman
#   writes them as merged prose rather than as references. Measured: 3 of 796 grafts
#   (0.4%) match any disputed claim by text, so per-claim attribution scores ~0 for
#   everyone — a different wrong answer, not a correction.
#
# The metric was ALREADY behaviourally null (15 rescues vs 15 contaminations against
# the ledger's answer key, net +0). A quantity that is null in outcome and unsound in
# computation does not get a number. UPHELD RATE stands and is unaffected.
GRAFT_WHEN_LOST_IS_NOT_COMPUTABLE = (
    "withdrawn 2026-07-28: council-wide attribution overcounts, per-claim matches "
    "0.4% of grafts, and the behavioural test was already net +0"
)
RECHAIRED = pathlib.Path("internal/experiments/rechair_backfill.jsonl")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


@dataclass
class DissentRecord:
    model: str
    upheld: int = 0
    overruled: int = 0
    grafted_when_overruled: int = 0

    @property
    def disputes(self) -> int:
        return self.upheld + self.overruled

    @property
    def trustworthy(self) -> bool:
        """A verdict is only published above the dispute floor."""
        return self.disputes >= MIN_DISPUTES

    def to_dict(self) -> dict[str, Any]:
        lo, hi = wilson(self.upheld, self.disputes)
        return {
            "model": self.model,
            "disputes": self.disputes,
            "upheld": self.upheld,
            "overruled": self.overruled,
            "upheld_rate": (self.upheld / self.disputes) if self.disputes else None,
            "upheld_ci": [lo, hi],
            "grafted_when_overruled": self.grafted_when_overruled,
            # WITHDRAWN 2026-07-28 — see GRAFT_WHEN_LOST_IS_NOT_COMPUTABLE below.
            # Emitting None rather than a rate, because both available attributions
            # are wrong and 0% would read as a measurement.
            "graft_when_lost_rate": None,
            "graft_when_lost_uncomputable": GRAFT_WHEN_LOST_IS_NOT_COMPUTABLE,
            "trustworthy": self.trustworthy,
        }


def _side_that_survived(entry: dict) -> tuple[list[str], list[str]] | None:
    """(winning_side, losing_side) for one disputed claim, or None if undecidable.

    `resolution` names WHICH SIDE survives, so the winner is recovered by finding the
    first provider it mentions and checking which side that provider argued. Reading
    the prose for truth-of-the-claim instead was a design bug caught 2026-07-25.
    Abstains rather than guessing when no side can be identified.
    """
    res = str(entry.get("resolution") or "").strip().lower()
    if not res or "unresolved" in res:
        return None
    fors = [str(normalize_provider_slug(str(x).lower())) for x in (entry.get("providers_for") or [])]
    against = [str(normalize_provider_slug(str(x).lower())) for x in (entry.get("providers_against") or [])]
    if not fors or not against:
        return None  # one-sided: nobody dissented, nothing to score
    # Find every provider mention and decide by the NEAREST survival verb, honouring
    # negation. First-mention-wins was wrong and shipped: on
    #   "codex's claim does not survive; claude's side survives because ..."
    # it returned codex, because codex is mentioned first. Found 2026-07-28 by an
    # independent GPT-5.6 review of this file. The original guard did not catch it
    # because its fixture ("codex survives - claude conceded") happens to put the
    # SURVIVOR first, so first-mention and truth coincided.
    NEG = ("does not survive", "doesn't survive", "did not survive", "fails", "loses",
           "is overruled", "does not hold", "doesn't hold", "is rejected", "cannot stand")
    POS = ("survives", "holds", "prevails", "wins", "is upheld", "stands")
    hits: list[tuple[int, str, bool]] = []          # (position, provider, survived)
    for cand in set(fors + against):
        aliases = {cand}
        if cand == "codex":
            aliases.add("gpt")
        if cand == "antigravity":
            aliases.add("gemini")
        for alias in aliases:
            start = 0
            while True:
                i = res.find(alias, start)
                if i < 0:
                    break
                start = i + 1
                window = res[i:i + 90]           # the clause following the mention
                neg = min((window.find(n) for n in NEG if n in window), default=-1)
                pos_ = min((window.find(v) for v in POS if v in window), default=-1)
                if neg < 0 and pos_ < 0:
                    continue                      # mentioned, no verdict verb nearby
                if neg >= 0 and (pos_ < 0 or neg <= pos_):
                    hits.append((i, cand, False))
                else:
                    hits.append((i, cand, True))
    if not hits:
        return None
    survivors = {c for _, c, ok in hits if ok}
    losers = {c for _, c, ok in hits if not ok}
    # A provider claimed both survived and did not is unreadable prose. Abstain
    # rather than pick, which is the same stance the unresolved branch takes.
    survivors -= losers
    if not survivors:
        # Only negations: the side NOT negated survives, if that is unambiguous.
        remaining = (set(fors) | set(against)) - losers
        if len(remaining) != 1:
            return None
        named = remaining.pop()
    elif len(survivors) == 1:
        named = survivors.pop()
    else:
        return None                               # both sides "survive": unreadable
    return (fors, against) if named in fors else (against, fors)


def compute(rows: Iterable[dict]) -> dict[str, DissentRecord]:
    """rows = {"routing_label": {...}} dicts from either store."""
    out: dict[str, DissentRecord] = {}
    for row in rows:
        rl = row.get("routing_label") or {}
        # PER-CLAIM, not per-council. This was a council-wide set applied inside the
        # per-claim loop, so one graft from a provider credited EVERY claim that
        # provider lost in that council: two losses and one graft scored two grafts.
        # It inflated every published graft-when-lost rate. Found 2026-07-28 by an
        # independent GPT-5.6 review.
        #
        # Grafts carry their own `claim` text, so attribution is a text match against
        # the disputed claim rather than a council-level join. Matching is prefix-based
        # because the chairman truncates and rephrases; a graft that matches nothing is
        # dropped rather than spread across the council.
        def _norm(x: str) -> str:
            return " ".join(str(x or "").lower().split())[:60]

        grafts_by_claim: dict[str, set[str]] = {}
        for g in (rl.get("grafts") or []):
            src = g.get("from")
            if not src:
                continue
            key = _norm(g.get("claim"))
            if not key:
                continue
            grafts_by_claim.setdefault(key, set()).add(
                str(normalize_provider_slug(str(src).lower())))

        def _grafters_for(claim_text: str) -> set[str]:
            k = _norm(claim_text)
            if not k:
                return set()
            if k in grafts_by_claim:
                return grafts_by_claim[k]
            for gk, provs in grafts_by_claim.items():
                if gk.startswith(k[:40]) or k.startswith(gk[:40]):
                    return provs
            return set()

        for entry in (rl.get("disagreed_claims") or []):
            sides = _side_that_survived(entry)
            if sides is None:
                continue
            winners, losers = sides
            for p in winners:
                out.setdefault(p, DissentRecord(p)).upheld += 1
            grafters = _grafters_for(entry.get("claim"))
            for p in losers:
                rec = out.setdefault(p, DissentRecord(p))
                rec.overruled += 1
                if any(same_provider(p, g) for g in grafters):
                    rec.grafted_when_overruled += 1
    return out


def load_rows() -> tuple[list[dict], dict[str, int]]:
    """Read both stores; return (rows, per-source counts) so any readout can disclose
    where its evidence came from."""
    from .state_paths import council_outcomes_dir

    rows: list[dict] = []
    src = {"production": 0, "rechaired": 0}
    try:
        for f in council_outcomes_dir().glob("*.json"):
            try:
                o = json.loads(f.read_text())
            except Exception:
                continue
            rl = o.get("routing_label") or {}
            if any(d.get("resolution") for d in (rl.get("disagreed_claims") or [])):
                rows.append(o)
                src["production"] += 1
    except Exception:
        pass
    if RECHAIRED.exists():
        for line in RECHAIRED.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                    src["rechaired"] += 1
                except Exception:
                    continue
    return rows, src


def summarize() -> dict[str, Any]:
    rows, src = load_rows()
    recs = compute(rows)
    ranked = sorted(recs.values(), key=lambda r: -r.disputes)
    return {
        "sources": src,
        "councils_read": len(rows),
        "min_disputes": MIN_DISPUTES,
        "models": [r.to_dict() for r in ranked],
        "note": (
            "chairman judgement, not behaviour: 'grafted' means the chairman judged the "
            "claim worth keeping, NOT that the user's later work endorsed it. The "
            "behaviour-validated per-model signal is the disagreement ledger."
        ),
    }
