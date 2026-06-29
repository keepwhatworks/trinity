"""EXTRACT-unification Stage 1 — the unified PreferenceAct evidence type.

Pins the adapters (RejectionSignal/Decision → PreferenceAct), the unified
reader over both on-disk stores, and round-trip. The writers + eval
harness are unchanged at this stage; later stages merge extraction and
migrate storage.
"""
from __future__ import annotations

import pytest

from trinity_local.me.decisions import Decision
from trinity_local.me.preference_acts import (
    MODEL_MISS,
    SELF_EXPRESSED,
    PreferenceAct,
    from_decision,
    from_rejection,
    iter_preference_acts,
    save_preference_acts,
)
from trinity_local.me.turn_pairs import RejectionSignal


def _seed_ledger(*, rejections=None, decisions=None):
    """#209: seed the unified ledger (the sole store) via the canonical
    adapters — replaces the retired save_rejections/save_decisions."""
    acts = [from_rejection(r) for r in (rejections or [])]
    acts += [from_decision(d) for d in (decisions or [])]
    save_preference_acts(acts, allow_shrink=True)


class TestAdapters:
    def test_rejection_maps_to_model_miss(self):
        r = RejectionSignal(
            id="r1", type="REFRAME", model_quote="do X the elaborate way",
            user_substitute="just do Y", why_signal="user reframed", prompt_id="p1",
            basin="b0", next_user_turn="and then Z",
        )
        a = from_rejection(r)
        assert a.trigger == MODEL_MISS
        assert a.privileged == "just do Y"        # user's substitute
        assert a.sacrificed == "do X the elaborate way"  # model's offering
        assert a.kind == "REFRAME"
        assert a.why == "user reframed"
        assert a.context == "and then Z"
        assert (a.prompt_id, a.basin, a.id) == ("p1", "b0", "r1")

    def test_decision_maps_to_self_expressed(self):
        d = Decision(
            id="d1", privileged="ship velocity", sacrificed="polish",
            valence="satisfaction", basin="b2", verbatim="just get it out",
            prompt_id="p2", would_flip_if="if it were user-facing", weight=2.0,
            source="user_logged",
        )
        a = from_decision(d)
        assert a.trigger == SELF_EXPRESSED
        assert a.privileged == "ship velocity"
        assert a.sacrificed == "polish"
        assert a.kind == "satisfaction"      # valence becomes kind
        assert a.why == "if it were user-facing"
        assert a.context == "just get it out"
        assert a.weight == 2.0
        assert a.source == "user_logged"


class TestRoundTrip:
    def test_to_from_dict_full(self):
        a = PreferenceAct(
            id="pa1", trigger=MODEL_MISS, privileged="P", sacrificed="S",
            kind="REDIRECT", why="W", prompt_id="p1", basin="b0",
            context="C", source="lens-build", weight=2.0,
        )
        assert PreferenceAct.from_dict(a.to_dict()) == a

    def test_to_dict_filters_defaults(self):
        a = PreferenceAct(id="pa1", trigger=SELF_EXPRESSED, privileged="P", sacrificed="S")
        d = a.to_dict()
        # Only the required fields survive when everything else is default.
        assert set(d) == {"id", "trigger", "privileged", "sacrificed"}

    def test_from_dict_tolerates_extra_keys(self):
        a = PreferenceAct.from_dict({
            "id": "pa1", "trigger": MODEL_MISS, "privileged": "P", "sacrificed": "S",
            "future_field": "ignored",
        })
        assert a.id == "pa1" and a.privileged == "P"


