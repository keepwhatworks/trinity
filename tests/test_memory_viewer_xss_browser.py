"""Real-browser stored-XSS regression test for the memory viewer (v1.7.216).

The memory viewer renders corpus-DERIVED content: lens.md markdown, plus basin
labels / representatives / top_terms from topics.json. That content is NOT just
the founder's typed words — the corpus ingests arbitrary text from captured
conversations (claude.ai / chatgpt / gemini), so a chat that merely DISCUSSED
HTML/JS can deposit `<script>` / `<img onerror>` into a basin representative or a
lens tension. Rendering it is therefore an attacker-influenceable stored-XSS
surface.

`renderMarkdown` defends it (parse via marked → DOMParser → strip
script/style/iframe/object/embed, drop on* handlers, reject non-http(s)/mailto
href/src), and JSON fields render via createElement+textContent. `test_memory_
viewer.py` already pins those defenses — but only by SOURCE GREP (asserting the
sanitizer strings appear in the emitted HTML). A source-grep stays green even if
the real browser behaviour has a bypass (the e2e_chrome_dogfood lesson:
string-presence asserts miss what only execution reveals). This test loads the
ACTUAL rendered page in a real browser over the documented file:// path with a
battery of payloads and asserts none execute.

Gated on Playwright + a launchable chromium (skips in bare CI, runs on a dev box
with the browser installed — same posture as the gated real-Chrome smokes).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Spawns portal-html + launches chromium → a real-Chrome/subprocess test. Marked
# slow so the default `pytest -q` stays fast; runs in the slow shard
# (TRINITY_SLOW=1 / `pytest -m slow`). Enforced by
# test_gstack_patterns.TestSlowMarkerDiscipline.
pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# Each payload pushes a tag onto window.__XSS__ if it executes. The viewer must
# neutralize every one — window.__XSS__ stays unset, no dialog fires.
_LENS_PAYLOADS = """# Lens

tension: web-security vs ship-speed

- script: <script>window.__XSS__=(window.__XSS__||[]).concat('script')</script>
- img-onerror: <img src=x onerror="window.__XSS__=(window.__XSS__||[]).concat('img-onerror')">
- svg-script: <svg><script>window.__XSS__=(window.__XSS__||[]).concat('svg-script')</script></svg>
- md-js-link: [click](javascript:window.__XSS__=(window.__XSS__||[]).concat('md-js-link'))
- a-js-href: <a href="javascript:window.__XSS__=(window.__XSS__||[]).concat('a-href')">x</a>
- details-ontoggle: <details open ontoggle="window.__XSS__=(window.__XSS__||[]).concat('ontoggle')">x</details>
- form-action: <form action="javascript:window.__XSS__=(window.__XSS__||[]).concat('form-action')"><button formaction="javascript:window.__XSS__=(window.__XSS__||[]).concat('formaction')">go</button></form>
- data-img: <img src="data:image/svg+xml,%3Csvg onload=window.__XSS__=(window.__XSS__||[]).concat('data-svg')%3E">
"""

_TOPICS_PAYLOADS = {
    "basins": [
        {
            "id": "b00",
            "label": "<img src=x onerror=window.__XSS__=(window.__XSS__||[]).concat('label')>",
            "top_terms": ["<script>window.__XSS__=(window.__XSS__||[]).concat('term')</script>"],
            "representatives": [
                "<img src=x onerror=window.__XSS__=(window.__XSS__||[]).concat('rep')>"
            ],
            "size": 3,
        }
    ]
}


# The generators tier (the lens "lift", shipped 2026-06-05) is the NEWEST
# corpus-derived render surface: generators.md is chairman output mined from the
# same captured-conversation corpus as the lens, so it carries the identical
# stored-XSS risk and routes through the same `renderMarkdown` (DOMParser) path.
# It's an OPTIONAL tab — rendered only when the file exists — so the
# lens.md/topics.json fixture never exercised it in a real browser. Same battery
# woven into real card text, plus a card-structure assertion so a render-path
# regression that blanks the tab can't pass the XSS-null check on an empty render.
_GENERATORS_PAYLOADS = """## Generators (cross-domain invariants)

### 1. Build the generator, not the instance <script>window.__XSS__=(window.__XSS__||[]).concat('gen-script')</script>

**system over instance**

- **software** — you write the rule <img src=x onerror="window.__XSS__=(window.__XSS__||[]).concat('gen-img')">
- **materials** — you spec the module, not the one-off cut
- **finance** — you buy the durable asset

Projects task-tensions: 1, 2, 7

### 2. Verify the foundation [click](javascript:window.__XSS__=(window.__XSS__||[]).concat('gen-jslink'))

**load-bearing over decorative**

- **epistemology** — you find the invariant under the change

