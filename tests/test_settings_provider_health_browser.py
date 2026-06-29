"""The settings modal's PROVIDER-HEALTH list + endpoint placeholder-hiding must
render honestly in a real browser.

UX-sweep Iter 27 (2026-06-17). The settings modal had real-browser coverage for
the sharing toggle (test_file_substrate_render.py) and string-level coverage for
the privacy copy (test_telemetry_no_pii.py / test_frontend_flow.py), but TWO
settings sub-surfaces were never rendered + asserted in a browser:

  1. The `.provider-health-list` "Providers" section — the install-command rows
     (label · "Missing" badge · detail · copyable install command) that appear
     `v-if="providerHealth.hasMissing"`. On the founder's machine every provider
     resolves (the runtime-env PATH finds the binaries), so `hasMissing` is always
     False there — the section never renders, so it was never driven. This forces
     the missing-provider payload (the legit harness technique) and asserts the
     rows render with the right label/badge/command AND the per-row copy chip
     flashes ✓ on click (⧉ → ✓) — the same confirmation pattern the rebuild chips
     use, without which the copy "feels unobservable".

  2. `displayedEndpoint` placeholder-hiding — a dev/test endpoint
     (example.invalid / localhost / 127.0.0.1) must render "Not configured" so the
     user doesn't think a broken URL is intentional, while a REAL endpoint renders
     verbatim, and an empty id renders "unassigned".

This drives the REAL petite-vue render (not a string-presence check) and is
mutation-provable: drop the v-if/`copiedKey` chip wiring or the displayedEndpoint
placeholder branch and the corresponding assertion reds with the exact symptom.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The missing-provider payload _provider_health_data() emits when codex + agy are
# absent from PATH. Crafted here because this machine has every provider installed
# (the runtime-env PATH resolves the binaries), so the natural render is hasMissing
# == False and the "Providers" section never appears.
_MISSING_PROVIDER_HEALTH = {
    "providers": [
        {
            "provider": "codex",
            "label": "Codex CLI",
            "installed": False,
            "detail": "codex not found in PATH",
            "installCommand": "npm install -g @openai/codex && codex --login",
        },
        {
            "provider": "antigravity",
            "label": "Antigravity",
            "installed": False,
            "detail": "agy not found in PATH",
            "installCommand": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
        },
    ],
    "missingCount": 2,
    "hasMissing": True,
    "footerNote": (
        "After installing, open a new terminal and run `trinity-local status`. "
        "Trinity will pick up newly installed providers automatically."
    ),
}


def _serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _render(tmp_path: Path, *, provider_health: dict | None, telemetry_settings: dict) -> Path:
    """Render the home launchpad with a crafted providerHealth + telemetry settings
    payload, serve it, and return the served directory."""
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # build_launchpad_payload() returns the WRAPPED {pageData, ...}; the renderer
    # wants the INNER pageData (passing the wrapper makes every binding undefined —
    # the Iter 4 harness trap).
    payload = build_launchpad_payload()
    page_data = payload["pageData"]
    if provider_health is not None:
        page_data["providerHealth"] = provider_health
    page_data["telemetry"] = {"settings": telemetry_settings}

    html = render_launchpad_html(page_data=page_data, view="home")
    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return tmp_path / "serve"


def _open_settings(page, port: int) -> None:
    page.add_init_script(
        "window.__TRINITY_DISPATCH__ = {_calls: [],"
        " dispatch(r){ this._calls.push(r);"
        "   if (r.onResult) r.onResult({ok:false, error:'no extension'});"
        "   return Promise.resolve({ok:false}); },"
        " onStateChange(){} };"
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
    page.locator("[aria-label='Open settings']").first.click()
    page.wait_for_selector(".settings-modal", state="visible", timeout=4000)


@pytest.mark.slow
@pytest.mark.browser
def test_settings_provider_health_list_renders_and_copy_chip_flashes(tmp_path, monkeypatch):
    """When a provider is missing, the settings modal's Providers section renders the
    install rows AND the per-row copy chip flashes ✓ on click."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    serve_dir = _render(
        tmp_path,
        provider_health=_MISSING_PROVIDER_HEALTH,
        telemetry_settings={
            "sharing_enabled": True,
            "endpoint": "",
            "share_install_id": "share_abc123",
        },
    )
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 393, "height": 1400}
                ).new_page()
                errs: list[str] = []
                page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
                _open_settings(page, port)

                rows = page.evaluate(
                    """() => {
                        const phl = document.querySelector('.settings-modal .provider-health-list');
                        if (!phl) return {present: false};
                        const items = [...phl.querySelectorAll('.provider-health-item')].map(it => ({
                            label: (it.querySelector('strong') || {}).innerText,
                            badge: (it.querySelector('.badge') || {}).innerText,
                            command: (it.querySelector('.provider-command code') || {}).innerText,
                            hasCopyBtn: !!it.querySelector('.provider-command .icon-action'),
                        }));
                        return {
                            present: true,
                            items,
                            raw: phl.innerText.includes('{{'),
                            // The list right edge must stay inside the 393px viewport.
                            maxRight: Math.max(...[...phl.querySelectorAll('*')]
                                .map(el => el.getBoundingClientRect().right)),
                        };
                    }"""
                )
                assert rows["present"], (
                    "the settings modal's Providers section is GONE for a missing "
                    "provider — the user has no in-settings install affordance"
                )
                assert len(rows["items"]) == 2, f"expected 2 missing-provider rows, got {rows['items']}"
                labels = {r["label"] for r in rows["items"]}
                assert labels == {"Codex CLI", "Antigravity"}, labels
                for r in rows["items"]:
                    assert r["badge"] == "Missing", f"missing-provider badge wrong: {r}"
                    assert r["command"], f"install command missing for {r['label']}"
                    assert r["hasCopyBtn"], f"no copy chip for {r['label']} — the copy feels unobservable"
                assert not rows["raw"], "raw petite-vue template ({{ }}) leaked in the provider list"
                assert rows["maxRight"] <= 393 + 1, (
                    f"provider-health list overflows the 393px viewport (maxRight={rows['maxRight']})"
                )

                # The copy chip must flash ⧉ → ✓ so the copy is observable.
                chip = page.evaluate(
                    """() => {
                        const btn = document.querySelector(
                            '.provider-health-list .provider-health-item .provider-command .icon-action');
                        const before = btn.innerText.trim();
                        btn.click();
                        return {before};
                    }"""
                )
                page.wait_for_function(
                    "() => { const b = document.querySelector("
                    "'.provider-health-list .provider-health-item .provider-command .icon-action');"
                    " return b && b.innerText.trim() === '✓'; }",
                    timeout=3000,
                )
                assert chip["before"] == "⧉", f"copy chip did not start as ⧉ (was {chip['before']!r})"
                assert not errs, f"console errors driving the provider-health list: {errs}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_settings_sharing_toggle_has_accessible_name(tmp_path, monkeypatch):
    """The settings telemetry "Sharing enabled" TOGGLE (an `<input type=checkbox>`
    inside `<label class="toggle-switch">`) must compute a NON-EMPTY accessible name
    that carries its visible "Sharing enabled" text.

    Founder symptom (UX-sweep Iter 236, WCAG 4.1.2 / 1.3.1): the `<label
    class="toggle-switch">` wraps ONLY the checkbox + the decorative `.toggle-slider`
    span; the visible "Sharing enabled" text lives in a SIBLING `.meta` span that is
    NOT associated. So the checkbox's computed accessible name is EMPTY — a
    screen-reader user hears "checkbox, not checked" with no idea what the
    privacy/telemetry toggle controls. The fix associates the visible text via
    `aria-labelledby` (NOT a redundant bolted-on aria-label) so the SAME visible text
    becomes the programmatic name.

    This reads the COMPUTED accessible name off Chromium's real Accessibility tree
    (CDP getPartialAXTree) — what an actual screen reader announces, not an attribute
    guess. Mutation-provable: drop the aria-labelledby (or the text's id) and the AX
    name empties → this reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    serve_dir = _render(
        tmp_path,
        provider_health=None,
        telemetry_settings={
            "sharing_enabled": True,
            "endpoint": "",
            "share_install_id": "share_abc123",
        },
    )
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
                cdp = page.context.new_cdp_session(page)
                cdp.send("Accessibility.enable")
                _open_settings(page, port)

                # PRECONDITION A: the toggle PAINTS as a real checkbox widget. The
                # input itself is visually-hidden by design (opacity:0/0x0 — the
                # slider is the visual control), so anchor "paints" on the VISIBLE
                # label+slider that the user actually sees and operates.
                geom = page.evaluate(
                    """() => {
                        const cb = document.querySelector('.sharing-toggle input[type=checkbox]');
                        const lbl = document.querySelector('.sharing-toggle label.toggle-switch');
                        const sld = document.querySelector('.sharing-toggle .toggle-slider');
                        if (!cb || !lbl || !sld) return {present: false};
                        const lr = lbl.getBoundingClientRect();
                        return {
                            present: true,
                            tag: cb.tagName,
                            type: cb.type,
                            labelW: lr.width, labelH: lr.height,
                            sliderPaints: sld.getClientRects().length > 0,
                        };
                    }"""
                )
                assert geom["present"], (
                    "the settings 'Sharing enabled' toggle is GONE — can't audit its name"
                )
                assert geom["tag"] == "INPUT" and geom["type"] == "checkbox", (
                    f"the sharing toggle is not a real checkbox: {geom}"
                )
                assert geom["sliderPaints"] and geom["labelW"] >= 44 and geom["labelH"] >= 20, (
                    f"the sharing toggle's visual control does not paint a real tap target: {geom}"
                )

                # PRECONDITION B (discriminating): the visible label text exists, so an
                # EMPTY computed name is a real wiring gap (not a no-text control).
                visible_text = page.evaluate(
                    "() => (document.querySelector('.sharing-toggle .meta') || {}).innerText"
                )
                assert visible_text and visible_text.strip(), (
                    "no visible 'Sharing enabled' text — precondition for the name guard absent"
                )

                # Read the COMPUTED accessible name off the real AX tree.
                doc = cdp.send("DOM.getDocument")
                node = cdp.send(
                    "DOM.querySelector",
                    {"nodeId": doc["root"]["nodeId"], "selector": ".sharing-toggle input[type=checkbox]"},
                )
                ax = cdp.send(
                    "Accessibility.getPartialAXTree",
                    {"nodeId": node["nodeId"], "fetchRelatives": False},
                )
                primary = ax["nodes"][0]
                ax_role = (primary.get("role") or {}).get("value")
                ax_name = ((primary.get("name") or {}).get("value") or "").strip()

                assert ax_role in ("checkbox", "switch"), (
                    f"the sharing toggle's AX role is {ax_role!r}, not a checkbox/switch"
                )
                assert ax_name, (
                    "the settings 'Sharing enabled' telemetry toggle computes an EMPTY "
                    "accessible name — its <label class='toggle-switch'> wraps only the "
                    "checkbox + decorative slider, and the visible 'Sharing enabled' text "
                    "is an UNASSOCIATED sibling. A screen reader announces 'checkbox, not "
                    "checked' with no idea what the privacy toggle controls. Associate the "
                    "visible text (aria-labelledby / wrap it in the label) — WCAG 4.1.2 / 1.3.1."
                )
                assert "sharing" in ax_name.lower(), (
                    f"the sharing toggle's accessible name {ax_name!r} does not carry its "
                    f"visible 'Sharing enabled' text (visible: {visible_text!r}) — the "
                    "programmatic name must match the visible label intent (WCAG 2.5.3)"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_settings_sharing_toggle_label_tracks_state(tmp_path, monkeypatch):
    """The settings telemetry toggle's LABEL (which is ALSO its accessible name via
    aria-labelledby) must NAME THE CURRENT STATE — "Sharing enabled" when the
    checkbox is checked, "Sharing disabled" when it is not.

    Founder symptom (UX-sweep): the label was a STATIC literal "Sharing enabled".
    When a user had turned sharing OFF (sharing_enabled=False → checkbox UNCHECKED),
    the label STILL read "Sharing enabled" beside the off switch — a
    self-contradiction the user (and, via aria-labelledby, the screen reader naming
    this checkbox: "Sharing enabled, checkbox, NOT checked") sees. This is the exact
    "don't claim sharing is on when it's off" class the body status paragraph above
    the toggle (`v-else-if="telemetry.enabled"`) was already careful about — the
    toggle's own label was the asymmetric sibling left static. The fix binds the
    label to `telemetry.enabled` so name + checkbox state agree.

    Drives the REAL petite-vue render in BOTH states and asserts the rendered label
    matches the checkbox. Mutation-provable: revert the label to the static "Sharing
    enabled" literal and the DISABLED case reds (label says enabled while unchecked).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # (sharing_enabled, expected checkbox checked, expected label text)
    cases = [
        (True, True, "Sharing enabled"),
        (False, False, "Sharing disabled"),
    ]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            for i, (enabled, exp_checked, exp_label) in enumerate(cases):
                serve_dir = _render(
                    tmp_path / f"state{i}",
                    provider_health=None,
                    telemetry_settings={
                        "sharing_enabled": enabled,
                        # A real configured endpoint so build/serve doesn't pop it;
                        # irrelevant to the label, which keys only on telemetry.enabled.
                        "endpoint": "https://t.keepwhatworks.com/v1/events",
                        "share_install_id": "share_state",
                    },
                )
                httpd, port = _serve(serve_dir)
                try:
                    page = browser.new_context(viewport={"width": 393, "height": 1400}).new_page()
                    errs: list[str] = []
                    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                    page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
                    _open_settings(page, port)

                    state = page.evaluate(
                        """() => {
                            const lbl = document.getElementById('sharing-toggle-label');
                            const cb = document.querySelector('.sharing-toggle input[type=checkbox]');
                            return {
                                present: !!(lbl && cb),
                                label: lbl ? lbl.innerText.trim() : null,
                                checked: cb ? cb.checked : null,
                                raw: lbl ? lbl.innerText.includes('{{') : false,
                            };
                        }"""
                    )
                    # PRECONDITION (non-vacuous): the toggle + its label render and the
                    # checkbox reflects the seeded state — so a wrong LABEL is a real
                    # name/state mismatch, not an absent control.
                    assert state["present"], "the sharing toggle + label are GONE — can't audit the label"
                    assert not state["raw"], "raw petite-vue ({{ }}) leaked in the sharing-toggle label"
                    assert state["checked"] == exp_checked, (
                        f"sharing_enabled={enabled} but checkbox.checked={state['checked']} "
                        f"(expected {exp_checked}) — the toggle doesn't reflect the seeded state"
                    )
                    # THE BITE: the label must name the CURRENT state.
                    assert state["label"] == exp_label, (
                        f"sharing_enabled={enabled} (checkbox checked={state['checked']}) but the "
                        f"toggle label reads {state['label']!r}, expected {exp_label!r}. A STATIC "
                        '"Sharing enabled" label beside an OFF switch claims sharing is on when it '
                        "is off — the same self-contradiction the body status paragraph guards "
                        "against; via aria-labelledby a screen reader announces the wrong state too."
                    )
                    assert not errs, f"console errors driving the sharing toggle (enabled={enabled}): {errs}"
                    page.close()
                finally:
                    httpd.shutdown()
        finally:
            browser.close()


