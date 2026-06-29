"""The capture host's `error` field is painted VERBATIM — it must not leak.

Every string `capture_host` returns in `{"ok": false, "error": ...}` is rendered
straight into a user-facing surface with no further sanitization:

  • the toolbar popup  — `setStatus("Failed: " + extractError(response))`
    (`browser-extension/popup.js` L467),
  • the launchpad dispatch ribbon — `this.launchError = String(detail)` where
    `detail = result.response.error` (`launchpad-init.js` L1806-1807),
  • the side-panel shell.

The dispatch path (`_run_action`) historically surfaced the *last line of the
CLI subprocess's stderr* verbatim. When a `trinity-local` subcommand crashes
with an uncaught exception, that last line is the bare Python traceback
exception line — e.g.

    FileNotFoundError: [Errno 2] No such file or directory:
        '/Users/vishi/.trinity/memories/topics.json'

So the popup painted

    Failed: FileNotFoundError: [Errno 2] No such file or directory:
        '/Users/vishi/.trinity/memories/topics.json'

leaking a Python TYPE NAME, an `[Errno N]`, and an ABSOLUTE filesystem path
(`/Users/<name>/…`) straight to the user — a violation of the TYPE-only-honest
error-copy constraint (#43102d25), AND dishonest UX: a non-technical user can't
act on a traceback frame.

`capture_host._safe_error` is the single choke point that maps any such raw
error to a TYPE-only, leak-free, still-actionable message. This guard:

  (1) drives the REAL `_run_action` with a crashed-CLI stderr that COULD leak
      (the discriminating input) and asserts the returned `error` is honest +
      actionable (`trinity-local status`) AND carries NO absolute path / Errno /
      Traceback / Python type name (the founder symptom);
  (2) drives the REAL popup DOM — loads `popup.html`, stubs `chrome.runtime` so
      launch-council returns that exact host response, clicks Run, and asserts
      the PAINTED `#status` ribbon is the honest text and contains none of the
      leaked internals.

Mutation proof: revert `_safe_error` at the L828 dispatch site (surface the raw
`last_stderr[-1]` again) → part (1) reds with the founder symptom and the popup
in part (2) paints the absolute path. Found 2026-06-21 by driving a crashed-CLI
dispatch through the host into the rendered popup.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
POPUP = REPO / "browser-extension" / "popup.html"

# The discriminating input: a REAL crashed-CLI stderr. The last non-frame line
# is a bare traceback exception carrying a Python type name, an [Errno N], and
# an absolute /Users/ path — exactly what a `trinity-local` subcommand prints
# when it dies on a missing/corrupt state file.
_CRASH_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "/Users/vishi/.local/lib/python3.11/site-packages/'
    'trinity_local/dream.py", line 88, in run\n'
    "    data = json.loads(path.read_text())\n"
    "FileNotFoundError: [Errno 2] No such file or directory: "
    "'/Users/vishi/.trinity/memories/topics.json'\n"
)

# Markers that must NEVER reach a user-facing error string.
_LEAKS = ("/Users/", "/home/", "/private/", "[Errno", "Traceback",
          "FileNotFoundError", ".trinity/memories", "site-packages")


def _host_error_for_crash() -> str:
    """Drive the REAL capture_host dispatch path with the crashed-CLI stderr and
    return the `error` the host hands the extension."""
    sys.path.insert(0, str(REPO / "src"))
    from trinity_local import capture_host

    class _Crashed:
        returncode = 1
        stdout = ""
        stderr = _CRASH_STDERR

    import subprocess as real_subprocess
    orig_run = real_subprocess.run
    orig_detached = capture_host._DETACHED_ACTIONS
    try:
        real_subprocess.run = lambda *a, **k: _Crashed()
        # 'dream' == the "Refresh memory" button: a non-detached blocking action
        # that flows through the synchronous last-line-of-stderr error surface.
        capture_host._DETACHED_ACTIONS = set()
        result = capture_host._run_action({"kind": "dream"})
    finally:
        real_subprocess.run = orig_run
        capture_host._DETACHED_ACTIONS = orig_detached

    assert result["ok"] is False, "precondition: the crashed CLI must report failure"
    return result["error"]


def test_host_dispatch_error_is_honest_actionable_and_leak_free():
    """Part (1): the host's `error` for a crashed CLI leaks nothing + is actionable."""
    err = _host_error_for_crash()

    # PRECONDITION (discriminating): the RAW last-stderr line — what the host
    # used to surface — really did carry the leak, so a passing assertion below
    # bites the sanitizer, not an absent input.
    raw_last_line = _CRASH_STDERR.strip().splitlines()[-1]
    assert "/Users/" in raw_last_line and "[Errno" in raw_last_line, (
        "precondition: the crash stderr's last line must carry the absolute "
        "path + Errno the host would otherwise paint"
    )

    # The founder symptom: NONE of the internals survive into the user string.
    for marker in _LEAKS:
        assert marker not in err, (
            f"LEAK: capture_host returned an error the popup paints verbatim that "
            f"contains {marker!r} — a crashed CLI's traceback line leaked an "
            f"absolute path / Errno / Python type name into 'Failed: <error>' "
            f"(#43102d25). Got: {err!r}"
        )
    # Honest: still names what went wrong (the file/IO problem), not a blank.
    assert err.strip(), "the error must not be empty"
    # Actionable: points at the recovery verb the user can run.
    assert "trinity-local status" in err, (
        f"UNACTIONABLE: the dispatch-failure error tells the user it failed but "
        f"not what to DO — it must point at `trinity-local status`. Got: {err!r}"
    )


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no launchable chromium: {exc}")


