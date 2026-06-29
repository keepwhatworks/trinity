"""`providers.result_hard_failed` — the shared hard-failure predicate the council
member dispatch + the eval judge dispatch use (each previously open-coded it).

The crux this guards: it is LENIENT (rc != 0 AND no stdout) and is deliberately
NOT the inverse of the CONSERVATIVE `_result_is_usable` chairman gate (rc == 0 AND
stdout). A nonzero exit that still printed real output is a hard failure for the
chairman (try the next chair) but NOT for a member/judge (use the stdout). Pinning
both keeps the two resilience models from being accidentally merged.
"""
from __future__ import annotations

from types import SimpleNamespace

from trinity_local.providers import _result_is_usable, result_hard_failed


def _r(returncode, stdout):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_clean_success_is_not_a_hard_failure():
    assert result_hard_failed(_r(0, "an answer")) is False
    assert _result_is_usable(_r(0, "an answer")) is True


def test_nonzero_with_empty_stdout_is_a_hard_failure():
    r = _r(1, "")  # rate-limited CLI: nonzero + nothing on stdout
    assert result_hard_failed(r) is True
    assert _result_is_usable(r) is False


def test_nonzero_WITH_stdout_is_lenient_not_a_hard_failure():
    # The load-bearing distinction: member/judge dispatch USES this stdout...
    r = _r(1, "a real (if exit-flagged) answer")
    assert result_hard_failed(r) is False
    # ...but the chairman gate is conservative and falls through to the next chair.
    assert _result_is_usable(r) is False


def test_zero_with_empty_stdout_is_not_hard_failed_but_not_usable():
    r = _r(0, "   ")  # succeeded but produced only whitespace
    assert result_hard_failed(r) is False  # lenient: rc==0, so not a HARD failure
    assert _result_is_usable(r) is False   # conservative: no real stdout


def test_thin_test_double_without_returncode_reads_as_success():
    # A SimpleNamespace judge double (no `returncode`) must not be flagged failed —
    # the getattr default is why the eval scorer could swap in this shared helper.
    assert result_hard_failed(SimpleNamespace(stdout="ok")) is False
