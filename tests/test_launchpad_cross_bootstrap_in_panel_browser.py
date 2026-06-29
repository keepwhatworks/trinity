"""The "Install the Chrome extension" cross-bootstrap card must not tell a
SIDE-PANEL viewer to install the extension they're already inside.

UX-sweep Iter 24 (2026-06-17). Driving the REAL side panel under the USEFULNESS
lens surfaced a MISPLACED / MISLEADING-copy card: when the capture host isn't
wired (`browserExtension.configured === false`, i.e. the user never ran
`trinity-local install-extension`), the home view renders a card headlined
"Install the Chrome extension for browser capture + auto-update". On the file://
launchpad that's correct. But the side panel (`sandbox/_bridge.js` sets
`window.__TRINITY_HOST_FETCH__ = true`) renders the SAME payload — so a user who
is literally looking at the extension's own side panel is told to install the
extension. The card's underlying value is real (capture isn't wired), but the
framing is wrong: the remaining step there is registering the Native-Messaging
host via `install-extension`, not installing the extension.

The fix adds an `inExtensionPanel` data field (`!!window.__TRINITY_HOST_FETCH__`)
and reframes the eyebrow / headline / body / footer when in-panel. This drives the
REAL petite-vue render with `__TRINITY_HOST_FETCH__` set and asserts:
  - in-panel: NO "Install the Chrome extension" headline, DOES name the
    `install-extension` step, NO "Add to Chrome" pitch;
  - file://: the install pitch is PRESERVED (the fix didn't over-broaden).

Mutation-provable: revert the headline/body to the single static install copy and
the in-panel assertion reds with the exact symptom.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_cross_bootstrap_card_branches_on_in_panel_signal():
    """CI-runnable canary: the card must branch on `inExtensionPanel`, not render a
    single static "Install the Chrome extension" headline for every surface."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    # The reactive in-panel flag is wired from the host-fetch bridge signal.
    assert "inExtensionPanel: !!window.__TRINITY_HOST_FETCH__" in src, (
        "lost the inExtensionPanel data field — the cross-bootstrap card can no "
        "longer tell a side-panel viewer apart from a file:// viewer"
    )
    # The headline is now branched; the install pitch must be guarded by v-else so
    # the side panel doesn't get "Install the Chrome extension".
    assert 'v-if="inExtensionPanel">\n          Wire up browser capture' in src, (
        "the side-panel headline ('Wire up browser capture…') is gone — a side-panel "
        "viewer would be told to install the extension they're already inside"
    )
    assert 'v-else>\n          Install the Chrome extension' in src, (
        "the file:// install headline is no longer v-else-gated — it would render "
        "in the side panel too (the contradiction this fix removes)"
    )


# ── Real-browser proof ──
def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _render_home(tmp_path, *, web_store_url: str | None = None,
                 sub: str = "serve") -> Path:
    """Render the home launchpad from an EMPTY home (no extension.json →
    browserExtension.configured == False → the cross-bootstrap card shows), serve
    it, and return the page directory.

    `web_store_url` overrides `browserExtension.webStoreUrl` so a single render can
    exercise either CTA branch of the card: empty → the docs fallback anchor;
    set → the "Add to Chrome →" anchor.
    """
    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # build_launchpad_payload() returns the WRAPPED {pageData, recentSidebarHtml,
    # title}; the template renderer wants the INNER pageData dict (passing the
    # wrapper makes every pageData.* binding undefined — the Iter 4 harness trap).
    payload = build_launchpad_payload()
    page_data = payload["pageData"]
    # Sanity: the card only shows when capture isn't wired. The empty home gives us
    # configured=False; assert it so the test can't silently pass on a wired home.
    be = page_data.get("browserExtension") or {}
    assert be.get("configured") is False, f"expected unwired extension, got {be!r}"
    if web_store_url is not None:
        page_data["browserExtension"]["webStoreUrl"] = web_store_url

    html = render_launchpad_html(page_data=page_data, view="home")
    pp = tmp_path / sub / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return tmp_path / sub


def _card_text(page, port: int) -> dict:
    page.add_init_script(
        "window.__TRINITY_DISPATCH__ = {dispatch: () => Promise.resolve({ok:true}), onStateChange: () => {}};"
    )
    page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
              wait_until="networkidle", timeout=20000)
    page.wait_for_function(
        "() => { const r = document.getElementById('launchpad-app');"
        " return r && !r.hasAttribute('v-cloak'); }",
        timeout=10000,
    )
    return page.evaluate(
        """() => {
            const s = [...document.querySelectorAll('section.home-card')]
                .find(x => /bootstrap|Finish setup/i.test(x.innerText));
            if (!s) return {present: false};
            return {
                present: true,
                eyebrow: (s.querySelector('.eyebrow') || {}).innerText.trim(),
                h2: (s.querySelector('h2') || {}).innerText.trim(),
                text: s.innerText,
            };
        }"""
    )


