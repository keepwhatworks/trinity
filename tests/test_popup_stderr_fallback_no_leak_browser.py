"""The popup's `extractError` stderr FALLBACK must not leak — #43102d25.

Iter 243 sanitized the capture host's `error` field (`_run_action` routes the
last stderr line through `utils.safe_error_message`). But the popup's own
`extractError` (`browser-extension/popup.js`) builds a candidate list and falls
through to the RAW, UN-sanitized stderr when no `error` field is present:

    const candidates = [response.error, response.detail, response.hint,
      (response.stderr || "").trim().split("\\n").pop(), ...];

The host sets `error` ONLY when the CLI exits NON-ZERO. On the backward-compat
SYNCHRONOUS path (`response.ok === true` but `response.detached` is falsy — an
older capture host that ran the council inline), the popup falls to line 467
`setStatus("Failed: " + extractError(response))`, the host set NO `error` field
(returncode 0), and `extractError` reaches the raw `stderr` candidate. A CLI
that printed a Python traceback to stderr while exiting 0 then painted

    Failed: FileNotFoundError: [Errno 2] No such file or directory:
        '/Users/vishi/.trinity/conversations/x.json'

into the `#status` ribbon — a Python TYPE NAME, an `[Errno N]`, and an ABSOLUTE
`/Users/<name>/…` path, straight to the user (the #43102d25 leak Iter 243 closed
for the host `error` field but NOT for this fallback). VERIFIED 2026-06-21 by
driving the real popup: the painted `#status` carried all three leak tokens.

The fix adds `safeErrorMessage` to popup.js (a JS mirror of the Python choke
point) and routes EVERY `extractError` candidate through it, so the raw-stderr
fallback can't paint an internal.

This guard:
  (A) PRECONDITION — drives the REAL popup DOM with the discriminating input (a
      production-shape multi-line Python traceback in `stderr`, returncode 0,
      detached falsy → the path that reaches the raw fallback) and waits for the
      ribbon to PAINT "Failed: …" (the failure state is really triggered);
  (B) asserts the painted `#status` text carries NO absolute path / [Errno N] /
      Python type name / Traceback frame, AND stays honest ("No such file or
      directory" survives — the actionable human tail);
  plus a NEGATIVE CONTROL: a clean message ("usage limit reached — resets …")
  passed as the same stderr fallback survives verbatim (the sanitizer does not
  mangle already-clean copy).

Mutation proof: drop the `safeErrorMessage(...)` wrap in `extractError` (return
the raw candidate) → the painted ribbon leaks `/Users/` + `[Errno` again and
this guard reds with the founder symptom. Restore → green.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
POPUP = REPO / "browser-extension" / "popup.html"

# Internals that must NEVER reach the painted ribbon (the founder symptom).
_LEAKS = ("/Users/", "[Errno", "FileNotFoundError", "Traceback", "site-packages")

# The discriminating input: a production-shape Python traceback (REAL newlines,
# the way subprocess stderr arrives). The last non-frame line is the bare
# exception carrying a type name, an [Errno N], and an absolute path. Host
# returns returncode 0 with NO `error` field (the sync/backward-compat path), so
# extractError reaches the RAW stderr candidate.
_RAW_TRACEBACK_JS = (
    "['Traceback (most recent call last):',"
    " '  File \"/Users/vishi/.trinity/venv/lib/python3.11/site-packages/"
    "trinity_local/ingest.py\", line 88, in parse',"
    " '    data = json.loads(p.read_text())',"
    " \"FileNotFoundError: [Errno 2] No such file or directory: "
    "'/Users/vishi/.trinity/conversations/x.json'\"].join(String.fromCharCode(10))"
)

_STUB_TRACEBACK = (
    "window.chrome = { runtime: { id: 'testext', lastError: null,"
    "  sendMessage: (m, cb) => {"
    "    if (m && m.kind === 'launch-council') {"
    "      setTimeout(() => cb({ ok: true, detached: false, returncode: 0,"
    "        stdout: '', stderr: " + _RAW_TRACEBACK_JS + " }), 5); return;"
    "    }"
    "    setTimeout(() => cb({ ok: true }), 5);"
    "  } } };"
)

# Negative control: an ALREADY-CLEAN message arrives via the same stderr
# fallback — it must survive verbatim (the sanitizer keeps honest copy).
_STUB_CLEAN = (
    "window.chrome = { runtime: { id: 'testext', lastError: null,"
    "  sendMessage: (m, cb) => {"
    "    if (m && m.kind === 'launch-council') {"
    "      setTimeout(() => cb({ ok: true, detached: false, returncode: 0,"
    "        stdout: '', stderr: 'usage limit reached — resets Jun 12th, 2026' }), 5); return;"
    "    }"
    "    setTimeout(() => cb({ ok: true }), 5);"
    "  } } };"
)


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"no launchable chromium: {exc}")


def _drive_run(stub: str) -> tuple[str, list[str]]:
    """Load the real popup, stub chrome.runtime with `stub`, click Run, return
    the painted #status text + any JS pageerrors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_context(viewport={"width": 460, "height": 760}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.add_init_script(stub)
            page.goto(f"file://{POPUP}")
            page.wait_for_selector("#compose", state="visible", timeout=5000)
            # PRECONDITION B: ribbon starts empty → a later "Failed: …" is a real
            # transition (not pre-existing text).
            assert page.evaluate("document.getElementById('status').textContent") == "", \
                "precondition: #status ribbon should start empty"
            page.fill("#task", "SQLite or DuckDB?")
            page.click("#run-btn")
            # PRECONDITION A: the failure state is triggered + the ribbon PAINTS.
            page.wait_for_function(
                "document.getElementById('status').textContent.includes('Failed')",
                timeout=4000,
            )
            painted = page.evaluate("document.getElementById('status').textContent")
            return painted, errs
        finally:
            browser.close()


