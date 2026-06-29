"""#252: life chapters + the canonical time field.

`prompt_time` reads the true conversation `timestamp` (falling back to ingest
`created_at`), so time-based surfaces see real history instead of the single
month the corpus was indexed. `detect_chapters` is deterministic topic-surge
detection. The real_corpus time-spread guard catches a regression to
`created_at` (the green-check-over-degenerate-data shape — #252/data-sampling).
"""
from __future__ import annotations

import pytest

from trinity_local.me.chapters import detect_chapters, prompt_time


def test_prompt_time_prefers_timestamp():
    # Real conversation time wins over ingest time.
    assert prompt_time({"timestamp": "2023-03-16T10:00:00", "created_at": "2026-05-29"}) \
        == "2023-03-16T10:00:00"


def test_prompt_time_falls_back_to_created_at():
    assert prompt_time({"timestamp": "", "created_at": "2026-05-29"}) == "2026-05-29"
    assert prompt_time({"timestamp": None, "created_at": "2026-05-29"}) == "2026-05-29"
    # too-short timestamp is unusable → fall back
    assert prompt_time({"timestamp": "2026", "created_at": "2026-05-29"}) == "2026-05-29"


def test_prompt_time_accepts_node_objects():
    class N:
        timestamp = "2024-01-02T00:00:00"
        created_at = "2026-05-29"
    assert prompt_time(N()) == "2024-01-02T00:00:00"


def test_detect_chapters_finds_a_surge(monkeypatch):
    # Synthetic corpus: a "reno" basin surges in 2025-06/07, "dev" is steady.
    class Basin:
        def __init__(self, bid, terms, pids):
            self.id = bid
            self.top_terms = terms
            self.prompt_ids = pids

    class Node:
        def __init__(self, nid, month):
            self.id = nid
            self.timestamp = f"{month}-15T00:00:00"
            self.created_at = "2026-05-29"

    reno_ids, dev_ids, nodes = [], [], []
    # steady "dev" background across 10 months — keeps reno a SMALL fraction of
    # the corpus overall (like a real basin ~3-12%), so a local surge can clear
    # the 3x-baseline bar (with only 2 equal basins it never could).
    for month in [f"2025-{mm:02d}" for mm in range(1, 11)]:
        for j in range(100):
            nid = f"dev-{month}-{j}"
            dev_ids.append(nid)
            nodes.append(Node(nid, month))
    # "reno" surge: tiny in May, huge in Jun/Jul, gone Aug
    for month, count in [("2025-05", 2), ("2025-06", 60), ("2025-07", 70), ("2025-08", 1)]:
        for j in range(count):
            nid = f"reno-{month}-{j}"
            reno_ids.append(nid)
            nodes.append(Node(nid, month))

    basins = [Basin("b0", ["dev", "code"], dev_ids), Basin("b1", ["reno", "kitchen"], reno_ids)]
    # detect_chapters imports these locally, so patch them at the source module.
    monkeypatch.setattr("trinity_local.me.basins.load_basins", lambda: basins)
    monkeypatch.setattr(
        "trinity_local.memory.store.iter_prompt_nodes_no_embedding",
        lambda *a, **k: iter(nodes),
    )

    chapters = detect_chapters()
    reno = [c for c in chapters if "reno" in c.label]
    assert reno, f"reno surge should be a chapter; got {[c.label for c in chapters]}"
    c = reno[0]
    assert c.start_month == "2025-06" and c.end_month == "2025-07"
    assert c.peak_month == "2025-07"


class _Basin:
    def __init__(self, terms, bid="b0"):
        self.top_terms = terms
        self.id = bid


def test_basin_label_filters_calendar_noise():
    """Live 2026-05-31: the timeline's first chapter rendered "Tip, November"
    — a month name as a topic theme. _basin_label drops unambiguous temporal
    tokens so the chapter reads as a topic, not a date."""
    from trinity_local.me.chapters import _basin_label
    assert _basin_label(_Basin(["tip", "november", "advice"])) == "tip, advice"
    # weekday + timezone tokens dropped too
    assert _basin_label(_Basin(["deploy", "friday", "gmt"])) == "deploy"
    # all-temporal terms → fall back to the basin id rather than an empty label
    assert _basin_label(_Basin(["november", "december"], bid="b7")) == "b7"


def test_basin_label_keeps_ambiguous_words():
    """'may' / 'march' / 'am' / 'pm' are common non-calendar words — filtering
    them would drop legitimate themes, so they are deliberately kept."""
    from trinity_local.me.chapters import _basin_label
    assert _basin_label(_Basin(["may", "march"])) == "may, march"
    assert _basin_label(_Basin(["am", "pm"])) == "am, pm"


@pytest.mark.real_corpus
class TestCorpusTimeSpread:
    """The corpus carries 38 months of real `timestamp`s. If a time field
    regresses to `created_at` (ingest day), every month collapses into one and
    the timeline/chapters/decay surfaces silently die. Guard the spread."""

    MIN_MONTHS_FOR_LARGE_CORPUS = 6
    LARGE_CORPUS = 5000

    def test_corpus_spans_many_months(self):
        from trinity_local.me.chapters import corpus_month_span
        from trinity_local.memory.store import iter_prompt_nodes_no_embedding

        n = sum(1 for _ in iter_prompt_nodes_no_embedding(limit=None))
        if n < self.LARGE_CORPUS:
            pytest.skip(f"corpus too small ({n}) for a time-spread signal")
        span = corpus_month_span()
        assert span >= self.MIN_MONTHS_FOR_LARGE_CORPUS, (
            f"corpus of {n} prompts spans only {span} month(s) — a time field "
            f"likely regressed to created_at (ingest day), flattening history "
            f"(#252). Time surfaces read prompt_time() = timestamp-or-created_at."
        )
