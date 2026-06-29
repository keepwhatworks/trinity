"""Real-browser guard: the LAUNCHPAD taste card must NOT paint a confident lens over
the SHA-1 TF-IDF fallback embedder — it must carry the same DEGRADED honesty the CLI
`lens-health` verb prints and the memory viewer's .viewer-trust-banner already carries.

The sibling-surface extension of the #35 green-while-degraded class (the viewer got
its trust banner in the prior iteration; the launchpad renders the SAME
embedding-derived artifact — the lens tensions — on the simple HOME view). Under the
SHA-1 TF-IDF fallback (a fresh install with no [mlx] extras — NORMAL operation, not
corrupt), the lens still BUILDS, but its tensions are "caricatures of your taste, not
it" (lens_health._embedding_backend → DEGRADED, trustworthy=False). The launchpad's
taste card was painting "Your taste, distilled · The patterns in how you think" with
the confident paired-lenses list and only a SOFT "Sharper meaning needs the embedder
— see stats" pointer (feature-framing, NOT a degraded warning) — while
`trinity-local lens-health` on the SAME home says DEGRADED/untrustworthy. The
embedder-status card's download mode (the fresh-install case) frames the missing
embedder as an OPTIONAL upsell and lives on /stats only, so the simple HOME view gave
ZERO honesty about the displayed lens being a caricature.

This seeds a populated lens home (me/lenses.json → tasteLenses present), renders the
real launchpad home view TWICE — once forcing the TF-IDF fallback
(TRINITY_DISABLE_MLX=1, the degraded case) and once with the live embedder (the
positive control) — and asserts:
  * DEGRADED home: the taste card shows a .lp-trust-banner naming "DEGRADED" +
    "TF-IDF" + "caricature".
  * REAL-embedder home (positive control): NO trust banner — it must not cry wolf on
    a trustworthy lens.

Bite preconditions (both must hold or the value assertion is meaningless):
  (A) the page MOUNTED — the confident taste content rendered ("The patterns in how
      you think") and there is NO raw `{{` petite-vue/template leak.
  (B) the discriminating seed is real, checked RENDER-INDEPENDENTLY: lens_health's own
      `_embedding_backend()` returns DEGRADED under TRINITY_DISABLE_MLX=1 and OK
      without it, on the SAME seeded home — so the test asserts the launchpad agrees
      with the verb, not a banner that fires unconditionally.

Mutation-proven RED on the un-fixed source: remove the .lp-trust-banner block from
the taste card (or the lensTrust payload) → the DEGRADED assertion goes red (the
confident taste card renders with no honesty) while the positive control stays green.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import functools
import http.server
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _render_launchpad(home: Path, force_tfidf: bool) -> Path:
    """Seed a populated lens home + render the real launchpad home view into
    <home>/portal_pages/launchpad.html. Seed + render run in ONE subprocess with
    TRINITY_HOME set, so the seeder's `write_portal_html` publishes the vendored
    petite-vue NEXT TO the page (else the app never mounts and the banner check is
    vacuous). me/lenses.json → tasteLenses present, so the confident taste card —
    the surface the trust banner must qualify — actually renders."""
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO) + os.pathsep + str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if force_tfidf:
        env["TRINITY_DISABLE_MLX"] = "1"
    else:
        env.pop("TRINITY_DISABLE_MLX", None)
    code = (
        "import os;from pathlib import Path;"
        "import scripts.seed_synthetic_home as seeder;"
        "h=Path(os.environ['TRINITY_HOME']);"
        "seeder.seed(h);"  # publishes vendor via write_portal_html (TRINITY_HOME set)
        "from trinity_local import launchpad_page;"
        "html=launchpad_page.render_launchpad_html(view='home');"
        "(h/'portal_pages'/'launchpad.html').write_text(html,encoding='utf-8')"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"launchpad seed+render failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "launchpad.html").exists()
    assert (pages / "vendor" / "petite-vue.iife.js").exists(), "vendor petite-vue not published — page won't mount"
    return pages


def _backend_status(home: Path, force_tfidf: bool) -> str:
    """Bite-precondition (B), render-independent: ask lens_health's OWN embedding probe
    on this exact home/env, in a clean subprocess (fresh mlx-probe cache). The
    launchpad must AGREE with this verdict, not fire unconditionally."""
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if force_tfidf:
        env["TRINITY_DISABLE_MLX"] = "1"
    else:
        env.pop("TRINITY_DISABLE_MLX", None)
    code = "from trinity_local import lens_health as lh; print(lh._embedding_backend().status)"
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"backend probe failed: {r.stderr[-300:]}"
    return r.stdout.strip().splitlines()[-1]


def _drive(pages: Path) -> dict:
    """Drive the real launchpad over http and report the taste-card trust banner +
    the two bite preconditions. Binds an EPHEMERAL port (0) so parallel runs / leftover
    driver servers never collide."""
    from playwright.sync_api import sync_playwright

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(pages))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as sp:
            try:
                browser = sp.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
                page.goto(f"http://127.0.0.1:{port}/launchpad.html", wait_until="load")
                page.wait_for_timeout(900)
                banner = page.query_selector(".taste-card .lp-trust-banner")
                body = page.evaluate("() => document.body.innerText")
                return {
                    "banner": bool(banner and banner.is_visible()),
                    "text": (banner.inner_text() if banner else "").replace("\n", " "),
                    "mounted": "The patterns in how you think" in body,  # bite (A)
                    "raw_leak": "{{" in body,                            # bite (A)
                }
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_tfidf_taste_card_shows_degraded_trust_banner():
    pytest.importorskip("playwright.sync_api")

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)

    # ── bite precondition (B): the seed genuinely produces DEGRADED under the
    # fallback — render-independent, straight from the verb. ──
    degraded_status = _backend_status(home, force_tfidf=True)
    assert degraded_status == "degraded", (
        "fixture sanity: lens_health._embedding_backend() must report DEGRADED under "
        f"TRINITY_DISABLE_MLX=1 (got {degraded_status!r}) — the discriminating seed is broken"
    )

    pages = _render_launchpad(home, force_tfidf=True)
    res = _drive(pages)

    # bite (A): the confident taste card actually painted (else "no banner" passes
    # vacuously on a blank page) and there's no raw template leak.
    assert res["mounted"], (
        "taste card did not mount ('The patterns in how you think' absent) — the banner "
        "check would be vacuous"
    )
    assert not res["raw_leak"], "raw '{{' template leak — launchpad broke before render"

    assert res["banner"], (
        "NO trust banner on the taste card over a TF-IDF-fallback lens — the launchpad "
        "paints a confident 'The patterns in how you think' over a keyword caricature "
        "(lens-health says DEGRADED). Founder symptom: green-while-degraded lens on the "
        "LAUNCHPAD taste card (#35), the memory viewer's sibling surface."
    )
    txt = res["text"].lower()
    assert "degraded" in txt, f"trust banner missing 'DEGRADED' label: {res['text'][:160]!r}"
    assert "tf-idf" in txt, f"trust banner does not name the TF-IDF fallback: {res['text'][:160]!r}"
    assert "caricature" in txt, f"trust banner does not name the caricature symptom: {res['text'][:160]!r}"


def test_real_embedder_taste_card_shows_no_trust_banner():
    """Positive control: with the live embedder the taste card must NOT show the trust
    banner — no crying wolf on a trustworthy lens."""
    pytest.importorskip("playwright.sync_api")

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)

    status = _backend_status(home, force_tfidf=False)
    if status != "ok":
        pytest.skip(f"real embedder not loadable in this env (backend={status!r}) — control N/A")

    pages = _render_launchpad(home, force_tfidf=False)
    res = _drive(pages)

    assert res["mounted"], "taste card did not mount in the positive-control render"
    assert not res["banner"], (
        "trust banner shown on a LIVE-embedder lens — the launchpad cried wolf: the "
        "lensTrust gate must key off lens_health._embedding_backend() == DEGRADED only"
    )
