"""Unit tests for the 3-stage lens-discovery pipeline.

Pipeline: basins.py + decisions.py + pair_mining.py + post-filter.
Tests cover the load-bearing pieces — k-means determinism with seed,
decision parser tolerance, and the basin post-filter that makes
topology evidence actually gate output (Option C, council_70eaf228d7753074).
"""

from __future__ import annotations




class TestBasins:
    def test_kmeans_pp_handles_all_zero_distances(self):
        """When all candidate points are duplicates of an existing centroid,
        the probability vector becomes all-zero and rng.choice would NaN.
        Fall back to uniform random."""
        import numpy as np
        from trinity_local.me.basins import _kmeans_pp_init

        # 10 identical rows — every candidate is at distance 0 from first centroid
        matrix = np.tile(np.array([[1.0, 0.0, 0.0]], dtype=np.float32), (10, 1))
        centroids = _kmeans_pp_init(matrix, k=3, seed=42)
        assert centroids.shape == (3, 3)
        # All centroids equal the duplicated row (no NaN, no crash)
        assert np.all(np.isfinite(centroids))

    def test_compute_basins_skips_nan_embeddings(self, monkeypatch, tmp_path):
        """A few NaN embeddings would otherwise poison every centroid via
        mean-update, collapsing all rows into one cluster."""
        import numpy as np
        from trinity_local.memory.schemas import PromptNode
        from trinity_local.me import basins as basins_mod

        # Build a synthetic corpus: 30 well-separated rows + 5 NaN.
        # Each node gets a unique transcript_id so thread-aware clustering
        # treats each turn as its own session and the test exercises the
        # NaN-skip path (not the thread-collapse path).
        def _node(node_id: str, text: str, embedding: list[float], tid: str) -> PromptNode:
            return PromptNode(
                id=node_id,
                transcript_id=tid,
                provider="test",
                source_path="/tmp/x",
                turn_index=0,
                text=text,
                embedding=embedding,
                created_at="2026-05-06T00:00:00",
            )

        good = [
            _node(
                f"p_{i:02d}",
                f"prompt {i}",
                np.eye(10)[i % 10].tolist() + [0.0] * 758,
                f"thread_{i:02d}",  # one thread per turn — exercise k-means
            )
            for i in range(30)
        ]
        bad = [_node(f"bad_{i}", f"poisoned {i}", [float("nan")] * 768, f"thread_bad_{i}") for i in range(5)]

        # Stage 1 basins reads `iter_prompt_nodes(limit=None)` — the
        # canonical uncapped walker (so populated installs aren't masked
        # by the 5000-node hot-path cap on the default call). Patch on
        # the basins module since the function is imported at the top.
        monkeypatch.setattr(basins_mod, "iter_prompt_nodes", lambda **kw: iter(good + bad))
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        result = basins_mod.compute_basins(k=5, seed=42)
        # NaN rows skipped, ≥2 healthy basins formed (would collapse to 1 if NaN
        # propagated through centroid means)
        assert len(result) >= 2, f"basins collapsed: {[(b.id, b.size) for b in result]}"
        total = sum(b.size for b in result)
        assert total == 30, "NaN rows should be skipped from clustering"

    def test_basin_for_prompt_lookup(self):
        from trinity_local.me.basins import Basin, basin_for_prompt
        b1 = Basin(id="b00", size=2, top_terms=[], centroid=[0.0], prompt_ids=["p1", "p2"])
        b2 = Basin(id="b01", size=1, top_terms=[], centroid=[1.0], prompt_ids=["p3"])
        assert basin_for_prompt([b1, b2], "p2") == "b00"
        assert basin_for_prompt([b1, b2], "p3") == "b01"
        assert basin_for_prompt([b1, b2], "missing") is None

    def test_basin_serialization_keeps_full_prompt_ids(self):
        """Regression: basins.py used to truncate prompt_ids to 50 entries
        in to_dict() "for readable JSON". After load_basins() round-trips
        through that JSON, basin_for_prompt() returned None for any prompt
        beyond the first 50 — silently breaking Stage 2/4 of the lens
        pipeline. Keep the full list so round-trips are lossless."""
        from trinity_local.me.basins import Basin
        ids = [f"p{i:04d}" for i in range(80)]  # > 50, the old cap
        basin = Basin(id="b00", size=80, top_terms=[], centroid=[0.0], prompt_ids=ids)
        payload = basin.to_dict()
        assert payload["prompt_ids"] == ids, (
            f"to_dict truncated prompt_ids ({len(payload['prompt_ids'])} of {len(ids)}); "
            "this breaks basin_for_prompt() lookup after load_basins() round-trip"
        )


