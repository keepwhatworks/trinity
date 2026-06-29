"""The correction-vector lens — the lens as GEOMETRY (#257).

Each preference act is a steer: the model offered `sacrificed`, the user
privileged `privileged` instead. The vector `embed(privileged) − embed(sacrificed)`
is the DIRECTION the user pushed in embedding space. Averaged over every
correction, the residual (after per-act topic noise cancels) is the user's
consistent taste *direction* — the text-tension lens, but as a single vector you
can decompose onto interpretable axes.

Validated on the real corpus (122 corrections): per-act coherence is LOW (~0.14,
just over the random-pairing null) — individual corrections scatter because each
is about a different specific thing. BUT the mean direction's projection onto
interpretable axes is highly significant (≈+0.20 = ~5σ; a random unit vector
loads ±1/√768 ≈ 0.036) and matches the known lens: this user steers strongly
toward concrete + decisive, mildly toward terse. So the lens-as-vector is real
in AGGREGATE; the right readout is the axis signature + an honest coherence
caveat, not a claim that every correction agrees.
"""
from __future__ import annotations

import math
from typing import Sequence

# Interpretable taste axes, each a (positive-pole, negative-pole) prototype pair.
# The signature reports the mean correction's cosine to each axis: + leans to the
# first pole. Add an axis = add a prototype pair (no rule, no retraining).
TASTE_AXES: dict[str, tuple[list[str], list[str]]] = {
    "concrete↔abstract": (
        ["give the complete runnable code to copy paste", "the exact command to run", "the specific product to buy"],
        ["explain the general concept", "a high-level overview", "walk through the underlying theory"],
    ),
    "terse↔verbose": (
        ["just the answer", "keep it short", "one line"],
        ["a detailed thorough explanation with full context and caveats"],
    ),
    "decisive↔hedging": (
        ["just pick one and commit", "make the call", "what would you do"],
        ["it depends on many factors", "there are tradeoffs to weigh", "I can't decide for you"],
    ),
    "action↔description": (
        ["do it now", "ship the change", "build it"],
        ["here is what you could do", "the options are", "an overview of approaches"],
    ),
}

# Below this many corrections the signature isn't worth surfacing.
_MIN_CORRECTIONS = 12


def _unit(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else list(vec)


def _mean(rows: list[list[float]]) -> list[float]:
    if not rows:
        return []
    dim = len(rows[0])
    acc = [0.0] * dim
    for r in rows:
        for i, x in enumerate(r):
            acc[i] += x
    return [x / len(rows) for x in acc]


def correction_signature() -> dict:
    """Compute the user's correction-vector lens: the mean steer direction, its
    coherence (how aligned the individual corrections are), and its loading on
    each interpretable taste axis. Best-effort; `{"ready": False}` on no
    embedder or too few corrections."""
    try:
        from ..embeddings import embed_batch, mlx_actually_loaded
        from .preference_acts import iter_preference_acts
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}

    # Real-embeddings gate: the correction-vector geometry is meaningless under
    # the TF-IDF fallback (0/5 on the meaning triplet — it groups by word
    # overlap). Abstain rather than emit inverted taste axes.
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    pairs = [
        ((a.sacrificed or "").strip(), (a.privileged or "").strip())
        for a in iter_preference_acts()
        if (a.sacrificed or "").strip() and (a.privileged or "").strip()
        and len((a.sacrificed or "").strip()) > 4 and len((a.privileged or "").strip()) > 2
    ]
    if len(pairs) < _MIN_CORRECTIONS:
        return {"ready": False, "n": len(pairs), "min": _MIN_CORRECTIONS}

    try:
        sac = [_unit(v) for v in embed_batch([s for s, _ in pairs])]
        pri = [_unit(v) for v in embed_batch([p for _, p in pairs])]
    except Exception as exc:
        return {"ready": False, "reason": f"embed failed: {exc!r}"}

    corrections = [[p - s for p, s in zip(pv, sv)] for pv, sv in zip(pri, sac)]
    mean_c = _mean(corrections)
    norms = [math.sqrt(sum(x * x for x in c)) for c in corrections]
    mean_norm = sum(norms) / len(norms) if norms else 0.0
    coherence = (math.sqrt(sum(x * x for x in mean_c)) / mean_norm) if mean_norm else 0.0
    mc = _unit(mean_c)

    axes: dict[str, float] = {}
    for name, (pos, neg) in TASTE_AXES.items():
        pcen = _mean([list(v) for v in embed_batch(pos)])
        ncen = _mean([list(v) for v in embed_batch(neg)])
        ax = _unit([p - n for p, n in zip(pcen, ncen)])
        axes[name] = round(sum(a * b for a, b in zip(mc, ax)), 3)

    return {
        "ready": True,
        "n": len(pairs),
        # Low per-act coherence is expected (corrections scatter by topic); the
        # axis loadings are the signal. Surfaced so callers don't over-claim.
        "coherence": round(coherence, 3),
        "axes": dict(sorted(axes.items(), key=lambda kv: -abs(kv[1]))),
    }


