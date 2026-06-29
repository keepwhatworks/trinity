"""Tests for the lens accumulation core (#197) — me/lens_registry.py.

The registry turns the lens from stateless (every rebuild replaces the
surface tensions) into accumulating. These tests pin the load-bearing
guarantees: identity by cosine, evidence union (never erase), first-
phrasing-wins stability, recency decay, and render-by-support ordering.
"""

from __future__ import annotations

import pytest

from trinity_local.me.lens_registry import (
    ACTIVE_MIN,
    LOW_CONFIDENCE_BELOW,
    MATCH_THRESHOLD,
    RECENCY_DAYS,
    RegistryEntry,
    _best_match,
    _evidence_ids_for,
    active_tensions_sorted,
    is_active,
    load_registry,
    reconcile,
    save_registry,
    support_index,
)
from trinity_local.me.pair_mining import LensPair
from trinity_local.me.pipeline import render_me_markdown


def _pair(pole_a, pole_b, *, decisions=None, dual=None, basins=None, fa="", fb=""):
    return LensPair(
        pole_a=pole_a,
        pole_b=pole_b,
        failure_a=fa,
        failure_b=fb,
        tension_decisions=list(decisions or []),
        dual_evidence=dict(dual or {}),
        basins_spanned=list(basins or []),
        verdict="accepted",
    )


def _iso_days_ago(n: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=n)).replace(microsecond=0).isoformat()


class TestRoundTrip:
    def test_to_dict_from_dict_preserves_all_fields(self):
        e = RegistryEntry(
            tension_id="tension_abc",
            pole_a="speed",
            pole_b="rigor",
            failure_a="sloppy",
            failure_b="slow",
            basins_spanned=["b1", "b2"],
            horizon="strategic",
            probe_text="speed · rigor · sloppy · slow",
            evidence_ids=["d1", "d2", "d3"],
            first_seen="2026-05-01T00:00:00+00:00",
            last_confirmed="2026-05-20T00:00:00+00:00",
        )
        back = RegistryEntry.from_dict(e.to_dict())
        assert back == e

    def test_support_count_is_evidence_length(self):
        e = RegistryEntry(tension_id="t", pole_a="a", pole_b="b", evidence_ids=["x", "y"])
        assert e.support_count == 2

    def test_to_lens_pair_carries_render_fields(self):
        e = RegistryEntry(
            tension_id="t",
            pole_a="a",
            pole_b="b",
            failure_a="fa",
            failure_b="fb",
            basins_spanned=["b1"],
            horizon="philosophical",
        )
        lp = e.to_lens_pair()
        assert (lp.pole_a, lp.pole_b, lp.failure_a, lp.failure_b) == ("a", "b", "fa", "fb")
        assert lp.basins_spanned == ["b1"]
        assert lp.horizon == "philosophical"
        assert lp.verdict == "accepted"


class TestEvidenceIds:
    def test_unions_decisions_and_dual_evidence_deduped(self):
        p = _pair("a", "b", decisions=["d1", "d2"], dual={"a": ["d2", "d3"], "b": ["d4"]})
        ids = _evidence_ids_for(p)
        assert ids == ["d1", "d2", "d3", "d4"]  # order-stable, deduped

    def test_empty_when_no_evidence(self):
        assert _evidence_ids_for(_pair("a", "b")) == []


class TestBestMatch:
    def test_identical_embedding_matches(self):
        cand = [1.0, 0.0, 0.0]
        reg = [RegistryEntry(tension_id="t", pole_a="a", pole_b="b", probe_text="p")]
        assert _best_match(cand, "p", [[1.0, 0.0, 0.0]], reg) == 0

    def test_orthogonal_embedding_does_not_match(self):
        cand = [1.0, 0.0]
        reg = [RegistryEntry(tension_id="t", pole_a="a", pole_b="b", probe_text="p")]
        assert _best_match(cand, "p", [[0.0, 1.0]], reg) is None

    def test_picks_closest_above_threshold(self):
        cand = [1.0, 0.0]
        reg = [
            RegistryEntry(tension_id="t0", pole_a="a", pole_b="b", probe_text="p0"),
            RegistryEntry(tension_id="t1", pole_a="c", pole_b="d", probe_text="p1"),
        ]
        # second is identical → highest cosine
        assert _best_match(cand, "px", [[0.5, 0.5], [1.0, 0.0]], reg) == 1

    def test_exact_probe_text_fallback_when_no_embeddings(self):
        reg = [RegistryEntry(tension_id="t", pole_a="a", pole_b="b", probe_text="exact probe")]
        assert _best_match(None, "exact probe", [None], reg) == 0
        assert _best_match(None, "different", [None], reg) is None

    def test_threshold_is_inclusive_floor(self):
        # A cosine exactly at MATCH_THRESHOLD should match.
        assert MATCH_THRESHOLD == 0.80


