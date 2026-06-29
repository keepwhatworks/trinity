"""#230 — the #194 clobber guard, propagated to every populated store.

The guard (refuse to overwrite a populated store with an empty / cliff-drop
result, stash a `.degenerate` sidecar, `allow_shrink` escape hatch) originally
lived only on ``me/preference_acts.save_preference_acts``. A degenerate
extraction blip can wipe ANY of these stores just as easily, so each carries
the same backstop now:

  - lens_registry.save_registry
  - basins.save_basins            (also made atomic)
  - pair_mining.save_lenses       (lenses + orderings)
  - cortex.save_routing_patterns  (refuse cliff-drop of populated picks)
  - me_builder.build_me empty-chairman branch (preserve lens.md, don't clobber)

One clobber-guard test per store. All share the threshold semantics from
``me/turn_pairs``: a cliff-drop is empty-when-≥5-exist OR below 25% of the
existing count.
"""
from __future__ import annotations

import pytest

from trinity_local.me.turn_pairs import (
    _CLOBBER_MIN_EXISTING,
    DegenerateExtractionError,
)


# ---------------------------------------------------------------------------
# lens_registry.save_registry
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("patch_trinity_home")
class TestRegistryClobberGuard:
    def _entries(self, n):
        from trinity_local.me.lens_registry import RegistryEntry

        return [
            RegistryEntry(
                tension_id=f"t{i}",
                pole_a=f"a{i}",
                pole_b=f"b{i}",
                evidence_ids=[f"e{i}"],
                first_seen="2026-05-01T00:00:00",
                last_confirmed="2026-05-01T00:00:00",
            )
            for i in range(n)
        ]

    def test_cliff_drop_refused_and_registry_preserved(self):
        from trinity_local.me.lens_registry import (
            load_registry,
            registry_path,
            save_registry,
        )

        save_registry(self._entries(_CLOBBER_MIN_EXISTING + 1), allow_shrink=True)
        with pytest.raises(DegenerateExtractionError):
            save_registry([])
        assert len(load_registry()) == _CLOBBER_MIN_EXISTING + 1
        sidecar = registry_path().parent / (registry_path().name + ".degenerate")
        assert sidecar.exists()

    def test_allow_shrink_escape_hatch(self):
        from trinity_local.me.lens_registry import load_registry, save_registry

        save_registry(self._entries(6), allow_shrink=True)
        save_registry([], allow_shrink=True)
        assert load_registry() == []

    def test_growth_and_cold_start_fine(self):
        from trinity_local.me.lens_registry import load_registry, save_registry

        save_registry([])  # cold start — no guard
        save_registry(self._entries(6), allow_shrink=True)
        save_registry(self._entries(8))  # growth — fine
        assert len(load_registry()) == 8


# ---------------------------------------------------------------------------
# basins.save_basins (+ atomic write)
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("patch_trinity_home")
class TestBasinsClobberGuard:
    def _basins(self, n):
        from trinity_local.me.basins import Basin

        return [
            Basin(id=f"b{i:02d}", size=10, top_terms=[f"term{i}"], centroid=[0.0, 1.0])
            for i in range(n)
        ]

    def test_cliff_drop_refused_and_topology_preserved(self):
        from trinity_local.me.basins import basins_path, load_basins, save_basins

        save_basins(self._basins(_CLOBBER_MIN_EXISTING + 2), allow_shrink=True)
        with pytest.raises(DegenerateExtractionError):
            save_basins([])  # degenerate compute_basins
        assert len(load_basins()) == _CLOBBER_MIN_EXISTING + 2
        sidecar = basins_path().parent / (basins_path().name + ".degenerate")
        assert sidecar.exists()

    def test_allow_shrink_escape_hatch(self):
        from trinity_local.me.basins import load_basins, save_basins

        save_basins(self._basins(8), allow_shrink=True)
        save_basins([], allow_shrink=True)
        assert load_basins() == []

    def test_growth_fine(self):
        from trinity_local.me.basins import load_basins, save_basins

        save_basins(self._basins(6), allow_shrink=True)
        save_basins(self._basins(9))
        assert len(load_basins()) == 9


