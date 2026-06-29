"""Static HTML viewer for the lens hierarchy (core.md + the three
thinking memories: lens.md, topics.json, vocabulary.md).

Writes a single page at ~/.trinity/portal_pages/memory.html that loads
the requested memory file by query param (?file=lens.md, picks.json...).
JSON is pretty-printed; .md is shown raw. No markdown rendering for now —
chairman context is the source of truth, this is for human inspection.

Memory contents are inlined into a `window.__TRINITY_MEMORIES__` global at
write time (same pattern as council thread manifests in live_council.html).
This makes the viewer work under file:// — no `fetch()`, no `trinity-local
serve` required — which matters because the launchpad opens via file://
from the macOS desktop shortcut, and the chips link straight here.

Linked from the launchpad. Generated alongside the launchpad on
`portal-html` and on every refresh.
"""
from __future__ import annotations

from pathlib import Path

from .design_system import FAVICON_LINK, FONT_FACE_CSS, _finite_json_safe
from .state_paths import (
    core_path,
    generators_path,
    lens_path,
    picks_path,
    portal_pages_dir,
    routing_path,
    topics_path,
    vocabulary_path,
)


# Allowlist matches what's in state_paths. Used by render time (to load
# file contents into the inlined JS payload) and by the client-side JS
# (to validate the ?file= param against a known set).
ALLOWED_FILES: list[dict[str, str]] = [
    # The four THINKING memories — what dream creates from your prompt
    # corpus, what the chairman reads as identity context on every
    # council. Ordered as the chairman reads them (top-down, drill-only-
    # when-needed): manifesto → tensions → basins → language.
    {"name": "core.md", "brain": "identity (distilled)",
     "tagline": "One-paragraph manifesto subsuming the three thinking memories. Chairman reads this FIRST on every council."},
    {"name": "lens.md", "brain": "value memory",
     "tagline": "Paired tensions you'd reject vs accept. Written by lens."},
    {"name": "topics.json", "brain": "semantic memory",
     "tagline": "Subject basins + evidence map for lens. Written by lens (Stage 1)."},
    {"name": "vocabulary.md", "brain": "linguistic memory",
     "tagline": "Anchors (proper nouns) + homonyms + synonyms. Written by vocabulary (also runs inside dream)."},
    # The OPTIONAL generators tier (the lens "lift") — the cross-domain invariants
    # the task tensions project from. On-demand (`lens-generators` verb); the
    # viewer shows this tab only when ~/.trinity/memories/generators.md exists.
    {"name": "generators.md", "brain": "generators (the lift)", "optional": "1",
     "tagline": "Cross-domain generating invariants — the reflex under your task tensions. Written by lens-generators."},
    # The two OPERATIONAL scoreboards — derived from your council
    # outcomes (the verdicts you log) and read by the chairman picker.
    # Not cognitive memory, not part of the lens distillation.
    {"name": "picks.json", "brain": "scoreboard (operational)",
     "tagline": "The recency-weighted chairman winner per lens basin. Written by consolidate; read by ask + chairman picker."},
    {"name": "routing.json", "brain": "scoreboard (operational)",
     "tagline": "Per-task-type provider track record. Computed from council outcomes; read by ask + launchpad."},
]


_FILE_PATH_RESOLVERS = {
    "lens.md": lens_path,
    "picks.json": picks_path,
    "routing.json": routing_path,
    "topics.json": topics_path,
    "vocabulary.md": vocabulary_path,
    "core.md": core_path,
    "generators.md": generators_path,
}


def _visible_files() -> list[dict[str, str]]:
    """ALLOWED_FILES minus OPTIONAL files (generators.md) that don't exist yet —
    so the advanced on-demand generators tier doesn't show an empty tab to users
    who never ran ``lens-generators``. The core memories + scoreboards always
    show; ``?file=`` validation still uses the full ALLOWED_FILES set."""
    out: list[dict[str, str]] = []
    for f in ALLOWED_FILES:
        if f.get("optional"):
            resolver = _FILE_PATH_RESOLVERS.get(f["name"])
            try:
                if not (resolver and resolver().exists()):
                    continue
            except Exception:
                continue
        out.append(f)
    return out


def _read_memory_contents() -> dict[str, str | None]:
    """Read each memory file at render time. Returns name → contents, with
    None for missing files (the viewer renders an empty-state for those).

    Rendering is client-side: `marked` handles markdown, the inline JSON
    viewer (in the embedded JS) handles JSON. Keeps it DRY — same JS
    libs Trinity already pulls (petite-vue, Chart.js); no parallel
    server-side renderer to maintain.
    """
    contents: dict[str, str | None] = {}
    for name, resolver in _FILE_PATH_RESOLVERS.items():
        path = resolver()
        try:
            contents[name] = path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            contents[name] = None
        except UnicodeDecodeError:
            # Non-UTF8 bytes (disk corruption, bad encoding). Show the readable
            # parts with U+FFFD replacements rather than crash the whole viewer
            # render OR hide the file behind a misleading "not built yet"
            # empty-state — the file IS there, just garbled.
            try:
                contents[name] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                contents[name] = None
    return contents


