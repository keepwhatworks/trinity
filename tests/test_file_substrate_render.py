"""Pin: the documented file:// production path renders clean (relative vendor
resolves, no console errors, no petite-vue template leak).

`trinity-local portal-html --open-browser` opens the launchpad via `file://` —
NOT http. That path DIVERGES from the HTTP browser-smoke gate (relative `./vendor/`
script srcs instead of absolute, the `?t=` cache-buster skipped on `file://`,
different script-injection) — see the `file_substrate_browser_testing` note. The
HTTP smoke can be fully green while a `file://`-specific regression (an absolute
`/vendor/` URL assumption, a broken `isFile` branch) silently blanks the real
page. This was only ever verified MANUALLY (2026-06-01, 2026-06-02); this converts
it into a standing guard.

Gated on Playwright + a launchable chromium (skips in bare CI; runs on a dev box).
Uses an isolated synthetic home + the real `portal-html` CLI — the production
render path, never a hand-built fixture; no PII.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "me").mkdir(parents=True)
    # Populated enough that the launchpad shows a lens card and the memory viewer
    # has a topology to draw (exercises petite-vue on the launchpad + d3 on the
    # memory viewer — the two vendored libs whose relative ./vendor/ load is the
    # file:// divergence point).
    (home / "me" / "lenses.json").write_text(json.dumps({"lenses": [{
        "pole_a": "concrete", "pole_b": "abstract", "failure_a": "vague",
        "failure_b": "brittle", "basins_spanned": ["b1", "b2"]}]}))
    (home / "memories" / "lens.md").write_text(
        "# Lens\n\n## Lenses (paired tensions)\n\n### 1. concrete ↔ abstract\n"
        "- Pure-concrete fails as: **vague**\n"
    )
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": f"b{i:02d}", "label": f"topic {i}", "top_terms": ["x", "y"],
         "representatives": ["r"], "size": 3} for i in range(5)
    ]}))
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pp = home / "portal_pages"
    assert (pp / "launchpad.html").exists() and (pp / "memory.html").exists()
    assert (pp / "vendor" / "petite-vue.iife.js").exists()
    return pp


def test_file_substrate_renders_clean():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    pp = _render_portal(home)
    # (name, file:// url, vendor global that must be defined if ./vendor/ resolved)
    targets = [
        ("launchpad", f"file://{pp}/launchpad.html", "vue"),
        ("memory:lens", f"file://{pp}/memory.html?file=lens.md", "d3"),
        ("memory:topics", f"file://{pp}/memory.html?file=topics.json", "d3"),
    ]
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed
            pytest.skip(f"no launchable chromium for the file:// render test: {exc}")
        try:
            page = browser.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            for name, url, glob in targets:
                errs.clear()
                page.goto(url, wait_until="load")  # networkidle can hang on file://
                page.wait_for_timeout(1000)
                st = page.evaluate(
                    "() => ({ body: (document.body.innerText || '').trim().length,"
                    " leak: /\\{\\{|\\}\\}/.test(document.body.innerText || '') })"
                )
                vendor_ok = page.evaluate(
                    "(g) => g === 'vue' ? (typeof window.__TRINITY_VUE__ !== 'undefined')"
                    " : (typeof window.d3 !== 'undefined')",
                    glob,
                )
                assert st["body"] > 50, f"{name}: blank on file:// (body={st['body']})"
                assert not st["leak"], f"{name}: petite-vue template leak on file://"
                assert vendor_ok, (
                    f"{name}: vendor ({glob}) did not load on file:// — a relative "
                    "./vendor/ or cache-buster regression broke the production path"
                )
                assert not errs, f"{name}: console errors on file://: {errs[:3]}"
        finally:
            browser.close()


def test_cold_launchpad_renders_clean_on_empty_home():
    """The COLD launchpad — a truly empty ~/.trinity, every new user's FIRST view —
    must render cleanly in a real browser. `test_phase7_fresh_install` only asserts
    portal-html exits 0 (does-it-crash), and `test_frontend_flow`'s cold-start test
    is STRING-presence (its own note: "cold-start browser-found 2026-06-01 — string
    presence is necessary [not sufficient]"). Nothing mounts petite-vue on empty
    pageData in a browser — exactly where the first impression breaks: a card that
    does `pageData.x.toFixed(2)` on a null field throws; one that does `'… ' + x`
    leaks the literal "undefined"; an unguarded empty-state leaves `{{ }}`. This
    pins the cold render: Vue mounts, cards paint, a first-action affordance is
    present, and NO undefined/NaN/null/template-leak reaches the visible page."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)  # EMPTY — no memories/, councils, evals, topics
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html crashed on a fresh home: {r.stderr[-400:]}"
    pp = home / "portal_pages"
    assert (pp / "launchpad.html").exists()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium for the cold-render test: {exc}")
        try:
            page = browser.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(f"file://{pp}/launchpad.html", wait_until="load")
            page.wait_for_timeout(1500)
            st = page.evaluate(
                """() => {
                  const t = document.body.innerText || '';
                  return {
                    bodyLen: t.trim().length,
                    leak: /\\{\\{|\\}\\}/.test(t),
                    undefinedLeak: /\\bundefined\\b/.test(t),
                    nanLeak: /\\bNaN\\b/.test(t),
                    nullLeak: /\\bnull\\b/.test(t),
                    vueMounted: typeof window.__TRINITY_VUE__ !== 'undefined',
                    firstAction: /Ask|Build|Get started|Install|lens/i.test(t),
                    cards: document.querySelectorAll('.card, section.card').length,
                  };
                }"""
            )
        finally:
            browser.close()

    assert st["vueMounted"], "petite-vue did not load on the cold launchpad (file://)"
    assert st["bodyLen"] > 200, f"the cold launchpad is ~blank (body={st['bodyLen']}) — bad first impression"
    assert st["cards"] >= 3, f"the cold launchpad rendered only {st['cards']} cards — empty-state collapsed"
    assert st["firstAction"], "no first-action affordance on the cold launchpad — a new user has no next step"
    assert not st["leak"], "petite-vue template leak ({{ }}) on the cold launchpad"
    # The green-while-degenerate trip-wires: a card computing on empty pageData must
    # NOT paint the raw degenerate value into the first view a user ever sees.
    assert not st["undefinedLeak"], "the literal 'undefined' leaked into the cold launchpad (a card read a null field)"
    assert not st["nanLeak"], "'NaN' leaked into the cold launchpad (a numeric card divided by an empty count)"
    assert not st["nullLeak"], "'null' leaked into the cold launchpad (a card stringified a null field)"
    assert not errs, f"the cold launchpad threw JS errors on empty pageData: {errs[:4]}"


def test_cold_memory_viewer_renders_clean_on_empty_home():
    """The COLD memory viewer — opened from the launchpad on a truly empty
    ~/.trinity — must render honest empty-states in a real browser, not blank
    tabs or leaked template tokens.

    `test_memory_viewer.py` is ENTIRELY string-presence (``"X" in html``); none
    of it proves the client-side ``renderEmpty`` path actually paints under
    file:// when a memory file is MISSING (the cold case every new user hits
    before running dream/lens-build). This is the viewer sibling of
    ``test_cold_launchpad_renders_clean_on_empty_home`` (the launchpad guard) —
    drives the real production file:// path on a synthetic empty home. For each
    core tab the content must show the "Not built yet. Run trinity-local …"
    affordance, NO undefined/NaN/{{ }} leak reaches the page, and there are zero
    console/page errors. The OPTIONAL generators tab must be ABSENT from the cold
    nav (it shows only after ``lens-generators`` writes generators.md)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)  # EMPTY — no memories/, councils, evals, topics
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html crashed on a fresh home: {r.stderr[-400:]}"
    pp = home / "portal_pages"
    assert (pp / "memory.html").exists(), "portal-html did not write the memory viewer"

    tabs = ("core.md", "lens.md", "topics.json", "vocabulary.md", "picks.json", "routing.json")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium for the cold-viewer test: {exc}")
        try:
            page = browser.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            per_tab: dict[str, tuple] = {}
            for tab in tabs:
                errs.clear()
                page.goto(f"file://{pp}/memory.html?file={tab}", wait_until="load")
                page.wait_for_timeout(500)
                st = page.evaluate(
                    """() => {
                      const t = document.body.innerText || '';
                      return {
                        bodyLen: t.trim().length,
                        emptyState: /Not built yet|No .+ yet|run trinity-local/i.test(t),
                        leak: /\\{\\{|\\}\\}/.test(t),
                        undef: /\\bundefined\\b/.test(t),
                        nan: /\\bNaN\\b/.test(t),
                      };
                    }"""
                )
                per_tab[tab] = (st, list(errs))
            # The optional generators tier must NOT show a tab on a cold home.
            page.goto(f"file://{pp}/memory.html", wait_until="load")
            page.wait_for_timeout(300)
            gen_hidden = page.evaluate(
                "() => !/generators/i.test(document.body.innerText || '')"
            )
        finally:
            browser.close()

    for tab, (st, tab_errs) in per_tab.items():
        assert not tab_errs, f"{tab}: console/page errors on the cold viewer: {tab_errs[:3]}"
        assert st["bodyLen"] > 80, f"{tab}: cold viewer is ~blank (body={st['bodyLen']})"
        assert st["emptyState"], (
            f"{tab}: no 'Not built yet' affordance on the cold viewer — a new user "
            f"who clicks a memory chip lands on a dead/blank tab with no next step"
        )
        assert not st["leak"], f"{tab}: petite-vue/template leak ({{ }}) on the cold viewer"
        assert not st["undef"], f"{tab}: the literal 'undefined' leaked into the cold viewer"
        assert not st["nan"], f"{tab}: the literal 'NaN' leaked into the cold viewer"
    assert gen_hidden, (
        "the optional generators tab must be hidden on a cold home — it only "
        "appears after lens-generators writes generators.md"
    )


def test_telemetry_toggle_without_extension_copies_cli_and_stays_honest():
    """The privacy toggle must WORK on the documented file:// open path even with
    NO Chrome extension — the common case (a privacy-conscious user who declined
    the extension, or anyone who opened the launchpad before wiring it).

    The launchpad applies settings through the extension's Native-Messaging
    dispatcher (`window.__TRINITY_DISPATCH__`), which is ALWAYS injected
    (launchpad_runtime.py) whether or not the extension is installed. So when the
    extension isn't reachable the dispatch FAILS — and `handleDispatchResult`
    would surface the generic "install our Chrome extension" banner. The settings
    modal promises "toggle it off anytime", so for a privacy opt-out, telling the
    user to INSTALL a browser extension to turn telemetry OFF is backwards. The
    fix routes a failed settings dispatch to `fallbackToSettingsCli`: it copies
    the equivalent CLI command and keeps the modal open with a ✓ that says the
    displayed toggle is unchanged until they run it.

    `test_telemetry_no_pii.py` pins the DATA + JS string-wiring deterministically;
    this pins the actual user PATH in a real browser — the synthetic `change`
    event petite-vue ignores (only a trusted click fires `@change`), so a
    string-presence test alone can't prove the click→failed-dispatch→copy→honest
    chain. NOTE the ✓ auto-clears after 1800ms (copyLens' restore timer), so this
    reads the confirmation the instant wait_for_selector(visible) returns, not
    after a fixed sleep. Mutation: revert triggerSettingsAction's failed-dispatch
    fallback → no ✓ appears (the install banner shows instead) → this reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    pp = _render_portal(home)  # telemetry is default-ON in a fresh home

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium for the telemetry-toggle test: {exc}")
        try:
            page = browser.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(f"file://{pp}/launchpad.html", wait_until="load")
            # Headless file:// has no actual extension to receive the Native Message,
            # so the always-present dispatcher reports unreachable — the path under test.
            no_extension = page.evaluate(
                "() => !(window.__TRINITY_DISPATCH__ && window.__TRINITY_DISPATCH__.extensionId"
                " && window.chrome && window.chrome.runtime)"
            )
            page.wait_for_timeout(700)
            page.locator("[aria-label='Open settings']").first.click()
            # The <input> is visually hidden behind the slider — a real user clicks
            # the slider, which fires a TRUSTED change petite-vue's @change honors.
            page.wait_for_selector(".sharing-toggle .toggle-slider", state="visible", timeout=4000)
            before = page.eval_on_selector(".sharing-toggle input[type=checkbox]", "el => el.checked")
            page.locator(".sharing-toggle .toggle-slider").first.click()
            # The dispatch fails fast (no extension) → fallbackToSettingsCli copies +
            # flips copiedKey; the ✓ renders, then auto-clears at 1800ms. Catch it on
            # appearance and snapshot immediately.
            page.wait_for_selector("text=run it in your terminal", state="visible", timeout=4000)
            st = page.evaluate(
                """() => ({
                  confShown: /✓ Copied — run it in your terminal/.test(document.body.innerText || ''),
                  checkbox: (() => { const i = document.querySelector('.sharing-toggle input[type=checkbox]'); return i ? i.checked : 'GONE'; })(),
                  modalOpen: !!document.querySelector('.sharing-toggle'),
                })"""
            )
        finally:
            browser.close()

    assert no_extension, "test precondition: headless file:// must have no reachable extension"
    assert st["confShown"], (
        "toggling telemetry off without the extension showed no ✓ confirmation — "
        "the toggle dead-ends on the install-extension banner with no way to opt out"
    )
    assert st["modalOpen"], "the settings modal closed — the ✓ confirmation would be invisible"
    # The displayed toggle must NOT flip: the CLI command is copied, not applied, so
    # showing it as 'off' would be dishonest (telemetry is still on until they run it).
    assert st["checkbox"] == before, (
        f"the toggle flipped its displayed state (before={before}, after={st['checkbox']}) "
        f"without applying the setting — dishonest; it must stay as-is until the user runs the command"
    )
    assert not errs, f"console errors while toggling telemetry on file://: {errs[:3]}"


def _seed_council() -> str:
    """Write a synthetic completed council + its launchpad redirect file into the
    CURRENT TRINITY_HOME (the caller MUST set it first — these are in-process calls
    that resolve `trinity_home()` at call time, unlike the portal-html subprocess)."""
    from trinity_local.council_review import write_unified_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )
    from trinity_local.launchpad_page import write_portal_html

    cid = "council_filesub1"
    outcome = CouncilOutcome(
        council_run_id=cid, bundle_id=cid, task_cluster_id="c", primary_provider="claude",
        winner_provider="claude", metadata={"task_text": "Cache in-process or per-call?"},
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text="In-process caching wins."),
            CouncilMemberResult(provider="codex", model="gpt", output_text="Per-call is simpler."),
        ],
        synthesis_prompt="p", synthesis_output="In-process caching wins for latency reasons.",
        routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
        created_at="2026-06-02T00:00:00+00:00",
    )
    save_council_outcome(outcome)
    write_portal_html()  # vendor + memory pages
    write_unified_council_page(PromptBundle(bundle_id=cid, task_cluster_id="c", task_text="Cache?"), outcome)
    return cid


