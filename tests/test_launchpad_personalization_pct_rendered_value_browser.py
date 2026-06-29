"""Browser guard: the /stats "Who the chairman picks, by task type" routing
cheat-sheet must paint each row's ``Personalization`` cell as the SIGMOID of
that task type's council count — ``round(sigmoid_alpha(n_personal) * 100)`` —
NOT a linear echo of n, not the raw n, not a stale/garbled number.

WHY THIS GUARD EXISTS (the recurring Trinity surface-binding gap, Iter 240):
The ``Personalization`` percentage is a value DERIVED from data by a NON-LINEAR
transform. ``launchpad_data._personal_routing_for_launchpad`` augments the table
with a ``cold_start`` block per task type:

    n_personal = max(provider["n"] for provider in by_task_type[tt])
    alpha = sigmoid_alpha(n_personal)            # 1/(1+e^-((n-5)/2))
    personalization_pct = int(round(alpha * 100))

petite-vue then paints ``{{ coldStartFor(taskType).personalization_pct }}%`` in
the cell (``launchpad_template.py`` ``.benchmark-score``). This is the chairman
picker's ACTUAL personal-vs-global blend weight surfaced to the user — a wrong
number tells a first-timer their pick is more (or less) personalized than the
chairman truly weights it.

THE GAP THIS CLOSES (audited 2026-06-21): NO test — data-layer OR surface —
pins this arithmetic. ``grep sigmoid_alpha tests/`` returns nothing. The ONLY
DOM test that reads this cell (``test_launchpad_routing_cheatsheet_rendered_
order_browser``) injects ``page_data`` with ``personalization_pct`` SEEDED
DIRECTLY (and it seeds 90% for n=20, which is itself ARITHMETICALLY WRONG —
sigmoid_alpha(20) rounds to 100% — proving that test does not care about the
value, only the row ORDER). So the binding ``n_personal -> sigmoid -> painted %``
is exercised by NOTHING. A regression that (a) drops the ``* 100``, (b) replaces
``round`` with bare ``int`` truncation, (c) keys the cell on ``wins`` / ``n`` /
the wrong column, or (d) garbles the Python->embed-JSON serialization would paint
a wrong personalization weight while every existing routing-table test stays
green.

This test drives the LIVE builder (``render_stats_html``, NO ``page_data``) over
a REAL seeded home so the FULL chain runs — council_outcomes ->
compute_personal_routing_table -> n_personal -> sigmoid_alpha -> embed JSON ->
petite-vue paint. The expected percents are computed render-INDEPENDENTLY from
the SAME ``sigmoid_alpha`` source the builder uses, keyed only on the seeded n.

DISCRIMINATING SEED: n = 12 / 8 / 3 -> 97% / 82% / 27%. None is a linear
multiple of n (a ``n*5``-style echo would give 60/40/15) and none equals n or a
seeded constant, so a linear/echo/raw-n regression reds.

MUTATION-PROVEN (against src/trinity_local/launchpad_data.py — the same source
this renders from): change ``int(round(alpha * 100))`` to ``int(alpha * 100)``
(truncation) -> 97% paints as 97 still but 27% (alpha .2689) stays 26 vs round
27 -> reds; or ``int(round(alpha * 10))`` -> 10/8/3 -> reds with the founder
symptom. The order-only sibling test stays GREEN under these (it seeds the value).
"""

from __future__ import annotations

import functools
import http.server
import math
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


# Discriminating council counts per task type. Distinct, all >= 2 (clear the
# n>=2 cheat-sheet floor), chosen so the resulting percents are NON-LINEAR in n
# (97/82/27, not 60/40/15) — a linear or raw-n regression cannot reproduce them.
_SEED_N = {"code_review": 12, "data_analysis": 8, "bug_fix": 3}


def _sigmoid_alpha(n: int) -> float:
    # RENDER-INDEPENDENT recomputation of the SAME curve the builder uses
    # (chairman_picker.PERSONAL_MIDPOINT=5, PERSONAL_STEEPNESS=2.0). Kept inline
    # so the expected value is derived from the math, not read back from render.
    return 1.0 / (1.0 + math.exp(-(n - 5) / 2.0))


# Expected painted percents, computed from the seeded n via the sigmoid — the
# value the cell MUST show. (BITE precondition B: the seed is the discriminating
# one, checked here on the fixture constants, not read from the render.)
_EXPECTED_PCT = {tt: int(round(_sigmoid_alpha(n) * 100)) for tt, n in _SEED_N.items()}


def _assert_discriminating() -> None:
    # The percents must NOT be reproducible by a linear `n * k` echo for any small
    # k, nor equal n itself — otherwise a linear-transform regression could pass.
    assert _EXPECTED_PCT == {"code_review": 97, "data_analysis": 82, "bug_fix": 27}, (
        "fixture drift: the seeded n no longer yields the non-linear 97/82/27 "
        f"percents the discriminating assertion depends on: {_EXPECTED_PCT}"
    )
    # No two percents equal (the per-row assertion can't pass by collision), and
    # none equals its own n (rules out a raw-n paint).
    assert len(set(_EXPECTED_PCT.values())) == len(_EXPECTED_PCT)
    for tt, n in _SEED_N.items():
        assert _EXPECTED_PCT[tt] != n, f"{tt}: pct equals n — not discriminating"