class TestDecisionParser:
    def test_valence_enum_includes_correction_and_cost(self):
        # correction/cost are in the valence enum so real lenses don't
        # get rejected for lacking literal regret quotes. (The earlier
        # citation to a council ID that never landed was removed in
        # commit a6a8a11 — same phantom shape caught in claude.md.
        # The enum itself is load-bearing in me/decisions.py; that's
        # what this test pins.)
        from trinity_local.me.decisions import VALID_VALENCES
        assert "correction" in VALID_VALENCES
        assert "cost" in VALID_VALENCES
        assert "regret" in VALID_VALENCES

    def test_parse_decisions_tolerates_markdown_fences(self):
        from trinity_local.me.decisions import parse_decisions

        raw = """```json
{"id": "d_1", "privileged": "speed", "sacrificed": "quality", "valence": "regret", "basin": "b00", "verbatim": "shipped fast hated it", "prompt_id": "p1"}
{"id": "d_2", "privileged": "rigor", "sacrificed": "speed", "valence": "satisfaction", "basin": "b01", "verbatim": "took weeks worth it", "prompt_id": "p2"}
```"""
        decisions = parse_decisions(raw, basins=[])
        assert len(decisions) == 2
        assert decisions[0].privileged == "speed"
        assert decisions[1].valence == "satisfaction"

    def test_parse_decisions_skips_malformed_lines(self):
        from trinity_local.me.decisions import parse_decisions

        raw = '{"id":"d_1","privileged":"a","sacrificed":"b","valence":"regret","basin":"b00","verbatim":"x","prompt_id":"p1"}\n'
        raw += "this is not json\n"
        raw += '{"missing_required":"yes"}\n'
        raw += '{"id":"d_2","privileged":"c","sacrificed":"d","valence":"BOGUS","basin":"b00","verbatim":"y","prompt_id":"p1"}\n'
        raw += '{"id":"d_3","privileged":"e","sacrificed":"f","valence":"correction","basin":"b00","verbatim":"z","prompt_id":"p1"}\n'

        decisions = parse_decisions(raw, basins=[])
        # Only d_1 and d_3 should survive; d_2 has invalid valence, plus the noise lines
        assert {d.id for d in decisions} == {"d_1", "d_3"}

    def test_extraction_prompt_asks_for_would_flip_if_with_blank_guard(self):
        """#138 Track B: chairman extracts would_flip_if retroactively
        from transcripts. The prompt MUST include the field in the
        schema AND the 'leave blank if unclear, do not rationalize'
        guard — without the guard, chairman invents plausible-sounding
        counterfactuals the user never actually thought (the
        rationalization-creep failure mode the user explicitly flagged
        when this task was scoped)."""
        from trinity_local.me.decisions import render_extraction_prompt

        prompt = render_extraction_prompt(samples=[
            {"prompt_id": "p1", "text": "sample text", "basin": "b00"},
        ], basins=[])
        # Schema must include the field
        assert '"would_flip_if"' in prompt, (
            "render_extraction_prompt missing would_flip_if in schema"
        )
        # Rationalization guard must be present
        prompt_lower = prompt.lower()
        assert "leave" in prompt_lower and "blank" in prompt_lower, (
            "render_extraction_prompt missing the 'leave blank if unclear' guard"
        )
        assert "do not rationalize" in prompt_lower or "rationaliz" in prompt_lower, (
            "render_extraction_prompt missing the 'do not rationalize' guard"
        )

    def test_parse_decisions_reads_chairman_would_flip_if(self):
        """When chairman emits would_flip_if (Track B path), the parser
        captures it on the Decision dataclass. Track A (live-logged)
        already populated this field via decision-log CLI; this test
        proves the chairman-extracted path also lands cleanly."""
        from trinity_local.me.decisions import parse_decisions
        raw = (
            '{"id":"d_1","privileged":"momentum to close",'
            '"sacrificed":"relational reciprocity","valence":"satisfaction",'
            '"basin":"b00","verbatim":"pay the 2% fast",'
            '"prompt_id":"p1",'
            '"would_flip_if":"If we needed the buyer agent\'s repeat business"}'
        )
        decisions = parse_decisions(raw, basins=[])
        assert len(decisions) == 1
        assert decisions[0].would_flip_if.startswith("If we needed")

    def test_parse_decisions_re_tags_basin_from_prompt_id(self):
        # Chairman's `basin` field is NOT trusted — re-tag from
        # ground-truth basin lookup. This is what makes basin tags
        # load-bearing per council_70eaf228d7753074.
        from trinity_local.me.basins import Basin
        from trinity_local.me.decisions import parse_decisions

        basins = [
            Basin(id="b00", size=1, top_terms=[], centroid=[0.0], prompt_ids=["real_prompt_1"]),
            Basin(id="b01", size=1, top_terms=[], centroid=[1.0], prompt_ids=["real_prompt_2"]),
        ]
        # Chairman claims basin=b99 (made up) but prompt_id maps to b00
        raw = '{"id":"d_1","privileged":"a","sacrificed":"b","valence":"regret","basin":"b99","verbatim":"x","prompt_id":"real_prompt_1"}'
        decisions = parse_decisions(raw, basins)
        assert decisions[0].basin == "b00", "basin should be re-tagged from prompt_id, not chairman"