def test_live_council_page_renders_on_file_substrate(tmp_path, monkeypatch):
    """The PAINKILLER on the documented file:// path. `portal-html --open-browser`
    opens the launchpad on file://, and a council link navigates to the redirect
    `{crid}.html` → `live_council.html?council_id=`. That chain is file://-specific
    and load-bearing AND non-obvious: the page reads `?council_id=` from
    `window.location.search` and loads the outcome JSONP via a relative
    `../council_outcomes/` base — and council_review.py warns "file:// URLs can't
    carry query strings" (true only for the JSONP cache-buster; the page's own query
    DOES survive on modern Chromium). The standing file:// guard covered only the
    launchpad + memory viewer, so the founder's primary output had no file:// guard:
    an absolute-base regression or a query-stripping "fix" would leave a clicked
    council BLANK on the documented open path, with the http smoke still green.
    Verified clean 2026-06-02; this pins it. Mutation: break outcomeScriptBaseUrl
    (the relative JSONP base) → the synthesis never loads → synthesisShown reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Isolate the home BEFORE seeding — _seed_council writes via in-process
    # trinity_home(), so without this it would pollute the real ~/.trinity.
    home = tmp_path / "trinity"
    home.mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    cid = _seed_council()
    # The launchpad links to the redirect file, NOT live_council directly — exercise
    # the full production chain: redirect → ?council_id= → relative JSONP load.
    redirect_url = f"file://{home / 'review_pages' / (cid + '.html')}"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"no launchable chromium for the file:// council test: {exc}")
        try:
            page = browser.new_page()
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(redirect_url, wait_until="load")
            page.wait_for_timeout(1800)  # redirect + JSONP load + petite-vue mount
            st = page.evaluate(
                """() => ({
                  search: window.location.search,
                  body: (document.body.innerText || '').trim().length,
                  leak: /\\{\\{|\\}\\}/.test(document.body.innerText || ''),
                  synthesisShown: /In-process caching wins for latency/.test(document.body.innerText || ''),
                  segments: document.querySelectorAll('.chain-segment[data-seg-key]').length,
                  vendorOk: typeof window.__TRINITY_VUE__ !== 'undefined',
                })"""
            )
        finally:
            browser.close()

    # The redirect must have landed on the council view WITH the query preserved.
    assert "council_id=" in st["search"], (
        f"the redirect dropped the ?council_id= query on file:// — the council is "
        f"unreachable on the open path (search={st['search']!r})"
    )
    assert st["vendorOk"], "petite-vue did not load on file:// (relative ./vendor/ regressed)"
    assert st["body"] > 50, f"the live council page is blank on file:// (body={st['body']})"
    assert not st["leak"], "petite-vue template leak ({{ }}) on the file:// council page"
    assert st["segments"] >= 1, "no council segment rendered — the JSONP outcome didn't load on file://"
    assert st["synthesisShown"], (
        "the synthesis never rendered — the relative JSONP outcome load failed on file:// "
        "(the painkiller would be blank when opened via portal-html --open-browser)"
    )
    assert not errs, f"console errors on the file:// council page: {errs[:3]}"
