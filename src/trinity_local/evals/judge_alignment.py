"""Judge-alignment validation — the trust artifact behind the eval card.

The eval scorer asks ONE model to judge whether a candidate response beats a
rejected one on the user's taste. The obvious skeptic attack is "you picked a
judge that favours its own family / you can't trust a model to grade models."

This module answers that attack with a MEASUREMENT, not an assertion. Every
`model_miss` PreferenceAct is already a human-labelled preference pair: the user
PRIVILEGED their own rewrite (`privileged`) over what the model said
(`sacrificed`). So we can hand a candidate judge those two answers — in a
position-balanced A/B — and ask which better matches the user's taste, then check
how often it picks the side the HUMAN actually chose.

That yields a per-user, human-anchored trust statement:

    "Judge <model> agrees with YOUR own past corrections N/M of the time
     (agreement = X%). It's the judge because it scored highest on that."

`pick_most_aligned_judge` runs this for each candidate and selects the
best-aligned one — so the judge is chosen by measurement, and the number ships on
the card / methodology page. The validation set is FREE (the user's own
rejections) and per-user, so the claim is "aligned with *your* taste," which is
stronger than generic alignment.

Position-balancing (the human side alternates A/B deterministically by index)
neutralises LLM position bias, so a judge that blindly answers "A" lands at ~50%,
not a fake-high score. No model dispatch happens here unless `validate_judge` is
called with a real provider; the pair-builder + parser + scorer are pure and unit
-tested with fake judges.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PreferencePair:
    """One human-labelled A/B preference, derived from a model_miss act.

    `human_side` is "A" or "B" — the side carrying `privileged` (what the user
    actually chose). A judge "agrees" when it picks `human_side`.
    """
    pair_id: str
    axis: str  # rejection kind: REFRAME / COMPRESSION / REDIRECT / SHARPENING
    option_a: str
    option_b: str
    human_side: str  # "A" | "B"
    source_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JudgeAlignmentResult:
    """How well one judge agrees with the user's own corrections."""
    judge_provider: str
    n_pairs: int          # total pairs presented
    n_parsed: int         # pairs the judge gave a parseable A/B for
    n_agreed: int         # of those, how many matched the human's choice
    by_axis: dict = field(default_factory=dict)  # axis -> {n, agreed}
    unparsed: int = 0     # judge output that wasn't a clean A/B
    # Length-controlled split: of the PARSED pairs, how the judge did when the
    # human's chosen side was the SHORTER answer vs the LONGER one. Disentangles
    # "agrees because it shares your taste" from "agrees because it prefers short"
    # — the length confound the methodology scanner flags at the data level.
    n_short_parsed: int = 0
    n_short_agreed: int = 0
    n_long_parsed: int = 0
    n_long_agreed: int = 0
    # Negative control: agreement after SHUFFLING the human-side labels (averaged over
    # many seeded permutations). A genuinely-aligned judge collapses to ~chance here —
    # proof the headline agreement reflects the real pair→human-side mapping, not an
    # artifact (a constant-answer bias, a parser quirk). None when too few parsed pairs.
    shuffle_null: float | None = None

    @property
    def agreement(self) -> float | None:
        """Fraction of PARSED pairs where the judge picked the human's side.
        None when nothing parsed (can't claim alignment from zero signal)."""
        return (self.n_agreed / self.n_parsed) if self.n_parsed else None

    @property
    def agreement_when_human_shorter(self) -> float | None:
        return (self.n_short_agreed / self.n_short_parsed) if self.n_short_parsed else None

    @property
    def agreement_when_human_longer(self) -> float | None:
        return (self.n_long_agreed / self.n_long_parsed) if self.n_long_parsed else None

    @property
    def length_gap(self) -> float | None:
        """How much MORE the judge agrees when the human's pick is the shorter
        side — the length-confound diagnostic. A large positive gap means the
        agreement is partly a conciseness prior, not pure taste; ~0 means the
        alignment holds regardless of length (the trust-worthy case). None when
        either bucket is too thin (< MIN_LENGTH_BUCKET) to compare honestly."""
        s, l = self.agreement_when_human_shorter, self.agreement_when_human_longer
        if s is None or l is None:
            return None
        if min(self.n_short_parsed, self.n_long_parsed) < MIN_LENGTH_BUCKET:
            return None
        return s - l

    def to_dict(self) -> dict:
        return {
            "judge_provider": self.judge_provider,
            "n_pairs": self.n_pairs,
            "n_parsed": self.n_parsed,
            "n_agreed": self.n_agreed,
            "agreement": self.agreement,
            "by_axis": self.by_axis,
            "unparsed": self.unparsed,
            "n_short_parsed": self.n_short_parsed,
            "n_short_agreed": self.n_short_agreed,
            "n_long_parsed": self.n_long_parsed,
            "n_long_agreed": self.n_long_agreed,
            "agreement_when_human_shorter": self.agreement_when_human_shorter,
            "agreement_when_human_longer": self.agreement_when_human_longer,
            "length_gap": self.length_gap,
            "shuffle_null": self.shuffle_null,
        }


