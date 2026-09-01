"""Prospective palate registry — the lens's stand-in claim, scored on LIVE choices.

The retrospective validation (preference_collapse: 90% held-out, p=0.011,
n=10) is a frozen split — thin, and it only ever re-scores the past. The
product claim under test ("when an agent faces a choice, your lens picks what
you'd pick") deserves a PROSPECTIVE number: every correction act that arrives
AFTER the lens direction was frozen is a real, observed user choice the frozen
direction never saw. Score them as they accumulate and the accuracy figure
grows with real usage instead of a fixed validation set.

Mechanics (LLM-free, append-only, numbers-only):
  1. `record_direction_snapshot()` — at lens-build time, fit the direction on
     the acts that exist NOW and freeze it with the SET of act ids it was fit
     on (`palate_snapshot.json`). Before replacing an existing snapshot, score
     any still-pending acts against the OUTGOING direction — no trial is lost,
     and every trial is scored by the direction that was live when the choice
     arrived.
  2. `score_prospective()` — for each act NOT in the snapshot's fit-set and
     not already scored: project privileged and sacrificed onto the frozen
     direction; correct = privileged projects higher. |gap| below
     ABSTAIN_GAP records an honest abstain (the stand-in says "ask the
     human"), counted separately — never as correct. Verdicts append to
     `palate_trials.jsonl` (act ids + numbers only; no text).

The train-on-test wall is the fit-id SET: an act the direction was fit on can
never become a trial. Discriminative-only by design — this measures the
validated palate (pick between two given options), not generation (measured
null 2026-07-05, n=30 p=0.43; see lens_wrong_level resolution).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..state_paths import trinity_home
from ..utils import now_iso

# Pre-registered (2026-07-06):
ABSTAIN_GAP = 0.02   # |proj(privileged) − proj(sacrificed)| below this → abstain
                     # (the preference_collapse noise floor, same scale)
MIN_FIT_ACTS = 8     # below this the direction is noise → no snapshot (pc.MIN_TRAIN)
EARLY_N = 10         # below this the running accuracy reads as "early", not a number


def _snapshot_path():
    return trinity_home() / "me" / "palate_snapshot.json"


def _act_sha256(a) -> str:
    """Stable content key for a preference act: hash of the two poles.

    Survives corpus regens that re-mint act ids, so a trial stays auditable
    against WHAT it scored even after every id it knew is gone.
    """
    import hashlib

    basis = f"{(a.privileged or '').strip()}\x1f{(a.sacrificed or '').strip()}"
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:16]


def _trials_path():
    return trinity_home() / "me" / "palate_trials.jsonl"


def _fit_acts(acts) -> list:
    return [a for a in acts if getattr(a, "privileged", None) and getattr(a, "sacrificed", None)]


def record_direction_snapshot(embed_fn: Callable | None = None) -> dict[str, Any]:
    """Freeze the current direction + fit-set. Scores pending trials against
    the OUTGOING snapshot first (score-then-replace), so a rebuild never
    swallows choices that arrived under the old direction. LLM-free; abstains
    under TF-IDF or a thin ledger. Never raises."""
    try:
        if embed_fn is None:
            from .constitution import _default_embed
            embed_fn = _default_embed()
        if embed_fn is None:
            return {"ok": False, "reason": "needs real embeddings"}
        from .preference_acts import load_preference_acts
        from .preference_collapse import _fit_direction

        acts = _fit_acts(load_preference_acts())
        if len(acts) < MIN_FIT_ACTS:
            return {"ok": False, "reason": f"thin ledger ({len(acts)} < {MIN_FIT_ACTS})"}

        # score-then-replace: settle pending trials on the outgoing direction
        scored_before = 0
        if _snapshot_path().exists():
            scored_before = score_prospective(embed_fn=embed_fn).get("newly_scored", 0)

        direction = _fit_direction(acts, embed_fn)
        if not direction:
            return {"ok": False, "reason": "direction fit failed"}
        payload = {
            "built_at": now_iso(),
            "fit_act_ids": sorted(a.id for a in acts),
            "direction": [round(float(x), 6) for x in direction],
        }
        p = _snapshot_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload), encoding="utf-8")
        return {"ok": True, "fit_n": len(acts), "settled_pending": scored_before}
    except Exception as exc:  # noqa: BLE001 — a meter must never break the build
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _load_scored_ids() -> set[str]:
    p = _trials_path()
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            if isinstance(d, dict) and d.get("act_id"):
                out.add(d["act_id"])
        except ValueError:
            continue
    return out


def _load_snapshot() -> dict | None:
    """Shape-guarded snapshot read (#304 corrupt-state vein): a hand-edited /
    truncated / wrong-typed palate_snapshot.json returns None — every caller
    degrades to an honest "snapshot unreadable" instead of leaking an
    AttributeError through choose / lens-health / score_prospective."""
    p = _snapshot_path()
    if not p.exists():
        return None
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) else None


def score_prospective(embed_fn: Callable | None = None) -> dict[str, Any]:
    """Score every not-yet-scored act that postdates the frozen direction.
    Appends verdicts (numbers + ids only) and returns the running summary.
    Idempotent: an act is scored at most once, against the direction that was
    frozen when it arrived. Never raises."""
    try:
        import numpy as np

        snap_p = _snapshot_path()
        if not snap_p.exists():
            return {"ready": False, "reason": "no direction snapshot yet (build the lens)"}
        if embed_fn is None:
            from .constitution import _default_embed
            embed_fn = _default_embed()
        if embed_fn is None:
            return {"ready": False, "reason": "needs real embeddings"}
        snap = _load_snapshot()
        if snap is None:
            return {"ready": False, "reason": "palate snapshot unreadable — rebuild with `trinity-local lens`"}
        fit_ids = set(snap.get("fit_act_ids") or [])
        d = np.array(snap.get("direction") or [], dtype=float)
        if d.size == 0:
            return {"ready": False, "reason": "snapshot missing direction"}

        from .preference_acts import load_preference_acts
        acts = _fit_acts(load_preference_acts())
        scored = _load_scored_ids()
        # The train-on-test wall: fit-set acts can NEVER become trials.
        pending = [a for a in acts if a.id not in fit_ids and a.id not in scored]

        rows = []
        for a in pending:
            vecs = embed_fn([a.privileged[:2000], a.sacrificed[:2000]])
            pv, sv = (np.array(v, dtype=float) for v in vecs)
            pn, sn = np.linalg.norm(pv), np.linalg.norm(sv)
            if pn == 0 or sn == 0:
                continue
            gap = float(np.dot(pv / pn, d) - np.dot(sv / sn, d))
            rows.append({
                "act_id": a.id,
                # Regen-proof audit keys (res_077): a corpus re-mine mints fresh
                # act ids, which orphaned 198 of 354 trials. The content hash
                # re-joins a trial to the same text under any future id, and
                # prompt_id is stable across regens (nodes persist; acts are
                # re-mined) and carries the provider join the per-environment
                # invariance slice reads. Ids + numbers only — the file's
                # no-text property is deliberate and unchanged.
                "act_sha256": _act_sha256(a),
                "prompt_id": getattr(a, "prompt_id", None) or None,
                "verdict": "abstain" if abs(gap) < ABSTAIN_GAP else ("correct" if gap > 0 else "incorrect"),
                "gap": round(gap, 4),
                "scored_at": now_iso(),
                "snapshot_built_at": snap.get("built_at"),
            })
        if rows:
            p = _trials_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        return {**summarize_trials(), "ready": True, "newly_scored": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "reason": f"{type(exc).__name__}: {exc}"}


def summarize_trials() -> dict[str, Any]:
    """Running prospective tally over all recorded trials. Abstains are
    disclosed, never counted correct — accuracy is over DECIDED trials only."""
    p = _trials_path()
    correct = incorrect = abstain = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                v = json.loads(line).get("verdict")
            except (ValueError, AttributeError):
                continue
            if v == "correct":
                correct += 1
            elif v == "incorrect":
                incorrect += 1
            elif v == "abstain":
                abstain += 1
    decided = correct + incorrect
    return {
        "trials": decided + abstain,
        "decided": decided,
        "correct": correct,
        "incorrect": incorrect,
        "abstained": abstain,
        "accuracy": round(correct / decided, 3) if decided else None,
        "early": decided < EARLY_N,
    }


# ── The choice-oracle (task #11, 2026-07-09) ────────────────────────────────
# The SELECTION half of the stand-in claim, productized. Pre-registered
# contract: rank_options ranks on the SAME frozen direction the prospective
# registry scores (live accuracy travels with every answer — the consumer
# sees the instrument's measured trust, not an implied one); the gap floor
# is the SAME ABSTAIN_GAP the registry uses; below it the oracle says "ask
# the human" instead of manufacturing a preference. Kill condition (shared
# with the registry, already registered): live accuracy < 0.60 at n>=10 →
# lens-health fires WEAK and the oracle stamps itself advisory-only.

def rank_options(options: list[str], embed_fn=None) -> dict:
    """Rank candidate options by the user's frozen taste direction.

    Returns {ready, ranked: [{option, score}...], confidence_gap, abstain,
    advisory_only, live_accuracy, decided_trials, reason?}. LLM-free: two
    local embeddings per option. Never raises."""
    try:
    
        import numpy as np

        if not options or len(options) < 2:
            return {"ready": False, "reason": "need at least two options to rank"}
        snap_p = _snapshot_path()
        if not snap_p.exists():
            return {"ready": False, "reason": "no direction snapshot yet (build the lens)"}
        if embed_fn is None:
            from .constitution import _default_embed
            embed_fn = _default_embed()
        if embed_fn is None:
            return {"ready": False, "reason": "needs real embeddings"}
        snap = _load_snapshot()
        if snap is None:
            return {"ready": False, "reason": "palate snapshot unreadable — rebuild with `trinity-local lens`"}
        d = np.array(snap.get("direction") or [], dtype=float)
        if d.size == 0:
            return {"ready": False, "reason": "snapshot missing direction"}

        vecs = embed_fn([str(o)[:2000] for o in options])
        scored = []
        for o, v in zip(options, vecs):
            v = np.array(v, dtype=float)
            n = np.linalg.norm(v)
            scored.append({"option": str(o),
                           "score": round(float(np.dot(v, d) / n), 4) if n else 0.0})
        ranked = sorted(scored, key=lambda r: -r["score"])
        gap = round(ranked[0]["score"] - ranked[1]["score"], 4)

        trials = summarize_trials()
        acc = trials.get("accuracy")
        n_dec = trials.get("decided", 0)
        advisory = bool(acc is not None and n_dec >= EARLY_N and acc < 0.60)
        return {
            "ready": True,
            "ranked": ranked,
            "confidence_gap": gap,
            # The same pre-registered floor the registry abstains under: a
            # near-zero gap is a coin flip the oracle must not dress up.
            "abstain": gap < ABSTAIN_GAP,
            "advisory_only": advisory,
            "live_accuracy": acc,
            "decided_trials": n_dec,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "reason": f"{type(exc).__name__}: {exc}"}