@pytest.mark.slow
@pytest.mark.browser
def test_side_panel_does_not_say_install_the_extension(tmp_path, monkeypatch):
    """In the side panel (__TRINITY_HOST_FETCH__ set), the card must NOT tell the
    user to install the extension they're already inside, and must name the real
    remaining step (install-extension / wire the host)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    serve_dir = _render_home(tmp_path)
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 393, "height": 1400}).new_page()
                # Stamp the in-panel signal BEFORE any app code reads it.
                page.add_init_script("window.__TRINITY_HOST_FETCH__ = true;")
                card = _card_text(page, port)
                assert card["present"], "cross-bootstrap card missing on an unwired home"
                assert "Install the Chrome extension" not in card["text"], (
                    "the side panel tells the user to INSTALL THE CHROME EXTENSION they "
                    "are already inside (you're reading this in its side panel) — "
                    "contradictory MISPLACED copy"
                )
                assert "install-extension" in card["text"], (
                    "the side-panel card dropped the REAL remaining step "
                    "(`trinity-local install-extension` to wire the capture host)"
                )
                assert "Add to Chrome" not in card["text"], (
                    "the side panel still pitches 'Add to Chrome' — you can't add an "
                    "extension you're already running"
                )
                assert "{{" not in card["text"], "raw petite-vue template leaked"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_file_launchpad_keeps_install_the_extension_pitch(tmp_path, monkeypatch):
    """On the file:// / localhost launchpad (NO __TRINITY_HOST_FETCH__), the
    install-the-extension pitch must be PRESERVED — the fix must not over-broaden
    and strip the cross-bootstrap CTA from the terminal-first user."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    serve_dir = _render_home(tmp_path)
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 393, "height": 1400}).new_page()
                # NO __TRINITY_HOST_FETCH__ — this is the file:// path.
                card = _card_text(page, port)
                assert card["present"], "cross-bootstrap card missing on an unwired home"
                assert "Install the Chrome extension" in card["h2"], (
                    "the file:// launchpad lost its 'Install the Chrome extension' "
                    "cross-bootstrap pitch — the terminal-first user has no extension CTA"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


# ── UX-sweep Iter 110: the card's CTA must OPEN THE RIGHT TARGET, not no-op ──
#
# The two tests above cover the card's COPY branching (in-panel vs file://). They
# never CLICK the card's anchor CTAs, so the literal target-open behaviour — does
# the "Add to Chrome →" / docs link open the RIGHT url in a new tab, or is it a
# dead/empty/wrong-target link? — was UN-DRIVEN. This guard drives both anchor
# branches end-to-end (resolved href + target=_blank + a real new-tab open).
#
# DOCS fallback href (webStoreUrl empty — the real current state, since
# CHROME_WEB_STORE_URL=="") must point at the in-repo install doc.
_DOCS_HREF = (
    "https://github.com/keepwhatworks/trinity/blob/main/docs/INSTALL-extension.md"
)


def _bootstrap_anchors(page, port: int) -> list[dict]:
    page.add_init_script(
        "window.__TRINITY_DISPATCH__ = {dispatch: () => Promise.resolve({ok:true}), onStateChange: () => {}};"
    )
    page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
              wait_until="networkidle", timeout=20000)
    page.wait_for_function(
        "() => { const r = document.getElementById('launchpad-app');"
        " return r && !r.hasAttribute('v-cloak'); }",
        timeout=10000,
    )
    return page.evaluate(
        """() => {
            const s = [...document.querySelectorAll('section.home-card')]
                .find(x => /bootstrap|Finish setup/i.test(x.innerText));
            if (!s) return null;
            return [...s.querySelectorAll('a')].map(a => ({
                text: a.innerText.trim(),
                href: a.getAttribute('href'),
                resolved: a.href,
                target: a.getAttribute('target'),
            }));
        }"""
    )


