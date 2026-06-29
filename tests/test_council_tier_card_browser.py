"""Browser guard for the council-tier card's TEMPLATE BINDING.

`test_council_tier_card.py` thoroughly covers the *data* layer
(`_council_tier_status` → tier 0/1/2/3 headlines, order-independence,
install commands, `defaultMembers` filtering). But it never renders the
petite-vue template that binds that data into a visible card
(launchpad_template.py ~L911-944). Those `{{ pageData.councilTier… }}`
bindings only resolve in a real JS engine — so a non-browser test cannot
prove the card actually renders. And critically the *founder's own*
launchpad is tier 3 (`show=False`), which HIDES this card — so a binding
regression (rename `nextStep.installCommand`, break the `v-if` gate, mangle
the eyebrow branches) passes every existing test AND escapes manual catch,
shipping a blank/broken card to exactly the new-user audience the "works
with 1, sells the other two" pitch targets.

This pins two mutation-provable invariants by driving the REAL launchpad
template through petite-vue in headless Chromium:

  1. tier 1 (only `claude` on PATH) → the card RENDERS with its data
     resolved: the eyebrow, a headline that name-checks the installed
     provider, and the EXACT next-step install command in <code> (proving
     the `nextStep.installCommand` binding resolves, not just that the data
     carries it). The copy button flips to ✓ on click.
  2. tier 3 (all three on PATH — the founder's state) → the card is ABSENT
     (proving the `show` gate hides it; a broken gate would surface a
     spurious card on every full-trio user's launchpad).

Slow + browser marked; skips when Playwright/chromium are absent. Found
2026-06-06 dogfooding the partial-provider launchpad — the founder's
tier-3 view never exercises this template, so it had zero render coverage.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _render_with_binaries(monkeypatch, tmp_home: Path, *, on_path: set[str]) -> str:
    """Render the real launchpad HTML with PATH-resolution forced to a binary set.

    `on_path` is the set of provider BINARY names that resolve (note Antigravity's
    binary is `agy`, not `antigravity`). Empty set → tier 0; {'claude'} → tier 1;
    {'claude','codex'} → tier 2; all three → tier 3.
    """
    monkeypatch.setenv("TRINITY_HOME", str(tmp_home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    def _which(name: str):
        return f"/usr/local/bin/{name}" if name in on_path else None

    monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", _which)
    from trinity_local.launchpad_page import render_launchpad_html

    return render_launchpad_html()


def _render_at_tier(monkeypatch, tmp_home: Path, *, only_claude: bool) -> str:
    """Back-compat wrapper for the tier-1/tier-3 tests below."""
    on_path = {"claude"} if only_claude else {"claude", "codex", "agy"}
    return _render_with_binaries(monkeypatch, tmp_home, on_path=on_path)


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


_TIER_PROBE = """() => {
  const eyebrows = [...document.querySelectorAll('.eyebrow')];
  const e = eyebrows.find(x => /Add a 2nd provider|Complete the council|Install a council provider/.test(x.textContent));
  if (!e) return { found: false };
  const card = e.closest('section.card');
  const h2 = card.querySelector('h2');
  const code = card.querySelector('code');
  return {
    found: true,
    eyebrow: e.textContent.trim(),
    headline: h2 ? h2.textContent.trim() : null,
    installCode: code ? code.textContent.trim() : null,
    hasCopyBtn: !!card.querySelector('button.icon-action'),
    leaks: /\\bundefined\\b|\\bNaN\\b|\\[object Object\\]/.test(card.textContent),
  };
}"""


def _write_prod_layout(html: str, serve_root: Path) -> str:
    """Mirror prod: launchpad.html at portal_pages/, vendor at portal_pages/vendor/.
    The shared-CSS @font-face uses `../portal_pages/vendor/…` and the launchpad's
    own JS uses `./vendor/…`; both only resolve from the portal_pages/ depth."""
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return "portal_pages/launchpad.html"


def test_tier1_card_renders_with_resolved_bindings(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _render_at_tier(monkeypatch, tmp_path / "home", only_claude=True)
    serve_root = tmp_path / "serve"
    rel = _write_prod_layout(html, serve_root)
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1100, "height": 1400}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.on(
                    "console",
                    lambda m: errs.append(f"console.error: {m.text[:160]}") if m.type == "error" else None,
                )
                # Never let a click reach the founder's live extension.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "[...document.querySelectorAll('.eyebrow')].some(e => /Add a 2nd provider/.test(e.textContent))",
                    timeout=10000,
                )
                s = page.evaluate(_TIER_PROBE)

                assert s["found"], "tier-1 council-tier card did not render"
                assert s["eyebrow"] == "Add a 2nd provider", s["eyebrow"]
                # Headline name-checks the installed provider + the pitch.
                assert "Claude Code" in s["headline"], s["headline"]
                assert "Add one more" in s["headline"], s["headline"]
                # The EXACT next-step install command — proves the
                # nextStep.installCommand binding resolves (mutation-provable:
                # rename the data field and this empties).
                assert s["installCode"] == "npm install -g @openai/codex && codex --login", s["installCode"]
                assert s["hasCopyBtn"], "copy button missing on the tier card"
                assert not s["leaks"], f"undefined/NaN/[object Object] leaked into the card: {s['installCode']!r}"

                # Copy button flips to ✓ (petite-vue @click → copyText needs a trusted click).
                page.click(
                    "section.card:has(.eyebrow:has-text('Add a 2nd provider')) button.icon-action",
                    timeout=3000,
                )
                page.wait_for_timeout(150)
                check = page.evaluate(
                    "() => [...document.querySelectorAll('.eyebrow')]"
                    ".find(e=>/Add a 2nd provider/.test(e.textContent))"
                    ".closest('section.card').querySelector('button.icon-action span').textContent.trim()"
                )
                assert check == "✓", f"copy button did not confirm: {check!r}"
                assert not errs, f"JS errors rendering the tier-1 launchpad: {errs[:4]}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_tier3_hides_the_card(tmp_path, monkeypatch):
    """The founder's own full-trio launchpad must NOT show the tier card —
    `show` is False at tier 3. A broken `v-if` gate would surface a spurious
    'council' card on every complete-council user's page."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _render_at_tier(monkeypatch, tmp_path / "home", only_claude=False)
    serve_root = tmp_path / "serve"
    rel = _write_prod_layout(html, serve_root)
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1100, "height": 1400}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                # Wait for petite-vue to mount: the root sheds its v-cloak (the app
                # is display:none until then, so waiting for a *visible* .eyebrow
                # would time out — the launchpad now cloaks until mount).
                page.wait_for_function(
                    "() => { const r = document.querySelector('[id$=\"-app\"]');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                present = page.evaluate(
                    "() => [...document.querySelectorAll('.eyebrow')]"
                    ".some(e => /Add a 2nd provider|Complete the council|Install a council provider/.test(e.textContent))"
                )
                assert present is False, (
                    "tier-3 (full trio) launchpad must HIDE the council-tier card "
                    "(councilTier.show is False) — the v-if gate is broken"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def _drive_and_probe(tmp_path, html: str, viewport_w: int = 1100):
    """Serve the rendered launchpad, mount petite-vue, return (_TIER_PROBE result, errs).

    Shared driver for the tier-0 / tier-2 render guards: both branches of the
    council-tier template (the tier-0 `v-else` 'Install a council provider'
    eyebrow + the tier-2 `tier === 2` 'Complete the council' eyebrow) are
    rendered in a real JS engine — neither was exercised by the tier-1/tier-3
    guards above, so a binding regression in EITHER branch (a broken eyebrow
    v-else, a tier-2 headline that drops the installed-provider labels, a
    mangled nextStep.installCommand) shipped a blank/garbled onboarding card to
    exactly the cold-install and two-provider audiences with zero coverage.
    """
    from playwright.sync_api import sync_playwright

    serve_root = tmp_path / "serve"
    rel = _write_prod_layout(html, serve_root)
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": viewport_w, "height": 1400}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.on(
                    "console",
                    lambda m: errs.append(f"console.error: {m.text[:160]}")
                    if m.type == "error"
                    else None,
                )
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.querySelector('[id$=\"-app\"]');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                return page.evaluate(_TIER_PROBE), errs
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_tier0_cold_install_card_renders_with_resolved_bindings(tmp_path, monkeypatch):
    """Tier 0 (NO provider on PATH — the truly-cold install) → the card renders
    its `v-else` 'Install a council provider' eyebrow + the cold headline + the
    first-provider install command, with NO undefined/garbled bindings.

    The tier-1/tier-3 guards never hit the `v-else` eyebrow branch — so a break
    there (e.g. the eyebrow collapsing or the cold headline binding evaluating
    to undefined) would ship a blank card to every cold-install user."""
    pytest.importorskip("playwright.sync_api")

    html = _render_with_binaries(monkeypatch, tmp_path / "home", on_path=set())
    s, errs = _drive_and_probe(tmp_path, html)

    assert s["found"], (
        "tier-0 (cold install, no providers) council-tier card did NOT render — "
        "the cold-install onboarding card is invisible to exactly the new-user audience"
    )
    assert s["eyebrow"] == "Install a council provider", s["eyebrow"]
    assert s["headline"] == "Install a council provider to get started.", s["headline"]
    # First missing provider's install command resolves (claude is _TIER_PROVIDERS[0]).
    assert s["installCode"] == "npm install -g @anthropic-ai/claude-code", s["installCode"]
    assert s["hasCopyBtn"], "copy button missing on the tier-0 card"
    assert not s["leaks"], (
        f"undefined/NaN/[object Object] leaked into the tier-0 card: {s['installCode']!r}"
    )
    assert not errs, f"JS errors rendering the tier-0 launchpad: {errs[:4]}"


