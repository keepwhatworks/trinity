"""Browser guard: a NON-FINITE float (NaN / Infinity) in page_data must NOT
crash the client-side mount — the founder symptom is a BLANK launchpad.

The crash class this closes (Lane D, error-path/partial-state robustness):
`json.dumps` defaults to `allow_nan=True`, emitting the BARE literals `NaN` /
`Infinity` / `-Infinity` for non-finite floats. Those tokens are NOT legal JSON,
so the browser's `JSON.parse(document.getElementById('page-data').textContent)`
THROWS a SyntaxError, petite-vue never mounts, and the WHOLE launchpad renders
blank (body innerText collapses to the static "No councils yet" sliver).

A single corrupted / partially-written `evals/results/eval_*.json` whose
`aggregate_score` is `NaN` is enough to take the surface down — `json.loads`
ACCEPTS the bare `NaN` literal, the value sails past the `is None` skip, and
`page_data_script_json` re-emits it as bare `NaN` into the inline `#page-data`
script. Proven before the fix: pageerror
`Unexpected token 'N', ..."te_score":NaN,... is not valid JSON` + body innerText
length 42.

The fix is at the shared embed boundary (`design_system._finite_json_safe` +
`allow_nan=False` in `page_data_script_json`) so EVERY surface that inlines
page_data (launchpad / stats / both council pages / memory viewer) is covered,
plus the eval data layer (`_usable_score`) skips a non-finite run so it can
neither headline the hero nor paint a `nan` leaderboard row.

This guard renders the REAL launchpad with a NaN-scored result on disk and pins:
  • NO pageerror (the JSON.parse SyntaxError is gone),
  • the petite-vue app MOUNTED (body innerText is the full home, not the sliver,
    and the hero tagline "Ask all three. Keep what works." painted),
  • no literal "nan" / "NaN" / "Infinity" leaked into the visible body.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_nan_eval_result(home: Path) -> None:
    """Write a single eval result whose aggregate_score (and one axis mean) is a
    bare NaN — the partial-write / external-corruption shape json.loads accepts."""
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    nan_result = {
        "eval_id": "eval_corrupt",
        "target_provider": "antigravity",
        "target_model": "Gemini 3.1 Pro",
        "aggregate_score": float("nan"),  # the corruption
        "items_completed": 5,
        "items_total": 5,
        "items_failed": 0,
        "by_rejection_type": {
            "REFRAME": {"count": 5, "mean_score": float("nan"), "min_score": 0.0, "max_score": 1.0},
        },
        "items": [{"judge_provider": "claude"}],
        "completed_at": "2026-06-18T12:00:00+00:00",
    }
    # json.dumps defaults to allow_nan=True → writes the bare `NaN` literal,
    # exactly what a partial/mangled on-disk result looks like.
    raw = json.dumps(nan_result)
    assert "NaN" in raw, "fixture precondition: the on-disk result carries a bare NaN literal"
    (results / "eval_corrupt__model_antigravity.json").write_text(raw, encoding="utf-8")
    (home / "evals" / "eval_corrupt.json").write_text(
        json.dumps({"eval_id": "eval_corrupt", "items": [1] * 5}), encoding="utf-8"
    )


def test_nan_score_does_not_blank_the_launchpad(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_nan_eval_result(tmp_path)

    # Source-sanity the embed boundary directly: the inline page-data JSON must be
    # finite-safe (no bare NaN/Infinity), or JSON.parse on the client throws and
    # the page blanks. This is the precondition the browser then proves end-to-end.
    from trinity_local.design_system import page_data_script_json
    from trinity_local.launchpad_page import build_launchpad_payload

    page_data = build_launchpad_payload()["pageData"]
    inline = page_data_script_json(page_data)
    assert "NaN" not in inline and "Infinity" not in inline, (
        "REGRESSION: page_data_script_json emitted a bare NaN/Infinity into the "
        "inline #page-data JSON — the client's JSON.parse will throw a SyntaxError "
        "and BLANK the whole launchpad. A non-finite float (a NaN-scored corrupt "
        f"eval result) reached page_data unsanitised. inline head: {inline[:200]!r}"
    )
    # And it must still be parseable as strict JSON (allow_nan=False round-trip).
    json.loads(inline.replace("\\u003c", "<"), parse_constant=_reject_constant)

    html = render_launchpad_html()
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1200)

                # The exact founder symptom: a JSON.parse SyntaxError on the bare
                # NaN aborts the mount. If the embed regresses, this reds first.
                assert not errs, (
                    "REGRESSION: a NaN-scored eval result on disk threw a JS error "
                    "(the client JSON.parse of the inline #page-data choked on a bare "
                    f"NaN/Infinity) and BLANKED the launchpad: {errs[:4]}"
                )

                info = page.evaluate(
                    """() => {
                      const body = document.body.innerText || '';
                      return {
                        len: body.length,
                        mounted: body.includes('Ask all three. Keep what works.'),
                        leaksNan: /\\bnan\\b/i.test(body) || body.includes('Infinity'),
                        body: body.slice(0, 200),
                      };
                    }"""
                )

                # MOUNTED: the petite-vue app painted the full home (hero tagline +
                # a substantial body), not the static "No councils yet" sliver that
                # remains when the mount aborts (pre-fix body len was ~42).
                assert info["mounted"] and info["len"] > 400, (
                    "REGRESSION: the launchpad did NOT mount with a NaN-scored eval "
                    "result on disk — petite-vue aborted on the inline JSON and the "
                    "page collapsed to the static sliver "
                    f"(len={info['len']}, mounted={info['mounted']}, body={info['body']!r})"
                )

                # The non-finite value must DEGRADE (template '—'), never paint as
                # the literal "nan"/"Infinity" score.
                assert not info["leaksNan"], (
                    "REGRESSION: a non-finite eval score leaked the literal "
                    f"'nan'/'Infinity' into the visible launchpad body: {info['body']!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def _reject_constant(name: str):  # pragma: no cover - only fires on a regression
    raise AssertionError(
        f"inline #page-data JSON contained the non-finite constant {name!r} — "
        "JSON.parse would reject it and blank the page"
    )
