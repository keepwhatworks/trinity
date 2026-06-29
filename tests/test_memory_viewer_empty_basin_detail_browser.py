"""Honest-render guard: a topology basin with NO representatives AND no top_terms
must NOT paint a dead-end detail panel — clicking (or deep-linking) it must surface
an honest "no representatives or terms … Rebuild via trinity-local lens" note inside
the detail panel, and a HEALTHY basin must NOT show that note.

The founder symptom (Iter 138): a topics.json basin can carry size/thread_count but an
empty `representatives` array and empty `top_terms` — a legacy topics.json written
before the representatives feature shipped, or a stale build. The `showDetail` reps
branch (`b.representatives.length`) and the top_terms branch both no-op on that shape,
and so do the pick xlink + launch chip (no seed text). So clicking such a node painted
ONLY the size header line — e.g. "b01 · 2 threads · 5 turns (29.4% of corpus)" — and
nothing else: a basin holding ~29% of the corpus that reveals nothing about itself and
offers no action. A dead-end ORPHAN render. The fix adds a `.topics-basin-empty` honest
note pointing at the rebuild that repopulates the basin.

This is a REAL-BROWSER interaction assertion (drive the deep-link, READ the rendered
detail DOM + visibility), not a source-string check: a regression where the note block
is dropped, builds invisible, or fires on a HEALTHY basin would be caught.

Mutation-proven (Iter 138): delete the `if (!hasReps && !hasTerms)` note block in
memory_viewer.py → test_empty_basin_detail_surfaces_honest_note reds with the founder
symptom ("painted only the size header, no honest note"); the healthy-basin contrast
case stays green. Restored byte-identical → both green.

Slow + browser marked; skips when Playwright/chromium are absent; runs in CI `browser`.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_empty_basin", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _render_with_degenerate_topics(home: Path, monkeypatch) -> Path:
    """Seed the synthetic home (publishes vendor + portal deps), then OVERWRITE
    topics.json with a 2-basin shape where b01 carries size/threads but NO
    representatives and NO top_terms (the legacy/stale dead-end shape), and
    re-render the viewer so it inlines the degenerate file."""
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _load_seeder().seed(home)

    # Unit-normalize 4-d centroids so the cosine/force layout has something to chew.
    def _unit(v):
        import math

        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    topics = {
        "basins": [
            {
                "id": "b00",
                "label": "Design the floor plan engine",
                "size": 12,
                "thread_count": 4,
                "top_terms": ["design", "floor", "plan", "engine"],
                "centroid": _unit([1, 0, 0, 0]),
                "prompt_id_count": 12,
                "representatives": [
                    {
                        "transcript_id": "t1",
                        "turn_count": 1,
                        "headline": "Design the floor plan engine for the prefab module",
                        "turns": [{"snippet": "Design the floor plan engine for the prefab module"}],
                    }
                ],
            },
            {
                # The dead-end shape: size + threads but no reps, no terms.
                "id": "b01",
                "label": "",
                "size": 5,
                "thread_count": 2,
                "top_terms": [],
                "centroid": _unit([0, 1, 0, 0]),
                "prompt_id_count": 5,
                "representatives": [],
            },
        ]
    }
    (home / "memories" / "topics.json").write_text(json.dumps(topics), encoding="utf-8")

    from trinity_local.memory_viewer import write_memory_viewer

    return write_memory_viewer()


# Scope the probe to the topology detail panel. The honest note lives there as
# `.topics-basin-empty`; the size header (`.basin-id` + text) is its sibling.
_DETAIL_PROBE = """() => {
  const dp = document.querySelector('.topics-graph-detail');
  if (!dp) return { detailPresent: false };
  const note = dp.querySelector('.topics-basin-empty');
  let noteVisible = false;
  if (note) {
    const r = note.getBoundingClientRect();
    noteVisible = r.width > 0 && r.height > 0 && !!note.offsetParent;
  }
  const code = note ? note.querySelector('code') : null;
  return {
    detailPresent: true,
    detailText: dp.innerText.trim(),
    childCount: dp.children.length,
    notePresent: !!note,
    noteVisible: noteVisible,
    noteText: note ? note.innerText.trim() : null,
    codeText: code ? code.innerText.trim() : null,
    hasRepsLabel: !!dp.querySelector('.topics-reps-label'),
    hasLaunchChip: !!dp.querySelector('.topics-launch-chip'),
  };
}"""


def test_empty_basin_detail_surfaces_honest_note(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    mv = _render_with_degenerate_topics(tmp_path / "trinity", monkeypatch)

    failures: list[str] = []
    # 560px — the MID breakpoint the steer flagged for the detail panel.
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 560, "height": 900}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.on(
                "console",
                lambda m: errs.append(m.text[:160])
                if m.type == "error" and "favicon" not in m.text.lower()
                else None,
            )

            # Deep-link straight to the degenerate basin so its detail auto-opens.
            page.goto(f"file://{mv}?file=topics.json&basin=b01", wait_until="load")
            page.wait_for_timeout(1500)  # d3 sim mounts + focusBasin opens b01

            st = page.evaluate(_DETAIL_PROBE)
            if not st.get("detailPresent"):
                failures.append("topology detail panel never rendered for b01")
            else:
                if not st["notePresent"] or not st["noteVisible"]:
                    failures.append(
                        "EMPTY basin b01 painted a DEAD-END detail panel — only the size "
                        f"header, no honest '.topics-basin-empty' note (childCount="
                        f"{st['childCount']}, text={st['detailText']!r}). A basin holding "
                        "~29% of the corpus that reveals nothing about itself."
                    )
                else:
                    nt = (st["noteText"] or "").lower()
                    if "no representatives" not in nt or "rebuild" not in nt:
                        failures.append(
                            f"honest empty-basin note copy regressed: {st['noteText']!r}"
                        )
                    if st["codeText"] != "trinity-local lens":
                        failures.append(
                            f"empty-basin note rebuild command is {st['codeText']!r}, "
                            "not 'trinity-local lens'"
                        )
            if errs:
                failures.append(f"JS errors on the empty-basin deep-link: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "memory-viewer empty-basin detail dead-end regressed:\n  " + "\n  ".join(failures)
    )


def test_healthy_basin_detail_shows_no_empty_note(tmp_path, monkeypatch):
    """Contrast / false-positive guard: a basin WITH representatives (b00) must open
    its reps + launch chip and must NOT show the empty-basin honest note — so a
    mutation that renders the note unconditionally is caught, not papered over."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    mv = _render_with_degenerate_topics(tmp_path / "trinity", monkeypatch)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 560, "height": 900}).new_page()
            page.goto(f"file://{mv}?file=topics.json&basin=b00", wait_until="load")
            page.wait_for_timeout(1500)
            st = page.evaluate(_DETAIL_PROBE)
        finally:
            browser.close()

    assert st.get("detailPresent"), "healthy basin b00 never rendered its detail panel"
    assert not st["notePresent"], (
        "a HEALTHY basin (b00, has representatives) wrongly showed the empty-basin "
        f"honest note — the !hasReps && !hasTerms gate is mis-firing (text={st['detailText']!r})"
    )
    assert st["hasRepsLabel"] and st["hasLaunchChip"], (
        "healthy basin b00 lost its representatives label / launch chip"
    )
