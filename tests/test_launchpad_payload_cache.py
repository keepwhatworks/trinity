"""The side-panel launchpad_data query must NOT re-run the full analytics build
on every open.

Founder-caught 2026-06-17: opening the Chrome side panel sat on "Loading Trinity…"
for ~6.5s EVERY time. Root cause — the shell holds its spinner until the iframe
mounts, the iframe can't mount until `launchpad_data` arrives, and that query ran
`build_launchpad_payload()`, which assembles the FULL launchpad analytics (timeline
/chapters, prompt-node iteration over the whole corpus, the routing table). On the
founder's 1.5k-council corpus that's ~6.5s — paid on EVERY open, even though the
data only changes when a council runs / the lens rebuilds.

The CLASS: a hot read-path recomputing expensive, rarely-changing derived state on
every call. The root-cause fix is an mtime-gated disk cache
(`build_launchpad_payload_cached`): serve the cached payload when no source dir is
newer than the cache; rebuild only on a real change.

This pins both halves, mutation-provably:
  • a repeated open with no source change does NOT rebuild (calls stay at 1), and
  • a council running (a source-dir mtime bump) DOES invalidate → one rebuild.
Remove the cache → the first assertion reds (rebuilds every call). Remove the mtime
gate → the second reds (never invalidates → serves stale forever).
"""
from __future__ import annotations