Projects task-tensions: 3
"""


def _render_adversarial_viewer(home: Path) -> Path:
    """Seed an isolated home with XSS-laden memory files, render the portal, and
    return the path to memory.html. Uses the real CLI (`portal-html`) so the test
    exercises the production render path, never a hand-built fixture."""
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "lens.md").write_text(_LENS_PAYLOADS, encoding="utf-8")
    (home / "memories" / "topics.json").write_text(
        json.dumps(_TOPICS_PAYLOADS), encoding="utf-8"
    )
    (home / "memories" / "generators.md").write_text(_GENERATORS_PAYLOADS, encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    mv = home / "portal_pages" / "memory.html"
    assert mv.exists(), "portal-html didn't write memory.html"
    # marked must be present, else renderMarkdown falls through to a <pre> (raw,
    # already-safe) path and the test wouldn't exercise the sanitizer at all.
    assert (home / "portal_pages" / "vendor" / "marked.min.js").exists(), (
        "vendored marked.min.js missing — the markdown sanitizer path wouldn't run"
    )
    return mv


def test_memory_viewer_neutralizes_stored_xss_in_real_browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    mv = _render_adversarial_viewer(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # chromium not installed in this env
            pytest.skip(f"no launchable chromium for the XSS browser test: {exc}")
        try:
            page = browser.new_context().new_page()
            alerts: list[str] = []

            def _on_dialog(d) -> None:
                alerts.append(d.message)
                d.dismiss()

            page.on("dialog", _on_dialog)

            # generators.md is the optional lens-lift tab: it must appear in the
            # nav ONLY because the file exists (the conditional-tab contract), and
            # then render + sanitize like the lens. Verify the nav link is present
            # before driving into the tabs.
            page.goto(f"file://{mv}")
            page.wait_for_timeout(800)
            gen_nav = page.query_selector("a.memory-nav-link[href*='generators.md']")
            assert gen_nav is not None, (
                "the generators tab didn't render in the nav even though "
                "generators.md exists — the conditional-tab wiring regressed"
            )

            for fname in ("lens.md", "topics.json", "generators.md"):
                page.goto(f"file://{mv}?file={fname}")
                page.wait_for_timeout(1000)
                xss = page.evaluate("window.__XSS__ || null")
                # Sanity: the file actually rendered (not a blank-page false pass).
                body_len = page.evaluate("document.body.innerText.length")
                marked_loaded = page.evaluate("!!window.marked")
                assert body_len > 50, (
                    f"{fname} produced a near-empty page ({body_len} chars) — the "
                    "XSS assertion would be a false pass on a blank render"
                )
                if fname == "generators.md":
                    # Same false-pass guard as lens.md: the XSS-null check passes on
                    # a raw `<pre>` fallback (raw text has no live elements), so pin
                    # that the cards rendered as MARKDOWN — headings + the generator
                    # name + projection list items became real DOM.
                    assert marked_loaded, (
                        "marked didn't load — the generators markdown took the "
                        "<pre> fallback, so the sanitizer path wasn't exercised"
                    )
                    gen = page.evaluate(
                        "(() => {"
                        "  const md = document.querySelector('.markdown-body') || document.body;"
                        "  return {"
                        "    headings: md.querySelectorAll('h2,h3').length,"
                        "    items: md.querySelectorAll('li').length,"
                        "    hasName: /Build the generator/.test(md.innerText),"
                        "  };"
                        "})()"
                    )
                    assert gen["hasName"] and gen["headings"] >= 2 and gen["items"] >= 1, (
                        "generators.md did NOT render as cards (headings="
                        f"{gen['headings']}, items={gen['items']}, hasName="
                        f"{gen['hasName']}) — the lens-lift tab is blank or broke"
                    )
                if fname == "lens.md":
                    assert marked_loaded, (
                        "marked didn't load — the markdown render took the <pre> "
                        "fallback, so the sanitizer path wasn't exercised"
                    )
                    # The lens IS the hero ("own your taste") — it must render as
                    # MARKDOWN, not raw text. The XSS + body_len + marked_loaded
                    # checks all pass on a raw-`<pre>` fallback render (raw text
                    # has no dangerous elements), so a render-path regression that
                    # left the lens as unreadable `# Lens / - script:` would slip
                    # through green-while-broken. The adversarial fixture seeds a
                    # `# Lens` heading + `- ` list items; assert they became real
                    # elements (so renderMarkdown ran, not the raw fallback).
                    rendered = page.evaluate(
                        "(() => {"
                        "  const md = document.querySelector('.markdown-body')"
                        "    || document.getElementById('content')"
                        "    || document.body;"
                        "  return {"
                        "    headings: md.querySelectorAll('h1,h2,h3,h4').length,"
                        "    items: md.querySelectorAll('li').length,"
                        "  };"
                        "})()"
                    )
                    assert rendered["headings"] >= 1 and rendered["items"] >= 1, (
                        "lens.md did NOT render as markdown (headings="
                        f"{rendered['headings']}, list items={rendered['items']}) — "
                        "renderMarkdown took the raw fallback or broke; the hero "
                        "surface would show unreadable raw `# Lens` text"
                    )
                assert xss is None, (
                    f"stored XSS executed while rendering {fname}: payload(s) "
                    f"{xss} ran — the memory-viewer sanitizer has a bypass"
                )
                assert not alerts, f"a dialog fired rendering {fname}: {alerts}"
        finally:
            browser.close()
