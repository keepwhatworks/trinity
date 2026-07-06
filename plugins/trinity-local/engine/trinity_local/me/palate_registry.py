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
        snap = json.loads(snap_p.read_text(encoding="utf-8"))
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
