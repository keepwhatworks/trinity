"""A user staring at a STALE / EDITED memory in the memory-health card must be
able to INSPECT that file before deciding to run the rebuild command.

USEFULNESS / orphan-affordance defect (2026-06-17 UX sweep): the memory-health
card template ships an "Inspect →" link (`<a v-if="issue.href">`) next to every
issue, but `launchpad_data._memory_health` hardcoded `href: None` on EVERY issue
(all 7 append sites), so the link could NEVER render. A user with a stale
core.md / lens.md / picks.json got a copy-command chip but no way to LOOK at the
file the issue is about — even though the memory viewer renders exactly those
files and the affordance was built for precisely this.

The fix backfills `href` = ../portal_pages/memory.html?file=<name> for the
FILE-backed issues (core/lens/vocab/topics/picks). The `extension` repair signals
are actions, not viewable files, so they correctly stay href=None.

This drives the REAL petite-vue render with a seeded STALE memory state (so the
href flows through the real _memory_health backfill, not a hand-crafted dict) and
asserts a clickable "Inspect →" link lives inside the memory-health card AND
navigates to the right memory.html?file=. Mutation-provable: revert the href
backfill → href is None again → the link never renders → this guard reds.
"""
from __future__ import annotations

import functools
import http.server
import os
import threading
import time
from pathlib import Path

