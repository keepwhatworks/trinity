from __future__ import annotations

import os
from pathlib import Path

from .council_review import write_live_council_page
from .launchpad_data import (
    _load_recent_councils,
    build_page_data,
    build_recent_sidebar_html,
)
from .launchpad_template import render_launchpad_html as _render_template
from .memory_viewer import write_memory_viewer
from .state_paths import portal_pages_dir

__all__ = [
    "build_launchpad_payload",
    "render_launchpad_html",
    "render_stats_html",
    "write_portal_html",
]


def _assemble_page_data(*, force_live_page: bool) -> tuple[dict, str]:
    """Build the launchpad `page_data` dict + recent-council sidebar HTML — the
    single data-gathering step shared by the HTML renderer (which bakes the data
    into launchpad.html) and `build_launchpad_payload` (which hands the SAME data
    to the in-extension launchpad over Native Messaging). Reads ~/.trinity only;
    no heavy deps.

    `force_live_page` controls whether the live-council page is REWRITTEN: the
    canonical `portal-html` renderer forces it (overwrite-with-current-source);
    a data-only read leaves it (force=False) so a per-poll fetch can't clobber a
    fresh page a previous portal-html wrote.
    """
    live_review_path = write_live_council_page(force=force_live_page).resolve()
    # Full history for the council rail (founder 2026-06-01: "why limit it to 8").
    # The sidebar is the single home for every council now; ~all councils with a
    # review page surface, scrollable. The page_data side still slices what it
    # needs (recentCouncilsCount); the rail wants the whole list.
    recent_councils = _load_recent_councils(limit=500)
    page_data = build_page_data(
        live_review_path=live_review_path,
        recent_councils=recent_councils,
    )
    recent_sidebar = build_recent_sidebar_html(recent_councils)
    return page_data, recent_sidebar


def render_launchpad_html(
    *, title: str = "Trinity · Ask all three", view: str = "home"
) -> str:
    # CLI's portal-html is the canonical place to refresh the live page;
    # force=True overwrites whatever's on disk with the current source.
    page_data, recent_sidebar = _assemble_page_data(force_live_page=True)
    return _render_template(
        page_data=page_data,
        recent_sidebar=recent_sidebar,
        title=title,
        view=view,
    )


def render_stats_html(*, title: str = "Trinity · stats") -> str:
    """Render the dedicated /stats page — the SAME launchpad template with
    `view="stats"`, which flips the body view class so the simple-home cards
    (the council + lens upsell) hide and every analytics/diagnostics card shows.
    The HTML string is identical to launchpad.html except for the view class; the
    split is pure CSS visibility, so no section is physically cut.
    """
    return render_launchpad_html(title=title, view="stats")


def build_launchpad_payload(*, title: str = "Trinity · Ask all three") -> dict:
    """Return the launchpad page-data the in-extension launchpad fetches over
    Native Messaging (capture_host `query_kind='launchpad_data'`) instead of
    reading a generated `launchpad.html`. Same data the HTML renderer bakes in,
    so the served file:// page and the in-extension page can't diverge.

    The returned dict is JSON-serialized straight to the extension, so it must
    stay well under Chrome's 1 MB host→extension Native-Messaging cap. Measured
    ~285 KB on a 313-council corpus (3.6x headroom); `tests/test_capture_host`
    pins a 900 KB budget so a future field-bloat can't silently breach the cap.
    """
    page_data, recent_sidebar = _assemble_page_data(force_live_page=False)
    return {"pageData": page_data, "recentSidebarHtml": recent_sidebar, "title": title}


# ── Launchpad-data cache (the side-panel open-latency fix) ───────────────────
# Assembling the payload runs the FULL launchpad analytics — timeline/chapters,
# prompt-node iteration over the whole corpus, the routing table — which on a
# large corpus (1.5k+ councils) costs ~6.5s. The side-panel shell holds its
# "Loading Trinity…" spinner until the iframe mounts, and the iframe can't mount
# until this payload arrives, so EVERY open paid that 6.5s (founder-caught
# 2026-06-17). The data only changes when a council runs / the lens rebuilds, so
# serve a cached `launchpad_data.json` and rebuild it ONLY when the source state
# is newer than the cache (mtime-gated) — turning a repeated open into a file read.
_LAUNCHPAD_CACHE_NAME = "launchpad_data.json"

