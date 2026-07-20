"""Disagreement ledger — the shippable core of the `trust` surface.

`trust` answers two questions off ONE object, the cross-provider council
disagreement:
  RETRIEVAL (live, LLM-free, census-validated): "this split keeps recurring" —
    given a query, the recurring cross-provider disagreements it maps to, with
    the raw council record (who argued which side, the chairman's pick) and a
    councils-on-split count. Validated in `internal/experiments/meta_pattern_census.py`
    (incremental hit-rate 0.240 vs shuffle-null 0.038; 90% cross-lab).
  TALLY (which model you side with): per-model win/loss over RESOLVED
    disagreements. Resolution is an LLM judgment of which branch your later work
    took — it lives in `resolve.py` (rides session sampling, #263) because cosine
    reads topic, not stance (measured: an LLM-free resolver agrees with the
    validated reference at chance — see behavioral_resolver_backtest.py).

This module owns the LLM-FREE halves: loading disagreements, assembling behavioral
evidence, the tally/Wilson-CI aggregation, and the retrieval. No LLM calls here.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .model_identity import parse_identity
from .state_paths import trinity_home
from .utils import now_iso

# Evidence-assembly knobs (match the retro instrument so evidence is identical in
# shape; validated wholesale, not tuned post hoc).
WINDOW_DAYS = 14
TOP_K = 8
ADJ_FLOOR = 0.35
MIN_EVIDENCE = 2
# Retrieval knobs (from the census).
PLACE_FLOOR = 0.45
# Kill/validity floors for the tally.
K3_LOW, K3_HIGH = 0.55, 0.90
K4_MIN_RESOLVED = 60
# A per-model cell needs this many resolved decisions before its win-rate/CI is
# trustworthy enough to show (the same floor K4 uses for a lab's CI check).
MIN_TALLY_N = 10

# Dispatch/web-capture slugs fold to the lab (routing + trust live at lab
# granularity — "which lab's model to trust").
_LAB = {
    "claude": "anthropic", "claude_ai": "anthropic", "anthropic": "anthropic",
    "cowork": "anthropic", "chatgpt": "openai", "codex": "openai",
    "openai": "openai", "gpt": "openai", "gemini": "google",
    "antigravity": "google", "google": "google",
}


def lab_of(slug: str) -> str:
    return _LAB.get((slug or "").lower(), (slug or "").lower())


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class DisagreementPattern:
    """One cross-provider council disagreement — the retrievable unit."""
    claim_id: str
    council_id: str
    at: str
    claim: str
    why_matters: str
    providers_for: list[str]       # labs that argued the claim
    providers_against: list[str]   # labs that argued against
    chairman_winner: str           # lab the chairman picked
    task_excerpt: str = ""
    member_models: dict[str, str] = field(default_factory=dict)
    # model×version identities (family·tier·version) that argued each side — the
    # finer granularity the tally keys on so Opus 4.8 doesn't hide inside "anthropic".
    models_for: list[str] = field(default_factory=list)
    models_against: list[str] = field(default_factory=list)

    @property
    def is_cross_provider(self) -> bool:
        return len({*self.providers_for, *self.providers_against}) >= 2

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v not in (None, "", [], {})}
        return d


def load_disagreements(home: str | None = None) -> list[DisagreementPattern]:
    """Attributed disagreed_claims from REAL councils (mode != synthesis_only).

    Virtual/dream-mined synthesis is excluded: it was authored with hindsight over
    the same transcripts, so crediting it would be circular.
    """
    base = home or str(trinity_home())
    out: list[DisagreementPattern] = []
    d = os.path.join(base, "council_outcomes")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not (fn.startswith("council_") and fn.endswith(".json")):
            continue
        try:
            rec = json.load(open(os.path.join(d, fn)))
        except (ValueError, OSError):
            continue
        if not isinstance(rec, dict):  # valid-JSON-wrong-type → skip, don't crash on .get
            continue
        if (rec.get("metadata") or {}).get("mode") == "synthesis_only":
            continue
        at = _parse_ts(rec.get("created_at") or "")
        if at is None:
            continue
        rl = rec.get("routing_label") or {}
        member_models = {}
        for m in rec.get("member_results") or []:
            if isinstance(m, dict) and m.get("provider"):
                eff = (m.get("metadata") or {}).get("effort")
                ident = str(m.get("model") or "")
                member_models[m["provider"]] = f"{ident} ({eff})" if eff and eff not in ident else ident
        # lab -> model identity for this council, so a side's lab can be resolved
        # to the actual model that argued it. Include the EFFORT leg when it was
        # captured (model×size×effort — the atomic unit); drop it to model×size when
        # the council didn't stamp effort, rather than littering the tally with "?".
        def _ident_label(mstr):
            mi = parse_identity(mstr, None)
            dims = (("family", "tier", "version", "effort")
                    if mi.effort not in ("?", "", None)
                    else ("family", "tier", "version"))
            return mi.label(*dims)

        lab_model = {lab_of(prov): _ident_label(m) for prov, m in member_models.items() if m}

        def _models(slugs):
            return [lab_model.get(lab_of(p), lab_of(p)) for p in (slugs or [])]

        for idx, c in enumerate(rl.get("disagreed_claims") or []):
            if not (isinstance(c, dict) and c.get("claim") and c.get("providers_for")):
                continue
            out.append(DisagreementPattern(
                claim_id=f'{rec.get("council_run_id")}#{idx}',
                council_id=str(rec.get("council_run_id") or ""),
                at=at.isoformat(),
                claim=str(c.get("claim"))[:500],
                why_matters=str(c.get("why_matters") or "")[:300],
                providers_for=[lab_of(p) for p in (c.get("providers_for") or [])],
                providers_against=[lab_of(p) for p in (c.get("providers_against") or [])],
                chairman_winner=lab_of(rl.get("winner") or ""),
                task_excerpt=str((rec.get("metadata") or {}).get("task_text") or "")[:200],
                member_models=member_models,
                models_for=_models(c.get("providers_for")),
                models_against=_models(c.get("providers_against")),
            ))
    return out


def assemble_evidence(
    patterns: list[DisagreementPattern],
    nodes: list[tuple[datetime, str, Any]],
    embed_batch_fn: Callable[[list[str]], list[list[float]]],
) -> dict[str, list[dict]]:
    """For each pattern, the user's subsequent topically-adjacent prompts (the
    behavioral evidence the resolver reads). LLM-FREE: stored node embeddings +
    one embed pass over claim texts. `nodes` are (ts, text, unit_vec) sorted by ts.

    Returns {claim_id: [{"sim", "at", "text"}, ...]}.
    """
    import numpy as np

    if not patterns:
        return {}
    cvecs = np.asarray(embed_batch_fn(
        [f"{p.claim} {p.why_matters}"[:2000] for p in patterns]), dtype=np.float32)
    cvecs /= (np.linalg.norm(cvecs, axis=1, keepdims=True) + 1e-9)
    out: dict[str, list[dict]] = {}
    for p, cv in zip(patterns, cvecs):
        t0 = _parse_ts(p.at)
        if t0 is None:
            out[p.claim_id] = []
            continue
        t1 = t0 + timedelta(days=WINDOW_DAYS)
        cands = []
        for ts, text, v in nodes:
            if ts <= t0 or ts > t1:
                continue
            s = float(np.dot(cv, v))
            if s >= ADJ_FLOOR:
                cands.append((s, ts, text))
        cands.sort(reverse=True)
        out[p.claim_id] = [{"sim": round(s, 3), "at": ts.isoformat(), "text": text[:800]}
                           for s, ts, text in cands[:TOP_K]]
    return out


def wilson_ci(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — the honest small-n CI for a per-model win-rate."""
    if n <= 0:
        return (0.0, 1.0)
    p = min(1.0, max(0.0, wins / n))
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / den
    half = z * math.sqrt(max(0.0, p * (1 - p) / n + z2 / (4 * n * n))) / den
    return (max(0.0, center - half), min(1.0, center + half))


