"""Real-browser guard: the topology basin DETAIL panel must pluralize against the
count. A single-thread / single-turn / single-prompt basin renders "1 thread ·
1 turn" / "1 prompt", NOT the ungrammatical hardcoded plural "1 threads · 1 turns"
/ "1 prompts".

This is the n=1 plural-literal CLASS first fixed on the /stats captions (the
launchpad routing/by-task-type captions + cheat-sheet margin title, Iter 101) and
the eval-share card subhead ("1 prompts, 1 axes" → "1 prompt, 1 axis"). The
memory-viewer topology detail was the unfixed sibling: lines built
`b.thread_count + " threads"`, `b.size + " turns"`, and `b.size + " prompts"`
with hardcoded plural literals, so a basin formed from a single thread / single
turn rendered "1 threads · 1 turns" and a legacy single-prompt basin rendered
"1 prompts".

A basin's `thread_count` and `size` CAN legitimately be 1 (a basin seeded from one
short thread, or a single-turn Gemini-Takeout import), so this is a real rendered
defect, not a can't-happen path. The turn-rep count (turnCount + " turn" + ternary)
already used the singular ternary in this same file — these three just missed it.

Seeds three basins:
  * b00 — thread-aware single (thread_count=1, size=1) → "1 thread · 1 turn"
  * b01 — legacy single (no thread_count, size=1)      → "1 prompt"
  * b02 — plural (thread_count=2, size=5)              → "2 threads · 5 turns"
renders the real portal, clicks each topology node, and reads the DETAIL panel's
rendered text (the petite-vue/JS ternary is evaluated at render time — only the
real DOM produces "1 thread" vs "1 threads"; a source string-presence check would
NOT bite).

Mutation-proven 2026-06-18: revert the three ternaries to the bare plural literals
→ b00/b01 singular assertions red. Restored byte-identical.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    # b00: thread-aware, single thread + single turn -> "1 thread · 1 turn"
    # b01: legacy schema (no thread_count), single prompt -> "1 prompt"
    # b02: plural sanity (2 threads, 5 turns) -> "2 threads · 5 turns"
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": [1.0, 0.0, 0.0, 0.0], "size": 1, "thread_count": 1,
         "label": "Solo", "top_terms": ["alpha", "beta"],
         "representatives": [{"id": "r0", "snippet": "one prompt"}]},
        {"id": "b01", "centroid": [0.0, 1.0, 0.0, 0.0], "size": 1,
         "label": "Legacy", "top_terms": ["gamma", "delta"],
         "representatives": [{"id": "r1", "snippet": "one legacy prompt"}]},
        {"id": "b02", "centroid": [0.0, 0.0, 1.0, 0.0], "size": 5, "thread_count": 2,
         "label": "Plural", "top_terms": ["eps", "zeta"],
         "representatives": [{"id": "r2", "snippet": "a few prompts"}]},
    ]}), encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def test_topology_detail_pluralizes_against_count():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1400, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))

            page.goto(f"file://{pages / 'memory.html'}?file=topics.json",
                      wait_until="load")
            page.wait_for_timeout(1600)  # d3 mounts + force settles

            detail_for = {}
            for basin_id in ("b00", "b01", "b02"):
                clicked = page.evaluate(
                    """(bid) => {
                      const c = [...document.querySelectorAll('#content svg circle')]
                        .find(x => x.__data__ && x.__data__.id === bid);
                      if (!c) return false;
                      c.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                      return true;
                    }""",
                    basin_id,
                )
                if not clicked:
                    failures.append(f"topology: no node circle bound to {basin_id}")
                    continue
                page.wait_for_timeout(350)
                detail = page.evaluate(
                    """() => { const d = document.querySelector('.topics-graph-detail, [class*=detail]');
                              return d ? (d.innerText || '').replace(/\\s+/g, ' ') : ''; }"""
                )
                detail_for[basin_id] = detail

            b00 = detail_for.get("b00", "")
            b01 = detail_for.get("b01", "")
            b02 = detail_for.get("b02", "")

            # b00: single thread + single turn -> singular, NEVER plural literal.
            if "1 thread · 1 turn" not in b00:
                failures.append(
                    "topology detail: single-thread basin must read "
                    f"'1 thread · 1 turn', got {b00[:160]!r}")
            if "1 threads" in b00 or "1 turns" in b00:
                failures.append(
                    "topology detail rendered ungrammatical '1 threads'/'1 turns' "
                    f"at n=1 (the hardcoded plural-literal regressed): {b00[:160]!r}")

            # b01: legacy single prompt -> "1 prompt", NEVER "1 prompts".
            if "1 prompt " not in b01 and not b01.rstrip().endswith("1 prompt"):
                # the count is followed by " (NN% of corpus)" so a trailing space
                # is expected; guard against the plural defensively below too.
                if "1 prompt (" not in b01:
                    failures.append(
                        "topology detail: legacy single-prompt basin must read "
                        f"'1 prompt', got {b01[:160]!r}")
            if "1 prompts" in b01:
                failures.append(
                    "topology detail rendered ungrammatical '1 prompts' at n=1 "
                    f"(the hardcoded plural-literal regressed): {b01[:160]!r}")

            # b02: the singular fix must NOT over-reach — n>=2 stays plural.
            if "2 threads · 5 turns" not in b02:
                failures.append(
                    "topology detail: n>=2 basin lost its correct plural "
                    f"'2 threads · 5 turns' (singular fix over-reached): {b02[:160]!r}")

            if errs:
                failures.append(f"JS errors during viewer render: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "topology basin detail mis-pluralized a count at the n=1 boundary:\n  "
        + "\n  ".join(failures))