class TestQField:
    """The q-field — the constitution's φ(r) causal-status guard (the confound guard).
    Optional + append-only-ledger back-compat: an unlabeled act serializes with NO q keys
    and reads as UNCERTAIN (the safe default), so an un-attributed axis is never enshrined."""

    def test_round_trips_q_fields_when_set(self):
        from trinity_local.me.preference_acts import Q_CONFOUND

        a = PreferenceAct(
            id="pa1", trigger=MODEL_MISS, privileged="P", sacrificed="S",
            q_axis="terse↔verbose", q_status=Q_CONFOUND,
        )
        d = a.to_dict()
        assert d["q_axis"] == "terse↔verbose" and d["q_status"] == Q_CONFOUND
        assert PreferenceAct.from_dict(d) == a

    def test_to_dict_omits_q_when_unset(self):
        # Compact + back-compat: an unlabeled act serializes with NO q keys, so existing
        # ledger rows are byte-unchanged by the new fields.
        a = PreferenceAct(id="pa1", trigger=MODEL_MISS, privileged="P", sacrificed="S")
        d = a.to_dict()
        assert "q_axis" not in d and "q_status" not in d

    def test_unlabeled_reads_as_uncertain(self):
        from trinity_local.me.preference_acts import Q_UNCERTAIN

        a = PreferenceAct(id="pa1", trigger=MODEL_MISS, privileged="P", sacrificed="S")
        assert a.q_status == ""                    # stored blank (unlabeled)
        assert a.causal_status() == Q_UNCERTAIN    # but READ as the safe default

    def test_causal_status_floors_unknown_to_uncertain(self):
        from trinity_local.me.preference_acts import Q_OPERATIVE, Q_UNCERTAIN

        assert PreferenceAct(id="x", trigger=MODEL_MISS, privileged="p", sacrificed="s",
                             q_status=Q_OPERATIVE).causal_status() == Q_OPERATIVE
        # A garbage value never masquerades as a real status.
        assert PreferenceAct(id="x", trigger=MODEL_MISS, privileged="p", sacrificed="s",
                             q_status="bogus").causal_status() == Q_UNCERTAIN

    def test_old_ledger_line_without_q_loads_clean(self):
        # A pre-q-field on-disk line (load path = from_dict) loads without error and reads
        # as UNCERTAIN — the append-only ledger is never broken by the added fields.
        from trinity_local.me.preference_acts import Q_UNCERTAIN

        a = PreferenceAct.from_dict(
            {"id": "r1", "trigger": "model_miss", "privileged": "u", "sacrificed": "m"}
        )
        assert a.q_axis == "" and a.causal_status() == Q_UNCERTAIN


@pytest.mark.usefixtures("patch_trinity_home")
class TestUnifiedReader:
    """EXTRACT Stage 4b: iter_preference_acts reads the unified ledger as the
    SOLE store (legacy rejections.jsonl + decisions.jsonl retired, #209)."""

    def test_iter_reads_both_triggers_from_ledger(self):
        _seed_ledger(
            rejections=[RejectionSignal(id="r1", type="REFRAME", model_quote="m", user_substitute="u")],
            decisions=[Decision(id="d1", privileged="a", sacrificed="b", valence="regret",
                                basin=None, verbatim="v")],
        )
        acts = iter_preference_acts()
        assert {a.trigger for a in acts} == {MODEL_MISS, SELF_EXPRESSED}
        assert len(acts) == 2
        # Order: model_miss first, then self_expressed.
        assert acts[0].trigger == MODEL_MISS
        assert acts[1].trigger == SELF_EXPRESSED

    def test_iter_is_a_pure_read(self):
        # A read must NOT mutate the ledger on disk (no write-on-read footgun
        # for callers like eval-build).
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            preference_acts_path,
        )
        _seed_ledger(rejections=[
            RejectionSignal(id="r1", type="REFRAME", model_quote="m", user_substitute="u"),
        ])
        before = preference_acts_path().read_text(encoding="utf-8")
        assert iter_preference_acts()
        # ...the read didn't rewrite the file.
        assert preference_acts_path().read_text(encoding="utf-8") == before
        assert len(load_preference_acts()) == 1

    def test_iter_empty_when_no_ledger(self):
        assert iter_preference_acts() == []

    def test_iter_handles_only_rejections(self):
        _seed_ledger(rejections=[
            RejectionSignal(id="r1", type="COMPRESSION", model_quote="long", user_substitute="short"),
        ])
        acts = iter_preference_acts()
        assert len(acts) == 1 and acts[0].trigger == MODEL_MISS

    def test_iter_reads_ledger_as_source_of_truth(self):
        save_preference_acts([
            PreferenceAct(id="ledger_only", trigger=MODEL_MISS, privileged="p",
                          sacrificed="s", kind="REDIRECT"),
        ])
        acts = iter_preference_acts()
        assert any(a.id == "ledger_only" for a in acts)