def _model_version_and_effort(label: str) -> tuple[str, str | None]:
    """Split a 'family · tier · version [· effort]' identity into its model×version
    PRIMARY and its optional effort leg. The primary is what the tally keys on so a
    single model never fragments into per-effort rows in the headline; effort is a
    secondary breakdown, surfaced only when a sub-cell independently clears the floor.
    Lab-fallback labels (no ' · ') pass through as the primary with no effort."""
    parts = label.split(" · ")
    if len(parts) >= 4:
        return " · ".join(parts[:3]), parts[3]
    return label, None


def aggregate_tally(
    patterns: list[DisagreementPattern],
    resolutions: dict[str, str],
) -> dict[str, Any]:
    """Per-lab win/loss over resolved disagreements + Wilson CI + the K3/K4 gates.

    followed -> credit providers_for as W (against as L); contradicted -> reverse.
    K3 = agreement with the chairman's winner (must land in [0.55, 0.90] — not
    noise, not parroting). K4 = after >=60 resolved, >=1 lab's CI excludes 0.5.
    """
    by_id = {p.claim_id: p for p in patterns}
    resolved = {cid: r for cid, r in resolutions.items()
                if r in ("followed", "contradicted") and cid in by_id}
    tally: dict[str, dict[str, int]] = {}
    eff_tally: dict[str, dict[str, dict[str, int]]] = {}  # model×version -> effort -> {w,l}
    ch_hit = ch_tot = 0
    for cid, r in resolved.items():
        p = by_id[cid]
        # PRIMARY row per model×version (a clean per-model read that never fragments);
        # effort is a SECONDARY breakdown surfaced only when a sub-cell independently
        # clears the floor. K3 stays at LAB granularity (chairman winner is a lab).
        # Falls back to lab when the member model wasn't captured — no row is lost.
        win = (p.models_for or p.providers_for) if r == "followed" else (p.models_against or p.providers_against)
        los = (p.models_against or p.providers_against) if r == "followed" else (p.models_for or p.providers_for)
        for m in win:
            mv, eff = _model_version_and_effort(m)
            tally.setdefault(mv, {"w": 0, "l": 0})["w"] += 1
            if eff:
                eff_tally.setdefault(mv, {}).setdefault(eff, {"w": 0, "l": 0})["w"] += 1
        for m in los:
            mv, eff = _model_version_and_effort(m)
            tally.setdefault(mv, {"w": 0, "l": 0})["l"] += 1
            if eff:
                eff_tally.setdefault(mv, {}).setdefault(eff, {"w": 0, "l": 0})["l"] += 1
        win_labs = p.providers_for if r == "followed" else p.providers_against
        los_labs = p.providers_against if r == "followed" else p.providers_for
        cw = p.chairman_winner
        if cw and (cw in win_labs or cw in los_labs):
            ch_tot += 1
            ch_hit += (cw in win_labs)
    k3 = (ch_hit / ch_tot) if ch_tot else None
    records = {}
    k4_pass = False
    for m, t in tally.items():
        n = t["w"] + t["l"]
        lo, hi = wilson_ci(t["w"], n)
        excl = n >= MIN_TALLY_N and (lo > 0.5 or hi < 0.5)
        k4_pass = k4_pass or excl
        records[m] = {"w": t["w"], "l": t["l"], "win_rate": round(t["w"] / n, 3) if n else 0.0,
                      "ci": [round(lo, 3), round(hi, 3)], "ci_excludes_half": excl}
    # Secondary effort breakdown: per model×version, only the effort sub-cells that
    # independently clear the floor — so a non-significant effort split (e.g. an n=13
    # cell whose CI includes chance) never surfaces as a headline, the exact overclaim
    # the trustworthiness gate exists to refuse.
    effort_breakdown: dict[str, dict[str, Any]] = {}
    for mv, effs in eff_tally.items():
        rows = {}
        for eff, t in effs.items():
            n = t["w"] + t["l"]
            if n < MIN_TALLY_N:
                continue
            lo, hi = wilson_ci(t["w"], n)
            rows[eff] = {"w": t["w"], "l": t["l"],
                         "win_rate": round(t["w"] / n, 3) if n else 0.0,
                         "ci": [round(lo, 3), round(hi, 3)],
                         "ci_excludes_half": bool(lo > 0.5 or hi < 0.5)}
        if rows:
            effort_breakdown[mv] = rows
    in_band = k3 is not None and K3_LOW <= k3 <= K3_HIGH
    return {
        "resolved": len(resolved),
        "records": records,
        "effort_breakdown": effort_breakdown,
        "k3_chairman_agreement": None if k3 is None else round(k3, 3),
        "k3_in_band": in_band,
        "k4_discriminates": k4_pass and len(resolved) >= K4_MIN_RESOLVED,
        # The tally is TRUSTWORTHY only when the resolver clears both label-free
        # gates. Otherwise the retrieval + raw record still ship; the per-model
        # verdict is withheld (green-gate: the disqualifier is IN the gate).
        "tally_trustworthy": bool(in_band and k4_pass and len(resolved) >= K4_MIN_RESOLVED),
    }


