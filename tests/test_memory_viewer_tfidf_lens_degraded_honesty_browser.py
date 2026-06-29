"""Real-browser guard: the memory viewer must NOT paint a confident lens over the
SHA-1 TF-IDF fallback embedder — it must carry the same DEGRADED honesty the CLI
`lens-health` verb prints.

CLAUDE.md's load-bearing rule (graceful degradation + green-gate discipline): under
the SHA-1 TF-IDF fallback (a fresh install with no [mlx] extras — NORMAL operation,
not corrupt), every SEMANTIC flow is keyword-shaped, not meaning-shaped, so the lens
tensions / topic basins / distillation are "caricatures of your taste, not it"
(lens_health._embedding_backend → DEGRADED, trustworthy=False). The viewer was
painting "Your lens · Paired tensions you'd reject vs accept" with the confident
tensions list and topology graph and NO embedder-trust honesty — the exact
green-while-degraded class (#35) on a NEW surface. A user on a fresh install reads a
green, authoritative lens built on garbage and has no idea it's untrustworthy, while
`trinity-local lens-health` on the SAME home says DEGRADED.

This seeds a populated lens home (lens.md + topics.json + core.md + generators.md
present), renders the real portal viewer TWICE — once forcing the TF-IDF fallback
(TRINITY_DISABLE_MLX=1, the degraded case) and once with the live embedder (the
positive control) — and asserts:
  * DEGRADED home: ALL FOUR embedding-derived files (lens.md, topics.json, core.md,
    generators.md — the cross-domain lift, the ONLY optional/hidden-when-absent one)
    each show a .viewer-trust-banner naming "DEGRADED" + "TF-IDF fallback" +
    "caricatures", and vocabulary.md (lexical anchors, correct on the fallback) +
    picks.json (council scoreboard, not embedding-derived) do NOT.
  * REAL-embedder home (positive control): NO trust banner on any file — the banner
    must not cry wolf on a trustworthy lens.

Bite preconditions (both must hold or the value assertion is meaningless):
  (A) the page MOUNTED — the confident lens content rendered ("Your lens" header) and
      there is NO raw `{{` petite-vue/template leak.
  (B) the discriminating seed is real, checked RENDER-INDEPENDENTLY: lens_health's own
      `_embedding_backend()` returns DEGRADED under TRINITY_DISABLE_MLX=1 and OK without
      it, on the SAME seeded home — so the test asserts the viewer agrees with the
      verb, not a banner that fires unconditionally.

Mutation-proven RED on the un-fixed source: remove the trust-banner block from
renderHeader (or the lens_trust payload) → the DEGRADED assertions go red (the
confident lens renders with no honesty) while the positive control stays green.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# The four embedding-derived files the trust banner must cover, and the two it must NOT.
# generators.md (the lens "lift") is the ONLY optional/hidden-when-absent one — it is
# in TRUST_SCOPED so a built-but-TF-IDF generators tab must carry the same DEGRADED
# honesty as the always-present three, or it paints a confident cross-domain lift over
# a keyword caricature (the green-while-degraded class, #35, on the generators surface).
_SEMANTIC = ("lens.md", "topics.json", "core.md", "generators.md")
_EXEMPT = ("vocabulary.md", "picks.json")


def _seed(home: Path) -> None:
    """A populated lens: lens.md tensions, a topics.json with basins, core.md, vocab,
    picks. Enough that every file view renders confident content — the surface the
    banner must qualify."""
    import json

    mem = home / "memories"
    mem.mkdir(parents=True)
    (mem / "lens.md").write_text(
        "## Lens\n\n### Tensions\n"
        "- **concrete vs abstract**: leans concrete\n"
        "- **action vs description**: leads with the action\n",
        encoding="utf-8",
    )
    (mem / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "centroid": [1.0, 0.0, 0.0, 0.0], "size": 20, "label": "Design",
         "top_terms": ["design", "arch"], "representatives": [{"id": "r0", "snippet": "a design prompt"}]},
        {"id": "b01", "centroid": [0.0, 1.0, 0.0, 0.0], "size": 12, "label": "Debug",
         "top_terms": ["debug", "fix"], "representatives": [{"id": "r1", "snippet": "a debug prompt"}]},
    ]}), encoding="utf-8")
    (mem / "vocabulary.md").write_text("## Anchors\n- Trinity\n", encoding="utf-8")
    # generators.md (the lift) — OPTIONAL/hidden-when-absent, so it must be written for
    # the viewer to surface its tab at all; with content present it is isBuilt, the
    # precondition for the trust banner (a built generators tab on a TF-IDF lens).
    (mem / "generators.md").write_text(
        "## Generators (the lift)\n\n### Cross-domain invariants\n"
        "- **Prefer the resilient default**: the same reflex in software and materials.\n"
        "- **Lead with the action, defer the description.**\n",
        encoding="utf-8",
    )
    (home / "core.md").write_text(
        "You favor concrete, resilient designs and lead with the action.\n", encoding="utf-8"
    )
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 8, "margin": 0.42, "n_episodes": 8, "evidence": ["c1"]},
    }), encoding="utf-8")


def _render_portal(home: Path, force_tfidf: bool) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if force_tfidf:
        env["TRINITY_DISABLE_MLX"] = "1"
    else:
        env.pop("TRINITY_DISABLE_MLX", None)
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def _backend_status(home: Path, force_tfidf: bool) -> str:
    """Bite-precondition (B), render-independent: ask lens_health's OWN embedding
    probe on this exact home/env, in a clean subprocess (so the mlx-probe cache is
    fresh). The viewer must AGREE with this verdict."""
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if force_tfidf:
        env["TRINITY_DISABLE_MLX"] = "1"
    else:
        env.pop("TRINITY_DISABLE_MLX", None)
    code = (
        "import sys; "
        "from trinity_local import lens_health as lh; "
        "print(lh._embedding_backend().status)"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"backend probe failed: {r.stderr[-300:]}"
    return r.stdout.strip().splitlines()[-1]


def _trust_banners(pages: Path) -> dict:
    """Drive the real viewer over file:// for each file and report, per file, whether
    a .viewer-trust-banner is present (+ its text) AND the two bite preconditions."""
    from playwright.sync_api import sync_playwright

    out: dict = {}
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            for fname in (*_SEMANTIC, *_EXEMPT):
                page.goto(f"file://{pages / 'memory.html'}?file={fname}", wait_until="load")
                page.wait_for_timeout(700)
                banner = page.query_selector(".viewer-trust-banner")
                body = page.evaluate("() => document.body.innerText")
                out[fname] = {
                    "banner": bool(banner),
                    "text": (banner.inner_text() if banner else "").replace("\n", " "),
                    "mounted": "Your lens" in body,         # bite (A)
                    "raw_leak": "{{" in body,               # bite (A)
                }
        finally:
            browser.close()
    return out


def test_tfidf_lens_shows_degraded_trust_banner():
    pytest.importorskip("playwright.sync_api")

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed(home)

    # ── bite precondition (B): the seed genuinely produces DEGRADED under the
    # fallback and OK with the live embedder — render-independent, from the verb. ──
    degraded_status = _backend_status(home, force_tfidf=True)
    assert degraded_status == "degraded", (
        "fixture sanity: lens_health._embedding_backend() must report DEGRADED under "
        f"TRINITY_DISABLE_MLX=1 (got {degraded_status!r}) — the discriminating seed is broken"
    )

    pages = _render_portal(home, force_tfidf=True)
    banners = _trust_banners(pages)

    failures: list[str] = []
    for fname in (*_SEMANTIC, *_EXEMPT):
        b = banners[fname]
        # bite (A): the confident lens surface actually painted (else "no banner"
        # would pass vacuously on a blank page).
        if not b["mounted"]:
            failures.append(f"{fname}: viewer did not mount ('Your lens' absent) — banner check is vacuous")
        if b["raw_leak"]:
            failures.append(f"{fname}: raw '{{{{' template leak — page broke before render")

    for fname in _SEMANTIC:
        b = banners[fname]
        if not b["banner"]:
            failures.append(
                f"{fname}: NO trust banner on a TF-IDF-fallback lens — the viewer paints a "
                "confident lens over a keyword caricature (lens-health says DEGRADED). "
                "Founder symptom: green-while-degraded lens on the memory viewer (#35)."
            )
            continue
        txt = b["text"].lower()
        if "degraded" not in txt:
            failures.append(f"{fname}: trust banner missing 'DEGRADED' label: {b['text'][:120]!r}")
        if "tf-idf" not in txt:
            failures.append(f"{fname}: trust banner does not name the TF-IDF fallback: {b['text'][:120]!r}")
        if "caricature" not in txt:
            failures.append(f"{fname}: trust banner omits the 'caricature' honesty: {b['text'][:120]!r}")

    for fname in _EXEMPT:
        if banners[fname]["banner"]:
            failures.append(
                f"{fname}: trust banner FALSELY shown — {fname} is "
                + ("lexical anchors (correct on the fallback)" if fname == "vocabulary.md"
                   else "a council scoreboard, not embedding-derived")
                + "; the banner must scope to the semantic files only"
            )

    assert not failures, "memory viewer TF-IDF degraded-honesty broken:\n  " + "\n  ".join(failures)


def test_real_embedder_lens_shows_no_trust_banner():
    """Positive control: with the live embedder, NO trust banner anywhere — the
    banner must not cry wolf on a trustworthy lens (otherwise it's noise on every
    install that DID set up the embedder)."""
    pytest.importorskip("playwright.sync_api")
    from trinity_local import embeddings

    if not embeddings.mlx_actually_loaded():
        pytest.skip("real MLX embedder not loadable in this env — positive control needs it")

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed(home)

    # bite (B): the verb says OK on this home with the live embedder.
    ok_status = _backend_status(home, force_tfidf=False)
    assert ok_status == "ok", (
        f"fixture sanity: live embedder should report OK (got {ok_status!r})"
    )

    pages = _render_portal(home, force_tfidf=False)
    banners = _trust_banners(pages)

    failures: list[str] = []
    for fname in (*_SEMANTIC, *_EXEMPT):
        b = banners[fname]
        if not b["mounted"]:
            failures.append(f"{fname}: viewer did not mount ('Your lens' absent)")
        if b["banner"]:
            failures.append(
                f"{fname}: trust banner shown on a REAL-embedder lens — crying wolf; "
                f"the banner must fire only under the TF-IDF fallback. text={b['text'][:120]!r}"
            )

    assert not failures, "trust banner cried wolf on a trustworthy lens:\n  " + "\n  ".join(failures)
