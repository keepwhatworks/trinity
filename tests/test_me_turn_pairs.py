"""Tests for Stage 0 turn-pair gap extraction.

Council `council_e7560934cb1f1d72` ratified Option A (one batch chairman
call) ONLY because it ships with deterministic post-validators that
catch chairman skim/miscategorization. These tests pin the validators —
without them, Option A is just plausible JSON, not real signal.
"""

from __future__ import annotations

from trinity_local.me.turn_pairs import RejectionSignal


class TestRejectionParser:
    def test_valid_signal_types_are_the_four_spec_types(self):
        from trinity_local.me.turn_pairs import VALID_SIGNAL_TYPES
        assert VALID_SIGNAL_TYPES == {"REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING"}

    def test_parse_rejections_skips_unknown_signal_types(self):
        from trinity_local.me.turn_pairs import parse_rejections

        raw = (
            '{"id":"r_1","type":"REFRAME","model_quote":"x","user_substitute":"y","why_signal":"a","prompt_id":"p1"}\n'
            '{"id":"r_2","type":"BOGUS","model_quote":"x","user_substitute":"y","why_signal":"a","prompt_id":"p1"}\n'
            '{"id":"r_3","type":"COMPRESSION","model_quote":"x","user_substitute":"y","why_signal":"a","prompt_id":"p1"}\n'
        )
        sigs = parse_rejections(raw, basins=[])
        # ids are content hashes now (not the chairman's r_1/r_2 sequence),
        # so assert on count + types, and that every id is a globally-stable
        # r_<hash> (the BOGUS-type row is dropped).
        assert len(sigs) == 2
        assert all(s.type in {"REFRAME", "COMPRESSION"} for s in sigs)
        assert all(s.id.startswith("r_") and len(s.id) > 5 for s in sigs)

    def test_parse_rejections_re_tags_basin_from_prompt_id(self):
        # Same trick as Stage 2 — chairman's basin field isn't trusted;
        # we re-attach from the ground-truth basin lookup.
        from trinity_local.me.basins import Basin
        from trinity_local.me.turn_pairs import parse_rejections

        basins = [
            Basin(id="b00", size=1, top_terms=[], centroid=[0.0], prompt_ids=["real_p1"]),
        ]
        raw = '{"id":"r_1","type":"REFRAME","model_quote":"x","user_substitute":"y","why_signal":"a","prompt_id":"real_p1","basin":"b99"}'
        sigs = parse_rejections(raw, basins)
        assert sigs[0].basin == "b00"


