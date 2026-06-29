"""Self-preference (self-enhancement bias) validation for the eval judges.

The existential question for a cross-provider eval: does an LLM judge INFLATE
scores for responses from its OWN provider family? If it does, a same-family
judge is biased and the whole cross-provider comparison is compromised (each lab's
judge would crown its own model). If it doesn't, self-judging is fine.

Method — PAIRED WITHIN-RESPONSE: every judge scores the IDENTICAL response text,
so item difficulty cancels (each response is its own control). The statistic, per
response authored by family F:

    delta = score(own-family judge of F) − mean(scores of the cross-family judges)

Self-preference ⇒ mean(delta) significantly > 0. Validated 2026-06-09 on the
founder's saved responses (n=30 paired deltas): judges are NOT self-preferential —
claude family −0.19 (self-CRITICAL), antigravity +0.02, overall z=−2.62. A judge's
PERSONALITY (harsh vs lenient) dominates the score; bias-toward-kin does not exist.

This is an EMPIRICAL property of live models — it can change when a lab ships a new
model — so it must be re-validated per model change (the persisted record + the
``unvalidated_models`` staleness helper drive that nudge).

`analyze_self_preference` is PURE (unit-tested with synthetic scores).
`collect_scores` does the judge dispatch via `score_run`. `run_self_preference`
orchestrates collect → analyze. The own-family judge of family F is the judge whose
slug == F (judges and families share the canonical slug space claude/codex/antigravity).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path

from ..utils import now_iso
from .runner import EvalRunResult, load_run_result, results_dir

# A judge column with more than this fraction of dispatch failures (rate-limit /
# credit-out / empty output) is DROPPED — its scores are noise. Mirrors the
# experiment's guard: never report a verdict over a degraded judge column.
JUDGE_FAIL_THRESHOLD = 0.20
# Two-sided 95% critical value for the crude z on the pooled delta.
Z_CRIT = 1.96


@dataclass
class JudgeHealth:
    judge: str
    n_failed: int
    n_total: int

    @property
    def fail_rate(self) -> float:
        return (self.n_failed / self.n_total) if self.n_total else 1.0

    @property
    def healthy(self) -> bool:
        return self.fail_rate <= JUDGE_FAIL_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "judge": self.judge,
            "n_failed": self.n_failed,
            "n_total": self.n_total,
            "fail_rate": round(self.fail_rate, 4),
            "healthy": self.healthy,
        }


@dataclass
class FamilyDelta:
    """The own-minus-cross statistic for one target family. `computable` is False
    when the family's own-family judge was dropped (degraded) — its self-cell can't
    be measured this run."""
    family: str
    computable: bool
    delta: float | None = None      # own − cross-mean, averaged over the family's responses
    n: int = 0
    n_positive: int = 0
    se: float = 0.0
    reason: str = ""                # why not computable

    def to_dict(self) -> dict:
        d = {"family": self.family, "computable": self.computable, "n": self.n}
        if self.computable:
            d.update({
                "delta": round(self.delta, 4) if self.delta is not None else None,
                "n_positive": self.n_positive,
                "se": round(self.se, 4),
            })
        elif self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class SelfPreferenceResult:
    judges: list[str]
    healthy_judges: list[str]
    dropped_judges: list[str]
    judge_health: list[JudgeHealth]
    matrix: dict[str, dict[str, float]]          # family -> judge -> mean score
    judge_leniency: dict[str, float]             # judge -> mean over all responses
    family_deltas: list[FamilyDelta]
    overall_delta: float | None
    overall_se: float
    overall_z: float | None
    overall_n: int
    verdict: str                                  # see VERDICTS below
    self_critical: bool                           # delta significantly NEGATIVE
    partial: bool                                 # a judge column was dropped

    def to_dict(self) -> dict:
        return {
            "judges": self.judges,
            "healthy_judges": self.healthy_judges,
            "dropped_judges": self.dropped_judges,
            "judge_health": [h.to_dict() for h in self.judge_health],
            "matrix": {f: {j: round(v, 4) for j, v in cols.items()} for f, cols in self.matrix.items()},
            "judge_leniency": {j: round(v, 4) for j, v in self.judge_leniency.items()},
            "family_deltas": [d.to_dict() for d in self.family_deltas],
            "overall_delta": round(self.overall_delta, 4) if self.overall_delta is not None else None,
            "overall_se": round(self.overall_se, 4),
            "overall_z": round(self.overall_z, 4) if self.overall_z is not None else None,
            "overall_n": self.overall_n,
            "verdict": self.verdict,
            "self_critical": self.self_critical,
            "partial": self.partial,
        }


# Verdict vocabulary.
VERDICT_NO_PREFERENCE = "no_self_preference"      # judges do NOT inflate their own family
VERDICT_SELF_PREFERENCE = "self_preference"        # judges DO inflate their own family (the bad case)
VERDICT_INCONCLUSIVE = "inconclusive"              # <2 healthy judges or no paired deltas


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _pstdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def analyze_self_preference(
    rows: list[dict],
    judges: list[str],
    *,
    z_crit: float = Z_CRIT,
) -> SelfPreferenceResult:
    """PURE analysis. `rows` is a list of per-response records:
        {"family": "<slug>", "scores": {"<judge>": float | None, ...}}
    where a None score is a judge-failure (dispatch/credit) excluded from the math.

    The own-family judge of family F is the judge whose slug == F. A family's
    self-cell is computable only when that judge is healthy. Returns a structured
    verdict; never raises on degraded data (it drops the bad column and reports
    `partial`)."""
    # 1. Judge health → which columns survive.
    health: list[JudgeHealth] = []
    healthy: list[str] = []
    for j in judges:
        n_total = len(rows)
        n_failed = sum(1 for r in rows if r["scores"].get(j) is None)
        h = JudgeHealth(judge=j, n_failed=n_failed, n_total=n_total)
        health.append(h)
        if h.healthy:
            healthy.append(j)
    dropped = [j for j in judges if j not in healthy]

    # 2. Matrix (family × healthy judge mean) + judge leniency.
    families = sorted({r["family"] for r in rows})
    matrix: dict[str, dict[str, float]] = {}
    for fam in families:
        fam_rows = [r for r in rows if r["family"] == fam]
        cols: dict[str, float] = {}
        for j in healthy:
            vals = [r["scores"][j] for r in fam_rows if r["scores"].get(j) is not None]
            if vals:
                cols[j] = _mean(vals)
        matrix[fam] = cols
    leniency: dict[str, float] = {}
    for j in healthy:
        vals = [r["scores"][j] for r in rows if r["scores"].get(j) is not None]
        if vals:
            leniency[j] = _mean(vals)

    # 3. Per-family own-minus-cross deltas (paired, within-response).
    family_deltas: list[FamilyDelta] = []
    pooled: list[float] = []
    for fam in families:
        if fam not in healthy:
            family_deltas.append(FamilyDelta(
                family=fam, computable=False,
                reason=f"own-family judge ({fam}) degraded — self-cell not measurable this run",
            ))
            continue
        deltas: list[float] = []
        for r in [r for r in rows if r["family"] == fam]:
            own = r["scores"].get(fam)
            cross = [r["scores"][j] for j in healthy if j != fam and r["scores"].get(j) is not None]
            if own is None or not cross:
                continue
            deltas.append(own - _mean(cross))
        if deltas:
            m = _mean(deltas)
            sd = _pstdev(deltas)
            se = (sd / math.sqrt(len(deltas))) if len(deltas) > 1 else 0.0
            family_deltas.append(FamilyDelta(
                family=fam, computable=True, delta=m, n=len(deltas),
                n_positive=sum(1 for x in deltas if x > 0), se=se,
            ))
            pooled.extend(deltas)
        else:
            family_deltas.append(FamilyDelta(
                family=fam, computable=False, reason="no paired cross-family scores",
            ))

    # 4. Pooled verdict.
    overall_delta: float | None = None
    overall_se = 0.0
    overall_z: float | None = None
    self_critical = False
    if len(healthy) < 2 or not pooled:
        verdict = VERDICT_INCONCLUSIVE
    else:
        overall_delta = _mean(pooled)
        sd = _pstdev(pooled)
        overall_se = (sd / math.sqrt(len(pooled))) if len(pooled) > 1 else 0.0
        overall_z = (overall_delta / overall_se) if overall_se > 0 else None
        if overall_z is not None and overall_delta > 0 and overall_z > z_crit:
            verdict = VERDICT_SELF_PREFERENCE
        else:
            verdict = VERDICT_NO_PREFERENCE
        self_critical = bool(overall_z is not None and overall_delta < 0 and overall_z < -z_crit)

    return SelfPreferenceResult(
        judges=list(judges),
        healthy_judges=healthy,
        dropped_judges=dropped,
        judge_health=health,
        matrix=matrix,
        judge_leniency=leniency,
        family_deltas=family_deltas,
        overall_delta=overall_delta,
        overall_se=overall_se,
        overall_z=overall_z,
        overall_n=len(pooled),
        verdict=verdict,
        self_critical=self_critical,
        partial=bool(dropped),
    )


def collect_scores(
    provider_configs: dict,
    lens_text: str,
    *,
    judges: list[str],
    n_per_family: int = 10,
    results_path: Path | None = None,
    cwd: Path | None = None,
    progress=None,
) -> list[dict]:
    """Score saved eval responses with EACH judge (the dispatch-heavy half).

    Loads every eval result file, groups by target family, samples up to
    `n_per_family` scoreable responses per family, and re-scores each with every
    judge via `score_run` (judge dispatch only — the target responses are already
    on disk, so no target re-dispatch). A judge-failure 0.5 is recorded as None so
    `analyze_self_preference` excludes it. Returns the `rows` analyze() consumes."""
    from ..council_schema import normalize_provider_slug
    from .scorer import _DEGENERATE_REASONS, score_run

    rd = results_path or results_dir()
    by_family: dict[str, list] = {}
    for path in sorted(rd.glob("eval_*__model_*.json")):
        res = load_run_result(path)
        if res is None:
            continue
        fam = normalize_provider_slug(res.target_provider)
        items = [it for it in res.items if it.target_response and not it.target_error]
        if items:
            by_family.setdefault(fam, []).append((res, items))

    rows: list[dict] = []
    for fam, runs in by_family.items():
        # Sample across this family's runs up to n_per_family.
        sampled: list = []
        for res, items in runs:
            for it in items:
                if len(sampled) >= n_per_family:
                    break
                sampled.append((res, it))
            if len(sampled) >= n_per_family:
                break
        # One row per sampled response; each judge fills its column.
        row_for: dict[str, dict] = {}
        for res, it in sampled:
            key = it.eval_item_id
            row_for[key] = {"family": fam, "scores": {}}
        for j in judges:
            for res, it in sampled:
                sub = EvalRunResult(
                    eval_id=res.eval_id, target_provider=res.target_provider,
                    target_model=res.target_model, started_at=res.started_at,
                    completed_at=res.completed_at, items_total=1,
                    items_completed=1, items_failed=0, items=[copy.deepcopy(it)],
                )
                try:
                    score_run(sub, lens_text, j, provider_configs, cwd=cwd)
                    scored = sub.items[0]
                    failed = (scored.score == 0.5
                              and (scored.score_reason or "").startswith(_DEGENERATE_REASONS))
                    row_for[it.eval_item_id]["scores"][j] = None if failed else scored.score
                except Exception:
                    row_for[it.eval_item_id]["scores"][j] = None
            if progress is not None:
                try:
                    progress(fam, j, len(sampled))
                except Exception:
                    pass
        rows.extend(row_for.values())
    return rows


def run_self_preference(
    provider_configs: dict,
    lens_text: str,
    *,
    judges: list[str],
    n_per_family: int = 10,
    results_path: Path | None = None,
    cwd: Path | None = None,
    progress=None,
) -> SelfPreferenceResult:
    """collect → analyze. The single entrypoint the CLI verb calls."""
    rows = collect_scores(
        provider_configs, lens_text, judges=judges, n_per_family=n_per_family,
        results_path=results_path, cwd=cwd, progress=progress,
    )
    return analyze_self_preference(rows, judges)


# ── persistence + per-model-change staleness ─────────────────────────────────

def self_preference_record_path() -> Path:
    """`~/.trinity/evals/self_preference.json` — the scores-only validation record
    (no prompt text/PII; just model slugs + deltas + verdict)."""
    from .builder import evals_dir
    return evals_dir() / "self_preference.json"


def save_self_preference_record(result: SelfPreferenceResult, models_validated: list[str]) -> Path:
    """Persist a compact, scores-only record so surfaces can show 'judges validated
    non-self-preferential as of <date>' and so `unvalidated_models` can detect when
    a newly-shipped model hasn't been re-checked."""
    import json
    p = self_preference_record_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "validated_at": now_iso(),
        "verdict": result.verdict,
        "self_critical": result.self_critical,
        "overall_delta": result.overall_delta,
        "overall_z": result.overall_z,
        "overall_n": result.overall_n,
        "partial": result.partial,
        "healthy_judges": result.healthy_judges,
        "dropped_judges": result.dropped_judges,
        "models_validated": sorted(set(models_validated)),
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def load_self_preference_record() -> dict | None:
    """The saved record, or None. Never raises (a malformed/absent record just means
    'not yet validated')."""
    import json
    try:
        p = self_preference_record_path()
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def unvalidated_models(current_models: list[str], record: dict | None = None) -> list[str]:
    """Models present now that the self-preference record has NOT validated yet.

    This is the per-model-change staleness signal (the #218 detect-new-models hook):
    when a lab ships a new model, it lands here as 'unvalidated' until the
    self-preference check re-runs and re-stamps the record. A `partial` record (a
    judge column was dropped, e.g. credit outage) does NOT count its dropped-judge
    family as validated, so the GPT-family cell stays flagged until codex is back."""
    if record is None:
        record = load_self_preference_record()
    if not record:
        return sorted(set(m for m in current_models if m))
    validated = set(record.get("models_validated") or [])
    return sorted(set(m for m in current_models if m and m not in validated))