# ---------------------------------------------------------------------------
# pair_mining.save_lenses (lenses + orderings)
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("patch_trinity_home")
class TestLensesClobberGuard:
    def _pairs(self, n, verdict="accepted"):
        from trinity_local.me.pair_mining import LensPair

        return [
            LensPair(
                pole_a=f"a{i}",
                pole_b=f"b{i}",
                failure_a="fa",
                failure_b="fb",
                verdict=verdict,
            )
            for i in range(n)
        ]

    def test_cliff_drop_on_lenses_refused_and_preserved(self):
        from trinity_local.me.pair_mining import (
            lenses_path,
            load_lenses,
            save_lenses,
        )

        save_lenses(self._pairs(6), [], allow_shrink=True)
        with pytest.raises(DegenerateExtractionError):
            save_lenses([], [])  # empty accepted vs 6 existing
        assert len(load_lenses()) == 6
        sidecar = lenses_path().parent / (lenses_path().name + ".degenerate")
        assert sidecar.exists()

    def test_allow_shrink_escape_hatch(self):
        from trinity_local.me.pair_mining import load_lenses, save_lenses

        save_lenses(self._pairs(6), [], allow_shrink=True)
        save_lenses([], [], allow_shrink=True)
        assert load_lenses() == []

    def test_growth_fine(self):
        from trinity_local.me.pair_mining import load_lenses, save_lenses

        save_lenses(self._pairs(6), [], allow_shrink=True)
        save_lenses(self._pairs(8), [])
        assert len(load_lenses()) == 8


# ---------------------------------------------------------------------------
# cortex.save_routing_patterns (refuse cliff-drop of populated picks)
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("patch_trinity_home")
class TestRoutingPatternsClobberGuard:
    def _patterns(self, n):
        # POST-COLLAPSE (#298): picks are the flat lens-basin tally. The clobber
        # guard is schema-agnostic (it counts entries), so the invariant
        # "refuse a cliff-drop of a populated picks store" holds on plain dicts.
        return {
            f"b{i:02d}": {
                "winner": "claude",
                "count": 10,
                "margin": 0.5,
                "n_episodes": 10,
                "evidence": [],
            }
            for i in range(n)
        }

    def test_cliff_drop_refused_and_picks_preserved(self):
        from trinity_local.cortex import (
            load_routing_patterns,
            save_routing_patterns,
        )
        from trinity_local.state_paths import cortex_routing_patterns_path

        save_routing_patterns(
            self._patterns(_CLOBBER_MIN_EXISTING + 1), allow_shrink=True
        )
        with pytest.raises(DegenerateExtractionError):
            save_routing_patterns({})  # consolidation produced nothing
        assert len(load_routing_patterns()) == _CLOBBER_MIN_EXISTING + 1
        path = cortex_routing_patterns_path()
        assert (path.parent / (path.name + ".degenerate")).exists()

    def test_allow_shrink_escape_hatch(self):
        from trinity_local.cortex import (
            load_routing_patterns,
            save_routing_patterns,
        )

        save_routing_patterns(self._patterns(6), allow_shrink=True)
        save_routing_patterns({}, allow_shrink=True)
        assert load_routing_patterns() == {}


# ---------------------------------------------------------------------------
# me_builder.build_me empty-chairman branch — preserve lens.md, don't clobber
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("patch_trinity_home")
class TestBuildMeEmptyChairmanPreservesLens:
    def test_empty_chairman_output_preserves_existing_me(self, monkeypatch):
        import trinity_local.config as config_mod
        import trinity_local.me_builder as me_builder
        import trinity_local.providers as providers_mod
        import trinity_local.ranker as ranker_mod
        from trinity_local.config import AppConfig, ProviderConfig
        from trinity_local.me_builder import build_me_via_council, me_path

        # Seed a good, populated lens.md on disk.
        good = "# /me\n\nThis is the user's real, hard-won lens.\n"
        p = me_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(good, encoding="utf-8")

        # Force the build to reach the chairman call with samples present.
        monkeypatch.setattr(
            me_builder,
            "_sample_diverse_with_embeddings",
            lambda **k: ["one sample prompt"],
            raising=False,
        )

        prov = ProviderConfig(
            name="claude",
            type="cli",
            enabled=True,
            label="Claude",
            command=["claude"],
            args=[],
            task_types=set(),
            model="claude-opus-4-8",
        )
        cfg = AppConfig(
            max_turns=1,
            notifications=False,
            providers={"claude": prov},
            task_preferences={},
        )
        monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: cfg)
        monkeypatch.setattr(
            ranker_mod, "predict_strongest_chairman", lambda *a, **k: "claude"
        )

        class _Result:
            stdout = ""
            stderr = "boom: model timed out"

        class _Provider:
            def run(self, *a, **k):
                return _Result()

        monkeypatch.setattr(providers_mod, "make_provider", lambda *a, **k: _Provider())

        path, meta = build_me_via_council()

        # The failure marker must NOT have replaced the good lens.
        assert path.read_text(encoding="utf-8") == good
        assert meta.get("validation_failed") is True
        assert "preserved" in (meta.get("note") or "").lower()