# A length bucket needs at least this many parsed pairs before its agreement rate
# is worth comparing — below it the shorter-vs-longer gap is just sampling noise.
MIN_LENGTH_BUCKET = 5


from ._textnorm import norm_for_compare as _norm, strip_code_fences  # shared eval text helpers


def build_preference_pairs(limit: int | None = None) -> list[PreferencePair]:
    """Human-labelled A/B pairs from the model_miss subset of the ledger.

    Position-balanced: the human-preferred side alternates A/B by index, so a
    judge with a constant-answer position bias scores ~50%, not a fake-high
    number. Skips degenerate pairs (privileged == sacrificed, or either empty) —
    they carry no preference signal, same floor-guard as the eval builder.
    """
    from ..me.preference_acts import MODEL_MISS, iter_preference_acts

    pairs: list[PreferencePair] = []
    idx = 0
    for act in iter_preference_acts():
        if act.trigger != MODEL_MISS:
            continue
        privileged = (act.privileged or "").strip()   # the human's choice
        sacrificed = (act.sacrificed or "").strip()    # what the model said
        if not privileged or not sacrificed:
            continue
        if _norm(privileged) == _norm(sacrificed):
            continue  # no preference signal
        # Alternate which side carries the human choice to cancel position bias.
        if idx % 2 == 0:
            option_a, option_b, human_side = privileged, sacrificed, "A"
        else:
            option_a, option_b, human_side = sacrificed, privileged, "B"
        pairs.append(PreferencePair(
            pair_id=f"pp_{idx}",
            axis=act.kind or "",
            option_a=option_a,
            option_b=option_b,
            human_side=human_side,
            source_id=act.id or "",
        ))
        idx += 1
        if limit is not None and len(pairs) >= limit:
            break
    return pairs


JUDGE_VALIDATION_PROMPT = """You are calibrating against a specific user's taste, drawn from their personal lens.

The user's taste (excerpt):
---
{lens_excerpt}
---

Two responses were given to the same request. Decide which one BETTER matches
THIS user's taste — the framing, length, shape, and precision they prefer.

Response A:
---
{option_a}
---

Response B:
---
{option_b}
---

Answer with ONLY a single character: A or B. No prose, no punctuation, no JSON.
"""


# Public-dataset variant: the ground truth in RewardBench / Arena / HH-RLHF is
# GENERIC human preference, not this user's taste — so the judge must be asked the
# same question those labels answer ("which is the better response"), NOT "which
# matches the user's taste". Reuses the {option_a}/{option_b} slots; the unused
# {lens_excerpt} kwarg passed by validate_judge is harmless (str.format ignores it).
GENERIC_PREFERENCE_PROMPT = """Two responses were given to the same request. Decide which one is the
BETTER response — more helpful, accurate, and appropriate — the one a careful
human evaluator would prefer.

Response A:
---
{option_a}
---

Response B:
---
{option_b}
---

Answer with ONLY a single character: A or B. No prose, no punctuation, no JSON.
"""


def _parse_ab(raw: str) -> str | None:
    """Extract the judge's A/B verdict. Tolerant of fences/prose around it, but
    returns None (not a guess) when no clean single-letter verdict is present —
    an unparsed judgement must NOT silently count as agreement or disagreement."""
    if not raw:
        return None
    cleaned = strip_code_fences(raw)
    # Exact single letter (the requested form).
    if cleaned in ("A", "B"):
        return cleaned
    # JSON-ish {"answer":"A"} or {"choice":"B"}.
    m = re.search(r'"(?:answer|choice|verdict|pick)"\s*:\s*"?([AB])\b', cleaned, re.I)
    if m:
        return m.group(1).upper()
    # Leading "A"/"B" with trailing punctuation/prose ("A.", "A — because…").
    m = re.match(r'\s*\(?([AB])\b', cleaned)
    if m:
        return m.group(1).upper()
    # Phrases: "Response A", "Answer: B", "I'd pick A".
    m = re.search(r"\bresponse\s+([AB])\b", cleaned, re.I) or re.search(r"\b([AB])\s+is\s+better\b", cleaned, re.I)
    if m:
        return m.group(1).upper()
    return None


_SHUFFLE_NULL_SEED = 20260608
_SHUFFLE_NULL_TRIALS = 300


