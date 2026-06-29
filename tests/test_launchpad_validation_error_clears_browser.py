"""Browser guard for the council composer's VALIDATION-error lifecycle.

`test_launchpad_launch_form.py` pins that an empty-task launch surfaces the inline
`.status-error` ribbon ("Please enter a task first.") instead of a blocking dialog.
But nothing asserted the ribbon CLEARS once the user actually types a task — and it
didn't: the error only cleared inside `launchCouncil()` on a real launch, so the red
"Please enter a task first." stayed pinned directly under a textarea the user had
since filled, contradicting itself ("enter a task" shown below a full task).

The fix wired an `@input="onPromptInput"` handler that drops the EMPTY_TASK_ERROR the
moment the textarea becomes non-empty (scoped to the bare-validation state — a real
dispatch error carries an `operation` + its own Dismiss button, so a keystroke must
not swallow it). This guard drives the REAL petite-vue render and pins both halves:
the error appears on empty launch, and DISAPPEARS on the first valid keystroke.

Slow + browser marked (spawns portal-html + chromium); skips when Playwright/chromium
absent. Cold synthetic home — no founder data, no real council ever launches.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_cold_portal(home: Path) -> Path:
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
    assert (pages / "launchpad.html").exists()
    return pages


def _err_state(page) -> dict:
    return page.evaluate(
        "() => { const e = document.querySelector('.status-error'); "
        "return { text: e ? e.innerText.trim() : null, "
        "visible: !!(e && e.offsetParent !== null) }; }"
    )


def test_empty_task_error_clears_on_valid_keystroke():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_cold_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.on("dialog", lambda d: d.dismiss())
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1000)

            def click_launch() -> None:
                page.eval_on_selector_all(
                    "button",
                    "(bs)=>{const b=bs.find(x=>/Launch Council/i.test(x.innerText)); if(b)b.click();}",
                )

            # Ensure the textarea is empty, then click Launch -> validation error.
            page.evaluate(
                "() => { const t=document.querySelector('#council-prompt'); "
                "if(t){t.value=''; t.dispatchEvent(new Event('input',{bubbles:true}));} }"
            )
            click_launch()
            page.wait_for_timeout(300)
            before = _err_state(page)
            assert before["visible"] and "task" in (before["text"] or "").lower(), (
                "empty-task launch did not surface the inline validation ribbon "
                f"(precondition for this guard): {before!r}"
            )

            # Now type a perfectly valid task. The validation ribbon MUST clear —
            # it must NOT stay pinned ("Please enter a task first.") under a full
            # textarea. This is the exact self-contradicting state the fix removed.
            page.evaluate(
                "() => { const t=document.querySelector('#council-prompt'); "
                "t.value='Should the event store be Postgres or DynamoDB?'; "
                "t.dispatchEvent(new Event('input',{bubbles:true})); }"
            )
            page.wait_for_timeout(300)
            after = _err_state(page)
            assert not after["visible"], (
                "the 'Please enter a task first.' validation ribbon is STILL visible "
                "after the user typed a valid task — the error contradicts a full "
                f"textarea. got: {after!r}"
            )
            assert not errs, f"JS errors during validation-clear flow: {errs[:3]}"
        finally:
            browser.close()