class TestValidators:
    def _sig(self, sig_type: str, prompt_id: str = "p1") -> RejectionSignal:
        return RejectionSignal(
            id="r_1",
            type=sig_type,
            model_quote="quote",
            user_substitute="substitute",
            why_signal="delta",
            prompt_id=prompt_id,
            basin=None,
        )

    def test_compression_keeps_when_user_text_is_short(self):
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": " ".join(["word"] * 100),  # 100 words
            "user_text": " ".join(["a"] * 5),            # 5 words → ratio 1/20
            "next_user_text": "",
        }}
        kept, rejected = validate_signals([self._sig("COMPRESSION")], index)
        assert len(kept) == 1
        assert not rejected

    def test_compression_drops_when_user_text_too_long(self):
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": " ".join(["word"] * 100),
            "user_text": " ".join(["a"] * 50),  # ratio 1/2 — well above 1/10
            "next_user_text": "",
        }}
        kept, rejected = validate_signals([self._sig("COMPRESSION")], index)
        assert not kept
        assert "ratio" in rejected[0]["reason"]

    def test_redirect_keeps_single_part_model_with_substantive_steer(self):
        # THE FIX (2026-06-06): a redirect lands on a SINGLE-part model answer too
        # — the user steers a different direction. The old `_looks_multi_part` gate
        # dropped these (14/14 of the founder's real shopping/home-automation
        # redirects); now a substantive user steer is kept regardless of model shape.
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "Do you want me to help you look for brushed gold options?",
            "user_text": "No, let me see if I can get a better customer picture instead",
            "next_user_text": "",
        }}
        kept, _ = validate_signals([self._sig("REDIRECT")], index)
        assert len(kept) == 1

    def test_redirect_keeps_when_model_is_multi_part(self):
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "1. First option\n2. Second option\n3. Third option",
            "user_text": "actually show me customer photos instead",
            "next_user_text": "",
        }}
        kept, _ = validate_signals([self._sig("REDIRECT")], index)
        assert len(kept) == 1

    def test_redirect_drops_trivial_user_turn(self):
        # A bare confirmation ("ok") is not a steer — still dropped, now by the
        # substantive-turn check rather than the (removed, over-strict) multi-part gate.
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "Just one short answer.",
            "user_text": "ok",
            "next_user_text": "",
        }}
        kept, rejected = validate_signals([self._sig("REDIRECT")], index)
        assert not kept
        assert "thin" in rejected[0]["reason"]

    def test_sharpening_keeps_when_keywords_overlap(self):
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "structural advantages emerge from compounding decisions",
            "user_text": "structural inevitability via compounding",
            "next_user_text": "",
        }}
        kept, _ = validate_signals([self._sig("SHARPENING")], index)
        assert len(kept) == 1

    def test_sharpening_drops_when_user_pivots_away(self):
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "consider the macroeconomic context",
            "user_text": "what about the technical implementation",
            "next_user_text": "",
        }}
        kept, rejected = validate_signals([self._sig("SHARPENING")], index)
        assert not kept
        assert "overlap" in rejected[0]["reason"]

    def test_reframe_keeps_when_substituted_frame_persists_into_next_turn(self):
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "talk about distribution channels for marketing",
            "user_text": "what about the manufacturing supply chain",
            "next_user_text": "and the manufacturing capacity over time",
        }}
        kept, _ = validate_signals([self._sig("REFRAME")], index)
        assert len(kept) == 1

    def test_reframe_drops_when_user_returns_to_model_frame(self):
        # User pivoted but next turn returned to model's original frame —
        # not a real reframe per spec (substituted frame must persist).
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "talk about distribution channels for marketing",
            "user_text": "what about manufacturing capacity",
            "next_user_text": "right, back to distribution channels then for marketing",
        }}
        kept, rejected = validate_signals([self._sig("REFRAME")], index)
        assert not kept
        assert "did not persist" in rejected[0]["reason"]

    def test_reframe_lenient_when_no_next_turn_data(self):
        # Without next-turn data, we can't confirm persistence; be lenient
        # rather than dropping signal that might be real.
        from trinity_local.me.turn_pairs import validate_signals
        index = {"p1": {
            "assistant_text": "model frame",
            "user_text": "user reframe",
            "next_user_text": "",
        }}
        kept, _ = validate_signals([self._sig("REFRAME")], index)
        assert len(kept) == 1


class TestStage0BatchFailureGuard:
    """#203: a per-batch chairman failure (timeout returncode==-1, or empty
    stdout) must be detected so the loop aborts instead of accumulating a
    silently-partial corpus that slips past the #194 clobber guard."""

    def _result(self, stdout, returncode):
        from trinity_local.providers import ProviderResult
        return ProviderResult(provider="claude", stdout=stdout, stderr="", returncode=returncode)

    def test_timeout_sentinel_is_a_failure(self):
        from trinity_local.me_builder import _stage0_batch_failed
        assert _stage0_batch_failed(self._result("", -1)) is True

    def test_empty_stdout_is_a_failure(self):
        from trinity_local.me_builder import _stage0_batch_failed
        assert _stage0_batch_failed(self._result("", 0)) is True
        assert _stage0_batch_failed(self._result("   \n  ", 0)) is True

    def test_real_output_is_not_a_failure(self):
        from trinity_local.me_builder import _stage0_batch_failed
        assert _stage0_batch_failed(self._result('[{"type":"REFRAME"}]', 0)) is False


