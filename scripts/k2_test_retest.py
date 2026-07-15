"""Taxless K2 — validate the disagreement extractor WITHOUT founder hand-labels
(founder: users won't label 30 claims; the whole tier's thesis is 'actions are
the label, not a form'). Test-retest reliability: run the SAME extractor on two
DISJOINT windows of the user's real subsequent behavior (days 0-7 vs 7-21). A
coin-flip judge can't stay consistent across two independent draws of real
behavior; a real reader will. Cohen's kappa between the two windows' verdicts.

PRE-REGISTERED (locked before running): kappa >= 0.6 PASS (the same bar judges
are held to); < 0.4 FAIL (the extractor is a coin flip — the ledger verdict is
suspect); [0.4, 0.6) INCONCLUSIVE (extend the window / more claims).

Artifacts: internal/experiments/disagreement-ledger-2026-07-14/k2_retest.jsonl
Founder directive on failure: RE-EXAMINE for bugs before any retraction.
"""
from __future__ import annotations
import json, re, sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from trinity_local.config import load_config
from trinity_local.providers import make_provider

BASE = Path(__file__).resolve().parent.parent / "internal/experiments/disagreement-ledger-2026-07-14"
SPLIT_DAY, MIN_PER = 7, 2
PROMPT = """A technical decision was contested. The contested claim:
CLAIM: {claim}
Below are things this person said/asked afterward, most-relevant first:
{evidence}
Judged ONLY by what they said/asked, did their work FOLLOW the claim, CONTRADICT it, or is it UNRESOLVED? Require concrete evidence. One JSON object: {{"resolution": "followed"|"contradicted"|"unresolved"}}"""

def _ts(s):
    try: return datetime.fromisoformat((s or "").replace("Z","+00:00"))
    except ValueError: return None

def _extract(provider, claim, ev):
    raw = (provider.run(PROMPT.format(claim=claim, evidence="\n".join(f'- {e["text"][:600]}' for e in ev)), cwd=Path("/tmp")).stdout or "")
    try:
        o = json.loads(raw[raw.rfind("{"):raw.rfind("}")+1])
        return o.get("resolution") if o.get("resolution") in ("followed","contradicted","unresolved") else "unresolved"
    except (ValueError, KeyError): return "unresolved"

def _kappa(pairs):
    cats = ("followed","contradicted","unresolved")
    n = len(pairs)
    if not n: return None
    po = sum(1 for a,b in pairs if a==b)/n
    pe = sum((sum(1 for a,_ in pairs if a==c)/n)*(sum(1 for _,b in pairs if b==c)/n) for c in cats)
    return (po-pe)/(1-pe) if pe != 1 else 1.0

def main():
    ev = {json.loads(l)["claim_id"]: json.loads(l) for l in (BASE/"evidence.jsonl").read_text().splitlines()}
    res = {json.loads(l)["claim_id"] for l in (BASE/"resolutions.jsonl").read_text().splitlines() if json.loads(l)["resolution"]!="unresolved"}
    provider = make_provider(load_config(None, required=True).providers["antigravity"])
    if hasattr(provider,"clean_completion"): provider.clean_completion = True
    ledger = BASE/"k2_retest.jsonl"
    done = {json.loads(l)["claim_id"] for l in ledger.read_text().splitlines()} if ledger.exists() else set()
    pairs = []
    with ledger.open("a") as f:
        for cid in sorted(res):
            c = ev.get(cid) or {}
            t0 = _ts(c.get("at"))
            if t0 is None: continue
            evd = c.get("evidence") or []
            early = [e for e in evd if _ts(e["at"]) and _ts(e["at"]) <= t0+timedelta(days=SPLIT_DAY)]
            late = [e for e in evd if _ts(e["at"]) and _ts(e["at"]) > t0+timedelta(days=SPLIT_DAY)]
            if len(early) < MIN_PER or len(late) < MIN_PER: continue
            if cid in done:
                for l in ledger.read_text().splitlines():
                    d = json.loads(l)
                    if d["claim_id"]==cid: pairs.append((d["early"], d["late"])); break
                continue
            e_r, l_r = _extract(provider, c["claim"], early), _extract(provider, c["claim"], late)
            f.write(json.dumps({"claim_id": cid, "early": e_r, "late": l_r})+"\n"); f.flush()
            pairs.append((e_r, l_r))
            if len(pairs)%15==0: print(f"  {len(pairs)} pairs", flush=True)
    k = _kappa(pairs)
    verdict = "PASS" if (k is not None and k>=0.6) else "FAIL" if (k is not None and k<0.4) else "INCONCLUSIVE"
    (BASE/"k2_summary.json").write_text(json.dumps({"n_pairs": len(pairs), "test_retest_kappa": round(k,4) if k is not None else None, "verdict": verdict, "floors": {"pass": 0.6, "fail": 0.4}}, indent=2))
    print(f"\nK2 test-retest: n={len(pairs)}, kappa={k:.3f} -> {verdict}" if k is not None else "no pairs")

if __name__ == "__main__": main()
