"""The scoped lens read ships dormant, and defers on every degradation.

Today a council is handed the first six of 167 tensions, ranked globally and
independent of the question. `scope_for_query` selects by the query's own basin
instead, and it was measured BEFORE it was built: hq_078 permuted the basin
label and the payload changed 96.7% against a pre-registered 25pp bar over 109
councils, so the basin is doing the selecting rather than churning context.

What is NOT measured is whether a chairman reading scoped tensions decides
differently, against a consumer measured at 1/12 causal. So it ships flag-gated
OFF and every failure path returns the global slice. These tests pin that: the
interesting assertion is not that scoping works, it is that scoping REFUSES.
"""

from __future__ import annotations

import json

import pytest

from trinity_local import lens_routing
from trinity_local.lens_routing import scope_for_query

FLAG = "TRINITY_DAG_SCOPED_LENS"


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


def _seed(home, *, basins=True, tensions=True):
    (home / "me").mkdir(parents=True, exist_ok=True)
    (home / "memories").mkdir(parents=True, exist_ok=True)
    if tensions:
        (home / "me" / "lens_registry.json").write_text(json.dumps({"tensions": [
            {"tension_id": "t1", "pole_a": "shipped", "pole_b": "described",
             "basins_spanned": ["b00"]},
            {"tension_id": "t2", "pole_a": "measured", "pole_b": "asserted",
             "basins_spanned": ["b01"]},
            {"tension_id": "t3", "pole_a": "refused", "pole_b": "guessed",
             "basins_spanned": ["b00"]},
        ]}))
    if basins:
        (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
            {"id": "b00", "centroid": [1.0, 0.0], "prompt_ids": []},
            {"id": "b01", "centroid": [0.0, 1.0], "prompt_ids": []},
        ]}))


def _make_it_capable(monkeypatch, tmp_path):
    """Arrange a world where scoping WOULD fire, so the flag is the only variable.

    Without this the dormancy test passes for the wrong reason: the test
    environment has no real embedder, `require_real_embedder` aborts first, and
    [] comes back whatever the flag says. Flipping the default to ON then
    survives the guard — measured, not hypothetical. A refusal test has to prove
    the other answer was REACHABLE.
    """
    from trinity_local import embeddings

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed(tmp_path)
    monkeypatch.setattr(embeddings, "require_real_embedder", lambda: None)
    monkeypatch.setattr(lens_routing, "place_query", lambda *a, **k: "b00")


