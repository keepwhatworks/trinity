"""Corpus-milestone fire-once behavior + defensive stat reads."""
from __future__ import annotations

import json

import pytest

from trinity_local import milestones as M


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _seed_councils(home, n):
    d = home / "council_outcomes"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"c{i}.json").write_text("{}", encoding="utf-8")


def _seed_prompts(home, n):
    d = home / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prompt_nodes.jsonl").write_text("".join("{}\n" for _ in range(n)), encoding="utf-8")


def test_empty_home_has_no_milestone(_home):
    assert M.compute_corpus_stats() == {"councils": 0, "prompts": 0, "basins": 0}
    assert M.pending_milestone() is None


def test_threshold_crossing_then_fire_once(_home):
    _seed_councils(_home, 10)
    m = M.surface_milestone()
    assert m is not None and m.metric == "councils" and m.threshold == 10
    # Marked → never re-shows for that metric.
    assert M.surface_milestone() is None
    assert M.pending_milestone() is None


def test_below_threshold_stays_quiet(_home):
    _seed_councils(_home, 9)  # below the first council threshold (10) but >= 1
    # 9 crosses the "1" threshold (first council) but not 10 — so the 1st-council
    # milestone is the live one until it's celebrated.
    m = M.surface_milestone()
    assert m is not None and m.threshold == 1
    assert M.surface_milestone() is None


def test_one_metric_per_call_then_next(_home):
    _seed_councils(_home, 10)
    _seed_prompts(_home, 120)
    first = M.surface_milestone()
    second = M.surface_milestone()
    assert {first.metric, second.metric} == {"councils", "prompts"}
    assert M.surface_milestone() is None  # both marked


def test_big_jump_celebrates_highest_crossed(_home):
    _seed_councils(_home, 300)  # crosses 1,10,25,50,100,250
    m = M.surface_milestone()
    assert m.threshold == 250  # the biggest crossed, celebrated once


def test_corrupt_marker_does_not_crash(_home):
    M.milestones_marker_path().write_text("{not json", encoding="utf-8")
    _seed_councils(_home, 10)
    # A garbage marker is treated as "nothing celebrated yet", never raises.
    assert M.surface_milestone().threshold == 10


def test_corrupt_topics_json_yields_zero_basins(_home):
    (_home / "memories").mkdir(parents=True, exist_ok=True)
    M_topics = _home / "memories" / "topics.json"
    M_topics.write_text("{not json", encoding="utf-8")
    assert M.compute_corpus_stats()["basins"] == 0


def test_marker_is_valid_json(_home):
    _seed_councils(_home, 10)
    M.surface_milestone()
    data = json.loads(M.milestones_marker_path().read_text(encoding="utf-8"))
    assert data.get("councils") == 10
