"""Blast-cap (lens-substrate step 3): protect the drift-stable core from recency-decay.

The seed = (persistent in old trajectory) ∩ (present in clean rebuild) is computed at the
one-time flush (tested in test_drift_stable_core). Here we test the REGISTRY-side mechanism:
a seeded tension is exempt from recency-decay in `is_active`, and ONLY when the flag is on.
Flag off → byte-for-byte the prior is_active behavior (the safe default).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trinity_local.me import lens_registry as lr
from trinity_local.me.lens_registry import RegistryEntry


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_LENS_BLAST_CAP", raising=False)
    return tmp_path


def _stale_entry(tid: str) -> RegistryEntry:
    """A tension last confirmed well past the recency window, low support + no basin
    spread → would decay to inactive (not robust)."""
    old = (datetime.now(timezone.utc) - timedelta(days=lr.RECENCY_DAYS + 30)).isoformat()
    return RegistryEntry(tension_id=tid, pole_a="a", pole_b="b", probe_text=f"probe {tid}",
                         evidence_ids=["e1"], first_seen=old, last_confirmed=old)


# ── seed persistence ──────────────────────────────────────────────────────────


def test_seed_roundtrip_and_seeded_marker(home):
    assert lr.blast_cap_seeded() is False
    assert lr.protected_seed_ids() == set()
    lr.save_blast_cap_seed(["t1", "t2", "t1"])  # dedups
    assert lr.blast_cap_seeded() is True
    assert lr.protected_seed_ids() == {"t1", "t2"}


def test_empty_seed_still_marks_seeded(home):
    """An empty intersection is a valid (conservative) flush outcome — mark seeded so the
    flush doesn't re-fire every build."""
    lr.save_blast_cap_seed([])
    assert lr.blast_cap_seeded() is True
    assert lr.protected_seed_ids() == set()


def test_corrupt_or_wrong_type_seed_is_safe(home):
    p = lr.blast_cap_seed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json", encoding="utf-8")
    assert lr.protected_seed_ids() == set() and lr.blast_cap_seeded() is False
    p.write_text('["a","b"]', encoding="utf-8")  # valid JSON, wrong type (list not dict)
    assert lr.protected_seed_ids() == set() and lr.blast_cap_seeded() is False


# ── the decay-exemption (flag-gated) ──────────────────────────────────────────


def test_flag_off_no_exemption_even_with_seed(home, monkeypatch):
    """The safe default: with the cap off, a seeded-but-stale tension still decays — is_active
    is unchanged from today."""
    lr.save_blast_cap_seed(["t1"])
    # flag NOT set
    assert lr.is_active(_stale_entry("t1")) is False


def test_flag_on_protects_only_seeded_tensions(home, monkeypatch):
    monkeypatch.setenv("TRINITY_LENS_BLAST_CAP", "1")
    lr.save_blast_cap_seed(["t1"])
    assert lr.is_active(_stale_entry("t1")) is True   # seeded → exempt from recency decay
    assert lr.is_active(_stale_entry("t2")) is False  # not seeded → decays as before


def test_protected_tension_still_needs_support(home, monkeypatch):
    """Exemption is from the RECENCY gate, not the support floor — a zero-evidence tension is
    never active, seeded or not (the cap protects real tensions, not phantoms)."""
    monkeypatch.setenv("TRINITY_LENS_BLAST_CAP", "1")
    lr.save_blast_cap_seed(["t1"])
    no_support = RegistryEntry(tension_id="t1", pole_a="a", pole_b="b", probe_text="p",
                               evidence_ids=[], first_seen="2026-01-01T00:00:00",
                               last_confirmed="2026-01-01T00:00:00")
    assert lr.is_active(no_support) is False


# ── compute_flush_seed: the one-time flush must not be consumed by a degenerate rebuild ──

_FB: dict[str, list[float]] = {}


def _concept_embed(text: str) -> list[float]:
    c = (text.split() or [""])[0]
    if c not in _FB:
        v = [0.0] * 32
        v[len(_FB)] = 1.0
        _FB[c] = v
    return _FB[c]


