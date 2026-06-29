"""The live council task header must NOT print the user's question twice.

THE FOUNDER SYMPTOM (UX sweep — questionable-usefulness disclosure widget):

The live council page (``render_live_council_page`` → ``review_pages/live_council.html``)
renders the council task as one of two shapes keyed on length:

  * ``<= 240`` chars  → a plain ``<h1>`` (the full question, once).
  * ``> 240`` chars   → a ``.task-collapsible`` ``<details>`` disclosure, ``:open``
                        by default when ``<= 600`` chars.

The ``<summary>`` USED to be ``threadTaskTextDisplay.slice(0, 200)…`` — a teaser
slice of the task. That only reads sensibly when the details is COLLAPSED. But for
a task in the open-by-default band (241–600 chars — a single paragraph, the most
common real length) the details renders OPEN, so the slice ``<summary>`` AND the
full-text ``<p>`` body both painted: the first ~200 chars of the user's question
appeared TWICE, stacked, nearly identically (bold teaser, then the body restarting
with the same words). A real user with a one-paragraph council task saw their
question printed twice and wondered if the page double-rendered.

THE FIX: the ``<summary>`` is now a fixed disclosure LABEL ("Your question"); the
body ``<p>`` is the single source of the task text. No duplication in either state.

THIS GUARD drives the open-by-default band (a 241–600 char task) in a real browser
and asserts a distinctive sentence from the FIRST 200 chars of the question appears
EXACTLY ONCE in the visible text — not twice. MUTATION-PROVEN: revert the summary
to ``{{ threadTaskTextDisplay.slice(0, 200) }}…`` and this reds with count == 2.
"""

from __future__ import annotations

import functools
import http.server
import threading
from urllib.parse import quote

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# A task in the OPEN-by-default band: > 240 (forces the <details>) AND <= 600
# (keeps it :open). The distinctive marker sits in the first ~60 chars, so it lands
# inside BOTH the old 200-char summary slice AND the body — the exact overlap that
# printed it twice. No unbreakable token (that's the overflow test's job); this is
# purely about duplicate rendering.
_MARKER = "ZEBRAFISH_MIGRATION_PLAN_MARKER"
_OPEN_BAND_TASK = (
    _MARKER + " — design a migration plan for moving the council outcome ledger "
    "from per-file JSON into a single append-only JSONL while preserving the existing "
    "readers and keeping the launchpad routing card live during the cutover, with a "
    "spelled-out rollback path and a back-compat window for older outcome schemas."
)
# Pin the discriminating shape at import time so a typo can't make the test vacuous.
assert 240 < len(_OPEN_BAND_TASK) <= 600, len(_OPEN_BAND_TASK)


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_live_council_open_band_task_renders_question_once(tmp_path, monkeypatch):
    """A 241–600 char task (open-by-default ``<details>``) must render the
    question's leading text ONCE, not duplicated by a teaser ``<summary>`` slice.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    write_portal_html()  # vendor assets the live page references
    write_live_council_page()

    httpd, port = _serve(tmp_path)
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                # Stub the full dispatcher interface so no real host/council is hit.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    " dispatch: function(o){ if(o&&o.onResult) o.onResult({ok:true}); },"
                    " probe: function(){ return Promise.resolve('ready'); },"
                    " onStateChange: function(){ return function(){}; } };"
                )
                page.set_viewport_size({"width": 768, "height": 1000})
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?task={quote(_OPEN_BAND_TASK)}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(1200)  # petite-vue mount
                probe = page.evaluate(
                    """(marker) => {
                        const det = document.querySelector('.task-collapsible');
                        const summary = det ? det.querySelector('summary') : null;
                        // Count occurrences of the distinctive marker in the VISIBLE
                        // text of the whole document (innerText excludes display:none).
                        const text = document.body.innerText;
                        let count = 0, idx = text.indexOf(marker);
                        while (idx !== -1) { count++; idx = text.indexOf(marker, idx + 1); }
                        return {
                            hasDetails: !!det,
                            detOpen: det ? det.open : null,
                            summaryText: summary ? summary.textContent.trim() : null,
                            markerCount: count,
                            braceLeak: /\\{\\{|\\}\\}/.test(text),
                        };
                    }""",
                    _MARKER,
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errors, f"live council threw on the open-band task: {errors}"
    assert not probe["braceLeak"], "raw {{ }} leaked: petite-vue didn't mount"
    # Bite precondition: the open-by-default <details> must actually render and be
    # open — else this drives the <h1> (<=240) or the collapsed (>600) path and the
    # duplication assertion is a false pass.
    assert probe["hasDetails"], (
        "the .task-collapsible <details> didn't render for a 241–600 char task "
        "— this guard must drive the open-by-default disclosure, not the <h1>"
    )
    assert probe["detOpen"] is True, (
        "the .task-collapsible <details> wasn't :open for a <=600 char task "
        "— the duplication only paints when the slice summary AND the body both show"
    )
    # THE class guard: the question's leading marker must appear EXACTLY ONCE in the
    # visible text. The slice-summary regression reprinted the first ~200 chars, so
    # the marker showed up TWICE (summary teaser + body). markerCount must be 1.
    assert probe["markerCount"] == 1, (
        f"the live council task header rendered the question's leading text "
        f"{probe['markerCount']}x (expected 1) — the .task-collapsible <summary> is "
        f"reprinting a slice of the task ({probe['summaryText']!r}) on top of the full "
        "<p> body, printing the user's question twice (the open-band duplication bug). "
        "The summary must be a fixed disclosure label, not a slice of the task."
    )
