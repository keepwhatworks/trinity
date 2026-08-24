"""Guards for core_gate — every core write must EARN its place.

Trinity's #1 recurring bug is a green over degenerate data, so the load-bearing
tests here are the REFUSALS: the gate must decline to admit when it cannot score,
and must decline to admit a candidate that prices held-out text worse. Each is
mutation-proven — delete the mechanism and the test reds.
"""
from __future__ import annotations

import pytest

from trinity_local import core_gate
from trinity_local.distill import write_core
from trinity_local.state_paths import core_path


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _pin_zlib_ruler(monkeypatch, request):
    """Behavioural tests pin the ZLIB ruler.

    core_gate prefers the neural ruler when a local model is cached, which makes
    the suite slow (16.7s vs 4.5s) and MACHINE-DEPENDENT — a contributor without
    the model would exercise a different scorer than CI. Verdict logic is what
    these tests guard, and it is ruler-agnostic by construction (candidate and
    incumbent are always scored with the same ruler). The dispatcher itself is
    tested explicitly in TestRulerDispatch, which opts out of this fixture.
    """
    if request.node.get_closest_marker("real_ruler"):
        return
    monkeypatch.setattr(core_gate, "_neural_available", lambda: False)


def _heldout(n: int = 60) -> list[str]:
    """Distinctive, repetitive held-out text so a dictionary containing it wins."""
    return [f"you compress over convenience and ship the smallest verifiable thing {i}"
            for i in range(n)]


class TestEarnsItsPlace:
    def test_a_better_candidate_is_NOT_admitted_in_production(self, isolated_home):
        """The shipped contract since res_079: a live incumbent is never replaced.

        The bits ruler ranks LENGTH, so "better" is not a quality claim — the
        same admission path put an OAuth error in core.md on 2026-08-18 and a
        session-limit notice on 2026-08-24, both because they were short.
        """
        held = _heldout()
        core_path().write_text("Unrelated boilerplate about aardvarks.\n", encoding="utf-8")
        good = "you compress over convenience and ship the smallest verifiable thing"
        v = core_gate.propose_core(good, heldout=held)
        assert not v.admitted and "LENGTH-CONFOUNDED" in v.reason
        assert v.archived, "the candidate must stay recoverable for human review"

    def test_better_candidate_is_admitted(self, isolated_home, monkeypatch):
        """The DORMANT path: what the gate does once a ruler earns trust again.

        Kept alive so the mechanism does not rot while it waits. It re-activates
        by clearing LENGTH_CONFOUNDED_RULER, which requires a ruler that beats a
        length-matched control (res_079's acceptance test).
        """
        monkeypatch.setattr(core_gate, "LENGTH_CONFOUNDED_RULER", False)
        held = _heldout()
        core_path().write_text("Unrelated boilerplate about aardvarks.\n", encoding="utf-8")
        good = "you compress over convenience and ship the smallest verifiable thing"
        v = core_gate.propose_core(good, heldout=held)
        assert v.admitted, v.reason
        assert v.candidate_bits is not None and v.incumbent_bits is not None
        assert v.candidate_bits < v.incumbent_bits

    def test_worse_candidate_is_REFUSED_and_core_unchanged(self, isolated_home):
        """The core case: a degradation must not be able to overwrite the core."""
        held = _heldout()
        strong = "you compress over convenience and ship the smallest verifiable thing"
        core_path().write_text(strong + "\n", encoding="utf-8")
        v = core_gate.propose_core("Assorted remarks concerning aardvarks.", heldout=held)
        assert not v.admitted, "a candidate that prices held-out text worse must be refused"
        assert "frozen" in v.reason or "degradation" in v.reason
        assert core_path().read_text(encoding="utf-8").strip() == strong

    def test_refuses_when_heldout_too_thin(self, isolated_home):
        """FAIL CLOSED — unscorable must keep the incumbent, not admit blindly."""
        core_path().write_text("Existing manifesto.\n", encoding="utf-8")
        v = core_gate.propose_core("Anything at all.", heldout=["one", "two"])
        assert not v.admitted
        assert "thin" in v.reason
        assert core_path().read_text(encoding="utf-8").strip() == "Existing manifesto."

    def test_first_write_is_admitted_without_an_incumbent(self, isolated_home):
        v = core_gate.propose_core("The very first core.", heldout=_heldout())
        assert v.admitted and "first write" in v.reason

    def test_empty_candidate_never_admitted(self, isolated_home):
        v = core_gate.propose_core("   ", heldout=_heldout())
        assert not v.admitted

    def test_every_candidate_is_archived_even_when_rejected(self, isolated_home):
        """Nothing is destroyed. The pre-gate behaviour kept one file and no history,
        so a bad distill was unrecoverable."""
        held = _heldout()
        strong = "you compress over convenience and ship the smallest verifiable thing"
        core_path().write_text(strong + "\n", encoding="utf-8")
        v = core_gate.propose_core("Aardvark musings.", heldout=held)
        assert not v.admitted
        assert v.archived is not None
        files = list(core_gate.history_dir().glob("core-*"))
        assert files, "a rejected candidate must still be archived"
        assert any("rejected" in f.name for f in files)


class TestWriteCoreIsGated:
    def test_write_core_does_not_clobber_with_a_worse_candidate(self, isolated_home):
        strong = "you compress over convenience and ship the smallest verifiable thing"
        core_path().write_text(strong + "\n", encoding="utf-8")
        seed = _heldout()
        import trinity_local.core_gate as cg
        orig = cg.heldout_texts
        cg.heldout_texts = lambda limit=400: seed          # type: ignore[assignment]
        try:
            write_core("Aardvark musings, entirely unrelated.")
        finally:
            cg.heldout_texts = orig                        # type: ignore[assignment]
        assert core_path().read_text(encoding="utf-8").strip() == strong

    def test_gated_false_restores_the_raw_overwrite(self, isolated_home):
        core_path().write_text("old\n", encoding="utf-8")
        write_core("new", gated=False)
        assert core_path().read_text(encoding="utf-8").strip() == "new"