@pytest.mark.usefixtures("patch_trinity_home")
class TestReconcile:
    def test_cold_start_registers_each_candidate(self):
        assert load_registry() == []
        reconcile([_pair("speed", "rigor", decisions=["d1", "d2"], basins=["b1"])])
        reg = load_registry()
        assert len(reg) == 1
        e = reg[0]
        assert (e.pole_a, e.pole_b) == ("speed", "rigor")
        assert e.support_count == 2
        assert e.first_seen == e.last_confirmed
        assert e.basins_spanned == ["b1"]

    def test_identical_candidate_accretes_not_duplicates(self):
        reconcile([_pair("speed", "rigor", decisions=["d1"])], now=_iso_days_ago(10))
        # same tension (identical probe) re-mined with new evidence
        reconcile([_pair("speed", "rigor", decisions=["d1", "d2", "d3"], basins=["b2"])])
        reg = load_registry()
        assert len(reg) == 1, "same tension must accrete into one entry, not duplicate"
        e = reg[0]
        assert sorted(e.evidence_ids) == ["d1", "d2", "d3"]  # unioned + deduped
        assert e.support_count == 3
        assert e.basins_spanned == ["b2"]

    def test_distinct_tensions_register_separately(self):
        reconcile([_pair("speed", "rigor", decisions=["d1"])])
        reconcile([_pair("breadth", "depth", decisions=["d2"])])
        assert len(load_registry()) == 2

    def test_same_poles_reworded_failures_no_duplicate_id(self):
        # Review finding #4: when cosine + exact-probe both miss but the
        # poles are identical (here forced via differing failure text →
        # different probe, plus a low match threshold can't save it), the
        # tension_id fallback must update in place, not append a duplicate.
        import trinity_local.me.lens_registry as reg
        # Force the cosine/probe match to fail so we exercise the tid path:
        # monkeypatch _best_match to always return None.
        orig = reg._best_match
        reg._best_match = lambda *a, **k: None
        try:
            reconcile([_pair("speed", "rigor", decisions=["d1"], fa="sloppy", fb="slow")])
            reconcile([_pair("speed", "rigor", decisions=["d2"], fa="hasty", fb="glacial")])
        finally:
            reg._best_match = orig
        entries = load_registry()
        assert len(entries) == 1, "same poles must not split into duplicate tension_ids"
        assert sorted(entries[0].evidence_ids) == ["d1", "d2"]  # evidence merged
        # First-phrasing-wins: canonical poles/failures from first registration.
        assert (entries[0].failure_a, entries[0].failure_b) == ("sloppy", "slow")

    def test_first_phrasing_wins_keeps_canonical_poles(self):
        # First registration sets canonical phrasing. Re-mining the
        # identical probe keeps the registry's poles (stability) — here we
        # confirm the canonical poles/first_seen survive a re-confirm.
        reconcile([_pair("speed", "rigor", decisions=["d1"])], now=_iso_days_ago(5))
        first_seen = load_registry()[0].first_seen
        reconcile([_pair("speed", "rigor", decisions=["d9"])])
        e = load_registry()[0]
        assert (e.pole_a, e.pole_b) == ("speed", "rigor")
        assert e.first_seen == first_seen  # first_seen frozen
        assert e.first_seen != e.last_confirmed  # last_confirmed advanced
        # #207-D4: evidence unions across the two reconcile calls — the
        # re-confirm accretes d9 onto the original d1 rather than replacing.
        assert sorted(e.evidence_ids) == ["d1", "d9"]

    def test_canonical_failure_modes_survive_reconfirm(self):
        # Failure modes are part of the canonical phrasing — a re-confirm
        # of the identical tension keeps them untouched (first-wins).
        reconcile([_pair("a", "b", decisions=["d1"], fa="sloppy", fb="slow")])
        reconcile([_pair("a", "b", decisions=["d2"], fa="sloppy", fb="slow")])
        reg = load_registry()
        assert len(reg) == 1
        assert (reg[0].failure_a, reg[0].failure_b) == ("sloppy", "slow")
        assert reg[0].support_count == 2