def shuffle_null_agreement(
    verdicts: list[tuple[str, str]],
    *,
    trials: int = _SHUFFLE_NULL_TRIALS,
    seed: int = _SHUFFLE_NULL_SEED,
) -> float | None:
    """Negative control for the judge-alignment measurement.

    Re-pairs each judge verdict with a SHUFFLED human side and measures agreement,
    averaged over `trials` seeded permutations. A real, label-driven agreement
    collapses to ~chance here (≈0.5 for position-balanced labels) — so a shuffle-null
    near 0.5 *proves* the headline agreement reflects the actual (pair → human-side)
    mapping rather than an artifact (a constant-answer bias, a parser quirk, an
    unbalanced label set). A shuffle-null that STAYS high is a red flag that the
    agreement number is rigged, not earned. Deterministic (seeded); pure, no I/O.

    `verdicts` is the list of (human_side, judge_verdict) for the PARSED pairs.
    Returns None below 4 parsed pairs (a shuffle of <4 is meaningless)."""
    parsed = [(h, v) for (h, v) in verdicts if h and v]
    n = len(parsed)
    if n < 4:
        return None
    humans = [h for h, _ in parsed]
    judge = [v for _, v in parsed]
    rng = random.Random(seed)
    total = 0.0
    for _ in range(trials):
        shuffled = humans[:]
        rng.shuffle(shuffled)
        total += sum(1 for sh, v in zip(shuffled, judge) if sh == v) / n
    return total / trials


def validate_judge(
    judge_provider: str,
    pairs: list[PreferencePair],
    provider_configs: dict,
    lens_text: str = "",
    *,
    cwd: Path | None = None,
    progress_callback=None,
    prompt_template: str = JUDGE_VALIDATION_PROMPT,
) -> JudgeAlignmentResult:
    """Ask `judge_provider` each A/B pair and measure agreement with the human's
    own choice. Unparseable verdicts are counted as `unparsed`, never as agree or
    disagree (a non-answer must not pad the agreement rate).

    `prompt_template` defaults to the taste-anchored prompt (validation against the
    user's own corrections). Pass GENERIC_PREFERENCE_PROMPT (with lens_text="") to
    validate against a PUBLIC dataset, where the label is generic human preference
    rather than this user's taste.
    """
    from ..providers import make_provider
    from .scorer import _lens_excerpt

    if judge_provider not in provider_configs:
        raise KeyError(
            f"Unknown judge provider '{judge_provider}'. Available: {sorted(provider_configs)}"
        )
    judge = make_provider(provider_configs[judge_provider])
    cwd = cwd or Path.cwd()
    excerpt = _lens_excerpt(lens_text)

    n_parsed = n_agreed = unparsed = 0
    n_short_parsed = n_short_agreed = n_long_parsed = n_long_agreed = 0
    by_axis: dict[str, dict] = {}
    captured_verdicts: list[tuple[str, str]] = []  # (human_side, verdict) for the null
    for i, pair in enumerate(pairs, start=1):
        prompt = prompt_template.format(
            lens_excerpt=excerpt,
            option_a=(pair.option_a or "")[:2000],
            option_b=(pair.option_b or "")[:2000],
        )
        verdict = None
        try:
            result = judge.run(prompt, cwd=cwd)
            if getattr(result, "returncode", 0) == 0 or (result.stdout or "").strip():
                verdict = _parse_ab(result.stdout or "")
        except Exception:
            verdict = None
        if verdict is None:
            unparsed += 1
        else:
            n_parsed += 1
            captured_verdicts.append((pair.human_side, verdict))
            agreed = verdict == pair.human_side
            if agreed:
                n_agreed += 1
            slot = by_axis.setdefault(pair.axis or "", {"n": 0, "agreed": 0})
            slot["n"] += 1
            slot["agreed"] += 1 if agreed else 0
            # Length-controlled bucketing: was the human's chosen side the shorter
            # answer? (only count strict differences — ties carry no length signal).
            human_text = pair.option_a if pair.human_side == "A" else pair.option_b
            model_text = pair.option_b if pair.human_side == "A" else pair.option_a
            if len(human_text) < len(model_text):
                n_short_parsed += 1
                n_short_agreed += 1 if agreed else 0
            elif len(human_text) > len(model_text):
                n_long_parsed += 1
                n_long_agreed += 1 if agreed else 0
        if progress_callback is not None:
            try:
                progress_callback(i, len(pairs), pair, verdict)
            except Exception:
                pass

    return JudgeAlignmentResult(
        judge_provider=judge_provider,
        n_pairs=len(pairs),
        n_parsed=n_parsed,
        n_agreed=n_agreed,
        by_axis=by_axis,
        unparsed=unparsed,
        n_short_parsed=n_short_parsed,
        n_short_agreed=n_short_agreed,
        n_long_parsed=n_long_parsed,
        n_long_agreed=n_long_agreed,
        shuffle_null=shuffle_null_agreement(captured_verdicts),
    )


