"""Tier-2 eval trust gate — SET composition guards.

The green-gate discipline (#35) says a degenerate-data test must assert the green
is REFUSED. These do exactly that for set composition: a skewed / concentrated /
thin eval set must be FLAGGED, and only a balanced, diverse set passes.

Pure-data (no judge, no real corpus) so it runs anywhere and is deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass

from trinity_local.evals.composition_floor import (
    MAX_AXIS_SHARE,
    MIN_AXIS_N,
    MAX_THREAD_SHARE,
    MIN_THREADS,
    evaluate_composition,
)


@dataclass
class _Item:
    rejection_type: str
    prompt_id: str


def _balanced_set():
    """A set that should PASS: 4 axes near-even, many distinct threads, no thread
    over the concentration cap."""
    axes = ["REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING"]
    items = []
    for t in range(20):  # 20 threads × 4 axes = 80 items, 25% each axis, 5% per thread
        for ax in axes:
            items.append(_Item(rejection_type=ax, prompt_id=f"p_{t}_{ax}"))
    thread_map = {it.prompt_id: f"tx_{it.prompt_id.split('_')[1]}" for it in items}
    return items, thread_map


def test_balanced_set_passes():
    items, tmap = _balanced_set()
    v = evaluate_composition(items, prompt_id_to_thread=tmap)
    assert v.balanced, v.violations
    assert v.axis_share <= MAX_AXIS_SHARE
    assert all(v.reportable_axes.values())
    assert v.n_threads >= MIN_THREADS


def test_axis_imbalance_is_refused():
    # 90% REFRAME → over the share cap → must be flagged.
    items = [_Item("REFRAME", f"p{i}") for i in range(90)]
    items += [_Item("REDIRECT", f"q{i}") for i in range(10)]
    tmap = {it.prompt_id: f"tx{i}" for i, it in enumerate(items)}  # all distinct threads
    v = evaluate_composition(items, prompt_id_to_thread=tmap)
    assert not v.balanced
    assert v.dominant_axis == "REFRAME"
    assert any("axis imbalance" in s for s in v.violations), v.violations


def test_thin_axis_is_not_reportable():
    # COMPRESSION with 2 items must be marked non-reportable (the #281 live bug).
    items = [_Item("REFRAME", f"p{i}") for i in range(30)]
    items += [_Item("COMPRESSION", f"c{i}") for i in range(2)]
    tmap = {it.prompt_id: f"tx{i}" for i, it in enumerate(items)}
    v = evaluate_composition(items, prompt_id_to_thread=tmap)
    assert v.reportable_axes.get("COMPRESSION") is False
    assert v.reportable_axes.get("REFRAME") is True
    assert any("thin ax" in s.lower() and "COMPRESSION" in s for s in v.violations), v.violations
    assert MIN_AXIS_N == 5  # pin the pre-registered floor so a silent loosening trips the test


def test_thread_concentration_is_refused():
    # One transcript supplies 40% of items → over the concentration cap.
    items = [_Item("REFRAME", f"a{i}") for i in range(40)]  # all one thread below
    items += [_Item("REDIRECT", f"b{i}") for i in range(30)]
    items += [_Item("SHARPENING", f"c{i}") for i in range(30)]
    tmap = {}
    for it in items[:40]:
        tmap[it.prompt_id] = "tx_hot"          # 40/100 from one transcript
    for i, it in enumerate(items[40:]):
        tmap[it.prompt_id] = f"tx_{i}"
    v = evaluate_composition(items, prompt_id_to_thread=tmap)
    assert not v.balanced
    assert v.dominant_thread == "tx_hot"
    assert v.thread_share > MAX_THREAD_SHARE
    assert any("thread concentration" in s for s in v.violations), v.violations


def test_too_few_threads_is_refused():
    # Balanced axes but only 3 distinct transcripts → source too narrow.
    axes = ["REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING"]
    items = [_Item(axes[i % 4], f"p{i}") for i in range(40)]
    tmap = {it.prompt_id: f"tx_{i % 3}" for i, it in enumerate(items)}  # 3 threads
    v = evaluate_composition(items, prompt_id_to_thread=tmap)
    assert v.n_threads == 3
    assert any("source too narrow" in s for s in v.violations), v.violations
    assert MIN_THREADS == 5  # pin the floor


def test_empty_set_is_refused():
    v = evaluate_composition([], prompt_id_to_thread={})
    assert not v.balanced
    assert any("empty" in s for s in v.violations)


def test_thread_checks_degrade_without_a_map():
    # No thread map → axis checks still run, thread checks silently skipped (no crash).
    items = [_Item("REFRAME", f"p{i}") for i in range(90)] + [_Item("REDIRECT", f"q{i}") for i in range(10)]
    v = evaluate_composition(items)  # no prompt_id_to_thread
    assert any("axis imbalance" in s for s in v.violations)  # axis check fired
    assert v.n_threads == 0  # thread check gracefully skipped