@pytest.mark.slow
@pytest.mark.browser
def test_settings_endpoint_hides_placeholder_shows_real(tmp_path, monkeypatch):
    """displayedEndpoint renders 'Not configured' for a dev/placeholder endpoint and
    'unassigned' for an empty id, but a REAL endpoint verbatim."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    cases = [
        # (endpoint, share_install_id) -> (expected endpoint text, expected id text)
        ("https://example.invalid/events", "", ("Not configured", "unassigned")),
        ("http://localhost:8080/v1", "share_x", ("Not configured", "share_x")),
        ("https://t.keepwhatworks.com/v1/events", "share_real", ("https://t.keepwhatworks.com/v1/events", "share_real")),
    ]

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            for i, (endpoint, share_id, (exp_ep, exp_id)) in enumerate(cases):
                serve_dir = _render(
                    tmp_path / f"case{i}",
                    provider_health=None,
                    telemetry_settings={
                        "sharing_enabled": True,
                        "endpoint": endpoint,
                        "share_install_id": share_id,
                    },
                )
                httpd, port = _serve(serve_dir)
                try:
                    page = browser.new_context(viewport={"width": 393, "height": 1400}).new_page()
                    errs: list[str] = []
                    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                    page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
                    _open_settings(page, port)
                    shown = page.evaluate(
                        """() => {
                            const rows = [...document.querySelectorAll('.settings-modal .setting-row')];
                            const ep = rows.find(r => /^Endpoint/.test(r.innerText));
                            const id = rows.find(r => /^Anonymous/.test(r.innerText));
                            return {
                                endpoint: ep ? ep.innerText.replace(/^Endpoint\\s*/, '').trim() : null,
                                anonId: id ? id.innerText.replace(/^Anonymous ID\\s*/, '').trim() : null,
                            };
                        }"""
                    )
                    assert shown["endpoint"] == exp_ep, (
                        f"endpoint {endpoint!r} rendered {shown['endpoint']!r}, expected {exp_ep!r} "
                        "(a dev/placeholder endpoint must read 'Not configured', a real URL must show)"
                    )
                    assert shown["anonId"] == exp_id, (
                        f"anon id {share_id!r} rendered {shown['anonId']!r}, expected {exp_id!r}"
                    )
                    assert not errs, f"console errors on endpoint case {endpoint!r}: {errs}"
                    page.close()
                finally:
                    httpd.shutdown()
        finally:
            browser.close()


@pytest.mark.slow
@pytest.mark.browser
@pytest.mark.parametrize("width", [393, 375, 360, 320])
def test_settings_modal_with_long_endpoint_url_does_not_clip_off_screen(
    tmp_path, monkeypatch, width
):
    """The OPENED settings modal must fit a narrow (side-panel / phone) viewport
    even when a REAL configured collector endpoint is a long separator-free URL.

    Founder symptom: the endpoint VALUE `<span class="meta">{{ displayedEndpoint }}</span>`
    is a flex child of `.setting-row` with the flex default `min-width:auto`. A real
    GA4/collector endpoint (a long URL with no break opportunities) pins that child's
    min-content width, pushing `.setting-row` → the `.card` flex item PAST the viewport
    — so the WHOLE settings modal (header, ×, body, every control) clips off BOTH edges
    and is unreachable on the side panel. The global initial-DOM overflow guard CANNOT
    see this: the modal is `display:none` (settingsOpen=false) until the gear is clicked,
    so this guard OPENS it first. Fixed by `.setting-row > .meta:last-child { min-width:0;
    overflow-wrap:anywhere; word-break:break-word }` so the URL wraps inside the row.

    Reads from src render_launchpad_html (file:// path) — no bundle rebuild needed.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # A real, non-dev collector endpoint that survives displayedEndpoint's
    # placeholder filter (not example.invalid / localhost) and is long + has the
    # query-string separators a GA4 measurement-protocol URL carries.
    long_endpoint = (
        "https://www.google-analytics.com/mp/collect"
        "?measurement_id=G-ABCDEF1234&api_secret=verylongsecrettoken_padmorepadmore"
    )
    serve_dir = _render(
        tmp_path,
        provider_health=_MISSING_PROVIDER_HEALTH,
        telemetry_settings={
            "sharing_enabled": True,
            "endpoint": long_endpoint,
            "share_install_id": "share_abcdef0123456789abcdef0123456789_padding_more",
        },
    )
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": width, "height": 1400}
                ).new_page()
                _open_settings(page, port)

                # PRECONDITION A (non-vacuous): the long endpoint actually rendered
                # in the modal (else the test isn't exercising the overflow case).
                ep_text = page.evaluate(
                    """() => {
                        const rows = [...document.querySelectorAll('.settings-modal .setting-row')];
                        const ep = rows.find(r => /^Endpoint/.test(r.innerText));
                        return ep ? ep.innerText : '';
                    }"""
                )
                assert "google-analytics.com" in ep_text, (
                    f"long endpoint did not render in the modal at {width}px "
                    f"(precondition failed — got {ep_text!r}); test would be vacuous"
                )

                geom = page.evaluate(
                    """(vw) => {
                        const modal = document.querySelector('.settings-modal');
                        const card = modal.querySelector('.card');
                        const cr = card.getBoundingClientRect();
                        const de = document.documentElement;
                        // any element inside the modal whose edge passes the viewport
                        const spill = [];
                        modal.querySelectorAll('*').forEach(el => {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && (r.right > vw + 1 || r.left < -1)) {
                                spill.push({tag: el.tagName, cls: String(el.className).slice(0,40),
                                            left: Math.round(r.left), right: Math.round(r.right)});
                            }
                        });
                        return {
                            cardLeft: Math.round(cr.left),
                            cardRight: Math.round(cr.right),
                            cardWidth: Math.round(cr.width),
                            docScrollWidth: de.scrollWidth,
                            docClientWidth: de.clientWidth,
                            spillCount: spill.length,
                            spill: spill.slice(0, 6),
                        };
                    }""",
                    width,
                )

                # The card must sit WITHIN the viewport — not hang off either edge.
                assert geom["cardLeft"] >= -1, (
                    f"settings-modal .card clips off the LEFT edge at {width}px "
                    f"(left={geom['cardLeft']}) — the long endpoint URL pinned the "
                    f"flex card's min-content width past the viewport: {geom}"
                )
                assert geom["cardRight"] <= width + 1, (
                    f"settings-modal .card clips off the RIGHT edge at {width}px "
                    f"(right={geom['cardRight']}, viewport={width}) — the long endpoint "
                    f"URL pinned the flex card's min-content width past the viewport: {geom}"
                )
                assert geom["spillCount"] == 0, (
                    f"{geom['spillCount']} element(s) in the settings modal spill past the "
                    f"{width}px viewport — the long endpoint URL blows the card off-screen: "
                    f"{geom['spill']}"
                )
                assert geom["docScrollWidth"] <= geom["docClientWidth"] + 1, (
                    f"document horizontally overflows at {width}px with the modal open: {geom}"
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