@pytest.mark.slow
@pytest.mark.browser
def test_cross_bootstrap_docs_cta_opens_correct_target(tmp_path, monkeypatch):
    """webStoreUrl EMPTY (current state): the card's only CTA is the docs link. It
    must carry the in-repo INSTALL-extension.md href + target=_blank, and the literal
    click must OPEN THAT EXACT url in a new tab — not a no-op / dead / wrong link.
    No empty 'Add to Chrome' anchor may render when webStoreUrl is blank."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # Dead-link sanity: the docs target the CTA points at must exist in-repo.
    assert (REPO / "docs" / "INSTALL-extension.md").exists(), (
        "the cross-bootstrap docs CTA points at docs/INSTALL-extension.md but that "
        "file is gone — the only file:// CTA is now a dead link"
    )

    serve_dir = _render_home(tmp_path, web_store_url="", sub="serve_docs")
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                ctx = browser.new_context(viewport={"width": 393, "height": 1400})
                page = ctx.new_page()
                anchors = _bootstrap_anchors(page, port)
                assert anchors is not None, "cross-bootstrap card missing on an unwired home"
                docs = [a for a in anchors if "INSTALL-extension" in (a["href"] or "")]
                assert docs, (
                    "the docs CTA anchor is GONE with webStoreUrl empty — the "
                    "terminal-first user's only extension link disappeared (no-op card). "
                    f"anchors={anchors!r}"
                )
                d = docs[0]
                assert d["href"] == _DOCS_HREF, (
                    f"the docs CTA points at the WRONG target: {d['href']!r} "
                    f"(expected {_DOCS_HREF!r})"
                )
                assert d["target"] == "_blank", (
                    "the docs CTA isn't target=_blank — clicking it would nav AWAY from "
                    f"the launchpad instead of opening a tab (target={d['target']!r})"
                )
                # With webStoreUrl blank, an "Add to Chrome →" anchor would have an
                # empty href — a dead link. It must NOT render.
                assert not any("Add to Chrome" in (a["text"] or "") for a in anchors), (
                    "an 'Add to Chrome →' anchor rendered while webStoreUrl is EMPTY — "
                    "its href would be blank, a dead CTA"
                )

                # Literal target-open: click the docs CTA, assert it opens THAT url
                # in a NEW tab (not a same-tab nav, not a no-op).
                with ctx.expect_page() as new_info:
                    page.click("section.home-card a[href*='INSTALL-extension']")
                opened = new_info.value
                assert opened.url == _DOCS_HREF, (
                    "clicking the cross-bootstrap docs CTA opened the WRONG target "
                    f"(or no-op'd): {opened.url!r} (expected {_DOCS_HREF!r})"
                )
                opened.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_cross_bootstrap_add_to_chrome_cta_opens_web_store(tmp_path, monkeypatch):
    """webStoreUrl SET: the card shows an 'Add to Chrome →' anchor. Its resolved href
    must EQUAL the webStoreUrl exactly (not a stale/placeholder), be target=_blank,
    and the literal click must fire a navigation request to THAT exact url in a new
    tab (the live store redirects a synthetic id to its homepage, so we assert on the
    navigation REQUEST, not the post-redirect landing url)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    web_store = "https://chromewebstore.google.com/detail/trinity-local/iterxbootstrapcta"
    serve_dir = _render_home(tmp_path, web_store_url=web_store, sub="serve_ws")
    httpd, port = _serve(serve_dir)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                ctx = browser.new_context(viewport={"width": 393, "height": 1400})
                page = ctx.new_page()
                anchors = _bootstrap_anchors(page, port)
                assert anchors is not None, "cross-bootstrap card missing on an unwired home"
                add = [a for a in anchors if "Add to Chrome" in (a["text"] or "")]
                assert add, (
                    "with webStoreUrl SET, the 'Add to Chrome →' CTA is missing — the "
                    f"one-click install path never renders. anchors={anchors!r}"
                )
                a = add[0]
                assert a["href"] == web_store, (
                    f"'Add to Chrome' points at the WRONG target: {a['href']!r} "
                    f"(expected the webStoreUrl {web_store!r})"
                )
                assert a["target"] == "_blank", (
                    f"'Add to Chrome' isn't target=_blank (target={a['target']!r})"
                )

                # Literal target-open: capture the navigation REQUEST the click fires.
                requested: dict = {}

                def _grab(req):
                    if req.is_navigation_request() and "chromewebstore" in req.url:
                        requested.setdefault("url", req.url)

                ctx.on("request", _grab)
                with ctx.expect_page() as new_info:
                    page.click("section.home-card a.btn")
                opened = new_info.value
                try:
                    opened.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                assert requested.get("url") == web_store, (
                    "clicking 'Add to Chrome →' fired a navigation to the WRONG url "
                    f"(or no-op'd): {requested.get('url')!r} (expected {web_store!r})"
                )
                opened.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