def _taste_signature_path():
    from .basins import me_dir
    return me_dir() / "taste_signature.json"


def save_taste_signature(sig: dict) -> None:
    """Persist the (expensive, embedding-derived) taste adjectives so the
    cold-open can read them cheaply at every paint instead of re-embedding.
    Called at lens-build time (#254). Best-effort — never raises into a build."""
    if not sig.get("ready"):
        return
    try:
        import json

        from ..utils import now_iso
        payload = {
            "adjectives": sig.get("adjectives", []),
            "n": sig.get("n", 0),
            "computed_at": now_iso(),
        }
        path = _taste_signature_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_taste_signature() -> dict | None:
    """Read the cached taste adjectives (cheap; no embedder). None if absent."""
    try:
        import json
        path = _taste_signature_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data.get("adjectives") else None
    except Exception:
        return None


def taste_signature(max_axes: int = 3, min_loading: float = 0.05) -> dict:
    """#254 cold-open material — a compact, human-facing synthesis of the
    correction lens: the top taste axes as adjectives (the pole the user steers
    TOWARD) plus ONE representative correction quoted in the user's OWN words.

    The "quote your taste back to you" payload no chat tab can produce. Returns
    the raw data; the user-facing copy/tone is composed at the call site (held
    for founder review per #254). Best-effort `{"ready": False}`.
    """
    try:
        from ..embeddings import embed_batch, mlx_actually_loaded
        from .preference_acts import iter_preference_acts
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}

    # Real-embeddings gate (TF-IDF can't separate meaning — abstain, don't
    # surface inverted taste adjectives in the user-facing cold-open).
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    pairs = [
        ((a.sacrificed or "").strip(), (a.privileged or "").strip())
        for a in iter_preference_acts()
        if (a.sacrificed or "").strip() and (a.privileged or "").strip()
        and len((a.sacrificed or "").strip()) > 4 and len((a.privileged or "").strip()) > 2
    ]
    if len(pairs) < _MIN_CORRECTIONS:
        return {"ready": False, "n": len(pairs), "min": _MIN_CORRECTIONS}

    try:
        sac = [_unit(v) for v in embed_batch([s for s, _ in pairs])]
        pri = [_unit(v) for v in embed_batch([p for _, p in pairs])]
        axisvec = _axis_vectors()
    except Exception as exc:
        return {"ready": False, "reason": f"embed failed: {exc!r}"}

    corrections = [[p - s for p, s in zip(pv, sv)] for pv, sv in zip(pri, sac)]
    mc = _unit(_mean(corrections))

    # Adjectives: the pole each axis steers toward (sign of the loading).
    loaded: list[tuple[float, str, float]] = []
    for name, av in axisvec.items():
        load = sum(a * b for a, b in zip(mc, av))
        if abs(load) >= min_loading:
            pole = name.split("↔")[0] if load > 0 else name.split("↔")[1]
            loaded.append((abs(load), pole, round(load, 3)))
    loaded.sort(reverse=True)
    adjectives = [pole for _, pole, _ in loaded[:max_axes]]

    # Representative correction: the steer best aligned with the mean direction
    # — the single most "you" rephrasing, quotable in the user's own words.
    aligns = [
        sum(ci * mi for ci, mi in zip(_unit(c), mc)) for c in corrections
    ]
    best = max(range(len(pairs)), key=lambda i: aligns[i])
    representative = {
        "model_offered": pairs[best][0][:160],
        "you_wanted": pairs[best][1][:160],
        "alignment": round(aligns[best], 3),
    }
    return {
        "ready": True,
        "n": len(pairs),
        "adjectives": adjectives,
        "axes_loaded": [{"pole": p, "loading": l} for _, p, l in loaded[:max_axes]],
        "representative": representative,
    }