# Dirs whose mtime means the payload could have changed. council_outcomes (the
# rail + routing), the cognitive memories + lens-build output (the lens card),
# scoreboards (cheat-sheet), the prompt index (timeline), evals (leaderboard),
# conversations (the browserCapture count + the >24h "captures are stale" badge).
#
# `conversations/` was the cache's BLIND SPOT (founder-class STALE-AFTER-CHANGE):
# a fresh Chrome-extension capture lands in `conversations/<provider>/<file>`, but
# that dir wasn't watched at all — so reopening the side panel served a cached
# payload that still showed the OLD count and a STILL-stale badge, making the
# fresh capture invisible and the "reconnect, captures are stale" warning read as
# a false alarm even right after the user re-synced. evals/ had the SIBLING shape:
# `eval-run` writes `evals/results/<file>`, bumping the SUBDIR mtime, not `evals/`
# — so a new score didn't bust the cache either. Both fixed by walking one level
# of CHILD-dir mtimes below (see _launchpad_source_mtime).
#
# `memories/` had the ONE-RUNG-LOWER shape: a `lens` / `vocabulary` /
# `dream --only-distill` run rewrites memories/lens.md · vocabulary.md · core's
# upstream IN PLACE (`path.write_text`, not an atomic rename), which bumps the
# FILE's mtime but NOT the dir's mtime — so a dir-only gate served the pre-rebuild
# payload (a fresh-vocab verdict over an already-rebuilt lens). _launchpad_source_mtime
# now also stats the top-level FILES of each watched dir (see its docstring).
_LAUNCHPAD_SOURCE_DIRS = (
    "council_outcomes", "memories", "me", "scoreboard", "prompts", "evals",
    "conversations",
)


def _launchpad_cache_path() -> Path:
    return portal_pages_dir() / _LAUNCHPAD_CACHE_NAME


def _launchpad_source_mtime() -> float:
    """Newest mtime across the dirs that feed the payload, INCLUDING one level of
    immediate child dirs AND the immediate top-level FILES of each watched dir. A
    new council bumps council_outcomes/; a lens rebuild bumps memories/lens.md —
    either invalidates the cache. Missing dirs are skipped (a cold install has none).

    Two write shapes have to bust this cache, and a DIRECTORY-only mtime catches
    only the first:

      1. New / atomically-renamed file (council_outcomes/<id>.json via tmp+rename,
         a capture into conversations/<provider>/, a score into evals/results/).
         A new dir ENTRY bumps the containing dir's mtime → the dir stat below
         catches it (incl. one scandir level deep, since a file added to a SUBDIR
         bumps the SUBDIR, not the parent — the founder browserCapture-staleness
         symptom that the child-dir scan fixed).

      2. IN-PLACE rewrite of an EXISTING file, same name (`path.write_text` over
         memories/lens.md · vocabulary.md · generators.md and the flat me/*.json
         the lens-build pipeline rewrites — NOT atomic, so no rename). This does
         NOT change the parent dir's mtime on APFS/ext4 (the dir ENTRY is
         unchanged), only the FILE's own mtime. A dir-only gate served the
         PRE-rewrite payload forever: `trinity-local vocabulary` /
         `dream --only-distill` / a `lens` run whose topics.json sibling didn't
         happen to atomic-rename left the memory-health card showing a fresh-vocab
         verdict while the lens.md it sits under had already been rebuilt past it
         (STALE-AFTER-CHANGE — same class as the conversations/ subdir blind spot,
         one rung lower: file-content vs dir-entry granularity).

    So we max over BOTH dir mtimes (one level deep) AND the top-level FILES of each
    watched dir. memories/ + me/ hold a handful of flat files (cheap to stat);
    conversations/ + evals/ have NO top-level files (only provider/results subdirs,
    where writes are atomic and already caught by the dir scan), so this adds no
    per-file cost on the large dirs. Still mtime stats only — no content reads — so
    the side-panel open stays fast (the whole reason the cache exists)."""
    from .state_paths import trinity_home

    home = trinity_home()
    newest = 0.0
    for name in _LAUNCHPAD_SOURCE_DIRS:
        d = home / name
        try:
            newest = max(newest, d.stat().st_mtime)
        except OSError:
            continue
        # One scandir level deep: a new capture in conversations/<provider>/ or a
        # new result in evals/results/ bumps the CHILD dir, not `d` itself; an
        # in-place rewrite of a top-level FILE (memories/lens.md) bumps the FILE,
        # not `d` — stat both.
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir():
                            newest = max(newest, entry.stat().st_mtime)
                        elif entry.is_file():
                            newest = max(newest, entry.stat().st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
    return newest


def build_launchpad_payload_cached(*, title: str = "Trinity · Ask all three") -> dict:
    """`build_launchpad_payload` with an mtime-gated disk cache. Returns the cached
    payload when no source dir has changed since it was written (a file read,
    ~milliseconds); otherwise rebuilds live and refreshes the cache. Any cache I/O
    failure falls through to a live build — the cache is an optimization, never a
    correctness dependency."""
    import json

    cache = _launchpad_cache_path()
    src_mtime = _launchpad_source_mtime()
    try:
        if cache.exists() and cache.stat().st_mtime >= src_mtime:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass  # unreadable/corrupt cache → rebuild
    data = build_launchpad_payload(title=title)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, default=str), encoding="utf-8")
    except OSError:
        pass  # read-only FS etc. → just serve the freshly built data
    return data


