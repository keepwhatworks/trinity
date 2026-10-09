"""hq_135's score() refuses to read outcomes unless its registered label-quality gate passed
(verify, codex, 2026-10-08: it could otherwise issue PASS/KILL on labels that failed kappa)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not (REPO / "internal" / "experiments").is_dir(), reason="internal/ is not present in the public export"
)


@pytest.fixture
def hq(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "internal" / "experiments"))
    spec = importlib.util.spec_from_file_location("hq135", REPO / "internal" / "experiments" / "hq135_pushback.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "OUT", tmp_path / "out.json")
    monkeypatch.setattr(mod, "_units", lambda: (_ for _ in ()).throw(AssertionError("read units past a failed gate")))
    return mod


@pytest.mark.parametrize("kappa", [None, {"gate": "VOID", "kappa": 0.41, "overlap": 300}])
def test_score_refuses_without_a_passed_kappa_gate(hq, kappa):
    if kappa is not None:
        hq.OUT.write_text(json.dumps({"kappa": kappa}))
    assert hq.score()["gate"]["verdict"] == "VOID"


def test_label_decision_and_critic_feature(hq):
    assert hq.label_of({"Yes": -0.1, "No": -2.0}) == 1
    assert hq.label_of({" no": -0.2, "yes": -3.0}) == 0
    assert hq.label_of({"The": -0.1}) is None
    assert hq.critic_feature({"Yes": -1.0, "The": -0.5, "x": -9.0}) == pytest.approx(-1.0 - (-9.0))
    assert hq.cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1.0)
