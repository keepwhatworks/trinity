"""Browser guard for the launchpad's PRIMARY action — the "Ask a new council"
launch form. `test_launchpad_dispatch.py` checks the extension-ID data plumbing
and string-asserts that `window.__TRINITY_DISPATCH__` exists, but nothing clicks
Launch and inspects the dispatched payload.

The payload is where the real bugs lived. The launchpad generates a
`status_token`, polls `launch_<token>` for completion, AND must forward that same
token to the CLI via the dispatch `extensionAction`. When the forwarding was
missing the CLI wrote to its own `bundle_<id>` path, the launchpad's poll 404'd
forever, and the Council card stuck on "QUEUED" even though the council finished
(found 2026-05-26 via real-Chrome dogfood — no test caught it). This pins the
contract where it executes: empty prompt is rejected, and a real launch
dispatches `{kind:'launch-council', task, status_token}` with a non-empty token.

Slow-marked (spawns portal-html + chromium); skips when Playwright/chromium are
absent. Cold synthetic home — no founder data, dispatcher STUBBED so no real
council ever launches.
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


def test_launch_form_validates_empty_and_forwards_status_token():
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
            dialogs: list[str] = []
            errs: list[str] = []

            def _on_dialog(d) -> None:
                dialogs.append(d.message)
                d.dismiss()

            page.on("dialog", _on_dialog)
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'launchpad.html'}")
            page.wait_for_timeout(1200)

            def click_launch():
                page.eval_on_selector_all(
                    "button",
                    "(bs)=>{const b=bs.find(x=>/Launch Council/i.test(x.innerText)); if(b)b.click();}",
                )

            # A. Empty prompt is rejected with an INLINE error (.status-error),
            # NOT a blocking window.alert that freezes the page — and nothing
            # dispatches. (v1.7.x: replaced the alert with the inline launchError
            # ribbon every other launch error already uses.)
            page.evaluate(
                "() => { window.__CAP__ = null; "
                "const t=document.querySelector('#council-prompt'); "
                "if(t){t.value=''; t.dispatchEvent(new Event('input',{bubbles:true}));} }"
            )
            click_launch()
            page.wait_for_timeout(300)
            assert not dialogs, (
                f"empty-prompt validation used a BLOCKING dialog, not an inline error: {dialogs}"
            )
            inline = page.evaluate(
                "() => (document.querySelector('.status-error') || {}).innerText || ''"
            )
            assert "task" in inline.lower(), (
                f"empty prompt did not surface an inline validation error: {inline!r}"
            )
            assert page.evaluate("() => window.__CAP__") is None, (
                "an empty-prompt launch still dispatched a council"
            )

            # B. A real prompt dispatches launch-council WITH the status_token.
            PROMPT = "Should the event store be Postgres or DynamoDB?"
            page.evaluate(
                "(prompt) => { window.__CAP__ = null; "
                "window.__TRINITY_DISPATCH__ = { dispatch: (o) => { window.__CAP__ = o.extensionAction; "
                "if (o.onResult) o.onResult({ok:true}); } }; "
                "const t=document.querySelector('#council-prompt'); "
                "t.value = prompt; t.dispatchEvent(new Event('input',{bubbles:true})); }",
                PROMPT,
            )
            page.wait_for_timeout(150)
            click_launch()
            page.wait_for_timeout(400)
            cap = page.evaluate("() => window.__CAP__")

            assert cap, "Launch did not dispatch an extensionAction"
            assert cap.get("kind") == "launch-council", f"wrong dispatch kind: {cap.get('kind')!r}"
            assert cap.get("task") == PROMPT, f"task not forwarded: {cap.get('task')!r}"
            token = cap.get("status_token") or ""
            assert token.startswith("launch_") and len(token) > 8, (
                "the launch dispatch did NOT forward a usable status_token — this is "
                "exactly the regression that made the launchpad poll 404 forever and "
                f"the Council card stick on QUEUED. got: {token!r}"
            )

            # C. Optimistic UI: the textarea clears and the operation card appears.
            ui = page.evaluate(
                "() => ({ cleared: (document.querySelector('#council-prompt')||{}).value === '', "
                "card: /Postgres|QUEUED|running|Launching|Starting next|Each model/i.test(document.body.innerText) })"
            )
            assert ui["cleared"], "prompt textarea was not cleared after launch"
            assert ui["card"], "no optimistic operation card appeared after launch"
            assert not errs, f"JS errors during launch flow: {errs[:3]}"
        finally:
            browser.close()
