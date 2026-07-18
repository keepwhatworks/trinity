

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

    # ── 2026-07-16 widening (#19): the COMPRESSION-artifact miss classes.
    # Fixtures are SYNTHETIC equivalents of the real misses (never corpus
    # text in committed tests — the e4e0d64d privacy class). MUTATION: revert
    # the corresponding pattern in classify_cold_answerable and each reds.

    def test_third_person_deixis_excluded(self):
        # Real-miss shape: "He has <condition>, not <other> ..." — the
        # antecedent person lives in lost conversation context.
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable(
            "She prefers the second design, not the rounded one we started from")
        assert not ok and "deictic" in reason

    def test_trailing_deixis_excluded(self):
        # Real-miss shape: "<opaque id> <site> says invalid entry for that"
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable(
            "the portal rejects the reference number and says invalid entry for that")
        assert not ok and "deictic" in reason

    def test_bug_enumeration_excluded(self):
        # Real-miss shape: "Bug #1 — <renderer error> fires 2x per session"
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable(
            "Bug #1 - the widget renderer drops its context twice per session during resize")
        assert not ok and "enumeration" in reason

    def test_pasted_notification_fragment_excluded(self):
        # Real-miss shape: forwarded app-notification text, many short lines,
        # no request.
        from trinity_local.evals.builder import classify_cold_answerable
        ok, reason = classify_cold_answerable(
            "Fast. Easy.\nShop in our app\nOpen\nnow\nTap here\nInstall today")
        assert not ok and "fragment" in reason

    def test_pasted_page_dump_excluded(self):
        # Real-miss shape: a forwarded shopping-page scrape — dozens of nav/
        # product lines, no question anywhere. MUTATION: drop the >=15-line
        # branch in _is_pasted_fragment and this reds.
        from trinity_local.evals.builder import classify_cold_answerable
        dump = "\n".join(
            ["Menu", "Home", "Deals today", "Up to 40% off"]
            + [f"Ultra Portable Charger model {i} with braided cable and travel case" for i in range(6)]
            + ["Price", "Under 1,000", "1,000 to 2,000", "Over 5,000", "Brands", "Wattage", "Add to cart"]
        )
        ok, reason = classify_cold_answerable(dump)
        assert not ok and "fragment" in reason

    def test_pasted_code_with_question_still_passes(self):
        # Over-exclusion guard for the page-dump branch: a long paste WITH a
        # question is a real request and must dispatch.
        from trinity_local.evals.builder import classify_cold_answerable
        code = "\n".join([f"    line_{i} = compute({i})" for i in range(20)])
        ok, reason = classify_cold_answerable(
            "why does the loop below allocate on every iteration?\n" + code)
        assert ok, f"over-excluded a real code question -> {reason}"

    def test_widening_does_not_eat_valid_prompts(self):
        # Guard against over-exclusion: normal self-contained prompts that
        # merely CONTAIN a pronoun or the word bug still pass.
        from trinity_local.evals.builder import classify_cold_answerable
        for p in (
            "explain how a debugger attaches to a running process on linux",
            "what does the borrow checker guarantee, and where does it stop helping",
            "write a short bio for a speaker who studies coral reefs in Fiji",
        ):
            ok, reason = classify_cold_answerable(p)
            assert ok, f"over-excluded: {p!r} -> {reason}"


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

    def test_items_present_but_zero_dispatchable_is_unavailable(self, tmp_path, monkeypatch):
        """green-over-degenerate (2026-07-17, workflow finding): a set can carry
        stats.items > 0 while every item is context-bound / gold-unreachable, so
        eval-run has NOTHING to dispatch. It must read NOT available, or the
        new-model nudge points at a hollow benchmark. MUTATION: gate on
        stats.items instead of stats.dispatchable and this reds."""
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import evals_dir, eval_set_available
        d = evals_dir(); d.mkdir(parents=True, exist_ok=True)
        (d / "eval_hollow.json").write_text(
            json.dumps({"stats": {"items": 8, "dispatchable": 0}}), encoding="utf-8")
        assert eval_set_available() is False
        # ...and a set WITH dispatchable items reads available.
        (d / "eval_real.json").write_text(
            json.dumps({"stats": {"items": 8, "dispatchable": 3}}), encoding="utf-8")
        assert eval_set_available() is True

    def test_legacy_set_without_dispatchable_falls_back_to_items(self, tmp_path, monkeypatch):
        """A set built before the dispatchable stamp (no stats.dispatchable) must
        still read available off stats.items > 0 (back-compat, no false negative)."""
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import evals_dir, eval_set_available
        d = evals_dir(); d.mkdir(parents=True, exist_ok=True)
        (d / "eval_legacy.json").write_text(
            json.dumps({"stats": {"items": 3}}), encoding="utf-8")
        assert eval_set_available() is True

    def test_malformed_set_degrades_to_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.evals.builder import evals_dir, eval_set_available
        d = evals_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "eval_bad.json").write_text("{not json", encoding="utf-8")
        assert eval_set_available() is False