def retrieve_recurring(
    query: str,
    *,
    top_k: int = 5,
    home: str | None = None,
    embed_fn: Callable[[str], list[float]] | None = None,
    patterns: list[DisagreementPattern] | None = None,
) -> list[dict]:
    """The census-validated retrieval: the recurring CROSS-PROVIDER disagreements
    a query maps into, most-relevant first, each with its raw council record and a
    councils-on-split count (how many distinct councils split on this same question).

    LLM-free (embeddings + counts). Requires a REAL embedder — refuses loudly
    rather than retrieving on the TF-IDF stub (the 'remove tf-idf' gate).
    """
    import numpy as np

    from .embeddings import embed as _embed, embed_batch, require_real_embedder
    require_real_embedder()
    embed_fn = embed_fn or _embed

    pats = [p for p in (patterns if patterns is not None else load_disagreements(home))
            if p.is_cross_provider]
    if not pats:
        return []
    qv = np.asarray(embed_fn(query), dtype=np.float32)
    qn = np.linalg.norm(qv)
    if qn == 0:
        return []
    qv = qv / qn
    pv = np.asarray(embed_batch([f"{p.claim} {p.why_matters}"[:2000] for p in pats]), dtype=np.float32)
    pv /= (np.linalg.norm(pv, axis=1, keepdims=True) + 1e-9)
    sims = pv @ qv
    # Recurrence: how many distinct councils split on ~the same question. Cheap
    # transitive count via pattern-pattern cosine (>=0.85 == same question).
    pp = pv @ pv.T
    order = np.argsort(-sims)
    out = []
    for i in order:
        if sims[i] < PLACE_FLOOR:
            break
        p = pats[i]
        councils_on_split = int(np.sum(pp[i] >= 0.85))
        out.append({
            "claim": p.claim,
            "why_matters": p.why_matters,
            "providers_for": p.providers_for,
            "providers_against": p.providers_against,
            "chairman_winner": p.chairman_winner,
            "council_id": p.council_id,
            # how many distinct councils split on ~this same question (>=1, includes
            # itself). Named for what it counts — NOT "recurrence", which would imply
            # the user returned to it; a value of 1 means this split is a one-off.
            "councils_on_split": councils_on_split,
            "relevance": round(float(sims[i]), 3),
        })
        if len(out) >= top_k:
            break
    return out


