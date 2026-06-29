"""Browser guard for the extension's STANDALONE side-panel view — the "download
your transcripts" tool that works with NO CLI/host — and the host-present launchpad
switch (sidepanel-shell.js host-detection).

This surface (the slice-2 fusion-first extension work) had no automated guard; it
was a "founder verifies in real Chrome" step. The persona sweep proved it's
headlessly testable via the harness's load-unpacked + safe stub-host helpers, so
this pins both branches of the host-detection so a regression can't silently ship:

  • NO native host → the standalone card (#standalone) renders, the launchpad
    iframe (#app) is hidden, seeded chrome.storage captures count up with a
    per-provider breakdown, and Download → chrome.downloads writes the bundle
    (the status line reports the Downloads/trinity/... save path).
  • host present    → the launchpad iframe (#app) renders, #standalone hidden.

Slow + browser marked; skips cleanly without Playwright/chromium. Reuses
scripts/extension_harness.py (the SAFE stub-host registration — never the real
Chrome NativeMessaging dir, #265 — backed up + restored)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _scripts_on_path():
    """Put scripts/ on sys.path INSIDE a fixture, not at module import — a
    module-level sys.path.insert leaks into every later test in the suite
    (test_no_module_level_env_mutation guards against exactly that)."""
    p = str(REPO / "scripts")
    if p not in sys.path:
        sys.path.insert(0, p)


def _launch(p, user_data: Path):
    from extension_harness import EXT

    return p.chromium.launch_persistent_context(
        user_data_dir=str(user_data), headless=False, accept_downloads=True,
        args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
    )


def _await_sw(ctx):
    if not ctx.service_workers:
        try:
            ctx.wait_for_event("serviceworker", timeout=8000)
        except Exception:
            pass


def test_no_host_shows_standalone_and_exports(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    caps = {
        "cap:claude:a1": {"provider": "claude", "conv_id": "a1",
                          "captured_at": "2026-06-16T01:00:00Z", "payload": {"x": 1}},
        "cap:chatgpt:b2": {"provider": "chatgpt", "conv_id": "b2",
                           "captured_at": "2026-06-16T02:00:00Z", "payload": {"y": 2}},
    }
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            # No host registered → host-detection fails → the standalone view, not the launchpad.
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            assert page.locator("#app").is_hidden(), "launchpad iframe should be hidden with no host"

            # Seed captures, reload, assert the live count + per-provider breakdown render.
            page.evaluate("(c) => new Promise(r => chrome.storage.local.set(c, r))", caps)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            page.wait_for_function(
                "() => document.getElementById('cap-count').textContent !== '0'", timeout=8000)
            assert page.locator("#cap-count").inner_text() == "2"
            providers = page.locator("#cap-providers").inner_text()
            assert "Claude" in providers and "ChatGPT" in providers

            # Download → chrome.downloads writes the bundle; the status line confirms the save path.
            page.click("#download-btn")
            page.wait_for_function(
                "() => document.getElementById('dl-status').textContent.startsWith('Saved')",
                timeout=8000)
            assert "Downloads/trinity/transcripts-" in page.locator("#dl-status").inner_text()
        finally:
            ctx.close()


def test_standalone_count_label_pluralizes_on_first_capture(tmp_path):
    """USABILITY (UNCLEAR / static-state-label) — the standalone view's ONE
    headline metric is `#cap-count` + the noun `#cap-count-label`. The noun was a
    static "conversations captured", so the first-capture path (exactly ONE
    captured conversation — the brand-new extension-only user who just browsed a
    single Claude/ChatGPT/Gemini chat) read "1 conversations captured" — an
    ungrammatical count noun on the surface's payoff metric. The label must
    pluralize off the SAME count #cap-count shows (mirrors the launchpad's
    `council{count===1?'':'s'}`). Drive the REAL no-host side panel at count=1 (the
    discriminating case) AND count=2 (must stay plural) so a regression that
    re-hardcodes either form bites."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    one = {"cap:claude:a1": {"provider": "claude", "conv_id": "a1",
                             "captured_at": "2026-06-16T01:00:00Z", "payload": {"x": 1}}}
    two = dict(one)
    two["cap:chatgpt:b2"] = {"provider": "chatgpt", "conv_id": "b2",
                             "captured_at": "2026-06-16T02:00:00Z", "payload": {"y": 2}}
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            # Establish the extension context once so chrome.storage is reachable.
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            # PRECONDITION (non-vacuous): the count + its label both render.
            assert page.locator("#cap-count").count() == 1, "#cap-count missing — guard is hollow"
            assert page.locator("#cap-count-label").count() == 1, "#cap-count-label missing — guard is hollow"

            def label_for(caps, expect_count):
                page.evaluate("() => new Promise(r => chrome.storage.local.clear(r))")
                page.evaluate("(c) => new Promise(r => chrome.storage.local.set(c, r))", caps)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
                page.wait_for_function(
                    "(n) => document.getElementById('cap-count').textContent === n",
                    arg=str(expect_count), timeout=8000)
                # Read the underlying text node (CSS uppercases it via text-transform).
                return page.evaluate(
                    "() => document.getElementById('cap-count-label').textContent")

            # The DISCRIMINATING case: exactly one capture must read SINGULAR.
            label_one = label_for(one, 1)
            assert label_one == "conversation captured", (
                f"first-capture path (count=1) reads '1 {label_one}' — the static "
                f"plural noun shipped '1 conversations captured' on the standalone "
                f"view's headline metric; must pluralize off the count"
            )
            # And two+ must stay PLURAL (so a naive 'always singular' fix also bites).
            label_two = label_for(two, 2)
            assert label_two == "conversations captured", (
                f"count=2 reads '2 {label_two}' — the noun lost its plural; it must "
                f"track the count both ways"
            )
        finally:
            ctx.close()