class TestStage0LowEffortExtractor:
    """Stage 0/2 extraction is mechanical → must run at LOW effort
    regardless of the provider's configured level, so the chairman call
    returns under the 8-min per-call timeout (a 40-pair batch at high
    effort timed out on real data; #203 caught it, this prevents it)."""

    def test_extractor_config_forces_low_effort(self):
        import dataclasses
        from trinity_local.config import ProviderConfig
        # Mirror the me_builder construction: replace(effort="low").
        chairman_config = ProviderConfig(
            name="claude", type="cli", enabled=True, label="Claude",
            command=["claude", "-p"], args=[], task_types={"general"},
            model="claude-opus-4-8", effort="high",
        )
        extract_config = dataclasses.replace(chairman_config, effort="low")
        assert extract_config.effort == "low"
        assert chairman_config.effort == "high"  # original unchanged (frozen)
        # And the low-effort flag is what the claude CLI command will inject.
        from trinity_local.providers import _effective_effort
        assert _effective_effort(extract_config) == "low"

    def test_batch_size_is_small_enough_for_timeout(self):
        from trinity_local.me_builder import _STAGE0_BATCH_SIZE
        # Smaller batches keep per-call generation under the timeout.
        assert _STAGE0_BATCH_SIZE <= 20


class TestCorpusFingerprintSkip:
    """#1 skip-if-unchanged: an unchanged corpus → same fingerprint → the
    next build short-circuits with zero model calls."""

    def test_fingerprint_stable_for_same_corpus(self, patch_trinity_home):
        from trinity_local.memory import upsert_prompt_node
        from trinity_local.memory.schemas import PromptNode
        from trinity_local.me_builder import _corpus_fingerprint
        for i in range(3):
            upsert_prompt_node(PromptNode(
                id=f"p{i}", transcript_id="t", provider="claude", source_path="/x",
                turn_index=i, text=f"prompt {i}", embedding=[0.0]*8,
                created_at="2026-05-12T00:00:00Z", following_assistant_text="",
            ))
        fp1 = _corpus_fingerprint()
        fp2 = _corpus_fingerprint()
        assert fp1 == fp2 and ":" in fp1  # deterministic, "count:hash" shape

    def test_fingerprint_changes_when_corpus_grows(self, patch_trinity_home):
        from trinity_local.memory import upsert_prompt_node
        from trinity_local.memory.schemas import PromptNode
        from trinity_local.me_builder import _corpus_fingerprint
        def add(i):
            upsert_prompt_node(PromptNode(
                id=f"p{i}", transcript_id="t", provider="claude", source_path="/x",
                turn_index=i, text=f"prompt {i}", embedding=[0.0]*8,
                created_at="2026-05-12T00:00:00Z", following_assistant_text="",
            ))
        add(0); add(1)
        before = _corpus_fingerprint()
        add(2)
        assert _corpus_fingerprint() != before  # new prompt → new fingerprint


class TestStage0ConcurrencyCap:
    def test_concurrency_cap_is_bounded(self):
        from trinity_local.me_builder import _STAGE0_MAX_CONCURRENCY
        # Capped so we don't spawn a swarm of claude subprocesses.
        assert 1 <= _STAGE0_MAX_CONCURRENCY <= 8