import os

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_repeated_open_serves_cache_and_a_council_invalidates(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    outcomes = tmp_path / "home" / "council_outcomes"
    outcomes.mkdir(parents=True)

    from trinity_local import launchpad_page

    calls: list[int] = []

    def _fake_build(**_kw):
        calls.append(1)
        return {"pageData": {"k": len(calls)}, "recentSidebarHtml": "", "title": "t"}

    # Replace the EXPENSIVE builder; the cached wrapper resolves it from module
    # globals at call time, so this measures exactly how often it rebuilds.
    monkeypatch.setattr(launchpad_page, "build_launchpad_payload", _fake_build)

    a = launchpad_page.build_launchpad_payload_cached()  # cold → builds + writes cache
    b = launchpad_page.build_launchpad_payload_cached()  # warm → cache read
    c = launchpad_page.build_launchpad_payload_cached()  # warm → cache read
    assert calls == [1], (
        f"the launchpad payload REBUILT on a repeated open (calls={calls}) — that's the "
        "~6.5s side-panel latency bug; the cache isn't being served"
    )
    assert a == b == c, "cache returned different data than the build"
    assert launchpad_page._launchpad_cache_path().exists(), "no cache file was written"

    # A council running bumps council_outcomes/ mtime → the cache must invalidate.
    cache_mtime = launchpad_page._launchpad_cache_path().stat().st_mtime
    future = cache_mtime + 100
    os.utime(outcomes, (future, future))

    d = launchpad_page.build_launchpad_payload_cached()  # stale → rebuilds
    assert calls == [1, 1], (
        f"the cache did NOT invalidate after a council ran (calls={calls}) — a stale "
        "panel would show outdated councils forever"
    )
    assert d["pageData"]["k"] == 2, "invalidation didn't return freshly-built data"


def test_a_fresh_capture_in_an_existing_provider_subdir_invalidates_the_cache(
    tmp_path, monkeypatch
):
    """STALE-AFTER-CHANGE (founder-class): a fresh Chrome-extension capture lands in
    `conversations/<provider>/<file>`, but the launchpad-payload cache watched
    neither `conversations/` NOR any source dir's CHILD dirs — so a new file in an
    EXISTING provider subdir bumped only the subdir mtime, never busting the cache.

    Reopening the side panel then served a cached `browserCapture` block showing the
    OLD count and a STILL-stale >24h badge, making the fresh capture invisible and
    the "reconnect — your captures are stale" warning read as a false alarm even
    right after the user re-synced.

    This drives the REAL cached builder (no fake — it must exercise
    `_browser_capture` reading `conversations/`): seed one >24h capture (cache shows
    count=1, stale=True), then drop a FRESH capture into the SAME existing provider
    subdir and assert the cached payload now reports count=2 + stale=False — i.e. the
    cache invalidated. Pre-fix (conversations/ unwatched AND no child-dir scan) the
    cached payload stays at count=1/stale=True and this reds.
    """
    import json
    import time

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    from trinity_local import launchpad_page
    from trinity_local.state_paths import conversations_dir

    # An EXISTING provider subdir with one capture backdated >24h so the badge is
    # genuinely stale and the count is genuinely 1 at build time.
    prov = conversations_dir() / "claude_ai"
    prov.mkdir(parents=True)
    old_file = prov / "conv_old.json"
    old_file.write_text(
        json.dumps({"messages": [{"role": "user", "text": "hi"}]}), encoding="utf-8"
    )
    old = time.time() - 200_000  # ~2.3 days ago → stale
    os.utime(old_file, (old, old))
    os.utime(prov, (old, old))
    os.utime(conversations_dir(), (old, old))

    p1 = launchpad_page.build_launchpad_payload_cached()
    bc1 = p1["pageData"]["browserCapture"]
    assert bc1["total_captured"] == 1, "fixture precondition: one capture at build time"
    assert bc1["stale"] is True, "fixture precondition: the lone capture is >24h → stale"

    # The REAL production event: the extension writes a fresh capture into the SAME
    # existing provider subdir. Bumps `conversations/<provider>/` mtime only.
    time.sleep(0.02)
    (prov / "conv_fresh.json").write_text(
        json.dumps({"messages": [{"role": "user", "text": "fresh"}]}), encoding="utf-8"
    )

    p2 = launchpad_page.build_launchpad_payload_cached()
    bc2 = p2["pageData"]["browserCapture"]
    assert bc2["total_captured"] == 2, (
        "STALE PANEL: the cached browserCapture count stayed "
        f"{bc2['total_captured']} after a fresh capture landed in an existing "
        "provider subdir — conversations/<provider>/ didn't bust the launchpad cache "
        "(the founder browserCapture-staleness blind spot)"
    )
    assert bc2["stale"] is False, (
        "STALE BADGE: the cached browserCapture still reads >24h-stale=True after a "
        "fresh capture — the 'reconnect, your captures are stale' warning persists "
        "even though the user just re-synced"
    )


def test_an_in_place_lens_rewrite_invalidates_the_cache(tmp_path, monkeypatch):
    """STALE-AFTER-CHANGE, one rung below the conversations/ subdir blind spot: a
    `lens` / `vocabulary` / `dream --only-distill` run rewrites memories/lens.md (or
    vocabulary.md) IN PLACE — `path.write_text` over the existing file, NOT an atomic
    rename. On APFS/ext4 that bumps the FILE's mtime but NOT the parent dir's mtime
    (the dir ENTRY is unchanged). The launchpad-payload cache used to stat only
    DIRECTORY mtimes (the watched dirs + one child-dir level), so an in-place file
    rewrite was invisible — the cache served the PRE-rebuild payload.

    Concretely: the memory-health card's vocab-freshness verdict went stale (lens.md
    now newer than vocabulary.md), but a reopened panel kept showing the fresh-vocab
    state — the user's `lens` rebuild had no visible effect, and the
    'your vocab is stale, run vocabulary' nudge never appeared.

    Drives the REAL cached builder (no fake — must exercise `_vocabulary_status`
    reading memories/), seeds fresh lens+vocab (no vocab issue at build time), then
    rewrites lens.md IN PLACE keeping its name and asserts the cached payload now
    surfaces the stale-vocabulary issue. Pre-fix (dir-mtime-only gate) the cached
    payload stays issue-free and this reds.
    """
    import time

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    from trinity_local import launchpad_page
    from trinity_local.state_paths import memories_dir, lens_path, vocabulary_path

    memories_dir().mkdir(parents=True, exist_ok=True)
    # lens first, vocab AFTER → vocab is the newest of the pair → fresh.
    lens_path().write_text("# Lens\n\nT1 vs T2\n", encoding="utf-8")
    time.sleep(0.02)
    vocabulary_path().write_text("# Vocabulary\n\nANCHOR_ONE\n", encoding="utf-8")

    def _vocab_issues(payload):
        mh = payload["pageData"].get("memoryHealth") or {}
        return [i for i in mh.get("issues", []) if i.get("name") == "vocabulary.md"]

    p1 = launchpad_page.build_launchpad_payload_cached()
    assert _vocab_issues(p1) == [], (
        "fixture precondition: vocab is newer than lens at build time → no stale-vocab issue"
    )

    # The REAL production event: `trinity-local lens` rewrites memories/lens.md IN
    # PLACE (me_builder uses path.write_text, not an atomic rename). Make it land a
    # full second later so the new file mtime is unambiguously newer than vocab.
    time.sleep(1.05)
    lens_path().write_text("# Lens\n\nT1 vs T2\n\nT3 vs T4 (rebuilt)\n", encoding="utf-8")

    # Ground truth: vocab IS now stale (lens.md newer). The cache must reflect it.
    from trinity_local.launchpad_data import _vocabulary_status
    assert _vocabulary_status().get("state") == "stale", (
        "test sanity: the in-place lens rewrite genuinely makes vocab stale"
    )

    p2 = launchpad_page.build_launchpad_payload_cached()
    issues = _vocab_issues(p2)
    assert issues and issues[0]["status"] == "stale", (
        "STALE-AFTER-CHANGE: the cached launchpad payload did NOT surface the "
        "stale-vocabulary issue after memories/lens.md was rewritten IN PLACE — an "
        "in-place file rewrite (the lens/vocabulary/distill write pattern) bumps the "
        "FILE mtime but not the dir mtime, so a dir-mtime-only cache gate served the "
        f"pre-rebuild payload (vocab issues seen: {issues})"
    )


def test_host_query_uses_the_cached_builder():
    """CI-runnable canary: the side-panel launchpad_data host query must route
    through the cached builder, not the uncached one (a revert would reintroduce
    the per-open 6.5s build)."""
    src = (REPO / "src" / "trinity_local" / "capture_host.py").read_text(encoding="utf-8")
    assert "build_launchpad_payload_cached" in src, (
        "_query_launchpad_data no longer uses the cached builder — every panel open "
        "would rebuild the full analytics payload again"
    )
