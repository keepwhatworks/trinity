"""Real-browser guard: a LOCAL model council member (Ollama/MLX — e.g. qwen35,
gemma4local, deepseek-r1) must render HONESTLY on the live council page — its own
slug-derived label, NEVER a fabricated frontier brand (Claude / GPT / Gemini).

Why this matters now: the founder already runs councils with local members
(gemma4local / qwen appear in the real routing data), and the free-tier strategy
makes a real free local member first-class (the "$0 local survived synthesis"
honesty claim is the Phase-1 spine). The live page brands members via
`formatProviderLabel` over `normalizeProviderSlug`. The brand map only maps
claude/antigravity/codex/mlx/openai; everything else (local slugs) must fall
through to a title-cased slug. The realistic regression is the gemma-vs-gemini
SUBSTRING trap: if `normalizeProviderSlug` ever became fuzzy/substring instead of
its current EXACT-match alias map, "gemma4local" could collapse to
gemini→antigravity→"Gemini" — presenting a fabricated frontier brand for a model
that never ran on a frontier vendor. That is exactly the falsifiable-claim class
the strategy red-team flagged as conversation-ending for the enterprise buyer.

Mirrors the live-council XSS / painkiller browser tests (seed via
save_council_outcome → write_live_council_page → serve over HTTP → ?council_id=).
Slow + browser marked. Synthetic data only; no PII.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_local_member_browsertest"
# The frontier brands a local model must NEVER be labeled as.
_FRONTIER = {"claude", "gpt", "gemini"}


def _seed_local_member_council() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        # claude is the anchor: proves the brand map still works (claude → Claude).
        CouncilMemberResult(provider="claude", model="claude-opus-4-8", output_text="Claude answer."),
        # qwen35: a local slug with no frontier substring — must stay honest.
        CouncilMemberResult(provider="qwen35", model="qwen3.6:35b-a3b", output_text="qwen local answer."),
        # gemma4local: the substring trap (gemma vs gemini) — must NOT become Gemini.
        CouncilMemberResult(provider="gemma4local", model="gemma3:27b", output_text="gemma local answer."),
    ]
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID, bundle_id=_CID, task_cluster_id="cluster_local",
            primary_provider="claude", winner_provider="claude",
            metadata={"task_text": "Did the local model survive synthesis?"},
            member_results=members,
            synthesis_prompt="Review.",
            synthesis_output="In-process answer.",
            routing_label=CouncilRoutingLabel(
                winner="claude", runner_up="qwen35", confidence="high", task_type="design",
                agreed_claims=["all agree"], disagreed_claims=[],
            ),
            created_at="2026-06-07T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


def _serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_live_council_renders_local_member_without_fabricated_brand(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_local_member_council()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}",
                    wait_until="load", timeout=15000,
                )
                page.wait_for_timeout(2200)  # outcome-script hydration + petite-vue mount
                # Member-row display names (the formatProviderLabel output).
                names = page.evaluate(
                    "() => [...document.querySelectorAll('.provider-status-name')]"
                    ".map(e => (e.textContent || '').trim())"
                )
                # Also exercise formatProviderLabel directly if it's reachable, as a
                # second, sharper probe (degrades gracefully if it's IIFE-scoped).
                fn_probe = page.evaluate(
                    "() => { try { return {"
                    "  qwen: formatProviderLabel('qwen35'),"
                    "  gemma: formatProviderLabel('gemma4local'),"
                    "  deepseek: formatProviderLabel('ollama:deepseek-r1'),"
                    "  mlx: formatProviderLabel('mlx'),"
                    "  codex: formatProviderLabel('codex') }; }"
                    " catch (e) { return null; } }"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert names, "no member rows rendered — the local-member check would be vacuous"
    lowered = [n.lower() for n in names]
    # The anchor: claude renders as its brand (proves the map runs at all).
    assert any("claude" in n for n in lowered), f"claude member missing — rows: {names}"
    # The local members must appear by their own identity, never as a frontier brand.
    assert any("qwen" in n for n in lowered), f"qwen35 local member missing from rows: {names}"
    assert any("gemma" in n for n in lowered), (
        f"gemma4local local member missing from rows: {names} — if it became 'Gemini' "
        "the gemma-vs-gemini substring trap regressed"
    )
    # No member row may read as a bare frontier brand for a LOCAL provider.
    for n, low in zip(names, lowered):
        if "qwen" in low or "gemma" in low or "deepseek" in low:
            assert low not in _FRONTIER, (
                f"a local member rendered as the frontier brand {n!r} — fabricated"
            )

    if fn_probe is not None:
        # Sharper: formatProviderLabel must not map a local slug to a frontier brand.
        assert fn_probe["qwen"].lower() not in _FRONTIER, fn_probe
        assert fn_probe["gemma"].lower() not in _FRONTIER, (
            f"formatProviderLabel('gemma4local') = {fn_probe['gemma']!r} — the "
            "gemma-vs-gemini substring trap; normalizeProviderSlug must stay EXACT-match"
        )
        assert fn_probe["deepseek"].lower() not in _FRONTIER, fn_probe
        assert fn_probe["mlx"] == "MLX", f"mlx must label as its runtime 'MLX': {fn_probe}"
        # Anchor: the real brand map still works (regression guard for #275).
        assert fn_probe["codex"] == "GPT", f"codex must still brand as GPT: {fn_probe}"


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
