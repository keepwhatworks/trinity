#!/usr/bin/env python3
"""Disagreement Resolution Ledger — the eval-rethink's first instrument (HOST-owned).

Council-ratified (council_9f4b3ab0a9b640c2): before funding any router or
behavioral tier, falsify the premise. For each REAL council where members
disagreed, determine which branch the user's SUBSEQUENT real work took, and
credit the models on that branch. Paired by construction, behavioral (no LLM
judge scores a quality), retro-runnable on history already on disk.

VIRTUAL COUNCILS EXCLUDED: dream-mined synthesis_only outcomes were authored
with hindsight over the same transcripts we read resolution from — circular
by construction. Only dispatched councils (mode != synthesis_only) count.

PRE-REGISTERED KILLS — locked 2026-07-14 BEFORE any extraction call; any ONE
kills the instrument (and kill 4's null kills the routing tier itself):
  K1 COVERAGE      resolvable (>=MIN_EVIDENCE adjacent subsequent prompts) on
                   >= 40% of attributed real-council claims.
  K2 RELIABILITY   Cohen's kappa >= 0.6 vs 30 hand-labeled claims (founder
                   labels the staged sheet; the extractor must clear the same
                   bar judges are held to).
  K3 NO LEAKAGE    extractor runs BLIND to provider names AND the chairman
                   synthesis; its agreement with the chairman's winner must
                   land in [0.55, 0.90] — ~0.5 = noise, ~1.0 = it's just
                   reading the synthesis back.
  K4 DISCRIMINATION after >= 60 resolved claims, at least one model's
                   resolution win-rate Wilson CI excludes 0.5. If none does,
                   SHIP THE NULL: "on your work the models are interchangeable
                   on average — the value is running them all, not picking
                   one." That kills the routing tier and vindicates the hero.

Assembly knobs (design choices, set before extraction; they shape coverage
and are validated wholesale by K2, not tuned post hoc):
  WINDOW_DAYS=14  TOP_K=8  ADJ_FLOOR=0.35  MIN_EVIDENCE=2

Usage:
  scripts/disagreement_ledger.py            # Phase A: assemble evidence (LLM-free)
  scripts/disagreement_ledger.py --extract  # Phase B: blind resolution extraction
  scripts/disagreement_ledger.py --report   # Phase C: tallies + kill evaluation
Artifacts: internal/experiments/disagreement-ledger-2026-07-14/
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

WINDOW_DAYS = 14
TOP_K = 8
ADJ_FLOOR = 0.35
MIN_EVIDENCE = 2
K1_COVERAGE = 0.40
K3_LOW, K3_HIGH = 0.55, 0.90
K4_MIN_RESOLVED = 60

OUT_DIR = Path(__file__).resolve().parent.parent / "internal/experiments/disagreement-ledger-2026-07-14"


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def load_real_claims() -> list[dict]:
    """Attributed disagreed_claims from REAL councils only."""
    outdir = Path.home() / ".trinity/council_outcomes"
    claims = []
    for p in sorted(outdir.glob("council_*.json")):
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        md = d.get("metadata") or {}
        if md.get("mode") == "synthesis_only":
            continue  # virtual — circular, excluded
        at = _parse_ts(d.get("created_at") or "")
        if at is None:
            continue
        rl = d.get("routing_label") or {}
        # Identity-triple join (2026-07-14): claims attribute at SLUG level,
        # but the same record carries each member's model (+ effort for
        # councils dispatched after the stamping landed) — carry it so the
        # report can slice by model when n allows.
        member_models = {}
        for m in d.get("member_results") or []:
            if isinstance(m, dict) and m.get("provider"):
                ident = m.get("model") or ""
                eff = (m.get("metadata") or {}).get("effort")
                member_models[m["provider"]] = f"{ident} ({eff})" if eff and eff not in str(ident) else str(ident)
        for idx, c in enumerate(rl.get("disagreed_claims") or []):
            if not (isinstance(c, dict) and c.get("claim") and c.get("providers_for")):
                continue
            claims.append({
                "claim_id": f'{d.get("council_run_id")}#{idx}',
                "council_id": d.get("council_run_id"),
                "at": at.isoformat(),
                "claim": str(c.get("claim"))[:500],
                "why_matters": str(c.get("why_matters") or "")[:300],
                "providers_for": list(c.get("providers_for") or []),
                "providers_against": list(c.get("providers_against") or []),
                "chairman_winner": (rl.get("winner") or ""),
                "member_models": member_models,
                "task_excerpt": str(md.get("task_text") or "")[:200],
            })
    return claims


def _load_nodes():
    """(timestamp, text, embedding) for every user prompt node with all three."""
    import numpy as np
    path = Path.home() / ".trinity/prompts/prompt_nodes.jsonl"
    nodes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ts = _parse_ts(d.get("timestamp") or d.get("created_at") or "")
        emb = d.get("embedding")
        text = (d.get("text") or "").strip()
        if ts is None or not emb or not text:
            continue
        v = np.array(emb, dtype=float)
        n = np.linalg.norm(v)
        if n == 0:
            continue
        nodes.append((ts, text, v / n))
    nodes.sort(key=lambda t: t[0])
    return nodes


def phase_a() -> None:
    """Assemble evidence bundles: for each claim, the user's subsequent
    topically-adjacent prompts. LLM-free (stored embeddings + one embed pass
    over claim texts). Emits evidence.jsonl + the K1 coverage read."""
    import numpy as np
    from trinity_local.me.constitution import _default_embed

    claims = load_real_claims()
    print(f"attributed claims from real councils: {len(claims)}", flush=True)
    embed = _default_embed()
    if embed is None:
        raise SystemExit("needs real embeddings (install [mlx] extras)")
    nodes = _load_nodes()
    print(f"user prompt nodes with embeddings: {len(nodes)}", flush=True)

    claim_vecs = embed([f'{c["claim"]} {c["why_matters"]}'[:2000] for c in claims])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    covered = 0
    with (OUT_DIR / "evidence.jsonl").open("w", encoding="utf-8") as f:
        for c, cv in zip(claims, claim_vecs):
            cv = np.array(cv, dtype=float)
            n = np.linalg.norm(cv)
            if n == 0:
                continue
            cv = cv / n
            t0 = _parse_ts(c["at"])
            if t0 is None:
                continue
            t1 = t0 + timedelta(days=WINDOW_DAYS)
            cands = [(float(np.dot(cv, v)), ts, text) for ts, text, v in nodes if t0 < ts <= t1]
            cands = sorted([x for x in cands if x[0] >= ADJ_FLOOR], reverse=True)[:TOP_K]
            c["evidence"] = [{"sim": round(s, 3), "at": ts.isoformat(), "text": text[:800]}
                             for s, ts, text in cands]
            if len(cands) >= MIN_EVIDENCE:
                covered += 1
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    cov = covered / len(claims) if claims else 0.0
    print(f"K1 coverage upper bound: {covered}/{len(claims)} = {cov:.1%} "
          f"(kill floor {K1_COVERAGE:.0%}) -> {'feasible' if cov >= K1_COVERAGE else 'KILLED at assembly'}")


_EXTRACT_PROMPT = """A technical decision was contested. The contested claim:

CLAIM: {claim}
WHY IT MATTERS: {why_matters}
(Decision context: {task_excerpt})

Below are the things this person actually said and asked in the following days, most-relevant first:

{evidence}

Question: judged ONLY by what they subsequently said/asked, did this person's actual work FOLLOW the claim's direction, CONTRADICT it, or is it UNRESOLVED from this evidence? Do not guess from tone; require concrete evidence. Answer with exactly one JSON object:
{{"resolution": "followed"|"contradicted"|"unresolved", "evidence_quote": "<the single most decisive phrase from the evidence, verbatim>"}}"""


def phase_b() -> None:
    """Blind extraction: no provider names, no chairman synthesis in the
    prompt. Resumable ledger; antigravity (the user-validated model) as the
    semantic classifier — K2/K3 validate it regardless of who it is."""
    from trinity_local.config import load_config
    from trinity_local.providers import make_provider

    rows = [json.loads(l) for l in (OUT_DIR / "evidence.jsonl").read_text().splitlines()]
    rows = [r for r in rows if len(r.get("evidence") or []) >= MIN_EVIDENCE]
    config = load_config(None, required=True)
    provider = make_provider(config.providers["antigravity"])
    if hasattr(provider, "clean_completion"):
        provider.clean_completion = True

    ledger = OUT_DIR / "resolutions.jsonl"
    done = set()
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                done.add(json.loads(line)["claim_id"])
            except (ValueError, KeyError):
                continue
    print(f"claims to extract: {len(rows)} (done: {len(done)})", flush=True)
    with ledger.open("a", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            if r["claim_id"] in done:
                continue
            ev = "\n".join(f'- [{e["at"][:10]}] {e["text"]}' for e in r["evidence"])
            prompt = _EXTRACT_PROMPT.format(claim=r["claim"], why_matters=r["why_matters"],
                                            task_excerpt=r["task_excerpt"], evidence=ev)
            res = provider.run(prompt, cwd=Path("/tmp"))
            raw = res.stdout or ""
            resolution, quote = "unresolved", ""
            try:
                start = raw.rfind("{")
                obj = json.loads(raw[start:raw.rfind("}") + 1]) if start != -1 else {}
                if obj.get("resolution") in ("followed", "contradicted", "unresolved"):
                    resolution = obj["resolution"]
                    quote = str(obj.get("evidence_quote") or "")[:200]
            except (ValueError, KeyError):
                pass
            f.write(json.dumps({"claim_id": r["claim_id"], "resolution": resolution,
                                "quote": quote}) + "\n")
            f.flush()
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(rows)}", flush=True)
    print("extraction complete")


def wilson_ci(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval (inlined — the pairwise_stats module died with
    its falsifier per lifecycle; this 10-liner is the only survivor needed)."""
    import math
    if n <= 0:
        return (0.0, 1.0)
    p_ = min(1.0, max(0.0, wins / n))
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p_ + z2 / (2 * n)) / den
    half = z * math.sqrt(max(0.0, p_ * (1 - p_) / n + z2 / (4 * n * n))) / den
    return (max(0.0, center - half), min(1.0, center + half))


