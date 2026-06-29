"""Guard: the embedding-coverage health check surfaces a silent backfill stall.

#235 (backfill stalled ~May 12 -> 66% of the corpus unembedded) was dangerous
because NOTHING surfaced it — the embedding BACKEND was live and prompts EXISTED,
so `status` stayed green while two-thirds of the corpus had empty vectors and the
lens / basins / semantic search silently ran on a third of the data. The fix at
the time was a re-embed; the *monitor* was never added. This pins it.

Green-gate discipline: the gate is on the INVARIANT (embedded fraction), with a
pre-registered floor (0.70 — catches 66%, passes the healthy 92.7% measured
2026-06-03 whose only gap is the recent-ingest frontier), and the degenerate-data
test below asserts the green is REFUSED (the check carries a `fix` so it surfaces
per the #273 soft-check contract) when coverage falls below the floor.
"""
from __future__ import annotations

import pathlib

import pytest


def _seed_corpus(home: pathlib.Path, embedded: int, empty: int) -> None:
    """Write a synthetic prompt_nodes.jsonl. The check only distinguishes
    `"embedding": []` (empty) from a populated array, so a 1-element array stands
    in for a real 768d vector — keeps fixtures tiny."""
    pdir = home / "prompts"
    pdir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(embedded):
        lines.append(f'{{"id": "e{i}", "embedding": [0.1]}}')
    for i in range(empty):
        lines.append(f'{{"id": "u{i}", "embedding": []}}')
    (pdir / "prompt_nodes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def coverage_check(monkeypatch):
    def run(home):
        monkeypatch.setenv("TRINITY_HOME", str(home))
        from trinity_local.health_checks import _check_embedding_coverage
        return _check_embedding_coverage()
    return run


def test_healthy_corpus_passes_clean(tmp_path, coverage_check):
    _seed_corpus(tmp_path, embedded=950, empty=50)  # 95%
    r = coverage_check(tmp_path)
    assert r.ok and not r.fix, f"healthy 95% corpus should pass clean: {r.to_dict()}"
    assert "95.0% embedded" in r.detail


def test_benign_recency_frontier_passes(tmp_path, coverage_check):
    # 92.7% — the real measured state (gap = recent frontier awaiting backfill).
    _seed_corpus(tmp_path, embedded=927, empty=73)
    r = coverage_check(tmp_path)
    assert r.ok and not r.fix, f"92.7% (benign frontier) must NOT flag: {r.to_dict()}"


def test_stalled_corpus_REFUSES_the_silent_green(tmp_path, coverage_check):
    """The #235 reproduction + the green-gate degenerate-data assertion: a corpus
    below the floor must NOT pass silently — it stays ok=True (self-heals) but MUST
    carry a `fix` so the #273 contract surfaces it."""
    _seed_corpus(tmp_path, embedded=660, empty=340)  # 66% — the #235 number
    r = coverage_check(tmp_path)
    assert r.fix, (
        "a 66%-embedded corpus (the #235 disaster) passed with NO fix — the silent "
        f"backfill-stall gap is back: {r.to_dict()}"
    )
    assert "66.0%" in r.detail and "#235" in r.fix


def test_small_corpus_still_ramping_is_not_flagged(tmp_path, coverage_check):
    # Below the min-nodes gate: early ingestion, don't false-flag.
    _seed_corpus(tmp_path, embedded=100, empty=200)  # 33% but only 300 nodes
    r = coverage_check(tmp_path)
    assert r.ok and not r.fix, f"a small ramping corpus must not flag: {r.to_dict()}"


def test_fresh_install_is_ok(tmp_path, coverage_check):
    r = coverage_check(tmp_path)  # no prompts/ at all
    assert r.ok and not r.fix and "no corpus" in r.detail


def test_floor_is_pre_registered_to_split_235_from_healthy():
    from trinity_local.health_checks import _EMBED_COVERAGE_FLOOR
    assert _EMBED_COVERAGE_FLOOR > 0.66, "floor must catch the #235 66% disaster"
    assert _EMBED_COVERAGE_FLOOR <= 0.927, "floor must pass the healthy 92.7% frontier state"