def _axis_vectors() -> dict:
    """Embed the interpretable taste-axis directions (pos centroid − neg
    centroid, unit). Shared by the signature + drift computations."""
    from ..embeddings import embed_batch

    out = {}
    for name, (pos, neg) in TASTE_AXES.items():
        pcen = _mean([list(v) for v in embed_batch(pos)])
        ncen = _mean([list(v) for v in embed_batch(neg)])
        out[name] = _unit([p - n for p, n in zip(pcen, ncen)])
    return out


def _mean_correction_unit(pairs: list[tuple[str, str]]) -> list[float]:
    """Mean steer direction (unit) over a set of (sacrificed, privileged)
    pairs — embed(privileged) − embed(sacrificed), averaged, normalized."""
    from ..embeddings import embed_batch

    sac = [_unit(v) for v in embed_batch([s for s, _ in pairs])]
    pri = [_unit(v) for v in embed_batch([p for _, p in pairs])]
    corrections = [[p - s for p, s in zip(pv, sv)] for pv, sv in zip(pri, sac)]
    return _unit(_mean(corrections))


def _basin_labels() -> dict:
    """Map basin id → human label from topics.json (best-effort, {} on miss)."""
    try:
        import json

        from ..state_paths import state_dir

        topics = json.loads(
            (state_dir() / "memories" / "topics.json").read_text(encoding="utf-8")
        )
        out = {}
        for b in topics.get("basins", []):
            bid = b.get("id")
            if bid:
                out[bid] = (b.get("label") or b.get("title") or b.get("name") or "").strip()
        return out
    except Exception:
        return {}


def correction_signature_by_basin(min_per_basin: int = _MIN_CORRECTIONS) -> dict:
    """Per-domain correction lens (#257): the taste-axis signature computed
    SEPARATELY for each subject basin with enough corrections.

    The same person steers differently by domain — on Trinity's own corpus the
    ticket/decision basin pushes hardest for `concrete` + `decisive`, while the
    review/admin basins push for `action`/executability. A single global
    signature averages that away; this surfaces it. Best-effort
    `{"ready": False}` on no embedder or no basin clearing `min_per_basin`.
    """
    try:
        from ..embeddings import mlx_actually_loaded
        from .preference_acts import iter_preference_acts
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}

    # Real-embeddings gate (parity with correction_signature/taste_signature):
    # per-domain correction axes computed under TF-IDF are inverted-axis noise.
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    from collections import defaultdict

    by_basin: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for a in iter_preference_acts():
        s, p = (a.sacrificed or "").strip(), (a.privileged or "").strip()
        if a.basin and len(s) > 4 and len(p) > 2:
            by_basin[a.basin].append((s, p))

    eligible = {b: pairs for b, pairs in by_basin.items() if len(pairs) >= min_per_basin}
    if not eligible:
        return {"ready": False, "reason": "no basin with enough corrections",
                "min_per_basin": min_per_basin}

    try:
        axisvec = _axis_vectors()
    except Exception as exc:
        return {"ready": False, "reason": f"embed failed: {exc!r}"}

    labels = _basin_labels()
    basins: dict[str, dict] = {}
    for basin, pairs in eligible.items():
        try:
            mc = _mean_correction_unit(pairs)
        except Exception:
            continue
        loads = {
            name: round(sum(x * y for x, y in zip(mc, av)), 3)
            for name, av in axisvec.items()
        }
        ranked = sorted(loads.items(), key=lambda kv: -abs(kv[1]))
        basins[basin] = {
            "n": len(pairs),
            "label": (labels.get(basin) or "")[:60],
            "axes": dict(ranked),
            "top_axis": {"axis": ranked[0][0], "loading": ranked[0][1]},
        }
    if not basins:
        return {"ready": False, "reason": "embed failed for all basins"}
    return {
        "ready": True,
        "n_basins": len(basins),
        # Most-corrected basins first.
        "basins": dict(sorted(basins.items(), key=lambda kv: -kv[1]["n"])),
    }