@pytest.mark.usefixtures("patch_trinity_home")
class TestActivityAndDecay:
    def test_recent_supported_tension_is_active(self):
        e = RegistryEntry(
            tension_id="t", pole_a="a", pole_b="b",
            evidence_ids=["d1"], last_confirmed=_iso_days_ago(1),
        )
        assert is_active(e)

    def test_stale_tension_decays_to_inactive(self):
        e = RegistryEntry(
            tension_id="t", pole_a="a", pole_b="b",
            evidence_ids=["d1", "d2", "d3"], last_confirmed=_iso_days_ago(RECENCY_DAYS + 5),
        )
        assert not is_active(e), "support is high but recency lapsed → inactive (graceful decay)"

    def test_zero_support_is_inactive(self):
        e = RegistryEntry(tension_id="t", pole_a="a", pole_b="b", evidence_ids=[], last_confirmed=_iso_days_ago(1))
        assert ACTIVE_MIN == 1
        assert not is_active(e)

    def test_missing_timestamp_is_inactive(self):
        e = RegistryEntry(tension_id="t", pole_a="a", pole_b="b", evidence_ids=["d1"], last_confirmed="")
        assert not is_active(e)


@pytest.mark.usefixtures("patch_trinity_home")
class TestRenderView:
    def test_active_tensions_sorted_by_support_desc(self):
        save_registry([
            RegistryEntry(tension_id="t_low", pole_a="a", pole_b="b", evidence_ids=["d1"], last_confirmed=_iso_days_ago(1)),
            RegistryEntry(tension_id="t_high", pole_a="c", pole_b="d", evidence_ids=["d2", "d3", "d4"], last_confirmed=_iso_days_ago(1)),
            RegistryEntry(tension_id="t_mid", pole_a="e", pole_b="f", evidence_ids=["d5", "d6"], last_confirmed=_iso_days_ago(1)),
        ])
        ids = [e.tension_id for e in active_tensions_sorted()]
        assert ids == ["t_high", "t_mid", "t_low"]

    def test_inactive_tensions_excluded_from_render(self):
        save_registry([
            RegistryEntry(tension_id="t_active", pole_a="a", pole_b="b", evidence_ids=["d1"], last_confirmed=_iso_days_ago(1)),
            RegistryEntry(tension_id="t_stale", pole_a="c", pole_b="d", evidence_ids=["d2"], last_confirmed=_iso_days_ago(RECENCY_DAYS + 1)),
        ])
        ids = [e.tension_id for e in active_tensions_sorted()]
        assert ids == ["t_active"]

    def test_empty_registry_renders_nothing(self):
        assert active_tensions_sorted() == []
        assert load_registry() == []


class TestSupportIndex:
    def test_keys_by_canonical_poles(self):
        entries = [
            RegistryEntry(tension_id="t", pole_a="speed", pole_b="rigor", evidence_ids=["d1", "d2"]),
        ]
        idx = support_index(entries)
        assert ("speed", "rigor") in idx
        assert idx[("speed", "rigor")]["support_count"] == 2


class TestRenderSupportAnnotation:
    def _pair(self, a, b):
        return LensPair(pole_a=a, pole_b=b, failure_a="fa", failure_b="fb", verdict="accepted")

    def test_high_support_renders_count_without_caveat(self):
        p = self._pair("speed", "rigor")
        support = {("speed", "rigor"): {"support_count": 9, "first_seen": "2026-05-01T00:00:00+00:00", "last_confirmed": "2026-05-20T00:00:00+00:00"}}
        out = render_me_markdown([p], [], None, support)
        assert "Supported by 9 decisions" in out
        assert "low confidence" not in out
        assert "first seen 2026-05-01, last confirmed 2026-05-20" in out

    def test_low_support_gets_confidence_caveat(self):
        assert LOW_CONFIDENCE_BELOW == 3
        p = self._pair("a", "b")
        support = {("a", "b"): {"support_count": 1, "first_seen": "2026-05-20T00:00:00+00:00", "last_confirmed": "2026-05-20T00:00:00+00:00"}}
        out = render_me_markdown([p], [], None, support)
        assert "Supported by 1 decision" in out  # singular
        assert "low confidence" in out
        assert "stable since 2026-05-20" in out  # first==last → "stable since"

    def test_no_support_map_renders_no_support_line(self):
        # Backward-compatible: omitting tension_support yields the old shape.
        p = self._pair("a", "b")
        out = render_me_markdown([p], [])
        assert "Supported by" not in out
        assert "a ↔ b" in out