def _seed_home(home: Path) -> None:
    """Write `n` real council_outcomes per task type (claude wins each) so
    compute_personal_routing_table derives the exact n_personal we expect."""
    import os

    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.personal_routing import freeze_routing_to_disk

    idx = 0
    for tt, count in _SEED_N.items():
        for _ in range(count):
            win, ru = "claude", "codex"
            wm, rm = "claude-opus-4-8", "gpt-5.5"
            members = [
                CouncilMemberResult(provider=win, model=wm, output_text=f"Synthetic {win} #{idx}. " * 15),
                CouncilMemberResult(provider=ru, model=rm, output_text=f"Synthetic {ru} #{idx}. " * 15),
            ]
            label = CouncilRoutingLabel(
                winner=win, runner_up=ru, confidence="high", task_type=tt,
                provider_scores={win: {"overall": 0.82}, ru: {"overall": 0.61}},
                agreed_claims=[f"agreed {idx}"], disagreed_claims=[],
            )
            save_council_outcome(CouncilOutcome(
                council_run_id=f"council_p{idx:03d}", bundle_id=f"council_p{idx:03d}",
                task_cluster_id=f"cluster_{tt}", primary_provider=win, primary_model=wm,
                winner_provider=win, winner_model=wm, agreement_score=0.7,
                metadata={"task_text": f"Q{idx} about {tt}?", "task_type": tt},
                member_results=members, synthesis_prompt="Review.",
                synthesis_output=f"Synth {idx}.",
                routing_label=label, created_at=f"2026-06-0{(idx % 8) + 1}T00:00:00+00:00",
            ))
            idx += 1
    freeze_routing_to_disk()


def _read_personalization_cells(html: str, served: Path) -> list[dict]:
    served.mkdir(parents=True, exist_ok=True)
    (served / "stats.html").write_text(html, encoding="utf-8")
    from trinity_local import vendor

    vendor.publish_vendor_files(served)

    Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/stats.html")
            page.wait_for_timeout(900)
            body = page.inner_text("body")
            # BITE precondition A: the value PAINTS (no un-mounted petite-vue leak).
            assert "{{" not in body, "raw petite-vue template leaked on /stats (un-mounted)"
            assert not errs, f"JS errors rendering /stats: {errs[:3]}"
            rows = page.evaluate(
                """() => {
                  const tables=[...document.querySelectorAll('table')];
                  let t=null;
                  for (const tb of tables){
                    const ths=[...tb.querySelectorAll('th')].map(x=>x.innerText.trim());
                    if (ths.some(h=>/Personalization/i.test(h))){ t=tb; break; }
                  }
                  if(!t) return null;
                  const headers=[...t.querySelectorAll('th')].map(x=>x.innerText.trim());
                  const pidx=headers.findIndex(h=>/Personalization/i.test(h));
                  const out=[];
                  for (const tr of t.querySelectorAll('tbody tr')){
                    const tds=[...tr.querySelectorAll('td')];
                    out.push({
                      task:(tds[0]?tds[0].innerText.trim():''),
                      pers:(tds[pidx]?tds[pidx].innerText.replace(/\\s+/g,' ').trim():''),
                    });
                  }
                  return out;
                }"""
            )
            browser.close()
            return rows
    finally:
        httpd.shutdown()


def test_personalization_pct_paints_sigmoid_of_council_count(tmp_path, monkeypatch):
    _assert_discriminating()

    home = tmp_path / "thome"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_home(home)

    # Drive the LIVE builder — NO page_data — so the FULL n_personal -> sigmoid
    # -> embed-JSON -> paint chain runs (this is what a real /stats does).
    from trinity_local.launchpad_page import render_stats_html

    html = render_stats_html()
    rows = _read_personalization_cells(html, tmp_path / "served")

    assert rows, "the by-task-type routing cheat-sheet (Personalization column) did not render"
    # Map painted cell text by a normalized task label.
    norm = {r["task"].strip().lower(): r["pers"] for r in rows}
    # All three seeded task types must be present (precondition: the seed lit them).
    label_map = {
        "code_review": "code review",
        "data_analysis": "data analysis",
        "bug_fix": "bug fix",
    }
    for tt, painted_label in label_map.items():
        assert painted_label in norm, (
            f"seeded task type {tt!r} (label {painted_label!r}) missing from the "
            f"cheat-sheet — precondition for the value assertion failed; rows={rows}"
        )

    # THE value assertion — the sole thing keyed on the binding under test.
    for tt, n in _SEED_N.items():
        cell = norm[label_map[tt]]
        expected_pct = _EXPECTED_PCT[tt]
        # The cell reads "<pct>% n=<n>". Pin BOTH halves: pct == sigmoid(n)*100
        # rounded, AND n == the seeded council count (so a wrong-column or
        # wrong-row regression also reds).
        assert f"{expected_pct}%" in cell, (
            f"Personalization cell for {tt!r} painted {cell!r}, but the chairman "
            f"picker's sigmoid weight for n_personal={n} is {expected_pct}% "
            f"(round(sigmoid_alpha({n})*100)). A linear/raw-n/truncation regression "
            f"in launchpad_data._personal_routing_for_launchpad surfaces here as a "
            f"wrong personalization weight while the order-only sibling test (which "
            f"seeds personalization_pct) stays green."
        )
        assert f"n={n}" in cell, (
            f"Personalization cell for {tt!r} painted {cell!r}; expected n={n} "
            f"(the seeded council count) — wrong row/column binding."
        )
