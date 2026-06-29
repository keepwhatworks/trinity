"""Browser guard for the SETTINGS-MODAL keyboard focus mechanics.

Found 2026-06-18 (Iter 95) driving the REAL launchpad settings modal: it was a
visual modal (fixed, z-index 1000, backdrop) but NOT a keyboard modal. With it
open, Tab walked focus straight OUT of the modal and into the page behind the
backdrop — measured 27/35 Tab presses landed on `View full stats`, the prompt
`<textarea>`, `Launch Council`, the rebuild chip, the rail filter, the rail toggle.
A keyboard / screen-reader user could operate the obscured page while the modal
claimed to be modal. Three coupled gaps:

  1. NO focus trap — Tab escaped the open modal (the headline a11y defect).
  2. NO focus move on open — `activeElement` stayed on the gear behind the modal,
     so the first Tab started OUTSIDE and immediately walked the page.
  3. NO focus return on close — closing dumped focus to <body> (top of page)
     instead of the gear that opened it.
  Plus the modal carried no `role="dialog"` / `aria-modal` so a screen reader
  never announced it as a dialog.

Escape-to-close was already covered (Iter 62); the × is a real keyboard-operable
<button>. This guards the TAB-TRAP + focus-IN-on-open + focus-RETURN-on-close +
dialog semantics — the parts string tests can't see.

The fix lives in the SHARED launchpad template (`openSettings` / `closeSettings` /
`trapSettingsTab` + `role=dialog`/`aria-modal`), so the file:// surface exercises
the same code the side panel renders. The modal is the only modal in the codebase,
so this guards the whole class.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _council(title: str, n: int) -> dict:
    return {
        "council_id": f"c{n}",
        "chain_root_id": f"bundle_{n}",
        "review_page_path": f"/x/review_pages/council_{n}.html",
        "title": title,
        "winner_provider": "claude",
        "created_at": f"2026-06-0{n}T00:00:00+00:00",
        "task_type": "design",
        "segment_count": 1,
    }


_SYNTHETIC = [_council("alpha workflow", 1), _council("beta workflow", 2)]


def test_settings_modal_traps_tab_moves_focus_in_and_returns_it(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import trinity_local.launchpad_page as lp

    monkeypatch.setattr(lp, "_load_recent_councils", lambda *a, **k: list(_SYNTHETIC))
    pages = lp.write_portal_html().parent

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context().new_page()
            page.set_viewport_size({"width": 393, "height": 852})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(900)

            gear = page.query_selector('button[aria-label="Open settings"]')
            assert gear, "settings gear (aria-label='Open settings') not found"
            gear.focus()
            gear.click()
            page.wait_for_timeout(400)

            # The modal must declare itself a dialog to assistive tech.
            attrs = page.evaluate(
                "() => { const m = document.querySelector('.settings-modal'); "
                "return m ? {role: m.getAttribute('role'), "
                "ariaModal: m.getAttribute('aria-modal')} : null; }"
            )
            assert attrs, "settings modal did not open on gear click"
            assert attrs["role"] == "dialog", (
                f"settings modal missing role=dialog (a screen reader won't "
                f"announce it as a modal): got role={attrs['role']!r}"
            )
            assert attrs["ariaModal"] == "true", (
                f"settings modal missing aria-modal=true: got {attrs['ariaModal']!r}"
            )

            # (2) focus must move INTO the modal on open — not stay on the gear
            # behind the backdrop (or the first Tab starts outside + walks the page).
            opened = page.evaluate(
                "() => { const a = document.activeElement, "
                "m = document.querySelector('.settings-modal'); "
                "return { inModal: !!(m && m.contains(a)), tag: a.tagName }; }"
            )
            assert opened["inModal"], (
                "focus did NOT move into the settings modal on open — it stayed "
                f"on {opened['tag']} behind the backdrop (Iter-95 focus-IN gap)"
            )

            # (1) the headline: real Tab presses must NEVER escape the open modal.
            escapes = []
            for i in range(35):
                page.keyboard.press("Tab")
                page.wait_for_timeout(15)
                d = page.evaluate(
                    "() => { const a = document.activeElement, "
                    "m = document.querySelector('.settings-modal'); "
                    "return { modalOpen: !!m, inModal: !!(m && m.contains(a)), "
                    "tag: a.tagName, "
                    "label: (a.getAttribute('aria-label')||a.innerText||a.value||'').slice(0,40) }; }"
                )
                if d["modalOpen"] and not d["inModal"]:
                    escapes.append((i, d["tag"], d["label"]))
            assert not escapes, (
                "Tab ESCAPED the open settings modal into the page behind the "
                f"backdrop ({len(escapes)} of 35 presses) — the founder's modal-"
                f"is-not-a-keyboard-modal bug. Sample escapes: {escapes[:5]}"
            )

            # Shift+Tab must also stay trapped (wrap at the first control).
            for _ in range(20):
                page.keyboard.press("Shift+Tab")
                page.wait_for_timeout(10)
            shift_inside = page.evaluate(
                "() => { const a = document.activeElement, "
                "m = document.querySelector('.settings-modal'); "
                "return !!(m && m.contains(a)); }"
            )
            assert shift_inside, "Shift+Tab escaped the open settings modal"

            # (3) closing via the × must return focus to the gear, not <body>.
            page.query_selector(
                '.settings-modal button[aria-label="Close settings"]'
            ).click()
            page.wait_for_timeout(400)
            closed = page.evaluate(
                "() => ({ open: !!document.querySelector('.settings-modal'), "
                "isBody: document.activeElement === document.body, "
                "label: document.activeElement.getAttribute('aria-label') }) "
            )
            assert not closed["open"], "settings modal did not close on ×"
            assert not closed["isBody"] and closed["label"] == "Open settings", (
                "closing the settings modal dumped focus to <body> instead of "
                f"returning it to the gear trigger: {closed}"
            )

            assert not errs, f"JS errors during settings-modal focus test: {errs[:3]}"
        finally:
            browser.close()
