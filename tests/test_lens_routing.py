"""Unit tests for lens-basin placement (lens_routing.place_query) and the shared
topics.json reader. Fully synthetic (injected embed_fn), deterministic.

The per-basin routing tally these tests once shared a file with left src/ on
2026-10-26 (the router was removed 2026-08-11); its tests moved with it to
tests/test_frozen_basin_routing.py.
"""
from __future__ import annotations

from trinity_local.lens_routing import place_query
from trinity_local.me import basins as _mb

# Three orthogonal synthetic basins; the embed_fn maps a keyword to that basis
# vector so we control exactly which basin each council lands in.
BASINS = [
    {"id": "b00", "centroid": [1.0, 0.0, 0.0]},  # "api"
    {"id": "b01", "centroid": [0.0, 1.0, 0.0]},  # "refactor"
    {"id": "b02", "centroid": [0.0, 0.0, 1.0]},  # "naming"
]


def _embed(text: str) -> list[float]:
    t = text.lower()
    if "api" in t:
        return [1.0, 0.0, 0.0]
    if "refactor" in t:
        return [0.0, 1.0, 0.0]
    if "naming" in t:
        return [0.0, 0.0, 1.0]
    return [0.0, 0.0, 0.0]  # out-of-domain → no basin


def _c(cid, task, winner, members=3, when="2026-06-01T00:00:00+00:00"):
    return {"council_id": cid, "task_text": task, "winner": winner,
            "substantive_members": members, "created_at": when}


def test_place_query_routes_to_nearest_basin():
    assert place_query("design the api surface", BASINS, _embed) == "b00"
    assert place_query("refactor the auth module", BASINS, _embed) == "b01"
    assert place_query("naming the product", BASINS, _embed) == "b02"



def test_place_query_drops_non_dict_basins():
    """Shape guard (#304 sibling): a corrupt topics.json can hand place_query a
    `basins` list whose ENTRIES are non-dicts; every access is `b.get(...)`.
    Mutation: drop the `[b for b in basins if isinstance(b, dict)]` filter and
    this raises."""
    corrupt = ["b00", 123, None, *BASINS]
    assert place_query("design the api surface", corrupt, _embed) == "b00"
    assert place_query("anything", ["x", 1, None], _embed) is None


def test_place_query_out_of_domain_returns_none():
    # Embeds to the zero vector → no basin clears the match floor → kNN handles it.
    assert place_query("translate this paragraph", BASINS, _embed) is None


def test_place_query_ambiguous_returns_none():
    # Equidistant from b00 and b01 (45°) → top1−top2 below margin → abstain.
    assert place_query("x", BASINS, lambda t: [0.707, 0.707, 0.0]) is None

# ── load_topics_basins: the single shape-guarded topics.json reader ──────────
# Unifies what the removed ask router / consolidate_via_lens_basins /
# launchpad_data._load_topics_basins / milestones._count_basins each open-coded
# (one with no top-level shape guard). A valid-JSON-wrong-shape topics.json must
# degrade to [] for EVERY reader, not crash one and mis-route another.
import json as _json

import pytest as _pytest

from trinity_local.me.basins import load_topics_basins as _load_topics_basins


@_pytest.fixture
def _topics(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))

    monkeypatch.setattr(_mb, "_TOPICS_BASINS_CACHE", None)  # isolate the module cache

    def _write(obj):
        from trinity_local.state_paths import topics_path

        p = topics_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")
        return p

    return _write


def test_load_topics_basins_happy_path(_topics):
    _topics({"basins": [{"id": "b00"}, {"id": "b01"}]})
    assert _load_topics_basins() == [{"id": "b00"}, {"id": "b01"}]


def test_load_topics_basins_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "nope"))

    monkeypatch.setattr(_mb, "_TOPICS_BASINS_CACHE", None)
    assert _load_topics_basins() == []


def test_load_topics_basins_guards_wrong_shapes(_topics):
    # The exact failure ask.py lacked: valid JSON, wrong type → [] not a crash.
    for bad in ('{"basins": "a string"}', '{"basins": 123}', "[1,2,3]", '"just a string"', "not json"):
        _topics(bad)
        assert _load_topics_basins() == [], f"wrong-shape {bad!r} must degrade to []"


def test_load_topics_basins_drops_non_dict_entries(_topics):
    _topics({"basins": [{"id": "b00"}, "b01", 42, None, {"id": "b02"}]})
    assert _load_topics_basins() == [{"id": "b00"}, {"id": "b02"}]