# ── the LLM resolver (rides session sampling, #263) + build orchestration ─────

def _load_nodes(home: str | None = None) -> list[tuple[datetime, str, Any]]:
    """(ts, text, unit_vec) for every embedded user prompt node — the corpus the
    resolver reads subsequent behavior from. LLM-free; shape-guarded per line."""
    import numpy as np

    from .embeddings import is_finite_embedding
    path = Path(home or trinity_home()) / "prompts" / "prompt_nodes.jsonl"
    if not path.exists():
        return []
    nodes: list[tuple[datetime, str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):  # valid-JSON-wrong-type → skip
            continue
        ts = _parse_ts(d.get("timestamp") or d.get("created_at") or "")
        emb = d.get("embedding")
        text = (d.get("text") or "").strip()
        if ts is None or not text or not is_finite_embedding(emb):
            continue
        v = np.asarray(emb, dtype=float)
        n = np.linalg.norm(v)
        if n == 0:
            continue
        nodes.append((ts, text, v / n))
    nodes.sort(key=lambda t: t[0])
    return nodes


# BLIND: no provider names, no chairman synthesis — the model can't read the
# answer back; it judges ONLY the user's subsequent behavior (the K3 leakage gate).
_EXTRACT_PROMPT = """A technical decision was contested. The contested claim:

CLAIM: {claim}
WHY IT MATTERS: {why_matters}
(Decision context: {task_excerpt})

Below are the things this person actually said and asked in the following days, most-relevant first:

{evidence}

Question: judged ONLY by what they subsequently said/asked, did this person's actual work FOLLOW the claim's direction, CONTRADICT it, or is it UNRESOLVED from this evidence? Do not guess from tone; require concrete evidence. Answer with exactly one JSON object:
{{"resolution": "followed"|"contradicted"|"unresolved", "evidence_quote": "<the single most decisive phrase, verbatim>"}}"""


def _parse_resolution(raw: str) -> tuple[str, str]:
    """Pull the resolution JSON out of a chairman response. Shape-guarded — a
    valid-JSON-wrong-type never crashes the ledger build."""
    try:
        start = raw.rfind("{")
        obj = json.loads(raw[start:raw.rfind("}") + 1]) if start != -1 else {}
    except (ValueError, TypeError):
        return "unresolved", ""
    if not isinstance(obj, dict):
        return "unresolved", ""
    res = obj.get("resolution")
    if res not in ("followed", "contradicted", "unresolved"):
        return "unresolved", ""
    return res, str(obj.get("evidence_quote") or "")[:200]


def resolve_claim(pattern: DisagreementPattern, evidence: list[dict],
                  config: Any = None, chairman: str = "claude") -> tuple[str, str]:
    """LLM judgment of which branch the user's later work took. Rides SESSION
    SAMPLING (#263): the claude seat routes through sampling/createMessage when a
    session is active (providers.py), else falls back to claude -p — the sanctioned
    lens-build path, not a per-token API bill. Founder-approved 2026-07-18 (the
    LLM-free resolver measured DORMANT — cosine reads topic, not stance)."""
    if len(evidence) < MIN_EVIDENCE:
        return "unresolved", ""
    from .me_builder import _stage_run_with_fallback
    ev = "\n".join(f'- [{str(e.get("at", ""))[:10]}] {e.get("text", "")}' for e in evidence)
    prompt = _EXTRACT_PROMPT.format(claim=pattern.claim, why_matters=pattern.why_matters,
                                    task_excerpt=pattern.task_excerpt, evidence=ev)
    with tempfile.TemporaryDirectory() as cwd:
        res = _stage_run_with_fallback(prompt, config, chairman, cwd, low_effort=True)
    return _parse_resolution(getattr(res, "stdout", "") or "")


def _ledger_dir(home: str | None = None) -> Path:
    return Path(home or trinity_home()) / "disagreement_ledger"


def build_ledger(*, home: str | None = None, config: Any = None, limit: int | None = None,
                 resolver: Callable[..., tuple[str, str]] | None = None,
                 embed_batch_fn: Callable[[list[str]], list[list[float]]] | None = None) -> dict:
    """Assemble evidence, resolve each cross-provider disagreement via the LLM
    (session sampling), aggregate, and persist to ~/.trinity/disagreement_ledger/.
    `resolver` is injectable for tests (default: resolve_claim). Requires a real
    embedder for the evidence pass. Returns the aggregate (with the
    tally_trustworthy gate — the per-model verdict is withheld unless it clears)."""
    from .embeddings import embed_batch, require_real_embedder
    require_real_embedder()
    embed_batch_fn = embed_batch_fn or embed_batch
    resolver = resolver or resolve_claim
    if config is None:
        try:
            from .config import load_config
            config = load_config(None)
        except Exception:  # noqa: BLE001 — no config → resolver falls back / no-ops
            config = None
    patterns = [p for p in load_disagreements(home) if p.is_cross_provider]
    if limit:
        patterns = patterns[:limit]
    evidence = assemble_evidence(patterns, _load_nodes(home), embed_batch_fn)
    resolutions: dict[str, str] = {}
    quotes: dict[str, str] = {}
    for p in patterns:
        res, quote = resolver(p, evidence.get(p.claim_id, []), config)
        resolutions[p.claim_id] = res
        if quote:
            quotes[p.claim_id] = quote
    agg = aggregate_tally(patterns, resolutions)
    d = _ledger_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "resolutions.jsonl").open("w", encoding="utf-8") as f:
        for cid, r in resolutions.items():
            f.write(json.dumps({"claim_id": cid, "resolution": r, "quote": quotes.get(cid, "")}) + "\n")
    (d / "summary.json").write_text(json.dumps({**agg, "built_at": now_iso()}, indent=2))
    return agg


def reaggregate_ledger(home: str | None = None) -> dict:
    """Re-derive summary.json from the EXISTING resolutions.jsonl — no re-resolve,
    no LLM, no embedder. Use to regenerate the tally after an aggregation change
    (e.g. the model×version granularity) without paying the resolver again. Returns
    {} when no resolutions have been built yet."""
    d = _ledger_dir(home)
    rpath = d / "resolutions.jsonl"
    if not rpath.exists():
        return {}
    resolutions: dict[str, str] = {}
    for line in rpath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("claim_id"):
            resolutions[row["claim_id"]] = row.get("resolution") or "unresolved"
    patterns = [p for p in load_disagreements(home) if p.is_cross_provider]
    agg = aggregate_tally(patterns, resolutions)
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps({**agg, "built_at": now_iso()}, indent=2))
    return agg
