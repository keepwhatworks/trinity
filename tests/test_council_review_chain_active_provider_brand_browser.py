"""Browser guard: the static council review page's CHAIN poller must paint the
model BRAND in its "<member> is responding…" status detail — never the raw
provider slug (#275 class).

THE BUG (found 2026-06-19, UX sweep Iter 137, driving the real chain poller):
``render_unified_council_page`` (CouncilApp — the persistent, shareable review
page a teammate opens) paints the live chain-action status detail in JS from the
polled status file. That file's ``active_provider`` is the raw CLI/capture slug
(``antigravity`` / ``codex`` / ``gemini``). Line ~794 interpolated it RAW:

    this.chainStatusDetail = `${status.active_provider} is responding…`;

so a running chain round rendered "antigravity is responding…" / "codex is
responding…" — the raw harness/capture slug, NOT the model brand (Gemini / GPT)
every OTHER council surface shows. The sibling LiveCouncilApp poller already
brands the identical render via ``formatProviderLabel(active)``; CouncilApp was
the ONE council page with no JS brander in scope (its static winner/members are
branded SERVER-side via provider_model_brand, but the client-side poller had no
JS helper). This is the exact #275 raw-slug-vs-brand straggler — the same class
as the council winners/legend, the routing table, the eval leaderboard, and the
Iter-136 browser-capture rows.

THE FIX: a local ``formatProviderLabel`` (+ its ``normalizeProviderSlug`` web-slug
fold) defined in the CouncilApp script scope (the launchpad + LiveCouncilApp pages
each define their own in-scope copy; a shared-runtime hoist would redeclare),
called at the render site. ``antigravity``/``gemini`` → Gemini, ``codex``/
``chatgpt`` → GPT, ``claude``/``claude_ai`` → Claude.

This guard DRIVES the real surface: serves the rendered page over http (the page
reads the status sidecar via a script tag the file:// protocol can't load with a
cache-buster, so http is required), stubs ``window.__TRINITY_DISPATCH__`` to
succeed, serves a RUNNING status sidecar whose ``active_provider`` is the raw
``antigravity`` slug, clicks Continue, waits for a poll tick, and reads the
RENDERED ``chainStatusDetail`` text. Asserts it brands to "Gemini is responding…"
and that no raw slug leaks. Mutation-proven to bite on the un-fixed render line.

Slow-marked (portal render + chromium); skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_chainbrandguard"


def _render_static_review_html() -> str:
    from trinity_local.council_review import render_unified_council_page
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
        PromptBundle,
    )

    bundle = PromptBundle(
        bundle_id="bundle_chainbrand",
        task_cluster_id="cluster_chainbrand",
        task_text="Cache the embedder in-process or per-call?",
        goal="Choose the strongest answer.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-06-01T12:00:00+00:00",
    )
    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8", output_text="In-process."),
        CouncilMemberResult(provider="antigravity", model="gemini-3.1-pro", output_text="Per-call."),
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude", runner_up="antigravity", confidence="high", task_type="design",
        agreed_claims=["Cache it"], disagreed_claims=[],
    )
    outcome = CouncilOutcome(
        council_run_id=_CID,
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider="claude",
        winner_provider="claude",
        member_results=members,
        synthesis_output="# Synthesis\n\nIn-process wins.",
        routing_label=routing_label,
        created_at="2026-06-01T12:05:00+00:00",
    )
    return render_unified_council_page(bundle, outcome)


# A status handler: any council_status_*.js → a RUNNING status whose
# active_provider is the raw `antigravity` CLI slug (what start_member_progress
# writes — council_status.py line ~276). The straggler painted this raw.
def _make_handler(root: str):
    class _Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if "council_status_" in path and path.endswith(".js"):
                token = path.split("council_status_")[1][: -len(".js")]
                payload = {
                    "status": "running",
                    "members": {"antigravity": {"status": "running"}},
                    "active_provider": "antigravity",
                    "active_providers": ["antigravity"],
                    "synthesis": {"status": "pending"},
                }
                body = (
                    "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
                    f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(payload)};\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, *a):  # silence
            pass

    return functools.partial(_Handler, directory=root)


_CLICK_CONTINUE = (
    "() => [...document.querySelectorAll('button')]"
    ".find(x=>/Continue \\(one round\\)/.test(x.textContent)).click()"
)
_DETAIL_TEXT = (
    "() => { const e = document.querySelector('.chain-loading .meta');"
    " return e ? e.innerText.trim() : ''; }"
)


def test_chain_active_provider_renders_brand_not_raw_slug(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local import vendor

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    review_dir = tmp_path / "review_pages"
    review_dir.mkdir()
    portal_dir = tmp_path / "portal_pages"
    portal_dir.mkdir()
    (portal_dir / "status").mkdir()
    # The page references ../portal_pages/vendor/* — publish there so petite-vue
    # mounts and the {{ }} braces resolve.
    vendor.publish_vendor_files(portal_dir)
    (review_dir / "council.html").write_text(_render_static_review_html(), encoding="utf-8")

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _make_handler(str(tmp_path)))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/review_pages/council.html"

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1200}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                # Stub a SUCCEEDING dispatcher so _startChainAction → _pollChainStatus
                # fires and the running status detail is painted.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = { dispatch: function(o){"
                    " setTimeout(function(){ o.onResult({ok:true}); }, 40); } };"
                )
                page.goto(url)
                page.wait_for_timeout(700)
                page.evaluate(_CLICK_CONTINUE)
                # The chain poller ticks every 1500ms; wait for the detail to carry
                # "responding" (the running branch fired and painted active_provider).
                page.wait_for_function(
                    "() => { const e = document.querySelector('.chain-loading .meta');"
                    " return e && /responding/.test(e.innerText); }",
                    timeout=8000,
                )
                detail = page.evaluate(_DETAIL_TEXT)

                assert not errs, f"JS page errors during chain poll: {errs[:3]}"
                assert "responding" in detail, (
                    f"chain status detail never painted the active-member line: {detail!r}"
                )
                # THE GUARD: brand, not slug. An `antigravity` active_provider must
                # render as "Gemini is responding…" — never "antigravity is responding…"
                # (the #275 raw-slug straggler this review page leaked).
                assert "Gemini is responding" in detail, (
                    "the chain poller painted the RAW provider slug instead of the model "
                    f"brand — '{detail}'. An `antigravity` active_provider must read "
                    "'Gemini is responding…' (the #275 raw-slug-vs-brand class; the "
                    "static review page CouncilApp was the one council page without a "
                    "JS brander in scope for its chain poller)."
                )
                lowered = detail.lower()
                for raw in ("antigravity", "codex", "chatgpt", "claude_ai"):
                    assert raw not in lowered, (
                        f"chain status detail leaked a raw provider slug '{raw}': {detail!r}"
                    )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