class TestPairMiningPostFilter:
    def _make_decisions(self):
        # Per spec ≥3 basins required for accepted lens. Build a corpus
        # where the topic-local pair sits in one basin and the structural
        # pair spans 3 distinct basins.
        from trinity_local.me.decisions import Decision
        return [
            # Topic-local tension: both directions in basin b00
            Decision(id="d1", privileged="speed", sacrificed="quality", valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="quality", sacrificed="speed", valence="regret", basin="b00", verbatim="y"),
            # Cross-basin tension: 3 distinct basins
            Decision(id="d3", privileged="long_view", sacrificed="last_day", valence="regret", basin="b00", verbatim="z"),
            Decision(id="d4", privileged="last_day", sacrificed="long_view", valence="regret", basin="b01", verbatim="w"),
            Decision(id="d5", privileged="long_view", sacrificed="last_day", valence="correction", basin="b02", verbatim="v"),
        ]

    def test_single_basin_pair_demoted_to_ordering(self):
        # All tension evidence in b00 → topic-local virtue, not real lens
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        decisions = self._make_decisions()
        pair = LensPair(
            pole_a="speed", pole_b="quality",
            failure_a="hack", failure_b="paralysis",
            tension_decisions=["d1", "d2"],
            verdict="accepted",
        )
        filtered = basin_post_filter([pair], decisions)
        assert filtered[0].verdict == "preserve_as_ordering"
        assert filtered[0].basins_spanned == ["b00"]

    def test_three_basin_pair_stays_accepted(self):
        # Per spec ≥3 basins required. Tension spans b00, b01, b02 → real lens.
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        decisions = self._make_decisions()
        pair = LensPair(
            pole_a="long_view", pole_b="last_day",
            failure_a="paralysis", failure_b="hedonism",
            tension_decisions=["d3", "d4", "d5"],
            verdict="accepted",
        )
        filtered = basin_post_filter([pair], decisions)
        assert filtered[0].verdict == "accepted"
        assert set(filtered[0].basins_spanned) == {"b00", "b01", "b02"}

    def test_two_basin_pair_now_accepted(self):
        # #267: threshold lowered 3→2 for cross-domain users. A tension that
        # recurs across TWO unrelated basins is a cross-domain lens, not a
        # topic-local virtue — it now stays accepted (was demoted under ≥3).
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        decisions = self._make_decisions()
        pair = LensPair(
            pole_a="long_view", pole_b="last_day",
            failure_a="paralysis", failure_b="hedonism",
            tension_decisions=["d3", "d4"],
            verdict="accepted",
        )
        filtered = basin_post_filter([pair], decisions)
        assert filtered[0].verdict == "accepted"
        assert set(filtered[0].basins_spanned) == {"b00", "b01"}

    def test_sentinel_basin_ids_treated_as_missing(self):
        # Chairman sometimes emits "?" or "unknown" when uncertain. These
        # mustn't inflate basins_spanned past the spec threshold or every
        # ambiguous pair would falsely qualify as a real lens.
        from trinity_local.me.decisions import Decision
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="?", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="unknown", verbatim="y"),
            Decision(id="d3", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="z"),
        ]
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        filtered = basin_post_filter([pair], decisions)
        # Only b00 counts; sentinels stripped → 1 basin → demoted.
        assert filtered[0].basins_spanned == ["b00"]
        assert filtered[0].verdict == "preserve_as_ordering"

    def test_pair_with_no_basin_evidence_dropped(self):
        # Decisions referenced don't exist → no basin coverage
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        decisions = self._make_decisions()
        pair = LensPair(
            pole_a="x", pole_b="y",
            failure_a="a", failure_b="b",
            tension_decisions=["nonexistent_1", "nonexistent_2"],
            verdict="accepted",
        )
        filtered = basin_post_filter([pair], decisions)
        assert filtered[0].verdict == "dropped"

    def test_already_dropped_pair_passes_through(self):
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        pair = LensPair(
            pole_a="x", pole_b="y", failure_a="a", failure_b="b",
            verdict="dropped",
        )
        filtered = basin_post_filter([pair], [])
        assert filtered[0].verdict == "dropped"