def phase_c() -> None:
    """Tallies + kill evaluation + the kappa hand-label sheet."""

    ev = {json.loads(l)["claim_id"]: json.loads(l)
          for l in (OUT_DIR / "evidence.jsonl").read_text().splitlines()}
    res = {}
    for line in (OUT_DIR / "resolutions.jsonl").read_text().splitlines():
        d = json.loads(line)
        res[d["claim_id"]] = d

    resolved = {cid: r for cid, r in res.items() if r["resolution"] != "unresolved"}
    n_claims_with_evidence = sum(1 for c in ev.values() if len(c.get("evidence") or []) >= MIN_EVIDENCE)
    all_claims = len(ev)
    k1 = n_claims_with_evidence / all_claims if all_claims else 0.0

    # per-model credit: followed -> providers_for; contradicted -> providers_against
    tally: dict[str, dict[str, int]] = {}
    chairman_hits = chairman_total = 0
    for cid, r in resolved.items():
        c = ev.get(cid) or {}
        winners = c.get("providers_for") if r["resolution"] == "followed" else c.get("providers_against")
        losers = c.get("providers_against") if r["resolution"] == "followed" else c.get("providers_for")
        for m in winners or []:
            tally.setdefault(m, {"w": 0, "l": 0})["w"] += 1
        for m in losers or []:
            tally.setdefault(m, {"w": 0, "l": 0})["l"] += 1
        cw = c.get("chairman_winner")
        if cw and (cw in (winners or []) or cw in (losers or [])):
            chairman_total += 1
            if cw in (winners or []):
                chairman_hits += 1

    k3 = chairman_hits / chairman_total if chairman_total else None
    print(f"\nclaims: {all_claims} | with evidence: {n_claims_with_evidence} (K1 {k1:.1%}, floor {K1_COVERAGE:.0%})")
    print(f"resolved: {len(resolved)} ({sum(1 for r in resolved.values() if r['resolution']=='followed')} followed, "
          f"{sum(1 for r in resolved.values() if r['resolution']=='contradicted')} contradicted) | "
          f"unresolved: {len(res) - len(resolved)}")
    print(f"K3 chairman agreement: {k3 if k3 is None else round(k3, 3)} (band [{K3_LOW}, {K3_HIGH}], n={chairman_total})")
    print(f"\nper-model resolution record (Wilson 95% CI):")
    k4_pass = False
    for m, t in sorted(tally.items(), key=lambda kv: -(kv[1]["w"] / max(1, kv[1]["w"] + kv[1]["l"]))):
        n = t["w"] + t["l"]
        wr = t["w"] / n if n else 0.0
        lo, hi = wilson_ci(t["w"], n)
        mark = ""
        if n >= 10 and (lo > 0.5 or hi < 0.5):
            mark = "  <-- CI excludes 0.5"
            k4_pass = True
        print(f"  {m:<14} {t['w']:>3}W {t['l']:>3}L  win-rate {wr:.3f}  CI [{lo:.3f}, {hi:.3f}]{mark}")

    verdicts = {
        "K1_coverage": {"value": round(k1, 3), "pass": k1 >= K1_COVERAGE},
        "K2_kappa": "PENDING founder hand-labels (kappa_sheet.jsonl staged)",
        "K3_chairman_agreement": {"value": None if k3 is None else round(k3, 3),
                                  "pass": (k3 is not None and K3_LOW <= k3 <= K3_HIGH)},
        "K4_discrimination": {"resolved": len(resolved), "min_required": K4_MIN_RESOLVED,
                              "any_ci_excludes_half": k4_pass,
                              "pass": (len(resolved) >= K4_MIN_RESOLVED and k4_pass)},
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(
        {"tally": tally, "kills": verdicts,
         "knobs": {"window_days": WINDOW_DAYS, "top_k": TOP_K,
                   "adj_floor": ADJ_FLOOR, "min_evidence": MIN_EVIDENCE}}, indent=2))
    # kappa sheet: 30 random resolved claims for founder hand-labeling
    import random
    rng = random.Random(20260714)
    sample = rng.sample(sorted(resolved), min(30, len(resolved)))
    with (OUT_DIR / "kappa_sheet.jsonl").open("w", encoding="utf-8") as f:
        for cid in sample:
            c = ev[cid]
            f.write(json.dumps({"claim_id": cid, "claim": c["claim"],
                                "evidence": c["evidence"],
                                "extractor_said": resolved[cid]["resolution"],
                                "your_label": ""}, ensure_ascii=False) + "\n")
    # Model-level slice (identity-triple join) — printed with its own
    # honesty line: cells shatter fast at this n, so model rows are
    # DESCRIPTIVE until a cell independently clears CI-excludes-0.5.
    model_tally: dict[str, dict[str, int]] = {}
    for cid, r in resolved.items():
        c = ev.get(cid) or {}
        mm = c.get("member_models") or {}
        winners = c.get("providers_for") if r["resolution"] == "followed" else c.get("providers_against")
        losers = c.get("providers_against") if r["resolution"] == "followed" else c.get("providers_for")
        for slug in winners or []:
            key = mm.get(slug) or f"{slug} (model unknown)"
            model_tally.setdefault(key, {"w": 0, "l": 0})["w"] += 1
        for slug in losers or []:
            key = mm.get(slug) or f"{slug} (model unknown)"
            model_tally.setdefault(key, {"w": 0, "l": 0})["l"] += 1
    if model_tally:
        print("\nmodel-level slice (descriptive below per-cell significance):")
        for mdl, t in sorted(model_tally.items(), key=lambda kv: -(kv[1]["w"] / max(1, kv[1]["w"] + kv[1]["l"]))):
            n = t["w"] + t["l"]
            lo, hi = wilson_ci(t["w"], n)
            print(f"  {mdl:<32} {t['w']:>3}W {t['l']:>3}L  wr {t['w']/n if n else 0:.3f}  CI [{lo:.2f},{hi:.2f}]")

    print(f"\nkill summary -> {OUT_DIR / 'summary.json'}")
    print(f"kappa sheet (30 claims, founder labels 'your_label') -> {OUT_DIR / 'kappa_sheet.jsonl'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.extract:
        phase_b()
    elif args.report:
        phase_c()
    else:
        phase_a()