@pytest.mark.usefixtures("patch_trinity_home")
class TestUnifiedLedgerFile:
    def test_save_then_load_round_trips(self):
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            save_preference_acts,
        )
        acts = [
            PreferenceAct(id="r1", trigger=MODEL_MISS, privileged="u", sacrificed="m", kind="REFRAME"),
            PreferenceAct(id="d1", trigger=SELF_EXPRESSED, privileged="a", sacrificed="b", kind="regret"),
        ]
        save_preference_acts(acts)
        back = load_preference_acts()
        assert [a.id for a in back] == ["r1", "d1"]
        assert [a.trigger for a in back] == [MODEL_MISS, SELF_EXPRESSED]

    def test_load_missing_file_is_empty(self):
        from trinity_local.me.preference_acts import load_preference_acts
        assert load_preference_acts() == []

    def test_save_empty_then_load_empty(self):
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            save_preference_acts,
        )
        save_preference_acts([])
        assert load_preference_acts() == []

    def test_load_skips_malformed_and_underspecified(self):
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            preference_acts_path,
        )
        p = preference_acts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '{"id":"r1","trigger":"model_miss","privileged":"u","sacrificed":"m"}\n'
            '{"trigger":"model_miss"}\n'   # no id → skip
            'not json\n',
            encoding="utf-8",
        )
        back = load_preference_acts()
        assert [a.id for a in back] == ["r1"]

    def test_load_skips_line_missing_required_fields(self):
        # #205: a line with id+trigger but missing privileged/sacrificed must
        # not crash from_dict's positional construction — skip it, keep the
        # valid one.
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            preference_acts_path,
        )
        p = preference_acts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            '{"id":"r1","trigger":"model_miss","privileged":"u","sacrificed":"m"}\n'
            '{"id":"bad","trigger":"model_miss"}\n'   # no privileged/sacrificed -> skip, no crash
            ,
            encoding="utf-8",
        )
        back = load_preference_acts()
        assert [a.id for a in back] == ["r1"]


@pytest.mark.usefixtures("patch_trinity_home")
class TestLedgerClobberGuard:
    """The unified ledger is on its way to being the source of truth, so
    it carries the #194 clobber guard — a degenerate (empty / cliff-drop)
    overwrite of a populated ledger is refused."""

    def _acts(self, n):
        return [PreferenceAct(id=f"a{i}", trigger=MODEL_MISS, privileged="p", sacrificed="s")
                for i in range(n)]

    def test_cliff_drop_refused_and_ledger_preserved(self):
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            preference_acts_path,
            save_preference_acts,
        )
        from trinity_local.me.turn_pairs import DegenerateExtractionError
        save_preference_acts(self._acts(6))
        with pytest.raises(DegenerateExtractionError):
            save_preference_acts([])  # 0 vs 6 existing → cliff-drop
        assert len(load_preference_acts()) == 6  # live ledger preserved
        assert (preference_acts_path().parent / "preference_acts.jsonl.degenerate").exists()

    def test_allow_shrink_escape_hatch(self):
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            save_preference_acts,
        )
        save_preference_acts(self._acts(6))
        save_preference_acts([], allow_shrink=True)  # explicit genuine shrink
        assert load_preference_acts() == []

    def test_cold_start_empty_is_fine(self):
        from trinity_local.me.preference_acts import save_preference_acts
        save_preference_acts([])  # no existing → no guard

    def test_growth_unaffected(self):
        from trinity_local.me.preference_acts import (
            load_preference_acts,
            save_preference_acts,
        )
        save_preference_acts(self._acts(6))
        save_preference_acts(self._acts(8))  # growth → fine
        assert len(load_preference_acts()) == 8