class TestStage4SemanticFilter:
    """T2 semantic filter for Stage 4 — #186.

    The lens-build chairman sometimes tags tensions to plausible-looking
    but semantically wrong basins. The count-only filter (basin_post_filter
    pre-#186) accepted any tension that claimed 3+ basin IDs without
    checking whether those basins' actual content matched the tension.
    The T2 filter (cosine vs basin centroid) catches this — basins whose
    centroid is semantically far from the tension probe text get dropped
    BEFORE the count rule decides the verdict.
    """

    def _make_decisions(self):
        from trinity_local.me.decisions import Decision
        return [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="b01", verbatim="y"),
            Decision(id="d3", privileged="a", sacrificed="b", valence="correction", basin="b02", verbatim="z"),
        ]

    def test_no_centroids_falls_through_unchanged(self):
        """Without basin_centroids the filter is a no-op — backward
        compat for callers that don't load topics.json."""
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        pair = LensPair(
            pole_a="speed", pole_b="quality",
            failure_a="hack", failure_b="paralysis",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        filtered = basin_post_filter([pair], self._make_decisions(), basin_centroids=None)
        assert filtered[0].verdict == "accepted"
        assert set(filtered[0].basins_spanned) == {"b00", "b01", "b02"}

    def test_basins_without_centroid_pass_through(self):
        """If a basin id has no centroid in the map (e.g. novel basin
        since topics.json was last built), keep it — avoid silent data
        loss."""
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        # Empty dict but truthy via being passed as non-None default
        # actually disables T2 entirely; this test pins the "centroid
        # missing for THIS basin" case where the map is non-empty but
        # the specific basin is absent.
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        # Provide centroid only for b99 (basin the pair doesn't claim)
        centroids = {"b99": [0.0] * 384}
        filtered = basin_post_filter(
            [pair], self._make_decisions(), basin_centroids=centroids,
        )
        # b00/b01/b02 have no centroid → all kept via the pass-through branch
        assert set(filtered[0].basins_spanned) == {"b00", "b01", "b02"}

    def test_semantically_close_basins_kept_far_ones_dropped(self):
        """The real T2 work — when the tension's probe text aligns
        semantically with some basin centroids but not others, only
        the close ones survive.

        Uses an orthogonal-basis synthetic embedding so cosine is
        deterministic without depending on the real embedder. Patches
        the embed() function to return a fixed vector aligned with
        b_close's centroid.
        """
        from unittest.mock import patch
        from trinity_local.me.pair_mining import LensPair, basin_post_filter

        # 3D orthogonal basis: tension aligns with axis 0.
        tension_emb = [1.0, 0.0, 0.0]
        centroids = {
            "b_close": [0.95, 0.05, 0.0],   # cosine ~0.99 with tension
            "b_far_1": [0.0, 1.0, 0.0],     # cosine 0.0 — orthogonal
            "b_far_2": [-0.5, 0.5, 0.7],    # cosine -0.5 — opposite
        }
        from trinity_local.me.decisions import Decision
        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b_close", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="b_far_1", verbatim="y"),
            Decision(id="d3", privileged="a", sacrificed="b", valence="correction", basin="b_far_2", verbatim="z"),
        ]
        pair = LensPair(
            pole_a="abstract_pole_a", pole_b="abstract_pole_b",
            failure_a="pure_a_failure", failure_b="pure_b_failure",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )

        # The semantic filter only runs when MLX is loaded (TF-IDF
        # can't bridge abstract↔concrete) — force it on for this test
        # of the discrimination logic.
        with patch("trinity_local.embeddings.mlx_actually_loaded", return_value=True), \
             patch("trinity_local.embeddings.embed", return_value=tension_emb):
            filtered = basin_post_filter([pair], decisions, basin_centroids=centroids)

        # Only b_close survives the 0.40 threshold → 1 basin → demoted
        # from "accepted" (needs ≥3) to "preserve_as_ordering" (1-2)
        assert filtered[0].basins_spanned == ["b_close"]
        assert filtered[0].verdict == "preserve_as_ordering"

    def test_all_basins_pass_when_above_threshold(self):
        """Sanity inverse: when every basin centroid is semantically
        close, all three survive and the tension stays accepted."""
        from unittest.mock import patch
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        from trinity_local.me.decisions import Decision

        tension_emb = [1.0, 0.0, 0.0]
        centroids = {
            "b00": [0.95, 0.05, 0.0],
            "b01": [0.90, 0.10, 0.0],
            "b02": [0.85, 0.15, 0.0],
        }
        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="b01", verbatim="y"),
            Decision(id="d3", privileged="a", sacrificed="b", valence="correction", basin="b02", verbatim="z"),
        ]
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        with patch("trinity_local.embeddings.mlx_actually_loaded", return_value=True), \
             patch("trinity_local.embeddings.embed", return_value=tension_emb):
            filtered = basin_post_filter([pair], decisions, basin_centroids=centroids)
        assert filtered[0].verdict == "accepted"
        assert set(filtered[0].basins_spanned) == {"b00", "b01", "b02"}

    def test_embedder_failure_falls_through_safely(self):
        """If the embedder raises (offline + no fallback), keep all
        basins — semantic filtering is advisory, not load-bearing.
        Without this, an offline machine with stale embed config
        would silently drop every tension to 'dropped'."""
        from unittest.mock import patch
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        from trinity_local.me.decisions import Decision

        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="b01", verbatim="y"),
            Decision(id="d3", privileged="a", sacrificed="b", valence="correction", basin="b02", verbatim="z"),
        ]
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        centroids = {"b00": [1.0, 0.0], "b01": [0.0, 1.0], "b02": [0.5, 0.5]}
        with patch("trinity_local.embeddings.mlx_actually_loaded", return_value=True), \
             patch(
                 "trinity_local.embeddings.embed",
                 side_effect=RuntimeError("embed backend down"),
             ):
            filtered = basin_post_filter([pair], decisions, basin_centroids=centroids)
        # Embedder failed → all basins kept (no silent data loss)
        assert filtered[0].verdict == "accepted"
        assert set(filtered[0].basins_spanned) == {"b00", "b01", "b02"}

    def test_dimension_mismatch_keeps_basin(self):
        """If basin centroid dim ≠ tension embedding dim (embedder
        changed since topics.json was built), keep the basin and
        let the user re-run dream to refresh."""
        from unittest.mock import patch
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        from trinity_local.me.decisions import Decision

        tension_emb = [1.0, 0.0, 0.0]  # 3-dim
        centroids = {
            "b00": [1.0, 0.0, 0.0, 0.0],  # 4-dim — mismatched
        }
        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="x"),
        ]
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1"],
            verdict="accepted",
        )
        with patch("trinity_local.embeddings.mlx_actually_loaded", return_value=True), \
             patch("trinity_local.embeddings.embed", return_value=tension_emb):
            filtered = basin_post_filter([pair], decisions, basin_centroids=centroids)
        # Dim mismatch → b00 kept via pass-through
        assert filtered[0].basins_spanned == ["b00"]

    def test_tfidf_fallback_skips_semantic_filter(self):
        """#185 — the load-bearing robustness guard. Under TF-IDF
        fallback (no MLX), the cosine of an abstract tension vs a
        concrete basin centroid collapses (~0.14 even for a RELATED
        pair, because TF-IDF is lexical and the texts share almost no
        tokens). Applying the 0.40 threshold would over-reject every
        tension and silently gut the lens — the same dormancy class
        as the retired moves T1 gate.

        So when mlx_actually_loaded() is False, the semantic filter
        must be a no-op: keep all basins, let the count-only rule
        stand. This test forces MLX off + provides centroids that
        WOULD fail the cosine threshold, and asserts nothing is
        dropped.
        """
        from unittest.mock import patch
        from trinity_local.me.pair_mining import LensPair, basin_post_filter
        from trinity_local.me.decisions import Decision

        # Orthogonal centroids — every cosine vs the tension probe is
        # 0.0, well below threshold. If the filter ran, all basins
        # would be dropped → tension dropped entirely.
        centroids = {
            "b00": [0.0, 1.0, 0.0],
            "b01": [0.0, 0.0, 1.0],
            "b02": [0.0, 1.0, 1.0],
        }
        decisions = [
            Decision(id="d1", privileged="a", sacrificed="b", valence="regret", basin="b00", verbatim="x"),
            Decision(id="d2", privileged="b", sacrificed="a", valence="regret", basin="b01", verbatim="y"),
            Decision(id="d3", privileged="a", sacrificed="b", valence="correction", basin="b02", verbatim="z"),
        ]
        pair = LensPair(
            pole_a="a", pole_b="b", failure_a="x", failure_b="y",
            tension_decisions=["d1", "d2", "d3"],
            verdict="accepted",
        )
        # embed would return a tension vector orthogonal to all
        # centroids — but mlx_actually_loaded=False must short-circuit
        # BEFORE embed is even called.
        with patch("trinity_local.embeddings.mlx_actually_loaded", return_value=False), \
             patch("trinity_local.embeddings.embed", return_value=[1.0, 0.0, 0.0]) as mock_embed:
            filtered = basin_post_filter([pair], decisions, basin_centroids=centroids)
        # All basins kept despite orthogonal centroids — filter skipped
        assert set(filtered[0].basins_spanned) == {"b00", "b01", "b02"}
        assert filtered[0].verdict == "accepted"
        # embed should never be called when MLX isn't loaded
        mock_embed.assert_not_called()


