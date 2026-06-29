"""Real-side-panel guard: the launchpad HOME council card ("Ask every model at
once") — the first-impression value prose every new user reads — must describe the
chairman's synthesis in PLAIN ENGLISH, never leak a raw JSON field name
(`why_matters`) into the user-facing copy.

Founder symptom (UX sweep, 2026-06-19, driven in the REAL Chrome side panel home
view): the council card's value paragraph read

    "A local chairman synthesizes — agreed claims, disagreed claims with
     *why_matters*, picked winner."

`why_matters` is the snake_case JSON KEY of the Routing-schema disagreement field
(council_runtime.py emits `"why_matters": "<why this disagreement matters>"`). It
has no meaning to a reader — and it sat ITALICIZED, as if it were a term of art,
between two plain-English phrases ("agreed claims", "picked winner"). CLAUDE.md's
naming rule is explicit: slugs / JSON keys belong in code, config, file paths and
the literal CLI commands a user pastes — user-facing UI uses plain words. This was
the single snake_case code-symbol leak in an emphasis span across the whole
launchpad template, on the highest-traffic surface in the product.

The fix: the emphasized term reads "why each matters" — natural English emphasis
that says what the field carries (why each disagreement matters).

Why the REAL side panel (not a served file:// render): the home view is petite-vue
mounted (the shell is v-cloak'd until @vue:mounted), and the mount reads live
pageData (`browserExtension`, …) the capture host supplies — a bare file:// render
with `page_data=None` never strips the cloak, so the home value card never paints.
The panel boot delivers real launchpad_data over the capture host, the home view
actually mounts + paints, and the probe reads the PAINTED prose — proving the user
sees no code symbol AND that the card carries no raw `{{ }}` leak.

Mutation-provable: revert the `<em>why each matters</em>` term back to
`<em>why_matters</em>`, rebuild the sidepanel bundle, and the no-code-symbol
assertion reds with the founder symptom. Slow + browser marked; skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import json
import re
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"

_STUB_OK = """
() => {
  window.__TRINITY_DISPATCH__ = {
    dispatch: function (opts) { if (opts && opts.onResult) opts.onResult({ ok: true, tier: 'extension' }); },
    probe: function () { return Promise.resolve('present'); },
    onStateChange: function () {}, subscribe: function () { return function () {}; },
  };
}
"""

# A snake_case code identifier: a lowercase word joined to another lowercase word
# by an underscore (why_matters, provider_scores, disagreed_claims, …). This is the
# shape a JSON key / dispatch slug takes when it leaks into prose.
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b")

_PROBE = """() => {
  const cards = [...document.querySelectorAll('article.card, section.card')];
  const card = cards.find(c => /Ask every model at once/.test(c.textContent || ''));
  if (!card) return { found: false };
  const r = card.getBoundingClientRect();
  const p = [...card.querySelectorAll('p.meta, p')]
              .find(el => /chairman synthesizes/.test(el.textContent || ''));
  if (!p) return { found: false };
  const em = p.querySelector('em');
  return {
    found: true,
    visible: r.width > 0 && r.height > 0 && card.offsetParent !== null,
    prose: (p.textContent || '').trim(),
    em: em ? (em.textContent || '').trim() : null,
    rawTemplateLeak: /\\{\\{/.test(card.innerHTML || ''),
  };
}"""


def _boot_panel(p, tmp_path, monkeypatch):
    """Boot the REAL side panel over a delegating capture-host stub with a seeded
    synthetic home, so the launchpad HOME view fully mounts + paints."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)

    from trinity_local.launchpad_page import _assemble_page_data, build_launchpad_payload

    _, recent_sidebar = _assemble_page_data(force_live_page=False)
    payload = build_launchpad_payload()
    payload["recentSidebarHtml"] = recent_sidebar
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    stub = (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json, os\n"
        f"os.environ['TRINITY_HOME'] = {str(home)!r}\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from trinity_local.capture_host import QUERY_HANDLERS\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "msg = msg or {}\n"
        "qk = msg.get('query_kind')\n"
        "if qk == 'launchpad_data':\n"
        f"    out = open({str(pl)!r}).read().encode()\n"
        "elif qk in QUERY_HANDLERS:\n"
        "    out = json.dumps(QUERY_HANDLERS[qk](msg), default=str).encode()\n"
        "else:\n"
        "    out = json.dumps({'ok': True}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )
    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(stub, encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    try:
        ctx = p.chromium.launch_persistent_context(
            str(ud), headless=False,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no launchable chromium: {exc}")

    sw = None
    for _ in range(50):
        if ctx.service_workers:
            sw = ctx.service_workers[0]
            break
        try:
            sw = ctx.wait_for_event("serviceworker", timeout=2000)
            break
        except Exception:
            time.sleep(0.1)
    assert sw, "extension service worker never registered (manifest invalid?)"
    ext_id = sw.url.split("/")[2]
    (nm / f"{HOST}.json").write_text(json.dumps({
        "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }), encoding="utf-8")

    page = ctx.new_page()
    page.add_init_script(_STUB_OK)
    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4500)
    return ctx, page


def test_council_card_prose_has_no_code_symbol_leak(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert lf is not None, "the launchpad iframe never loaded in the panel"
            s = lf.evaluate(_PROBE)

            assert s.get("found"), (
                "the home council card ('Ask every model at once') value paragraph did "
                f"not render in the panel — the first-impression surface is missing. got: {s!r}"
            )
            assert s.get("visible"), (
                "the home council card is in the DOM but not visible — the home view "
                f"never mounted/painted in the panel. got: {s!r}"
            )
            prose = s["prose"]
            assert prose, "the council-card value prose rendered empty"
            assert not s.get("rawTemplateLeak"), (
                "the council card leaked raw {{ }} — petite-vue did not mount the home view"
            )

            # THE BITE: the user-facing council prose must carry NO snake_case
            # code symbol. `why_matters` (the JSON disagreement-field key) leaking
            # ITALICIZED into the first-impression copy is the founder symptom.
            leaked = _SNAKE.findall(prose)
            assert not leaked, (
                "REGRESSION: the launchpad HOME council card leaked a raw JSON "
                "field name / code symbol into user-facing first-impression prose "
                f"(the *why_matters* leak) — found {leaked!r} in: {prose!r}. "
                "User-facing UI uses plain English; JSON keys stay in code/config."
            )
            assert "why_matters" not in prose, (
                f"the council card still names the JSON key 'why_matters' in prose: {prose!r}"
            )
            assert s.get("em") and "why" in s["em"].lower() and "matter" in s["em"].lower(), (
                "the council card's emphasized term should read as plain English about "
                f"WHY the disagreements matter — got em={s.get('em')!r}"
            )
        finally:
            ctx.close()


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys as _sys

    _sys.exit(pytest.main([__file__, "-v", "-s"]))
