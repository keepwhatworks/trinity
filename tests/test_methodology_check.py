"""The methodology scanner — does it actually catch the bug classes it claims?

Each test plants ONE methodology defect and asserts the scan flags exactly it at
`risk`, and that a clean set stays clean. Plus the privacy invariant: the findings
carry counts + metrics, never raw prompt/response text (they're meant to be
shareable). These are the two bugs found by hand on 2026-06-08 (length confound,
n=12 noise) plus the rest of the class, turned into a regression net.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from trinity_local.evals.methodology_check import audit_eval_methodology


def _item(prompt="some prompt here", gold="a distinct preferred answer",
          axis="REFRAME", basin="b1", provider=None):
    return SimpleNamespace(
        prompt=prompt, user_substitute=gold, rejection_type=axis,
        basin_id=basin, provider_of_rejected_response=provider,
    )


def _pair(human="the human preferred rewrite", model="the model answer that lost",
          human_side="A"):
    a, b = (human, model) if human_side == "A" else (model, human)
    return SimpleNamespace(option_a=a, option_b=b, human_side=human_side, axis="REFRAME")


def _by_name(findings):
    return {f.name: f for f in findings}


def _healthy_set(n=50):
    # Balanced axes, spread basins, distinct golds, length-balanced pairs.
    axes = ["REFRAME", "REDIRECT", "COMPRESSION", "SHARPENING"]
    items = [
        _item(prompt=f"prompt number {i} about topic {i % 9}",
              gold=f"the user's distinct rewrite for item {i} here",
              axis=axes[i % 4], basin=f"b{i % 9}", provider="claude" if i % 2 else "codex")
        for i in range(n)
    ]
    # half human-shorter, half human-longer → no length skew; alternate A/B.
    pairs = []
    for i in range(n):
        if i % 2 == 0:
            p = _pair(human="short take", model="a noticeably longer model response here",
                      human_side="A" if i % 4 == 0 else "B")
        else:
            p = _pair(human="a noticeably longer human rewrite that runs on", model="terse",
                      human_side="A" if i % 4 == 1 else "B")
        pairs.append(p)
    return SimpleNamespace(eval_id="eval_test", items=items), pairs


def test_clean_set_flags_no_risk():
    eval_set, pairs = _healthy_set(50)
    findings = audit_eval_methodology(eval_set, pairs)
    risks = [f for f in findings if f.severity == "risk"]
    assert not risks, f"healthy set should flag no risks, got {[f.name for f in risks]}"
    # The full battery still runs (one finding per check).
    names = _by_name(findings)
    for expected in ("axis_imbalance", "length_confound", "sample_size",
                     "degenerate_gold_leak", "provenance_coverage"):
        assert expected in names


def test_length_confound_is_caught():
    # Human side ALWAYS shorter → a length-prior judge 'agrees' for the wrong reason.
    eval_set, _ = _healthy_set(40)
    pairs = [_pair(human="ok", model="a very much longer losing model answer indeed",
                   human_side="A" if i % 2 == 0 else "B") for i in range(40)]
    f = _by_name(audit_eval_methodology(eval_set, pairs))["length_confound"]
    assert f.severity == "risk"
    assert "40/40" in f.metric and "shorter" in f.metric


def test_axis_imbalance_is_caught():
    items = [_item(axis="REFRAME") for _ in range(45)] + [_item(axis="REDIRECT") for _ in range(5)]
    eval_set = SimpleNamespace(eval_id="e", items=items)
    _, pairs = _healthy_set(40)
    f = _by_name(audit_eval_methodology(eval_set, pairs))["axis_imbalance"]
    assert f.severity == "risk"  # 90% one axis
    assert "REFRAME" in f.metric


def test_small_n_is_caught():
    eval_set, _ = _healthy_set(40)
    pairs = [_pair(human_side="A" if i % 2 else "B") for i in range(12)]  # the n=12 trap
    f = _by_name(audit_eval_methodology(eval_set, pairs))["sample_size"]
    assert f.severity == "risk"
    assert "n = 12" in f.metric


def test_degenerate_gold_leak_is_caught():
    # prompt == gold → every model 'passes', the item measures nothing (#247).
    items = [_item(prompt="same text", gold="same text") for _ in range(3)] + [_item() for _ in range(40)]
    eval_set = SimpleNamespace(eval_id="e", items=items)
    _, pairs = _healthy_set(40)
    f = _by_name(audit_eval_methodology(eval_set, pairs))["degenerate_gold_leak"]
    assert f.severity == "risk"
    assert "3" in f.metric


def test_findings_leak_no_raw_text():
    # The scan is meant to be shareable: a secret in the data must NOT appear in
    # any finding's metric/detail (counts + metrics only — the privacy invariant).
    secret = "SUPERSECRETPROMPTTOKEN"
    items = [_item(prompt=f"{secret} {i}", gold=f"{secret} gold {i}", basin=f"b{i}") for i in range(40)]
    eval_set = SimpleNamespace(eval_id="e", items=items)
    pairs = [_pair(human=f"{secret} human {i}", model=f"{secret} model {i}",
                   human_side="A" if i % 2 else "B") for i in range(40)]
    blob = json.dumps([f.to_dict() for f in audit_eval_methodology(eval_set, pairs)])
    assert secret not in blob, "methodology findings must not echo raw prompt/response text"
