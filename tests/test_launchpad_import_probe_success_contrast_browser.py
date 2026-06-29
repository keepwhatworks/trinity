"""The Bulk-import card's PROBE-SUCCESS confirmation must clear WCAG AA — the
"✓ Detected N export(s)" headline and each detected source name are READABLE TEXT,
not a decorative fill.

CONTRAST defect (UX sweep 2026-06-22): a user who pastes a Takeout/export path and
clicks Probe lands on the success banner:

    ✓ Detected 2 export(s)
      chatgpt · 12 conversations
      gemini  · 40 conversations

Both the bold "✓ Detected …" headline and each source name (`chatgpt`/`gemini`)
were drawn in the brand FILL teal — `color: var(--success, #4f9095)` and
`color: var(--accent, #4f9095)` — over the green success tint
`rgba(45,106,79,0.06)`-over-`--surface`. design_system.py's own token comment says
`--success` is "NOT readable as text on the green success tint (2.9:1)" — it is the
FILL token (the 3px border-left, the checkmark glyph), and `--success-text`
(#2d6a4f) exists precisely for readable text on this tint. The source-name span was
ALSO double-dimmed by an `opacity: 0.8` on its `<li>`, dropping it to ~2.5:1.

Measured: headline 3.235:1, source-name span 2.466:1 (opacity-folded) — both BELOW
the 4.5:1 AA body floor (13px bold is below the 14px-bold large threshold; 12px is
plain body).

WHY THE GLOBAL CONTRAST GUARD (test_contrast_aa_global_browser) MISSED IT: this
banner is a CONDITIONAL state (`v-if="importProbeResult && !importProbeResult.error"`)
inside the settings drawer's bulk-import `<details>` — it only mounts AFTER a
successful Probe dispatch. The global guard reads each surface's INITIAL DOM and
never seeds `importProbeResult`, so these text nodes were never composited. This
guard drives the REAL success state to mount them.

The fix: the headline + source name use `--success-text` (#2d6a4f, 5.67:1 on the
tint), and the `<li>`'s opacity-dimming was replaced by an opaque `--text-secondary`
hint color (5.35:1) so the source name isn't double-dimmed.

MUTATION-PROVEN to BITE: revert the headline/source color back to `var(--success)`
/ `var(--accent)` (and restore the `<li>` `opacity:0.8`) → REBUILD the bundles →
this guard reds at ~3.24/2.47:1 with the founder symptom; the diagnostic guard
(test_launchpad_import_probe_diagnostic_browser) stays green (it never reads color).
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# AA body floor (the codebase convention; both text nodes are < the large threshold).
_AA_BODY = 4.5


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# A composited-contrast reader run IN the page: folds the FULL ancestor background
# stack (every rgba tint over white) AND the EFFECTIVE OPACITY CHAIN into the
# foreground — the two dimensions an un-folded getComputedStyle().color would miss
# (the panel sweep proved a tint under the text and stacked `opacity` both matter).
_CONTRAST_JS = r"""
(selector) => {
  function lin(c){ c/=255; return c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4); }
  function lum(rgb){ return 0.2126*lin(rgb[0])+0.7152*lin(rgb[1])+0.0722*lin(rgb[2]); }
  function ratio(a,b){ const L1=lum(a),L2=lum(b),hi=Math.max(L1,L2),lo=Math.min(L1,L2); return (hi+0.05)/(lo+0.05); }
  function parse(s){ const m=s.match(/[\d.]+/g).map(Number); return {rgb:m.slice(0,3), a: m.length>=4?m[3]:1}; }
  const el = document.querySelector(selector);
  if (!el) return { missing: true };
  // composite the ancestor bg stack over white
  let stack=[], n=el;
  while(n){ const cs=getComputedStyle(n); const bc=cs.backgroundColor;
    if(bc && bc!=='rgba(0, 0, 0, 0)' && bc!=='transparent'){ stack.push(parse(bc)); } n=n.parentElement; }
  let bg=[255,255,255];
  for(let i=stack.length-1;i>=0;i--){ const {rgb,a}=stack[i]; bg=[0,1,2].map(k=> Math.round(a*rgb[k]+(1-a)*bg[k])); }
  // effective opacity chain (opacity composites the element + its ink over the backdrop)
  let op=1; n=el; while(n){ op*= parseFloat(getComputedStyle(n).opacity); n=n.parentElement; }
  const cs=getComputedStyle(el);
  let fg=parse(cs.color).rgb;
  fg=[0,1,2].map(k=> Math.round(op*fg[k]+(1-op)*bg[k]));
  return { ratio: ratio(fg,bg), color: cs.color, fontSize: cs.fontSize, text: el.innerText.trim().slice(0,40), opacity: op };
}
"""


def test_import_probe_success_banner_text_clears_aa(tmp_path, monkeypatch):
    """The probe-success headline + each detected source name must clear AA on the
    green success tint — the FILL teal as readable text was sub-AA."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "stats.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")

    # A dispatcher that answers the dry-run probe with a TWO-source detection so the
    # success banner mounts with a real headline + source-name rows.
    probe_ok = json.dumps(
        {
            "ok": True,
            "stdout": json.dumps(
                {"detected": [{"source": "chatgpt", "hint": "12 conversations"},
                              {"source": "gemini", "hint": "40 conversations"}]}
            ),
        }
    )

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # 393 (phone) and 1280 (desktop): contrast is width-independent here,
                # but the card has mobile flex rules, so prove it on both.
                for width in (393, 1280):
                    page = browser.new_context(viewport={"width": width, "height": 1100}).new_page()
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
                    # Paste a path + Probe → the success banner mounts.
                    page.fill("section.import-export-card input[type=text]", "/Users/you/Downloads/exports")
                    page.evaluate(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " c.querySelector('button').click(); }"
                    )
                    page.wait_for_function(
                        "() => { const c = document.querySelector('section.import-export-card');"
                        " const s = c.querySelector('strong');"
                        " return s && /Detected/.test(s.innerText); }",
                        timeout=4000,
                    )

                    # PRECONDITION (non-vacuous): the banner headline + a source-name
                    # span are actually present and painted before we judge contrast.
                    headline = page.evaluate(
                        _CONTRAST_JS, "section.import-export-card div strong"
                    )
                    source = page.evaluate(
                        _CONTRAST_JS, "section.import-export-card ul li span"
                    )
                    assert not headline.get("missing"), (
                        f"@{width}px: the probe-SUCCESS '✓ Detected …' headline never "
                        "rendered — the success banner did not mount, so this guard is "
                        "vacuous. Check the dispatcher stub / probe wiring."
                    )
                    assert not source.get("missing"), (
                        f"@{width}px: the detected source-name span never rendered."
                    )
                    assert "Detected" in headline["text"], headline
                    assert source["text"], source

                    assert headline["ratio"] >= _AA_BODY, (
                        f"@{width}px: the Bulk-import PROBE-SUCCESS headline "
                        f"'✓ Detected N export(s)' draws the brand FILL teal as "
                        f"readable text — {headline['ratio']:.3f}:1 (color {headline['color']}, "
                        f"{headline['fontSize']}), BELOW the {_AA_BODY}:1 AA body floor. "
                        "Use --success-text (the readable green TEXT token), not "
                        "--success (the FILL token design_system documents as 'NOT "
                        "readable as text on the green success tint')."
                    )
                    assert source["ratio"] >= _AA_BODY, (
                        f"@{width}px: the detected source name "
                        f"('{source['text']}') in the probe-success banner is sub-AA "
                        f"at {source['ratio']:.3f}:1 (color {source['color']}, effective "
                        f"opacity {source['opacity']:.2f}) — the brand FILL teal "
                        "double-dimmed by the <li> opacity. Use --success-text and "
                        "drop the opacity dimming so the source name is readable."
                    )
                    page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
