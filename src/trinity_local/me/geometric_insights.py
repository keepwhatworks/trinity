"""Embedding-insight toolkit (#257) — geometric mining over the prompt corpus.

The correction-space tools (drift, per-basin signature) live in
`correction_lens.py`; this module holds the PROMPT-space tools. First one:
`outlier_prompts()` — the asks that sit farthest from every subject basin, i.e.
the user's most unusual prompts (novel directions, one-off explorations that
never became a topic).

All read-only, best-effort, no LLM calls (pure geometry over local embeddings).
"""
from __future__ import annotations

import json

from ..embeddings import is_finite_embedding
from ..state_paths import state_dir


def _load_basin_centroids() -> list[tuple[str, str, list[float]]]:
    """Return [(basin_id, label, centroid_vec), ...] from topics.json. Empty
    list when topics.json is missing / has no centroids."""
    try:
        topics = json.loads(
            (state_dir() / "memories" / "topics.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(topics, dict):
        return []
    out: list[tuple[str, str, list[float]]] = []
    for b in topics.get("basins", []):
        cen = b.get("centroid")
        if isinstance(cen, list) and cen:
            out.append((b.get("id", ""), (b.get("label") or "").strip(), cen))
    return out


def outlier_prompts(top_n: int = 8, min_chars: int = 40) -> dict:
    """The user's most UNUSUAL prompts: those with the lowest max-cosine to ANY
    subject basin centroid — asks that don't fit an established topic.

    `min_chars` floors out terse fillers ("continue", "ok") so the outliers are
    real prose, not noise. Returns `{"ready": False}` when there are no basin
    centroids or no embedded prompts. Pure geometry — no LLM, no network.
    """
    import numpy as np

    # Real-embeddings gate (parity with the correction_lens flows): outlier scoring
    # is cosine-to-centroid geometry, meaningless when the corpus was embedded under
    # the SHA-1 TF-IDF fallback — the "most unusual prompts" become noise dressed as
    # insight in the `trinity-local me` output. Abstain rather than surface garbage.
    from ..embeddings import mlx_actually_loaded
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    centroids = _load_basin_centroids()
    if not centroids:
        return {"ready": False, "reason": "no basin centroids (run trinity-local lens)"}

    try:
        from ..memory.store import iter_prompt_nodes
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}

    cen_mat = np.asarray([c for _, _, c in centroids], dtype=float)
    cen_norms = np.linalg.norm(cen_mat, axis=1)
    cen_norms[cen_norms == 0] = 1.0
    cen_unit = cen_mat / cen_norms[:, None]

    scored: list[tuple[float, int, str]] = []  # (max_cos, basin_idx, text)
    n_considered = 0
    for node in iter_prompt_nodes(limit=None):
        emb = getattr(node, "embedding", None)
        text = (getattr(node, "text", "") or "").strip()
        if not is_finite_embedding(emb) or len(text) < min_chars:
            continue
        v = np.asarray(emb, dtype=float)
        nv = np.linalg.norm(v)
        if nv == 0:
            continue
        sims = cen_unit @ (v / nv)
        best = int(np.argmax(sims))
        scored.append((float(sims[best]), best, text))
        n_considered += 1

    if not scored:
        return {"ready": False, "reason": "no embedded prompts meet the floor"}

    # Lowest max-cosine first = farthest from every basin = most unusual.
    # Tie-break on the snippet text so the scored[:top_n] cut is a TOTAL order:
    # two prompts equidistant from every basin straddling the top_n boundary
    # would otherwise keep the scan order, so which outlier is shown would flip.
    scored.sort(key=lambda r: (r[0], r[2]))
    outliers = []
    for max_cos, bidx, text in scored[:top_n]:
        bid, label, _ = centroids[bidx]
        outliers.append({
            "snippet": text[:160],
            "nearest_basin": bid,
            "nearest_label": label[:50],
            "nearest_cosine": round(max_cos, 3),
        })
    return {
        "ready": True,
        "n_considered": n_considered,
        "n_basins": len(centroids),
        "outliers": outliers,
    }
