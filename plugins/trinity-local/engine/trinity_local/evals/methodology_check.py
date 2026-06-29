"""Methodology scanner for the eval pipeline — review the data, flag the bugs.

The eval's whole credibility rests on the methodology, and methodology bugs are
quiet: they don't crash, they just make the number mean something other than what
it claims (the green-while-degenerate shape, data_sampling_principle). Two were
found by hand on 2026-06-08 — a length confound (the judge agrees because the
preferred side is shorter, not because it read the taste) and an n=12 noise pick.
This scans for that whole class so the next one isn't found by hand.

Every check is LOCAL (no model dispatch) and PRIVACY-SAFE (reports counts + metrics,
never raw prompt/response text). It reviews the built eval set + the human-labelled
preference pairs and emits findings with a severity, so `eval-audit` can show "here's
what would let a skeptic poke a hole, and what to do about it."

(Judge-behaviour checks that need dispatch — position bias, a placebo/shuffled-label
null test, self-preference — are a deliberate follow-on; this is the no-quota,
always-runnable data review.)
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class MethodologyFinding:
    severity: str   # "ok" | "info" | "warn" | "risk"
    name: str
    metric: str     # the headline number (privacy-safe)
    detail: str     # what it means + what to do

    def to_dict(self) -> dict:
        return {"severity": self.severity, "name": self.name, "metric": self.metric, "detail": self.detail}


_SEV_RANK = {"risk": 0, "warn": 1, "info": 2, "ok": 3}


def _human_model_sides(pair):
    human = pair.option_a if pair.human_side == "A" else pair.option_b
    model = pair.option_b if pair.human_side == "A" else pair.option_a
    return human, model


def audit_eval_methodology(eval_set, pairs) -> list[MethodologyFinding]:
    """Run the local methodology battery over a built eval set + its preference
    pairs. Returns findings sorted worst-first. Pure + privacy-safe."""
    from .builder import _norm_eval_text

    items = list(getattr(eval_set, "items", []) or [])
    findings: list[MethodologyFinding] = []
    n_items = len(items)
    n_pairs = len(pairs)

    # 1. AXIS IMBALANCE — if one rejection axis dominates, the aggregate is really
    #    that axis wearing a trenchcoat. Report per-axis, never just the headline.
    axc = Counter(it.rejection_type for it in items)
    if n_items:
        top_axis, top_n = axc.most_common(1)[0]
        share = top_n / n_items
        sev = "risk" if share > 0.70 else "warn" if share > 0.55 else "ok"
        findings.append(MethodologyFinding(
            sev, "axis_imbalance", f"{top_axis} = {share*100:.0f}% of {n_items}",
            f"{top_axis} dominates the set; the aggregate score mostly measures it. "
            "Lead with the per-axis breakdown, not a single headline number.",
        ))

    # 2. LENGTH CONFOUND — if your preferred side is systematically shorter/longer,
    #    a judge with a length prior 'agrees' for the wrong reason (it's measuring
    #    conciseness, not your taste). This is the one found by hand 2026-06-08.
    shorter = longer = 0
    for p in pairs:
        human, model = _human_model_sides(p)
        if len(human) < len(model):
            shorter += 1
        elif len(human) > len(model):
            longer += 1
    n_len = shorter + longer
    if n_len:
        max_side = max(shorter, longer)
        frac = max_side / n_len
        sev = "risk" if frac > 0.70 else "warn" if frac > 0.60 else "ok"
        direction = "shorter" if shorter >= longer else "longer"
        findings.append(MethodologyFinding(
            sev, "length_confound",
            f"preferred side {direction} in {max_side}/{n_len}",
            "Your preferred answers skew one length — a judge biased toward that "
            "length would score high without reading your taste. Control for it: "
            "report agreement split by length-match, or length-balance the pairs.",
        ))

    # 3. THIN GOLD — single-token corrections ('yes'/'5'/'start') carry almost no
    #    taste signal for a judge to align to; too many = a weak answer key.
    thin = sum(1 for p in pairs if len((_human_model_sides(p)[0]).split()) < 3)
    if n_pairs:
        share = thin / n_pairs
        sev = "warn" if share > 0.30 else "info" if share > 0.15 else "ok"
        findings.append(MethodologyFinding(
            sev, "thin_gold", f"{thin}/{n_pairs} preferred answers < 3 words",
            "Very terse 'golds' carry little signal — a judge can't tell taste from "
            "noise on them. Consider weighting or filtering ultra-short corrections.",
        ))

    # 4. BASIN/TOPIC CONCENTRATION — if one topic basin dominates, 'your taste' is
    #    really 'your taste in that one topic'.
    bc = Counter(it.basin_id for it in items if it.basin_id)
    if bc:
        _, top_bn = bc.most_common(1)[0]
        share = top_bn / sum(bc.values())
        sev = "warn" if share > 0.45 else "info" if share > 0.30 else "ok"
        findings.append(MethodologyFinding(
            sev, "topic_concentration", f"top basin = {share*100:.0f}%",
            "One topic dominates the corpus the eval is drawn from; the score "
            "generalises less than 'your taste' implies. Surface the topic spread.",
        ))

    # 5. SAMPLE SIZE — the n=12 trap. Tie this to the judge-pick floor.
    sev = "risk" if n_pairs < 15 else "warn" if n_pairs < 40 else "ok"
    findings.append(MethodologyFinding(
        sev, "sample_size", f"n = {n_pairs} preference pairs",
        "Judge agreement needs >=15 pairs to clear sampling noise and >=40 for a "
        "tight estimate; below that, any ranking is noise (the n=12 case).",
    ))

    # 6. PROVENANCE COVERAGE — self-preference ('does a judge favour its own family')
    #    can only be measured where the rejected response's producer is known.
    with_prov = sum(1 for it in items if getattr(it, "provider_of_rejected_response", None))
    if n_items:
        share = with_prov / n_items
        sev = "info" if share < 0.5 else "ok"
        findings.append(MethodologyFinding(
            sev, "provenance_coverage", f"{with_prov}/{n_items} have a known producer",
            "Self-preference (a judge favouring its own model's outputs) is only "
            "measurable where we know which model produced the rejected answer. "
            "Low coverage = that check can't run on most items.",
        ))

    # 7. DEGENERATE-GOLD LEAK — prompt == gold makes every model pass (#247). The
    #    builder gates it; this is the belt-and-suspenders that it's actually 0.
    leak = sum(1 for it in items if _norm_eval_text(it.prompt) == _norm_eval_text(it.user_substitute))
    findings.append(MethodologyFinding(
        "risk" if leak else "ok", "degenerate_gold_leak", f"{leak} prompt==gold items",
        "If the prompt already contains the preferred answer, every model 'passes' "
        "and the item measures nothing. Must be 0 (the builder drops these).",
    ))

    findings.sort(key=lambda f: _SEV_RANK.get(f.severity, 9))
    return findings