class TestExtractedPairIdsState:
    """#210: lens_build_state.json carries the set of turn-pairs Stage 0 has
    already classified, so the next build skips them (delta-extraction)."""

    def test_empty_when_no_state(self, patch_trinity_home):
        from trinity_local.me_builder import _extracted_pair_ids
        assert _extracted_pair_ids() == set()

    def test_reads_recorded_ids(self, patch_trinity_home):
        import json
        from trinity_local.me_builder import (
            _extracted_pair_ids,
            _lens_build_state_path,
        )
        sp = _lens_build_state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({
            "fingerprint": "3:abc", "extracted_pair_ids": ["p1", "p2", "p3"],
        }), encoding="utf-8")
        assert _extracted_pair_ids() == {"p1", "p2", "p3"}

    def test_malformed_state_yields_empty(self, patch_trinity_home):
        from trinity_local.me_builder import (
            _extracted_pair_ids,
            _lens_build_state_path,
        )
        sp = _lens_build_state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text("not json", encoding="utf-8")
        assert _extracted_pair_ids() == set()

    def test_missing_key_is_backward_compatible(self, patch_trinity_home):
        # A pre-#210 state file (fingerprint only) must read as no-extracted.
        import json
        from trinity_local.me_builder import (
            _extracted_pair_ids,
            _lens_build_state_path,
        )
        sp = _lens_build_state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"fingerprint": "3:abc"}), encoding="utf-8")
        assert _extracted_pair_ids() == set()


class TestToRejectionRoundTrip:
    """#210: to_rejection is the inverse of from_rejection, so delta-build
    can reload prior rejections from the unified ledger and merge new ones."""

    def test_round_trips_through_preference_act(self):
        from trinity_local.me.preference_acts import from_rejection, to_rejection
        original = RejectionSignal(
            id="r_abc", type="REDIRECT", model_quote="a long multi-thread answer",
            user_substitute="just thread two", why_signal="ignored the rest",
            prompt_id="p7", basin="b03", next_user_turn="and then what",
        )
        back = to_rejection(from_rejection(original))
        assert back.id == original.id
        assert back.type == original.type
        assert back.model_quote == original.model_quote
        assert back.user_substitute == original.user_substitute
        assert back.why_signal == original.why_signal
        assert back.prompt_id == original.prompt_id
        assert back.basin == original.basin
        assert back.next_user_turn == original.next_user_turn


class _RecordingProvider:
    """A fake provider that records which turn-pair ids each Stage 0 prompt
    asked about, and returns parseable output for every lens-build stage."""

    def __init__(self):
        self.stage0_batches: list[list[str]] = []

    def run(self, prompt, cwd=None):
        import json
        import re
        from trinity_local.providers import ProviderResult

        # Stage 0 turn-pair prompt: has the four-signal preamble + [id · basin=]
        # headers. Record the ids and emit one rejection per pair.
        if "THE FOUR SIGNAL TYPES" in prompt:
            ids = [m.strip() for m in re.findall(r"\[([^\]·]+) · basin=", prompt)]
            self.stage0_batches.append(ids)
            lines = "\n".join(
                json.dumps({
                    "id": "r", "type": "REFRAME",
                    "model_quote": "a verbose multi-paragraph framing of the answer",
                    "user_substitute": "no — reframe it around the cost instead",
                    "why_signal": "user pivoted the frame", "prompt_id": pid,
                })
                for pid in ids
            )
            return ProviderResult(provider="claude", stdout=lines, stderr="", returncode=0)

        # Stage 2 decision extraction: emit one decision so the build doesn't
        # short-circuit on "no_decisions_extracted" (which skips the state write).
        if "privileged" in prompt and "sacrificed" in prompt:
            return ProviderResult(
                provider="claude",
                stdout=json.dumps({
                    "id": "d1", "privileged": "ship velocity", "sacrificed": "polish",
                    "valence": "correction", "basin": "b00",
                    "verbatim": "just ship it", "prompt_id": "p0",
                }),
                stderr="", returncode=0,
            )

        # Stage 3 pair-mining (and anything else): no pairs needed for the
        # delta assertion; an empty array parses cleanly.
        return ProviderResult(provider="claude", stdout="[]", stderr="", returncode=0)