# A judge is only "chosen" when the evidence is strong enough to MEAN something.
# Found 2026-06-08: on a real n=12 run the picker declared Gemini the winner off a
# 9-vs-7 lead — 2 pairs, p≈0.07 vs random, indistinguishable from Claude. That's the
# leader-by-noise trap (data_sampling_principle). So gate the pick on: enough parsed
# pairs, the winner beats random, AND a clear lead over the runner-up.
MIN_ALIGNMENT_PAIRS = 15      # below this, an agreement rate is too noisy to choose on
MIN_ALIGNMENT_MARGIN = 0.10   # the winner must lead the runner-up by >= 10 points


def select_aligned_judge(results: dict[str, JudgeAlignmentResult]) -> tuple[str | None, str]:
    """Pick the most-aligned judge ONLY when the evidence clears the noise floor.
    Returns (chosen|None, human-readable reason). The single source of truth for
    the selection — both pick_most_aligned_judge and the CLI use it, so the
    significance gate can't drift between them.

    Refuses to choose when: <MIN_ALIGNMENT_PAIRS parsed pairs (too noisy), the
    leader doesn't beat random 50%, or the lead over the runner-up is within noise
    (<MIN_ALIGNMENT_MARGIN). Refusing → the caller falls back to a non-target
    heuristic judge; better an honest "no clear winner" than a noise pick.
    """
    eligible = [
        r for r in results.values()
        if r.n_parsed >= MIN_ALIGNMENT_PAIRS and r.agreement is not None
    ]
    if not eligible:
        return None, (
            f"no judge cleared the {MIN_ALIGNMENT_PAIRS}-pair floor — agreement on "
            f"fewer pairs is too noisy to choose on (run a larger --limit)"
        )
    # eligible already excludes agreement is None; `or 0.0` only satisfies the
    # type-checker (and is a no-op for a genuine 0.0 agreement). The third key
    # (judge_provider) makes the order a TOTAL order: two judges tied on BOTH
    # agreement AND n_parsed would otherwise keep `results.values()` dict order,
    # so which one lands at `ranked[0]`/`ranked[1]` — and thus the order the
    # "statistically tied — X vs Y" abstention reason names them — flipped on
    # input order. `-` on the first two keys (newest-best-first) but ASCENDING
    # slug (so `reverse=True` doesn't reverse the slug); express via negation.
    ranked = sorted(
        eligible,
        key=lambda r: (-(r.agreement or 0.0), -r.n_parsed, r.judge_provider),
    )
    top = ranked[0]
    top_agr = top.agreement or 0.0
    if top_agr <= 0.5:
        return None, "no judge beat random (50%) agreement with your corrections"
    runner = ranked[1] if len(ranked) > 1 else None
    if runner is not None and (top_agr - (runner.agreement or 0.0)) < MIN_ALIGNMENT_MARGIN:
        return None, (
            f"judges are statistically tied — {top.judge_provider} "
            f"{top_agr*100:.0f}% vs {runner.judge_provider} {(runner.agreement or 0.0)*100:.0f}% "
            f"(lead < {MIN_ALIGNMENT_MARGIN*100:.0f} pts); run a larger --limit or pass --judge"
        )
    return top.judge_provider, f"{top.judge_provider} leads at {top_agr*100:.0f}% (n={top.n_parsed})"


def pick_most_aligned_judge(
    candidates: list[str],
    pairs: list[PreferencePair],
    provider_configs: dict,
    lens_text: str = "",
    *,
    cwd: Path | None = None,
) -> tuple[str | None, dict[str, JudgeAlignmentResult]]:
    """Validate each candidate judge against the user's own corrections and pick
    the best-aligned one IF the evidence clears the noise floor (select_aligned_judge).
    Returns (chosen_judge | None, {judge: result})."""
    results: dict[str, JudgeAlignmentResult] = {}
    for judge in candidates:
        if judge not in provider_configs:
            continue
        results[judge] = validate_judge(
            judge, pairs, provider_configs, lens_text, cwd=cwd
        )
    chosen, _reason = select_aligned_judge(results)
    return chosen, results


def save_alignment_report(
    chosen: str | None,
    results: dict[str, JudgeAlignmentResult],
    out_path: Path,
) -> Path:
    """Persist the judge-alignment report — the public trust artifact."""
    payload = {
        "chosen_judge": chosen,
        "judges": {name: r.to_dict() for name, r in results.items()},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
