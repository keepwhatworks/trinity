"""verify's advisory quality angles (founder 2026-10-09: one review pass). The panel also answers
reuse / root-cause / dead-code questions, but only when it runs anyway, never as a blocking vote,
and never as an 'unverified' acceptance criterion."""
from __future__ import annotations

import json

import pytest

from trinity_local import verify as V

J = {"id": "j", "kind": "judgment", "statement": "the change addresses the context", "blocking": True}
T = {"id": "t", "kind": "test", "statement": "tests pass", "command": "true", "blocking": True}


def test_added_only_when_a_panel_would_run():
    assert [d["id"] for d in V.with_quality([T, J])] == ["t", "j", "q_reuse", "q_root", "q_dead"]
    assert V.with_quality([T]) == [T], "a test-only block must not start a panel (extra dispatch)"
    assert V.with_quality([T, J], quality=False) == [T, J]


def test_env_switch_and_caller_ids_win(monkeypatch):
    monkeypatch.setenv(V.QUALITY_FLAG, "0")
    assert V.with_quality([J]) == [J]
    monkeypatch.delenv(V.QUALITY_FLAG)
    own = {"id": "q_root", "kind": "judgment", "statement": "mine", "blocking": True}
    block = V.with_quality([J, own])
    assert [d["id"] for d in block] == ["j", "q_root", "q_reuse", "q_dead"]
    mine = V.Criterion.from_dict(block[1])
    assert mine.blocking and not mine.advisory, "a caller's own q_root stays an acceptance criterion"
    assert V.with_quality([J], run_panel=False) == [J], "--no-panel adds nothing"


@pytest.fixture
def panel(monkeypatch, tmp_path):
    """Two readers that pass the acceptance criterion and fail every quality angle."""
    def fake(name, prompt, cwd, config, effort):
        votes = {"j": "PASS", "q_reuse": "FAIL", "q_root": "FAIL", "q_dead": "FAIL"}
        out = json.dumps({"votes": votes, "why": "duplicated helper"})
        return type("R", (), {"stdout": out, "stderr": "", "returncode": 0, "usage": {}})(), \
            type("P", (), {"model": "m", "effort": None})()
    monkeypatch.setattr(V, "_dispatch", fake)
    cfg = type("C", (), {"providers": {n: type("P", (), {"enabled": True})() for n in V.DEFAULT_PANEL}})()
    return tmp_path, cfg


def test_a_quality_fail_never_changes_the_vote_or_the_unverified_list(panel):
    """MUTATION: make QUALITY_CRITERIA blocking and the readers' `passed` turns False."""
    cwd, cfg = panel
    out = V.verify([J], "diff", "ctx", cwd, run_tests=False, config=cfg, exclude_lab="anthropic")
    reads = out["panel"]["reads"]
    assert reads and all(r["passed"] for r in reads)
    assert all(r["votes"]["q_reuse"] is False for r in reads)
    assert out["unverified"] == ["j"]


def test_the_card_marks_quality_concerns_advisory(panel):
    from trinity_local.commands.verify import render_card
    cwd, cfg = panel
    card = render_card(V.verify([J], "diff", "ctx", cwd, run_tests=False, config=cfg, exclude_lab="anthropic"))
    assert "[q_reuse]" in card and "(advisory: does not change the verdict)" in card
    assert "[j]" not in card.split("drew a concern")[-1]


def test_a_reader_that_skips_the_advisory_votes_still_counts():
    """MUTATION: drop `optional` from _parse_votes and a reader answering only the acceptance
    criterion is thrown away as unparseable."""
    adv = frozenset({"q_reuse", "q_root", "q_dead"})
    out = V._parse_votes('{"votes": {"j": "FAIL"}, "why": "w"}', ["j", "q_reuse", "q_root", "q_dead"], optional=adv)
    assert out == ({"j": False}, "w")
    assert V._parse_votes('{"votes": {"q_reuse": "PASS"}}', ["j", "q_reuse"], optional=adv) is None