@pytest.mark.usefixtures("patch_trinity_home")
class TestLegacyMigration:
    """Review finding #3: a one-time, idempotent recovery seeds the ledger
    from legacy rejections.jsonl / decisions.jsonl left by a pre-#209 build,
    so an upgrade doesn't silently lose data until the next lens-build."""

    def _write_legacy(self, *, rejections=None, decisions=None):
        import json
        from trinity_local.me.basins import me_dir
        d = me_dir()
        d.mkdir(parents=True, exist_ok=True)
        if rejections is not None:
            (d / "rejections.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rejections) + "\n", encoding="utf-8")
        if decisions is not None:
            (d / "decisions.jsonl").write_text(
                "\n".join(json.dumps(x) for x in decisions) + "\n", encoding="utf-8")

    def test_recovers_rejections_and_decisions_into_ledger(self):
        from trinity_local.me.preference_acts import (
            MODEL_MISS,
            SELF_EXPRESSED,
            _migrate_legacy_preference_stores,
            load_preference_acts,
        )
        self._write_legacy(
            rejections=[{"id": "r1", "type": "REFRAME", "model_quote": "verbose",
                         "user_substitute": "terse", "why_signal": "w", "prompt_id": "p1"}],
            decisions=[{"id": "d1", "privileged": "speed", "sacrificed": "polish",
                        "valence": "correction", "basin": "b0", "verbatim": "ship it"}],
        )
        n = _migrate_legacy_preference_stores()
        assert n == 2
        acts = load_preference_acts()
        by_trigger = {a.trigger for a in acts}
        assert by_trigger == {MODEL_MISS, SELF_EXPRESSED}
        assert {a.id for a in acts} == {"r1", "d1"}

    def test_is_idempotent(self):
        from trinity_local.me.preference_acts import (
            _migrate_legacy_preference_stores,
            load_preference_acts,
        )
        self._write_legacy(rejections=[
            {"id": "r1", "type": "REFRAME", "model_quote": "m", "user_substitute": "u"},
        ])
        assert _migrate_legacy_preference_stores() == 1
        # Second run recovers nothing — the id is already in the ledger.
        assert _migrate_legacy_preference_stores() == 0
        assert len(load_preference_acts()) == 1

    def test_noop_when_no_legacy_files(self):
        from trinity_local.me.preference_acts import _migrate_legacy_preference_stores
        assert _migrate_legacy_preference_stores() == 0

    def test_skips_when_ledger_already_populated(self):
        """#316 anti-dilution: the recovery is one-time BY CONSTRUCTION — it
        exists only to fill an EMPTY ledger for a pre-#209 upgrade. The legacy
        stores are never deleted, so once the ledger holds ANY act, re-reading
        them re-injects STALE old-prompt rejections on every call (a fresh `lens`
        re-extraction makes new ids → no dedup → the ledger grew 120→190 the
        instant eval-build ran after a clean re-extraction, diluting the eval
        with the exact noise the re-extraction removed). A populated ledger is
        the source of truth: migration must be a NO-OP that leaves it untouched."""
        from trinity_local.me.preference_acts import (
            MODEL_MISS,
            PreferenceAct,
            _migrate_legacy_preference_stores,
            load_preference_acts,
            save_preference_acts,
        )
        save_preference_acts([PreferenceAct(
            id="fresh", trigger=MODEL_MISS, privileged="FRESH", sacrificed="x")])
        # Legacy stores still on disk (never cleaned up) with DIFFERENT ids —
        # the dilution vector. The gate must refuse them on a populated ledger.
        self._write_legacy(rejections=[
            {"id": "r2", "type": "REDIRECT", "model_quote": "m2", "user_substitute": "u2"},
        ])
        assert _migrate_legacy_preference_stores() == 0  # populated → no-op
        acts = {a.id: a for a in load_preference_acts()}
        assert set(acts) == {"fresh"}  # legacy r2 NOT re-injected
        assert acts["fresh"].privileged == "FRESH"  # ledger untouched


class TestRenderUnifiedSection:
    def test_preference_acts_render_both_triggers(self):
        from trinity_local.me.pipeline import render_me_markdown
        from trinity_local.me.pair_mining import LensPair
        acts = [
            PreferenceAct(id="r1", trigger=MODEL_MISS, privileged="just do Y",
                          sacrificed="do X elaborately", kind="REFRAME", why="reframed"),
            PreferenceAct(id="d1", trigger=SELF_EXPRESSED, privileged="velocity",
                          sacrificed="polish", kind="satisfaction"),
        ]
        out = render_me_markdown(
            [LensPair(pole_a="a", pole_b="b", failure_a="", failure_b="", verdict="accepted")],
            [], None, None, acts,
        )
        assert "## Preference acts" in out
        assert "Model-miss" in out and "REFRAME" in out
        assert "Self-expressed" in out
        assert "**velocity** > polish" in out
        # The legacy heading is replaced when preference_acts are present.
        assert "## Implicit rejections" not in out

    def test_legacy_rejections_path_still_works(self):
        # Back-compat: no preference_acts → old section renders.
        from trinity_local.me.pipeline import render_me_markdown
        from trinity_local.me.pair_mining import LensPair
        rej = [RejectionSignal(id="r1", type="REFRAME", model_quote="m", user_substitute="u", why_signal="w")]
        out = render_me_markdown(
            [LensPair(pole_a="a", pole_b="b", failure_a="", failure_b="", verdict="accepted")],
            [], rej,
        )
        assert "## Implicit rejections" in out
        assert "## Preference acts" not in out
