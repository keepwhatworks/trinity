"""Real-browser stored-XSS regression test for the LAUNCHPAD corpus surface — the
sibling of test_live_council_xss_browser.py (live page) and
test_council_review_xss_browser.py (static review page).

The launchpad renders ATTACKER-INFLUENCEABLE corpus text in TWO places the other
XSS tests never touch:

  1. the recent-councils RAIL — `build_recent_sidebar_html` builds RAW HTML in
     Python (NOT petite-vue), injecting the council title (= the corpus
     metadata.task_text), the winner brand, and the relative date straight into
     `<a class="rail-council">…</a>`. Every field MUST route through `_esc`
     (html.escape). A new rail field that skips `_esc`, or a swap of `_esc` for a
     raw concat, opens stored-XSS: a malicious prompt / captured web page whose
     task_text contains `<img onerror=>` or `</script><script>` would execute on
     EVERY launchpad open.

  2. the `pageData` JSON baked into `<script type="application/json" id="page-data">`
     — `page_data_script_json` escapes every `<` to `<` so a task_text
     containing `</script>` cannot break out of the inline script element. Dropping
     that escape reopens a `</script><script>…</script>` breakout.

The petite-vue `{{ }}` interpolation that consumes pageData is auto-escaped, so the
live-rendered rail/cards are safe by default — but the Python-built rail HTML and
the inline-JSON breakout are SEPARATE sinks that the `{{ }}` safety does NOT cover,
and no existing browser test drove the launchpad with a poisoned corpus. This test
seeds one council whose task_text + winner-model + member output + claims carry a
live XSS battery, renders the REAL launchpad.html over HTTP, and asserts NOTHING
executes while the rail still renders the (escaped) title.

Synthetic data only; no PII. Slow + browser marked.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Each payload bumps window.__xss_fired if it executes. A correct escape
# neutralizes every one. The `</script>` and `">`-breakout variants target the
# launchpad-specific sinks (inline page-data JSON + the raw-HTML rail attrs) the
# live/review XSS tests don't exercise.
_PAYLOADS = " ".join([
    '<img src=x onerror="window.__xss_fired=(window.__xss_fired||0)+1">',
    "<script>window.__xss_fired=(window.__xss_fired||0)+1</script>",
    '<svg onload="window.__xss_fired=(window.__xss_fired||0)+1"></svg>',
    '"><svg onload="window.__xss_fired=(window.__xss_fired||0)+1">',
    "</script><script>window.__xss_fired=(window.__xss_fired||0)+1</script>",
    "[md-link](javascript:window.__xss_fired=1)",
    '<a href="javascript:window.__xss_fired=1">click</a>',
])


def _seed_xss_lens() -> None:
    """Seed a poisoned taste lens — its poles flow into `pageData.tasteLenses`,
    baked into the inline `<script type="application/json" id="page-data">`. A
    pole containing `</script><script>…` would break out of the inline JSON unless
    `page_data_script_json` escapes `<` to `<` (the second launchpad-specific
    sink, separate from the raw-HTML rail). The lens card renders the poles via
    petite-vue `{{ }}` (auto-escaped) on the home view AND ships in pageData on
    both views, so this exercises the breakout sink regardless of view class.
    """
    import json

    from trinity_local.me.pair_mining import lenses_path

    path = lenses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The reader (`_load_pairs`) expects a dict with a "lenses" list, not a bare
    # array — guard the shape so the seed actually loads.
    path.write_text(json.dumps({"lenses": [{
        "pole_a": "POISONED-LENS speed " + _PAYLOADS,
        "pole_b": "rigor " + _PAYLOADS,
        "failure_a": "thrash", "failure_b": "paralysis",
        "tension_decisions": [], "dual_evidence": {},
        "basins_spanned": [], "verdict": "accepted", "horizon": "tactical",
    }]}), encoding="utf-8")


def _seed_xss_council() -> None:
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    _seed_xss_lens()

    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                            output_text="Claude reframes. " + _PAYLOADS),
        # Poison the MODEL string too — it flows into the rail winner / page-data.
        CouncilMemberResult(provider="codex", model="gpt-5.5 " + _PAYLOADS,
                            output_text="Codex enumerates. " + _PAYLOADS),
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude", runner_up="codex", confidence="high", task_type="design",
        agreed_claims=["cache key " + _PAYLOADS],
        disagreed_claims=[{
            "claim": "per-call vs in-process " + _PAYLOADS,
            "providers_for": ["claude"], "providers_against": ["codex"],
            "why_matters": "tenancy leak " + _PAYLOADS,
        }],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id="council_lp_xss", bundle_id="council_lp_xss",
            task_cluster_id="cluster_xss", primary_provider="claude",
            winner_provider="claude",
            # The rail card title = this corpus task_text. The poison rides here.
            metadata={
                "task_text": "POISONED QUESTION " + _PAYLOADS,
                "chain_root_id": "council_lp_xss",
            },
            member_results=members, synthesis_prompt="Review.",
            synthesis_output="# Synthesis\n\n" + _PAYLOADS,
            routing_label=routing_label,
            created_at="2026-06-07T00:00:00+00:00",
        )
    )
    write_portal_html()  # renders launchpad.html + stats.html + vendor assets


def _serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.parametrize("page_name", ["launchpad.html", "stats.html"])
def test_launchpad_neutralizes_corpus_xss_in_real_browser(tmp_path, monkeypatch, page_name):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_xss_council()

    httpd, port = _serve(tmp_path)
    dialogs: list[str] = []
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()

                def _on_dialog(d) -> None:
                    dialogs.append(d.message)
                    d.dismiss()

                def _on_pageerror(e) -> None:
                    errors.append(str(e))

                page.on("dialog", _on_dialog)
                page.on("pageerror", _on_pageerror)
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/{page_name}",
                    wait_until="load", timeout=15000,
                )
                page.wait_for_timeout(2000)  # petite-vue mount + any deferred fire
                fired = int(page.evaluate("() => window.__xss_fired || 0") or 0)
                # The rail title must render the payload as INERT TEXT — innerText
                # of a live <img>/<svg> would be empty; a real escaped title keeps
                # the literal "<img ...>" as text content.
                rail_text = str(page.evaluate(
                    "() => (document.querySelector('.rail-council-title') || {}).innerText || ''"
                ) or "")
                # No LIVE element from the payload may exist inside the rail link.
                live_onerror = int(page.evaluate(
                    "() => [...document.querySelectorAll('.rail-council img')]"
                    ".filter(i => i.getAttribute('onerror')).length"
                ) or 0)
                raw_embeds = int(page.evaluate(
                    "() => document.querySelectorAll('.rail-council script, .rail-council svg').length"
                ) or 0)
                rail_count = int(page.evaluate(
                    "() => document.querySelectorAll('.rail-council').length"
                ) or 0)
                # Page-data breakout sink: the inline JSON <script id=page-data>
                # must still hold the poisoned LENS pole as escaped JSON text
                # (a </script> breakout would split the element, dropping the
                # token from #page-data and spilling a live <script> into <body>).
                page_data_has_lens = bool(page.evaluate(
                    "() => { const el = document.getElementById('page-data');"
                    " return !!el && el.textContent.includes('POISONED-LENS'); }"
                ))
                stray_scripts = int(page.evaluate(
                    "() => [...document.scripts].filter(s => !s.src &&"
                    " s.id !== 'page-data' && /__xss_fired/.test(s.textContent || '')).length"
                ) or 0)
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert fired == 0, (
        f"a corpus XSS payload EXECUTED on the launchpad ({page_name}, "
        f"window.__xss_fired={fired}) — a malicious prompt/captured page whose "
        "task_text/winner/member-output carries <img onerror=>/<script>/"
        "</script> breakout would run script on EVERY launchpad open"
    )
    assert not dialogs, f"a payload triggered a dialog on {page_name}: {dialogs}"
    assert not errors, f"a payload caused a pageerror on {page_name}: {errors}"
    assert live_onerror == 0, (
        f"an <img onerror=> survived as a LIVE element in the recent-councils rail "
        f"({page_name}) — the rail title escape (_esc) was bypassed"
    )
    assert raw_embeds == 0, (
        f"a raw <script>/<svg> from the corpus survived into the rail ({page_name})"
    )
    assert stray_scripts == 0, (
        f"a corpus </script> breakout spilled a LIVE <script> into the DOM on "
        f"{page_name} — page_data_script_json failed to escape '<' in the inline "
        "page-data JSON (a lens pole / cold-open carrying </script><script> would "
        "break out of the <script type=application/json> element)"
    )
    # False-pass guard for the breakout sink: the poisoned lens pole MUST reach the
    # inline page-data JSON (else the breakout assertions are vacuous), and it must
    # survive there intact (= it was escaped, not split out by a </script>).
    assert page_data_has_lens, (
        f"the poisoned lens pole did not reach #page-data on {page_name} (token "
        "missing) — either the lens didn't load (breakout assertions vacuous) or a "
        "</script> breakout split the inline JSON and dropped it"
    )
    # False-pass guard: the poisoned council must actually render in the rail (else
    # the XSS assertions are vacuously true on an empty page), and its title must
    # carry the ESCAPED payload as literal text (proving the <img> is inert text,
    # not a stripped/executed element).
    assert rail_count >= 1, (
        f"the seeded council did not render in the rail on {page_name} "
        f"(rail_count={rail_count}) — the XSS assertions would be a false pass"
    )
    assert "POISONED QUESTION" in rail_text and "<img" in rail_text, (
        f"the rail title did not render the payload as inert escaped text on "
        f"{page_name} (rail_text={rail_text[:80]!r}) — XSS assertions could be a false pass"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
