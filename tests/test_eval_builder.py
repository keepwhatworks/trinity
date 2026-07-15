

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


class TestBaselineResolution:
    """Full-turn baseline resolution (council 2026-07-14, post-null roadmap):
    the extraction's <=25-word quote is provenance, not a baseline. At build,
    the act's prompt_id resolves to the FULL assistant turn when the quote
    verifiably belongs to it; the eval_id fingerprint pins the resolution so
    upgraded baselines mint a NEW set id (the static-set rule). Mutation
    targets: drop the containment check, drop the fingerprint token, drop the
    loader default → each reds a test here."""

    _FULL = ("Here is a long, complete assistant answer that talks through the "
             "options at length. The decisive quote lives right here in the "
             "middle of it, and then the answer keeps going for a while with "
             "more sentences of reasoning and a conclusion.")

    def _seed_and_build(self, tmp_path, monkeypatch, resolver):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals import builder
        me = tmp_path / "me"
        me.mkdir(parents=True, exist_ok=True)
        (me / "preference_acts.jsonl").write_text(json.dumps({
            "id": "r_res01", "trigger": "model_miss", "kind": "REFRAME",
            "sacrificed": "The decisive quote lives right here in the middle",
            "privileged": "just give me the spec, not a narrative",
            "why": "wanted spec", "prompt_id": "pn_full1", "basin": "b00",
            "question_text": "Write the onboarding spec for the new service?",
        }) + "\n", encoding="utf-8")
        monkeypatch.setattr(builder, "_resolve_full_turns", resolver)
        return builder.build_eval_set()

    def test_resolves_full_turn_when_quote_contained(self, tmp_path, monkeypatch):
        es = self._seed_and_build(tmp_path, monkeypatch,
                                  lambda ids: {"pn_full1": self._FULL})
        item = es.items[0]
        assert item.baseline_resolution == "full_turn"
        assert item.rejected_response == self._FULL

    def test_falls_back_to_quote_when_not_contained(self, tmp_path, monkeypatch):
        es = self._seed_and_build(tmp_path, monkeypatch,
                                  lambda ids: {"pn_full1": "a completely different answer entirely, long enough to pass length floors but sharing no text."})
        item = es.items[0]
        assert item.baseline_resolution == "quote"
        assert item.rejected_response.startswith("The decisive quote")

    def test_resolution_changes_the_eval_id(self, tmp_path, monkeypatch):
        """Same acts, different baseline content → DIFFERENT set id, so old
        results stay pinned to the set content they ran against."""
        id_quote = self._seed_and_build(tmp_path, monkeypatch, lambda ids: {}).eval_id
        id_full = self._seed_and_build(tmp_path, monkeypatch,
                                       lambda ids: {"pn_full1": self._FULL}).eval_id
        assert id_quote != id_full

    def test_legacy_sets_load_as_quote(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import evals_dir, load_eval_set
        d = evals_dir()
        (d / "eval_legacy1.json").write_text(json.dumps({
            "eval_id": "eval_legacy1", "built_at": "2026-07-01T00:00:00",
            "source": "rejections", "stats": {},
            "items": [{"eval_item_id": "ei_1", "prompt": "a self contained question about caching strategies?",
                       "rejection_type": "REFRAME", "rejected_response": "quote text",
                       "user_substitute": "u", "rubric_signal": "r", "basin_id": None,
                       "source": "rejections", "source_id": "r_1", "prompt_id": None,
                       "provider_of_rejected_response": None}],
        }))
        es = load_eval_set("eval_legacy1")
        assert es.items[0].baseline_resolution == "quote"


class TestEvalSetAvailable:
    """Green-gate guard for eval_set_available() — relocated from launchpad_data
    to evals.builder 2026-07-14 when the launchpad eval-score card was removed
    (`status`'s new-model nudge still gates on it). A built set counts as
    available ONLY with >=1 scoreable item: a 0-item hollow set (a ledger of
    only self_expressed / all-degenerate acts builds one) must read as NOT
    available, else the eval-run nudge points at a benchmark with no signal."""

    def _write_set(self, tmp_path, monkeypatch, items):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import evals_dir
        d = evals_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "eval_test.json").write_text(
            json.dumps({"stats": {"items": items}}), encoding="utf-8")

    def test_no_evals_dir_is_unavailable_and_stays_uncreated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import eval_set_available
        assert eval_set_available() is False
        # The no-mkdir contract: the check must NOT create the ghost dir
        # (evals_dir() mkdirs — using it here was the 2026-07-14 review catch).
        # MUTATION: route the check through evals_dir() and this reds.
        assert not (tmp_path / "evals").exists(), (
            "eval_set_available() created ~/.trinity/evals/ — the read-only "
            "contract is broken (ghost dir falsely reads as eval-ready to any "
            "is_dir() consumer)"
        )

    def test_hollow_zero_item_set_is_unavailable(self, tmp_path, monkeypatch):
        # MUTATION: if the >0 guard degrades to "any eval_*.json exists", reds.
        self._write_set(tmp_path, monkeypatch, 0)
        from trinity_local.evals.builder import eval_set_available
        assert eval_set_available() is False

    def test_set_with_items_is_available(self, tmp_path, monkeypatch):
        self._write_set(tmp_path, monkeypatch, 3)
        from trinity_local.evals.builder import eval_set_available
        assert eval_set_available() is True

    def test_malformed_set_degrades_to_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import evals_dir, eval_set_available
        d = evals_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "eval_bad.json").write_text("{not json", encoding="utf-8")
        assert eval_set_available() is False