def test_tier2_two_provider_card_renders_with_resolved_bindings(tmp_path, monkeypatch):
    """Tier 2 (claude + codex on PATH, antigravity missing) → the card renders
    its `tier === 2` 'Complete the council' eyebrow + a headline that name-checks
    BOTH installed providers + the antigravity install command.

    The tier-1/tier-3 guards never hit the `tier === 2` eyebrow branch nor the
    dual-provider 'You have X + Y' headline — a regression that drops the
    installed-provider labels from that headline (the core of the 'sells the
    other two' pitch) would pass every existing test."""
    pytest.importorskip("playwright.sync_api")

    html = _render_with_binaries(
        monkeypatch, tmp_path / "home", on_path={"claude", "codex"}
    )
    s, errs = _drive_and_probe(tmp_path, html)

    assert s["found"], "tier-2 (two providers) council-tier card did NOT render"
    assert s["eyebrow"] == "Complete the council", s["eyebrow"]
    # Headline name-checks BOTH installed providers (the 'works with what you
    # have, sells the next one' pitch) — a binding that drops them would empty
    # this assert.
    assert "Claude Code" in s["headline"], s["headline"]
    assert "Codex CLI" in s["headline"], s["headline"]
    assert "completes the canonical council" in s["headline"], s["headline"]
    # Next-step install command = the ONE missing provider (antigravity).
    assert (
        s["installCode"]
        == "curl -fsSL https://antigravity.google/cli/install.sh | bash"
    ), s["installCode"]
    assert not s["leaks"], (
        f"undefined/NaN/[object Object] leaked into the tier-2 card: {s['installCode']!r}"
    )
    assert not errs, f"JS errors rendering the tier-2 launchpad: {errs[:4]}"


