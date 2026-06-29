"""A user who pastes a valid path that holds NO recognizable exports must see
the CLI's actionable diagnostic, not a useless "exit code 1".

USEFULNESS / NO-FEEDBACK defect (2026-06-17 UX sweep): the Bulk-import card's
Probe step (`import-export-dry-run`) discarded the CLI's own diagnostic. When the
path exists but contains nothing Trinity recognizes — the common mistake: pointing
at the parent Downloads folder, or an un-extracted Takeout `.zip` — the CLI prints
a STRUCTURED `{error: "no exports detected", hint: "Expected: conversations.json …"}`
to *stdout* and exits non-zero. The capture host fills `r.error` from *stderr*
(empty here) → falls back to "exit code 1", so the front-end's `r.ok === false`
branch rendered:

    ⚠ exit code 1
    Make sure the path exists and points at an export file or directory.

— throwing away the actionable guidance sitting in `r.stdout`. The fix parses the
structured diagnostic out of `r.stdout` first and surfaces THAT (what Trinity
expects), keeping the generic fallback only when stdout has nothing parseable
(true dispatch failures: CLI-not-on-PATH).

Mutation-provable: revert the stdout-parse in `probeImportPath` and the browser
test reds (the banner reads "exit code 1" instead of "no exports detected").
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_probe_error_branch_reads_structured_stdout():
    """CI-runnable canary: the import-probe error branch must parse the CLI's
    structured diagnostic out of r.stdout, not blindly use r.error."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    anchor = "extensionAction: {{ kind: 'import-export-dry-run', path: this.importPath }}"
    assert anchor in src, "probeImportPath dispatch changed — re-anchor this guard"
    after = src[src.index(anchor):]
    # Scope to the ERROR branch only (r.ok === false), which ends where the
    # success path begins ("// The host returns stdout"). The success path
    # ALSO parses r.stdout, so an un-scoped search would pass vacuously — the
    # whole bug was the error branch NOT parsing stdout.
    err_branch = after[: after.index("// The host returns stdout")]
    assert "r.ok === false" in err_branch, "error-branch anchor moved — re-scope guard"
    assert "r.stdout" in err_branch and "JSON.parse" in err_branch and "cliErr" in err_branch, (
        "the import-probe ERROR branch no longer reads the structured diagnostic "
        "from r.stdout — a 'no exports detected' result regresses to a useless "
        "'exit code 1' banner, discarding the CLI's actionable hint"
    )


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_import_probe_surfaces_cli_diagnostic_not_exit_code(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html

    # INNER pageData (not the {pageData:...} wrapper) — the known false-alarm shape.
    page_data = build_launchpad_payload()["pageData"]
    # The import card is a sibling of the browser-capture card under the same
    # <details>; seed browserCapture so the <details> exists (the import card is
    # always present, but seeding keeps the card layout realistic).
    page_data["browserCapture"] = {
        "has_data": False,
        "stale": False,
        "install_command": "trinity-local install-extension",
    }
    html = render_launchpad_html(page_data=page_data, view="stats")

    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    # The EXACT capture-host result shape for a valid path with no exports:
    # host ok=False (non-zero exit), error filled from EMPTY stderr → "exit code 1",
    # and the CLI's real structured diagnostic only in stdout.
    no_exports_stdout = json.dumps(
        {
            "ok": False,
            "error": "no exports detected",
            "hint": (
                "Expected: a file conversations.json (ChatGPT or Claude.ai), or a "
                "Gemini Takeout extract containing My Activity/Gemini Apps/"
                "MyActivity.html. Pass --source to force a parser if auto-detect "
                "gets it wrong."
            ),
            "path": "/Users/you/Downloads/wrongdir",
        }
    )
    host_result = {
        "ok": False,
        "returncode": 1,
        "stdout": no_exports_stdout,
        "stderr": "",
        "error": "exit code 1",
        "action": "import-export-dry-run",
    }

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    "  if (o && o.extensionAction"
                    "      && o.extensionAction.kind === 'import-export-dry-run'"
                    "      && o.onResult) { o.onResult("
                    + json.dumps(host_result)
                    + "); }"
                    " }, onStateChange: function(){}, isAvailable: function(){return true;} };"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # Fill the path + click Probe.
                page.fill("section.import-export-card input[type=text]", "/Users/you/Downloads/wrongdir")
                page.evaluate(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " c.querySelector('button').click(); }"
                )
                # Read the warning banner the user actually sees.
                banner = page.wait_for_function(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " const divs = Array.from(c.querySelectorAll('div'));"
                    " const warn = divs.find(d => d.textContent.trim().startsWith('\\u26a0'));"
                    " return warn ? warn.textContent : null; }",
                    timeout=4000,
                )
                text = banner.json_value()
                assert "no exports detected" in text, (
                    "the import-probe banner discarded the CLI's structured diagnostic — "
                    "a valid path with no exports must say 'no exports detected', not the "
                    f"useless 'exit code 1'. Banner was: {text!r}"
                )
                assert "Expected: a file conversations.json" in text, (
                    "the import-probe banner dropped the CLI's actionable hint (what "
                    f"Trinity expects). Banner was: {text!r}"
                )
                assert "exit code 1" not in text, (
                    "the import-probe banner still leaks the raw 'exit code 1' instead "
                    f"of the CLI diagnostic. Banner was: {text!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_import_confirm_no_dispatcher_gives_honest_feedback(tmp_path, monkeypatch):
    """Clicking "Import N source(s)" with NO dispatcher must surface the SAME
    honest {error, hint} banner as the Probe step — not a silent dead no-op.

    USEFULNESS / NO-FEEDBACK defect (2026-06-18 UX sweep, iter 112): the Bulk-import
    card's `confirmImport` no-dispatcher branch silently rolled `importStatus` back
    to idle and returned — the button flipped back to "Import N source(s)" with ZERO
    feedback, while its DIRECT SIBLING `probeImportPath` (same card, same
    no-dispatcher path) surfaces "No Chrome extension or Shortcut dispatcher
    available." + a terminal-command hint. The dispatcher CAN vanish between a
    successful Probe and this Import (the extension disabled/reloaded), and a dead
    silent button reads as broken (the brief's exact NO-FEEDBACK class).

    This drives the asymmetry for real: a successful Probe (dispatcher present) →
    the Import button appears → the dispatcher is removed → the Import click must now
    show the honest co-located error banner with the FULL `import-export --path …`
    command (no --dry-run), NOT silently no-op.

    Mutation-provable: revert `confirmImport`'s no-dispatcher branch to the bare
    `this.importStatus = 'idle'; return;` and this guard reds — no ⚠ banner appears
    and the Import button silently survives.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # INNER pageData (not the {pageData:...} wrapper — the known false-alarm shape).
    page_data = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    probe_path = "/Users/you/Downloads/exports"
    probe_ok = json.dumps(
        {"ok": True, "stdout": json.dumps({"detected": [{"source": "chatgpt", "hint": "12 conversations"}]})}
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1100}).new_page()
                # A dispatcher that answers ONLY the dry-run probe (success). It
                # does NOT answer import-export — but we remove it before that click
                # anyway, to drive the no-dispatcher branch.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    "  if (o && o.extensionAction"
                    "      && o.extensionAction.kind === 'import-export-dry-run'"
                    "      && o.onResult) { o.onResult("
                    + probe_ok
                    + "); }"
                    " }, onStateChange: function(){}, isAvailable: function(){return true;} };"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # Probe a path → the Import button must appear.
                page.fill("section.import-export-card input[type=text]", probe_path)
                page.evaluate(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " c.querySelector('button').click(); }"
                )
                page.wait_for_function(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " return Array.from(c.querySelectorAll('button')).some("
                    "   b => /import\\s+\\d+\\s+source/i.test(b.innerText)); }",
                    timeout=4000,
                )
                # The dispatcher vanishes (extension disabled/reloaded after the probe).
                page.evaluate("() => { delete window.__TRINITY_DISPATCH__; }")
                # Click the "Import N source(s)" button.
                clicked = page.evaluate(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " const b = Array.from(c.querySelectorAll('button.suggestion-chip'))"
                    "   .find(x => /import\\s+\\d+\\s+source/i.test(x.innerText));"
                    " if (b) { b.click(); return b.innerText.trim(); } return null; }"
                )
                assert clicked, "the Import N source(s) button never rendered after a successful probe"

                # The honest banner must now appear — NOT a silent no-op.
                banner = page.wait_for_function(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " const divs = Array.from(c.querySelectorAll('div'));"
                    " const warn = divs.find(d => d.textContent.trim().startsWith('\\u26a0'));"
                    " return warn ? warn.textContent : null; }",
                    timeout=4000,
                )
                text = banner.json_value()
                assert "No Chrome extension or Shortcut dispatcher available" in text, (
                    "the Import step gave NO feedback on the no-dispatcher path — a dead "
                    "silent no-op while its Probe sibling shows an honest error. The "
                    f"⚠ banner was: {text!r}"
                )
                # The hint must carry the FULL import command (no --dry-run), with the
                # user's path interpolated — so they can finish from a terminal.
                assert "trinity-local import-export --path " + probe_path in text, (
                    "the no-dispatcher Import banner dropped the actionable terminal "
                    f"command (`import-export --path <PATH>`). Banner was: {text!r}"
                )
                assert "--dry-run" not in text, (
                    "the Import banner hint must point at the FULL ingest command, not "
                    f"the --dry-run probe. Banner was: {text!r}"
                )
                # The success div (and its Import button) must be GONE — the error
                # branch replaces it, so the user isn't staring at a still-clickable
                # button that just no-op'd.
                still_has_import = page.evaluate(
                    "() => { const c = document.querySelector('section.import-export-card');"
                    " return Array.from(c.querySelectorAll('button')).some("
                    "   b => /import\\s+\\d+\\s+source/i.test(b.innerText)); }"
                )
                assert not still_has_import, (
                    "the 'Import N source(s)' button survived the no-dispatcher click — "
                    "it must be replaced by the honest error banner, not silently re-armed"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_import_card_probe_button_no_child_escape_at_320(tmp_path, monkeypatch):
    """The Bulk-import card's path-input + Probe button row must not spill a
    child past the 320px viewport (UX sweep iter 93 — the CHILD-ESCAPES-CARD
    overflow class, the same shape iter 92 fixed on the #252 timeline card).

    Root cause: the path <input> carries `flex: 1` but the CSS default
    `min-width: auto` pins it at its intrinsic ~186px text-input min-content, so
    at viewport widths <=360px the flex row refuses to shrink and the "Probe"
    button is shoved 0.8px past the 320px viewport (right edge frozen at 320.8) —
    a page-level horizontal overflow on a phone. The CARD's own bounding box stays
    within 320 (cardRight~289), so a card-box-only guard is BLIND to it: the probe
    must check EVERY DESCENDANT's right edge, not just the card's.

    Fix (`min-width: 0` on the input + `flex-shrink: 0` on the button +
    `flex-wrap`): the input shrinks, the button stays inside the viewport, and at
    sub-280 it wraps below the input instead of escaping.

    Mutation-proven: revert the input to `flex: 1` (min-width:auto) and this guard
    reds — the Probe button's right edge crosses 320.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # INNER pageData (not the {pageData:...} wrapper — the known false-alarm shape).
    # The import-export card is always present regardless of corpus data.
    page_data = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                W = 320
                page = browser.new_context(
                    viewport={"width": W, "height": 2200}
                ).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                probe = page.evaluate(
                    r"""(W) => {
                      const card = document.querySelector('section.import-export-card');
                      if (!card) return {missing: true};
                      const cb = card.getBoundingClientRect();
                      // The child-escape probe: EVERY visible descendant of the
                      // import card must keep its right edge within the viewport —
                      // NOT merely the card's own box (cardRight stays < 320 even
                      // while the Probe button spills past it).
                      const escapers = [];
                      for (const el of card.querySelectorAll('*')) {
                        const cs = getComputedStyle(el);
                        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                        // An overflow-x:auto/scroll ancestor makes spill intentional.
                        let inScroll = false, a = el.parentElement;
                        while (a && a !== card.parentElement) {
                          const acs = getComputedStyle(a);
                          if ((acs.overflowX === 'auto' || acs.overflowX === 'scroll')
                              && a.scrollWidth > a.clientWidth) { inScroll = true; break; }
                          a = a.parentElement;
                        }
                        if (inScroll) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 && r.height === 0) continue;
                        // 0.5 tolerance (not 1.0): the un-fixed Probe button lands at
                        // right=320.8 on a 320px viewport — a real escape that a +1
                        // tolerance would mask. Sub-pixel layout rounding is < 0.5.
                        if (r.right > W + 0.5) {
                          escapers.push({
                            tag: el.tagName.toLowerCase(),
                            cls: (el.className || '').toString().slice(0, 40),
                            right: Math.round(r.right * 10) / 10,
                            text: (el.innerText || '').slice(0, 24).replace(/\s+/g, ' ').trim(),
                          });
                        }
                      }
                      return {
                        cardRight: Math.round(cb.right * 10) / 10,
                        docOverflow: document.documentElement.scrollWidth
                                     > document.documentElement.clientWidth,
                        scrollW: document.documentElement.scrollWidth,
                        clientW: document.documentElement.clientWidth,
                        escapers,
                      };
                    }""",
                    W,
                )
                assert not probe.get("missing"), (
                    "the Bulk-import card disappeared from /stats — the import-export "
                    "card must always render (it's data-independent)"
                )
                # Non-vacuous: the card's own box fits, proving the card-box guard
                # would have passed while a child still escaped.
                assert probe["cardRight"] <= W + 1, (
                    f"the import card's OWN box overflows @320 (right {probe['cardRight']}) "
                    "— unexpected; the child-escape probe below is what should fire"
                )
                assert not probe["escapers"], (
                    f"a descendant of the Bulk-import card escapes the {W}px viewport: "
                    f"{probe['escapers']!r}. The path-input's `flex:1` + default "
                    "`min-width:auto` pins it at its ~186px intrinsic min-content, so the "
                    "row can't shrink and the 'Probe' button is shoved past the viewport "
                    "(iter 92 timeline child-escape class) — restore `min-width:0` on the "
                    "input + `flex-shrink:0` on the button + flex-wrap on the row"
                )
                assert not probe["docOverflow"], (
                    f"/stats @{W} has a page-level horizontal overflow "
                    f"(scrollWidth {probe['scrollW']} > clientWidth {probe['clientW']}) "
                    "— a child escaped a card past the viewport"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_import_path_input_has_accessible_name(tmp_path, monkeypatch):
    """The Bulk-import path <input> — a real typing field — must expose a
    PROGRAMMATIC accessible name, not just a placeholder sample path.

    WCAG 1.3.1 / 4.1.2 (a11y sweep iter 235): the `importPath` text input shipped
    with only `placeholder="/Users/you/Downloads/Takeout"` and NO `<label for>`,
    `aria-label`, or `aria-labelledby`. The browser's AccName algorithm then falls
    back to the placeholder, so a screen-reader user focusing the field hears the
    SAMPLE PATH ("/Users/you/Downloads/Takeout") as the field's name — which (a)
    vanishes the moment they type (then the field announces "blank"/"edit text") and
    (b) never says what the field is FOR. The visible <h2> "Import old Claude /
    ChatGPT / Gemini exports" and the explanatory <p> are NOT programmatically
    associated, so AT gets nothing useful.

    This reads the input's COMPUTED accessible name from Chromium's real
    Accessibility tree (CDP getPartialAXTree) — what an actual screen reader
    announces — not a string grep.

    Mutation-proven: remove the `aria-label` in launchpad_template.py and this guard
    reds — the computed name collapses back to the placeholder echo
    "/Users/you/Downloads/Takeout".
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # INNER pageData (not the {pageData:...} wrapper — the known false-alarm shape).
    # The import-export card is always present regardless of corpus data.
    page_data = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    SELECTOR = "section.import-export-card input[type=text]"
    PLACEHOLDER = "/Users/you/Downloads/Takeout"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1100}
                ).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                el = page.query_selector(SELECTOR)
                # PRECONDITION A — the control PAINTS and is a real typing input.
                assert el is not None, "the Bulk-import path input vanished from /stats"
                assert el.is_visible(), (
                    "the Bulk-import path input is not painted on /stats — can't "
                    "assert its accessible name on a hidden control"
                )
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                input_type = el.evaluate("e => (e.getAttribute('type') || 'text')")
                assert tag == "input" and input_type == "text", (
                    f"expected a typing <input type=text>, got <{tag} type={input_type}> "
                    "— re-anchor this accessible-name guard"
                )
                # PRECONDITION B — the placeholder IS the sample path (so a name equal
                # to it proves the placeholder-fallback regression, not a real label).
                assert el.evaluate("e => e.getAttribute('placeholder')") == PLACEHOLDER, (
                    "the import-path placeholder changed — re-anchor the placeholder-echo "
                    "check so the assertion stays discriminating"
                )

                # Read the COMPUTED accessible name from Chromium's real AX tree.
                client = page.context.new_cdp_session(page)
                doc = client.send("DOM.getDocument", {"depth": 0})
                root_id = doc["root"]["nodeId"]
                nid = client.send(
                    "DOM.querySelector", {"nodeId": root_id, "selector": SELECTOR}
                )["nodeId"]
                ax = client.send(
                    "Accessibility.getPartialAXTree",
                    {"nodeId": nid, "fetchRelatives": False},
                )
                ax_name = None
                ax_role = None
                for n in ax.get("nodes", []):
                    nm = n.get("name", {})
                    if nm:
                        ax_name = nm.get("value")
                        ax_role = (n.get("role", {}) or {}).get("value")
                        break

                assert ax_role == "textbox", (
                    f"the import-path input did not expose role=textbox to AT (got "
                    f"{ax_role!r}) — re-anchor this guard"
                )
                assert ax_name, (
                    "the Bulk-import path input has an EMPTY computed accessible name — "
                    "a screen-reader user focusing it hears 'edit text'/'blank' with no "
                    "idea what to type (WCAG 1.3.1/4.1.2). Add an aria-label (or wired "
                    "<label for>) naming what to paste."
                )
                assert ax_name.strip() != PLACEHOLDER, (
                    "the Bulk-import path input's ONLY accessible name is the placeholder "
                    f"sample path ({PLACEHOLDER!r}) — a placeholder is NOT a label: it "
                    "vanishes the moment the user types and never says what the field is "
                    "FOR. A screen reader announces the literal example path as the field "
                    "name (WCAG 1.3.1/4.1.2). Add an aria-label naming what to paste."
                )
                # Meaningful: the name must describe the import target, not be a stray
                # token. (Both 'import' and a path noun anchor a real description.)
                low = ax_name.lower()
                assert "import" in low or "takeout" in low or "export" in low, (
                    "the import-path input's accessible name does not describe what to "
                    f"type (a path to import). Computed name was: {ax_name!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
