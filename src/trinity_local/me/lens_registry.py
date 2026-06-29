"""Lens accumulation registry — the ACCUMULATE layer of the lens redesign.

Turns the lens from *stateless* (every rebuild replaces the surface
tensions) into *accumulating* (a durable registry of tensions; each
rebuild reinforces or extends rather than overwrites). See
``docs/lens-redesign.md`` build-sequence step 1.

Identity is by **embedding cosine** on a tension's probe text — not a
string match on its poles — because the chairman rephrases the same
tension run-to-run. Measured live: a rebuild over an unchanged
49-rejection corpus produced 3 tensions one run, 2 the next, with zero
string overlap but clear semantic rhyme. Cosine identity is what lets
the same tension persist across those rephrasings, which is the whole
point of accretion.

State: ``~/.trinity/me/lens_registry.json`` — one entry per ``tension_id``::

    {tension_id, pole_a, pole_b, failure_a, failure_b, basins_spanned,
     horizon, probe_text, evidence_ids [unioned across rebuilds],
     first_seen, last_confirmed}

Derived at render (NOT stored — a derived field can't drift out of sync
with the evidence, which is the corruption class this session kept
hitting)::

    support_count(t) = len(evidence_ids)
    active(t)        = support_count >= ACTIVE_MIN
                       and (now - last_confirmed) <= RECENCY_DAYS

The registry only ever **unions** evidence and **bumps** last_confirmed
— a fresh extraction can extend a tension but never erase it (an
append-only guarantee: a tension is only added to, never overwritten). Decay is
purely via recency: a tension that stops being confirmed goes inactive
after ``RECENCY_DAYS`` even though its support_count is unchanged. There
is nothing to "decay" actively — support and recency are recomputed from
the stored evidence every render.

Probe embeddings are recomputed from ``probe_text`` at reconcile time
rather than cached on disk: there are only a handful of tensions, so the
cost is trivial, and it sidesteps the stale-embedding class entirely
(when the embedding backend changes — TF-IDF ↔ MLX — cached vectors
become incomparable, but a freshly recomputed one is always in the live
space).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import atomic_write_text, now_iso, stable_id
from .basins import me_dir
from .pair_mining import LensPair, _tension_probe_text

# Cosine threshold for "these two probe texts are the same tension".
# Start at 0.80 per docs/lens-redesign.md risk #2; tune on real rebuild
# cadence. Too high → the same tension splits into duplicates each
# rebuild (no accretion); too low → distinct tensions merge (lost
# resolution). Under the TF-IDF fallback the same threshold is coarser
# (lexical overlap, not semantic) — rephrasings with different words
# won't match, which is acceptable, documented degradation (risk #4).
MATCH_THRESHOLD = 0.80

# A tension is "active" (rendered) once it has at least this much
# distinct evidence. 1 is deliberate: a brand-new user's lens *should*
# surface on the first build (low-confidence is correct, invisible is
# not — cold-start, risk #3). Higher floors belong to a later
# confidence-tiering pass, not the accumulation core.
ACTIVE_MIN = 1

# Recency window (days). A tension not re-confirmed within this many days
# fades to inactive regardless of accumulated support — graceful decay.
RECENCY_DAYS = 90

# Below this much evidence a rendered tension is flagged low-confidence
# (the confidence-honesty pattern: n<3 is too thin to state plainly).
# Distinct from ACTIVE_MIN: a tension can be active-and-rendered yet still
# carry the "seen in few decisions" caveat.
LOW_CONFIDENCE_BELOW = 3

# ─── #256 decay step-2 — recency-weight + robustness override ────────────
#
# The plain recency gate (above) fades ANY tension the latest build didn't
# re-confirm within RECENCY_DAYS. Since the turn-pair window is now
# recency-biased (v1.7.72), a DURABLE tension that simply didn't resurface in
# the recent window would wrongly fade. The founder's rule: "decay old prompts
# so they don't take over, but keep them if they create robust lenses."
#
# Two mechanisms, forward-looking (they don't alter a freshly-built registry,
# only how it decays/ranks over subsequent builds):
#
#  1. Robustness override — a high-support, cross-domain tension stays active
#     past the recency gate. `basins_spanned` is the on-entry durability signal
#     (build timestamps can't carry evidence-time chapter-spread — they're all
#     equal on a fresh build; cross-DOMAIN breadth is the cheap proxy for a
#     cross-cutting, durable tension). Finer evidence-time chapter-spread is a
#     future refinement that needs per-evidence time resolution.
ROBUST_SUPPORT_MIN = 5
ROBUST_BASINS_MIN = 3
#  2. Recency-weighted ranking — support discounted by age, so a stale
#     high-support tension (kept alive by the override) doesn't DOMINATE fresh
#     ones. Half the weight per this many days of staleness.
RANK_HALFLIFE_DAYS = 120


def registry_path() -> Path:
    """``~/.trinity/me/lens_registry.json`` — the durable tension registry."""
    return me_dir() / "lens_registry.json"


# ─── Blast-cap (lens-substrate sequence step 3) ──────────────────────────────
#
# Bounds how far one dream cycle can move the lens, by PROTECTING a drift-stable
# core from recency-decay. The core is seeded once, at the "flush" (the first
# clean rebuild after the de-weight) — seed = (persistent in the old trajectory)
# ∩ (present in the clean rebuild), via `bounded_update.drift_stable_core` (the
# intersection the founder's catch demands: persistence over a CONTAMINATED
# history is a degenerate green; only the intersection is both stable AND clean).
# In this append-only registry "protect" means EXEMPT-FROM-DECAY: a seed tension
# stays `is_active` even past the recency gate, so a later dream can't quietly
# let the founder's durable core fade. Flag-gated (default OFF → behavior
# identical to today); the founder flips it after review. "Auto on next refresh":
# the first build with the flag on computes + persists the seed, then arms.
_BLAST_CAP_FLAG = "TRINITY_LENS_BLAST_CAP"


def blast_cap_enabled() -> bool:
    return os.environ.get(_BLAST_CAP_FLAG) == "1"


def blast_cap_seed_path() -> Path:
    return me_dir() / "blast_cap_seed.json"


def blast_cap_seeded() -> bool:
    """True once the one-time flush has computed + persisted the protected core."""
    p = blast_cap_seed_path()
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return isinstance(data, dict) and bool(data.get("seeded"))
    except (OSError, ValueError):
        return False


def save_blast_cap_seed(protected_ids: list[str]) -> Path:
    """Persist the flush's protected drift-stable-core tension-ids. Marks seeded
    even when the set is empty — the flush HAPPENED; an empty intersection is a
    valid (conservative) outcome, not a reason to re-flush every build."""
    p = blast_cap_seed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        p, json.dumps({"seeded": True, "protected_ids": sorted(set(protected_ids))},
                      indent=2, ensure_ascii=False)
    )
    return p


def protected_seed_ids() -> set[str]:
    """The protected tension-ids (empty if unseeded / unreadable / type-skew)."""
    p = blast_cap_seed_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    ids = data.get("protected_ids")
    return {str(i) for i in ids} if isinstance(ids, list) else set()


def compute_flush_seed(
    clean_probes: list[str],
    persistent_old: list[RegistryEntry],
    *,
    embed_fn,
) -> list[str] | None:
    """The blast-cap flush seed = persistent_old ∩ clean_probes (the drift-stable core).

    Returns the protected tension-ids, OR **None when `clean_probes` is empty** — a degenerate
    rebuild (the chairman produced no candidates this build) must NOT consume the one-time
    flush, else the cap would pin an empty set and never re-flush. The caller persists the seed
    only when this is not None. A NON-empty rebuild whose intersection is empty returns `[]` —
    a valid conservative seed that DOES mark seeded (no persistent tension survived the clean
    rebuild, so there is nothing durable to protect yet)."""
    if not clean_probes:
        return None
    from .bounded_update import drift_stable_core

    seed = drift_stable_core(
        persistent_old, clean_probes, key=lambda e: e.probe_text, embed_fn=embed_fn
    )
    return [e.tension_id for e in seed]


@dataclass
class RegistryEntry:
    """One durable tension. Poles/failures are the *canonical* (first
    registered) phrasing — kept stable across rebuilds so the rendered
    lens doesn't reword itself when the chairman does. Evidence and
    basins accrete; ``last_confirmed`` advances on every match."""

    tension_id: str
    pole_a: str
    pole_b: str
    failure_a: str = ""
    failure_b: str = ""
    basins_spanned: list[str] = field(default_factory=list)
    horizon: str = "tactical"
    probe_text: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_confirmed: str = ""

    @property
    def support_count(self) -> int:
        return len(self.evidence_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tension_id": self.tension_id,
            "pole_a": self.pole_a,
            "pole_b": self.pole_b,
            "failure_a": self.failure_a,
            "failure_b": self.failure_b,
            "basins_spanned": self.basins_spanned,
            "horizon": self.horizon,
            "probe_text": self.probe_text,
            "evidence_ids": self.evidence_ids,
            "first_seen": self.first_seen,
            "last_confirmed": self.last_confirmed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegistryEntry":
        return cls(
            tension_id=d["tension_id"],
            pole_a=d.get("pole_a", ""),
            pole_b=d.get("pole_b", ""),
            failure_a=d.get("failure_a", ""),
            failure_b=d.get("failure_b", ""),
            basins_spanned=list(d.get("basins_spanned", [])),
            horizon=d.get("horizon", "tactical"),
            probe_text=d.get("probe_text", ""),
            evidence_ids=list(d.get("evidence_ids", [])),
            first_seen=d.get("first_seen", ""),
            last_confirmed=d.get("last_confirmed", ""),
        )

    def to_lens_pair(self) -> LensPair:
        """Reconstruct a renderable LensPair from the registry entry, so
        the existing lens.md renderer can stay unchanged — the registry
        carries every field render needs."""
        return LensPair(
            pole_a=self.pole_a,
            pole_b=self.pole_b,
            failure_a=self.failure_a,
            failure_b=self.failure_b,
            basins_spanned=list(self.basins_spanned),
            horizon=self.horizon,
            verdict="accepted",
        )


def _evidence_ids_for(pair: LensPair) -> list[str]:
    """The distinct evidence ids backing a candidate tension: its decision
    ids plus any ids referenced in dual_evidence. Order-stable + deduped."""
    ids: list[str] = list(pair.tension_decisions)
    for vals in (pair.dual_evidence or {}).values():
        if isinstance(vals, list):
            ids.extend(str(v) for v in vals)
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def load_registry() -> list[RegistryEntry]:
    path = registry_path()
    if not path.exists():
        return []
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("tensions", []) if isinstance(data, dict) else []
    out: list[RegistryEntry] = []
    for e in entries:
        if isinstance(e, dict) and e.get("tension_id"):
            out.append(RegistryEntry.from_dict(e))
    return out


def save_registry(entries: list[RegistryEntry], *, allow_shrink: bool = False) -> Path:
    """Persist the tension registry atomically, carrying the #194 clobber
    guard: refuse to overwrite a populated registry with a cliff-drop
    (empty when >= _CLOBBER_MIN_EXISTING tensions exist, or below
    _CLOBBER_MIN_FRACTION of the existing count) — almost always a
    transient empty rebuild, not a real shrink. The live registry is
    preserved and the would-be result is stashed to a `.degenerate`
    sidecar. `allow_shrink=True` is the escape hatch for a genuine
    shrink (a manual prune). The registry is append-only by construction
    (reconcile only unions evidence and bumps recency), so a count
    cliff-drop is a strong corruption signal — the same class #194 caught
    on the rejections corpus."""
    import json

    from .turn_pairs import (
        _CLOBBER_MIN_EXISTING,
        _CLOBBER_MIN_FRACTION,
        DegenerateExtractionError,
    )

    path = registry_path()
    existing = len(load_registry())
    floor = max(1, int(existing * _CLOBBER_MIN_FRACTION))
    if not allow_shrink and existing >= _CLOBBER_MIN_EXISTING and len(entries) < floor:
        sidecar = path.parent / (path.name + ".degenerate")
        try:
            sidecar.write_text(
                json.dumps(
                    {"tensions": [e.to_dict() for e in entries]},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        raise DegenerateExtractionError(
            f"Refusing to overwrite {existing} registry tensions with "
            f"{len(entries)} (cliff-drop below {floor}). Live registry "
            f"preserved; degenerate result written to {sidecar.name}. Pass "
            f"allow_shrink=True only if the registry genuinely shrank."
        )
    payload = {"tensions": [e.to_dict() for e in entries]}
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def _embed_probes(texts: list[str]) -> list[list[float] | None]:
    """Embed probe texts with the live backend. Returns None for any text
    that fails to embed (offline + no fallback) so the caller degrades to
    string matching for that probe rather than crashing the build."""
    from ..embeddings import embed

    out: list[list[float] | None] = []
    for t in texts:
        try:
            out.append(embed(t) if t else None)
        except Exception:
            out.append(None)
    return out


def _best_match(
    cand_emb: list[float] | None,
    cand_probe: str,
    reg_embs: list[list[float] | None],
    registry: list[RegistryEntry],
) -> int | None:
    """Index of the registry entry whose probe is closest to the candidate
    above MATCH_THRESHOLD, or None. Uses embedding cosine when both
    vectors are present and dimension-compatible; otherwise falls back to
    exact probe-text equality (the only string match safe enough to merge
    on — a looser lexical fuzzy-match risks collapsing distinct tensions)."""
    from ..embeddings.backend_tfidf import cosine_similarity

    best_idx: int | None = None
    best_score = MATCH_THRESHOLD
    for idx, (entry, reg_emb) in enumerate(zip(registry, reg_embs)):
        if cand_emb is not None and reg_emb is not None and len(cand_emb) == len(reg_emb):
            score = cosine_similarity(cand_emb, reg_emb)
            if score >= best_score:
                best_score = score
                best_idx = idx
        elif cand_probe and entry.probe_text == cand_probe:
            return idx
    return best_idx


def reconcile(candidates: list[LensPair], *, now: str | None = None) -> list[RegistryEntry]:
    """Stage 4.5 — match this rebuild's accepted candidates against the
    durable registry by cosine identity, accrue their evidence, and bump
    recency. New tensions register fresh. Deterministic; no LLM.

    Canonical phrasing is *first-registered-wins*: a matched candidate's
    own poles/failures are discarded in favor of the registry's, so the
    rendered lens stays stable when the chairman rewords an unchanged
    tension. Only evidence ids, spanned basins, and last_confirmed change
    on a match.

    Returns the full updated registry (persisted as a side effect)."""
    ts = now or now_iso()
    registry = load_registry()
    reg_embs = _embed_probes([e.probe_text for e in registry])

    for cand in candidates:
        probe = _tension_probe_text(cand)
        cand_emb = _embed_probes([probe])[0]
        evidence = _evidence_ids_for(cand)
        tid = stable_id("tension", cand.pole_a, cand.pole_b)
        match_idx = _best_match(cand_emb, probe, reg_embs, registry)
        if match_idx is None:
            # Fallback to the content-addressed id: cosine + exact-probe both
            # missed (embeddings flipped TF-IDF<->MLX across builds, or the
            # failure-mode text was reworded so the probe differs), but the
            # poles are identical. Update that entry instead of appending a
            # second one with the SAME tension_id (which would split support
            # and let active_tensions_sorted return duplicates).
            match_idx = next(
                (i for i, e in enumerate(registry) if e.tension_id == tid), None
            )

        if match_idx is not None:
            entry = registry[match_idx]
            merged = list(entry.evidence_ids)
            seen = set(merged)
            for e in evidence:
                if e not in seen:
                    seen.add(e)
                    merged.append(e)
            entry.evidence_ids = merged
            for b in cand.basins_spanned:
                if b not in entry.basins_spanned:
                    entry.basins_spanned.append(b)
            entry.last_confirmed = ts
        else:
            registry.append(
                RegistryEntry(
                    tension_id=tid,
                    pole_a=cand.pole_a,
                    pole_b=cand.pole_b,
                    failure_a=cand.failure_a,
                    failure_b=cand.failure_b,
                    basins_spanned=list(cand.basins_spanned),
                    horizon=cand.horizon,
                    probe_text=probe,
                    evidence_ids=evidence,
                    first_seen=ts,
                    last_confirmed=ts,
                )
            )
            # Keep the parallel embedding list aligned so a second
            # candidate this run can match the just-registered tension.
            # cand_emb may be None under the TF-IDF fallback / offline embed
            # (documented degradation, risk #4): that just-registered tension
            # is then invisible to later same-run cosine matches, but the
            # tension_id fallback in _best_match still catches exact-pole
            # repeats, so support isn't lost — only same-run merges of
            # reworded duplicates are.
            reg_embs.append(cand_emb)

    save_registry(registry)
    return registry


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_robust(entry: RegistryEntry) -> bool:
    """#256 robustness signal: a high-support tension that recurs across many
    DOMAINS (basins) is a durable, cross-cutting constant of the user's taste
    — not a phase. Such tensions are kept active past the recency gate (they
    just didn't happen to resurface in the latest recency-biased window)."""
    return (
        entry.support_count >= ROBUST_SUPPORT_MIN
        and len(entry.basins_spanned) >= ROBUST_BASINS_MIN
    )


def is_active(entry: RegistryEntry, *, now: str | None = None) -> bool:
    if entry.support_count < ACTIVE_MIN:
        return False
    # Blast-cap: a protected drift-stable-core tension never decays — it's the
    # slow core the founder's flush pinned, exempt from the recency gate below.
    # Flag-gated: with the cap off, `blast_cap_enabled()` short-circuits and
    # is_active is byte-for-byte the prior behavior.
    if blast_cap_enabled() and entry.tension_id in protected_seed_ids():
        return True
    confirmed = _parse_iso(entry.last_confirmed)
    if confirmed is None:
        return False
    now_dt = _parse_iso(now or now_iso()) or datetime.now(timezone.utc)
    if (now_dt - confirmed).days <= RECENCY_DAYS:
        return True
    # #256 chapter-spread override: a durable cross-domain tension survives the
    # recency gate. The recency-weighted ranking below then keeps it from
    # DOMINATING fresher tensions even though it stays visible.
    return is_robust(entry)


def _recency_weighted_support(entry: RegistryEntry, now_dt: datetime) -> float:
    """#256: raw support discounted by staleness — half-weight per
    RANK_HALFLIFE_DAYS since last confirmation. A stale-but-robust tension (kept
    active by the override) ranks below a fresh one of comparable support, so
    'old tensions don't take over' the render order / cold-open."""
    confirmed = _parse_iso(entry.last_confirmed)
    if confirmed is None:
        return 0.0
    age = max(0, (now_dt - confirmed).days)
    return entry.support_count * (0.5 ** (age / RANK_HALFLIFE_DAYS))


def active_tensions_sorted(*, now: str | None = None) -> list[RegistryEntry]:
    """The render view: active tensions, strongest first. 'Strongest' is now
    RECENCY-WEIGHTED support (#256) — dominant *and current* tensions lead, the
    chairman weights them heaviest — so a high-support tension the user has
    moved past doesn't anchor the lens. Ties broken by raw recency. Inactive
    tensions stay in the registry (revivable) but aren't rendered."""
    now_dt = _parse_iso(now or now_iso()) or datetime.now(timezone.utc)
    active = [e for e in load_registry() if is_active(e, now=now)]
    active.sort(
        key=lambda e: (_recency_weighted_support(e, now_dt), e.last_confirmed),
        reverse=True,
    )
    return active


def persistent_registry_tensions(
    entries: list[RegistryEntry] | None = None,
) -> list[RegistryEntry]:
    """The 'persistent in the old trajectory' half of the drift-stable-core intersection
    (the blast-cap seed). A tension is persistent if it was RE-CONFIRMED in a later build
    than the one it first appeared in — ``first_seen < last_confirmed`` — i.e. it survived
    ≥2 builds rather than being a one-off. That excludes transient single-build tensions
    without an arbitrary support threshold, using only stored fields.

    NOTE this is necessary-not-sufficient for protection: over a CONTAMINATED history the
    drivers were re-confirmed every build, so contamination is maximally 'persistent' here.
    Persistence alone is a degenerate green; only its INTERSECTION with the clean rebuild
    (``bounded_update.drift_stable_core``) is safe to protect. This function deliberately
    returns the over-broad persistent set so the intersection can do the narrowing."""
    reg = entries if entries is not None else load_registry()
    return [e for e in reg if e.first_seen and e.last_confirmed and e.first_seen < e.last_confirmed]


def support_index(
    entries: list[RegistryEntry],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (pole_a, pole_b) → accumulation signal for the lens renderer.
    Keyed by canonical poles because that's what `to_lens_pair()` carries
    into render — the renderer looks up support without needing the
    tension_id.

    Two distinct tension_ids can share identical poles (#204-A4); keying by
    poles means the second would silently overwrite the first and the render
    would show the wrong support. On a key collision, keep the entry with the
    higher support_count so the renderer surfaces the strongest one."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for e in entries:
        key = (e.pole_a, e.pole_b)
        prior = out.get(key)
        if prior is not None and prior["support_count"] >= e.support_count:
            continue
        out[key] = {
            "support_count": e.support_count,
            "first_seen": e.first_seen,
            "last_confirmed": e.last_confirmed,
        }
    return out