class TestItemValues:
    def test_dead_item_does_not_earn_its_place(self, isolated_home):
        """An item whose removal costs nothing has not earned its place."""
        held = _heldout()
        items = ["you compress over convenience and ship the smallest verifiable thing",
                 "aardvarks are nocturnal burrowing mammals of southern Africa"]
        vals = core_gate.item_values(items, heldout=held)
        assert len(vals) == 2
        by_head = {v["index"]: v for v in vals}
        assert by_head[0]["earns_place"], "the item matching held-out text must pay"
        assert by_head[0]["bits_saved"] > by_head[1]["bits_saved"]

    def test_returns_empty_when_unscorable(self, isolated_home):
        assert core_gate.item_values(["a", "b"], heldout=["x"]) == []


class TestNoFounderLockViolation:
    def test_does_not_touch_the_regression_gate_flag(self):
        """TRINITY_REGRESSION_GATE is founder-locked default-OFF and governs a
        DIFFERENT surface (lens-tension reconcile). This module must not read,
        set, or arm it."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(core_gate))
        # Strip docstrings: the module docstring NAMES the locked flag on purpose,
        # to document the boundary. What must be absent is executable use of it.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and ast.get_docstring(node):
                node.body = node.body[1:]
        code = ast.unparse(tree)
        assert "TRINITY_REGRESSION_GATE" not in code, "must not read or arm the locked flag"
        assert "environ" not in code and "getenv" not in code, "no env flags on this path"

    def test_scores_against_transcripts_not_council_outcomes(self):
        """Founder-lock: the lens learns from transcripts ONLY. The scoring target
        must be prompt nodes, never council outcomes."""
        import inspect
        src = inspect.getsource(core_gate.heldout_texts)
        assert "prompt_nodes" in src
        assert "council" not in src.lower()


@pytest.mark.parametrize("mutation", ["tolerance", "thin_guard"])
def test_mutation_proof(isolated_home, monkeypatch, mutation):
    """Delete the mechanism -> the guard must RED. A guard that survives its own
    deletion is decoration."""
    held = _heldout()
    strong = "you compress over convenience and ship the smallest verifiable thing"
    core_path().write_text(strong + "\n", encoding="utf-8")

    if mutation == "tolerance":
        # A huge tolerance is the mutation: degradations would sail through.
        monkeypatch.setattr(core_gate, "TOLERANCE_BITS", 10 ** 9)
        # exercised on the dormant path — with the production fail-closed on,
        # NOTHING is admitted and the tolerance bar would be untestable
        monkeypatch.setattr(core_gate, "LENGTH_CONFOUNDED_RULER", False)
        v = core_gate.propose_core("Aardvark musings.", heldout=held)
        assert v.admitted, "mutation should admit — proving the real bar is what refuses"
    else:
        # Dropping the thin-sample floor is the mutation: unscorable input would score.
        monkeypatch.setattr(core_gate, "MIN_HELDOUT", 0)
        v = core_gate.propose_core("Anything.", heldout=["one", "two"])
        assert "thin" not in v.reason, "mutation should bypass the thin-sample refusal"


class TestRulerDispatch:
    """The dispatcher is the part the pinning fixture hides, so test it directly."""

    def test_prefers_neural_when_available(self, isolated_home, monkeypatch):
        calls = []
        monkeypatch.setattr(core_gate, "_neural_available", lambda: True)
        monkeypatch.setattr(core_gate, "_neural_bits",
                            lambda t, a: calls.append("neural") or 123.0)
        bits, which = core_gate.score_bits(["x" * 50], b"artifact")
        assert which == "neural" and bits == 123.0 and calls == ["neural"]

    def test_degrades_to_zlib_when_unavailable(self, isolated_home, monkeypatch):
        monkeypatch.setattr(core_gate, "_neural_available", lambda: False)
        _, which = core_gate.score_bits(["x" * 50], b"artifact")
        assert which == "zlib"

    def test_a_broken_neural_stack_degrades_rather_than_blocking(self, isolated_home,
                                                                 monkeypatch):
        """A build must never fail because the local model is broken."""
        monkeypatch.setattr(core_gate, "_neural_available", lambda: True)
        def boom(t, a):
            raise RuntimeError("model file truncated")
        monkeypatch.setattr(core_gate, "_neural_bits", boom)
        bits, which = core_gate.score_bits(["x" * 50], b"artifact")
        assert which == "zlib" and bits > 0

    def test_zlib_arm_returns_BITS_not_bytes(self, isolated_home, monkeypatch):
        """The 8x unit bug (bytes reported as bits) cost a factual claim on
        2026-08-10. score_bits must return bits from both arms."""
        monkeypatch.setattr(core_gate, "_neural_available", lambda: False)
        texts = ["hello world " * 20]
        bits, _ = core_gate.score_bits(texts, None)
        raw_bytes = core_gate._zlib_bytes(texts, None)
        assert bits == raw_bytes * 8

    def test_never_downloads_when_model_absent(self, isolated_home, monkeypatch):
        """HF_HUB_OFFLINE is pinned at startup (commitment #5); a missing model
        must degrade, never reach the network."""
        monkeypatch.setattr(core_gate.pathlib.Path, "home", staticmethod(lambda: isolated_home))
        assert core_gate._neural_available() is False