import pytest


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_memory_health_issue_renders_clickable_inspect_link(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # Seed a REAL stale state so _memory_health emits FILE-backed issues with real
    # hrefs (not a hand-crafted memoryHealth dict): core.md backdated older than a
    # fresh lens.md → core.md stale; topics.json with basins but no thread_count →
    # pre-thread-aware. Both are file-backed → both must get an Inspect-> href.
    core = home / "core.md"
    core.write_text("old distillation", encoding="utf-8")
    old = time.time() - 10
    os.utime(core, (old, old))
    (home / "memories" / "lens.md").write_text("fresh lens tensions", encoding="utf-8")
    (home / "memories" / "topics.json").write_text(
        '{"basins": [{"id": "b00", "size": 1, "top_terms": [], "centroid": []}]}',
        encoding="utf-8",
    )

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    issues = page_data.get("memoryHealth", {}).get("issues", [])
    file_issues = [i for i in issues if i["name"] in ("core.md", "topics.json")]
    assert file_issues, "fixture should produce >=1 file-backed memory-health issue"

    # The memory-health card is a stats-card (display:none on home), so render /stats.
    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    # A stub memory.html so the Inspect-> click resolves to a real URL (we only
    # assert the navigation TARGET, not the viewer contents).
    (pp / "memory.html").write_text(
        "<html><body id='memory-viewer-stub'>memory viewer</body></html>", encoding="utf-8"
    )
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1200}).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                # Every file-backed issue ROW must carry a visible "Inspect →" link
                # pointing at the memory viewer for that exact file. This is the
                # orphan-affordance invariant: the link existed in the template but
                # the data layer never fed it, so it rendered ZERO times.
                probe = page.evaluate(
                    "() => {"
                    " const card = document.querySelector('section.memory-health-card');"
                    " if (!card) return { card: false };"
                    " const rows = Array.from(card.querySelectorAll('li'));"
                    " const out = rows.map(li => {"
                    "   const name = (li.querySelector('code')||{}).textContent || '';"
                    "   const link = Array.from(li.querySelectorAll('a'))"
                    "     .find(a => /inspect/i.test(a.textContent));"
                    "   return { name: name.trim(),"
                    "     hasInspect: !!link,"
                    "     href: link ? link.getAttribute('href') : null }; });"
                    " return { card: true, visible: card.offsetParent !== null, rows: out }; }"
                )
                assert probe["card"], "memory-health card not rendered"
                assert probe["visible"], "memory-health card not visible on /stats"
                rows_by_name = {r["name"]: r for r in probe["rows"]}
                for fname in ("core.md", "topics.json"):
                    if fname not in rows_by_name:
                        continue
                    row = rows_by_name[fname]
                    assert row["hasInspect"], (
                        f"the {fname} memory-health issue has NO 'Inspect →' link — "
                        "the card's inspect affordance is DEAD (href was None on every "
                        "issue), so the user can't look at the stale memory before "
                        "running the rebuild command"
                    )
                    assert row["href"] == f"../portal_pages/memory.html?file={fname}", (
                        f"the {fname} Inspect link points at the wrong target: "
                        f"{row['href']!r}"
                    )

                # And clicking it actually navigates to the memory viewer for that
                # file (a real, non-dead anchor — not a # / no-op).
                page.evaluate(
                    "() => {"
                    " const card = document.querySelector('section.memory-health-card');"
                    " const link = Array.from(card.querySelectorAll('a'))"
                    "   .find(a => /inspect/i.test(a.textContent));"
                    " link.click(); }"
                )
                page.wait_for_function(
                    "() => /memory\\.html\\?file=/.test(location.href)", timeout=4000
                )
                assert "memory.html?file=" in page.url, (
                    "Inspect → click did not navigate to the memory viewer "
                    f"(landed on {page.url})"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.slow
@pytest.mark.browser
def test_memory_health_headline_counts_match_the_issue_list(tmp_path, monkeypatch):
    """The memory-health card's HEADLINE summary scalar must paint the right
    numbers: "N memories need attention · M of K healthy" where N == the number
    of issue ROWS below it, M == ok_count, K == total_count, and M + N == K.

    SCALAR value-binding gap (2026-06-21 UX sweep, Track A): the card headline
    binds three DERIVED counts — `{{ memoryHealth.issues.length }}` (the count of
    memories needing attention), `{{ memoryHealth.ok_count }}` and
    `{{ memoryHealth.total_count }}` ("M of K healthy"). The data layer guards the
    invariant `ok_count + len(issues) == total_count` (test_memory_health.py) and
    the cost-signal browser test renders the card — but with a HAND-CRAFTED
    memoryHealth dict, and it asserts only the dispatch-COST copy, never these
    counts. So the rendered headline scalar was UNGUARDED against the real
    `_memory_health()` arithmetic: a template mis-binding (the headline bound to
    `total_count` instead of `issues.length`, `ok_count` painted in the
    needs-attention slot, or the issues array swapped) would paint a headline that
    DISAGREES with the issue list it heads — "5 memories need attention" over 2
    rows — while every existing memory-health test stays green.

    This drives the REAL petite-vue render with a seeded state that produces a
    DISCRIMINATING headline (2 issues, ok_count=5, total_count=7 — all three
    distinct, so an issues↔ok↔total swap can't pass vacuously), and asserts the
    painted headline numbers against the FIXTURE CONSTANTS read render-INDEPENDENTLY
    from page_data. Mutation-provable: bind the needs-attention count to
    `memoryHealth.total_count` (or `ok_count`) in the template → the painted "N
    memories need attention" no longer equals the row count → this reds.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "home"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    # Discriminating seed → EXACTLY two file-backed issues (core.md stale +
    # topics.json pre-thread-aware), so ok_count=5, total_count=7 (all 3 distinct).
    # vocab.md is written NEWEST of the three thinking memories so the vocabulary
    # staleness signal stays silent (keeps the issue count deterministic at 2).
    core = home / "core.md"
    core.write_text("old distillation", encoding="utf-8")
    now = time.time()
    os.utime(core, (now - 100, now - 100))
    (home / "memories" / "topics.json").write_text(
        '{"basins": [{"id": "b00", "size": 1, "top_terms": [], "centroid": []}]}',
        encoding="utf-8",
    )
    os.utime(home / "memories" / "topics.json", (now - 80, now - 80))
    (home / "memories" / "lens.md").write_text("fresh lens tensions", encoding="utf-8")
    os.utime(home / "memories" / "lens.md", (now - 60, now - 60))
    (home / "memories" / "vocabulary.md").write_text("# vocab", encoding="utf-8")
    os.utime(home / "memories" / "vocabulary.md", (now - 40, now - 40))

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    page_data = build_launchpad_payload()["pageData"]
    mh = page_data.get("memoryHealth") or {}
    # FIXTURE CONSTANTS, read render-INDEPENDENTLY from the data layer (NEVER from
    # the painted DOM — else a real regression would red on a misleading "seed"
    # message a dev could mis-fix to mask the bug).
    issues_n = len(mh.get("issues") or [])
    ok_count = mh.get("ok_count")
    total_count = mh.get("total_count")
    # BITE precondition: the seed actually produced the discriminating shape, so
    # the headline assertion below can't pass vacuously on equal numbers.
    assert issues_n == 2 and ok_count == 5 and total_count == 7, (
        "fixture drifted — expected exactly 2 issues / ok_count=5 / total_count=7 "
        f"(all distinct so a count-swap is detectable), got "
        f"issues={issues_n} ok_count={ok_count} total_count={total_count} "
        f"names={[i.get('name') for i in (mh.get('issues') or [])]}"
    )
    assert len({issues_n, ok_count, total_count}) == 3, "the three counts must be distinct"

    html = render_launchpad_html(page_data=page_data, view="stats")

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                probe = page.evaluate(
                    "() => {"
                    " const card = document.querySelector('section.memory-health-card');"
                    " if (!card) return { card: false };"
                    " const h2 = card.querySelector('h2');"
                    " const rows = Array.from(card.querySelectorAll('ul.memory-health-list > li'));"
                    " return { card: true,"
                    "   visible: card.offsetParent !== null,"
                    "   h2text: h2 ? h2.innerText.replace(/\\s+/g,' ').trim() : null,"
                    "   rawLeak: (card.innerHTML || '').includes('{{'),"
                    "   rowCount: rows.length }; }"
                )
                # BITE precondition A: the headline element actually PAINTS (no raw
                # template leak — an un-mounted petite-vue would leave `{{ }}`).
                assert probe["card"] and probe["visible"], "memory-health card not visible on /stats"
                assert probe["h2text"], "memory-health headline <h2> never painted"
                assert not probe["rawLeak"], (
                    "memory-health card leaked a raw `{{` template — petite-vue did "
                    "not hydrate, so the count assertion can't bite"
                )
                assert not errs, f"JS errors rendering the memory-health card: {errs[:3]}"

                h2 = probe["h2text"]
                # THE BINDING ASSERTION (sole thing keyed on the painted scalars):
                # the headline must name the SEEDED counts, and the needs-attention
                # number must equal the actual row count it heads.
                assert f"{issues_n} memories need attention" in h2, (
                    "FOUNDER SYMPTOM: the memory-health headline count disagrees with "
                    "the issue list it heads — it painted a 'N memories need attention' "
                    f"that is NOT the {issues_n} issue rows below it. A template "
                    "mis-binding (needs-attention bound to total_count/ok_count instead "
                    f"of issues.length) paints e.g. '{total_count} memories need "
                    f"attention' over {issues_n} rows. Headline: {h2!r}"
                )
                assert f"{ok_count} of {total_count} healthy" in h2, (
                    "FOUNDER SYMPTOM: the memory-health headline 'M of K healthy' "
                    f"painted the wrong arithmetic — expected '{ok_count} of "
                    f"{total_count} healthy' (ok_count of total_count, where "
                    f"ok_count + issues == total_count). Headline: {h2!r}"
                )
                assert probe["rowCount"] == issues_n, (
                    "FOUNDER SYMPTOM: the painted issue-row count "
                    f"({probe['rowCount']}) disagrees with the seeded issue count "
                    f"({issues_n}) — the headline and the list it heads are bound to "
                    "different data."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