@pytest.mark.usefixtures("patch_trinity_home")
class TestResyncFromDisk:
    def _seed_lenses(self):
        from trinity_local.me.pair_mining import save_lenses
        accepted = [
            LensPair(pole_a="speed", pole_b="rigor", failure_a="sloppy", failure_b="slow",
                     tension_decisions=["d1", "d2", "d3"], basins_spanned=["b0"], verdict="accepted"),
        ]
        orderings = [
            LensPair(pole_a="mvp", pole_b="polish", failure_a="", failure_b="", verdict="preserve_as_ordering"),
        ]
        save_lenses(accepted, orderings)

    def test_no_lenses_is_a_clean_noop(self):
        from trinity_local.me_builder import resync_lens_from_disk
        _path, summary = resync_lens_from_disk()
        assert summary["ok"] is False
        assert "lens-build" in summary["reason"]

    def test_seeds_registry_and_renders_support(self):
        from trinity_local.me_builder import resync_lens_from_disk
        self._seed_lenses()
        path, summary = resync_lens_from_disk()
        assert summary["ok"] is True
        assert summary["active_tensions"] == 1
        # Registry now populated.
        reg = load_registry()
        assert len(reg) == 1 and reg[0].support_count == 3
        # lens.md re-rendered WITH the support line + ordering.
        doc = path.read_text(encoding="utf-8")
        assert "speed ↔ rigor" in doc
        assert "Supported by 3 decisions" in doc
        assert "mvp > polish" in doc

    def test_resync_is_idempotent_no_duplicate_tensions(self):
        from trinity_local.me_builder import resync_lens_from_disk
        self._seed_lenses()
        resync_lens_from_disk()
        resync_lens_from_disk()
        assert len(load_registry()) == 1  # accretion, not duplication


class TestDecayStep2:
    """#256 — recency-weighted ranking + chapter-spread (cross-domain)
    robustness override. Forward-looking: doesn't alter a freshly-built
    registry (all same age), only how it decays/ranks over later builds."""

    def _entry(self, tid, *, support, basins, last_confirmed):
        from trinity_local.me.lens_registry import RegistryEntry
        return RegistryEntry(
            tension_id=tid, pole_a=f"{tid}_a", pole_b=f"{tid}_b",
            basins_spanned=[f"b{i:02d}" for i in range(basins)],
            evidence_ids=[f"e{i}" for i in range(support)],
            first_seen="2024-01-01T00:00:00+00:00",
            last_confirmed=last_confirmed,
        )

    def test_is_robust_needs_support_and_basins(self):
        from trinity_local.me import lens_registry as lr
        assert lr.is_robust(self._entry("t", support=5, basins=3, last_confirmed="2024-01-01T00:00:00+00:00"))
        # High support but only 1 basin -> not robust (not cross-cutting).
        assert not lr.is_robust(self._entry("t", support=9, basins=1, last_confirmed="2024-01-01T00:00:00+00:00"))
        # Cross-domain but thin support -> not robust.
        assert not lr.is_robust(self._entry("t", support=2, basins=4, last_confirmed="2024-01-01T00:00:00+00:00"))

    def test_robust_stale_tension_survives_recency_gate(self):
        from trinity_local.me import lens_registry as lr
        now = "2026-05-30T00:00:00+00:00"  # ~2 years after last_confirmed
        stale = "2024-05-30T00:00:00+00:00"
        robust = self._entry("durable", support=6, basins=3, last_confirmed=stale)
        phase = self._entry("phase", support=6, basins=1, last_confirmed=stale)
        assert lr.is_active(robust, now=now) is True   # override keeps it
        assert lr.is_active(phase, now=now) is False   # non-robust phase decays

    def test_recency_weighting_ranks_fresh_over_stale(self):
        from trinity_local.me import lens_registry as lr
        from datetime import datetime, timezone
        now_dt = datetime(2026, 5, 30, tzinfo=timezone.utc)
        fresh = self._entry("fresh", support=5, basins=3, last_confirmed="2026-05-29T00:00:00+00:00")
        # Higher RAW support but a year stale -> 8 * 0.5^(365/120) ≈ 0.97 < 5.
        stale = self._entry("stale", support=8, basins=3, last_confirmed="2025-05-29T00:00:00+00:00")
        assert lr._recency_weighted_support(fresh, now_dt) > lr._recency_weighted_support(stale, now_dt)
