"""Real-browser guard: the memory viewer's PRIMARY controls convey their selected /
pressed state PROGRAMMATICALLY, not by color alone (WCAG 1.3.1 / 4.1.2).

THE DEFECT (found 2026-06-18 driving the real memory viewer in the UX sweep, Iter
100 — the third major HTML surface, after the launchpad + live-council aria sweeps):
two primary controls signalled their state ONLY via a `.active` color class, with no
programmatic equivalent —

  • NAV TABS — the 7 file tabs (core/lens/topics/vocab/picks/routing) are the main way
    a user switches files. The current tab got a `.active` class but NO
    aria-current="page"; the page <h1> is the static "Your lens" on EVERY file, so it
    doesn't disambiguate either. A screen-reader user navigating the <nav> landmark
    could not tell which file is the current page (measured in-browser: every active
    link had `aria-current=None`).
  • VIEW-TOGGLE — the Reader / Raw-JSON switch on the picks/routing/topics JSON views
    marked the chosen view with `.active` only; both buttons reported
    `aria-pressed=None` and the group had no role. The selected view was invisible to AT.

THE FIX (memory_viewer.py): aria-current="page" on the active nav link (set where
`.active` is set — single source of truth); role=group + aria-label on the toggle wrap
and aria-pressed mirroring `.active` on each toggle button (+ type=button); an
aria-label on the <nav> landmark so it isn't anonymous.

This guard DRIVES the real viewer over file:// and asserts:
  (1) the active nav tab carries aria-current="page" and the inactive ones do NOT
      (after a real nav CLICK, so the JS that sets it is exercised — not the source);
  (2) the Reader/Raw toggle reports aria-pressed reflecting which view is shown, and
      clicking Raw flips the pressed state to the Raw button.
Both are RENDERED-DOM aria-state assertions (not source-string checks). Mutation-proven
to red on the un-fixed code (drop the aria-current setter / the aria-pressed setters).

Drives the documented `portal-html --open-browser` file:// prod path (the MCP browser
tools can't reach file://). Seeds a PII-free synthetic home. Slow + browser marked;
skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_control_state_aria", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_active_nav_tab_has_aria_current_page(tmp_path, monkeypatch):
    """The active file tab must carry aria-current='page'; inactive tabs must not.

    The page heading is the static 'Your lens' on every file, so without
    aria-current a screen-reader user navigating the nav landmark cannot tell which
    file is the current page — the visual `.active` color is the ONLY 'you are here'
    signal. Drives a real nav CLICK so the JS that sets the state is exercised.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _load_seeder().seed(home)

    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1100})
            page.goto(f"file://{mv}", wait_until="load")
            page.wait_for_timeout(700)

            # CLICK the lens.md tab — exercises the JS that sets .active + aria-current.
            link = page.query_selector("a.memory-nav-link[href*='file=lens.md']")
            assert link is not None, "lens.md nav tab missing — fix the fixture"
            link.click()
            page.wait_for_load_state("load")
            page.wait_for_timeout(700)

            state = page.evaluate(
                """() => {
                  const links = [...document.querySelectorAll('a.memory-nav-link')];
                  return links.map(a => ({
                    file: a.dataset.file,
                    active: a.classList.contains('active'),
                    ariaCurrent: a.getAttribute('aria-current'),
                  }));
                }"""
            )
            actives = [s for s in state if s["active"]]
            if len(actives) != 1 or actives[0]["file"] != "lens.md":
                failures.append(
                    f"expected exactly lens.md active after the click, got {actives!r}"
                )
            for s in state:
                if s["active"]:
                    if s["ariaCurrent"] != "page":
                        failures.append(
                            f"the ACTIVE nav tab ({s['file']}) has aria-current="
                            f"{s['ariaCurrent']!r}, not 'page' — a screen-reader user "
                            "can't tell which file is the current page (the <h1> is the "
                            "static 'Your lens' on every file). WCAG 1.3.1/4.1.2."
                        )
                else:
                    if s["ariaCurrent"] is not None:
                        failures.append(
                            f"an INACTIVE nav tab ({s['file']}) wrongly carries "
                            f"aria-current={s['ariaCurrent']!r} — only the current "
                            "page's tab may."
                        )
        finally:
            browser.close()

    assert not failures, (
        "memory-viewer nav aria-current regressed:\n  " + "\n  ".join(failures)
    )