def _persist(tid: str, probe: str) -> RegistryEntry:
    return RegistryEntry(tension_id=tid, pole_a="a", pole_b="b", probe_text=probe,
                         first_seen="2026-01-01T00:00:00", last_confirmed="2026-03-01T00:00:00")


def test_compute_flush_seed_degenerate_rebuild_returns_none():
    """THE FIX: an empty rebuild (chairman produced no candidates) returns None, so the caller
    does NOT persist a seed — the one-time flush isn't consumed, and the next build retries.
    Without this the cap would pin an empty set and never re-flush."""
    assert lr.compute_flush_seed([], [_persist("t1", "ships first")], embed_fn=_concept_embed) is None


def test_compute_flush_seed_matching_intersection_pins_ids():
    seed = lr.compute_flush_seed(
        ["ships reworded by the chairman"],
        [_persist("t1", "ships first wording"), _persist("t2", "autonomy axis")],
        embed_fn=_concept_embed,
    )
    assert seed == ["t1"]  # 'ships' matches; 'autonomy' doesn't


def test_compute_flush_seed_empty_intersection_still_seeds():
    """A REAL rebuild whose intersection is empty returns [] (not None) — a valid conservative
    seed that DOES mark seeded; nothing durable to protect yet, but the flush happened."""
    seed = lr.compute_flush_seed(
        ["brandnew unrelated topic"], [_persist("t1", "ships first")], embed_fn=_concept_embed
    )
    assert seed == []  # not None → caller marks seeded


def test_fresh_tension_unaffected_by_flag(home, monkeypatch):
    """A recently-confirmed tension is active regardless of the cap — the exemption only adds
    survivors, never removes them."""
    monkeypatch.setenv("TRINITY_LENS_BLAST_CAP", "1")
    fresh = RegistryEntry(tension_id="t9", pole_a="a", pole_b="b", probe_text="p",
                          evidence_ids=["e1"],
                          first_seen=datetime.now(timezone.utc).isoformat(),
                          last_confirmed=datetime.now(timezone.utc).isoformat())
    assert lr.is_active(fresh) is True


# ── the clean-probe set must be ACTIVE, not the mined candidates (2026-06-04 pin-0 bug) ──


def test_flush_intersects_active_not_mined_candidates():
    """REGRESSION: arming the cap on the real corpus pinned 0 because the flush intersected
    persistent_old against the freshly-MINED `accepted` candidates. A durable axis is an
    already-active tension (re-confirmed across builds, never re-proposed as a NEW candidate),
    so that intersection is structurally near-empty. The clean-probe set must be the rebuilt
    ACTIVE tensions — the ones the clean rebuild KEEPS."""
    persistent = [_persist("t1", "executable artifact over explanatory description")]
    # Durable axis SURVIVES into the active set (kept by the clean rebuild) → pinned.
    active_probes = ["executable artifact over explanatory description", "another live tension"]
    assert lr.compute_flush_seed(active_probes, persistent, embed_fn=_concept_embed) == ["t1"]
    # The freshly-mined candidates are NEW pairs that don't include the durable axis → 0.
    candidate_probes = ["brandnew mined candidate", "another fresh candidate"]
    assert lr.compute_flush_seed(candidate_probes, persistent, embed_fn=_concept_embed) == []


def test_me_builder_flush_wires_active_set_not_accepted():
    """Wiring guard (mutation-resistant: asserts the correct pattern present AND the buggy
    one absent). me_builder's flush must derive clean_probes from the rebuilt `active` set,
    never from `accepted`. A substring-present-only check would survive a partial revert that
    left dead code, so we also assert the old buggy expression is gone."""
    import pathlib

    import trinity_local.me_builder as mb

    src = pathlib.Path(mb.__file__).read_text(encoding="utf-8")
    assert "clean_probes = [e.probe_text for e in active]" in src
    assert "_tension_probe_text(p) for p in accepted" not in src
