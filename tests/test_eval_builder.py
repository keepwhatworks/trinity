

class TestColdAnswerableClassifier:
    """#316 productized: the deterministic filter that keeps context-bound
    prompts out of head-to-head dispatch (a cold dispatch of 'take a look at
    this photo' measures apologies, not taste — ~65% of a conversational
    corpus was degenerate this way, measured 2026-06-11)."""

    def test_fragment_excluded(self):
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable("continue")
        assert not ok and "fragment" in reason

    def test_image_grounded_excluded(self):
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable(
            "is this gold? take a look and tell me what you think of it")
        assert not ok and "image/artifact" in reason

    def test_conversational_continuation_excluded(self):
        from trinity_local.evals.builder import classify_cold_answerable
        ok, _ = classify_cold_answerable("what about the second option we discussed")
        assert not ok

    def test_bare_deixis_excluded(self):
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable("this is shiny in some angles, why though")
        assert not ok and "deictic" in reason

    def test_self_contained_question_passes(self):
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable(
            "compare fixed versus variable mortgage for a fifteen year horizon")
        assert ok and reason == "self-contained"