def test_reader_raw_toggle_conveys_aria_pressed(tmp_path, monkeypatch):
    """The Reader/Raw-JSON view toggle must report aria-pressed reflecting the shown
    view; clicking Raw must flip the pressed state to the Raw button.

    The selected view was conveyed ONLY by the `.active` color class — both buttons
    reported aria-pressed=None, so a screen-reader user could not tell (or hear)
    which view was active. WCAG 4.1.2.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _load_seeder().seed(home)

    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1100})
            # picks.json renders the Reader/Raw toggle (Reader is the default view).
            page.goto(f"file://{mv}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)

            def read_toggle():
                return page.evaluate(
                    """() => {
                      const btns = [...document.querySelectorAll('.view-toggle button')];
                      return btns.map(b => ({
                        label: b.textContent.trim(),
                        active: b.classList.contains('active'),
                        pressed: b.getAttribute('aria-pressed'),
                      }));
                    }"""
                )

            before = read_toggle()
            if len(before) < 2:
                pytest.skip("picks.json did not render a 2-button view toggle here")

            def find(rows, label):
                for r in rows:
                    if r["label"] == label:
                        return r
                return None

            reader_b = find(before, "Reader")
            raw_b = find(before, "Raw JSON")
            assert reader_b and raw_b, f"toggle labels changed: {before!r}"

            # Default view = Reader → Reader pressed=true, Raw pressed=false.
            if reader_b["pressed"] != "true":
                failures.append(
                    "the DEFAULT (Reader) view button reports aria-pressed="
                    f"{reader_b['pressed']!r}, not 'true' — the chosen view is "
                    "conveyed by color alone (WCAG 4.1.2)."
                )
            if raw_b["pressed"] != "false":
                failures.append(
                    "the un-selected Raw button reports aria-pressed="
                    f"{raw_b['pressed']!r}, not 'false'."
                )

            # Click Raw JSON → pressed state must flip to Raw.
            page.click(".view-toggle button:has-text('Raw JSON')")
            page.wait_for_timeout(300)
            after = read_toggle()
            reader_a = find(after, "Reader")
            raw_a = find(after, "Raw JSON")
            if not (raw_a and raw_a["pressed"] == "true" and raw_a["active"]):
                failures.append(
                    f"after clicking Raw JSON, the Raw button's aria-pressed did not "
                    f"become 'true' (got {raw_a!r}) — the toggle's pressed state is "
                    "not announced to AT on switch."
                )
            if not (reader_a and reader_a["pressed"] == "false"):
                failures.append(
                    f"after clicking Raw JSON, the Reader button's aria-pressed did "
                    f"not become 'false' (got {reader_a!r})."
                )
        finally:
            browser.close()

    assert not failures, (
        "memory-viewer view-toggle aria-pressed regressed:\n  " + "\n  ".join(failures)
    )


def _seed_with_thread_basin(home):
    """Seed a synthetic home whose topics.json carries a MULTI-TURN thread rep, so
    the topics Reader renders the expandable `<li class="topics-rep-thread expandable">`
    disclosure control (the seeder ships single-turn reps that get no expand affordance).
    """
    import json

    _load_seeder().seed(home)
    basins = [
        {
            "id": "basin-thread-0",
            "size": 12,
            "label": "floor-plan engine",
            "top_terms": ["floor", "plan", "engine"],
            "representatives": [
                {
                    "id": "rep-A",
                    "transcript_id": "tx-A",
                    "turn_count": 3,
                    "headline": "How should the floor-plan engine handle L-shaped rooms?",
                    "turns": [
                        {"turn_index": 0, "snippet": "first turn about L-shaped rooms"},
                        {"turn_index": 1, "snippet": "second turn refining the constraint"},
                        {"turn_index": 2, "snippet": "third turn picking the approach"},
                    ],
                }
            ],
        }
    ]
    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": basins}), encoding="utf-8"
    )


def test_expandable_thread_rep_is_keyboard_operable(tmp_path, monkeypatch):
    """The topics Reader's expandable thread card must be a real button-role disclosure:
    reachable by Tab (focusable), Enter/Space-activatable, and announce aria-expanded.

    THE DEFECT (found 2026-06-19 driving the real viewer, Iter 131): the multi-turn
    thread `<li class="topics-rep-thread expandable">` had ONLY a click handler — a bare
    <li> with no tabindex (not in the Tab order → keyboard users can't open it, WCAG
    2.1.1), no role/aria-expanded (mute to AT → no "expanded/collapsed" state, WCAG
    4.1.2), and no keydown handler (Enter did nothing). Mouse-only. Measured in-browser:
    tabindex=None, role=None, aria-expanded=None, li.focus() did not move focus, and
    Enter did NOT toggle .open (only a mouse click did).

    Drives the real viewer over file://, focuses the expandable card, presses Enter, and
    asserts focus actually landed on it AND the card expanded with aria-expanded flipping
    to 'true' — a RENDERED-DOM keyboard/role assertion, not a source-string check.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_with_thread_basin(home)

    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1100})
            page.goto(f"file://{mv}?file=topics.json", wait_until="load")
            page.wait_for_timeout(800)

            # Click a basin node to render the detail pane (the reps list).
            page.evaluate(
                """() => {
                  const c = document.querySelector('circle');
                  if (c) c.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                }"""
            )
            page.wait_for_timeout(600)

            present = page.evaluate(
                """() => {
                  const li = document.querySelector('li.topics-rep-thread.expandable');
                  if (!li) return {found:false};
                  return {
                    found: true,
                    tabindex: li.getAttribute('tabindex'),
                    role: li.getAttribute('role'),
                    ariaExpanded: li.getAttribute('aria-expanded'),
                  };
                }"""
            )
            if not present.get("found"):
                pytest.skip("topics Reader did not render an expandable thread card here")

            # ROLE/STATE: a button-role disclosure with an initial collapsed state.
            if present["role"] != "button":
                failures.append(
                    f"the expandable thread card has role={present['role']!r}, not "
                    "'button' — a screen-reader user isn't told it's an activatable "
                    "control (WCAG 4.1.2)."
                )
            if present["tabindex"] != "0":
                failures.append(
                    f"the expandable thread card has tabindex={present['tabindex']!r}, "
                    "not '0' — it's not in the Tab order, so a keyboard-only user can't "
                    "reach it to expand the turns (WCAG 2.1.1)."
                )
            if present["ariaExpanded"] != "false":
                failures.append(
                    f"the collapsed thread card reports aria-expanded="
                    f"{present['ariaExpanded']!r}, not 'false' — the expand state is "
                    "conveyed by the chevron glyph alone (WCAG 4.1.2)."
                )

            # KEYBOARD OPERATION: focus the card, press Enter, assert it expands.
            focused = page.evaluate(
                """() => {
                  const li = document.querySelector('li.topics-rep-thread.expandable');
                  li.focus();
                  return document.activeElement === li;
                }"""
            )
            if not focused:
                failures.append(
                    "the expandable thread card cannot receive focus (li.focus() did "
                    "not move document.activeElement to it) — it's not keyboard-"
                    "focusable, so the Enter test below is moot (WCAG 2.1.1)."
                )

            before_open = page.evaluate(
                "() => document.querySelector('li.topics-rep-thread.expandable')"
                ".classList.contains('open')"
            )
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            after = page.evaluate(
                """() => {
                  const li = document.querySelector('li.topics-rep-thread.expandable');
                  return {
                    open: li.classList.contains('open'),
                    ariaExpanded: li.getAttribute('aria-expanded'),
                  };
                }"""
            )
            if before_open or not after["open"]:
                failures.append(
                    f"pressing Enter did NOT expand the thread card (open: "
                    f"{before_open} -> {after['open']}) — the disclosure is mouse-only; "
                    "keyboard users can't read the turns (WCAG 2.1.1)."
                )
            if after["ariaExpanded"] != "true":
                failures.append(
                    f"after Enter expanded the card, aria-expanded={after['ariaExpanded']!r}, "
                    "not 'true' — the new state isn't announced to AT (WCAG 4.1.2)."
                )
        finally:
            browser.close()

    assert not failures, (
        "memory-viewer expandable-thread keyboard/role regressed:\n  "
        + "\n  ".join(failures)
    )
