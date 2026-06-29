"""Tests for basin label heuristic (Theme B #1 from the 100-persona audit).

Real-corpus audit found 100% of basins had empty `label`, so the viewer
fell back to `headline` — and on greeting-heavy basins headline was just
"Hello.". The largest cluster of 3,408 prompts rendered as "Hello." in
the topology graph.

Fix: at basin-construction time, scan the top representatives for the
longest multi-word non-greeting snippet and use that as `label`. Pure
heuristic, no LLM call. Chairman labeler stage stays a forward-arc item.
"""
from __future__ import annotations

from trinity_local.me.basins import _pick_label_snippet


class TestPickLabelSnippet:
    def test_skips_short_greeting_headline_for_longer_turn(self):
        """The real-corpus regression: basin b00 had headline 'Hello.' but
        the first turn was a substantive health question. The picker must
        skip 'Hello.' and surface the real turn."""
        reps = [
            {
                "headline": "Hello.",
                "turns": [
                    {"snippet": "I have a chronic condition and need to know what to do when I eat gluten"},
                    {"snippet": "Hi"},
                    {"snippet": "continue"},
                ],
            },
            {"headline": "Thanks!", "turns": [{"snippet": "great work today"}]},
        ]
        label = _pick_label_snippet(reps)
        assert "chronic condition" in label
        assert label != "Hello."

    def test_returns_empty_when_only_greetings(self):
        """When every candidate is a greeting/ack, return "" so the viewer
        can fall through to top_terms instead of mislabeling."""
        reps = [
            {"headline": "hi", "turns": [{"snippet": "yes"}, {"snippet": "ok"}]},
            {"headline": "thanks", "turns": [{"snippet": "?"}]},
        ]
        assert _pick_label_snippet(reps) == ""

    def test_uses_headline_when_substantive(self):
        """When the headline IS substantive (real-corpus basin b02),
        the picker should use it."""
        reps = [{
            "headline": "Find more properties that fit this bill. Reevaluate based on my plan for str and events.",
            "turns": [],
        }]
        label = _pick_label_snippet(reps)
        assert label.startswith("Find more properties")

    def test_truncates_at_label_max_chars(self):
        """Labels over 80 chars get truncated with an ellipsis so the
        topology graph doesn't overflow node labels."""
        long = "This is a very long substantive question about " + ("complex topic " * 10)
        reps = [{"headline": long, "turns": []}]
        label = _pick_label_snippet(reps)
        assert label.endswith("…")
        assert len(label) <= 81  # 80 + ellipsis

    def test_picks_longest_across_reps(self):
        """Across multiple reps, picks the LONGEST substantive snippet."""
        reps = [
            {"headline": "short but valid sentence", "turns": []},
            {"headline": "this is a much longer substantive headline that should win", "turns": []},
            {"headline": "another short one here", "turns": []},
        ]
        label = _pick_label_snippet(reps)
        assert "much longer substantive headline" in label

    def test_caps_at_five_reps(self):
        """Only scans top-5 reps for performance — extra reps ignored."""
        reps = [{"headline": "first", "turns": []}] * 4
        reps.append({"headline": "fifth substantive thread of representative work", "turns": []})
        # 6th rep has the substantive content but should NOT be considered
        reps.append({"headline": "sixth this is a substantive thread that beats everything else", "turns": []})
        label = _pick_label_snippet(reps)
        # The 6th rep's longer snippet is OUT of scope.
        assert "sixth" not in label

    def test_empty_reps_returns_empty(self):
        assert _pick_label_snippet([]) == ""

    def test_rep_with_no_turns_no_headline_is_skipped(self):
        reps = [{"headline": "", "turns": []}]
        assert _pick_label_snippet(reps) == ""


class TestBasinIntegratedLabel:
    """Smoke that the Basin dataclass + save→load round-trip preserves
    the new `label` field set by the heuristic. Construction integration
    via compute_basins runs against the live in-process corpus, so we
    cover the picker shape here and reserve corpus-shape coverage for
    the lens-build smoke."""

    def test_label_round_trips_through_save_load(self, tmp_path, monkeypatch):
        from trinity_local.me.basins import Basin, save_basins, load_basins

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        original = Basin(
            id="b00", size=10, top_terms=["x", "y"],
            centroid=[0.0] * 8, thread_count=5,
            label="how to debug a memory leak in a long-running Python process",
        )
        save_basins([original])
        loaded = load_basins()
        assert len(loaded) == 1
        assert loaded[0].label == original.label

    def test_load_handles_legacy_basin_without_label(self, tmp_path, monkeypatch):
        """Old topics.json files written before this fix have no `label`
        key. Loader must default to empty string, not crash."""
        import json
        from trinity_local.me.basins import load_basins, basins_path

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        path = basins_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Legacy shape: no `label` field
        path.write_text(json.dumps({
            "basins": [{
                "id": "b00", "size": 10, "thread_count": 5,
                "top_terms": ["a", "b"], "centroid": [0.0] * 8,
                "prompt_ids": [], "representatives": [],
            }]
        }))
        loaded = load_basins()
        assert loaded[0].label == ""
