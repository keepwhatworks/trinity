"""Drift-stable core = (persistent in old trajectory) ∩ (present in clean rebuild).

The blast-cap's protected seed (founder spec 2026-06-03). Persistence ALONE is a degenerate
green: over a contaminated history the drivers ("continue"/"status") were re-confirmed every
build, so a contamination-derived tension is maximally persistent — protect by persistence
and you freeze the most stable dirt. The intersection with the CLEAN rebuild excludes stable
contamination (gone once drivers are de-weighted); persistence excludes transient clean-noise.

The load-bearing tests: the founder's full failure-mode table, plus
`test_conservative_threshold_excludes_coincidental_rhyme` — the guarantee that the
intersection "can only err conservative" depends on the protect-match floor leaning toward
NON-match (stricter than accretion's 0.80).
"""
from __future__ import annotations

import math

from trinity_local.me.bounded_update import drift_stable_core, _DRIFT_STABLE_MATCH_MIN
from trinity_local.me.lens_registry import RegistryEntry, persistent_registry_tensions


# ── concept embedder: leading token = concept; same concept cos 1.0, different 0.0 ──
_BASIS: dict[str, list[float]] = {}


def concept_embed(text: str) -> list[float]:
    c = (text.split() or [""])[0]
    if c not in _BASIS:
        v = [0.0] * 64
        v[len(_BASIS)] = 1.0
        _BASIS[c] = v
    return _BASIS[c]


def dsc(persistent, clean, **kw):
    return drift_stable_core(persistent, clean, key=lambda s: s, embed_fn=concept_embed, **kw)


# ── the founder's failure-mode table ──────────────────────────────────────────


def test_contamination_persistent_old_but_absent_clean_is_excluded():
    """A contamination tension is persistent-old but the clean rebuild (drivers de-weighted)
    no longer produces it → no clean match → EXCLUDED. The whole point."""
    persistent = ["driver tension from continue/status repetition"]
    clean = ["ships tension only trusts what shipped", "action tension prefers executable"]
    assert dsc(persistent, clean) == []


def test_real_taste_in_both_is_protected():
    persistent = ["ships first-registered wording"]
    clean = ["ships reworded by the chairman this build"]  # same concept ('ships')
    assert dsc(persistent, clean) == ["ships first-registered wording"]


def test_clean_only_noise_cannot_be_protected():
    """Clean-rebuild noise that was never persistent isn't in persistent_old, so it can never
    be returned — protection only ever flows from the persistent set."""
    persistent = ["ships tension"]
    clean = ["ships tension", "transient one-off clean noise"]
    assert dsc(persistent, clean) == ["ships tension"]


def test_new_tension_not_yet_persistent_is_not_protected_but_not_deleted():
    """A genuinely new clean tension isn't persistent yet → not in the seed. unprotected ≠
    deleted: it's simply absent from persistent_old, so drift_stable_core never names it;
    it survives as a normal tension and accretes forward as it persists."""
    persistent = ["ships tension"]
    clean = ["brand new tension this build", "ships tension"]
    out = dsc(persistent, clean)
    assert out == ["ships tension"]
    assert "brand new tension this build" not in out


# ── the conservative-threshold guarantee (the refinement on the spec) ──────────


def _at_cosine(target: float) -> list[float]:
    return [target, math.sqrt(max(0.0, 1.0 - target * target))]


def angle_embed_factory():
    """Map two specific probes to unit vectors at a chosen cosine, everything else orthogonal."""
    table = {"PERSIST": [1.0, 0.0]}

    def embed(text: str) -> list[float]:
        return table.get(text, [0.0, 0.0, 1.0])

    return table, embed


def test_conservative_threshold_excludes_coincidental_rhyme():
    """A persistent-old tension that only COINCIDENTALLY rhymes with a clean candidate
    (cosine below the strict floor) must NOT be protected — erring conservative. A loose
    floor (accretion's 0.80) would wrongly protect contamination on coincidental rhyme."""
    table, embed = angle_embed_factory()
    # just BELOW the floor → excluded
    table["CLEAN"] = _at_cosine(_DRIFT_STABLE_MATCH_MIN - 0.03)
    assert drift_stable_core(["PERSIST"], ["CLEAN"], key=lambda s: s, embed_fn=embed) == []
    # at/above the floor → protected
    table["CLEAN"] = _at_cosine(_DRIFT_STABLE_MATCH_MIN + 0.03)
    assert drift_stable_core(["PERSIST"], ["CLEAN"], key=lambda s: s, embed_fn=embed) == ["PERSIST"]


def test_drift_stable_floor_is_stricter_than_accretion():
    """Documents the relationship the conservative guarantee rests on."""
    from trinity_local.me.lens_registry import MATCH_THRESHOLD
    assert _DRIFT_STABLE_MATCH_MIN > MATCH_THRESHOLD


def test_empty_either_side_is_empty():
    assert dsc([], ["x"]) == []
    assert dsc(["x"], []) == []


# ── persistent_registry_tensions: re-confirmed across builds, not one-off ──────


def _entry(tid, first, last):
    return RegistryEntry(tension_id=tid, pole_a="a", pole_b="b", probe_text=f"probe {tid}",
                         first_seen=first, last_confirmed=last)


def test_persistent_requires_reconfirmation_in_a_later_build():
    reg = [
        _entry("multi", "2026-01-01T00:00:00", "2026-03-01T00:00:00"),  # re-confirmed later → persistent
        _entry("once", "2026-02-01T00:00:00", "2026-02-01T00:00:00"),   # first==last → one-off, NOT persistent
        _entry("blank", "", ""),                                         # no timestamps → NOT persistent
    ]
    got = [e.tension_id for e in persistent_registry_tensions(reg)]
    assert got == ["multi"]