def test_popup_extracterror_stderr_fallback_paints_no_internal_leak():
    """A raw Python traceback in `stderr` (no `error` field) is sanitized before
    the popup paints "Failed: <error>" — no path / Errno / type name leaks."""
    pytest.importorskip("playwright.sync_api")

    painted, errs = _drive_run(_STUB_TRACEBACK)

    leaked = [tok for tok in _LEAKS if tok in painted]
    assert not leaked, (
        "#43102d25 LEAK: the popup's extractError stderr FALLBACK painted internals "
        f"into the #status ribbon: {leaked} — ribbon was {painted!r}. The raw "
        "subprocess traceback (FileNotFoundError: [Errno 2] … '/Users/<name>/…') "
        "reached the rendered pixels because extractError fell through to the "
        "un-sanitized `response.stderr` candidate. Route every candidate through "
        "safeErrorMessage (the JS mirror of utils.safe_error_message)."
    )
    # Honest: the human-readable tail survives so the user still learns WHAT failed.
    assert "No such file or directory" in painted, (
        f"sanitizer over-stripped the actionable tail; ribbon was {painted!r}"
    )
    assert painted.startswith("Failed:"), painted
    assert not errs, f"popup raised JS errors: {errs}"


def test_popup_extracterror_keeps_already_clean_message():
    """NEGATIVE CONTROL — a clean stderr message survives the sanitizer verbatim."""
    pytest.importorskip("playwright.sync_api")

    painted, errs = _drive_run(_STUB_CLEAN)

    assert "usage limit reached" in painted and "resets Jun 12th, 2026" in painted, (
        "the sanitizer mangled an already-clean message (it must only redact "
        f"paths/Errno/type-names, not honest copy); ribbon was {painted!r}"
    )
    leaked = [tok for tok in _LEAKS if tok in painted]
    assert not leaked, leaked
    assert not errs, f"popup raised JS errors: {errs}"