class TestStage0DeltaExtraction:
    """#210: the second build classifies ONLY turn-pairs the first build
    didn't — the central delta-extraction guarantee."""

    def _seed_pairs(self, n: int, *, start: int = 0):
        from trinity_local.memory import upsert_prompt_node
        from trinity_local.memory.schemas import PromptNode
        for i in range(start, start + n):
            upsert_prompt_node(PromptNode(
                id=f"p{i}", transcript_id="t", provider="claude", source_path="/x",
                turn_index=i,
                text=f"user turn number {i} with enough words to be a real prompt",
                embedding=[0.0] * 8,
                created_at="2026-05-12T00:00:00Z",
                preceding_assistant_text=(
                    f"assistant said something verbose and multi-part for turn {i}"
                ),
                following_assistant_text="",
            ))

    def test_second_build_extracts_only_new_pairs(self, patch_trinity_home, monkeypatch):
        from unittest.mock import patch as _patch

        from trinity_local import me_builder

        self._seed_pairs(3)
        fake = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3)
        first_seen = {pid for batch in fake.stage0_batches for pid in batch}
        assert {"p0", "p1", "p2"} <= first_seen, (
            f"first build should classify all seeded pairs; saw {first_seen}"
        )

        # Add one new turn-pair; the corpus fingerprint changes so the build
        # is NOT skipped, but Stage 0 should only see the new pair.
        self._seed_pairs(1, start=3)
        fake2 = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake2):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3)
        second_seen = {pid for batch in fake2.stage0_batches for pid in batch}
        assert second_seen == {"p3"}, (
            f"delta build should classify ONLY the new pair; saw {second_seen}"
        )

    def test_force_re_extracts_everything(self, patch_trinity_home):
        from unittest.mock import patch as _patch

        from trinity_local import me_builder

        self._seed_pairs(3)
        fake = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3)

        # force=True bypasses the delta — every pair is re-classified.
        fake2 = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake2):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3, force=True)
        forced_seen = {pid for batch in fake2.stage0_batches for pid in batch}
        assert {"p0", "p1", "p2"} <= forced_seen, (
            f"force should re-extract all pairs; saw {forced_seen}"
        )

    def test_force_preserves_provider_imported_acts(self, patch_trinity_home):
        # Review finding #2: a model_miss act imported via eval-import /
        # import_provider_memory has prompt_id=None and is not re-derivable
        # from turn-pairs. A `--force` rebuild (which sets existing_rejections
        # =[]) must NOT drop it from the ledger.
        from unittest.mock import patch as _patch

        from trinity_local import me_builder
        from trinity_local.me.preference_acts import (
            MODEL_MISS,
            PreferenceAct,
            append_preference_acts,
            load_preference_acts,
        )

        self._seed_pairs(3)
        # Imported signal: no prompt_id, distinct content id.
        append_preference_acts([PreferenceAct(
            id="imported_xyz", trigger=MODEL_MISS, privileged="user phrasing",
            sacrificed="model phrasing", kind="REFRAME", prompt_id=None)])

        fake = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3, force=True)

        survivors = {a.id for a in load_preference_acts()}
        assert "imported_xyz" in survivors, (
            "a --force rebuild dropped the provider-imported model_miss act"
        )

    def test_delta_preserves_prior_rejections_in_ledger(self, patch_trinity_home):
        from unittest.mock import patch as _patch

        from trinity_local import me_builder
        from trinity_local.me.preference_acts import (
            MODEL_MISS,
            load_preference_acts,
        )

        self._seed_pairs(3)
        fake = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3)
        after_first = [
            a for a in load_preference_acts() if a.trigger == MODEL_MISS
        ]

        self._seed_pairs(1, start=3)
        fake2 = _RecordingProvider()
        with _patch("trinity_local.providers.make_provider", return_value=fake2):
            me_builder.build_me_via_lens_pipeline(sample_size=10, k_basins=3)
        after_second = [
            a for a in load_preference_acts() if a.trigger == MODEL_MISS
        ]
        # The delta build re-extracted only p3 but the ledger must still
        # carry the p0–p2 rejections (carried forward, not dropped).
        first_ids = {a.id for a in after_first}
        assert first_ids <= {a.id for a in after_second}, (
            "delta build dropped previously-extracted rejections from the ledger"
        )
