"""Characterization guard: the eval leaderboard must keep its honesty disclosures.

The cross-provider leaderboard ("Claude 0.82 > GPT 0.78 > Gemini 0.50 on YOUR
rejections") is only defensible BECAUSE it discloses what makes the numbers
roughly comparable. On the real corpus the rows genuinely mix judges (Claude
judged by codex, GPT + Gemini by claude), so without the disclosure the ranking
would read as more apples-to-apples than it is. The load-bearing disclosures:

  (a) the per-row JUDGE label,
  (b) the rotation methodology ("a model never grades itself"),
  (c) the mixed-eval-set warning (rows must share an eval set to be comparable),
  (d) the excluded-runs note (means are over completed items only).

Browser-verified 2026-06-02 that these render (inside the 2026-05-21 demoted
<details> — the eval card is collapsed-by-default, so the wedge is a retention
surface, not a hero). This pins the disclosure copy so a future "simplify the
leaderboard" refactor can't silently strip it and leave a bare, misleadingly-
clean ranking. Mutation-verified: deleting any disclosure reds its test.
"""
from __future__ import annotations

from trinity_local.launchpad_template import render_launchpad_html


def _html() -> str:
    # The disclosure copy is static template text inside the leaderboard block,
    # always present in the rendered HTML regardless of page_data (v-if hides at
    # runtime, not at render) — so an empty page_data is enough to pin presence.
    return render_launchpad_html(page_data={})


def test_per_row_judge_label_present():
    """Each leaderboard row names its judge — the rows mix judges on real data."""
    assert "judge:" in _html()
    assert "row.judge" in _html()


def test_judge_rotation_methodology_disclosed():
    assert "never grades itself" in _html()


def test_mixed_eval_set_warning_present():
    assert "mixed_eval_sets" in _html()


def test_excluded_runs_disclosure_present():
    assert "excluded_runs" in _html()


def test_corpus_first_framing_not_global_leaderboard():
    """The wedge is 'strongest on YOUR rejections', explicitly NOT a global
    benchmark claim — dropping that framing would overclaim."""
    html = _html()
    assert "YOUR rejections" in html or "your rejections" in html.lower()
