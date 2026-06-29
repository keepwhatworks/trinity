"""#252: Stage-0 turn-pair extraction is recency-biased + diverse.

The extractor iterates the corpus chronologically; passing the `limit` straight
through took the OLDEST pairs and froze the lens on early history (a 2023
IDE-era workaround stayed the #1 tension for 14 months). The fix samples
recent-first, capped per month so no single burst fills the window.
"""
from __future__ import annotations

import collections


def test_collect_turn_pairs_is_recent_and_diverse(monkeypatch):
    import trinity_local.me.pipeline as pipe

    # 25 months of pairs, 30/month, chronological (oldest first) — like the real
    # iter_turn_pairs output.
    months = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01"]
    # Substantive user text so each pair's thread clears the #269 seed-signal
    # floor — this test exercises recency + per-month diversity, not the seed
    # gate (which has its own tests), so the gate must be a no-op here.
    substantive = "a real substantive user prompt with genuine content " * 30
    pairs = []
    for mo in months:
        for j in range(30):
            pid = f"{mo}-{j}"
            pairs.append(("assistant text " * 5, substantive, pid, ""))

    class Node:
        def __init__(self, pid, mo):
            self.id = pid
            self.transcript_id = pid  # each pair its own high-signal thread
            self.text = substantive
            self.timestamp = f"{mo}-15T00:00:00"
            self.created_at = "2026-02-01"

    nodes = [Node(p[2], p[2][:7]) for p in pairs]

    monkeypatch.setattr(pipe, "iter_turn_pairs", lambda limit=None: iter(pairs))
    monkeypatch.setattr(
        "trinity_local.memory.store.iter_prompt_nodes_no_embedding",
        lambda *a, **k: iter(nodes),
    )

    selected, index = pipe.collect_turn_pairs(limit=200)
    assert len(selected) == 200
    by_month = collections.Counter(pid["prompt_id"][:7] for pid in selected)

    # Recency: the newest month is present; the OLDEST months are NOT.
    assert "2026-01" in by_month, "must include the most recent month"
    assert "2024-01" not in by_month, "must not freeze on the oldest month"
    assert "2024-02" not in by_month

    # Diversity: per-month cap (~limit/10=20) means no single month dominates.
    assert max(by_month.values()) <= 20, f"a month exceeded the cap: {by_month}"
    # Spread across ~10 recent months, not one burst.
    assert len(by_month) >= 8, f"expected a diverse recent spread, got {by_month}"


def test_collect_turn_pairs_small_corpus_unchanged(monkeypatch):
    # Below the limit, all pairs flow through (no sampling).
    import trinity_local.me.pipeline as pipe
    pairs = [("a" * 30, f"user {i}", f"p{i}", "") for i in range(10)]
    monkeypatch.setattr(pipe, "iter_turn_pairs", lambda limit=None: iter(pairs))
    selected, _ = pipe.collect_turn_pairs(limit=200)
    assert len(selected) == 10
