"""Tests for the hierarchical memory index (§8.4 + §8.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from trinity_local.memory import (
    PromptNode,
    TurnWindow,
    iter_prompt_nodes,
    record_council_outcome,
    replay_value_score,
    infer_hardness,
    search,
    search_prompt_nodes,
    upsert_prompt_node,
    upsert_turn_window,
    load_cursor,
    save_cursor,
)
from trinity_local.memory.replay_value import (
    HIGH_VALUE_THEMES,
    diversify_mmr,
    staleness_score,
    theme_score,
)


@pytest.fixture
def memory_home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _node(*, id: str, text: str, embedding: list[float] | None = None, **overrides) -> PromptNode:
    base = dict(
        id=id,
        transcript_id=f"t-{id}",
        provider="claude",
        source_path=f"/tmp/{id}.jsonl",
        turn_index=0,
        text=text,
        embedding=embedding or [1.0, 0.0, 0.0, 0.0],
        created_at="2026-05-01T00:00:00Z",
    )
    base.update(overrides)
    return PromptNode(**base)


class TestStore:
    def test_upsert_and_iter_prompt_node(self, memory_home: Path):
        node = _node(id="p1", text="run a council on auth")
        upsert_prompt_node(node)
        nodes = list(iter_prompt_nodes())
        assert len(nodes) == 1
        assert nodes[0].id == "p1"
        assert nodes[0].text == "run a council on auth"

    def test_upsert_overwrites_by_id(self, memory_home: Path):
        upsert_prompt_node(_node(id="p1", text="v1"))
        upsert_prompt_node(_node(id="p1", text="v2"))
        nodes = list(iter_prompt_nodes())
        assert len(nodes) == 1
        assert nodes[0].text == "v2"

    def test_record_council_outcome_attaches_run(self, memory_home: Path):
        upsert_prompt_node(_node(id="p1", text="x"))
        ok = record_council_outcome(
            prompt_node_id="p1",
            council_run_id="c-abc",
            chairman_winner="claude",
        )
        assert ok is True
        node = next(iter_prompt_nodes())
        assert "c-abc" in node.council_run_ids
        assert node.chairman_winner == "claude"

    def test_record_council_outcome_missing_node_returns_false(self, memory_home: Path):
        assert record_council_outcome(prompt_node_id="missing", council_run_id="c") is False

    def test_cursor_roundtrip(self, memory_home: Path):
        assert load_cursor("claude") == {}
        save_cursor("claude", {"last_mtime": 1234567890.0})
        save_cursor("codex", {"last_offset": 42})
        assert load_cursor("claude") == {"last_mtime": 1234567890.0}
        assert load_cursor("codex") == {"last_offset": 42}


class TestReplayValue:
    def test_replay_value_score_weights(self):
        # All maxed = 0.30+0.14+0.06+0.14+0.14+0.16+0.10+0.06 - 0 = 1.10 (clip not needed at function level)
        score = replay_value_score(
            prompt_similarity=1.0,
            window_similarity=1.0,
            transcript_similarity=1.0,
            cluster_density=1.0,
            known_theme=1.0,
            uncertainty=1.0,
            importance=1.0,
            staleness=1.0,
            recently_run=0.0,
        )
        assert score == pytest.approx(1.10, rel=1e-3)

    def test_recently_run_penalizes(self):
        with_recent = replay_value_score(prompt_similarity=1.0, recently_run=1.0)
        without_recent = replay_value_score(prompt_similarity=1.0, recently_run=0.0)
        assert with_recent < without_recent

    def test_infer_hardness_high_value_theme_bonus(self):
        node = _node(id="x", text="x", themes=["trinity", "router"])
        h = infer_hardness(node)
        # +0.15 (council_count==0) +0.20 (high-value theme) = 0.35
        assert h == pytest.approx(0.35, rel=1e-3)
        assert "trinity" in HIGH_VALUE_THEMES

    def test_theme_bonus_is_unreachable_from_any_live_writer(self):
        """The guard above is VACUOUS on production data, and this pins why.

        `themes` is written ONLY by `guess_task_type` (ingest_helpers.py:118,
        incremental_ingest.py:370), whose range is a small TASK-TYPE vocabulary.
        `theme_score` reads against HIGH_VALUE_THEMES, a disjoint TOPICAL
        vocabulary. The intersection is empty, so the +0.20 branch has never
        fired since writer and reader landed in the same commit on 2026-05-06 —
        measured 0.0 on 47,536/47,536 nodes on disk.

        The test above passes `themes=["trinity","router"]`, values no live
        writer can emit, so it is permanently green over an impossible input:
        this repo's #1 named bug class (green while the data is degenerate)
        living inside its own suite. This test makes the MISMATCH the
        assertion, so the day either vocabulary is re-pointed at the other,
        THIS reds and the stale guard above becomes meaningful again.

        The writer's range is read from `guess_task_type`'s own AST rather
        than by sampling texts through it. Sampling is what this repo calls
        producer-asserted/consumer-unverified: a first draft of this test
        exercised eight strings, reached only five of the return values, and
        would have missed a convergence on any it did not happen to trigger.
        The AST cannot miss one, and reading the module with a regex is worse
        still — `task_types.py` also defines VALID_HORIZONS
        (tactical/strategic/philosophical) for an unrelated function, which a
        module-wide scan wrongly folds into the writer's range.
        """
        import ast
        import inspect

        from trinity_local import task_types

        tree = ast.parse(inspect.getsource(task_types))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "guess_task_type")
        writable = {n.value.value for n in ast.walk(fn)
                    if isinstance(n, ast.Return)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)}
        assert writable, "could not read guess_task_type's range — fixture is broken, not the code"

        overlap = writable & set(HIGH_VALUE_THEMES)
        assert not overlap, (
            f"A live writer can now emit a HIGH_VALUE_THEME ({sorted(overlap)}) — "
            "the vocabularies have converged, so "
            "test_infer_hardness_high_value_theme_bonus is no longer vacuous and "
            "this test should be deleted along with this assertion."
        )
        # And the consequence, pinned: every value a real node can carry scores zero.
        assert all(theme_score([t]) == 0.0 for t in writable), (
            "theme_score is now non-zero for a writable theme — the dead lookup "
            "came alive and every replay-value consumer changed behaviour"
        )

    def test_infer_hardness_sums_all_active_terms(self):
        # All scoring terms active: a re-run-worthy node (>1 council, a
        # high-value theme, high importance). The chairman pick is the
        # only winner signal now — the user-pick disagree term is gone.
        node = _node(
            id="x", text="x",
            chairman_winner="claude",
            council_run_ids=["c1", "c2"],
            themes=["trinity"],
            importance=0.9,
        )
        # council_count>1 (+0.10) + theme (+0.20) + importance>0.7 (+0.15) = 0.45
        assert infer_hardness(node) == pytest.approx(0.45, rel=1e-3)
        # The clamp is the ceiling — never exceeds 1.0.
        assert infer_hardness(node) <= 1.0

    def test_staleness_score_buckets(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)

        recent = (now - timedelta(hours=1)).isoformat()
        assert staleness_score(recent) == 0.0

        week_old = (now - timedelta(days=5)).isoformat()
        assert staleness_score(week_old) == 0.25

        month_old = (now - timedelta(days=20)).isoformat()
        assert staleness_score(month_old) == 0.6

        ancient = (now - timedelta(days=60)).isoformat()
        assert staleness_score(ancient) == 1.0

        assert staleness_score(None) == 1.0
        assert staleness_score("not a date") == 1.0

    def test_theme_score(self):
        assert theme_score([]) == 0.0
        assert theme_score(["unknown"]) == 0.0
        assert theme_score(["trinity"]) == 0.5
        assert theme_score(["trinity", "router"]) == 1.0
        assert theme_score(["trinity", "router", "evals"]) == 1.0  # clamped


class TestSearch:
    def test_empty_index_returns_empty(self, memory_home: Path):
        assert search_prompt_nodes("anything") == []
        assert search("anything") == []

    def test_search_finds_similar_prompt(self, memory_home: Path):
        from trinity_local.embeddings import embed
        # Use real embeddings so cosine actually distinguishes texts
        upsert_prompt_node(_node(
            id="p1",
            text="run a council on the authentication migration plan",
            embedding=embed("search_document: run a council on the authentication migration plan"),
        ))
        upsert_prompt_node(_node(
            id="p2",
            text="what is the weather today",
            embedding=embed("search_document: what is the weather today"),
        ))
        results = search_prompt_nodes("authentication migration", top_k=5)
        assert len(results) >= 1
        # The auth-related prompt should outrank the weather one
        assert results[0].prompt_id == "p1"

    def test_search_includes_window_similarity_when_available(self, memory_home: Path):
        upsert_prompt_node(_node(id="p1", text="design model router"))
        upsert_turn_window(TurnWindow(
            id="w1",
            transcript_id="t-p1",
            center_prompt_id="p1",
            text="prior context: discussion of model routing trade-offs",
            embedding=[1.0, 0.0, 0.0, 0.0],
            turn_start=0,
            turn_end=2,
        ))
        results = search("model router", top_k=5)
        assert len(results) >= 1
        # window_similarity should be populated for p1 since we wrote a window
        match = next((r for r in results if r.prompt_id == "p1"), None)
        assert match is not None



class TestMmr:
    def test_mmr_diversifies_near_duplicates(self):
        from trinity_local.memory.index import SearchResult
        # Items with similar scores — MMR should prefer the diverse one
        items = [
            SearchResult(prompt_id=str(i), text=text, score=score,
                         prompt_similarity=score, window_similarity=0,
                         transcript_similarity=0, hardness=0, reasons=[])
            for i, (text, score) in enumerate([
                ("authentication migration plan", 0.90),
                ("authentication migration steps", 0.88),
                ("weather forecast for today", 0.85),
                ("authentication plan migration", 0.87),
            ])
        ]
        diversified = diversify_mmr(items, top_k=2, lambda_factor=0.5)
        ids = [r.prompt_id for r in diversified]
        assert ids[0] == "0"  # highest score
        # With lambda=0.5 and close scores, the weather item (zero overlap)
        # should beat the auth near-duplicates
        assert ids[1] == "2"


class TestReplayValueInertnessIsReported:
    """The replay-value quality term is INERT on the real corpus and must say so.

    Measured 2026-08-07: replay_value_score returns exactly 0.084 for 100% of
    5,000 sampled real nodes. All four inputs are degenerate simultaneously, and
    three share one root cause — record_council_outcome() is exported, tested,
    and has ZERO production callers, so council_run_ids / importance /
    last_replayed_at are null corpus-wide.

    Because base_score ADDS quality to the rejection signal, a constant shifts
    every candidate equally and changes no ranking. That is precisely what made
    this invisible for so long: inert, not wrong. These pin the detector so the
    collapse is reported instead of reading as a working ranking.
    """

    def test_detector_fires_on_constant_quality(self):
        from trinity_local.me_builder import _quality_is_inert

        assert _quality_is_inert([0.084] * 50) is True

    def test_detector_silent_when_quality_actually_ranks(self):
        from trinity_local.me_builder import _quality_is_inert

        assert _quality_is_inert([0.084, 0.084, 0.30, 0.084]) is False, (
            "a term that DOES vary must not be reported as inert, or the "
            "warning becomes noise the day the recorder gets wired up"
        )

    def test_infer_hardness_collapses_without_a_council_recorder(self):
        """Root-cause pin: with no council_run_ids/themes/importance — the state
        of every node on the real corpus — hardness is its no-council constant."""
        from trinity_local.memory.replay_value import infer_hardness
        from trinity_local.memory.schemas import PromptNode

        bare = PromptNode(
            id="n1", text="a substantive prompt about architecture",
            transcript_id="t1", provider="claude", source_path="/tmp/x.jsonl",
            turn_index=0, embedding=[0.0] * 768, created_at="2026-08-07T00:00:00Z")
        assert infer_hardness(bare) == 0.15, (
            "infer_hardness reads only fields that record_council_outcome writes, "
            "so if this stops being constant the recorder was wired up and the "
            "me_builder inertness comment needs re-measuring"
        )