def _picks_margin_fmt_map(picks_text: str | None) -> dict[str, str]:
    """Pre-format every picks.json margin to 2dp with PYTHON rounding (the same
    `f"{m:.2f}"` the CLI `consolidate` line and the launchpad routing card use),
    keyed by the JS-canonical string of the raw value (``repr`` of a round-tripped
    double matches JS ``Number.prototype.toString``).

    The cross-language bug this closes: the viewer reads the raw picks.json float
    client-side, and JS ``Number.toFixed(2)`` rounds half-UP while Python's float
    format rounds half-to-EVEN — so an exact dyadic margin like 0.625 painted
    "0.63" in the viewer (and launchpad pre-fix) while the CLI printed "0.62"
    (same picks.json, two numbers across surfaces). Re-deriving Python's
    binary-correct rounding in JS arithmetic is not reliable, so we pre-format
    here and the viewer renders the string verbatim (see fmtMargin in the JS).
    Returns {} on any read/parse failure (the JS falls back to toFixed).
    """
    if not picks_text:
        return {}
    try:
        data = _json_module().loads(picks_text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        m = entry.get("margin")
        # bool is an int subclass, and a NaN/Inf margin (a poisoned/hand-edited
        # picks.json) is a float that passes isinstance but then `int(round(NaN,3))`
        # raises ValueError ("cannot convert float NaN to integer"), crashing
        # render_memory_viewer_html → write_portal_html. Skip non-finite/bool so a
        # corrupt margin can't take down the served portal (sibling of
        # _load_cortex_rules._safe_number on the launchpad path).
        if not isinstance(m, (int, float)) or isinstance(m, bool):
            continue
        if isinstance(m, float) and m != m or m in (float("inf"), float("-inf")):
            continue
        # Key by the SAME value the JS computes: round(value, 3) → toString.
        # repr() of a round-tripped double matches JS toString for fractional
        # decimals; normalize the integer-valued case (JS "0"/"1", not "0.0")
        # so an exactly-0.0 or 1.0 margin keys identically (those never hit the
        # tie anyway, but the map stays a faithful mirror).
        rv = round(float(m), 3)
        key = repr(int(rv)) if rv == int(rv) else repr(rv)
        out[key] = f"{float(m):.2f}"
    return out


def _json_module():
    import json as _json
    return _json


def _slim_topics_for_viewer(contents: dict[str, str | None]) -> dict[str, str | None]:
    """Trim the inlined topics.json payload to what the viewer actually renders.

    Measured 2026-06-02: topics.json is ~2.2MB and ~79% of memory.html, but a
    per-basin `prompt_ids` list (thousands of opaque ids — 2,626 in the largest
    basin, ~545KB / 20% of the file) is read client-side ONLY for its `.length`
    (the stale-topology check: prompt_ids count vs basin size). Shipping the full
    id arrays just to compute a length is pure weight. Replace each basin's
    `prompt_ids` with a `prompt_id_count` int; the client reads the count.

    Returns a NEW dict (does not mutate the input); the on-disk topics.json is
    untouched — only the viewer COPY is slimmed. Tolerant of a malformed/absent
    topics.json (returns it unchanged) so a bad memory can't break the render —
    same principle as the client's swallow-and-degrade JSON.parse.

    (The per-basin 768-d `centroid` — ~820KB — is the larger remaining payload,
    but the client uses its values for the basin↔pick cosine bridge
    (`matchBasinsToPicks`), and that bridge is precision-sensitive near the 0.36
    match threshold; removing it cleanly means precomputing the link maps
    server-side, a separate higher-risk change. Left intact here.)
    """
    import json as _json

    raw = contents.get("topics.json")
    if not raw:
        return contents
    try:
        obj = _json.loads(raw)
    except (ValueError, TypeError):
        return contents
    basins = obj.get("basins") if isinstance(obj, dict) else None
    if not isinstance(basins, list):
        return contents

    for basin in basins:
        if isinstance(basin, dict) and "prompt_ids" in basin:
            ids = basin.get("prompt_ids")
            basin["prompt_id_count"] = len(ids) if isinstance(ids, list) else 0
            del basin["prompt_ids"]

    slimmed = dict(contents)
    # ALWAYS re-serialize a successfully-parsed topics.json through
    # _finite_json_safe + allow_nan=False — NOT only when prompt_ids slimming
    # happened. A NaN/Inf centroid coordinate (a poisoned embedding) on a basin
    # with NO prompt_ids (e.g. a freshly-built basin) would otherwise survive the
    # early-return as a bare `NaN` INSIDE this topics.json string, and the
    # client's `JSON.parse(slimmed)` (the topology graph at __TRINITY_MEMORIES__
    # + the Raw-JSON view + the basin↔pick bridge) would throw on it and blank the
    # graph. Coerce to null at the source regardless of slimming.
    slimmed["topics.json"] = _json.dumps(
        _finite_json_safe(obj), ensure_ascii=True, allow_nan=False
    )
    return slimmed


def _render_nav_links(health: dict | None = None) -> str:
    """Server-rendered nav. Server-controlled values, escaped via html.escape.

    When `health` carries issues, each affected file's chip gets a
    small warning dot — the same signal the launchpad memory-health
    row + per-file banner already surface, hoisted up to the nav so
    the user sees which files need attention BEFORE clicking through.
    """
    import html as _html

    # Index issues by file name so each chip's lookup is O(1).
    stale_files: set[str] = set()
    if health and isinstance(health, dict):
        for issue in health.get("issues") or []:
            name = issue.get("name") if isinstance(issue, dict) else None
            if name:
                stale_files.add(name)

    parts = []
    for f in _visible_files():
        is_stale = f["name"] in stale_files
        stale_class = " memory-nav-link-stale" if is_stale else ""
        dot = '<span class="memory-nav-dot" aria-label="needs attention" title="needs attention"></span>' if is_stale else ""
        parts.append(
            f'<a class="memory-nav-link{stale_class}" '
            f'href="memory.html?file={_html.escape(f["name"])}" '
            f'data-file="{_html.escape(f["name"])}">'
            f'<span class="memory-name">{_html.escape(f["name"])}{dot}</span>'
            f'<span class="memory-brain">{_html.escape(f["brain"])}</span>'
            '</a>'
        )
    return "\n".join(parts)


def render_memory_viewer_html() -> str:
    """Return the viewer HTML with memory contents inlined.

    Reads each memory file at render time and emits its contents into
    `window.__TRINITY_MEMORIES__` so the page works under file:// (no
    fetch needed). Same pattern as live_council.html's thread manifests.
    """
    import json as _json

    def _inline(obj):
        # Embed JSON inside an inline <script>: escape `<` so a literal
        # "</script>" in user content (e.g. a memory file discussing HTML/JS)
        # cannot close the script tag and break the page's JavaScript.
        # ensure_ascii already escapes the U+2028/U+2029 line terminators.
        # _finite_json_safe + allow_nan=False coerce a non-finite float
        # (a NaN topics.json centroid from a poisoned embedding) to null so a
        # bare `NaN` can't make the client's JSON.parse throw and blank the
        # viewer — the same crash page_data_script_json closes on the launchpad.
        return _json.dumps(
            _finite_json_safe(obj), ensure_ascii=True, allow_nan=False
        ).replace("<", "\\u003c")

    files_json = _inline(_visible_files())
    # The full allowlist (incl. hidden-when-absent optional files) so the client can
    # tell a known-but-unbuilt file (generators.md) from a truly-unknown ?file= value.
    all_files_json = _inline(ALLOWED_FILES)
    # Slim the topics.json copy (drop the unrendered prompt_ids arrays → count)
    # before inlining — ~20% smaller memory.html with no visible change.
    _memory_contents = _read_memory_contents()
    memories_payload = _inline(_slim_topics_for_viewer(_memory_contents))
    # Pre-format picks margins server-side (Python 2dp) so the viewer renders the
    # SAME margin string as the CLI + launchpad — never a JS toFixed half-up
    # divergence on an exact dyadic tie. See _picks_margin_fmt_map / fmtMargin.
    margin_fmt_json = _inline(_picks_margin_fmt_map(_memory_contents.get("picks.json")))
    # The real routing gate, threaded into the JS so the picks badge + topology
    # basin detail use the SAME confidence threshold `ask` routes on (#299). Local
    # import + literal fallback mirrors launchpad_data.py (avoids a hard module dep
    # if lens_routing ever moves).
    try:
        from .lens_routing import WINNER_MARGIN_FLOOR as _wmf
        winner_margin_floor = float(_wmf)
    except Exception:
        winner_margin_floor = 0.15
    # Inline the same memory-health payload the launchpad surfaces so
    # the viewer can carry the staleness signals forward when the user
    # clicks through to inspect. Same shape as launchpad_data._memory_health()
    # — signals include core staleness, picks audit-disagreed,
    # pre-thread-aware topology, and picks cortex-stale.
    # Resolved at render time so the warning travels with the file, not
    # with the page that linked you to it.
    try:
        from .launchpad_data import _memory_health
        health_data = _memory_health()
        health_payload = _inline(health_data)
    except Exception:
        # Memory viewer must not crash when the launchpad data layer
        # has unrelated issues — degrade silently to no banners.
        health_data = None
        health_payload = "{}"
    # Lens TRUSTWORTHINESS (not staleness). The memory-health payload above only
    # carries *staleness* signals (core newer than lens, vocab behind, picks
    # un-consolidated). It says NOTHING about whether the lens was built on real
    # 768d semantic embeddings or the SHA-1 TF-IDF fallback — yet a TF-IDF lens
    # is, per lens_health's own DEGRADED verdict, a keyword caricature, not the
    # user's taste (CLAUDE.md: semantic flows ABSTAIN under the fallback rather
    # than ship inverted-TF-IDF garbage). Without this signal the viewer paints a
    # confident "Your lens · paired tensions you'd reject vs accept" over garbage
    # on every fresh install that lacks the [mlx] extras — the exact
    # green-while-degraded class (#35) on a NEW surface. Surface the embedding
    # backend so the cognitive files (lens/topics/core/generators) carry the same
    # honesty the CLI `lens-health` verb prints. Cheap (cached embed probe ~ms;
    # the mlx backend is None on the fallback path, so no real embed runs) and
    # read-only. Never let a probe blow up the whole viewer.
    lens_trust: dict[str, object] = {"embedder_degraded": False}
    try:
        from . import lens_health as _lh

        backend = _lh._embedding_backend()
        if backend.status == _lh.DEGRADED:
            lens_trust = {
                "embedder_degraded": True,
                "summary": backend.summary,
                "fix": backend.fix,
            }
    except Exception:
        # An embedder probe failure must not blank the viewer — degrade to "no
        # trust banner" (the staleness banners + content still render).
        lens_trust = {"embedder_degraded": False}
    lens_trust_payload = _inline(lens_trust)
    # (#298 cortex collapse) The picks→topology bridge is now an identity match
    # on the lens basin id (both picks.json and topics.json key by b00..), so the
    # centroid-similarity threshold (BASIN_SIM_THRESHOLD) the old cosine bridge
    # injected here is no longer needed — matchBasinsToPicks applies no threshold.
    # Pass the same health dict to the nav renderer so each chip can
    # show a dot when its file has issues. One source of truth across
    # the nav + per-file banner + launchpad row.
    nav_links = _render_nav_links(health_data)
    # Bundled JS deps — same pattern as launchpad_template.py
    # (petite-vue + Chart.js from CDN). `marked` is the standard markdown
    # renderer; ~30KB gzipped. Kills the dual-renderer problem (we already
    # have markdown_utils server-side for council pages; client-side
    # marked() keeps the memory viewer DRY).
    marked_src = "./vendor/marked.min.js"
    # wordcloud2.js (timdream) — standalone, ~31KB. Used by the topics.json
    # Reader view to render a basin cloud above the bar list.
    # d3 modules for the topics.json basin-relation graph. We pull only
    # the pieces we need rather than the full ~250KB d3 bundle:
    #   - d3-selection: DOM binding (.select, .selectAll, .data, .join)
    #   - d3-drag: pointer drag for moving nodes
    #   - d3-dispatch + d3-timer: event + animation loop (force needs these)
    #   - d3-quadtree: spatial index used by forceCollide + forceManyBody
    #   - d3-force: the simulation itself
    # Total ~80KB — still under the full d3 (~250KB).
    d3_select_src = "./vendor/d3-selection.min.js"
    d3_dispatch_src = "./vendor/d3-dispatch.min.js"
    d3_timer_src = "./vendor/d3-timer.min.js"
    d3_quadtree_src = "./vendor/d3-quadtree.min.js"
    d3_drag_src = "./vendor/d3-drag.min.js"
    d3_force_src = "./vendor/d3-force.min.js"
    # d3-zoom — pan + scroll-wheel zoom on the topic graph. The viewer
    # advertises "scroll to zoom" in the hint chip; without this module
    # that was a lie.
    d3_zoom_src = "./vendor/d3-zoom.min.js"
    # d3-interpolate is a transitive dep of d3-zoom (for the transform
    # interpolation during programmatic zoom). Tiny (~5KB).
    d3_interpolate_src = "./vendor/d3-interpolate.min.js"
    d3_color_src = "./vendor/d3-color.min.js"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trinity · Memory viewer</title>
  {FAVICON_LINK}
  <script src="{marked_src}"></script>
  <script src="{d3_select_src}"></script>
  <script src="{d3_dispatch_src}"></script>
  <script src="{d3_timer_src}"></script>
  <script src="{d3_quadtree_src}"></script>
  <script src="{d3_drag_src}"></script>
  <script src="{d3_force_src}"></script>
  <script src="{d3_color_src}"></script>
  <script src="{d3_interpolate_src}"></script>
  <script src="{d3_zoom_src}"></script>
  <style>
    {FONT_FACE_CSS}
    /* Palette + type: matches DESIGN.md + design_system.py — Calm/Muted-Teal
       (cool-mist bg, muted-teal primary action + accent) on the brand fonts
       Hanken Grotesk / JetBrains Mono (via FONT_FACE_CSS above). */
    :root {{
      --bg: #eaecef;
      --bg-wash: #eff1f4;
      --surface: #ffffff;
      --surface-muted: #f1f3f6;
      --fg: #2f363c;
      --meta: #5e666f;          /* AA 4.5:1+ on light bgs */
      --primary: #3f777c;       /* muted teal (action_primary) — white text 4.96:1 (AA) */
      --primary-hover: #34666b; /* deep teal (action_primary_hover) */
      --primary-text: #fbfdfc;  /* near-white action text */
      --accent: #4f9095;        /* muted teal */
      --border: #dde1e6;
      --code-bg: #f1f3f6;
      --success: #4f9095;
      --warning: #bd9658;       /* muted ochre — FILL (border-left/icon); NOT readable text and NOT a small graphical mark (2.74:1 on white, below the 1.4.11 3:1 non-text floor) */
      --warning-text: #79591b;  /* deep amber — the TEXT/small-mark token: 6.45:1 on white (mirrors design_system --warning-text). The nav stale-dot draws on THIS, not --warning. */
      --danger: #bd6a5a;        /* muted terracotta — FILL (border-left/icon); NOT readable as 11px text on the danger tint (2.98:1) */
      --danger-text: #99392c;   /* deep terracotta — the TEXT token: .viewer-trust-label "degraded" clears AA 4.5:1 on the trust-banner tint (mirrors design_system --danger-text) */
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Hanken Grotesk", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--fg);
      line-height: 1.5;
    }}
    /* Shared topbar — same shape as live_council.html. Spec:
       DESIGN.md → "Memory Viewer Guidance". Uses the .trinity-topbar
       contract so a single CSS change tweaks both pages. */
    .trinity-topbar {{
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 14px 28px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
    }}
    .trinity-topbar .topbar-back {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      font-size: 14px;
      font-weight: 500;
      color: var(--fg);
      text-decoration: none;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--bg);
      transition: background 0.12s, border-color 0.12s;
    }}
    .trinity-topbar .topbar-back:hover {{
      background: var(--surface-muted);
      border-color: var(--meta);
    }}
    .trinity-topbar .topbar-title {{
      font-size: 16px;
      font-weight: 600;
      margin: 0;
      color: var(--fg);
    }}
    .trinity-topbar .topbar-spacer {{ flex: 1; }}
    .layout {{
      display: grid;
      grid-template-columns: 240px 1fr;
      gap: 0;
      min-height: calc(100vh - 57px);
      min-height: calc(100dvh - 57px);
    }}
    .nav {{
      border-right: 1px solid var(--border);
      padding: 24px 16px;
      background: white;
    }}
    .nav-eyebrow {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--meta);
      margin-bottom: 12px;
    }}
    .memory-nav-link {{
      display: block;
      padding: 10px 12px;
      margin-bottom: 4px;
      border-radius: 6px;
      text-decoration: none;
      color: var(--fg);
      transition: background 0.1s;
    }}
    .memory-nav-link:hover {{ background: var(--code-bg); }}
    .memory-nav-link.active {{ background: rgba(79, 144, 149, 0.10); }}
    /* Stale-file dot indicator. Tiny warning-color circle next to the
       filename — same signal as the launchpad memory-health row and
       the per-file banner. The user spots which files need attention
       before clicking.
       --warning-text (#79591b, 6.45:1 on white / 5.79:1 on the active-tab
       tint), NOT --warning (#bd9658, the FILL token — 2.74:1, BELOW the WCAG
       1.4.11 3:1 non-text floor). The dot is the ONLY at-a-glance "which files
       need attention" mark in the nav (its whole job is to be spottable before
       clicking through), so a 7px graphical indicator at 2.74:1 defeated its
       own purpose for a low-vision user. The per-file health banner + the
       launchpad memory-health row already render this signal on the contrast-
       bearing --warning-text amber via a 3px border bar; the nav dot was the
       lone instance still drawing the sub-3:1 FILL token. UX sweep 2026-06-23. */
    .memory-nav-dot {{
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--warning-text);
      margin-left: 6px;
      vertical-align: middle;
      position: relative;
      top: -1px;
    }}
    .memory-name {{
      display: block;
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Monaco, monospace;
      font-size: 13px;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      font-weight: 500;
    }}
    .memory-brain {{
      display: block;
      font-size: 11px;
      color: var(--meta);
      margin-top: 2px;
    }}
    .content {{
      padding: 32px;
      max-width: 880px;
      margin: 0 auto;
    }}
    .content-header {{
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }}
    .content-header h2 {{
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Monaco, monospace;
      font-size: 18px;
      margin: 0 0 6px;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
    }}
    .content-header .meta {{
      font-size: 13px;
      color: var(--meta);
      margin: 0;
    }}
    /* Per-file health banner — carries the launchpad's memory-health
       warning into the file view so the user reads in context. Same
       warning fill + warning-color left border as the launchpad
       row (DESIGN.md palette: --warning #bd9658). */
    .viewer-health-banner {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 12px;
      padding: 8px 12px;
      background: rgba(79, 144, 149, 0.08);
      border-left: 3px solid var(--warning);
      border-radius: 0 6px 6px 0;
      font-size: 13px;
      flex-wrap: wrap;
    }}
    .viewer-health-status {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--meta);
    }}
    .viewer-health-hint {{
      color: var(--fg);
      flex: 1;
      min-width: 200px;
    }}
    .viewer-health-cmd {{
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 12px;
      color: var(--fg);
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 4px 10px;
      cursor: pointer;
      white-space: nowrap;
    }}
    .viewer-health-cmd:hover {{
      background: var(--surface-muted);
    }}
    /* Lens-TRUST banner — distinct from the ochre staleness banner above.
       This fires when the lens was built on the TF-IDF fallback embedder, i.e.
       the WHOLE lens is a keyword caricature (lens-health DEGRADED), not merely
       stale. Stronger terracotta (--danger) left border so the eye reads it as
       "don't trust this" rather than "this is a little behind". Body text is the
       same --fg-on-tint as the staleness banner (AA-compliant; NOT the
       white-on-danger button cell #200). */
    .viewer-trust-banner {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 12px;
      padding: 10px 12px;
      background: rgba(189, 106, 90, 0.10);
      border-left: 3px solid var(--danger);
      border-radius: 0 6px 6px 0;
      font-size: 13px;
      flex-wrap: wrap;
    }}
    .viewer-trust-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      /* --danger-text (deep terracotta), not --danger (#bd6a5a): this 11px uppercase
         "degraded" label is small text and must clear AA 4.5:1 — #bd6a5a on the
         trust-banner tint was 2.98:1 (unreadable-grade). The border-left below stays
         --danger (a fill, exempt). Mirrors --warning-text/--success-text. */
      color: var(--danger-text);
      font-weight: 600;
    }}
    .viewer-trust-hint {{
      color: var(--fg);
      flex: 1;
      min-width: 200px;
    }}
    /* Persistent rebuild chip — distinct intent from .viewer-health-cmd
       (which fires on staleness). This chip is always present so the
       user can rebuild a memory before staleness signals it (e.g.,
       after a milestone session adds new prompts the lens should see).
       Warm-brown accent reads as "additive" rather than a warning. */
    .viewer-rebuild-chip {{
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 12px;
      /* --primary-hover #34666b (5.1:1 on the grey --bg chip), NOT --primary
         #3f777c (only 4.29:1 there) — the chip's own grey background pulls the
         readable teal under AA, so it needs the deep teal. (The text rules on
         white/tinted surfaces use --primary; this chip carries its own --bg fill.) */
      color: var(--primary-hover);
      background: var(--bg);
      border: 1px solid var(--accent);
      border-radius: 4px;
      padding: 3px 9px;
      margin-left: 10px;
      cursor: pointer;
      white-space: nowrap;
      vertical-align: middle;
    }}
    .viewer-rebuild-chip:hover {{
      background: rgba(79, 144, 149, 0.08);
    }}
    pre.body {{
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Monaco, monospace;
      font-size: 13px;
      background: var(--code-bg);
      padding: 20px;
      border-radius: 6px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-wrap: break-word;
      margin: 0;
    }}
    .empty, .error {{
      padding: 40px;
      text-align: center;
      color: var(--meta);
    }}
    .error {{ color: #b91c1c; }}
    .empty code, .error code {{
      background: var(--code-bg);
      padding: 2px 6px;
      border-radius: 4px;
    }}
    /* Rendered markdown ─────────────────────────────────────────────── */
    .markdown-body {{ font-size: 14px; line-height: 1.65; min-width: 0; overflow-wrap: break-word; }}
    .markdown-body h1 {{ font-size: 22px; margin: 28px 0 12px; }}
    .markdown-body h2 {{ font-size: 18px; margin: 24px 0 10px; }}
    .markdown-body h3 {{ font-size: 16px; margin: 20px 0 8px; }}
    .markdown-body p {{ margin: 10px 0; }}
    .markdown-body ul, .markdown-body ol {{ margin: 10px 0; padding-left: 24px; }}
    .markdown-body li {{ margin: 4px 0; }}
    .markdown-body code {{
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Monaco, monospace;
      font-size: 0.92em;
      background: var(--code-bg);
      padding: 2px 6px;
      border-radius: 4px;
    }}
    .markdown-body pre {{
      background: var(--code-bg);
      padding: 16px;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 13px;
      /* overflow-x:auto alone lets the <pre> grow to fit a wide code line; without
         max-width it stretches the page on a phone (the lens/generators tabs
         render dev content with code + long tokens). Bound it, then scroll. */
      max-width: 100%;
    }}
    .markdown-body pre code {{ background: none; padding: 0; }}
    .markdown-body blockquote {{
      border-left: 3px solid var(--accent);
      padding: 4px 14px;
      margin: 12px 0;
      color: var(--meta);
    }}
    .markdown-body table {{
      border-collapse: collapse;
      margin: 16px 0;
      font-size: 13px;
      width: 100%;
      /* A chairman-emitted memory table (lens.md tensions, core.md, generators.md)
         can carry a long unbreakable token in a cell — a file path / URL / regex /
         hash from an evidence citation. `display:table` + `overflow-x:visible` lets
         such a cell push the whole table past the content column, blowing the
         DOCUMENT out horizontally at the WIDE (>=1180px) breakpoints where the
         `@media (max-width:1179px)` inner-scroll rule below doesn't apply. Make the
         table its own horizontal scroll container at EVERY width (the same bounded
         inner-scroll the narrow rule already used) so the page never scrolls
         sideways — only the table does. */
      display: block;
      overflow-x: auto;
      max-width: 100%;
    }}
    .markdown-body th, .markdown-body td {{
      border: 1px solid var(--border);
      padding: 8px 12px;
      text-align: left;
    }}
    .markdown-body th {{ background: var(--code-bg); font-weight: 600; }}
    .markdown-body tr:nth-child(even) td {{ background: #fbfbf8; }}
    .markdown-body em {{ color: var(--meta); }}
    /* JSON quick views ──────────────────────────────────────────────── */
    .pick-card {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 18px;
      margin-bottom: 12px;
      background: white;
    }}
    .pick-head {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }}
    .pick-basin {{
      font-family: "JetBrains Mono", ui-monospace, "SF Mono", Monaco, monospace;
      font-size: 14px;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      font-weight: 600;
      word-break: break-all;   /* long basin/pick ids wrap instead of overflowing on a phone */
    }}
    /* Human-readable basin name beside the opaque basin id, so a pick reads
       "b00 · Design" — what kind of question this routing rule is for. The name
       is the topology `label` / top-terms — free corpus text, so a single
       top_term can be a long unbreakable token (a dev identifier / path / URL
       the user keeps citing). Unlike the sibling .pick-basin id (which already
       carries word-break: break-all), this NAME had no break rule, so a long
       label demanded its full intrinsic width and blew the picks.json view past
       a 320px phone (pick-head scrollWidth 834 vs clientWidth 250 → the document
       horizontal-scrolled to ~869px). Break it like the launchpad .lens-basin-chip
       twin (same topology-label-on-a-narrow-card class). */
    .pick-basin-name {{
      font-size: 13px;
      font-weight: 600;
      color: var(--fg);
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .pick-primary {{
      font-size: 13px;
      color: var(--fg);
    }}
    .pick-badge {{
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 10px;
      background: var(--code-bg);
      color: var(--meta);
    }}
    .pick-badge.high {{ background: #dcfce7; color: #166534; }}
    .pick-badge.med  {{ background: #fef9c3; color: #854d0e; }}
    .pick-badge.low  {{ background: #fee2e2; color: #991b1b; }}
    .pick-meta {{ margin-top: 8px; font-size: 12px; color: var(--meta); }}
    .pick-failures {{ margin-top: 10px; font-size: 13px; }}
    .pick-failures ul {{ margin: 4px 0 0; padding-left: 20px; }}
    .routing-table {{
      border-collapse: collapse;
      font-size: 13px;
      width: 100%;
      margin: 12px 0;
    }}
    .routing-table th, .routing-table td {{
      border: 1px solid var(--border);
      padding: 6px 10px;
    }}
    .routing-table th {{ background: var(--code-bg); font-weight: 600; text-align: left; }}
    .routing-table td.score {{ text-align: right; font-family: "JetBrains Mono", ui-monospace, monospace; }}
    .routing-table td.best {{ background: rgba(34, 197, 94, 0.08); font-weight: 600; }}
    /* Cross-memory deep-links (picks ↔ routing) */
    .routing-task-link {{
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      text-decoration: none;
      border-bottom: 1px dotted var(--accent);
    }}
    .routing-task-link:hover {{
      border-bottom-style: solid;
    }}
    /* Routing → topology chip. Only renders when the row's task_type
       has a centroid match into topics.json. Sits inline next to the
       task name link, kept visually quiet so the primary task→picks
       link stays the dominant affordance. */
    .routing-topology-chip {{
      display: inline-block;
      margin-left: 8px;
      font-size: 10px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--meta);
      text-decoration: none;
      padding: 1px 6px;
      border: 1px solid var(--border);
      border-radius: 3px;
      vertical-align: middle;
    }}
    .routing-topology-chip:hover {{
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      border-color: var(--accent);
    }}
    tr.routing-row-focused td {{
      background: rgba(79, 144, 149, 0.10);
    }}
    .pick-actions {{
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 12px;
      flex-wrap: wrap;
    }}
    .pick-xlink {{
      display: inline-block;
      font-size: 12px;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      text-decoration: none;
      padding: 4px 10px;
      border: 1px solid var(--border);
      border-radius: 4px;
    }}
    .pick-xlink:hover {{
      background: var(--surface-muted);
    }}
    .pick-card-focused {{
      box-shadow: 0 0 0 2px var(--accent);
      background: rgba(79, 144, 149, 0.04);
    }}
    /* topics.json reader — basin-relation graph (Obsidian-style). */
    .topics-graph-wrap {{
      background: #1a1715;       /* ink-on-paper inverse — same hue family as --fg */
      border-radius: 8px;
      padding: 0;
      margin-bottom: 16px;
      position: relative;
      overflow: hidden;
      border: 1px solid var(--border);
    }}
    .topics-graph-svg {{
      display: block;
      width: 100%;
      height: 520px;
      background: radial-gradient(circle at center, #221c18 0%, #14110f 100%);
      cursor: grab;
    }}
    .topics-graph-svg:active {{ cursor: grabbing; }}
    .topics-graph-svg .link {{
      stroke: rgba(79, 144, 149, 0.35);   /* warm-brown, low opacity */
      stroke-width: 1px;
    }}
    .topics-graph-svg .link.strong {{
      stroke: rgba(79, 144, 149, 0.75);
      stroke-width: 2px;
    }}
    .topics-graph-svg .node {{
      cursor: pointer;
      stroke: rgba(255, 255, 255, 0.4);
      stroke-width: 1.5px;
    }}
    .topics-graph-svg .node:hover {{ stroke: white; stroke-width: 2.5px; }}
    /* Basins crystallized into routing rules get a warm-brown outer
       ring — same color family as the pick-xlink in the detail panel,
       so the visual encoding reads consistently. At-a-glance the user
       sees which basins matter vs which are noise. */
    .topics-graph-svg .node.pick-basin {{
      stroke: rgba(79, 144, 149, 0.95);
      stroke-width: 2.5px;
    }}
    .topics-graph-svg .node.pick-basin:hover {{
      stroke: rgba(79, 144, 149, 1);
      stroke-width: 3.5px;
    }}
    /* Visible keyboard focus (WCAG 2.4.7) for the now-focusable basin nodes.
       The default UA outline on an SVG element renders inconsistently / can be
       clipped by the viewport; a bright stroke ring reads clearly over the dark
       graph canvas and matches the hover affordance's "this is interactive" cue.
       Declared AFTER (and at higher specificity than) the .pick-basin stroke so
       the focus ring wins on BOTH plain and pick basins — a pick-basin must not
       swallow the keyboard focus indicator. */
    .topics-graph-svg .node:focus {{ outline: none; }}
    .topics-graph-svg .node.node:focus-visible,
    .topics-graph-svg .node.pick-basin:focus-visible {{
      outline: none;
      stroke: var(--accent);
      stroke-width: 4px;
    }}
    .topics-graph-svg .label {{
      fill: rgba(255, 255, 255, 0.92);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-weight: 600;
      pointer-events: none;
      text-anchor: middle;
      paint-order: stroke fill;
      stroke: rgba(0, 0, 0, 0.7);
      stroke-width: 3px;
      stroke-linejoin: round;
    }}
    .topics-graph-hint {{
      position: absolute;
      bottom: 12px;
      right: 16px;
      font-size: 11px;
      /* Gesture/recovery instruction over the dark graph canvas — INFORMATIONAL,
         not decorative, so it must clear the 4.5:1 AA body floor. White@0.4 read
         ~3.8:1 over the #14110f gradient center (below AA); @0.62 lands ~7.6:1
         while staying visibly dimmer than the node labels (white@0.92). The
         "Graph library not loaded — try the Raw JSON view." recovery hint shares
         this class, so the fix rescues the broken-state guidance too. */
      color: rgba(255, 255, 255, 0.62);
      pointer-events: none;
    }}
    .topics-graph-detail {{
      background: white;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 12px;
      font-size: 13px;
      min-height: 64px;
    }}
    .topics-graph-detail .empty {{ color: var(--meta); padding: 0; text-align: left; }}
    /* Honest empty-state for a basin with no representatives AND no terms
       (legacy/stale topics.json) — without it the detail panel paints only
       the size header and reads as a dead-end. --meta is AA on white. */
    .topics-basin-empty {{ color: var(--meta); margin-top: 10px; line-height: 1.45; }}
    .topics-basin-empty code {{
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 12px;
      background: var(--code-bg, #f0f2f4);
      padding: 1px 5px;
      border-radius: 4px;
      color: var(--fg);
    }}
    .topics-graph-detail .basin-id {{
      font-family: "JetBrains Mono", ui-monospace, monospace;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      font-weight: 600;
      word-break: break-all;   /* long basin ids wrap instead of overflowing on a phone */
    }}
    .topics-graph-detail .row-label {{ color: var(--meta); }}
    /* Launch-council chip — turns the topology view into an action
       surface. Sits below the representatives list; click copies a
       `trinity-local council-launch --task "<headline>"` command using
       the basin's closest-to-centroid representative as the seed prompt.
       Teal accent (var(--fg)) to read as "go" / additive. */
    .topics-launch-chip {{
      display: inline-block;
      margin-top: 12px;
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 12px;
      color: var(--fg);
      background: var(--bg);
      border: 1px solid var(--fg);
      border-radius: 4px;
      padding: 4px 10px;
      cursor: pointer;
      white-space: nowrap;
    }}
    .topics-launch-chip:hover {{
      background: rgba(79, 144, 149, 0.08);
    }}
    /* Cross-memory pick link in the basin detail panel — surfaces
       when a basin has been consolidated into a routing rule. Same
       shape as .pick-xlink (used in the routing Reader). Reading
       left-to-right: routing rule lives, click to see it. */
    .topics-pick-xlink {{
      display: inline-block;
      margin-top: 10px;
      margin-right: 10px;
      font-size: 12px;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      text-decoration: none;
      padding: 4px 10px;
      border: 1px solid var(--border);
      border-radius: 4px;
    }}
    .topics-pick-xlink:hover {{
      background: var(--surface-muted);
    }}
    .topics-reps-label {{
      color: var(--meta);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-top: 10px;
      margin-bottom: 6px;
    }}
    .topics-reps-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .topics-rep {{
      background: var(--code-bg);
      border-radius: 6px;
      padding: 10px 14px;
      font-size: 13px;
      color: var(--fg);
      border-left: 3px solid var(--accent);
      line-height: 1.5;
    }}
    .topics-rep-thread.expandable {{
      cursor: pointer;
      transition: background 0.1s;
    }}
    .topics-rep-thread.expandable:hover {{
      background: var(--surface-muted);
    }}
    .topics-rep-thread.expandable:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .topics-rep-head {{
      display: flex;
      align-items: baseline;
      gap: 10px;
    }}
    /* min-width:0 lets the flex headline shrink below its content's min-content
       (so the flex row can't blow the card wide); overflow-wrap:anywhere then
       BREAKS a long separator-free headline token (a URL/path/identifier — the
       headline is the rep's verbatim first prompt) instead of relying on the
       browser's default URL-only break points (which leave a separator-free
       identifier or path unbroken). Twin of .topics-rep-turn-text below. */
    .topics-rep-headline {{ flex: 1; min-width: 0; overflow-wrap: anywhere; word-break: break-word; }}
    .topics-rep-meta {{
      font-size: 11px;
      color: var(--meta);
      white-space: nowrap;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--bg);
      border: 1px solid var(--border);
    }}
    .topics-rep-chev {{
      color: var(--meta);
      font-size: 13px;
      width: 12px;
      text-align: center;
    }}
    /* Per-rep replay chip — finer-grained than the basin-level launch
       chip. Lets the user replay any specific representative thread
       (not just the closest-to-centroid one the basin chip uses). */
    .topics-rep-replay {{
      font-family: "JetBrains Mono", ui-monospace, monospace;
      font-size: 11px;
      color: var(--fg);
      background: var(--bg);
      border: 1px solid var(--fg);
      border-radius: 4px;
      padding: 2px 8px;
      cursor: pointer;
      white-space: nowrap;
      opacity: 0.7;
    }}
    .topics-rep-replay:hover {{
      background: rgba(79, 144, 149, 0.08);
      opacity: 1;
    }}
    .topics-rep-turns {{
      list-style: none;
      padding: 0;
      margin: 10px 0 0;
      display: none;
      border-top: 1px solid var(--border);
      padding-top: 8px;
    }}
    .topics-rep-thread.open .topics-rep-turns {{ display: block; }}
    .topics-rep-turn {{
      display: grid;
      /* minmax(0, 1fr) NOT a bare 1fr: a grid track's implicit min is
         `auto` = min-content, so an expanded turn's snippet (free user-prompt
         text — reachably a long separator-free token: a URL, a ~/.cache/...
         path, a long identifier the founder pastes) pins the 1fr track to its
         longest run and pushes the whole memory.html document past the
         viewport (proven: a 199-char headerless-token turn blew memory.html to
         scrollWidth 1053 across the entire 561–1024 single-column band — the
         topics basin-detail horizontal-scrolled; masked ≤560px only by the
         incidental overflow-x:auto the .topics-graph-detail overflow-y:auto
         couples on, and absorbed ≥1080px by the wide content). minmax(0,1fr)
         lets the track shrink below min-content; the overflow-wrap on
         .topics-rep-turn-text below then breaks the token inside it. Same
         grid-track-min-content blow-out belt as .provider-status-row's
         minmax(0,...) tracks. */
      grid-template-columns: 32px minmax(0, 1fr);
      gap: 8px;
      padding: 4px 0;
      font-size: 12px;
      line-height: 1.5;
      color: var(--fg);
    }}
    /* The turn snippet is VERBATIM user-prompt text — exactly where a long
       UNBREAKABLE token lives. Without a break rule it demands its full
       intrinsic width even in the minmax(0,1fr) track; overflow-wrap:anywhere
       wraps it so it stays inside the basin-detail panel. Same rule the
       headline + .pick-basin-name + the launchpad corpus chips carry. */
    .topics-rep-turn-text {{
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .topics-rep-turn-idx {{
      font-family: "JetBrains Mono", ui-monospace, monospace;
      color: var(--primary);  /* AA: --primary #3f777c (5.07:1 white / 4.55:1 active-tab tint) — NOT --accent #4f9095 (the WORDMARK teal, 3.08–3.65:1 as functional text). Wordmark-teal-as-text is sub-AA; --accent stays the fill (borders/svg strokes/focus rings). */
      font-weight: 600;
      font-size: 11px;
    }}
    /* JSON syntax highlight (used for topics.json Raw view + others) */
    .json-body {{ font-family: ui-monospace, monospace; font-size: 12px; }}
    .json-key {{ color: #3f777c; }}
    .json-str {{ color: #166534; }}
    .json-num {{ color: #b45309; }}
    .json-bool {{ color: #7fa0ad; }}
    .json-null {{ color: var(--meta); }}
    /* View toggle */
    .view-toggle {{
      display: inline-flex;
      gap: 8px;
      margin-bottom: 16px;
      font-size: 12px;
    }}
    .view-toggle button {{
      border: 1px solid var(--border);
      background: white;
      padding: 4px 10px;
      border-radius: 4px;
      cursor: pointer;
      color: var(--meta);
    }}
    .view-toggle button.active {{
      /* --primary (#3f777c, white 4.96:1 AA), NOT --accent (the #4f9095 WORDMARK
         teal): white on --accent was 3.65:1 — the SELECTED toggle's label
         (Reader/Raw) was the LEAST readable state, below the AA body floor.
         --accent is the WCAG-exempt brand mark, not a white-text fill; --primary
         is the purpose-built AA-readable teal (same hue family). (Sibling of the
         council .rank-badge + the CTA-button AA push.) UX sweep 2026-06-21. */
      background: var(--primary);
      color: white;
      border-color: var(--primary);
    }}
    /* Narrow screens: the viewer shipped with NO responsive handling — a fixed
       240px-nav + 1fr-content grid where the content's grid-item `min-width:
       auto` let it expand to its widest child's min-content (a long JSON line,
       a markdown/routing table), so pages scrolled sideways on a phone (~718px
       over) AND the whole tablet/small-laptop range (240px nav + ~789px table
       doesn't fit until ~1093px). Breakpoint is 1179px (not 768 — v1.7.183's
       phone-only fix left the tablet dead zone overflowing; matches the
       launchpad's rail-collapse width). Stack the nav above the content, let it
       shrink (min-width:0 — THEN the inner pre actually scrolls), and keep the
       topology SVG and tables inside the viewport. ≥1180px keeps the 2-column. */
    @media (max-width: 1179px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .nav {{ border-right: none; border-bottom: 1px solid var(--border); }}
      .content {{ min-width: 0; max-width: 100%; padding: 20px 16px; }}
      .routing-table {{ display: block; overflow-x: auto; }}
      .topics-graph-svg {{ max-width: 100%; }}
    }}
    /* Phone: the topology SVG ships a fixed ~1000×520 viewBox that squashes
       ~2.67:1 (near-unreadable) on a narrow viewport, and the basin-detail
       panel can grow tall enough to push the graph off-screen. Shorten the
       SVG and cap the detail so the graph stays in view; tighten the topbar
       chrome too. */
    @media (max-width: 767px) {{
      .trinity-topbar {{ padding: 10px 12px; }}
      .topics-graph-svg {{ height: 340px; }}
    }}
    @media (max-width: 560px) {{
      .topics-graph-svg {{ height: 300px; }}
      .topics-graph-detail {{ max-height: 32vh; overflow-y: auto; }}
    }}
    /* Visually-hidden but screen-reader-available — backs the persistent
       aria-live status mirror so a "✓ Copied" glyph swap (which is mute to AT)
       ALSO speaks (WCAG 4.1.3 Status Messages). NOT display:none — that removes
       it from the a11y tree; the clip+1px technique keeps it spoken while taking
       zero visual space. The launchpad already had this (SHARED_CSS .sr-only);
       the memory viewer ships its OWN style block and was the un-fixed sibling. */
    .sr-only {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
  </style>
</head>
<body>
  <header class="trinity-topbar">
    <a class="topbar-back" href="../portal_pages/launchpad.html">← Launchpad</a>
    <h1 class="topbar-title">Your lens</h1>
  </header>
  <div class="layout">
    <nav class="nav" aria-label="Memory files">
      <div class="nav-eyebrow">Lens (core · lens · topics · vocabulary) + Scoreboards (picks · routing)</div>
      {nav_links}
    </nav>
    <main class="content" id="content">
      <div class="empty">Loading…</div>
    </main>
  </div>
  <!-- Persistent screen-reader status mirror (WCAG 4.1.3). Every copy chip in the
       viewer (Replay / Launch council / ↻ Rebuild / health-fix / stale-basin
       commands) only swaps its label to "✓ Copied" — a visual change AT never
       hears. announceCopy() routes "Copied to clipboard" through this polite
       region so the copy is spoken. One shared region, lowest precedence. -->
  <div id="sr-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>

  <script>
    // Memory contents are inlined at render time (not fetched at runtime)
    // so the viewer works under file:// from the desktop shortcut.
    // Refreshes whenever portal-html runs.
    window.__TRINITY_MEMORIES__ = {memories_payload};
    // Health signal travels with the file: when the launchpad surfaced
    // "picks.json audit-disagreed", clicking through to inspect should
    // KEEP that warning visible so the user reads the file in context.
    window.__TRINITY_MEMORY_HEALTH__ = {health_payload};
    // Lens TRUSTWORTHINESS (distinct from the staleness signals above). When the
    // lens was built on the SHA-1 TF-IDF fallback embedder (a fresh install with
    // no [mlx] extras — NORMAL operation), every semantic file here is a keyword
    // caricature, not the user's taste. `lens-health` reports this DEGRADED;
    // the viewer must say the same instead of painting a confident lens over
    // garbage (#35 green-while-degraded). Drives the trust banner on the
    // embedding-derived files (lens/topics/core/generators) only — vocabulary is
    // lexical anchors that stay correct under the fallback, and picks/routing are
    // council-outcome scoreboards, not embedding-derived.
    window.__TRINITY_LENS_TRUST__ = {lens_trust_payload};
    // The REAL routing gate (lens_routing.WINNER_MARGIN_FLOOR), injected so the
    // picks badge + the topology basin detail report a basin's confidence with the
    // SAME threshold `ask` actually routes on — not a hardcoded one. A basin whose
    // winner-margin is below this is a near-tie: `ask` ignores the tally and falls
    // to kNN, so the viewer must NOT claim it "Routes to X" (#299, propagated to
    // the viewer's two confidence surfaces).
    const WINNER_MARGIN_FLOOR = {winner_margin_floor};
    // Server-pre-formatted picks margins (raw-value repr → Python 2dp string), so
    // the picks badge + topology basin detail render the SAME margin as the CLI
    // `consolidate` line and the launchpad routing card. Re-rounding the raw
    // float in JS would round half-UP (toFixed) and disagree with Python's
    // half-to-even on an exact dyadic tie like 0.625 ("0.63" vs "0.62"). Keyed by
    // round(value,3).toString() — see fmtMargin().
    const MARGIN_FMT = {margin_fmt_json};
    const FILES = {files_json};
    // The FULL allowlist (incl. optional files like generators.md hidden from the
    // nav when absent). `?file=` validation resolves against this so a direct link
    // to a known-but-unbuilt optional file shows "not built yet — run X" rather
    // than a misleading "Unknown memory" dead-end (the nav can't point there).
    const ALL_FILES = {all_files_json};
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("file") || FILES[0].name;
    const file = FILES.find(f => f.name === requested);
    const content = document.getElementById("content");

    // Highlight active link. The `.active` class is a VISUAL "you are here"
    // marker in the nav landmark; a screen-reader user navigating the file
    // list otherwise can't tell which file is the current page (the page <h1>
    // is the static "Your lens" on every file — it doesn't disambiguate). So
    // mirror the visual state programmatically with aria-current="page" on the
    // active link (WCAG 1.3.1 / 4.1.2 — current-page state must be conveyed to
    // AT, not by color alone). Single source of truth: set it where `.active`
    // is set so the two never drift.
    document.querySelectorAll(".memory-nav-link").forEach(a => {{
      if (a.dataset.file === requested) {{
        a.classList.add("active");
        a.setAttribute("aria-current", "page");
      }}
    }});

    // DOM helpers — building with createElement + textContent avoids any
    // innerHTML write path so a future change to FILES (or a corrupted
    // file body) can never inject HTML, even though the source is local.
    function clearContent() {{ while (content.firstChild) content.removeChild(content.firstChild); }}
    function el(tag, cls, text) {{
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }}

    // WCAG 4.1.3 — every copy chip's "✓ Copied" label swap is mute to AT.
    // Push the confirmation through the persistent #sr-status role=status region
    // so a screen-reader user who copies a command HEARS it. clear-then-set on a
    // microtask so a repeated copy of the SAME command re-fires the polite region
    // (an unchanged textContent wouldn't re-announce); self-clears after 2s.
    let __srClearTimer = null;
    function announceCopy(msg) {{
      const region = document.getElementById("sr-status");
      if (!region) return;
      region.textContent = "";
      Promise.resolve().then(() => {{ region.textContent = msg || "Copied to clipboard"; }});
      if (__srClearTimer) clearTimeout(__srClearTimer);
      __srClearTimer = setTimeout(() => {{ region.textContent = ""; }}, 2000);
    }}

    // Bridge between picks.json and topics.json. Returns
    // {{ basinIdToTask, taskToBasinId }} — bidirectional Maps for the
    // cross-memory links. Both Reader views (picks, topics) call this
    // so the matching rules can't drift between the two directions.
    //
    // POST-COLLAPSE (#298): the routing picks ARE keyed by the lens basin id
    // (b00..), the SAME id space topics.json uses, so the bridge is now a plain
    // identity match — a pick links to the topology basin of the same id. (Pre-
    // collapse picks were keyed by a task_type label and carried a SEPARATE
    // basin_centroid, so the bridge was a cosine match; that centroid is gone.)
    // SIM_THRESHOLD is retained in the signature for the legacy centroid path
    // but unused by the identity match.
    function matchBasinsToPicks(basins, picksObj) {{
      const basinIdToTask = new Map();
      const taskToBasinId = new Map();
      const basinIdToPick = new Map();
      if (!Array.isArray(basins) || !picksObj) return {{ basinIdToTask, taskToBasinId, basinIdToPick }};
      const topologyIds = new Set(basins.map(b => b.id).filter(Boolean));
      Object.keys(picksObj).forEach(basinId => {{
        const pick = picksObj[basinId];
        // Only live lens-basin picks (carry a `winner`) bridge to topology.
        const winner = pick && (pick.winner || (pick.routing_rule && pick.routing_rule.primary));
        // String shape-guard: a corrupt non-string winner (a NUMBER 42 or an
        // OBJECT {{primary:…}}) is truthy, but `String(winner)` here would freeze
        // "42" / "[object Object]" into pick.winner — and the topology xlink then
        // brands THAT string ("[object Object]" title-cased to garbage), past
        // providerBrand's non-string gate (the string was already coerced here).
        // Reject a non-string winner at the bridge so it never enters basinIdToPick.
        if (typeof winner !== "string" || !winner.trim()) return;
        if (topologyIds.has(basinId)) {{
          if (!basinIdToTask.has(basinId)) basinIdToTask.set(basinId, basinId);
          taskToBasinId.set(basinId, basinId);
          // Expose winner + margin so the topology basin detail can show the
          // routing pick INLINE (post-#298 a basin IS a routing unit, so its
          // chairman-winner is its defining property — surfacing it where the
          // user explores topics saves a click into the picks Reader).
          const margin = isFiniteNumber(pick.margin) ? pick.margin : null;
          basinIdToPick.set(basinId, {{ winner: winner, margin }});
        }}
      }});
      return {{ basinIdToTask, taskToBasinId, basinIdToPick }};
    }}

    // Convenience: load the picks payload + topics payload from the
    // inlined memories and run the centroid match. Swallows parse
    // errors so a malformed memory can't break either Reader view.
    function loadCrossMemoryMaps() {{
      let picksObj = null, topicsObj = null;
      try {{
        const raw = window.__TRINITY_MEMORIES__?.["picks.json"];
        if (raw) picksObj = JSON.parse(raw);
      }} catch (_) {{}}
      try {{
        const raw = window.__TRINITY_MEMORIES__?.["topics.json"];
        if (raw) topicsObj = JSON.parse(raw);
      }} catch (_) {{}}
      const basins = topicsObj && Array.isArray(topicsObj.basins) ? topicsObj.basins : [];
      const maps = matchBasinsToPicks(basins, picksObj || {{}});
      // Tick #39: also expose basin → top-3-terms labels for tooltip
      // text on the cross-memory chips that target topology basins.
      // Same data the launchpad reads server-side; mirrored here so
      // viewer-side chips don't show opaque "b03" hovers.
      const basinLabels = new Map();
      // basinNames: the human-READABLE basin name (the semantic `label`,
      // falling back to top_terms) — what topics.json's labelFor() shows on a
      // node. The picks Reader heads each card with this so a routing pick reads
      // "b00 · Design · Use Claude" instead of an opaque "b00 · Use Claude" the
      // user can't connect to a kind of question (UX sweep: the picks scoreboard
      // headed every pick with a bare basin id, leaving the user unable to tell
      // WHAT a pick was for without hovering the off-card topology link).
      const basinNames = new Map();
      for (const b of basins) {{
        const bid = b && b.id;
        // Array.isArray guard, not a truthy `|| []`: a corrupt/hand-edited
        // topics.json whose `top_terms` is a STRING ("design-arch-api") is
        // truthy with a `.length`, so it slipped past `terms.length` and
        // `terms.slice(0,3)` returned a STRING — whose `.join` is undefined →
        // an UNCAUGHT "terms.slice(...).join is not a function" that blanked
        // BOTH the picks Reader AND the topics Reader (this helper is shared
        // by renderPicksReader + renderTopicsReader). Every other top_terms
        // access in this file already Array.isArray-guards; this was the one
        // un-guarded sibling. Non-array → treat as absent.
        const terms = Array.isArray(b && b.top_terms) ? b.top_terms : [];
        if (bid && terms.length) {{
          basinLabels.set(String(bid), terms.slice(0, 3).join(" · "));
        }}
        if (bid) {{
          const name = (b && typeof b.label === "string" && b.label.trim())
            ? b.label.trim()
            : (terms.length ? terms.slice(0, 2).join(" · ") : "");
          if (name) basinNames.set(String(bid), name);
        }}
      }}
      maps.basinLabels = basinLabels;
      maps.basinNames = basinNames;
      return maps;
    }}

    // Tooltip helper for cross-memory chips that deep-link into the
    // topology view. Surfaces the basin's top-3 terms when available;
    // falls back to a generic "Open basin <id>" when the label map is
    // empty (cold install, no lens-build). Mirrors the Vue method
    // basinHoverLabel in launchpad_template — same wording so the
    // viewer + launchpad agree on hover text.
    function basinHoverTitle(basinId, labels) {{
      if (!basinId) return "";
      const terms = labels && labels.get && labels.get(String(basinId));
      if (terms) return "Basin " + basinId + " — " + terms;
      return "Open basin " + basinId + " in the topology graph";
    }}
    function renderHeader(file, isBuilt) {{
      const wrap = el("div", "content-header");
      // Title row: file name + persistent build/rebuild chip. The chip copies
      // the relevant CLI to the clipboard — works under file:// without
      // a server, same mechanic as the memory-health chip.
      const titleRow = el("div");
      titleRow.style.display = "flex";
      titleRow.style.alignItems = "center";
      titleRow.style.flexWrap = "wrap";
      titleRow.style.gap = "6px";
      titleRow.appendChild(el("h2", null, file.name));
      const rebuildCmd = "trinity-local " + suggestionFor(file.name);
      // ↻ Build / ↻ Rebuild — unified copy with launchpad lens-rebuild
      // (tick #76) and cortex-rebuild (tick #77) chips. Tick #79 brought the
      // memory viewer chip in line so the user sees the same affordance
      // across surfaces. Principle #11: shared UI primitives stay
      // consistent across pages.
      // On a NOT-built-yet file (cold install) "Rebuild" is a lie — there is
      // nothing to re-build — and it contradicts the body's "Not built yet…
      // to GENERATE it" with the IDENTICAL command. Label the first build
      // "↻ Build"; only a populated file gets "↻ Rebuild" (UX sweep iter 73).
      const buildLabel = isBuilt ? "↻ Rebuild" : "↻ Build";
      const rebuildChip = el("button", "viewer-rebuild-chip", buildLabel);
      rebuildChip.type = "button";
      rebuildChip.title = "Copy: " + rebuildCmd;
      rebuildChip.dataset.file = file.name;
      rebuildChip.dataset.built = isBuilt ? "1" : "0";
      rebuildChip.addEventListener("click", () => {{
        if (navigator.clipboard?.writeText) {{
          navigator.clipboard.writeText(rebuildCmd).catch(() => null);
        }}
        rebuildChip.textContent = "✓ Copied";
        announceCopy("Copied: " + rebuildCmd);
        setTimeout(() => {{ rebuildChip.textContent = buildLabel; }}, 2200);
      }});
      titleRow.appendChild(rebuildChip);
      wrap.appendChild(titleRow);
      wrap.appendChild(el("p", "meta", file.brain + " · " + file.tagline));
      // Lens-TRUST banner (embedder honesty) — fires BEFORE the staleness banner
      // so the user reads "this whole lens is a keyword caricature" before any
      // localized staleness note. Only on the SEMANTIC files: lens.md (tensions),
      // topics.json (basins), core.md (distillation of both), generators.md (the
      // cross-domain lift) — all built from the embedding geometry, so all are
      // caricatures under the TF-IDF fallback. NOT vocabulary.md (lexical anchors
      // stay correct on the fallback — CLAUDE.md's "always-correct lexical
      // anchors") and NOT picks.json / routing.json (council-outcome scoreboards,
      // not embedding-derived). One source of truth with the CLI `lens-health`
      // verb (lens_health._embedding_backend), so the surfaces can't disagree.
      const TRUST_SCOPED = ["lens.md", "topics.json", "core.md", "generators.md"];
      const trust = window.__TRINITY_LENS_TRUST__ || {{}};
      if (trust.embedder_degraded && TRUST_SCOPED.indexOf(file.name) !== -1 && isBuilt) {{
        const tb = el("div", "viewer-trust-banner");
        tb.appendChild(el("span", "viewer-trust-label", "degraded"));
        const hint = el("span", "viewer-trust-hint");
        // The embedder summary already names the symptom (TF-IDF fallback →
        // caricature); append the fix command from the same lens-health row.
        hint.textContent = (trust.summary || "Built on the TF-IDF fallback embedder — not real semantic vectors.")
          + (trust.fix ? "  Fix: " + trust.fix : "");
        tb.appendChild(hint);
        wrap.appendChild(tb);
      }}
      // Per-file health banner: filter the inlined health payload to
      // issues that mention this file by name. The launchpad surfaces
      // the same data in aggregate; the viewer surfaces it in-context
      // so the user reads the file knowing what's stale about it.
      const issues = ((window.__TRINITY_MEMORY_HEALTH__ || {{}}).issues) || [];
      const relevant = issues.filter(i => i.name === file.name);
      relevant.forEach(issue => {{
        const banner = el("div", "viewer-health-banner");
        banner.appendChild(el("span", "viewer-health-status", issue.status));
        const hintWrap = el("span", "viewer-health-hint");
        hintWrap.textContent = issue.hint || "";
        banner.appendChild(hintWrap);
        // Mirror the launchpad's click-to-copy command chip OR the
        // "Inspect →" href so the same action surfaces in both places.
        if (issue.command) {{
          const chip = el("button", "viewer-health-cmd");
          chip.type = "button";
          chip.textContent = issue.command;
          chip.title = "Copy: " + issue.command;
          chip.addEventListener("click", () => {{
            if (navigator.clipboard?.writeText) {{
              navigator.clipboard.writeText(issue.command).catch(() => null);
            }}
            chip.textContent = "✓ Copied";
            announceCopy("Copied: " + issue.command);
            setTimeout(() => {{ chip.textContent = issue.command; }}, 2200);
          }});
          banner.appendChild(chip);
        }}
        wrap.appendChild(banner);
      }});
      return wrap;
    }}
    function renderEmpty(file) {{
      const wrap = el("div", "empty");
      wrap.appendChild(document.createTextNode("Not built yet. Run "));
      wrap.appendChild(el("code", null, "trinity-local " + suggestionFor(file.name)));
      wrap.appendChild(document.createTextNode(" to generate it."));
      return wrap;
    }}
    if (!file) {{
      clearContent();
      // A known optional file (e.g. generators.md) hidden from the nav because it
      // isn't built yet — reached by a direct ?file= link. It's NOT unknown: show
      // its header + the "run trinity-local <verb>" empty-state, same as any other
      // unbuilt memory. Only a genuinely unrecognized name gets "Unknown memory".
      const known = ALL_FILES.find(f => f.name === requested);
      if (known) {{
        // Reached only when the file isn't in the nav = not built yet → "↻ Build".
        content.appendChild(renderHeader(known, false));
        content.appendChild(renderEmpty(known));
      }} else {{
        const errWrap = el("div", "error");
        errWrap.appendChild(document.createTextNode("Unknown memory: "));
        errWrap.appendChild(el("code", null, requested));
        errWrap.appendChild(document.createTextNode(". Pick one from the nav."));
        content.appendChild(errWrap);
      }}
    }} else {{
      // Read from the inlined payload — no fetch, works under file://.
      const text = window.__TRINITY_MEMORIES__?.[file.name];
      clearContent();
      // A file with no usable content is "not built yet" → chip says "↻ Build",
      // matching the renderEmpty body. A populated file → "↻ Rebuild".
      const isBuilt = !(text === null || text === undefined || !text.trim());
      content.appendChild(renderHeader(file, isBuilt));
      if (!isBuilt) {{
        content.appendChild(renderEmpty(file));
      }} else if (file.name.endsWith(".md")) {{
        renderMarkdown(content, text);
      }} else {{
        renderJson(content, file.name, text);
      }}
    }}

    // ─── Markdown rendering ──────────────────────────────────────────────
    // Uses `marked` (CDN dep, same pattern as petite-vue/Chart.js). Parsed
    // HTML goes through DOMParser so we never call innerHTML on the live
    // tree — avoids the XSS surface even if a memory file is hand-edited.
    function renderMarkdown(target, mdText) {{
      const wrap = el("div", "markdown-body");
      try {{
        const html = window.marked ? window.marked.parse(mdText) : null;
        if (html) {{
          const parsed = new DOMParser().parseFromString(html, "text/html");
          // Strip any <script>/<style>/<iframe> tags before adopting nodes —
          // marked doesn't emit them but a future config change could.
          parsed.querySelectorAll("script,style,iframe,object,embed").forEach(n => n.remove());
          // Defense-in-depth: drop on*= event handlers and any href/src that
          // isn't http/https/mailto (kills javascript:/data: stored-XSS).
          const safeScheme = /^(https?:|mailto:)/i;
          parsed.querySelectorAll("*").forEach(node => {{
            for (const attr of Array.from(node.attributes)) {{
              const name = attr.name.toLowerCase();
              if (name.startsWith("on")) {{
                node.removeAttribute(attr.name);
              }} else if ((name === "href" || name === "src") && !safeScheme.test(attr.value.trim())) {{
                node.removeAttribute(attr.name);
              }}
            }}
          }});
          // Demote the PROGRAMMATIC level of every content heading (aria-level)
          // without touching the rendered tag. A lens file that opens with
          // "# Lens" parses to a literal <h1>, which would be a SECOND <h1>
          // competing with the topbar page <h1> "Your lens" AND an outline
          // break under the <h2> file-name section a screen-reader user hits
          // navigating by heading (WCAG 1.3.1 / 2.4.6). The file name is an
          // <h2>, so content headings nest at level 3+ (tag offset 2). The
          // visible <hN> tag — and the font-size the .markdown-body hN CSS keys
          // on — is unchanged; only the announced level moves.
          parsed.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach(node => {{
            const lvl = parseInt(node.tagName[1], 10);
            const ariaLevel = Math.min(6, lvl + 2);
            if (ariaLevel !== lvl) node.setAttribute("aria-level", String(ariaLevel));
          }});
          while (parsed.body.firstChild) wrap.appendChild(parsed.body.firstChild);
        }} else {{
          // marked failed to load — fall through to raw text in <pre>
          wrap.appendChild(el("pre", "body", mdText));
        }}
      }} catch (e) {{
        wrap.appendChild(el("pre", "body", mdText));
      }}
      target.appendChild(wrap);
    }}

    // ─── JSON rendering ──────────────────────────────────────────────────
    // Two views: a schema-aware "Reader" view (cards/tables for picks +
    // routing) and a "Raw" pretty-printed JSON view. Toggle preserves
    // across nav clicks via the active button state.
    function renderJson(target, name, jsonText) {{
      let parsed = null;
      try {{ parsed = JSON.parse(jsonText); }}
      catch (_) {{
        target.appendChild(el("pre", "body", jsonText));
        return;
      }}

      // The Readers assume a specific object shape (picks: basin_id -> pattern;
      // routing: a by_task_type map; topics: a basins array). A valid-JSON-but-
      // WRONG-TYPE file — a clobbered/corrupt array or scalar where an object is
      // expected — must fall to the Raw view, NOT iterate array indices as fake
      // basins (browser-found 2026-06-02 driving a corrupt home: an array-shaped
      // picks.json rendered "0"/"1"/"2" cards with dead Mark-wrong actions). The
      // client instance of the server-side guard_shape_not_just_parse fixes.
      function readerShapeOk(p) {{
        if (p === null || typeof p !== "object" || Array.isArray(p)) return false;
        if (name === "topics.json") return Array.isArray(p.basins);
        if (name === "routing.json") {{
          const bt = p.by_task_type;
          return bt === undefined || (bt !== null && typeof bt === "object" && !Array.isArray(bt));
        }}
        return true;  // picks.json: any non-array object (basin_id -> pattern)
      }}
      const isJsonReaderFile = name === "picks.json" || name === "routing.json" || name === "topics.json";
      const readerSupported = isJsonReaderFile && readerShapeOk(parsed);
      const toggleWrap = el("div", "view-toggle");
      // The Reader/Raw toggle is a 2-state view switch; its selected view was
      // conveyed ONLY by the `.active` color class. Mark the group + buttons so
      // AT announces which view is pressed (WCAG 4.1.2 — toggle state by color
      // alone is invisible to a screen reader). aria-pressed mirrors `.active`
      // (set together below so they can't drift); type="button" so neither
      // submits if this ever sits inside a form.
      toggleWrap.setAttribute("role", "group");
      toggleWrap.setAttribute("aria-label", "Choose view: Reader or Raw JSON");
      const readerBtn = el("button", null, "Reader");
      const rawBtn = el("button", null, "Raw JSON");
      readerBtn.type = "button";
      rawBtn.type = "button";
      const viewWrap = el("div");

      function showReader() {{
        readerBtn.classList.add("active");
        readerBtn.setAttribute("aria-pressed", "true");
        rawBtn.classList.remove("active");
        rawBtn.setAttribute("aria-pressed", "false");
        clearChildren(viewWrap);
        if (name === "picks.json") renderPicksReader(viewWrap, parsed);
        else if (name === "routing.json") renderRoutingReader(viewWrap, parsed);
        else if (name === "topics.json") renderTopicsReader(viewWrap, parsed);
      }}
      function showRaw() {{
        rawBtn.classList.add("active");
        rawBtn.setAttribute("aria-pressed", "true");
        readerBtn.classList.remove("active");
        readerBtn.setAttribute("aria-pressed", "false");
        clearChildren(viewWrap);
        const pre = el("pre", "body json-body");
        pre.appendChild(highlightJson(JSON.stringify(parsed, null, 2)));
        viewWrap.appendChild(pre);
      }}

      readerBtn.addEventListener("click", showReader);
      rawBtn.addEventListener("click", showRaw);
      if (readerSupported) toggleWrap.appendChild(readerBtn);
      toggleWrap.appendChild(rawBtn);
      target.appendChild(toggleWrap);
      target.appendChild(viewWrap);
      // Wrong-shape JSON (or a non-Reader .json) defaults to the Raw view so the
      // user sees the actual (corrupt) content instead of a blank or fake cards.
      if (readerSupported) showReader();
      else showRaw();
    }}

    function clearChildren(node) {{ while (node.firstChild) node.removeChild(node.firstChild); }}

    function isFiniteNumber(value) {{
      // The numeric shape-guard for every value loaded from a state file the user
      // can hand-edit (picks.json margin/count, routing.json overall/n). A bare
      // `typeof x === "number"` ACCEPTS NaN and Infinity — and picks.json /
      // routing.json are inlined as RAW TEXT (NOT re-serialized through
      // _finite_json_safe like topics.json), so a poisoned `"margin": 1e400`
      // survives JS JSON.parse as `Infinity` and `value.toFixed(2)` paints the
      // literal "Infinity" / "-Infinity" / "NaN" into the pick badge ("margin
      // Infinity · n=Infinity"). This is the JS analog of utils.finite_float_or_none
      // (#304): require a FINITE number or treat the field as absent so the render
      // degrades to "—"/hidden instead of leaking a non-finite token.
      return typeof value === "number" && isFinite(value);
    }}

    function trustBadgeClass(margin) {{
      // Color by the SAME gate ask routes on: below WINNER_MARGIN_FLOOR the basin
      // is a near-tie ask won't route (falls to kNN) → "low" (red). At/above it the
      // basin routes; >=0.5 is decisive ("high"), else modest ("med"). The old
      // hardcoded 0.4/0.7 cuts predated #298/#299 and painted confidently-routed
      // basins (margin 0.15-0.4, ~half the real picks) red as if they didn't route.
      if (!isFiniteNumber(margin)) return "";
      if (margin < WINNER_MARGIN_FLOOR) return "low";
      if (margin >= 0.5) return "high";
      return "med";
    }}

    function fmtMargin(value) {{
      // Render a picks.json margin to 2dp matching EVERY Python surface that
      // touches the same file — the CLI `consolidate` line (cortex.py Python 2dp
      // format) and the launchpad routing card (pre-formatted server-side via
      // _fmt_score). JS `Number.toFixed(2)` rounds half-UP while Python's float
      // format rounds half-to-EVEN, so a 0.625 margin painted "0.63" here while
      // the CLI printed "0.62" — same picks.json, two numbers across surfaces
      // (the cross-language rounding class _fmt_score closed for eval scores).
      // Re-deriving Python's binary-correct rounding in JS arithmetic is not
      // reliable (toFixed/toString hide whether the binary value sits above or
      // below the decimal half), so we don't: the server pre-formats each
      // margin into MARGIN_FMT (keyed by the raw value's repr) and we render
      // that string verbatim. Falls back to toFixed only for a value the server
      // didn't pre-format (legacy inlined data) — still correct off the tie grid.
      // isFiniteNumber (not bare typeof) rejects a non-finite margin so a poisoned
      // picks.json 1e400 can't fall through to value.toFixed(2) === "Infinity".
      if (!isFiniteNumber(value)) return "";
      const k = (Math.round(value * 1000) / 1000).toString();
      const pre = (MARGIN_FMT && Object.prototype.hasOwnProperty.call(MARGIN_FMT, k))
        ? MARGIN_FMT[k] : null;
      return (pre !== null && pre !== undefined) ? pre : value.toFixed(2);
    }}

    // First-timer gloss for the bare "margin 0.42" badge. The number is the
    // routing-confidence proxy — how decisively the chairman's winner beat the
    // runner-up across this basin's councils — but a first-timer reading
    // "margin 0.42" on the picks scoreboard has NO way to tell what 0.42 means,
    // which direction is better, or that it gates whether `ask` actually routes
    // on this pick. The launchpad cheat-sheet already glosses the identical
    // value (margin-over-runner-up + the gate); the viewer's picks Reader badge
    // and the topology basin-detail xlink were the un-glossed siblings. One
    // helper feeds the :title at both sites so the explanation can't drift.
    // Returns "" for a non-number so the badge title stays absent on bad data.
    function marginGloss(margin) {{
      if (!isFiniteNumber(margin)) return "";
      const decisive = margin < WINNER_MARGIN_FLOOR
        ? "Below the " + fmtMargin(WINNER_MARGIN_FLOOR) + " routing gate — a near-tie, so ask falls back to kNN here until more councils sharpen it."
        : (margin >= 0.5
            ? "A decisive lead, so ask routes this pick directly."
            : "Above the " + fmtMargin(WINNER_MARGIN_FLOOR) + " routing gate, so ask routes this pick.");
      return "Margin = how decisively the chairman's winner beat the runner-up across this basin's councils (0 = coin-flip, 1 = unanimous). " + decisive;
    }}

    // Web-era capture slugs (chatgpt / claude_ai / gemini) fold to the CLI
    // dispatch slugs (codex / claude / antigravity). A picks.json consolidated
    // before the #249/#260 outcome-slug canon can still carry a web-era
    // `routing_rule.primary` ("chatgpt"); cortex canonicalizes it at LOAD for
    // `ask`, but THIS reader reads the raw inlined file, so without folding here
    // a stale "Use chatgpt" leaks while every other surface shows "codex"
    // (symmetric to the eval-leaderboard display canon, #292). Mirrors
    // council_schema.normalize_provider_slug for the web-era set. The brand
    // display (codex → GPT) is a separate, founder-gated call (#275).
    function canonProviderSlug(slug) {{
      if (typeof slug !== "string") return slug;
      // Map literal lives INSIDE the function: this is a hoisted function
      // declaration callable during the initial render, before any top-level
      // `const` initializer line would have run (TDZ) — a function-local const
      // sidesteps that entirely.
      // FULL mirror of Python council_schema._LEGACY_PROVIDER_ALIASES — not a
      // subset. The old {{chatgpt,gpt,claude_ai,gemini}} silently diverged from
      // Python AND the launchpad cheat-sheet on google/bard/anthropic/claude.ai:
      // a picks.json winner of "google" branded "Google" here while Python (and
      // the launchpad) both branded it "Gemini". Same value, two readers, two
      // answers — folded onto one alias set so the picks reader can't drift.
      const webEra = {{
        gemini: "antigravity", google: "antigravity", bard: "antigravity",
        chatgpt: "codex", openai: "codex", gpt: "codex",
        claude_ai: "claude", "claude.ai": "claude", anthropic: "claude",
      }};
      return webEra[slug.toLowerCase()] || slug;
    }}

    // DISPLAY brand for a provider slug — the MODEL trio (Claude / GPT / Gemini),
    // single-sourced to match council_schema.provider_model_brand AND the
    // launchpad's formatProviderLabel. #275 raw-slug-vs-brand: the picks Reader's
    // "Use <X>" and the topology basin detail's "Routes to <X>" are USER-FACING
    // routing recommendations — the SAME picks.json winner the launchpad cheat-
    // sheet already brands "GPT"/"Gemini". Reading them as the raw dispatch slug
    // (codex/antigravity) was the leftover #275 sibling (the founder call landed
    // 2026-06-06; the eval-leaderboard judge sibling closed alongside). Folds
    // web-era slugs first (chatgpt/gemini → codex/antigravity) so a pre-canon
    // picks.json brands correctly too. The raw slug stays in canonProviderSlug
    // for the picks↔topology IDENTITY bridge — only the DISPLAY brands.
    function providerBrand(slug) {{
      // STRING shape-guard for a slug loaded from a state file the user can hand-
      // edit (picks.json `winner`, routing.json `best_per_task_type` value). These
      // are inlined as RAW TEXT and JSON.parse'd client-side, so a poisoned/half-
      // migrated entry can carry a NUMBER (`"winner": 42`), an OBJECT
      // (`{{"primary": "claude"}}`), null, or an array where a slug string is
      // expected. The old `if (typeof canon !== "string") return canon;` PASSED
      // THE NON-STRING THROUGH — the caller then did "Use " + 42 ("Use 42") or
      // "Use " + {{}} ("Use [object Object]"). Coerce to "" for any non-string /
      // empty input (the string analog of isFiniteNumber); callers treat an empty
      // brand as ABSENT (no "Use X" chip / a "—" Best cell), mirroring the
      // launchpad formatProviderLabel's `if (!provider) return ''`.
      if (typeof slug !== "string" || !slug.trim()) return "";
      const canon = canonProviderSlug(slug);
      if (typeof canon !== "string") return "";
      const labels = {{ claude: "Claude", codex: "GPT", antigravity: "Gemini", openai: "GPT", mlx: "MLX" }};
      const key = canon.trim().toLowerCase();
      if (labels[key]) return labels[key];
      return key
        .split(/[_\\s-]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    }}

    function humanizeTaskType(taskType) {{
      // routing.json keys by the RAW snake_case task_type enum
      // (code_generation, data_analysis, cowork_general). The launchpad
      // routing cheat-sheet — the deep-link SOURCE that links INTO this
      // table via `?file=routing.json&task=<task_type>` — already renders
      // the SAME value title-cased ("Code Generation"). Showing the raw
      // snake_case enum here is the un-humanized sibling (the #165
      // picks.json + #191 unified-review-page snake_case-enum-in-prose
      // class): a user who clicks "Data Analysis" on the launchpad lands
      // on a row labelled `data_analysis`. Mirror the launchpad transform
      // EXACTLY so the two surfaces agree on the same data.
      // String shape-guard (sibling of providerBrand): the keys this is called on
      // are always strings (Object.keys / a URL param), but a non-string must
      // degrade to "" not paint a raw number/[object Object] should a future
      // caller pass a disk-loaded value.
      if (typeof taskType !== "string") return "";
      return taskType
        .replace(/_/g, " ")
        .split(" ")
        .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
        .join(" ");
    }}

    function renderPicksReader(target, picks) {{
      // picks.json shape (post-collapse #298): the flat lens-basin tally
      // {{basin_id: {{winner, count, margin, n_episodes, evidence}}}}. A legacy
      // RoutingPattern dict (routing_rule/trust_score/...) is rendered
      // best-effort via the same fields where they overlap.
      const basins = Object.keys(picks);
      if (basins.length === 0) {{
        target.appendChild(el("p", "meta", "No picks yet — run trinity-local consolidate."));
        return;
      }}
      // Cross-memory bridge: picks → topology via the shared centroid
      // match. taskToBasinId[task_type] = topology basin id (e.g. b00).
      // Used to render the "View in topology →" xlink per pick card,
      // completing the bidirectional bridge tick #30 opened. basinLabels
      // (tick #39) feeds richer hover text on the topology chip.
      const {{ taskToBasinId, basinLabels, basinNames }} = loadCrossMemoryMaps();
      // Deep-link target: ?task=<basin_id> scrolls to + highlights the
      // matching card so cross-links from routing.json land usefully.
      // If the task isn't yet in picks (cortex hasn't consolidated this
      // basin), surface a small "not yet" banner — same warm-warning
      // shape as the per-file health banner — so the user understands
      // the link landed but the data isn't there.
      const focusTask = params.get("task");
      if (focusTask && !basins.includes(focusTask)) {{
        const banner = el("div", "viewer-health-banner");
        banner.appendChild(el("span", "viewer-health-status", "not yet"));
        const hint = el("span", "viewer-health-hint");
        hint.textContent =
          'No pick for "' + focusTask + '" yet — this task hasn\\'t been ' +
          'consolidated. Run trinity-local consolidate to add it.';
        banner.appendChild(hint);
        const chip = el("button", "viewer-health-cmd");
        chip.type = "button";
        chip.textContent = "trinity-local consolidate";
        chip.title = "Copy: trinity-local consolidate";
        chip.addEventListener("click", () => {{
          if (navigator.clipboard?.writeText) {{
            navigator.clipboard.writeText("trinity-local consolidate").catch(() => null);
          }}
          chip.textContent = "✓ Copied";
          announceCopy("Copied: trinity-local consolidate");
          setTimeout(() => {{ chip.textContent = "trinity-local consolidate"; }}, 2200);
        }});
        banner.appendChild(chip);
        target.appendChild(banner);
      }}
      basins.forEach(basinId => {{
        const p = picks[basinId];
        const card = el("div", "pick-card");
        card.dataset.task = basinId;
        if (focusTask === basinId) card.classList.add("pick-card-focused");
        const head = el("div", "pick-head");
        head.appendChild(el("span", "pick-basin", basinId));
        // Human-readable basin name (the topology `label` / top-terms) so the
        // user can tell WHAT kind of question this pick is for — an opaque
        // "b00" alone is meaningless on the routing scoreboard. Rendered only
        // when topics.json carries a name for this basin (a pick whose source
        // basin no longer exists just shows the id, same as the topology xlink).
        const basinName = basinNames.get(basinId);
        if (basinName) {{
          head.appendChild(el("span", "pick-basin-name", basinName));
        }}
        // Margin is the confidence proxy (how decisively the winner beat the
        // runner-up); fall back to a legacy trust_score.value when present.
        const trust = p.trust_score || {{}};
        const mval = isFiniteNumber(p.margin)
          ? p.margin
          : (isFiniteNumber(trust.value) ? trust.value : null);
        // A near-tie below the routing gate isn't a pick ask uses — it falls to
        // kNN. Say "Lean X" not "Use X" so the primary doesn't overclaim a route.
        const advisory = (mval !== null) && (mval < WINNER_MARGIN_FLOOR);
        // Post-collapse the winner is the top-level field; a legacy
        // RoutingPattern dict carried it under routing_rule.primary.
        const winner = p.winner || (p.routing_rule && p.routing_rule.primary) || null;
        // Gate on the BRANDED string, not the raw field: a corrupt non-string
        // winner (42 / {{}} / []) is truthy but providerBrand now coerces it to ""
        // — so "Use 42" / "Use [object Object]" no longer paints (no chip at all,
        // same as a missing winner). Clean slugs are unaffected.
        const winnerBrand = providerBrand(winner);
        if (winnerBrand) {{
          head.appendChild(el("span", "pick-primary",
            (advisory ? "Lean " : "Use ") + winnerBrand));
        }}
        if (mval !== null) {{
          const badge = el("span", "pick-badge " + trustBadgeClass(mval),
            "margin " + fmtMargin(mval) + (advisory ? " · near-tie" : ""));
          // Gloss the bare number so a first-timer learns what 0.42 means + which
          // way is better (the badge color alone can't say). Same value the
          // launchpad cheat-sheet already glosses.
          badge.title = marginGloss(mval);
          head.appendChild(badge);
        }}
        const n = isFiniteNumber(p.count) ? p.count
          : (isFiniteNumber(p.n_episodes) ? p.n_episodes : null);
        if (n !== null) {{
          head.appendChild(el("span", "pick-badge", "n=" + n));
        }}
        card.appendChild(head);
        // Action row holds the cross-memory links for this basin.
        const actions = el("div", "pick-actions");
        // NO picks→routing link post-#298: picks.json keys by basin_id (b00..)
        // while routing.json keys by task_type — different key spaces. A basin
        // spans MANY task_types, so it has no single routing row; a
        // `routing.json&task=<basin_id>` link landed every card on a dead "No
        // routing data" banner (verified in-browser). The valid cross-refs are
        // the evidence councils (in the head, above) + the topology basin
        // (below, both basin-keyed).
        // Cross-memory link: jump to the topology basin this pick was
        // extracted from. Centroid-matched (see loadCrossMemoryMaps).
        // Only renders when a match cleared SIM_THRESHOLD — orphan picks
        // (those whose source basin no longer exists in topics.json)
        // get no chip rather than a broken link.
        const topologyBasinId = taskToBasinId.get(basinId);
        if (topologyBasinId) {{
          const tlink = el("a", "pick-xlink", "View in topology →");
          tlink.href = "memory.html?file=topics.json&basin=" + encodeURIComponent(topologyBasinId);
          tlink.title = basinHoverTitle(topologyBasinId, basinLabels);
          actions.appendChild(tlink);
        }}
        card.appendChild(actions);
        target.appendChild(card);
      }});

      // Scroll focused card into view after the DOM settles.
      if (focusTask) {{
        const focused = target.querySelector(".pick-card-focused");
        if (focused) setTimeout(() => focused.scrollIntoView({{block: "center", behavior: "smooth"}}), 100);
      }}
    }}

    function renderRoutingReader(target, routing) {{
      // routing.json shape: {{by_task_type: {{task: {{provider: {{n, overall}}}}}},
      //   best_per_task_type: {{task: provider}}, computed_at: iso}}
      const by = routing.by_task_type || {{}};
      const best = routing.best_per_task_type || {{}};
      // Task types whose "best" is a tie / coin-flip (no strict chairman-win
      // lead). Same green-gate demotion as the launchpad cheat-sheet: a 2-2 or
      // 1-1-1 split must not render a confident "Best" — say "X · tied".
      const tie = routing.pick_is_tie || {{}};
      const allTaskTypes = Object.keys(by);
      if (allTaskTypes.length === 0) {{
        target.appendChild(el("p", "meta", "No routing data yet — run a few councils to populate it."));
        return;
      }}
      const focusTask = params.get("task");

      // A task_type earns a row only if it carries real signal: some provider
      // with a track record (n >= 2), OR it's the deep-link target. With n=1 a
      // "score" is a single council — noise that buries the signal (the
      // founder's home: 349 of 424 task_types are n=1-only → a 22k-px wall of
      // "(n=1)" rows). Same n>=2 floor as the launchpad routing cheat-sheet
      // (#290); the Raw JSON toggle still shows the full table. (No
      // task_type→basin bridge clause post-#298 — picks/topology key by
      // basin_id, routing by task_type; the old bridge was a phantom.)
      const MIN_N = 2;
      const maxN = (t) => Math.max(0, ...Object.values(by[t] || {{}})
        .map(s => (s && isFiniteNumber(s.n)) ? s.n : 0));
      const taskTypes = allTaskTypes.filter(t =>
        maxN(t) >= MIN_N || t === focusTask
      ).sort((a, b) => (maxN(b) - maxN(a)) || a.localeCompare(b));
      const hiddenCount = allTaskTypes.length - taskTypes.length;

      // Routing data EXISTS but EVERY task_type is a single council (n=1) — the
      // founder's real shape (349 of 424 task_types n=1-only). The early-return
      // above only catches a TRULY empty file; this catches "all rows are sub-
      // floor noise". Without it the table renders with a header row ("Task type
      // | Best") and ZERO body rows — a header-only ghost table that reads as
      // broken/loading, with the real "all single-council" explanation buried in
      // a meta note below it. Show the honest empty-state instead (mirrors the
      // picks reader's "No picks yet" + the launchpad cheat-sheet sibling).
      if (taskTypes.length === 0) {{
        const msg = el("p", "meta",
          "No routing pattern yet — all " + allTaskTypes.length + " task type" +
          (allTaskTypes.length === 1 ? " has" : "s have") + " just a single " +
          "council each (n=1 isn't a track record). Run more councils, then " +
          "trinity-local consolidate. Switch to Raw JSON to see every entry.");
        target.appendChild(msg);
        return;
      }}

      const providers = new Set();
      taskTypes.forEach(t => Object.keys(by[t] || {{}}).forEach(p => providers.add(p)));
      const provList = Array.from(providers).sort();

      const tbl = el("table", "routing-table");
      const thead = el("thead");
      const hr = el("tr");
      hr.appendChild(el("th", null, "Task type"));
      // #275: column headers read the MODEL BRAND (Claude / GPT / Gemini), the
      // same data the launchpad routing table already brands — a raw "codex"/
      // "antigravity" header here was the un-branded sibling.
      provList.forEach(p => hr.appendChild(el("th", null, providerBrand(p))));
      hr.appendChild(el("th", null, "Best"));
      thead.appendChild(hr);
      tbl.appendChild(thead);

      // Deep-link target: ?task=<task_type> scrolls to + highlights the
      // matching row so a click from picks.json lands usefully. The "not yet"
      // banner fires only when the task is genuinely absent from routing
      // (checked against allTaskTypes — a singleton focus row is KEPT visible
      // by the filter above, so it must not trip this banner).
      if (focusTask && !allTaskTypes.includes(focusTask)) {{
        const banner = el("div", "viewer-health-banner");
        banner.appendChild(el("span", "viewer-health-status", "not yet"));
        const hint = el("span", "viewer-health-hint");
        hint.textContent =
          'No routing data for "' + humanizeTaskType(focusTask) + '" yet — no councils for this ' +
          'task. Run a council on it, then trinity-local consolidate.';
        banner.appendChild(hint);
        target.appendChild(banner);
      }}

      const tbody = el("tbody");
      taskTypes.forEach(t => {{
        const tr = el("tr");
        tr.dataset.task = t;
        if (focusTask === t) tr.classList.add("routing-row-focused");
        // Task name is plain text. Post-#298 routing.json (keyed by task_type)
        // has NO bridge to picks/topology (keyed by basin_id): there is no
        // stored task_type→basin map, and the viewer can't embed client-side to
        // derive one. Both cross-link directions resolved to dead "No pick / No
        // routing data" banners (verified in-browser), so routing.json stands
        // alone as the per-task-type provider track record. (A useful bridge
        // would need `consolidate` to emit task_type→basin server-side — a
        // feature, not a viewer fix; founder call.)
        // Display the HUMANIZED task name (matches the launchpad routing
        // card's title-case); the RAW snake_case stays in tr.dataset.task
        // (the deep-link key + focus matcher), so the cross-link bridge and
        // the `?task=` highlight keep working unchanged.
        const taskTd = el("td");
        taskTd.appendChild(document.createTextNode(humanizeTaskType(t)));
        tr.appendChild(taskTd);
        const row = by[t] || {{}};
        provList.forEach(p => {{
          const cell = row[p];
          const td = el("td", "score");
          if (cell && isFiniteNumber(cell.overall)) {{
            const txt = cell.overall.toFixed(1) + (isFiniteNumber(cell.n) ? " (n=" + cell.n + ")" : "");
            td.textContent = txt;
            // Only highlight the winning cell when it's a CONFIDENT pick — a
            // tie has no winner to bold (green-gate #35).
            if (best[t] === p && !tie[t]) td.classList.add("best");
          }} else {{
            td.textContent = "—";
          }}
          tr.appendChild(td);
        }});
        // "Best" column: confident pick → bare provider; tie → "X · tied" so
        // a coin-flip isn't painted as a clear winner (green-gate #35, mirrors
        // the launchpad cheat-sheet "Lean · no clear pick").
        const bestTd = el("td");
        // #275: the Best column names the winning MODEL by BRAND, matching the
        // branded column headers above + the launchpad cheat-sheet. Gate on the
        // BRANDED string: a corrupt non-string best_per_task_type value (99 /
        // {{slug:…}}) is truthy but providerBrand coerces it to "" — so "99" /
        // "[object Object]" no longer paints the Best cell; it degrades to "—".
        const bestBrand = providerBrand(best[t]);
        if (bestBrand && tie[t]) {{
          bestTd.textContent = bestBrand + " · tied";
          bestTd.title = "The chairman split evenly here — no clear pick yet.";
          bestTd.style.opacity = "0.7";
        }} else {{
          bestTd.textContent = bestBrand || "—";
        }}
        tr.appendChild(bestTd);
        tbody.appendChild(tr);
      }});
      tbl.appendChild(tbody);
      target.appendChild(tbl);
      if (hiddenCount > 0) {{
        target.appendChild(el("p", "meta",
          hiddenCount + " single-sample task type" + (hiddenCount === 1 ? "" : "s") +
          " hidden (n=1, one council each — not yet a track record). " +
          "Switch to Raw JSON for the full table."));
      }}
      if (routing.computed_at) {{
        target.appendChild(el("p", "meta", "Computed " + routing.computed_at));
      }}
      // Scroll focused row into view.
      if (focusTask) {{
        const focused = target.querySelector(".routing-row-focused");
        if (focused) setTimeout(() => focused.scrollIntoView({{block: "center", behavior: "smooth"}}), 100);
      }}
    }}

    function renderTopicsReader(target, topics) {{
      // topics.json shape: {{basins: [{{id, size, top_terms, centroid, prompt_ids}}]}}
      // We visualize basins as a force-directed graph (Obsidian-style).
      // Nodes = basins (size by basin.size), edges = centroid cosine
      // similarity, force layout pulls related topics together so the
      // user can SEE which subjects cluster vs. which sit alone.
      const basins = Array.isArray(topics.basins) ? topics.basins.slice() : [];
      if (basins.length === 0) {{
        target.appendChild(el("p", "meta", "No topics yet — run trinity-local lens to compute basins."));
        return;
      }}

      // Cross-memory bridge: picks ↔ topology. Post-#298 picks.json keys by
      // the lens basin id (same id space topics.json uses), so this is a plain
      // identity match (see matchBasinsToPicks). basinIdToPick carries the
      // winner+margin so the detail can show the routing pick inline.
      const {{ basinIdToTask: basinToPickTask, basinIdToPick }} = loadCrossMemoryMaps();

      // Detail panel above the graph — populated on node click.
      const detail = el("div", "topics-graph-detail");
      const detailEmpty = el("div", "empty", "Click a basin to see its top terms and prompt count.");
      detail.appendChild(detailEmpty);
      target.appendChild(detail);

      // Graph container — dark canvas + SVG overlay for the force layout.
      const graphWrap = el("div", "topics-graph-wrap");
      const svgNS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("class", "topics-graph-svg");
      svg.setAttribute("viewBox", "0 0 1000 520");
      svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
      graphWrap.appendChild(svg);
      // Pointer-aware gesture hint: "scroll to zoom" is a MOUSE-WHEEL gesture —
      // on a touch phone (the common consumer device) scrolling pans the PAGE,
      // never the graph, so the instruction was actively wrong and a touch user
      // would never discover the working gesture (d3-zoom v3 DOES handle a
      // two-finger pinch on touch — verified — it just isn't "scroll"). Detect a
      // coarse pointer and name the gesture the user can actually perform:
      // pinch-to-zoom + tap-for-detail on touch, scroll + click on mouse. "Drag
      // nodes" works on both. matchMedia('(pointer: coarse)') is the standard
      // touch-primary test; falls back to scroll/click when unsupported.
      const coarsePointer =
        typeof window.matchMedia === "function" &&
        window.matchMedia("(pointer: coarse)").matches;
      const gestureHint = coarsePointer
        ? "Drag nodes · pinch to zoom · tap for detail"
        : "Drag nodes · scroll to zoom · click or Tab + Enter for detail";
      const hint = el("div", "topics-graph-hint", gestureHint);
      graphWrap.appendChild(hint);
      target.appendChild(graphWrap);

      if (!window.d3 || !window.d3.forceSimulation) {{
        graphWrap.removeChild(svg);
        graphWrap.appendChild(el("div", "topics-graph-hint",
          "Graph library not loaded — try the Raw JSON view."));
        return;
      }}

      const W = 1000, H = 520;
      // Cosine similarity over the centroid embeddings. Centroids are
      // 768-d (Nomic). 20 basins → 190 pairs → trivial.
      function cosine(a, b) {{
        if (!a || !b || a.length !== b.length) return 0;
        let dot = 0, na = 0, nb = 0;
        for (let i = 0; i < a.length; i++) {{
          const x = a[i], y = b[i];
          dot += x * y; na += x * x; nb += y * y;
        }}
        const denom = Math.sqrt(na) * Math.sqrt(nb);
        return denom > 0 ? dot / denom : 0;
      }}

      // Label = first 4 words of the top representative prompt — the
      // closest-to-centroid prompt is the most semantically central thing
      // the user actually asked, so its opening words tell you what this
      // basin is *about*. Falls back to TF-IDF top_terms when representatives
      // haven't been written yet (legacy topics.json files from before the
      // representatives feature shipped — those clear on the next lens-build).
      // Greetings + short ack words that should NOT label a basin (mirror
      // of _LABEL_GREETINGS in src/trinity_local/me/basins.py — kept in
      // sync because old on-disk topics.json files from before the Python
      // labeler shipped have empty `label`; this JS fallback rescues them
      // at render-time without forcing a lens-rebuild).
      const _LABEL_GREETINGS = new Set([
        "hi","hello","hey","yo","sup","thanks","thank you","ok","okay",
        "yes","no","sure","got it","cool","nice","great","awesome",
        "continue","go on","next","more","again","?","??","..."
      ]);
      function _pickSubstantiveSnippet(reps) {{
        // Same algorithm as Python _pick_label_snippet: longest multi-word
        // non-greeting turn across the top-5 reps. Returns "" when nothing
        // qualifies (caller falls through to top_terms).
        let best = "";
        const cap = Math.min(reps.length, 5);
        for (let i = 0; i < cap; i++) {{
          const rep = reps[i] || {{}};
          const candidates = [rep.headline || ""];
          for (const t of (rep.turns || [])) {{
            const s = (t.snippet || "").trim();
            if (s && !candidates.includes(s)) candidates.push(s);
          }}
          for (const cand of candidates) {{
            const trimmed = (cand || "").trim();
            if (!trimmed) continue;
            const norm = trimmed.toLowerCase().replace(/[?.!,;:'\" ]+$/, "");
            if (_LABEL_GREETINGS.has(norm)) continue;
            if (trimmed.split(/\\s+/).length < 3) continue;
            if (trimmed.length > best.length) best = trimmed;
          }}
        }}
        return best;
      }}
      function labelFor(b) {{
        // Prefer the Python-computed semantic label when present (new
        // basins). Fall through to the JS picker (same heuristic) for
        // legacy basins where `label` was never written.
        const truncate = (s) => s.length > 36 ? s.slice(0, 36) + "…" : s;
        if (b.label) return truncate(b.label);
        const reps = Array.isArray(b.representatives) ? b.representatives : [];
        const picked = _pickSubstantiveSnippet(reps);
        if (picked) {{
          const words = picked.split(/\\s+/).slice(0, 6).join(" ");
          return truncate(words);
        }}
        return (b.top_terms && b.top_terms[0]) || b.id || "?";
      }}
      function tooltipFor(b) {{
        // Hover tooltip prefers the full (non-truncated) label when present;
        // legacy basins use the substantive picker; finally top_terms.
        if (b.label) return b.label;
        const reps = Array.isArray(b.representatives) ? b.representatives : [];
        const picked = _pickSubstantiveSnippet(reps);
        if (picked) return picked;
        return (b.top_terms || []).join(", ");
      }}
      const nodes = basins.map((b, i) => ({{
        id: b.id || ("b" + i),
        basin: b,
        size: b.size || 0,
        label: labelFor(b),
        tooltip: tooltipFor(b),
      }}));
      const sizeMax = nodes.reduce((m, n) => Math.max(m, n.size), 1);
      const sizeMin = nodes.reduce((m, n) => Math.min(m, n.size), sizeMax);
      // Node radius: sqrt-scale (so a basin 100x bigger is 10x wider, not 100x).
      function radiusFor(n) {{
        const t = Math.sqrt(Math.max(1, n.size) / sizeMax);
        return 10 + 32 * t;
      }}

      // Build edges: every pair with cosine > threshold. We tune the
      // threshold so each node gets ~3-5 neighbors on average — that's
      // the visual sweet spot. With 20 basins that means ~50 edges.
      const allPairs = [];
      for (let i = 0; i < basins.length; i++) {{
        for (let j = i + 1; j < basins.length; j++) {{
          const sim = cosine(basins[i].centroid, basins[j].centroid);
          allPairs.push({{ source: i, target: j, sim }});
        }}
      }}
      // Pick a similarity threshold so we keep the top ~3*n edges.
      const targetEdgeCount = Math.min(allPairs.length, nodes.length * 3);
      // Similarity DESC, then (source, target) index ASC as a stable tie-break
      // so two edges with an equal cosine straddling the slice boundary don't
      // swap which edge survives the top-N cut when the basin order changes.
      allPairs.sort((a, b) => (b.sim - a.sim) || (a.source - b.source) || (a.target - b.target));
      const edges = allPairs.slice(0, targetEdgeCount).map(p => ({{
        source: nodes[p.source].id,
        target: nodes[p.target].id,
        sim: p.sim,
        strong: p.sim > 0.6,
      }}));

      // d3-force simulation. Force config tuned for 20 nodes:
      //   link distance proportional to (1 - sim) so similar basins sit close
      //   charge repels nodes so labels don't overlap
      //   center keeps the whole thing on canvas
      //   x/y gently corral every node toward the middle — forceCenter only
      //     translates the centroid, so without these the weakly-linked
      //     singleton basins (no strong edge to pull them in) fly to the far
      //     corners under the -380 charge, leaving a tiny central blob + a
      //     ring of scattered, clipped satellites. A mild 0.06 spring pulls
      //     them into one coherent, readable cluster without crushing the
      //     link-driven structure.
      //   collide prevents node overlap
      const sim = window.d3.forceSimulation(nodes)
        .force("link", window.d3.forceLink(edges).id(d => d.id)
          .distance(d => 60 + (1 - d.sim) * 220)
          .strength(d => 0.2 + d.sim * 0.6))
        .force("charge", window.d3.forceManyBody().strength(-380))
        .force("center", window.d3.forceCenter(W / 2, H / 2))
        .force("x", window.d3.forceX(W / 2).strength(0.06))
        .force("y", window.d3.forceY(H / 2).strength(0.06))
        .force("collide", window.d3.forceCollide().radius(d => radiusFor(d) + 6).strength(0.9))
        .alpha(1).alphaDecay(0.025);

      // Pre-compute adjacency so click-highlight is O(1) per node.
      const neighborsOf = new Map();
      nodes.forEach(n => neighborsOf.set(n.id, new Set([n.id])));
      edges.forEach(e => {{
        const s = typeof e.source === "object" ? e.source.id : e.source;
        const t = typeof e.target === "object" ? e.target.id : e.target;
        neighborsOf.get(s)?.add(t);
        neighborsOf.get(t)?.add(s);
      }});

      // d3-zoom group — everything else nests inside `viewport` so the
      // zoom transform applies uniformly to links + nodes + labels.
      const d3svg = window.d3.select(svg);
      const viewport = d3svg.append("g").attr("class", "viewport");

      const linkSel = viewport.append("g")
        .selectAll("line")
        .data(edges)
        .join("line")
        .attr("class", d => d.strong ? "link strong" : "link");

      const nodeSel = viewport.append("g")
        .selectAll("circle")
        .data(nodes)
        .join("circle")
        // Mark basins that have crystallized into routing rules — the
        // warm-brown ring (.node.pick-basin) is the visual companion
        // to the "Routing rule: <task> →" chip in the detail panel
        // shipped tick #30.
        .attr("class", d => basinToPickTask.has(d.id) ? "node pick-basin" : "node")
        .attr("r", radiusFor)
        .attr("fill", d => {{
          // Light teal (small) → deep teal (large) by size for hierarchy.
          // Stays in the brand teal hue (#4f9095 accent / #3f777c primary are
          // both hsl(~184, ~30%, ~44%)); varies lightness + saturation for the
          // size ramp instead of sweeping orange→yellow-green off-brand.
          const t = Math.sqrt(d.size / sizeMax);
          const hue = 184;
          const sat = 28 + 12 * t;    // 28% → 40%
          const light = 58 - 26 * t;  // 58% (small, light) → 32% (large, deep)
          return "hsl(" + hue + "," + sat + "%," + light + "%)";
        }})
        .on("click", (event, d) => {{
          event.stopPropagation();  // don't bubble to the svg background
          showDetail(d.basin);
          highlightNeighborhood(d.id);
        }});

      // Keyboard operability (WCAG 2.1.1 + 2.4.7): a d3/SVG <circle> with only
      // an .on("click") is MOUSE-ONLY — a keyboard user can never reach a basin's
      // detail panel, and that panel is the ONLY surface carrying the per-basin
      // "Launch council on this topic" + per-rep "Replay" controls (the Raw JSON
      // view exposes the raw terms but none of these interactive affordances). Make
      // each node a real focusable button: tabindex=0 so Tab reaches it, role=button
      // + aria-label so AT announces it, and an Enter/Space keydown that fires the
      // SAME showDetail + highlightNeighborhood as the click (single behavior, two
      // input paths). A circle in document order is in the focus order; that gives a
      // sane left-to-right-ish traversal of the basins.
      nodeSel
        .attr("tabindex", 0)
        .attr("role", "button")
        .attr("aria-label", d => "Basin " + (d.label || d.id) +
          " — " + d.size + (d.size === 1 ? " prompt" : " prompts") +
          ". Press Enter to see its detail.")
        .on("keydown", (event, d) => {{
          if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {{
            event.preventDefault();
            event.stopPropagation();
            showDetail(d.basin);
            highlightNeighborhood(d.id);
          }}
        }});

      // Native SVG <title> for hover tooltip — first representative or
      // fallback to TF-IDF top terms. Browser renders it natively, no JS.
      // Pick-bearing basins also surface their routing rule in the
      // tooltip so the user can identify them without clicking.
      nodeSel.append("title").text(d => {{
        const pickTask = basinToPickTask.get(d.id);
        return pickTask ? (d.tooltip + "\\n— Routing rule: " + pickTask) : d.tooltip;
      }});

      // Drag re-energizes the sim so the dragged node "pulls" neighbors
      // with it (Obsidian feel).
      nodeSel.call(window.d3.drag()
        .on("start", (event, d) => {{
          if (!event.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        }})
        .on("drag", (event, d) => {{ d.fx = event.x; d.fy = event.y; }})
        .on("end", (event, d) => {{
          if (!event.active) sim.alphaTarget(0);
          d.fx = null; d.fy = null;
        }}));

      const labelSel = viewport.append("g")
        .selectAll("text")
        .data(nodes)
        .join("text")
        .attr("class", "label")
        .attr("font-size", d => 11 + Math.sqrt(d.size / sizeMax) * 9)
        .text(d => d.label);

      // Background click clears the highlight selection.
      d3svg.on("click", () => clearHighlight());

      // d3-zoom: scroll-wheel zoom + click-drag pan over the viewport.
      // Scale clamped 0.5×–4× so the user can't lose the graph by
      // zooming to a single pixel or so far out it vanishes.
      // `zoom` is hoisted so fitToView() (below) can drive the transform; the
      // 0.3 floor (was 0.5) lets a wide graph zoom out far enough to fit.
      let zoom = null;
      // d3-zoom v3's wheel/mousedown handlers call `interrupt(node)` to cancel any
      // pending zoom transition (minified source: `r.interrupt(this)`). That
      // `interrupt` comes from d3-transition — d3-zoom's UMD lists it as a
      // dependency and, in the browser-global build, binds EVERY missing dep to
      // `window.d3`, so the call is literally `window.d3.interrupt(this)`. We
      // vendor d3-selection/force/zoom/interpolate but NOT d3-transition, so
      // `window.d3.interrupt` is undefined → "interrupt is not a function" threw
      // on EVERY user scroll-zoom / pan (browser-found 2026-06-02). On LOAD we
      // already sidestep this (fitToView sets node.__zoom directly — #294), but
      // the load-fix never covered user gestures; this is its interactive sibling.
      // The graph runs a force simulation, never a d3-transition, so a no-op
      // interrupt is correct: there is no transition to cancel.
      if (window.d3 && typeof window.d3.interrupt !== "function") {{
        window.d3.interrupt = function() {{}};
      }}
      if (window.d3.zoom) {{
        zoom = window.d3.zoom()
          .scaleExtent([0.3, 4])
          .on("zoom", (event) => viewport.attr("transform", event.transform));
        d3svg.call(zoom);
      }}

      function highlightNeighborhood(centerId) {{
        const neighbors = neighborsOf.get(centerId) || new Set([centerId]);
        nodeSel.style("opacity", d => neighbors.has(d.id) ? 1 : 0.18);
        labelSel.style("opacity", d => neighbors.has(d.id) ? 1 : 0.18);
        linkSel.style("opacity", d => {{
          const s = typeof d.source === "object" ? d.source.id : d.source;
          const t = typeof d.target === "object" ? d.target.id : d.target;
          return (s === centerId || t === centerId) ? 1 : 0.05;
        }});
      }}
      function clearHighlight() {{
        nodeSel.style("opacity", 1);
        labelSel.style("opacity", 1);
        linkSel.style("opacity", null);  // back to CSS-defined stroke alpha
      }}

      function renderTick() {{
        linkSel
          .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
        nodeSel.attr("cx", d => d.x).attr("cy", d => d.y);
        labelSel.attr("x", d => d.x).attr("y", d => d.y + radiusFor(d) + 14);
      }}
      sim.on("tick", renderTick);

      // Zoom-to-fit on settle. forceCenter only pins the CENTROID, so with many
      // basins the force layout spills well past the viewport (on a 48-basin
      // corpus ~half the nodes rendered off-screen on the default view — you
      // opened your "domains" map and saw half of it, clipped). After the
      // simulation settles, frame the whole node bounding box, then let the user
      // pan/scroll to drill in. Fires once: detach after the first settle so a
      // later drag-induced "end" doesn't yank the view back.
      let fitted = false;
      function fitToView() {{
        if (fitted || !zoom || !nodes.length) return;
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (const n of nodes) {{
          if (typeof n.x !== "number" || typeof n.y !== "number") return;  // not settled
          const r = radiusFor(n);
          if (n.x - r < minX) minX = n.x - r;
          if (n.x + r > maxX) maxX = n.x + r;
          if (n.y - r < minY) minY = n.y - r;
          if (n.y + r > maxY) maxY = n.y + r;
        }}
        const bw = maxX - minX, bh = maxY - minY;
        if (bw <= 0 || bh <= 0) return;
        const scale = Math.max(0.3, Math.min(4, 0.9 * Math.min(W / bw, H / bh)));
        const tx = W / 2 - scale * (minX + bw / 2);
        const ty = H / 2 - scale * (minY + bh / 2);
        const t = window.d3.zoomIdentity.translate(tx, ty).scale(scale);
        // Apply WITHOUT zoom.transform(): that path calls selection.interrupt(),
        // which lives in d3-transition — and we vendor d3-selection + d3-force +
        // d3-zoom + d3-interpolate but NOT d3-transition — so it throws
        // "i.interrupt is not a function" and silently fails to apply. Set the
        // zoom's stored transform on the node + apply it to the viewport
        // directly; subsequent pan/scroll gestures resume from this fitted state.
        const node = d3svg.node();
        if (node) node.__zoom = t;
        viewport.attr("transform", t);
        fitted = true;
      }}
      sim.on("end", fitToView);

      // WCAG 2.3.3 (Animation from Interactions / vestibular safety): the force
      // simulation animates EVERY node for ~273 ticks (~4.5s, alpha 1 → alphaMin
      // 0.001 at alphaDecay 0.025) on load — the longest, largest motion in the
      // whole viewer. This is a JS rAF-driven tick loop, NOT a CSS animation, so
      // the SHARED-CSS `@media (prefers-reduced-motion: reduce)` global zero (which
      // the viewer doesn't even include) could never reach it — d3 moves nodes via
      // `.attr("cx", …)` per tick. When the OS asks for reduced motion, settle the
      // layout INSTANTLY: stop the timer, run every tick synchronously off-screen
      // so alpha decays to the same resting state, then paint the final layout once
      // and fit. The SAME final graph renders — just with zero visible node motion.
      // (Placed AFTER fitToView's declaration: it closes over the `let fitted`
      // binding, so calling it earlier would hit the temporal dead zone.)
      const reduceMotion =
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduceMotion) {{
        sim.stop();
        // ceil(log(alphaMin) / log(1 - alphaDecay)) ticks drains alpha below the
        // 0.001 stop threshold — the exact iteration count d3 would have animated.
        const ticks = Math.ceil(
          Math.log(sim.alphaMin()) / Math.log(1 - sim.alphaDecay()));
        for (let i = 0; i < ticks; i++) sim.tick();
        renderTick();   // paint the settled positions in a single frame
        fitToView();    // frame the whole graph (sim.on("end") won't fire now)
      }}

      // Deep-link target: ?basin=<id> auto-opens the matching basin's
      // detail panel + highlights its neighborhood. Used by the picks
      // Reader's "View in topology →" xlink + the lens-card chips.
      // Stale-basin case (tick #40): when the requested id doesn't
      // match any node, surface a small "not yet" banner inside the
      // detail panel — same warm-warning shape the picks Reader uses
      // when ?task= points at an unconsolidated task. Lens cards
      // reference basin ids from the lens-build run that produced
      // them; if topology has since been rebuilt with different ids,
      // the link lands but the user otherwise has no feedback.
      const focusBasin = params.get("basin");
      if (focusBasin) {{
        const match = nodes.find(n => n.id === focusBasin);
        if (match) {{
          showDetail(match.basin);
          highlightNeighborhood(match.id);
        }} else {{
          // Replace the "click a basin" empty-state with a stale notice.
          clearChildren(detail);
          const banner = el("div", "viewer-health-banner");
          banner.appendChild(el("span", "viewer-health-status", "not found"));
          const hint = el("span", "viewer-health-hint");
          hint.textContent =
            'Basin "' + focusBasin + '" not in the current topology — ' +
            'this link may reference a stale lens build. Rebuild via ' +
            'trinity-local lens.';
          banner.appendChild(hint);
          const chip = el("button", "viewer-health-cmd");
          chip.type = "button";
          chip.textContent = "trinity-local lens";
          chip.title = "Copy: trinity-local lens";
          chip.addEventListener("click", () => {{
            if (navigator.clipboard?.writeText) {{
              navigator.clipboard.writeText("trinity-local lens").catch(() => null);
            }}
            chip.textContent = "✓ Copied";
            announceCopy("Copied: trinity-local lens");
            setTimeout(() => {{ chip.textContent = "trinity-local lens"; }}, 2200);
          }});
          banner.appendChild(chip);
          detail.appendChild(banner);
        }}
      }}

      // Bash-safe quoting for `--task "<text>"` clipboard payloads.
      // Used by both the basin-level and per-rep launch chips.
      function escapeBashArg(s) {{
        return (s || "")
          .replace(/\\\\/g, "\\\\\\\\")
          .replace(/"/g, '\\\\"')
          .replace(/`/g, "\\\\`")
          .replace(/\\$/g, "\\\\$");
      }}

      function renderThreadRep(rep) {{
        // One representative thread = a clickable card.
        // - Headline = single turn closest to basin centroid
        // - Click to expand: shows all turns in conversational order
        // - Single-turn threads (Gemini Takeout) get no expand affordance
        // - Per-rep replay chip closes the action arc on the turn level
        //   (forward-arc bullet "click a turn in an expanded thread →
        //   replay this through the council"). The chip uses the rep's
        //   headline as the seed; click.stopPropagation prevents the
        //   surrounding li from toggling expand on chip click.
        const li = el("li", "topics-rep topics-rep-thread");
        const turnCount = Number(rep.turn_count || (rep.turns && rep.turns.length) || 1);
        const headRow = el("div", "topics-rep-head");
        headRow.appendChild(el("span", "topics-rep-headline", rep.headline || "(no headline)"));
        const replaySeed = rep.headline || (rep.turns && rep.turns[0] && rep.turns[0].snippet) || "";
        if (replaySeed) {{
          const replayCmd = 'trinity-local council --task "' + escapeBashArg(replaySeed) + '"';
          const replay = el("button", "topics-rep-replay", "Replay");
          replay.type = "button";
          replay.title = "Copy: " + replayCmd;
          replay.dataset.transcriptId = rep.transcript_id || "";
          replay.addEventListener("click", (event) => {{
            event.stopPropagation();
            if (navigator.clipboard?.writeText) {{
              navigator.clipboard.writeText(replayCmd).catch(() => null);
            }}
            replay.textContent = "✓ Copied";
            announceCopy("Copied: " + replayCmd);
            setTimeout(() => {{ replay.textContent = "Replay"; }}, 2200);
          }});
          headRow.appendChild(replay);
        }}
        if (turnCount > 1) {{
          const chev = el("span", "topics-rep-chev", "▸");
          const meta = el("span", "topics-rep-meta",
            turnCount + " turn" + (turnCount === 1 ? "" : "s"));
          headRow.appendChild(meta);
          headRow.appendChild(chev);
          li.classList.add("expandable");
          // Keyboard-operable disclosure (WCAG 2.1.1 + 4.1.2): a bare <li>
          // with only a click handler is mouse-only and mute to AT. Make it a
          // real button-role widget — focusable (tabindex=0), self-describing
          // (role=button + aria-expanded), and Enter/Space-activatable. The
          // turns list it controls gets an id so aria-controls points at it.
          const turnsId = "topics-turns-" + (rep.transcript_id || rep.id || Math.random().toString(36).slice(2));
          const turnsList = el("ol", "topics-rep-turns");
          turnsList.id = turnsId;
          (rep.turns || []).forEach(turn => {{
            const tl = el("li", "topics-rep-turn");
            tl.appendChild(el("span", "topics-rep-turn-idx",
              "T" + (Number(turn.turn_index || 0) + 1)));
            tl.appendChild(el("span", "topics-rep-turn-text",
              turn.snippet || turn.id || ""));
            turnsList.appendChild(tl);
          }});
          // Lazy-attach the turns list — collapsed by default.
          li.appendChild(headRow);
          li.appendChild(turnsList);
          li.setAttribute("role", "button");
          li.setAttribute("tabindex", "0");
          li.setAttribute("aria-expanded", "false");
          li.setAttribute("aria-controls", turnsId);
          // Single toggle path so click AND keyboard keep .open / aria-expanded /
          // the chevron in lockstep — no second source of truth to drift.
          const toggleTurns = () => {{
            const open = li.classList.toggle("open");
            li.setAttribute("aria-expanded", open ? "true" : "false");
            chev.textContent = open ? "▾" : "▸";
          }};
          li.addEventListener("click", (event) => {{
            event.stopPropagation();
            toggleTurns();
          }});
          li.addEventListener("keydown", (event) => {{
            if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {{
              event.preventDefault();
              event.stopPropagation();
              toggleTurns();
            }}
          }});
        }} else {{
          // Single-turn thread — no expand needed; just show the headline.
          li.appendChild(headRow);
        }}
        return li;
      }}

      function showDetail(b) {{
        const total = nodes.reduce((s, n) => s + n.size, 0);
        const pct = total > 0 ? (100 * (b.size || 0) / total) : 0;
        clearChildren(detail);
        const head = el("div");
        head.appendChild(el("span", "basin-id", b.id || "?"));
        head.appendChild(document.createTextNode(" · "));
        // New thread-aware schema: basin.size is total turns,
        // basin.thread_count is distinct sessions. Legacy: only size.
        if (typeof b.thread_count === "number" && b.thread_count > 0) {{
          // Pluralize against the count — a single-thread / single-turn basin
          // renders "1 thread · 1 turn", not "1 threads · 1 turns" (the same
          // n=1 plural-literal class as the /stats captions; the turn-rep
          // count below already uses this ternary).
          const tc = b.thread_count, sz = (b.size || 0);
          head.appendChild(document.createTextNode(
            tc.toLocaleString() + " thread" + (tc === 1 ? "" : "s") + " · " +
            sz.toLocaleString() + " turn" + (sz === 1 ? "" : "s") +
            " (" + pct.toFixed(1) + "% of corpus)"));
        }} else {{
          const sz = (b.size || 0);
          head.appendChild(document.createTextNode(
            sz.toLocaleString() + " prompt" + (sz === 1 ? "" : "s") +
            " (" + pct.toFixed(1) + "% of corpus)"));
        }}
        detail.appendChild(head);

        // Honest empty-state: a basin can carry size/threads but NO
        // representatives AND no top_terms — e.g. a legacy topics.json
        // written before the representatives feature shipped, or a stale
        // build. Without this, clicking such a node paints ONLY the
        // "b01 · 2 threads · 5 turns" header line and nothing else: a
        // dead-end render that tells the user a basin holds 29% of their
        // corpus but reveals nothing about it and offers no action. Say so
        // and point at the rebuild that repopulates it (same warm-warning
        // shape as the ?basin= stale notice + the picks Reader's unbuilt
        // copy). Below the representatives/top_terms branches everything
        // else (pick xlink, launch chip) already no-ops on this shape.
        const hasReps = Array.isArray(b.representatives) && b.representatives.length;
        const hasTerms = Array.isArray(b.top_terms) && b.top_terms.length;
        if (!hasReps && !hasTerms) {{
          const note = el("div", "topics-basin-empty");
          note.appendChild(document.createTextNode(
            "No representatives or terms for this basin yet — its content " +
            "wasn't captured in the current lens build. Rebuild via "));
          const code = el("code", null, "trinity-local lens");
          note.appendChild(code);
          note.appendChild(document.createTextNode(" to repopulate it."));
          detail.appendChild(note);
        }}

        // Representatives — top-K closest to centroid. New shape is
        // thread-aware: each rep carries transcript_id, turn_count,
        // headline, turns[]. Legacy shape was flat {{id, snippet}}; the
        // renderer handles both so a stale topics.json doesn't break.
        if (Array.isArray(b.representatives) && b.representatives.length) {{
          const isThreadShape = b.representatives[0] && Array.isArray(b.representatives[0].turns);
          detail.appendChild(el("div", "topics-reps-label",
            isThreadShape
              ? "Most-representative threads (click to expand turns)"
              : "Most-representative prompts (closest to centroid)"));
          const ul = el("ul", "topics-reps-list");
          b.representatives.forEach(rep => {{
            if (isThreadShape) {{
              ul.appendChild(renderThreadRep(rep));
            }} else {{
              ul.appendChild(el("li", "topics-rep", rep.snippet || rep.id || ""));
            }}
          }});
          detail.appendChild(ul);
        }}
        if (Array.isArray(b.top_terms) && b.top_terms.length) {{
          const tline = el("div");
          tline.style.marginTop = "10px";
          tline.appendChild(el("span", "row-label", "Top terms: "));
          tline.appendChild(document.createTextNode(b.top_terms.join(", ")));
          detail.appendChild(tline);
        }}
        // Launch-council action: derive a seed prompt from the basin's
        // closest-to-centroid representative (.headline for thread-shape,
        // .snippet for legacy), escape shell metacharacters, and copy
        // `trinity-local council-launch --task "<seed>"`. Closes the
        // topology action arc from the forward arc bullet "click a basin
        // → launch a council on this topic".
        // Cross-memory pick link: if this basin has been consolidated
        // into a routing rule, link straight to its picks.json entry.
        // basinToPickTask is precomputed once in renderTopicsReader.
        const pickTask = basinToPickTask.get(b.id);
        if (pickTask) {{
          const xlink = el("a", "topics-pick-xlink");
          xlink.href = "memory.html?file=picks.json&task=" + encodeURIComponent(pickTask);
          // Show the routing WINNER (+ margin) inline — post-#298 a basin's
          // defining property IS its chairman-winner, so surface it here rather
          // than making the user click through to read it. providerBrand brands
          // the display (codex → GPT) so this reads consistent with the picks
          // Reader's "Use <brand>" AND the launchpad cheat-sheet — #275. Falls
          // back to the basin id when the winner/margin aren't available.
          const pick = basinIdToPick.get(b.id);
          // Gate on the BRANDED winner string, not the raw field: a corrupt non-
          // string pick.winner (42 / {{}}) is truthy but providerBrand coerces it
          // to "" — so "Routes to [object Object]" / "Routes to 42" no longer
          // paints; the basin falls back to the "Routing rule: <task>" label.
          const pickBrand = (pick && pick.winner) ? providerBrand(pick.winner) : "";
          if (pickBrand) {{
            const hasM = isFiniteNumber(pick.margin);
            const m = hasM ? " · margin " + fmtMargin(pick.margin) : "";
            // Below the routing gate the basin is a near-tie ask does NOT route on
            // (it falls to kNN) — so don't assert "Routes to X" (a false claim for
            // ~half the real picks, margin p50≈0.17). Say it leans X but is a tie.
            const adv = hasM && pick.margin < WINNER_MARGIN_FLOOR;
            xlink.textContent = adv
              ? "Leans " + pickBrand + m + " · near-tie → kNN →"
              : "Routes to " + pickBrand + m + " →";
          }} else {{
            xlink.textContent = "Routing rule: " + pickTask + " →";
          }}
          // When this xlink paints a "· margin 0.42", gloss what that number means
          // (first-timer can't read the bare value) AND keep the click-action hint.
          const pick2 = basinIdToPick.get(b.id);
          const mg = (pick2 && isFiniteNumber(pick2.margin)) ? marginGloss(pick2.margin) : "";
          xlink.title = (mg ? mg + " " : "") + "Click to open this basin's pick in the picks Reader.";
          detail.appendChild(xlink);
        }}
        const seedRep = Array.isArray(b.representatives) ? b.representatives[0] : null;
        const seedText = seedRep ? (seedRep.headline || seedRep.snippet || "") : "";
        if (seedText) {{
          // Bash-safe quoting handled by escapeBashArg (shared with the
          // per-rep replay chip in renderThreadRep).
          const launchCmd = 'trinity-local council --task "' + escapeBashArg(seedText) + '"';
          const chip = el("button", "topics-launch-chip", "Launch council on this topic");
          chip.type = "button";
          chip.title = "Copy: " + launchCmd;
          chip.dataset.basin = b.id || "";
          chip.addEventListener("click", () => {{
            if (navigator.clipboard?.writeText) {{
              navigator.clipboard.writeText(launchCmd).catch(() => null);
            }}
            chip.textContent = "✓ Copied";
            announceCopy("Copied: " + launchCmd);
            setTimeout(() => {{ chip.textContent = "Launch council on this topic"; }}, 2200);
          }});
          detail.appendChild(chip);
        }}
        // The viewer payload ships `prompt_id_count` (the slimmed form — the
        // full prompt_ids array is dropped server-side since only its length is
        // read here). Fall back to a literal prompt_ids array for any older
        // inlined payload that still carries it.
        const idCount = (typeof b.prompt_id_count === "number")
          ? b.prompt_id_count
          : (Array.isArray(b.prompt_ids) ? b.prompt_ids.length : null);
        if (idCount !== null && idCount !== (b.size || 0)) {{
          // Pre-2026-05-12 topics.json files were written with prompt_ids
          // truncated to 50. New writes carry the full list. If they
          // diverge, the user has a stale on-disk topics.json — surface it.
          const note = el("div", "row-label");
          note.style.marginTop = "4px";
          note.style.fontSize = "12px";
          note.appendChild(document.createTextNode(
            "Stale topology: prompt_ids carries " + idCount + " entries vs basin size " +
            (b.size || 0) + ". Re-run trinity-local lens to refresh."));
          detail.appendChild(note);
        }}

        // WCAG 4.1.3 (Status Messages): activating a basin node — by mouse OR,
        // crucially, by keyboard (Enter/Space, where focus STAYS on the <circle>)
        // — silently rewrites this detail panel, which sits EARLIER in the DOM than
        // the node a keyboard user just activated (browser-confirmed: the panel
        // "PRECEDES" the focused node). A sighted user sees it; a screen-reader user
        // got NOTHING — no signal the panel changed, and the panel holds the only
        // "Launch council on this topic" + per-rep Replay controls (reachable only by
        // Shift+Tab BACKWARD past the whole graph). Push a concise summary through the
        // SAME #sr-status live region the copy chips already use so the activation is
        // perceivable: which basin opened, its weight, and its routing pick. This is
        // the populate-on-activation sibling of the copy-ack announcement — the one
        // interactive panel in the viewer that was mute.
        const detailParts = [];
        detailParts.push("Showing basin " + (b.label || b.id || "?"));
        if (typeof b.thread_count === "number" && b.thread_count > 0) {{
          detailParts.push(b.thread_count.toLocaleString() + " thread" + (b.thread_count === 1 ? "" : "s"));
        }} else {{
          const sz0 = (b.size || 0);
          detailParts.push(sz0.toLocaleString() + " prompt" + (sz0 === 1 ? "" : "s"));
        }}
        const announcePick = basinIdToPick.get(b.id);
        const announceBrand = (announcePick && announcePick.winner) ? providerBrand(announcePick.winner) : "";
        if (announceBrand) {{
          const advTie = isFiniteNumber(announcePick.margin) && announcePick.margin < WINNER_MARGIN_FLOOR;
          detailParts.push(advTie ? ("leans " + announceBrand + ", a near-tie") : ("routes to " + announceBrand));
        }}
        announceCopy(detailParts.join(". ") + ".");
      }}
    }}

    function highlightJson(text) {{
      // Token-level highlight — returns a DocumentFragment. Standalone
      // (no library) so we don't pull a JSON renderer just for this.
      const frag = document.createDocumentFragment();
      const re = /("[^"\\\\]*(?:\\\\.[^"\\\\]*)*")(\\s*:)?|\\b(true|false|null)\\b|(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)/g;
      let last = 0;
      let m;
      while ((m = re.exec(text)) !== null) {{
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        if (m[1]) {{
          const span = el("span", m[2] ? "json-key" : "json-str", m[1]);
          frag.appendChild(span);
          if (m[2]) frag.appendChild(document.createTextNode(m[2]));
        }} else if (m[3]) {{
          frag.appendChild(el("span", m[3] === "null" ? "json-null" : "json-bool", m[3]));
        }} else if (m[4]) {{
          frag.appendChild(el("span", "json-num", m[4]));
        }}
        last = re.lastIndex;
      }}
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      return frag;
    }}

    function suggestionFor(name) {{
      // What to run to populate each memory if it's missing.
      // core.md was historically rebuilt via `trinity-local distill`,
      // but the distill CLI was hidden in commit c9b1f9d (it lives as
      // an internal Phase-5 callable inside dream). For users clicking
      // the rebuild chip, `dream` is the live path — heavier than
      // pure distill but always works and ships in v1.7.4.
      if (name === "lens.md" || name === "topics.json") return "lens";
      if (name === "generators.md") return "lens-generators";
      if (name === "picks.json") return "consolidate";
      if (name === "routing.json") return "dream";
      if (name === "vocabulary.md") return "vocabulary";
      if (name === "core.md") return "dream";
      return "dream";
    }}
  </script>
</body>
</html>
"""


def write_memory_viewer() -> Path:
    """Write the viewer HTML to ~/.trinity/portal_pages/memory.html."""
    path = portal_pages_dir() / "memory.html"
    path.write_text(render_memory_viewer_html(), encoding="utf-8")
    return path