class TestPairMiningParser:
    def test_parses_array_with_markdown_fences(self):
        from trinity_local.me.pair_mining import parse_pair_mining_output

        raw = """```json
[
  {"pole_a": "speed", "pole_b": "rigor", "failure_a": "hack", "failure_b": "paralysis", "tension_decisions": ["d1", "d2"], "dual_evidence": {"pole_a": ["d1"], "pole_b": ["d2"]}, "verdict": "accepted"},
  {"pole_a": "x", "pole_b": "y", "failure_a": "z", "failure_b": "w", "tension_decisions": ["d3"], "dual_evidence": {}, "verdict": "dropped"}
]
```"""
        pairs = parse_pair_mining_output(raw)
        assert len(pairs) == 2
        assert pairs[0].pole_a == "speed"
        assert pairs[1].verdict == "dropped"

    def test_skips_pairs_with_missing_poles(self):
        from trinity_local.me.pair_mining import parse_pair_mining_output

        raw = '[{"pole_a": "x", "pole_b": "x", "verdict": "accepted"}, {"pole_a": "a", "pole_b": "b", "verdict": "accepted"}]'
        pairs = parse_pair_mining_output(raw)
        # First skipped (poles equal), second kept
        assert len(pairs) == 1
        assert pairs[0].pole_a == "a"

    def test_horizon_field_parsed_and_clamped(self):
        """#139 (#1): chairman emits horizon per pair (tactical /
        strategic / philosophical). Invalid or missing horizons clamp
        to 'tactical' (the safe always-applies floor; strategic /
        philosophical would over-claim)."""
        from trinity_local.me.pair_mining import parse_pair_mining_output, VALID_HORIZONS

        raw = (
            '['
            '{"pole_a":"a","pole_b":"b","horizon":"strategic","verdict":"accepted"},'
            '{"pole_a":"c","pole_b":"d","horizon":"philosophical","verdict":"accepted"},'
            '{"pole_a":"e","pole_b":"f","horizon":"tactical","verdict":"accepted"},'
            '{"pole_a":"g","pole_b":"h","horizon":"BOGUS","verdict":"accepted"},'
            '{"pole_a":"i","pole_b":"j","verdict":"accepted"}'
            ']'
        )
        pairs = parse_pair_mining_output(raw)
        assert len(pairs) == 5
        assert pairs[0].horizon == "strategic"
        assert pairs[1].horizon == "philosophical"
        assert pairs[2].horizon == "tactical"
        assert pairs[3].horizon == "tactical"  # clamped from BOGUS
        assert pairs[4].horizon == "tactical"  # missing → tactical default
        assert VALID_HORIZONS == {"tactical", "strategic", "philosophical"}

    def test_horizon_in_to_dict_and_prompt(self):
        """Horizon survives serialization (so lenses.json carries it)
        AND chairman is asked for it in the prompt (so new lens-builds
        produce it from real corpus, not just from default-tactical)."""
        from trinity_local.me.pair_mining import (
            LensPair, render_pair_mining_prompt,
        )

        p = LensPair(pole_a="a", pole_b="b", failure_a="x", failure_b="y",
                     horizon="philosophical")
        assert p.to_dict()["horizon"] == "philosophical"

        prompt = render_pair_mining_prompt(decisions=[])
        assert '"horizon"' in prompt
        assert "tactical" in prompt and "strategic" in prompt and "philosophical" in prompt
        # The "prefer strategic if unsure" guidance must be present —
        # otherwise chairman over-claims philosophical for everything.
        assert "prefer" in prompt.lower() and "strategic" in prompt.lower()

    def test_prompt_carries_generator_over_generated_meta_rule(self):
        """When two candidate poles BOTH pass the cross-basin test,
        the chairman should prefer the GENERATOR (the rule that
        derives the other) over the GENERATED (the instance). This is
        a load-bearing meta-directive — drop it and lens-build keeps
        emitting one-level-too-low pairs like "shipping velocity over
        polish" instead of "executable artifact over description of one"
        (the rule that generates the shipping-velocity preference).

        Pinning the keywords + at least one worked example so a future
        prompt-cleanup doesn't silently strip the directive."""
        from trinity_local.me.pair_mining import render_pair_mining_prompt
        prompt = render_pair_mining_prompt(decisions=[])
        lower = prompt.lower()
        # Headline keyword + the explicit definition
        assert "generator" in lower and "generated" in lower
        assert "derives the other" in lower or "rule beats the instance" in lower
        # At least one of the worked examples (the shipping-velocity or
        # data-ownership pair) must be present so the chairman has
        # something concrete to pattern-match against.
        assert (
            "shipping velocity" in lower
            or "user ownership of data" in lower
        )
