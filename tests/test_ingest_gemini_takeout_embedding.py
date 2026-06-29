"""Tests for embedding-based Gemini Takeout multi-turn reconstruction.

Task #107 — the previous time-proximity-only grouper (v="2") didn't
reconstruct threads correctly per user report 2026-05-22. Three failure
modes the v="3" path must handle:

1. Cells with same topic separated by >30min gap → cluster together
   (v="2" wrongly splits).
2. Cells with different topics inside a short window → cluster separately
   (v="2" wrongly merges).
3. Cells with missing timestamps → don't fragment.

The v="3" path is the new default in ``parse_gemini_takeout_html``; the
v="2" path is preserved behind ``use_embedding_grouping=False`` for
back-compat / TF-IDF fallback / embed-less environments.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from trinity_local.embeddings import mlx_actually_loaded
from trinity_local.ingest import (
    GEMINI_TAKEOUT_EMBED_MAX_WINDOW_SECONDS,
    GEMINI_TAKEOUT_EMBED_SIMILARITY,
    _group_cells_by_embedding,
    _group_cells_into_sessions,
    parse_gemini_takeout_html,
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic cells the same shape _parse_gemini_takeout_cells
# emits, so we can drive the grouper directly without round-tripping HTML.
# ---------------------------------------------------------------------------


def _cell(prompt: str, response: str, ts: datetime | None, native_id: str) -> dict:
    return {
        "prompt": prompt,
        "response": response,
        "ts_iso": ts.isoformat() if ts is not None else None,
        "native_id": native_id,
    }


# Two corpora that should embed clearly differently under nomic-768d.
# "Same-topic" cells share lots of overlapping content vocabulary
# (rust+memory+lifetime), "different-topic" pivots to recipe text.
SAME_TOPIC_TURNS = [
    (
        "Explain Rust's borrow checker and how lifetimes propagate through "
        "function signatures with multiple references.",
        "The borrow checker tracks ownership at compile time. Each value has "
        "exactly one owner; references are borrowed loans tracked with lifetimes.",
    ),
    (
        "How does the borrow checker handle nested function calls when "
        "references with different lifetimes are passed through?",
        "Lifetime elision rules cover most function signatures, but when "
        "multiple input references exist the compiler requires explicit "
        "lifetime annotations to map outputs to their borrowed source.",
    ),
    (
        "Show an example of a function signature where lifetime elision "
        "fails and explicit annotations like 'a become required.",
        "fn longest<'a>(x: &'a str, y: &'a str) -> &'a str { ... } — the "
        "compiler can't infer which input's lifetime the output is bound to.",
    ),
]

DIFFERENT_TOPIC_TURN = (
    "What's a good chocolate chip cookie recipe with crispy edges and "
    "chewy centers? I want bakery-style not soft.",
    "Use brown butter, more brown sugar than white, chill the dough 24h, "
    "and bake at 375F until edges are deep golden.",
)


# ---------------------------------------------------------------------------
# Failure mode 1 — same topic separated by >30min should cluster together
# ---------------------------------------------------------------------------


class TestSameTopicLongGap:
    """v="3" should cluster a resumed topic; v="2" wrongly splits it."""

    def _build_cells(self) -> list[dict]:
        """Three turns on Rust borrow checker, with a 90-minute gap between
        turn 2 and turn 3 (>30min v="2" threshold, <24h v="3" bound).
        """
        t0 = datetime(2026, 5, 1, 9, 0, 0)
        return [
            _cell(*SAME_TOPIC_TURNS[0], t0, "rust-1"),
            _cell(*SAME_TOPIC_TURNS[1], t0 + timedelta(minutes=2), "rust-2"),
            # 90-minute gap — v="2" splits here.
            _cell(*SAME_TOPIC_TURNS[2], t0 + timedelta(minutes=92), "rust-3"),
        ]

    @pytest.mark.skipif(
        not mlx_actually_loaded(),
        reason="v=3 hybrid only runs when MLX nomic embeddings are "
        "actually loaded (HF cache populated). CI runners without the "
        "model fall back to TF-IDF where the 0.55 cosine threshold "
        "doesn't apply.",
    )
    def test_v3_clusters_resumed_topic(self):
        cells = self._build_cells()
        groups = _group_cells_by_embedding(cells)
        # All three turns on the same topic → exactly one cluster.
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_v2_baseline_splits_same_topic(self):
        # Documents the failure mode v="3" is meant to fix. v="2" splits
        # on the 90-min gap even though it's the same topic.
        cells = self._build_cells()
        groups = _group_cells_into_sessions(cells)
        assert len(groups) >= 2


# ---------------------------------------------------------------------------
# Failure mode 2 — different topics in a short window should NOT merge
# ---------------------------------------------------------------------------


class TestMultitaskShortWindow:
    """v="3" should keep distinct topics in distinct clusters even when
    interleaved within 30min. v="2" wrongly merges them.
    """

    def _build_cells(self) -> list[dict]:
        t0 = datetime(2026, 5, 1, 14, 0, 0)
        # Rust turn → cookie turn → Rust turn, all within 10 minutes.
        return [
            _cell(*SAME_TOPIC_TURNS[0], t0, "rust-a"),
            _cell(*DIFFERENT_TOPIC_TURN, t0 + timedelta(minutes=4), "cookie-a"),
            _cell(*SAME_TOPIC_TURNS[1], t0 + timedelta(minutes=8), "rust-b"),
        ]

    @pytest.mark.skipif(
        not mlx_actually_loaded(),
        reason="v=3 hybrid only runs when MLX nomic embeddings are "
        "actually loaded (HF cache populated).",
    )
    def test_v3_keeps_topics_separate(self):
        cells = self._build_cells()
        groups = _group_cells_by_embedding(cells)
        # Two clusters: Rust (2 cells) + Cookie (1 cell).
        assert len(groups) == 2
        # Verify the cookie cell ended up alone (it's the only off-topic one).
        cookie_groups = [
            g for g in groups
            if any("cookie" in cell["native_id"] for cell in g)
        ]
        assert len(cookie_groups) == 1
        assert len(cookie_groups[0]) == 1

    def test_v2_baseline_merges_unrelated_topics(self):
        # Documents the failure mode. v="2" merges everything into one
        # cluster because all cells are within the 30-min gap window.
        cells = self._build_cells()
        groups = _group_cells_into_sessions(cells)
        assert len(groups) == 1
        assert len(groups[0]) == 3


# ---------------------------------------------------------------------------
# Failure mode 3 — cells with missing timestamps shouldn't fragment
# ---------------------------------------------------------------------------


class TestMissingTimestamps:
    """v="3" anchors None-timestamp cells to last_seen + 1s so they join
    their physical neighbors instead of fragmenting. v="2" starts a new
    group on every None.
    """

    def _build_cells(self) -> list[dict]:
        t0 = datetime(2026, 5, 1, 9, 0, 0)
        return [
            _cell(*SAME_TOPIC_TURNS[0], t0, "rust-a"),
            # Missing timestamp — v="2" fragments here.
            _cell(*SAME_TOPIC_TURNS[1], None, "rust-b"),
            _cell(*SAME_TOPIC_TURNS[2], t0 + timedelta(minutes=3), "rust-c"),
        ]

    @pytest.mark.skipif(
        not mlx_actually_loaded(),
        reason="v=3 hybrid only runs when MLX nomic embeddings are "
        "actually loaded (HF cache populated).",
    )
    def test_v3_does_not_fragment_on_missing_timestamps(self):
        cells = self._build_cells()
        groups = _group_cells_by_embedding(cells)
        # All same-topic → one cluster despite the missing timestamp.
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_v2_baseline_fragments_on_missing_timestamps(self):
        # Documents the failure mode. v="2" splits whenever gap is None.
        cells = self._build_cells()
        groups = _group_cells_into_sessions(cells)
        assert len(groups) >= 2


# ---------------------------------------------------------------------------
# Back-compat — v="2" path still callable behind the feature flag
# ---------------------------------------------------------------------------


def _outer_cell(href: str, prompt: str, ts: str, response: str) -> str:
    """Render one Takeout outer-cell. Matches the regex shape in ingest.py
    (outer-cell wraps mdl-grid → header-cell + 3 content-cells)."""
    return (
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
        '<div class="mdl-grid">'
        '<div class="header-cell mdl-cell mdl-cell--12-col">'
        '<p class="mdl-typography--title">Gemini Apps<br></p></div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        f'Prompted <a href="{href}">{prompt}</a><br>'
        f"{ts}"
        f"<p>{response}</p></div>"
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"></div>'
        '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
        "<b>Products:</b><br>&emsp;Gemini Apps</div></div></div>"
    )


# Two turns on the same topic, 90 minutes apart (>30min v="2" threshold,
# <24h v="3" bound). v="2" splits → 2 sessions; v="3" merges → 1.
GEMINI_SAME_TOPIC_HTML = "<html><body>" + _outer_cell(
    "https://gemini.google.com/app/rust-1",
    "Explain Rust borrow checker and how lifetimes propagate through "
    "function signatures with multiple references.",
    "May 1, 2026, 9:00:00 AM EDT",
    "The borrow checker tracks ownership at compile time. Each value has "
    "exactly one owner; references are borrowed loans tracked with lifetimes.",
) + _outer_cell(
    "https://gemini.google.com/app/rust-2",
    "How does the borrow checker handle nested function calls when "
    "references with different lifetimes are passed through?",
    "May 1, 2026, 10:30:00 AM EDT",
    "Lifetime elision rules cover most function signatures, but when "
    "multiple input references exist the compiler requires explicit lifetime "
    "annotations to map outputs to their borrowed source.",
) + "</body></html>"


class TestParseGeminiTakeoutFeatureFlag:
    """The feature flag picks v="2" vs v="3" inside parse_gemini_takeout_html;
    metadata advertises which path produced the session.
    """

    @pytest.fixture
    def html_path(self, tmp_path: Path) -> Path:
        path = tmp_path / "MyActivity.html"
        path.write_text(GEMINI_SAME_TOPIC_HTML, encoding="utf-8")
        return path

    @pytest.mark.skipif(
        not mlx_actually_loaded(),
        reason="v=3 hybrid only runs when MLX nomic embeddings are "
        "actually loaded (HF cache populated).",
    )
    def test_default_path_is_v3(self, html_path: Path):
        sessions = list(parse_gemini_takeout_html(html_path))
        # Same-topic turns with 90-min gap — v="3" clusters into 1 session,
        # v="2" splits into 2.
        assert len(sessions) == 1
        s = sessions[0]
        assert s.source_format_version == "3"
        assert s.metadata.get("reconstruction") == "embedding+time"
        assert s.metadata.get("reconstruction_threshold") == GEMINI_TAKEOUT_EMBED_SIMILARITY
        assert (
            s.metadata.get("reconstruction_window_seconds")
            == GEMINI_TAKEOUT_EMBED_MAX_WINDOW_SECONDS
        )

    def test_explicit_v2_path_still_works(self, html_path: Path):
        # Explicit v="2" caller path (back-compat); doesn't depend on MLX.
        sessions = list(
            parse_gemini_takeout_html(html_path, use_embedding_grouping=False)
        )
        # v="2" splits on the 90-min gap → 2 sessions.
        assert len(sessions) == 2
        assert all(s.source_format_version == "2" for s in sessions)
        for s in sessions:
            assert s.metadata.get("reconstruction") == "time_proximity"


# ---------------------------------------------------------------------------
# Performance / batching invariant — embed_batch called ONCE
# ---------------------------------------------------------------------------


class TestEmbedCalledOnce:
    """Meta-principle: batch at the boundary. 10k cells × per-cell embed()
    would be ~10× slower than one embed_batch() call.
    """

    def test_embed_batch_called_with_all_texts_once(self, monkeypatch):
        # Spy on embed_batch — verify it's called exactly once and that
        # the call carries every cell's text in one list.
        calls: list[list[str]] = []

        from trinity_local import embeddings as embeddings_module

        original = embeddings_module.embed_batch

        def spy(texts, *args, **kwargs):
            calls.append(list(texts))
            return original(texts, *args, **kwargs)

        monkeypatch.setattr(embeddings_module, "embed_batch", spy)

        t0 = datetime(2026, 5, 1, 9, 0, 0)
        cells = [
            _cell(*SAME_TOPIC_TURNS[0], t0, "a"),
            _cell(*SAME_TOPIC_TURNS[1], t0 + timedelta(minutes=2), "b"),
            _cell(*SAME_TOPIC_TURNS[2], t0 + timedelta(minutes=4), "c"),
        ]
        _group_cells_by_embedding(cells)

        assert len(calls) == 1
        assert len(calls[0]) == 3