def test_tier_card_icon_action_clears_44px_tap_target(tmp_path, monkeypatch):
    """The tier card's `.icon-action` copy button must clear the 44px touch
    floor at phone widths.

    Founder symptom + UX sweep: every `.icon-action` (Copy install command on the
    tier card + provider-command rows + embedder download, Reset anonymous ID,
    Ingest transcripts) is a REAL action button, yet the class is a fixed 34x34
    circle — a HALF-step under the WCAG 2.5.5 / Apple HIG 44px floor the founder
    explicitly flagged ("every action button clear 44px on touch widths"). On a
    touch-width launchpad (393/320) those are the ONLY tap target for those
    actions. This is the un-fixed sibling of the Iter-161 `.copy-badge` fix: that
    floored the code-block overlay badge with a `::before` hit-area extender; the
    `.icon-action` class (drawn from design_system's 34px circle, no floor) was
    never touched. Fix mirrors the copy-badge pattern: a transparent centered
    `::before` extends the HIT AREA to 44x44 while the visible circle stays 34px.

    Real-browser, rendered-geometry guard (not a string check): renders the
    tier-1 launchpad (the cold-user audience whose card carries the icon-action),
    scrolls the copy button into the viewport, and probes the clickable hit band
    via document.elementFromPoint at offsets that exceed the visible 34px circle.
    MUTATION-PROVEN red when the `.icon-action::before` hit-area extender is
    removed (the band collapses to the ~34px circle).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = _render_at_tier(monkeypatch, tmp_path / "home", only_claude=True)
    serve_root = tmp_path / "serve"
    rel = _write_prod_layout(html, serve_root)
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                for width in (393, 320):
                    page = browser.new_context(
                        viewport={"width": width, "height": 850}
                    ).new_page()
                    page.add_init_script(
                        "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                    )
                    page.goto(
                        f"http://127.0.0.1:{port}/{rel}",
                        wait_until="networkidle",
                        timeout=20000,
                    )
                    page.wait_for_function(
                        "[...document.querySelectorAll('.eyebrow')].some(e => /Add a 2nd provider/.test(e.textContent))",
                        timeout=10000,
                    )
                    sel = (
                        "section.card:has(.eyebrow:has-text('Add a 2nd provider')) "
                        "button.icon-action"
                    )
                    btn = page.query_selector(sel)
                    assert btn is not None, (
                        f"@ {width}px: tier-card copy `.icon-action` did not render — "
                        "the tap-target guard's precondition is missing (fixture regressed)."
                    )
                    # Tag the button with a stable id so the in-page probe can
                    # re-find it via NATIVE document.querySelector (the Playwright
                    # `:has-text()` engine selector is invalid inside evaluate()).
                    btn.evaluate("b => b.setAttribute('data-uxprobe', 'tier-copy')")
                    # Bring the (far-down-page) button into the viewport so
                    # elementFromPoint can probe it. Playwright's scroll settles
                    # reliably; an in-page scrollIntoView often does not for a
                    # below-fold element.
                    btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(250)

                    m = page.evaluate(
                        """() => {
                            const b = document.querySelector('button.icon-action[data-uxprobe="tier-copy"]');
                            const rect = b.getBoundingClientRect();
                            const cx = rect.left + rect.width / 2;
                            const cy = rect.top + rect.height / 2;
                            // Probe the vertical hit band: how far above/below the
                            // visible circle center does a click still land on the
                            // button? The transparent ::before extends this to 44px.
                            let up = 0, down = 0;
                            for (let dy = 0; dy <= 24; dy++) {
                                const el = document.elementFromPoint(cx, cy - dy);
                                if (el && el.closest && el.closest('button.icon-action') === b) up = dy; else break;
                            }
                            for (let dy = 0; dy <= 24; dy++) {
                                const el = document.elementFromPoint(cx, cy + dy);
                                if (el && el.closest && el.closest('button.icon-action') === b) down = dy; else break;
                            }
                            return { visibleH: Math.round(rect.height), hitBand: up + down };
                        }"""
                    )

                    # The visible circle stays compact (34px by design) so the
                    # extended hit area is what's load-bearing — a guard that
                    # passed because the circle GREW to 44px would be the wrong
                    # fix; this proves the ::before extender is the mechanism.
                    assert m["visibleH"] < 40, (
                        f"@ {width}px: the tier-card `.icon-action` visible circle is "
                        f"{m['visibleH']}px — expected the compact 34px circle (<40px). "
                        "If it grew, this guard's premise changed."
                    )
                    # The BITE: the clickable hit band must span >=40px (the 44px
                    # ::before box minus a sub-pixel / 1px-resolution margin).
                    # Pre-fix the band == the ~34px circle → RED. Founder symptom
                    # named in the message.
                    assert m["hitBand"] >= 40, (
                        f"@ {width}px: SUB-44px tap target — the tier card's `.icon-action` "
                        f"'Copy install command' button (the cold-user onboarding affordance) "
                        f"has a clickable hit band of only {m['hitBand']}px (visible circle "
                        f"{m['visibleH']}px) — under the 44px touch floor (WCAG 2.5.5 / Apple "
                        "HIG). The .icon-action::before hit-area extender is missing or "
                        "ineffective (the un-fixed sibling of the .copy-badge fix)."
                    )
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
