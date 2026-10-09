"""hq_139 build-first artifact: AskUserQuestion answers become exact decisions only through the
provenance filter, the ladder is prequential, and the stop rule refuses thin or saturated data
(amd_0344)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "internal" / "experiments"

pytestmark = pytest.mark.skipif(
    not EXP.is_dir(), reason="internal/ is not present in the public export"
)


@pytest.fixture
def m(monkeypatch):
    monkeypatch.syspath_prepend(str(EXP))
    spec = importlib.util.spec_from_file_location("hq139_ask_decisions", EXP / "hq139_ask_decisions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _session(tmp_path: Path, name: str, asks: list[dict]) -> None:
    """One transcript: a founder turn, then each ask and its answer."""
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    lines = [{"type": "user", "timestamp": "2026-06-01T10:00:00Z",
              "message": {"role": "user", "content": "should we ship the parser now or wait"}}]
    for i, a in enumerate(asks):
        tid = f"t{name}{i}"
        q = {"question": a.get("q", f"Q{name}{i}?"), "header": "h", "multiSelect": a.get("multi", False),
             "options": [{"label": lab, "description": ""} for lab in a["labels"]]}
        lines.append({"type": "assistant", "entrypoint": a.get("entry", "cli"), "timestamp": f"2026-06-01T10:{i:02d}:00Z",
                      "message": {"content": [{"type": "tool_use", "id": tid, "name": "AskUserQuestion",
                                               "input": {"questions": [q]}}]}})
        res = {"questions": [q], "answers": {q["question"]: a["answer"]}}
        if a.get("afk"):
            res["afkTimeoutMs"] = 60000
        lines.append({"type": "user", "timestamp": f"2026-06-01T10:{i:02d}:{a.get('latency', 30):02d}Z",
                      "toolUseResult": res,
                      "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})
    (d / f"{name}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_provenance_keeps_only_interactive_human_single_select_answers(m, tmp_path):
    _session(tmp_path, "s1", [
        {"labels": ["Ship now (Recommended)", "Wait"], "answer": "Wait"},
        {"labels": ["A", "B"], "answer": "A", "entry": "sdk-cli"},
        {"labels": ["A", "B"], "answer": "A", "latency": 1},
        {"labels": ["A", "B"], "answer": "A", "afk": True},
        {"labels": ["A", "B"], "answer": "A, B", "multi": True},
        {"labels": ["Keep", "Drop", "Defer"], "answer": "something I typed instead"},
    ])
    units, drops = m.extract(tmp_path)
    assert len(units) == 2
    assert drops == {"not_interactive": 1, "too_fast_or_no_time": 1, "afk_timeout_path": 1, "multi_select": 1}
    first, other = units
    assert first["chosen"] == 1 and first["recommended"] == [1, 0] and first["k"] == 2
    assert other["chosen"] == 3  # a typed answer outside the options is OTHER (index K)


def test_artifact_holds_no_question_or_option_text(m, tmp_path):
    _session(tmp_path, "s1", [{"q": "SECRET question text?", "labels": ["SECRET option", "Other"], "answer": "Other"}])
    units, _ = m.extract(tmp_path)
    blob = json.dumps(units)
    assert "SECRET" not in blob and "parser" not in blob


def test_duplicate_asks_count_once(m, tmp_path):
    _session(tmp_path, "s1", [{"q": "Same?", "labels": ["A", "B"], "answer": "A"}])
    _session(tmp_path, "s2", [{"q": "Same?", "labels": ["A", "B"], "answer": "B"}])
    units, drops = m.extract(tmp_path)
    assert len(units) == 1 and drops["duplicate"] == 1


def test_ladder_is_prequential_and_learns_a_position_habit(m):
    """The first unit is scored with no fit (uniform over K + OTHER); a founder who always takes the
    first option drives the position rung far below uniform."""
    units = [{"k": 3, "chosen": 0, "recommended": [0, 0, 0], "overlap": [0.0, 0.0, 0.0]} for _ in range(120)]
    lad = m.ladder(units)
    assert lad["uniform"]["bits"] == pytest.approx(2.0)
    assert lad["position"]["bits"] < 0.6 * lad["uniform"]["bits"]
    assert lad["position"]["bits_second_half"] < lad["position"]["bits"]


def test_stop_rule_refuses_thin_data(m, tmp_path, monkeypatch):
    _session(tmp_path, "s1", [{"labels": ["A", "B", "C"], "answer": "B"} for _ in range(3)])
    orig = m.extract
    monkeypatch.setattr(m, "extract", lambda: orig(tmp_path))
    out = m.main()
    assert out["stop_rule"] == "STOP"


def test_stop_rule_refuses_a_saturated_ladder(m, monkeypatch):
    units = [{"id": str(i), "session": str(i % 40), "at": f"2026-06-{i % 28 + 1:02d}", "month": "2026-06", "k": 3,
              "chosen": 0, "recommended": [1, 0, 0], "overlap": [0.0, 0.0, 0.0]} for i in range(300)]
    monkeypatch.setattr(m, "extract", lambda: (units, {}))
    out = m.main()
    assert out["units"] == 300 and out["best_content_blind_bits"] <= 0.6
    assert out["stop_rule"] == "STOP" and any("ladder" in r for r in out["stop_reasons"])
