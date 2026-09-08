"""`trinity-local verify` — the pre-deploy gate's CLI surface.

Kernel path only (no panel dispatch in the suite). The rule itself is pinned in
test_verify_rule.py; this pins the verb's contract: what it refuses, what it
exits with, and that the kernel actually ran the command in --cwd.
"""
from __future__ import annotations

import argparse
import inspect
import json

import pytest

from trinity_local import verify as V
from trinity_local.commands import verify as cmd


def _ns(tmp_path, criteria, diff="--- a\n+++ b\n", **kw):
    d = tmp_path / "d.patch"; d.write_text(diff)
    c = tmp_path / "c.json"; c.write_text(json.dumps(criteria))
    ns = argparse.Namespace(diff=str(d), criteria=str(c), context=None, cwd=str(tmp_path),
                            members="claude,codex,antigravity", exclude_lab=None,
                            no_panel=True, no_tests=False, effort=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_engine_accepts_a_per_call_effort():
    assert "effort" in inspect.signature(V.verify).parameters


class TestRefusals:
    def test_non_list_criteria_refused(self, tmp_path, capsys):
        assert cmd.handle_verify(_ns(tmp_path, {"nope": 1})) == 1
        assert "refused" in capsys.readouterr().err

    def test_test_criterion_without_command_refused(self, tmp_path, capsys):
        assert cmd.handle_verify(_ns(tmp_path, [{"id": "t", "kind": "test", "statement": "x"}])) == 1
        assert "requires a command" in capsys.readouterr().err

    def test_unknown_kind_refused(self, tmp_path, capsys):
        assert cmd.handle_verify(_ns(tmp_path, [{"id": "t", "kind": "vibe", "statement": "x"}])) == 1
        assert "test|judgment" in capsys.readouterr().err

    def test_acceptance_object_form_accepted(self, tmp_path, capsys):
        rc = cmd.handle_verify(_ns(tmp_path, {"acceptance": [
            {"id": "t", "kind": "test", "statement": "ok", "command": "true"}]}))
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["triage"] == "READ"


class TestKernelPath:
    def test_green_without_panel_is_read_exit_0(self, tmp_path, capsys):
        rc = cmd.handle_verify(_ns(tmp_path, [{"id": "t", "kind": "test", "statement": "ok", "command": "true"}]))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["triage"] == "READ"
        assert out["kernel"]["green"] is True and out["kernel"]["relevance_basis"] == "declared"

    def test_red_is_stop_exit_2(self, tmp_path, capsys):
        rc = cmd.handle_verify(_ns(tmp_path, [{"id": "t", "kind": "test", "statement": "no", "command": "false"}]))
        out = json.loads(capsys.readouterr().out)
        assert rc == 2 and out["triage"] == "STOP"

    def test_command_runs_in_cwd(self, tmp_path, capsys):
        (tmp_path / "marker").write_text("x")
        rc = cmd.handle_verify(_ns(tmp_path, [{"id": "t", "kind": "test", "statement": "sees marker",
                                                "command": "test -f marker"}]))
        assert rc == 0 and json.loads(capsys.readouterr().out)["kernel"]["green"] is True

    def test_non_blocking_red_does_not_stop(self, tmp_path, capsys):
        rc = cmd.handle_verify(_ns(tmp_path, [
            {"id": "a", "kind": "test", "statement": "ok", "command": "true"},
            {"id": "b", "kind": "test", "statement": "advisory", "command": "false", "blocking": False}]))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["kernel"]["green"] is True and out["triage"] == "READ"

    def test_nothing_ran_reads_and_says_so(self, tmp_path, capsys):
        rc = cmd.handle_verify(_ns(tmp_path, [{"id": "j", "kind": "judgment", "statement": "fine"}],
                                   no_tests=True))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["triage"] == "READ" and "nothing vouched" in out["reason"]


class TestVoteParsing:
    @pytest.mark.parametrize("text,expect", [
        ('{"votes": {"a": "PASS", "b": "FAIL"}, "why": ""}', {"a": True, "b": False}),
        ('preamble\n{"votes": {"a": "pass", "b": "FAIL"}}\ntrailer', {"a": True, "b": False}),
        ('{"votes": {"a": "PASS"}}', None),               # missing b
        ('{"votes": {"a": "MAYBE", "b": "FAIL"}}', None), # not a vote
        ('[1, 2, 3]', None),                              # valid JSON, wrong shape
        ('{"votes": "PASS"}', None),                      # votes not a dict
        ('no json here', None),
    ])
    def test_parse(self, text, expect):
        assert V._parse_votes(text, ["a", "b"]) == expect


class TestUsageAccounting:
    """A cost table built from one provider's key shape covers one provider.

    parse_codex_usage emits `total_tokens`; parse_claude_json emits the
    breakdown and no total. The first version asked only for `total_tokens`,
    so every claude read banked None and the hq_106 cost table silently
    covered codex alone.
    """
    def test_codex_shape(self):
        assert V._tokens({"total_tokens": 4899, "cost_usd": None}) == 4899

    def test_claude_breakdown_is_summed(self):
        assert V._tokens({"input_tokens": 2, "output_tokens": 24,
                          "cache_read_tokens": 0, "cache_creation_tokens": 3995,
                          "cost_usd": 0.04}) == 4021

    def test_a_provider_that_reports_nothing_is_none_not_zero(self):
        assert V._tokens({}) is None and V._tokens({"source": "agy_unparsed"}) is None

    def test_partial_breakdown_still_sums(self):
        assert V._tokens({"output_tokens": 24}) == 24

    def test_read_carries_cost(self):
        import dataclasses
        assert "cost_usd" in {f.name for f in dataclasses.fields(V.Read)}