class TestGoldReachableClassifier:
    """Gold-side twin of the cold-answerable filter (2026-07-17): the item
    matrix on the six-model board found 12/35 items where EVERY model scored
    zero; the autopsy showed their golds are conversation continuations
    (context reveals / topic pivots), not taste corrections. Retro-verified
    on that board: union with the cold filter excludes 10/12 dead items while
    eating 1/22 discriminating ones. Fixtures are SYNTHETIC equivalents of
    the real misses (never corpus text — the e4e0d64d privacy class).
    MUTATION: revert the matching rule in classify_gold_reachable and each
    test here reds."""

    def _check(self, gold, prompt="compare the two proposals for the city park",
               rejected="Proposal A offers more green space overall."):
        from trinity_local.evals.builder import classify_gold_reachable
        return classify_gold_reachable(gold, prompt, rejected)

    def test_third_person_fact_reveal_excluded(self):
        ok, reason = self._check(
            "He takes lisinopril for blood pressure and walks with a cane")
        assert not ok and "context-reveal" in reason

    def test_first_person_fact_reveal_excluded(self):
        ok, reason = self._check(
            "I just bought the ceramic version from the outlet store")
        assert not ok and "context-reveal" in reason

    def test_continuation_opener_excluded(self):
        ok, reason = self._check(
            "same problem in the other browser. can I do it by phone")
        assert not ok and "lost conversation thread" in reason

    def test_novel_entity_reveal_excluded(self):
        # A mid-sentence name absent from prompt+rejected = facts from
        # outside the exchange.
        ok, reason = self._check("send the summary to Ferdinand at the branch")
        assert not ok and "novel entities" in reason and "Ferdinand" in reason

    def test_underscore_identifier_excluded(self):
        ok, reason = self._check("check LEGACY_AUDIT_NOTES first")
        assert not ok and "novel entities" in reason

    def test_personal_pivot_question_excluded(self):
        ok, reason = self._check(
            "How do I explain all of it to my eight year old nephew?")
        assert not ok and "topic pivot" in reason

    # ── over-exclusion guards: correction shapes MUST survive ──

    def test_corrective_not_shape_passes(self):
        # "X, not Y" argues against the answer's framing — a steer, even when
        # it opens with a third-person fact frame that would otherwise flag.
        # MUTATION: drop the corrective escape and this reds (third-person
        # rule fires on "She has ..."); the escape spared 4 real board items.
        ok, reason = self._check(
            "She has the compact layout in mind, not the expanded grid")
        assert ok, f"ate a corrective steer -> {reason}"

    def test_no_opener_correction_passes(self):
        ok, reason = self._check("no, just give me the spec as a table")
        assert ok, f"ate a classic 'no, ...' correction -> {reason}"

    def test_allcaps_shouting_is_not_an_entity(self):
        # Plain ALLCAPS words (BUG, CLASS) are emphasis, not entities —
        # only underscore identifiers count.
        ok, reason = self._check(
            "BUG CATEGORY two: the layout engine drops labels under resize")
        assert ok, f"ate an ALLCAPS taste-steer -> {reason}"

    def test_entity_present_in_rejected_passes(self):
        # Reachability includes the rejected answer: naming something the
        # model itself said is a steer on THAT answer.
        ok, reason = self._check(
            "focus on the Riverside section only",
            prompt="compare the two proposals for the city park",
            rejected="Proposal A adds the Riverside section and a playground.")
        assert ok, f"ate a steer that references the rejected answer -> {reason}"

    def test_question_with_topic_overlap_passes(self):
        # A question that stays on-topic is a reframe, not a pivot.
        ok, reason = self._check(
            "shouldn't the proposals be judged on maintenance cost for my neighborhood?")
        assert ok, f"ate an on-topic reframing question -> {reason}"

    def test_pivot_needs_a_prompt_to_pivot_from(self):
        # Callers without a resolved prompt (judge-alignment pair builder)
        # must not see every "my ...?" question flagged as a pivot.
        # MUTATION: drop the empty-prompt guard in _personal_pivot_question
        # and this reds.
        ok, reason = self._check(
            "How do I explain all of it to my eight year old nephew?",
            prompt="", rejected="")
        assert ok, f"pivot fired with no prompt to pivot from -> {reason}"