def test_popup_dispatch_failure_ribbon_paints_no_leak():
    """Part (2): the popup's painted 'Failed: <error>' ribbon carries no leak."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # The host response the popup will receive — produced by the REAL host, so
    # if the sanitizer regresses this string carries the leak and the DOM asserts
    # below fire.
    host_error = _host_error_for_crash()

    # launch-council returns the host's sanitized synchronous failure (ok:false,
    # no detached) → popup falls to the `setStatus("Failed: " + extractError)`
    # branch and paints `host_error` into #status.
    stub = (
        "window.chrome = { runtime: { id: 'testext', lastError: null,\n"
        "  sendMessage: (m, cb) => {\n"
        "    if (m && m.kind === 'launch-council') {\n"
        "      setTimeout(() => cb({ ok: false, error: "
        + repr(host_error)
        + " }), 5); return;\n"
        "    }\n"
        "    setTimeout(() => cb({ ok: true }), 5);\n"
        "  } } };\n"
    )

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_context(
                viewport={"width": 460, "height": 760}
            ).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(stub)
            page.goto(f"file://{POPUP}")
            page.wait_for_selector("#compose", state="visible", timeout=5000)

            # PRECONDITION A: the action fires + the failure ribbon PAINTS.
            page.fill("#task", "SQLite or DuckDB?")
            page.click("#run-btn")
            page.wait_for_function(
                "document.getElementById('status').textContent.includes('Failed')",
                timeout=5000,
            )
            painted = page.evaluate(
                "document.getElementById('status').textContent"
            )

            # The founder symptom in the RENDERED pixels: no leaked internal.
            for marker in _LEAKS:
                assert marker not in painted, (
                    f"LEAK IN DOM: the popup's 'Failed: <error>' ribbon painted "
                    f"{marker!r} — a crashed CLI's traceback line reached the "
                    f"rendered pixels. Painted: {painted!r}"
                )
            # And it still tells the user what to do.
            assert "trinity-local status" in painted, (
                f"the painted dispatch-failure ribbon is not actionable: {painted!r}"
            )
            assert not errs, f"popup raised JS errors: {errs}"
        finally:
            browser.close()