def correction_drift() -> dict:
    """Diachronic correction lens (#257): split the user's timed corrections
    into an early and a recent half and report how each interpretable
    taste-axis loading MOVED.

    This is the asymmetric insight no within-session memory (Auto-Dream
    included) can produce — not "what's your taste" but "how did your taste
    *change*." On Trinity's own corpus it shows the user's #1 steer shifting
    from `concrete↔abstract` (early) to `action↔description` (recent): as the
    models matured they stopped having to ask for concreteness and started
    pushing for executability.

    Time comes from each act's prompt_id → PromptNode → prompt_time. Acts with
    no resolvable time are dropped (can't be placed on the axis). Best-effort:
    `{"ready": False}` on no embedder or too few timed corrections to split.
    """
    try:
        from ..embeddings import mlx_actually_loaded
        from ..memory.store import load_prompt_node
        from .chapters import prompt_time
        from .preference_acts import iter_preference_acts
    except Exception:
        return {"ready": False, "reason": "imports unavailable"}

    # Real-embeddings gate (parity with correction_signature/taste_signature): the
    # correction-vector geometry is meaningless under the TF-IDF fallback, so a
    # taste-DRIFT computed on it is inverted-axis noise dressed as an insight.
    # Importability of embed_batch is NOT enough — it imports fine under TF-IDF.
    if not mlx_actually_loaded():
        return {"ready": False, "reason": "needs real embeddings (install [mlx])"}

    timed: list[tuple[str, str, str]] = []
    for a in iter_preference_acts():
        s, p = (a.sacrificed or "").strip(), (a.privileged or "").strip()
        if not (len(s) > 4 and len(p) > 2 and a.prompt_id):
            continue
        node = load_prompt_node(a.prompt_id)
        if node is None:
            continue
        t = prompt_time(node)
        if t:
            timed.append((t, s, p))

    # Need enough on BOTH sides of the split to trust either half.
    if len(timed) < 2 * _MIN_CORRECTIONS:
        return {"ready": False, "n": len(timed), "min": 2 * _MIN_CORRECTIONS}

    timed.sort(key=lambda x: x[0])
    mid = len(timed) // 2
    early, recent = timed[:mid], timed[mid:]

    try:
        axisvec = _axis_vectors()
        mc_e = _mean_correction_unit([(s, p) for _, s, p in early])
        mc_r = _mean_correction_unit([(s, p) for _, s, p in recent])
    except Exception as exc:
        return {"ready": False, "reason": f"embed failed: {exc!r}"}

    axes: dict[str, dict] = {}
    for name, av in axisvec.items():
        e = round(sum(x * y for x, y in zip(mc_e, av)), 3)
        r = round(sum(x * y for x, y in zip(mc_r, av)), 3)
        axes[name] = {"early": e, "recent": r, "delta": round(r - e, 3)}

    mover_name, mover = max(axes.items(), key=lambda kv: abs(kv[1]["delta"]))
    return {
        "ready": True,
        "n_early": len(early),
        "n_recent": len(recent),
        "early_span": [early[0][0][:10], early[-1][0][:10]],
        "recent_span": [recent[0][0][:10], recent[-1][0][:10]],
        "axes": dict(sorted(axes.items(), key=lambda kv: -abs(kv[1]["delta"]))),
        "biggest_mover": {"axis": mover_name, **mover},
    }
