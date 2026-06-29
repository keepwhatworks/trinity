"""Value-asserting guard: the topology basin-detail "Stale topology" verdict must
fire with the ARITHMETICALLY CORRECT two numbers when the on-disk basin's
prompt_ids length diverges from its `size`, and must stay SILENT when they match.

The verdict (memory_viewer.py `showDetail`):

    const idCount = (typeof b.prompt_id_count === "number")
      ? b.prompt_id_count : (Array.isArray(b.prompt_ids) ? b.prompt_ids.length : null);
    if (idCount !== null && idCount !== (b.size || 0)) {
      "Stale topology: prompt_ids carries " + idCount + " entries vs basin size "
        + (b.size || 0) + ". Re-run trinity-local lens to refresh."
    }

A fresh `lens` build writes `size = len(member_turn_indices)` and
`prompt_ids = [nodes[i].id for i in member_turn_indices]` from the SAME list
(me/basins.py:538/542) — so size == len(prompt_ids) by construction and the note
is correctly silent on healthy data. A STALE on-disk topics.json (legacy
prompt_ids-truncated-to-50 writes) makes them diverge, and the user must be told
the EXACT mismatch so they know to rebuild.

Why this guard exists (the gap it closes): the prior coverage was a slimmer
count-equality UNIT check (`prompt_id_count == len(original list)`) plus a
string-PRESENCE check (`"b.prompt_id_count" in html`). Neither bites the rendered
VERDICT: an inverted comparison (`===` instead of `!==`, or `>` instead of `!==`),
a swapped number pair (size and count transposed in the copy), or a wrong
right-hand side (comparing against `thread_count` instead of `size`) would all
keep both old tests green while painting a WRONG or MISSING staleness verdict — a
green-while-degenerate inversion on a data-correctness claim. This drives the real
deep-link, reads the painted detail DOM, and asserts the literal numbers.

Mutation-proven (Iter 166): in memory_viewer.py change the verdict's `!==` to
`===` (so it fires on the HEALTHY basin and silences on the stale one) → this
test reds on BOTH the silent-when-matched and fires-when-divergent assertions.
Swap the two interpolated numbers in the copy (idCount <-> b.size) → the
exact-number assertion reds. Restored byte-identical → green.

Slow + browser marked; skips when Playwright/chromium are absent; runs in CI
`browser`.
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_stale_topology", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# Two basins with KNOWN, distinct arithmetic:
#   b00 HEALTHY: size 12, prompt_ids length 12  -> verdict SILENT (fresh build shape).
#   b01 STALE:   size 30, prompt_ids length  7  -> verdict fires "carries 7 ... vs basin size 30".
_STALE_COUNT = 7
_STALE_SIZE = 30
_MATCH_SIZE = 12


def _render_with_topics(home: Path, monkeypatch) -> Path:
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _load_seeder().seed(home)

    topics = {
        "basins": [
            {
                "id": "b00",
                "label": "Healthy basin",
                "size": _MATCH_SIZE,
                "thread_count": 4,
                "top_terms": ["alpha", "beta"],
                "centroid": _unit([1, 0, 0, 0]),
                "prompt_ids": [f"p{i}" for i in range(_MATCH_SIZE)],
                "representatives": [
                    {
                        "transcript_id": "t1",
                        "turn_count": 1,
                        "headline": "a healthy prompt",
                        "turns": [{"snippet": "a healthy prompt"}],
                    }
                ],
            },
            {
                "id": "b01",
                "label": "Stale basin",
                "size": _STALE_SIZE,
                "thread_count": 6,
                "top_terms": ["gamma", "delta"],
                "centroid": _unit([0, 1, 0, 0]),
                "prompt_ids": [f"q{i}" for i in range(_STALE_COUNT)],
                "representatives": [
                    {
                        "transcript_id": "t2",
                        "turn_count": 1,
                        "headline": "a stale prompt",
                        "turns": [{"snippet": "a stale prompt"}],
                    }
                ],
            },
        ]
    }
    (home / "memories" / "topics.json").write_text(json.dumps(topics), encoding="utf-8")

    from trinity_local.memory_viewer import write_memory_viewer

    return write_memory_viewer()


_PROBE = """() => {
  const dp = document.querySelector('.topics-graph-detail');
  if (!dp) return { detailPresent: false };
  const txt = dp.innerText || '';
  const line = (txt.split('\\n').find((l) => l.includes('Stale topology')) || null);
  return {
    detailPresent: true,
    hasStaleNote: txt.includes('Stale topology'),
    staleLine: line,
  };
}"""


def test_stale_topology_verdict_paints_correct_numbers(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    mv = _render_with_topics(tmp_path / "trinity", monkeypatch)

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.on(
                "console",
                lambda m: errs.append(m.text[:160])
                if m.type == "error" and "favicon" not in m.text.lower()
                else None,
            )

            # HEALTHY basin: size == len(prompt_ids) -> verdict must be SILENT.
            page.goto(f"file://{mv}?file=topics.json&basin=b00", wait_until="load")
            page.wait_for_timeout(1600)  # d3 sim mounts + focusBasin opens b00
            healthy = page.evaluate(_PROBE)
            if not healthy.get("detailPresent"):
                failures.append("topology detail panel never rendered for the HEALTHY basin b00")
            elif healthy.get("hasStaleNote"):
                failures.append(
                    "Stale-topology verdict FIRED on a HEALTHY basin (size "
                    f"{_MATCH_SIZE} == prompt_ids {_MATCH_SIZE}) — a fresh `lens` build writes "
                    "size==len(prompt_ids), so the note must stay silent. The "
                    "comparison may have inverted (=== vs !==). Line: "
                    f"{healthy.get('staleLine')!r}"
                )

            # STALE basin: size 30 vs prompt_ids 7 -> verdict fires with BOTH exact numbers.
            page.goto(f"file://{mv}?file=topics.json&basin=b01", wait_until="load")
            page.wait_for_timeout(1600)
            stale = page.evaluate(_PROBE)
            if not stale.get("detailPresent"):
                failures.append("topology detail panel never rendered for the STALE basin b01")
            elif not stale.get("hasStaleNote"):
                failures.append(
                    "Stale-topology verdict was SILENT on a DIVERGENT basin (size "
                    f"{_STALE_SIZE} vs prompt_ids {_STALE_COUNT}) — the user is never told "
                    "their on-disk topics.json is stale. The comparison may have inverted "
                    "or the right-hand side may read the wrong field."
                )
            else:
                line = stale.get("staleLine") or ""
                # The verdict copy is: "... carries <count> entries vs basin size <size>. ..."
                expected = (
                    f"prompt_ids carries {_STALE_COUNT} entries vs basin size {_STALE_SIZE}"
                )
                if expected not in line:
                    failures.append(
                        "Stale-topology verdict painted the WRONG numbers: expected "
                        f"{expected!r} (count={_STALE_COUNT} from len(prompt_ids), "
                        f"size={_STALE_SIZE} from basin.size), got line {line!r}. The two "
                        "interpolated numbers may be swapped (count<->size) or sourced from "
                        "the wrong field (thread_count instead of size)."
                    )

            if errs:
                failures.append(f"console/page errors on the topology surface: {errs}")
        finally:
            browser.close()

    assert not failures, "Stale-topology value guard:\n  - " + "\n  - ".join(failures)
