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

    THIS TIER IS A PROXY, NOT GROUND TRUTH (relabelled 2026-08-06, unanimous
    council claim in council_a3196cfdb40680a5). The resolver reproduces its own
    decided verdicts 45% of the time; the label is COMPOSED by a model rather
    than extracted from an observed act; and what it measures is taste-
    consistency inferred at claim elevation. See BEHAVIOURAL_TIER_CAVEAT below
    for the full statement and the (conservative) direction of the bias. The
    numbers did not change when the name did — only the claim over them.

This module owns the LLM-FREE halves: loading disagreements, assembling behavioral
evidence, the tally/Wilson-CI aggregation, and the retrieval. No LLM calls here.
"""
from __future__ import annotations

import json
import re
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
    # HOW THE MODEL STRING WAS OBTAINED, per provider: echoed | pinned | assumed.
    # The tally keys on model×version, so a row whose model was never verified
    # is not worth the same as one the CLI stated — and until 2026-08-17 every
    # row looked identical. Empty for councils recorded before capture existed:
    # that is UNKNOWN provenance, deliberately not back-filled as "assumed",
    # because inventing provenance for rows nobody observed is the same defect
    # in a new costume (res_045).
    model_sources: dict[str, str] = field(default_factory=dict)

    @property
    def is_cross_provider(self) -> bool:
        """Did the labs actually SPLIT — not merely appear on the claim.

        This tested `len(union of both sides) >= 2`, a PROXY, and so admitted
        claims that two labs jointly asserted with nobody arguing the other side:
        agreement counted as disagreement. Measured 2026-08-07 on the live
        corpus: 6 of 357 admitted patterns (1.7%) had an EMPTY `against` side.
        Every one of them credited a win to the for-side with no opposing side to
        lose, which nudges win rates upward — on the ledger that is the product's
        one defensible claim.

        The invariant is: both sides are occupied, and they are not the same
        single lab arguing with itself.
        """
        f, a = set(self.providers_for), set(self.providers_against)
        if not f or not a:
            return False
        return bool(f - a) or bool(a - f)

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
        model_sources: dict[str, str] = {}
        for m in rec.get("member_results") or []:
            if isinstance(m, dict) and m.get("provider"):
                meta = m.get("metadata") or {}
                eff = meta.get("effort")
                ident = str(m.get("model") or "")
                member_models[m["provider"]] = f"{ident} ({eff})" if eff and eff not in ident else ident
                src = meta.get("model_source")
                if src:
                    model_sources[m["provider"]] = str(src)
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
                model_sources=model_sources,
                models_for=_models(c.get("providers_for")),
                models_against=_models(c.get("providers_against")),
            ))
    return out


def assemble_evidence(
    patterns: list[DisagreementPattern],
    nodes: list[tuple[datetime, str, Any]],
    embed_batch_fn: Callable[[list[str]], list[list[float]]],
    window_days: int = WINDOW_DAYS,
) -> dict[str, list[dict]]:
    """For each pattern, the user's subsequent topically-adjacent prompts (the
    behavioral evidence the resolver reads). LLM-FREE: stored node embeddings +
    one embed pass over claim texts. `nodes` are (ts, text, unit_vec) sorted by ts.

    Returns {claim_id: [{"sim", "at", "text"}, ...]}.

    MACHINE TURNS ARE EXCLUDED (2026-07-26). This is the load-bearing correction:
    the whole claim of this ledger is that a verdict reflects what the PERSON did
    next. `_load_nodes` reads prompt_nodes raw, so harness output captured as
    role=user — hook fires, subagent notifications, /loop drivers, and worst of all
    Trinity's OWN `_EXTRACT_PROMPT` from a previous `trust --build` — was being
    retrieved as evidence of their behaviour. Measured before the fix: 37.1% of all
    evidence rows were machine-generated, 185 of 294 claims carried at least one, and
    37 claims were resolved on machine-only evidence. Those verdicts described text
    the user never wrote, and every build deepened the loop.

    `is_user_facing_text` is the read-time projection of the ingest gate, which is
    exactly the seam the ingest filter was designed for: improvements there become
    retroactively effective here with no re-ingest.
    """
    import numpy as np

    from .ingest import is_user_facing_text

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
        # window_days is a PARAMETER (default = the production WINDOW_DAYS, so
        # this is inert until a caller opts in) because the 14-day window is the
        # ledger's hardest-bound knob: a claim whose evidence never reached
        # MIN_EVIDENCE inside 14 days is filed "unresolved" WITHOUT a model call,
        # and `_needs_resolve` then refuses to retry it once the window closes —
        # so evidence-starved and genuinely-ambiguous claims are indistinguishable
        # on disk. Widening it is a validity question, not a free win (later
        # prompts may be a different context entirely), which is why the knob
        # exists for measurement first and the default stays 14.
        t1 = t0 + timedelta(days=window_days)
        cands = []
        for ts, text, v in nodes:
            if ts <= t0 or ts > t1:
                continue
            if not is_user_facing_text(text):
                continue  # harness output is not this person's behaviour
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


# ─── WHY THE BEHAVIOURAL TIER IS NO LONGER CALLED "GOLD" (2026-08-06) ───
#
# It was renamed to PROXY on a unanimous council claim (council_a3196cfdb40680a5:
# "The ledger must be relabeled from gold truth to proxy labels"), backed by
# measurement rather than taste:
#
#   * Re-running THIS resolver over 20 claims it had already DECIDED — same
#     model, same effort, same evidence — reproduced only 45%, and ~31% of
#     decided-to-decided pairs FLIPPED DIRECTION. A key whose own instrument
#     cannot reproduce it is not ground truth.
#   * The label is COMPOSED by a model reading transcripts, not extracted from
#     an observed act. Per the coupling essay, "ground truth stops being ground
#     truth the moment your system can write to it" — and composing is writing.
#   * What it actually measures is whether the user's later work moved in a
#     claim's direction: taste-CONSISTENCY inferred at claim elevation. That is
#     a fast proxy on its own clock, not the slow truth it was standing in for.
#
# Nothing about the NUMBERS changed when the name did — only what we claim they
# are.
#
# THE ATTENUATION RESCUE WAS RETRACTED 2026-08-07. This comment, and the user-
# facing string below, used to say the bias direction was conservative: random
# label noise attenuates toward 50%, so rates would be understated and the
# significance tests would survive as rejections of chance. That argument holds
# only if the noise is NON-DIFFERENTIAL — equally likely whichever model argued
# the claim — which was asserted and never measured. Measured now on the 27
# claims resolved twice in internal/experiments/resolver_effort_arms.jsonl: the
# flip rate is 8/27 = 29.6% overall but runs 0% (GPT-5.5, n=5) to 27% (Gemini
# 3.1-high, n=11) per model. Two-proportion z=1.30, so differentiality is
# UNTESTED rather than refuted — and it points the wrong way, because the model
# with the LOWEST trust rate carries the HIGHEST flip rate, which is what
# differential noise manufacturing a low score would look like.
#
# So the rescue may not be stated to users as fact. The string below says only
# what is measured. The confidence intervals exclude label noise either way.
BEHAVIOURAL_TIER_CAVEAT = (
    "PROXY (behavioural) — inferred by a model reading your later transcripts, "
    "not extracted from an observed choice. Measured 2026-08-06: this resolver "
    "reproduces its own decided verdicts 45% of the time and flips direction on "
    "~31% of decided pairs, so treat per-model rates as directional. Whether "
    "that noise biases the rates in a particular direction is UNMEASURED — it "
    "would need to be equally likely across models, and the per-model flip rate "
    "ranges 0% to 27% on the claims read twice. The intervals shown do NOT "
    "include label noise."
)


def aggregate_tally(
    patterns: list[DisagreementPattern],
    resolutions: dict[str, str],
) -> dict[str, Any]:
    # NOTE (2026-08-07): the returned dict carries `caveat` =
    # BEHAVIOURAL_TIER_CAVEAT. The gold->proxy relabel added that constant but
    # wired it to NOTHING — CLI, MCP and the launchpad card all still shipped the
    # un-caveated verdict, so the council-ratified line "do not quote these numbers
    # as settled without that caveat" was violated by three live surfaces while the
    # sibling SILVER tier carried its calibration everywhere. Emitting it here is
    # what makes every downstream consumer inherit it: summary.json, `trust --json`,
    # the MCP tool payload and the launchpad card all read this dict.
    """Per-lab win/loss over resolved disagreements + Wilson CI + the K3/K4 gates.

    followed -> credit providers_for as W (against as L); contradicted -> reverse.
    K3 = agreement with the chairman's winner (must land in [0.55, 0.90] — not
    noise, not parroting). K4 = after >=60 resolved, >=1 lab's CI excludes 0.5.
    """
    by_id = {p.claim_id: p for p in patterns}
    resolved = {cid: r for cid, r in resolutions.items()
                if r in ("followed", "contradicted") and cid in by_id}
    # PROVENANCE OF THE THING THE TALLY KEYS ON. Every row used to look equally
    # trustworthy; a model string is worth less when nobody verified it. Counted
    # over the RESOLVED claims because those are the ones the rates rest on.
    prov: dict[str, int] = {}
    for cid in resolved:
        srcs = (by_id[cid].model_sources or {}).values()
        for s_ in (set(srcs) or {"unknown"}):
            prov[s_] = prov.get(s_, 0) + 1
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
        # Rides the aggregate so no consumer can render the tally without it.
        "caveat": BEHAVIOURAL_TIER_CAVEAT,
        # How the model strings the tally KEYS ON were obtained, over the
        # resolved claims. `unknown` means the council predates provenance
        # capture (2026-08-17) — deliberately not relabelled "assumed", because
        # inventing provenance for rows nobody observed is the defect it fixes.
        #
        # SEMANTICS, because the totals do not sum to `resolved` and a consumer
        # would otherwise misread them: a claim is counted ONCE PER DISTINCT
        # SOURCE among its members, so a council mixing an echoed member with an
        # assumed one adds to BOTH. That is the useful reading — it says how many
        # claims have any verified member and how many have any unverified one —
        # but it means sum(model_provenance.values()) >= resolved. Compare each
        # count against `resolved`, never against the other counts.
        "model_provenance": dict(sorted(prov.items())),
        "model_provenance_denominator": len(resolved),
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


def _prior_resolutions(home: str | None = None) -> dict[str, tuple[str, str]]:
    """{claim_id: (resolution, quote)} already on disk. Empty when none built."""
    out: dict[str, tuple[str, str]] = {}
    rpath = _ledger_dir(home) / "resolutions.jsonl"
    if not rpath.exists():
        return out
    for line in rpath.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # guard_shape_not_just_parse: a valid-JSON-but-non-dict line (hand edit,
        # partial write) would crash `.get` and take the whole incremental build
        # down — which would silently fall back to re-resolving everything.
        if not isinstance(row, dict):
            continue
        cid = row.get("claim_id")
        if cid:
            out[str(cid)] = (str(row.get("resolution") or "unresolved"),
                             str(row.get("quote") or ""))
    return out


def _needs_resolve(pattern: "DisagreementPattern", prior: dict[str, tuple[str, str]],
                   now: datetime | None = None) -> bool:
    """Should this claim cost an LLM call on THIS build?

    A rebuild used to re-resolve all 300 cross-provider disagreements from scratch
    — 286 billable calls to refresh 39 genuinely new ones. That cost is why the
    ledger sat six days stale: the only way to add today's councils was to pay for
    every prior day again. Resolutions are deterministic given (claim, evidence),
    so re-paying for a settled one buys nothing.

    Skip a claim whose stored verdict is DECIDED (followed/contradicted) — settled
    is settled. Retry an `unresolved` one ONLY while its evidence window is still
    open: `assemble_evidence` reads the 14 days after the council, so once that
    window has closed no new prompt can enter it and the verdict cannot change.
    An unresolved claim from a council two days ago can still resolve tomorrow; one
    from last month cannot.
    """
    cid = pattern.claim_id
    if cid not in prior:
        return True  # never seen
    res, _ = prior[cid]
    if res in ("followed", "contradicted"):
        return False  # settled
    t0 = _parse_ts(pattern.at)
    if t0 is None:
        return False  # undatable: its window can never re-open
    now = now or datetime.now(t0.tzinfo)
    return now <= t0 + timedelta(days=WINDOW_DAYS)


def build_ledger(*, home: str | None = None, config: Any = None, limit: int | None = None,
                 resolver: Callable[..., tuple[str, str]] | None = None,
                 embed_batch_fn: Callable[[list[str]], list[list[float]]] | None = None,
                 force: bool = False) -> dict:
    """Assemble evidence, resolve each cross-provider disagreement via the LLM
    (session sampling), aggregate, and persist to ~/.trinity/disagreement_ledger/.
    `resolver` is injectable for tests (default: resolve_claim). Requires a real
    embedder for the evidence pass. Returns the aggregate (with the
    tally_trustworthy gate — the per-model verdict is withheld unless it clears).

    INCREMENTAL by default (2026-07-25): claims already settled on disk are carried
    forward instead of re-resolved, so a refresh costs one call per genuinely new or
    still-open claim rather than one per claim in the corpus. `force=True` re-resolves
    everything — use it when the resolver prompt or the evidence knobs change, since
    carried-forward verdicts were produced by the OLD instrument.
    """
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
    prior = {} if force else _prior_resolutions(home)
    todo = [p for p in patterns if _needs_resolve(p, prior)]
    todo_ids = {p.claim_id for p in todo}   # set, not `in todo` — that is an O(n^2)
    carried = len(patterns) - len(todo)     # dataclass-equality scan over 300 rows
    # Evidence only for what we will actually resolve — the embed pass is cheap but
    # not free, and assembling it for carried-forward claims is pure waste.
    evidence = assemble_evidence(todo, _load_nodes(home), embed_batch_fn)
    resolutions: dict[str, str] = {}
    quotes: dict[str, str] = {}
    for p in patterns:
        if p.claim_id not in todo_ids and p.claim_id in prior:
            res, quote = prior[p.claim_id]
        else:
            res, quote = resolver(p, evidence.get(p.claim_id, []), config)
        resolutions[p.claim_id] = res
        if quote:
            quotes[p.claim_id] = quote
    agg = aggregate_tally(patterns, resolutions)
    agg["resolved_this_build"] = len(todo)
    agg["carried_forward"] = carried
    d = _ledger_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "resolutions.jsonl").open("w", encoding="utf-8") as f:
        for cid, r in resolutions.items():
            f.write(json.dumps({"claim_id": cid, "resolution": r, "quote": quotes.get(cid, "")}) + "\n")
    (d / "summary.json").write_text(json.dumps({**agg, "built_at": now_iso()}, indent=2))
    return agg


# ─── SILVER tier: chairman-adjudicated claim-side tally (council_a5ba36c437d492f9) ───
#
# Shipped opt-in only (`trust --silver` / MCP `silver:true`), launchpad stays
# behavioural-only, and silver is NEVER merged into the behavioural tally — all
# three were unanimous council claims. Floor is SEPARATE from the behavioural
# tier's MIN_TALLY_N: the
# adjudicator (the chairman) is ~11% nondeterministic on identical input, so a
# floor of 10 admits cells a single re-run could flip; 25 is the council-ratified
# minimum for a cell to mean anything.
SILVER_MIN_N = 25

# The calibration text NAMES ITS SOURCE — the council's decisive catch: 63% was
# measured on the single-chairman RE-CHAIRED research corpus, while the cells
# below come from the user's LIVE labels (a mixed-chairman population), so the
# number is context, not a property of these cells. Re-measure clock per the
# council: native n>=150 credited claims or 2026-11-01, whichever first.
SILVER_CALIBRATION = (
    "SILVER — chairman-adjudicated (opinion, not behaviour), computed from your "
    "own live council labels (prosecutor era, 2026-07-24+). Calibration context: "
    "on a single-chairman research re-chair of 634 historical councils, "
    "chairman verdicts agreed with behaviourally-settled outcomes 63% (n=123). "
    "The live labels shown here have NOT been separately calibrated yet; they "
    "will be re-measured at n>=150 credited claims or 2026-11-01, whichever "
    "comes first. The behavioural ledger remains the verdict tier."
)


_SILVER_FAMILY = {
    "claude": "claude", "claude_ai": "claude", "anthropic": "claude",
    "codex": "codex", "chatgpt": "codex", "openai": "codex", "gpt": "codex",
    "antigravity": "antigravity", "gemini": "antigravity", "google": "antigravity",
}


def _silver_family(text: str) -> str | None:
    """First token of a resolution/side label -> dispatch family, else None."""
    head = re.split(r"[\s:;,./]", str(text or "").strip().lower(), maxsplit=1)[0]
    return _SILVER_FAMILY.get(head)


def silver_tally(home: str | None = None) -> dict:
    """Claim-side chairman-adjudicated win rates from the user's OWN live labels.

    Crediting is by CLAIM SIDE, never member-vs-council-winner: the member-level
    rule was falsified by its own output (one family read 89% and 23% in two
    cells whose only difference was OPPONENT POOL). A claim credits only when
    its `resolution` parses to a single family that sits on one of its sides;
    everything else is counted in `claims_skipped`, not guessed.

    Green-gate: `cells` contains ONLY rows with n >= SILVER_MIN_N — the
    disqualifier is in the gate, not a sibling field. Below-floor cells are
    counted in `withheld_cells`. Zero prosecutor-era claims -> cells={},
    claims_credited=0: an abstain, never an error and never a fabricated green.
    """
    from .model_identity import parse_identity

    base = Path(home).expanduser() if home else trinity_home()
    outcomes = base / "council_outcomes"
    raw: dict[str, list[int]] = {}
    credited = skipped = councils = 0
    if outcomes.is_dir():
        for f in sorted(outcomes.glob("council_*.json")):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue          # parsed-but-wrong-shape is degenerate data, not input
            # EXCLUDE dream/virtual councils, exactly as the behavioural tier does
            # (load_disagreements: "authored with hindsight over the same
            # transcripts, so crediting it would be circular"). This tier stamps
            # itself "computed from your own live council labels (prosecutor era)"
            # and had no such filter: measured 2026-08-07, 6 of 53 credited claims
            # (11%) came from 4 mode='synthesis_only' councils, each an experiment
            # replay run twice, so the claims were correlated duplicates too. The
            # escalation is what makes it urgent rather than cosmetic — `lens --deep`
            # mines hundreds of virtual councils and now stamps member identities on
            # them, so the next deep build would flood the silver cells with
            # hindsight-authored labels while the stamp still promised live ones.
            if (doc.get("metadata") or {}).get("mode") == "synthesis_only":
                continue
            claims = (doc.get("routing_label") or {}).get("disagreed_claims") or []
            if not claims:
                continue
            ident_by_fam: dict[str, Any] = {}
            for m in doc.get("member_results") or []:
                fam = _SILVER_FAMILY.get(str(m.get("provider") or "").lower())
                if fam:
                    meta = m.get("metadata") or {}
                    ident_by_fam[fam] = parse_identity(m.get("model"), meta.get("effort"))
            used_here = False
            for c in claims:
                surv = _silver_family(c.get("resolution"))
                fors = {_silver_family(x) for x in c.get("providers_for") or []} - {None}
                ags = {_silver_family(x) for x in c.get("providers_against") or []} - {None}
                if not surv or not fors or not ags or (surv not in fors and surv not in ags):
                    skipped += 1
                    continue
                credited += 1
                used_here = True
                win_side, lose_side = (fors, ags) if surv in fors else (ags, fors)
                for side, won in ((win_side, True), (lose_side, False)):
                    for fam in side:
                        ident = ident_by_fam.get(fam)
                        if ident is None or ident.family == "?":
                            continue          # identity-unknown members carry no cell
                        mv = f"{ident.family} · {ident.tier} · {ident.version}"
                        cell = raw.setdefault(mv, [0, 0])
                        cell[0 if won else 1] += 1
            councils += used_here
    cells: dict[str, dict] = {}
    withheld = 0
    for mv, (w, l) in raw.items():
        n = w + l
        if n < SILVER_MIN_N:
            withheld += 1
            continue
        lo, hi = wilson_ci(w, n)
        cells[mv] = {"w": w, "l": l, "win_rate": round(w / n, 3),
                     "ci": [round(lo, 3), round(hi, 3)],
                     "ci_excludes_half": bool(lo > 0.5 or hi < 0.5)}
    return {
        "source": "live-prosecutor-labels",
        "claims_credited": credited,
        "claims_skipped": skipped,
        "councils": councils,
        "cells": cells,
        "withheld_cells": withheld,
        "silver_min_n": SILVER_MIN_N,
        "calibration": SILVER_CALIBRATION,
    }


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
