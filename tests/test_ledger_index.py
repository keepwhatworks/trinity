"""The derived index must refuse rather than guess.

Every test here pins a failure that actually happened while building it.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ledger_index",
    Path(__file__).resolve().parent.parent / "internal/experiments/ledger_index.py")
li = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(li)


class TestAmbiguityRefuses:
    """A status whose headline disagrees with its own substance has no answer."""

    @pytest.mark.parametrize("text", [
        "PASS as registered, INCONCLUSIVE in substance — the bar omitted a multiplier",
        "VOID (registered KILL fired, but on a constant predictor)",
        "KILL by the letter of the registered bar (lift +3.3pp < 5pp) — but the PASS stands",
        "INCONCLUSIVE — and the registered branch would have read PASS",
    ])
    def test_two_verdict_tokens_report_contested(self, text):
        _, verdict = li.classify(text)
        assert verdict == "CONTESTED", (
            f"{text!r} carries two verdicts; taking the first is the vote-parser "
            "bug of 2026-09-16 in a new store")

    @pytest.mark.parametrize("text,expected", [
        ("KILL", "KILL"),
        ("KILL — the selector family is closed", "KILL"),
        ("PASS on the corrected run", "PASS"),
        ("INCONCLUSIVE (rho=0.811 in [0.70, 0.90))", "INCONCLUSIVE"),
        ("VOID — harness defective", "VOID"),
        ("held", "PASS"),
        ("failed", "KILL"),
    ])
    def test_unambiguous_rows_still_classify(self, text, expected):
        """Refusal is for CONFLICT, not for qualification. A verdict with prose
        after it is still that verdict."""
        assert li.classify(text)[1] == expected

    def test_synonyms_across_dialects_do_not_contest(self):
        """`held` and PASS say the same thing in two ledgers' dialects. A row
        carrying both is agreeing with itself."""
        assert li.classify("held — PASS on both pre-registered bars")[1] == "PASS"

    def test_empty_refuses_rather_than_defaulting(self):
        assert li.classify("") == ("UNCLASSIFIED", "NONE")
        assert li.classify("   ") == ("UNCLASSIFIED", "NONE")

    def test_unrecognised_prose_is_unclassified_not_open(self):
        """A default of OPEN would manufacture a frontier reading."""
        state, _ = li.classify("Medium stands. A 7.7x speedup for a yield difference")
        assert state == "UNCLASSIFIED"


class TestAxesAreReadSeparately:
    """The defect this file's own first draft shipped."""

    def test_verdict_is_not_shadowed_by_the_lifecycle_field(self, tmp_path):
        """`status:"resolved"` sat in front of the field carrying the real
        prose, so the index printed CONTESTED=0 over a corpus that visibly
        contained contested rows. State and verdict are different axes."""
        p = tmp_path / "q.jsonl"
        p.write_text(json.dumps({
            "id": "hq_x", "at": "2026-08-12", "status": "resolved",
            "verdict": "PASS as registered, INCONCLUSIVE in substance"}) + "\n")
        rows = li.rows_of(p, ("status",), ("verdict",), ("at",))
        assert rows[0]["state"] == "CLOSED"
        assert rows[0]["verdict"] == "CONTESTED", \
            "the verdict field was shadowed by the lifecycle field"

    def test_a_verdict_closes_a_row_the_lifecycle_field_left_open(self, tmp_path):
        p = tmp_path / "q.jsonl"
        p.write_text(json.dumps({"id": "r", "at": "2026-08-01",
                                 "status": "open", "verdict": "KILL"}) + "\n")
        assert li.rows_of(p, ("status",), ("verdict",), ("at",))[0]["state"] == "CLOSED"


class TestUndatedRowsDoNotFakeAge:
    def test_undated_rows_are_flagged_not_sorted_first(self, tmp_path):
        """77 of 112 hypothesis rows carry no `at`. On the first run they
        filled the entire 'oldest still open' list, because "" sorts before
        any date — a neglected-branch reading that was pure sort artifact."""
        p = tmp_path / "q.jsonl"
        p.write_text("\n".join(json.dumps(d) for d in [
            {"id": "undated", "status": "open"},
            {"id": "dated", "status": "open", "at": "2026-07-25"}]) + "\n")
        rows = {r["id"]: r for r in li.rows_of(p, ("status",), ("status",), ("at",))}
        assert rows["undated"]["dated"] is False
        assert rows["dated"]["dated"] is True

    def test_alternate_date_fields_are_read(self, tmp_path):
        p = tmp_path / "q.jsonl"
        p.write_text(json.dumps({"id": "h", "status": "open",
                                 "registered_at": "2026-08-04"}) + "\n")
        r = li.rows_of(p, ("status",), ("status",), ("at", "registered_at"))[0]
        assert r["at"] == "2026-08-04" and r["dated"] is True


class TestTheIndexRefusesToBeVacuous:
    def test_no_rows_is_a_failure_not_a_clean_index(self, monkeypatch, capsys):
        """An index over nothing must not print as an index over everything."""
        monkeypatch.setattr(li, "LEDGERS", [(Path("/nonexistent.jsonl"),
                                             ("status",), ("status",), ("at",))])
        assert li.main() == 1
        assert "refusing" in capsys.readouterr().out.lower()


class TestTheLiveLedgersStillParse:
    def test_real_stores_classify_and_the_enum_is_closed(self):
        """Mutation canary: if a ledger's vocabulary drifts far enough that the
        normalizer stops recognising it, this reds before the index silently
        reports a smaller frontier than exists."""
        allowed_state = {"OPEN", "BLOCKED", "CLOSED", "SUPERSEDED", "UNCLASSIFIED"}
        allowed_verdict = {"PASS", "KILL", "INCONCLUSIVE", "VOID",
                           "NONE", "CONTESTED", "UNCLASSIFIED"}
        seen = 0
        for path, sf, vf, df in li.LEDGERS:
            for r in li.rows_of(path, sf, vf, df):
                seen += 1
                assert r["state"] in allowed_state
                assert r["verdict"] in allowed_verdict
        assert seen > 400, f"only {seen} ledger rows read; a store went missing"

    def test_amendment_ledger_is_fully_classified(self):
        """The control store. 251/251 rows carry a four-value enum, which is
        the whole reason its frontier reading is computable and the prose
        stores' are not. If this ever reds, the enum has drifted."""
        path, sf, vf, df = li.LEDGERS[0]
        rows = li.rows_of(path, sf, vf, df)
        assert rows, "amendment ledger unreadable"
        assert not [r for r in rows if r["state"] == "UNCLASSIFIED"]