# WCAG 2.5.5 Target Size / Apple HIG — a thumb-hit control on the narrow draggable
# side panel. The launchpad's .button already floors this (design_system.py); the
# hand-maintained standalone shell (sidepanel.html) never inherited it.
MIN_TAP = 44

_MEASURE_CONTROLS = """
() => {
  const out = [];
  // Every interactive control in the standalone card: the primary CTA + the tip dismiss.
  document.querySelectorAll('#standalone button, #standalone a, #standalone [role=button]')
    .forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;  // not rendered
      out.push({
        id: el.id || null,
        cls: String(el.className || ''),
        text: (el.textContent || '').trim().slice(0, 40),
        w: Math.round(r.width * 10) / 10,
        h: Math.round(r.height * 10) / 10,
      });
    });
  return out;
}
"""


def test_standalone_controls_meet_44px_tap_target(tmp_path):
    """The brand-new-user "Download my transcripts" view is the extension's whole
    value with no CLI installed, and it lives in the narrow, thumb-tappable side
    panel. Drive the REAL panel (no host → standalone) and assert every interactive
    control clears the 44px touch floor at 393/375/320. Founder symptom this bites:
    the primary "Download my transcripts" CTA rendered 43px (1px under 44), and the
    tip × dismiss rendered a 20px sliver — sub-44 mis-taps on the only two controls
    on the surface."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            saw_download = False
            saw_dismiss = False
            for width in (393, 375, 320):
                page.set_viewport_size({"width": width, "height": 760})
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                # No host registered → host-detection fails → the standalone view.
                page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
                # Let the tip resolve (next_tip round-trip) so the × is laid out.
                page.wait_for_timeout(500)
                controls = page.evaluate(_MEASURE_CONTROLS)
                assert controls, f"no interactive controls measured in #standalone @ {width}px"
                # No horizontal overflow at the narrow panel width.
                doc_overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                assert doc_overflow <= 1, (
                    f"standalone view overflows the {width}px panel (docOverflow={doc_overflow})"
                )
                for c in controls:
                    is_download = c["id"] == "download-btn"
                    is_dismiss = "x" in c["cls"].split()
                    if is_download:
                        saw_download = True
                    if is_dismiss:
                        saw_dismiss = True
                    assert c["h"] >= MIN_TAP, (
                        f"standalone control {c['id'] or c['cls']!r} ({c['text']!r}) is "
                        f"{c['h']}px tall @ {width}px — under the {MIN_TAP}px touch floor "
                        f"(the 'Download my transcripts' CTA shipped at 43px / the tip × at 20px; "
                        f"a sub-44 mis-tap on the only controls on the brand-new-user surface)"
                    )
                    # Icon-only controls also need a 44px-wide hit box, not just height.
                    if is_dismiss:
                        assert c["w"] >= MIN_TAP, (
                            f"the tip × dismiss is only {c['w']}px wide @ {width}px — under the "
                            f"{MIN_TAP}px touch floor (a 20px corner-glyph mis-tap)"
                        )
            # The check is hollow if the two real controls never rendered.
            assert saw_download, "the #download-btn primary CTA was never measured — guard is hollow"
            assert saw_dismiss, "the tip × dismiss was never measured — guard is hollow"
        finally:
            ctx.close()


def test_standalone_tip_does_not_duplicate_header_reassurance(tmp_path):
    """USEFULNESS / IA — the cold standalone view (captured=0) renders the header
    `.sub` AND the capture-nudge tip simultaneously on one narrow panel. Drive the
    REAL no-host panel and assert the privacy reassurance + provider trio are NOT
    restated by the tip.

    Founder symptom this bites: the capture-nudge tip shipped as "Open Claude,
    ChatGPT, or Gemini — Trinity captures your conversations here automatically.
    They never leave your machine." — a near-verbatim restatement of the `.sub`
    header ("Captured locally from Claude, ChatGPT & Gemini — they never leave
    your machine."), so "they never leave your machine" appeared TWICE and the
    provider trio THREE TIMES on a single ~6cm-tall surface. A dismissible callout
    that only re-states the always-visible header is REDUNDANT — it must earn its
    space by saying something the header/count-card do NOT (the payoff)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            page.set_viewport_size({"width": 393, "height": 760})
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            # The tip is rendered via the async next_tip round-trip — wait for it so
            # the de-dup check is non-vacuous (a missing tip can't prove anything).
            page.wait_for_selector("#tip-slot .tip", timeout=12000)

            sub = page.locator("#standalone .sub").inner_text().strip()
            tip = page.locator("#tip-slot .tip").inner_text().strip()
            # Strip the dismiss glyph so it doesn't count as tip content.
            tip = tip.replace("×", "").strip()

            # Precondition: the header DOES carry the privacy reassurance (so the
            # de-dup assertion below is about the tip, not a blank header).
            assert "never leave your machine" in sub.lower(), (
                f"the header .sub no longer carries the privacy reassurance — guard "
                f"precondition broken (sub={sub!r})"
            )
            assert tip, "the capture-nudge tip rendered empty — guard would be vacuous"

            # The reassurance must appear AT MOST ONCE across the whole surface.
            full = page.locator("#standalone").inner_text().lower()
            n_priv = full.count("never leave your machine")
            assert n_priv <= 1, (
                f"the 'never leave your machine' privacy reassurance appears {n_priv} "
                f"times on the cold standalone panel — the capture-nudge tip is "
                f"restating the .sub header verbatim (REDUNDANT copy on one narrow "
                f"surface). The header owns the privacy promise; the tip must say "
                f"something distinct.\n  sub={sub!r}\n  tip={tip!r}"
            )
            # And the tip must not be a near-clone of the header — require it to add
            # the payoff vocabulary the cold surface states nowhere else.
            assert "council" in tip.lower() or "lens" in tip.lower(), (
                f"the capture-nudge tip adds no NEW value over the header/count-card "
                f"(no 'council'/'lens' payoff) — it just re-states 'go browse, it's "
                f"local', which the .sub + empty-hint already cover. tip={tip!r}"
            )
        finally:
            ctx.close()


def _ax_name(page, selector):
    """The browser's COMPUTED accessible name for `selector`, read from
    Chromium's real AX tree (CDP) — not an attribute guess. The AccName
    algorithm is what an actual screen reader announces."""
    handle = page.query_selector(selector)
    assert handle is not None, f"{selector!r} not present — guard is hollow"
    cdp = page.context.new_cdp_session(page)
    cdp.send("Accessibility.enable")
    dom = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    res = cdp.send("DOM.querySelector", {"nodeId": dom["root"]["nodeId"], "selector": selector})
    node_id = res.get("nodeId")
    assert node_id, f"could not resolve {selector!r} in the DOM tree"
    backend_id = cdp.send("DOM.describeNode", {"nodeId": node_id})["node"]["backendNodeId"]
    tree = cdp.send("Accessibility.getFullAXTree")
    for n in tree.get("nodes", []):
        if n.get("backendDOMNodeId") == backend_id:
            return (n.get("name") or {}).get("value")
    return None


def test_standalone_tip_dismiss_has_accessible_name(tmp_path):
    """A11y / WCAG 4.1.2 Name, Role, Value — the standalone capture-nudge tip's
    dismiss control is an ICON-ONLY <button> whose only visible content is the
    "×" glyph (`x.textContent = "×"`). With no aria-label the AccName algorithm
    falls back to that visible text — the bare "×" (U+00D7) — and a `title` does
    NOT win over text content, so a screen-reader user heard "times, button" /
    "multiplication sign, button" with no idea it dismisses the tip. Found
    2026-06-21 reading the real Chromium AX tree (the sidepanel-shell tip-dismiss
    computed name was literally "×", the same shape as the popup close ×).

    Drives the REAL no-host standalone panel (extension harness), waits for the
    async next_tip round-trip so the × is laid out, then asserts the computed
    accessible name is meaningful."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            page.set_viewport_size({"width": 393, "height": 760})
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            # The dismiss × only exists once the async next_tip round-trip lands.
            page.wait_for_selector("#tip-slot .tip .x", timeout=12000)

            # Precondition (A): the control PAINTS + is a real interactive button.
            geo = page.evaluate(
                "() => { const b = document.querySelector('#tip-slot .tip .x');"
                " const r = b.getBoundingClientRect();"
                " return { tag: b.tagName.toLowerCase(), visible: r.width>0 && r.height>0,"
                " disabled: b.disabled }; }"
            )
            assert geo["tag"] == "button" and geo["visible"] and not geo["disabled"], (
                f".tip .x is not a painted interactive button: {geo}"
            )
            # Precondition (B): it is ICON-ONLY — visible text is just the glyph.
            text = page.evaluate("() => document.querySelector('#tip-slot .tip .x').textContent.trim()")
            assert text in ("×", "✕", "x", "X"), (
                f".tip .x is no longer icon-only (visible text {text!r}) — re-derive "
                f"this guard's preconditions"
            )

            name = _ax_name(page, "#tip-slot .tip .x")

            assert name and name.strip(), (
                "standalone tip-dismiss button has NO accessible name — a "
                "screen-reader user hears only 'button' (WCAG 4.1.2 Name, Role, Value)."
            )
            assert name.strip() not in ("×", "✕", "x", "X"), (
                f"standalone tip-dismiss button announces only the bare glyph {name!r} "
                f"— a screen reader reads 'multiplication sign, button', not what the "
                f"control DOES. Needs an aria-label (WCAG 4.1.2)."
            )
            assert "dismiss" in name.lower() or "close" in name.lower(), (
                f"standalone tip-dismiss accessible name {name!r} does not say it "
                f"DISMISSES anything — lead with the action (WCAG 4.1.2)."
            )
        finally:
            ctx.close()


# Captures-present + no host → the `install-cli` tip, whose cta is the canonical
# landing domain. This is the brand-new extension-only user (some conversations
# captured, CLI not yet installed) — the exact conversion target.
_INSTALL_CTA_CAPS = {
    "cap:claude:a1": {"provider": "claude", "conv_id": "a1",
                      "captured_at": "2026-06-16T01:00:00Z", "payload": {"x": 1}},
    "cap:chatgpt:b2": {"provider": "chatgpt", "conv_id": "b2",
                       "captured_at": "2026-06-16T02:00:00Z", "payload": {"y": 2}},
}


def test_standalone_install_cta_is_an_actionable_link(tmp_path):
    """USABILITY / NO-OP — the standalone side panel's `install-cli` tip surfaces
    the install CTA "keepwhatworks.com" to an extension-only user (captures>0, no
    CLI). It SHIPPED as a bare teal+bold <span> — it LOOKED like a link but had no
    href, no handler, no copy: clicking it changed nothing (url unchanged, no new
    tab, no clipboard). The one conversion affordance the standalone view shows was
    a dead pseudo-link. Found 2026-06-22 driving the real no-host side panel.

    Drives the REAL no-host standalone panel (captures seeded so the install-cli
    tip fires), then asserts the CTA is a real, focusable <a> pointing at the
    install site — the affordance an extension-only user needs to actually install.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            page.set_viewport_size({"width": 393, "height": 760})
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            # Seed captures so the install-cli tip (not capture-nudge) fires.
            page.evaluate(
                "(c) => new Promise(r => chrome.storage.local.set(c, r))",
                _INSTALL_CTA_CAPS,
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            page.wait_for_selector("#tip-slot .tip", timeout=12000)

            # Precondition (A): the install-cli tip IS the one rendered — its body
            # names installing Trinity, and a CTA element exists. If it didn't, the
            # guard would be vacuous (a missing CTA can't prove a dead one).
            tip_text = page.locator("#tip-slot .tip").inner_text().strip().lower()
            assert "install trinity" in tip_text, (
                f"the install-cli tip did not render (tip={tip_text!r}); the CTA "
                f"guard would be vacuous. Seed captures>0 + no host to reach it."
            )
            cta = page.query_selector("#tip-slot .tip .cta")
            assert cta is not None, "the tip rendered NO .cta element — guard is hollow"

            # Precondition (B): the CTA text is the canonical landing domain, read
            # render-independently from the extension's tip ladder source so the
            # assertion is about a real seeded value, not whatever rendered.
            cta_text = cta.inner_text().strip()
            assert cta_text == "keepwhatworks.com", (
                f"the install CTA text is {cta_text!r}, not the expected landing "
                f"domain — re-derive this guard's preconditions"
            )

            # THE BITE: the CTA must be a real actionable link, not a dead span. A
            # screen-reader/keyboard user must be able to focus it; a click must
            # have a navigable target. The founder symptom: a teal pseudo-link to
            # the install site that did NOTHING.
            shape = page.evaluate(
                """() => {
                  const a = document.querySelector('#tip-slot .tip .cta');
                  return {
                    tag: a.tagName.toLowerCase(),
                    href: a.getAttribute('href'),
                    cursor: getComputedStyle(a).cursor,
                    focusable: a.tagName.toLowerCase() === 'a'
                      || a.tagName.toLowerCase() === 'button'
                      || a.tabIndex >= 0,
                  };
                }"""
            )
            assert shape["tag"] in ("a", "button"), (
                f"the install CTA 'keepwhatworks.com' rendered as a <{shape['tag']}> "
                f"— a dead pseudo-link (a teal+bold <span> with no href/handler). "
                f"Clicking the one conversion affordance an extension-only user "
                f"sees did NOTHING (NO-OP). It must be an actionable <a>/<button>."
            )
            assert shape["href"] and "keepwhatworks.com" in shape["href"], (
                f"the install CTA <a> has no href to the install site "
                f"(href={shape['href']!r}) — clicking it can't open keepwhatworks.com."
            )
            assert shape["focusable"], (
                "the install CTA is not keyboard-focusable — a keyboard-only user "
                "can't reach the install link (WCAG 2.1.1)."
            )
            assert shape["cursor"] == "pointer", (
                f"the install CTA cursor is {shape['cursor']!r}, not 'pointer' — it "
                f"gives no affordance cue that it's clickable."
            )

            # And clicking it actually opens a tab to the install site (real action,
            # not just a well-shaped-but-inert element).
            with ctx.expect_page(timeout=6000) as new_page_info:
                page.click("#tip-slot .tip .cta")
            new_page = new_page_info.value
            assert "keepwhatworks.com" in (new_page.url or ""), (
                f"clicking the install CTA opened {new_page.url!r}, not the install "
                f"site — the conversion link is mis-targeted."
            )
            new_page.close()
        finally:
            ctx.close()


_DL_STATUS_CAPS = {
    "cap:claude:a1": {"provider": "claude", "conv_id": "a1",
                      "captured_at": "2026-06-16T01:00:00Z", "payload": {"x": 1}},
    "cap:chatgpt:b2": {"provider": "chatgpt", "conv_id": "b2",
                       "captured_at": "2026-06-16T02:00:00Z", "payload": {"y": 2}},
}


def test_standalone_download_status_is_announced(tmp_path):
    """A11y / WCAG 4.1.3 Status Messages — "Download my transcripts" is the
    standalone view's whole payoff and a DEFERRED async action (a bundle fetch +
    the chrome.downloads.download callback). Its result text — "Bundling…" then
    "Saved N conversation(s) → Downloads/…" / "Download failed: …" / "Nothing
    captured yet…" — is written IN PLACE into #dl-status while keyboard focus has
    already left the now-disabled button (drops to <body>). The element shipped as
    a bare `<div class="status" id="dl-status">` with NO role=status / aria-live,
    so a screen-reader user clicked Download and heard NOTHING about whether the
    download bundled, saved, or failed — the un-announced sibling of the popup's
    #status (which got role=status aria-live) and the topology basin-detail panel.
    Found 2026-06-22 driving the real no-host side panel: after the click, the
    "Saved …" result landed in a mute <div> with no live ancestor.

    Drives the REAL no-host standalone panel (extension harness), seeds captures so
    the bundle is non-empty, clicks Download, and asserts the result text lands
    inside a live region (role=status / aria-live!=off) so AT announces it."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID

    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    with sync_playwright() as p:
        try:
            ctx = _launch(p, tmp_path / "ud")
        except Exception as exc:
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            _await_sw(ctx)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)

            # Seed captures so Download produces a real "Saved …" result (an empty
            # store would short-circuit to "Nothing captured yet" and never exercise
            # the deferred chrome.downloads callback path the bug lives on).
            page.evaluate(
                "(c) => new Promise(r => chrome.storage.local.set(c, r))",
                _DL_STATUS_CAPS,
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#standalone:not([hidden])", timeout=12000)
            page.wait_for_function(
                "() => document.getElementById('cap-count').textContent !== '0'",
                timeout=8000,
            )

            # Precondition (A): #dl-status PAINTS (empty pre-click) and the Download
            # button is the enabled, focusable action — a no-render can't vacuously
            # pass. Capture the live-ancestor BEFORE the click so the assertion is
            # about the element that will carry the deferred result.
            pre = page.evaluate(
                """() => {
                  const el = document.getElementById('dl-status');
                  const btn = document.getElementById('download-btn');
                  let n = el, live = null;
                  while (n) {
                    const r = n.getAttribute && n.getAttribute('role');
                    const lv = n.getAttribute && n.getAttribute('aria-live');
                    if (r === 'status' || r === 'alert' || (lv && lv !== 'off')) {
                      live = (n.id || n.tagName) + ' role=' + r + ' aria-live=' + lv;
                      break;
                    }
                    n = n.parentElement;
                  }
                  return { present: !!el, text: (el && el.textContent) || '',
                           btnDisabled: btn ? btn.disabled : null, live };
                }"""
            )
            assert pre["present"], "#dl-status is not present — guard is hollow"
            assert pre["text"].strip() == "", (
                f"#dl-status is non-empty before any download ({pre['text']!r}) — "
                f"the empty-start precondition is broken"
            )
            assert pre["btnDisabled"] is False, (
                "the #download-btn is not the enabled action with captures seeded — "
                "precondition broken"
            )

            # Precondition (B): the deferred result actually lands. Click Download
            # and wait for the real "Saved …" text (proves the chrome.downloads
            # callback fired — the discriminating result the bug muted).
            page.click("#download-btn")
            page.wait_for_function(
                "() => document.getElementById('dl-status').textContent.startsWith('Saved')",
                timeout=8000,
            )
            result = page.locator("#dl-status").inner_text().strip()
            assert result.startswith("Saved"), (
                f"the download did not produce a 'Saved …' result ({result!r}) — the "
                f"deferred-result path the guard checks never ran"
            )

            # THE BITE: the result text must sit inside a live region. Walk up from
            # #dl-status for a role=status/alert or aria-live ancestor — exactly what
            # AT uses to decide whether an in-place text change is announced.
            announced = page.evaluate(
                """() => {
                  let n = document.getElementById('dl-status'), found = null;
                  while (n) {
                    const r = n.getAttribute && n.getAttribute('role');
                    const lv = n.getAttribute && n.getAttribute('aria-live');
                    if (r === 'status' || r === 'alert' || (lv && lv !== 'off')) {
                      found = (n.id || n.tagName) + ' role=' + r + ' aria-live=' + lv;
                      break;
                    }
                    n = n.parentElement;
                  }
                  return found;
                }"""
            )
            assert announced, (
                f"the 'Download my transcripts' result {result!r} is written into a "
                f"plain <div> (#dl-status) with NO role=status / aria-live region — a "
                f"screen-reader user who clicks Download (focus drops to <body>) hears "
                f"NOTHING about whether the download bundled, SAVED, or FAILED (WCAG "
                f"4.1.3 Status Messages, the deferred-result sibling of the popup "
                f"#status). pre-click live ancestor was: {pre['live']!r}"
            )
        finally:
            ctx.close()


def test_host_present_shows_launchpad(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from extension_harness import EXT_ID, _register_stub_host, _restore_hosts

    home = tmp_path / "home"
    (home / "portal_pages" / "status").mkdir(parents=True, exist_ok=True)
    user_data = tmp_path / "ud"
    log_path = home / "host.log"
    log_path.write_text("")
    written = _register_stub_host(home, log_path, user_data)  # SAFE dirs; restored in finally
    url = f"chrome-extension://{EXT_ID}/sidepanel.html"
    try:
        with sync_playwright() as p:
            try:
                ctx = _launch(p, user_data)
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                _await_sw(ctx)
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                # The stub host answers launchpad_data {ok:true} → host detected → the launchpad.
                page.wait_for_selector("#app:not([hidden])", timeout=12000)
                assert page.locator("#standalone").is_hidden(), "standalone card should be hidden with a host"
            finally:
                ctx.close()
    finally:
        _restore_hosts(written)