def write_portal_html(*, title: str = "Trinity · Ask all three") -> Path:
    pages_dir = portal_pages_dir()
    # Refresh the frozen routing scoreboard from council_outcomes/ BEFORE rendering,
    # so the memory viewer (which reads scoreboard/routing.json from disk) shows the
    # SAME task_types the launchpad cheat-sheet computes live. Without this the two
    # diverge: a fresh cheat-sheet row links to
    # `memory.html?file=routing.json&task=<task>`, but the stale frozen file lacks
    # that task, so the deep-link lands on a "not yet — rate a council" banner for a
    # task the user has ALREADY rated (verified on the real corpus: 1/38 cross-links
    # missed this way). compute_personal_routing_table() is cached on the
    # outcomes-dir mtime, so this reuses the same walk render does — no extra cost.
    # Best-effort: a freeze failure must never break the render (the cheat-sheet is
    # still correct; only the cross-link target would lag).
    try:
        from .personal_routing import freeze_routing_to_disk

        freeze_routing_to_disk()
    except Exception:
        pass
    # Publish the vendored JS (petite-vue, chart.js, d3 modules) the HTML below
    # references as `./vendor/*` BEFORE writing the page — so the launchpad never
    # 404s its own scripts. Without this, `window.__TRINITY_VUE__` is undefined
    # and the whole Vue app fails to mount (search, dispatch, charts all dead).
    # This is the documented contract ("copied on each portal-html generation",
    # vendor.py) — historically only `refresh_launchpad` published, leaving this
    # public, exported writer producing a broken page for any direct caller.
    # Idempotent (skips when content matches), so refresh_launchpad's path is
    # unaffected.
    from .vendor import publish_vendor_files

    publish_vendor_files(pages_dir)
    path = pages_dir / "launchpad.html"
    path.write_text(render_launchpad_html(title=title), encoding="utf-8")
    # Also publish the dedicated /stats page (the openrouter.ai/fusion-style
    # split: launchpad.html = the simple cross-provider council + lens upsell;
    # stats.html = all analytics/diagnostics). Same template, same vendor files
    # (published once above), only the body view class differs — the split is
    # pure CSS visibility. The launchpad.html Path stays the return value (the
    # canonical home), so every existing caller is unaffected.
    stats_path = pages_dir / "stats.html"
    stats_path.write_text(render_stats_html(title="Trinity · stats"), encoding="utf-8")
    # The memory viewer is a static page rendered from a hardcoded
    # allowlist — no per-render data, but write it here so a fresh
    # install / portal-html run produces both pages.
    write_memory_viewer()
    return path
