"""Residual-over-time log — the cheap, passive half of compression-loop gap #3
(council_65048bab0ab67858, amd_0024).

#3 wants to "aim attention where compression is improving fastest" — the
learning-progress derivative. That derivative needs a RECORD of the residual over
time, which did not exist. This module ships only that record: an append-only
snapshot of the lens/ledger's current prediction-quality state, written once per
lens build. It is PURE RECORDING — no objective is changed, the optimizer-airgap
is untouched, nothing here decides what to explore. It only makes #3 *decidable*
later (compute the derivative off this log once enough points accrue).

Fields are cheap file reads (no embedder, no dispatch) and every one is broad-
guarded: a snapshot must NEVER crash a lens build. A missing input yields null for
that field, not an exception.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..state_paths import trinity_home
from ..utils import now_iso


def residual_log_path(home: str | None = None) -> Path:
    return Path(home or trinity_home()) / "me" / "residual_log.jsonl"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot(home: str | None = None) -> dict:
    """A point-in-time residual snapshot. Cheap, best-effort, never raises.

    The metrics are prediction-quality proxies whose TRAJECTORY encodes learning
    progress: k3 (how well the resolver's stance tracks the chairman), the lens
    symbol count (has the representation stabilised), and the topology health
    (top-basin share — a high share is an uncompressed junk drawer)."""
    base = Path(home or trinity_home())
    out: dict[str, Any] = {"at": now_iso()}

    summ = _read_json(base / "disagreement_ledger" / "summary.json")
    if isinstance(summ, dict):
        out["resolved"] = summ.get("resolved")
        out["k3"] = summ.get("k3_chairman_agreement")
        recs = summ.get("records")
        out["n_model_cells"] = len(recs) if isinstance(recs, dict) else None

    reg = _read_json(base / "me" / "lens_registry.json")
    tensions = reg.get("tensions") if isinstance(reg, dict) else None
    if isinstance(tensions, list):
        out["n_tensions"] = len(tensions)
        out["n_active_tensions"] = sum(1 for t in tensions
                                       if isinstance(t, dict) and t.get("is_active", True))

    topics = _read_json(base / "memories" / "topics.json")
    basins = topics.get("basins") if isinstance(topics, dict) else None
    if isinstance(basins, list) and basins:
        total = sum(int(b.get("size", 0)) for b in basins if isinstance(b, dict)) or 1
        top = max((int(b.get("size", 0)) for b in basins if isinstance(b, dict)), default=0)
        out["n_basins"] = len(basins)
        out["top_basin_share"] = round(top / total, 4)

    return out


def record_snapshot(home: str | None = None) -> dict:
    """Append one residual snapshot to the log. Best-effort: returns the snapshot,
    or {} if it could not be written (a logging failure must not fail the build)."""
    try:
        snap = snapshot(home)
        path = residual_log_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap) + "\n")
        return snap
    except Exception:  # noqa: BLE001 — a logger must never crash the lens build
        return {}


def load_log(home: str | None = None) -> list[dict]:
    """Every recorded snapshot, oldest first. [] when nothing logged yet."""
    path = residual_log_path(home)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out
