"""The stance-margin admitter ships DORMANT, and armed it admits only decisively.

The measured matrix (res_092/094/096): garbage-vs-good refused 0-1/9, good-vs-
garbage admitted 9/9, good-vs-good a 3-5/9 coin flip. Under the margin rule
(forward >= 8/9 AND mirror <= 1/9) that makes the gate self-healing against a
garbage incumbent and frozen among equals. This file pins the contract:

  * flag OFF (default): byte-identical to the fail-closed gate — nothing admits
  * flag ON + decisive both ways: admits, with bits recorded but not deciding
  * flag ON + coin flip: refuses (freezing among equals)
  * flag ON + one-sided decisiveness: refuses (position bias, not preference)
  * flag ON + judge unreachable: refuses (a missing judge must never admit)
"""
from __future__ import annotations

import pytest

from trinity_local import core_gate


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    core_gate.core_path().parent.mkdir(parents=True, exist_ok=True)
    core_gate.core_path().write_text("the earned incumbent core " * 8)
    # bits ruler says "admit" (the confounded recommendation the flag decides on)
    monkeypatch.setattr(core_gate, "score_bits",
                        lambda texts, art: ((10.0 if art and len(art) < 200 else 9000.0), "neural"))
    return tmp_path


def _judge(fwd, mir):
    """A stance stub returning (wins, scored) per direction."""
    calls = {"n": 0}

    def fake(candidate, incumbent, texts):
        calls["n"] += 1
        k, n = fwd if calls["n"] == 1 else mir
        return (k > n / 2, f"stub prefers the candidate on {k}/{n} held-out prompts")
    return fake


HELD = ["prompt"] * core_gate.MIN_HELDOUT
CAND = "a genuinely better distillation"


class TestDormancy:
    def test_default_is_the_fail_closed_gate_even_with_a_decisive_judge(
            self, home, monkeypatch):
        monkeypatch.setattr(core_gate, "stance_prefers_candidate", _judge((9, 9), (0, 9)))
        v = core_gate.propose_core(CAND, heldout=HELD)
        assert not v.admitted and "LENGTH-CONFOUNDED" in v.reason


class TestArmed:
    def test_decisive_both_ways_admits_with_bits_recorded_not_deciding(
            self, home, monkeypatch):
        monkeypatch.setattr(core_gate, "stance_prefers_candidate", _judge((9, 9), (0, 9)))
        v = core_gate.propose_core(CAND, heldout=HELD, stance_admitter=True)
        assert v.admitted and "stance-margin admission" in v.reason
        assert v.candidate_bits is not None, "bits must stay recorded"

    def test_the_coin_flip_is_refused_so_the_core_never_churns_among_equals(
            self, home, monkeypatch):
        monkeypatch.setattr(core_gate, "stance_prefers_candidate", _judge((4, 9), (4, 9)))
        v = core_gate.propose_core(CAND, heldout=HELD, stance_admitter=True)
        assert not v.admitted

    def test_one_sided_decisiveness_is_position_bias_and_refused(
            self, home, monkeypatch):
        monkeypatch.setattr(core_gate, "stance_prefers_candidate", _judge((9, 9), (5, 9)))
        v = core_gate.propose_core(CAND, heldout=HELD, stance_admitter=True)
        assert not v.admitted

    def test_a_middling_forward_with_a_clean_mirror_is_still_refused(
            self, home, monkeypatch):
        """Isolates the FORWARD bar. fwd=5/9 with mirror=0/9 passes the mirror
        check, so only the 8/9 forward requirement stands between this and an
        admission — the first mutation run proved the coin-flip test alone
        lets the forward bar drop to 4/9 unnoticed (refusal came from the
        mirror). A margin rule is two bars, and each needs its own red."""
        monkeypatch.setattr(core_gate, "stance_prefers_candidate", _judge((5, 9), (0, 9)))
        v = core_gate.propose_core(CAND, heldout=HELD, stance_admitter=True)
        assert not v.admitted

    def test_a_missing_judge_never_admits(self, home, monkeypatch):
        monkeypatch.setattr(core_gate, "stance_prefers_candidate", lambda *a: None)
        v = core_gate.propose_core(CAND, heldout=HELD, stance_admitter=True)
        assert not v.admitted

    def test_provider_errors_are_refused_before_the_judge_is_ever_consulted(
            self, home, monkeypatch):
        monkeypatch.setattr(core_gate, "stance_prefers_candidate",
                            lambda *a: (_ for _ in ()).throw(AssertionError("judge consulted")))
        v = core_gate.propose_core("You've hit your session limit · resets 12am",
                                   heldout=HELD, stance_admitter=True)
        assert not v.admitted and "PROVIDER ERROR" in v.reason


class TestTheFlagIsNotArmedAnywhere:
    def test_shipped_config_does_not_set_it(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for sub in ("src", "scripts"):
            for f in (root / sub).rglob("*"):
                if f.suffix not in {".py", ".sh", ".json", ".toml"} or not f.is_file():
                    continue
                t = f.read_text(errors="replace")
                # arming SHAPES, not prose: the docstring legitimately tells a
                # human how to arm it, which is not the same as arming it
                for shape in ('export TRINITY_STANCE_ADMITTER',
                              'setenv("TRINITY_STANCE_ADMITTER"',
                              'environ["TRINITY_STANCE_ADMITTER"] ='):
                    assert shape not in t, (f, shape)

    def test_core_gate_itself_reads_no_env(self):
        """The module's own hygiene: arming is INJECTED (distill reads the env),
        so core_gate stays flag-free — the same rule its stance_fn follows."""
        import ast, inspect
        tree = ast.parse(inspect.getsource(core_gate))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and ast.get_docstring(node):
                node.body = node.body[1:]
        code = ast.unparse(tree)
        assert "environ" not in code and "getenv" not in code
