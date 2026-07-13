#!/usr/bin/env python3
"""Phase-0 falsifier for the pairwise-vs-rejected eval primitive (HOST-owned).

Council-ratified protocol (council_42411f505043553f design, council_34b026f12254ae45
build review). Runs BEFORE any scorer change: if pairwise judging does not remove
judge-identity dominance, the redesign is dead and we keep absolute scoring.

PRE-REGISTERED THRESHOLDS — locked 2026-07-13, before any judge call:
  PASS:         kendall_tau(judgeA_ranking, judgeB_ranking) >= 0.8
                AND max per-model win-rate swing across judges <= 0.10 (10pp)
  FAIL:         tau < 0.6 OR swing > 0.10, WITH adequate power
  INCONCLUSIVE: median decisive (non-tie) pairs per model < 8, or tau in [0.6, 0.8)
                — underpowered at n~19; extend the item set, do not revert.
                (The council's claude finding: a binary pass/revert at n=19 risks
                reverting a valid design on a power failure, not a design failure.)

Protocol details (each a council amendment):
  * BASELINE-INTEGRITY FILTER FIRST: an item participates only if its
    rejected_response is a real answer (>= 80 chars AND sentence punctuation) —
    else the baseline is a strawman. Drops are logged; survivors saved as the
    static v2 item registry (stable denominators — the 2-1 static-set verdict).
  * NEUTRAL LABELS: the judge sees "Answer 1"/"Answer 2" and the user's own
    fragment as context. It is NEVER told which answer was rejected (the codex
    role-label finding — the label would bias the verdict independent of order).
  * BOTH ORDERS: each pair is judged twice with positions swapped; the two
    verdicts must agree on the winner or the pair scores as a tie.
  * TIES: count 0.5 in win-rate (reported separately); Wilson CI on the
    effective wins.
  * RESUMABLE: every verdict appends to the JSONL immediately; reruns skip
    already-judged (judge, model, item, order) keys — 380 calls must survive
    an interruption (the codex resumability finding).

Usage:
  PYTHONPATH=src .venv/bin/python scripts/pairwise_falsifier.py [--judges antigravity,claude]
Artifacts:  internal/experiments/pairwise-falsifier-2026-07-13/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trinity_local.config import load_config
from trinity_local.evals.pairwise_stats import (
    PAIRWISE_JUDGE_PROMPT,
    kendall_tau,
    parse_pairwise_verdict,
    wilson_ci,
    win_rate,
)
from trinity_local.providers import make_provider

# Fresh set (2026-07-13): the 8b2679 falsifier draft died at load — the
# baseline-integrity filter left 7/18 items, structurally INCONCLUSIVE (the
# decisive-median floor is 8). eval_d04bbda339c3 yields 18 eligible items.
# RUNS maps model identity -> the newest --no-score dispatch on this set,
# resolved at load by (target_model, target_effort) so timestamped filenames
# don't need hand-editing.
EVAL_SET = "eval_d04bbda339c3"
RUN_IDENTITIES = {
    "fable-xhigh": ("claude-fable-5", "xhigh"),
    "gpt5.5-high": ("gpt-5.5", "high"),
    "5.6-terra":   ("gpt-5.6-terra", "xhigh"),
    "5.6-luna":    ("gpt-5.6-luna", "xhigh"),
    "5.6-sol":     ("gpt-5.6-sol", "xhigh"),
}


def resolve_runs() -> dict[str, str]:
    """Newest result file per model identity on EVAL_SET."""
    rdir = Path.home() / ".trinity/evals/results"
    out = {}
    for name, (model, effort) in RUN_IDENTITIES.items():
        candidates = []
        for p in rdir.glob(f"eval_{EVAL_SET}__model_*.json"):
            try:
                d = json.loads(p.read_text())
            except ValueError:
                continue
            if d.get("target_model") == model and (d.get("target_effort") or "xhigh") == effort:
                candidates.append((p.stat().st_mtime, p.name))
        if not candidates:
            raise SystemExit(f"no dispatch on {EVAL_SET} for {name} ({model} {effort}) — run the dispatch phase first")
        out[name] = max(candidates)[1]
    return out


RUNS: dict[str, str] = {}
OUT_DIR = Path(__file__).resolve().parent.parent / "internal/experiments/pairwise-falsifier-2026-07-13"
MIN_BASELINE_CHARS = 80
TAU_PASS, TAU_FAIL = 0.8, 0.6
MAX_SWING = 0.10
MIN_DECISIVE_MEDIAN = 8


def baseline_ok(rejected: str) -> bool:
    t = (rejected or "").strip()
    return len(t) >= MIN_BASELINE_CHARS and bool(re.search(r"[.!?]", t))


def load_runs() -> tuple[dict, dict]:
    """-> (per-model {item_id: response}, {item_id: item-fields}) on the
    baseline-filtered common set."""
    rdir = Path.home() / ".trinity/evals/results"
    per_model, meta = {}, {}
    runs = RUNS or resolve_runs()
    for name, fname in runs.items():
        d = json.loads((rdir / fname).read_text())
        per_model[name] = {
            i["eval_item_id"]: i["target_response"]
            for i in d["items"]
            if (i.get("target_response") or "").strip() and not i.get("target_error")
        }
        for i in d["items"]:
            meta.setdefault(i["eval_item_id"], i)
    common = set.intersection(*(set(v) for v in per_model.values()))
    kept = {i for i in common if baseline_ok(meta[i].get("rejected_response") or "")}
    dropped = sorted(common - kept)
    print(f"common items: {len(common)}; baseline-integrity kept {len(kept)}, "
          f"dropped {len(dropped)}: {dropped}", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "v2_item_registry.json").write_text(json.dumps({
        "eval_set": EVAL_SET, "kept": sorted(kept), "dropped": dropped,
        "rule": f">= {MIN_BASELINE_CHARS} chars AND sentence punctuation in rejected_response",
    }, indent=2))
    return ({m: {i: r for i, r in v.items() if i in kept} for m, v in per_model.items()},
            {i: meta[i] for i in kept})


def judge_call(provider, prompt: str) -> str:
    r = provider.run(prompt, cwd="/tmp")
    return r.stdout or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judges", default="antigravity,claude")
    args = ap.parse_args()
    judges = [j.strip() for j in args.judges.split(",") if j.strip()]

    config = load_config(None, required=True)
    providers = {}
    for j in judges:
        p = make_provider(config.providers[j])
        if hasattr(p, "clean_completion"):
            p.clean_completion = True
        providers[j] = p

    per_model, meta = load_runs()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = OUT_DIR / "verdicts.jsonl"
    done = set()
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines():
            try:
                v = json.loads(line)
                done.add((v["judge"], v["model"], v["item"], v["order"]))
            except (ValueError, KeyError):
                continue

    total = len(judges) * len(per_model) * len(meta) * 2
    n_done = len(done)
    print(f"verdicts needed: {total}; already on ledger: {n_done}", flush=True)

    with ledger_path.open("a", encoding="utf-8") as ledger:
        for judge in judges:
            for model, responses in per_model.items():
                for item_id, it in meta.items():
                    for order in ("mr", "rm"):  # model-first / rejected-first
                        key = (judge, model, item_id, order)
                        if key in done:
                            continue
                        a1, a2 = ((responses[item_id], it["rejected_response"])
                                  if order == "mr"
                                  else (it["rejected_response"], responses[item_id]))
                        prompt = PAIRWISE_JUDGE_PROMPT.format(
                            prompt=it.get("prompt") or "",
                            context_fragment=it.get("user_substitute") or "",
                            answer_1=(a1 or "")[:6000],
                            answer_2=(a2 or "")[:6000],
                        )
                        raw = judge_call(providers[judge], prompt)
                        winner, reason = parse_pairwise_verdict(raw)
                        # normalize to model/rejected/tie under this order
                        pick = ("model" if (winner == "1") == (order == "mr") and winner != "tie"
                                else "tie" if winner == "tie" else "rejected")
                        ledger.write(json.dumps({
                            "judge": judge, "model": model, "item": item_id,
                            "order": order, "pick": pick, "reason": reason[:160],
                        }) + "\n")
                        ledger.flush()
                        n_done += 1
                        if n_done % 20 == 0:
                            print(f"  {n_done}/{total} verdicts", flush=True)

    # ---- aggregate: both-orders agreement -> decision, else tie
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines()]
    verdicts: dict[tuple, dict] = {}
    for v in rows:
        verdicts.setdefault((v["judge"], v["model"], v["item"]), {})[v["order"]] = v["pick"]

    results = {}
    for judge in judges:
        stats = {}
        for model in per_model:
            w = t = l = 0
            for item_id in meta:
                pair = verdicts.get((judge, model, item_id), {})
                a, b = pair.get("mr"), pair.get("rm")
                if a == b == "model":
                    w += 1
                elif a == b == "rejected":
                    l += 1
                else:
                    t += 1
            wr = win_rate(w, t, l)
            lo, hi = wilson_ci(w + 0.5 * t, w + t + l)
            stats[model] = {"wins": w, "ties": t, "losses": l,
                            "win_rate": round(wr, 4), "ci": [round(lo, 4), round(hi, 4)],
                            "decisive": w + l}
        results[judge] = stats

    # ---- the pre-registered verdict
    j1, j2 = judges[0], judges[1] if len(judges) > 1 else judges[0]
    rank = lambda j: sorted(per_model, key=lambda m: -results[j][m]["win_rate"])
    tau = kendall_tau(rank(j1), rank(j2))
    swing = max(abs(results[j1][m]["win_rate"] - results[j2][m]["win_rate"]) for m in per_model)
    decisive_median = sorted(
        min(results[j][m]["decisive"] for j in judges) for m in per_model
    )[len(per_model) // 2]

    if decisive_median < MIN_DECISIVE_MEDIAN or (TAU_FAIL <= tau < TAU_PASS):
        verdict = "INCONCLUSIVE"
    elif tau >= TAU_PASS and swing <= MAX_SWING:
        verdict = "PASS"
    else:
        verdict = "FAIL"

    summary = {"thresholds": {"tau_pass": TAU_PASS, "tau_fail": TAU_FAIL,
                              "max_swing": MAX_SWING, "min_decisive_median": MIN_DECISIVE_MEDIAN},
               "judges": judges, "results": results,
               "rankings": {j: rank(j) for j in judges},
               "tau": round(tau, 4), "max_swing": round(swing, 4),
               "decisive_median": decisive_median, "verdict": verdict}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
