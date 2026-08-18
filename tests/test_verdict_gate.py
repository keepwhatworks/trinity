"""A verdict may not be published when the opposite answer was unreachable.

Three instrument failures in one evening shared one shape: a rate read off an
aggregate whose units were never inspected.

  res_058  15% compile rate — checks were scanning tests/, internal/experiments/
           (docstrings quoting the very defects) and plugins/ (a generated copy
           of src/). Re-scored in scope: 65%.
  res_059  0/29 generality, about to ship as KILL. The sample was drawn for
           DIVERSITY — 0 of 153 pairs shared even two content words — so a
           PERFECTLY general detector also scores 0.
  res_060  "every tension traces to one provider", from a join that silently
           dropped 21 of 80 evidence ids.

And it predates tonight: res_035's admissibility bar required PASS = baseline +
15pp, arithmetically impossible once the baseline passed 85%.

`gated_verdict` refuses in exactly these cases. These tests pin the refusals,
because a gate that cannot refuse is the thing it is guarding against.

Mutation-proven 2026-08-18: making `reachable` always return (True, "") REDs
every refusal test; dropping the `not scoreable` branch REDs the empty case.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "verdict_gate", REPO / "internal" / "experiments" / "verdict_gate.py")
vg = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: verdict_gate uses @dataclass under
# `from __future__ import annotations`, so every annotation is a string and
# dataclasses resolves them via sys.modules[cls.__module__]. Unregistered, that
# lookup returns None and the module fails to exec with a bare AttributeError
# that names neither the module nor the cause.
sys.modules["verdict_gate"] = vg
_spec.loader.exec_module(vg)

ALWAYS = lambda _u: (True, "")            # noqa: E731
SCOREABLE = lambda u: u.get("scoreable", True)  # noqa: E731
OK = lambda u: u.get("ok", False)          # noqa: E731


class TestItRefusesWhenTheOppositeWasImpossible:
    def test_generality_with_no_repeated_class_is_VOID_not_KILL(self):
        """res_059 exactly: 20 units, 20 distinct classes, zero transfer possible."""
        units = [{"cls": f"c{i}", "ok": False} for i in range(20)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.25, kill_bar=0.10, provenance="measured",
                             reachable=vg.needs_distinct_classes(lambda u: u["cls"]))
        assert v.verdict == "VOID", "a 0% that was structurally forced is not a KILL"
        assert "unobservable" in v.reason or "UNREACHABLE" in v.reason

    def test_the_same_data_with_a_repeated_class_is_scoreable(self):
        """The gate must not refuse everything — a refusal that always fires is
        as useless as a verdict that never does."""
        units = [{"cls": "same", "ok": False}, {"cls": "same", "ok": True}]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.25, kill_bar=0.10, provenance="measured",
                             reachable=vg.needs_distinct_classes(lambda u: u["cls"]))
        assert v.verdict != "VOID" and v.scored == 2

    def test_an_unsatisfiable_pass_bar_is_VOID(self):
        """res_035: PASS required baseline+15pp once the baseline passed 85%."""
        units = [{"ok": True} for _ in range(40)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=1.09, kill_bar=0.10, provenance="measured",
                             reachable=vg.bars_are_satisfiable(1.09, 1.0))
        assert v.verdict == "VOID" and "ceiling" in v.reason

    def test_nothing_scoreable_is_VOID_not_a_zero(self):
        """'Nothing ran' and 'everything failed' must never share a bucket."""
        units = [{"scoreable": False} for _ in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.5, kill_bar=0.2, provenance="measured", reachable=ALWAYS)
        assert v.verdict == "VOID" and "nothing ran" in v.reason


class TestUnitsTravelWithTheVerdict:
    def test_every_verdict_carries_its_rows_and_composition(self):
        """A rate without its rows cannot be sampled afterwards — and sampling
        afterwards is what caught all three failures."""
        units = [{"ok": True, "verdict": "COMPILED"},
                 {"ok": False, "verdict": "fires-on-both"},
                 {"ok": False, "scoreable": False, "verdict": "never-ran"}]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.5, kill_bar=0.2, provenance="measured", reachable=ALWAYS)
        assert len(v.rows) == 3, "all units travel, including excluded ones"
        c = v.composition
        assert c["_units_total"] == 3 and c["_scoreable"] == 2 and c["_excluded"] == 1
        assert c["COMPILED"] == 1 and c["fires-on-both"] == 1

    def test_excluded_units_are_visible_not_dropped(self):
        """res_060's shape: 21 of 80 ids resolved nowhere and the denominator
        never said so."""
        units = [{"ok": True} for _ in range(5)] + [{"scoreable": False} for _ in range(21)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.5, kill_bar=0.2, provenance="measured", reachable=ALWAYS)
        assert v.scored == 5 and v.composition["_excluded"] == 21
        assert len(v.rows) == 26


class TestTheOrdinaryVerdictsStillWork:
    def test_pass(self):
        units = [{"ok": i < 8} for i in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="measured", reachable=ALWAYS)
        assert v.verdict == "PASS" and v.rate == 0.8

    def test_kill(self):
        units = [{"ok": i < 1} for i in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="measured", reachable=ALWAYS)
        assert v.verdict == "KILL"

    def test_inconclusive(self):
        units = [{"ok": i < 4} for i in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="measured", reachable=ALWAYS)
        assert v.verdict == "INCONCLUSIVE"


class TestTheGateGotItsOwnMotivatingCaseWrong:
    """`needs_distinct_classes` counted RE-MEASUREMENTS as repeated classes.

    Run against the 29 compiled checks that produced res_059, it reported the
    class ('count','count') as repeated and returned KILL. Those two rows were
    the SAME COMMIT scored twice — once by the presence-only instrument, once by
    the absence-capable one. One unit measured twice, not two instances of a
    class. The gate built to refuse unreachable verdicts published one, because
    its key did not know units could be duplicated.

    Clause 2 is only as good as its key, and keys need sampling too.
    """

    def test_two_rows_for_the_SAME_unit_are_not_a_repeated_class(self):
        units = [{"cls": "dup", "uid": "sha1", "ok": False},
                 {"cls": "dup", "uid": "sha1", "ok": False}]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.25, kill_bar=0.10, provenance="measured",
                             reachable=vg.needs_distinct_classes(
                                 lambda u: u["cls"], unit_id=lambda u: u["uid"]))
        assert v.verdict == "VOID", (
            "the same unit measured twice is a re-measurement, not evidence that "
            "a class recurs")

    def test_the_same_class_across_DISTINCT_units_is_reachable(self):
        units = [{"cls": "dup", "uid": "sha1", "ok": False},
                 {"cls": "dup", "uid": "sha2", "ok": True}]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.25, kill_bar=0.10, provenance="measured",
                             reachable=vg.needs_distinct_classes(
                                 lambda u: u["cls"], unit_id=lambda u: u["uid"]))
        assert v.verdict != "VOID" and v.scored == 2

    def test_without_unit_id_the_old_permissive_behaviour_is_preserved(self):
        """Not every sample can be duplicated; the clause is opt-in on purpose,
        and its absence must not silently change the meaning of existing calls."""
        units = [{"cls": "dup", "ok": False}, {"cls": "dup", "ok": True}]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.25, kill_bar=0.10, provenance="measured",
                             reachable=vg.needs_distinct_classes(lambda u: u["cls"]))
        assert v.verdict != "VOID"


class TestSyntheticUnitsCanNeverPass:
    """Hand-built units show the code RUNS. They never show a claim HOLDS.

    On 2026-08-18 a cache fingerprint was declared "verified" off three
    hand-constructed namespaces, and an effort rotation "verified to produce both
    levels" off six samples. Both shipped as done in the same message. Re-measured
    properly, the rotation held (49.9/50.1 over 2,000 ids, 424/416 over the 840
    real council ids) and the fingerprint had never been run against a real
    cluster at all.

    The gate cannot tell a real unit from a fake one. It CAN refuse to let a fake
    one wear the word PASS, which is the part that misleads.

    Mutation-proven 2026-08-18: dropping the synthetic branch REDs
    test_synthetic_tops_out_at_smoke_ok.
    """

    def test_synthetic_tops_out_at_smoke_ok(self):
        units = [{"ok": True} for _ in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="synthetic",
                             reachable=ALWAYS)
        assert v.verdict == "SMOKE-OK", "synthetic units must never read as PASS"
        assert "not evidence about the real system" in v.reason

    def test_the_same_units_measured_do_pass(self):
        units = [{"ok": True} for _ in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="measured",
                             reachable=ALWAYS)
        assert v.verdict == "PASS"

    def test_synthetic_can_still_KILL(self):
        """A smoke run that fails is still a real failure — the asymmetry is
        deliberate. Fake units cannot prove something works; they can absolutely
        prove it is broken."""
        units = [{"ok": False} for _ in range(10)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="synthetic",
                             reachable=ALWAYS)
        assert v.verdict == "KILL"

    def test_provenance_is_required_not_defaulted(self):
        """Required because it is exactly the field a hurried author omits."""
        import pytest as _pt
        with _pt.raises(TypeError):
            vg.gated_verdict([{"ok": True}], ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, reachable=ALWAYS)


class TestPowerIsNamedSeparatelyFromReachability:
    """res_065: `needs_both_outcomes_possible(8)` was a sample-size floor wearing
    a reachability check's name, so an underpowered run was refused with "the
    opposite verdict was unreachable" — a claim about the DESIGN when the problem
    was PRECISION. A refusal naming the wrong defect trains the reader to ignore
    refusals."""

    def test_underpowered_says_underpowered(self):
        units = [{"ok": True}, {"ok": False}]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.6, kill_bar=0.3, provenance="measured",
                             min_n=8, reachable=ALWAYS)
        assert v.verdict == "VOID" and "UNDERPOWERED" in v.reason
        assert "unreachable" not in v.reason.lower(), "wrong defect named"

    def test_unreachable_says_unreachable(self):
        units = [{"cls": f"c{i}", "ok": False} for i in range(20)]
        v = vg.gated_verdict(units, ok_fn=OK, scoreable_fn=SCOREABLE,
                             pass_bar=0.25, kill_bar=0.10, provenance="measured",
                             min_n=1,
                             reachable=vg.needs_distinct_classes(lambda u: u["cls"]))
        assert v.verdict == "VOID" and "UNREACHABLE" in v.reason
        assert "UNDERPOWERED" not in v.reason


class TestEveryVerdictSamplesItsOwnUnits:
    """The sampling rule moved from discipline into the mechanism.

    Looking at raw rows before confirming a result was a step I was supposed to
    remember, and twice did not. The gate now does it unprompted at every exit.
    """

    def _units(self):
        return (
            [{"id": f"src/real_{i}.py", "ok": True, "s": True} for i in range(13)]
            + [{"id": f"src/real_{i}.py", "ok": False, "s": True} for i in range(13, 20)]
            + [{"id": f"internal/experiments/quotes_it_{i}.py", "ok": False, "s": False}
               for i in range(11)]
        )

    def _run(self, capsys):
        v = vg.gated_verdict(
            self._units(), ok_fn=lambda u: u["ok"], scoreable_fn=lambda u: u["s"],
            pass_bar=0.60, kill_bar=0.40, provenance="measured",
            reachable=vg.needs_both_outcomes_possible(5),
            label_fn=lambda u: "scored" if u["s"] else "EXCLUDED",
        )
        return v, capsys.readouterr().err

    def test_the_excluded_rows_print_as_rows_not_as_a_count(self, capsys):
        _, err = self._run(capsys)
        # res_058: the denominator was wrong and its COUNT looked plausible.
        # Only the paths gave it away, so the paths must reach the operator.
        assert "internal/experiments/quotes_it_0.py" in err
        assert "src/real_0.py" in err

    def test_the_sample_fires_without_the_caller_asking_for_it(self, capsys):
        _, err = self._run(capsys)
        assert "sample of the actual units" in err

    def test_it_goes_to_stderr_so_it_cannot_corrupt_a_json_contract(self, capsys):
        self._run(capsys)
        assert capsys.readouterr().out == ""

    def test_bookkeeping_totals_are_not_reported_as_missing_classes(self, capsys):
        _, err = self._run(capsys)
        # An alarm that fires on every healthy call is noise, and noise gets
        # filtered out by the human it was built for.
        assert "composition and units disagree" not in err
        assert "_units_total" not in err

    def test_but_a_REAL_disagreement_still_alarms(self):
        out = vg._exemplars([{"k": "a"}], {"a": 1, "ghost": 4}, lambda u: u["k"])
        assert "composition and units disagree" in out