class TestItShipsDormant:
    def test_the_setup_can_actually_fire(self, armed, monkeypatch, tmp_path):
        """The positive control. If this ever goes empty, the test below is void."""
        _make_it_capable(monkeypatch, tmp_path)
        assert scope_for_query("q", embed_fn=lambda _t: [1.0, 0.0]), (
            "the capable fixture produced no scope — the dormancy test beneath "
            "it would then pass vacuously"
        )

    def test_flag_off_returns_nothing_so_the_caller_keeps_the_global_slice(
            self, monkeypatch, tmp_path):
        _make_it_capable(monkeypatch, tmp_path)
        monkeypatch.delenv(FLAG, raising=False)
        assert scope_for_query("q", embed_fn=lambda _t: [1.0, 0.0]) == []

    def test_the_flag_is_not_armed_anywhere_in_shipped_config(self):
        """Dormant means dormant — an installer that sets it is not dormant."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for sub in ("src", "scripts"):
            for f in (root / sub).rglob("*"):
                if f.suffix not in {".py", ".sh", ".json", ".toml"} or not f.is_file():
                    continue
                t = f.read_text(errors="replace")
                if f".setenv(\"{FLAG}\"" in t or f'{FLAG}=1' in t or f'{FLAG}="1"' in t:
                    raise AssertionError(f"{f} arms the dormant flag")


class TestItRefusesRatherThanGuessing:
    """Every one of these must yield the GLOBAL slice, never a wrong scope."""

    def test_empty_query(self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        assert scope_for_query("   ") == []

    def test_no_registry(self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed(tmp_path, tensions=False)
        assert scope_for_query("a real question") == []

    def test_no_basins(self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed(tmp_path, basins=False)
        assert scope_for_query("a real question") == []

    def test_unplaceable_query_is_not_forced_into_a_basin(
            self, armed, monkeypatch, tmp_path):
        """place_query returning None means out-of-domain or ambiguous.

        Scoping on a placement the router itself refused would be exactly the
        stale-orthogonal-space failure that killed the cortex engine.
        """
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        monkeypatch.setattr(lens_routing, "place_query", lambda *a, **k: None)
        assert scope_for_query("q", embed_fn=lambda _t: [1.0, 0.0]) == []

    def test_a_basin_with_no_entitled_tensions_defers(
            self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        monkeypatch.setattr(lens_routing, "place_query", lambda *a, **k: "b99")
        assert scope_for_query("q", embed_fn=lambda _t: [1.0, 0.0]) == []

    def test_it_abstains_under_the_tfidf_stub_embedder(
            self, armed, monkeypatch, tmp_path):
        """Placement is semantic; under SHA-1 TF-IDF the nearest centroid is noise.

        This repo's rule is that such flows abstain rather than ship an
        inverted-TF-IDF answer, and a scope chosen by noise is worse than the
        global slice because it looks personalised.
        """
        from trinity_local import embeddings

        _make_it_capable(monkeypatch, tmp_path)

        def _stub():
            raise RuntimeError("needs real embeddings")

        monkeypatch.setattr(embeddings, "require_real_embedder", _stub)
        assert scope_for_query("q", embed_fn=lambda _t: [1.0, 0.0]) == []

    def test_zero_k_is_not_treated_as_unbounded(self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        assert scope_for_query("q", 0) == []


class TestWhenItDoesFire:
    def test_it_returns_only_that_basins_tensions_in_registry_order(
            self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        monkeypatch.setattr(lens_routing, "place_query", lambda *a, **k: "b00")
        got = scope_for_query("q", 6, embed_fn=lambda _t: [1.0, 0.0])
        # t2 belongs to b01 and must not appear; order follows the registry,
        # because re-ranking here would be the ranking layer amd_0169 killed.
        assert got == [("shipped", "described"), ("refused", "guessed")]

    def test_k_bounds_the_payload(self, armed, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        monkeypatch.setattr(lens_routing, "place_query", lambda *a, **k: "b00")
        assert len(scope_for_query("q", 1, embed_fn=lambda _t: [1.0, 0.0])) == 1

    def test_it_writes_nothing_back_into_the_lens(
            self, armed, monkeypatch, tmp_path):
        """Founder-lock #1: the lens learns from transcripts only.

        Scoped to LENS ARTIFACTS, not to every byte under TRINITY_HOME: the
        state layer stamps a SCHEMA_VERSION marker on first access, which the
        global-slice path writes too, so it is not attributable to scoping.
        The lock is about lens CONTENT, and that is what this asserts.
        """
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path)); _seed(tmp_path)
        monkeypatch.setattr(lens_routing, "place_query", lambda *a, **k: "b00")

        def lens_state():
            return {f: f.read_bytes() for f in tmp_path.rglob("*")
                    if f.is_file() and (
                        "me" in f.parts or "memories" in f.parts or f.name == "core.md")}

        before = lens_state()
        assert before, "fixture wrote no lens artifacts — the test would pass vacuously"
        scope_for_query("q", embed_fn=lambda _t: [1.0, 0.0])
        assert lens_state() == before, "a read-side scope must not mutate lens state"


class TestBothCallSitesUseIt:
    def test_no_unrouted_tension_slice_remains(self):
        """One slice fixed and one left behind is this repo's signature bug.

        There were two `[:6]` sites — the chairman and the (dormant) member
        prompt — and three council status pollers before them.
        """
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "src" / "trinity_local" / "council_runtime.py").read_text()
        raw = src.count("_TENSION_HEADING.findall(lens_md)[:6]")
        routed = src.count("scope_for_query(")
        assert raw == routed == 2, (
            f"{raw} raw slices vs {routed} scoped reads — every tension slice "
            "must route through scope_for_query or the two sites will drift."
        )
