from __future__ import annotations

import html
import json
from pathlib import Path


from .council_schema import CouncilOutcome, PromptBundle
from .design_system import page_data_script_json, render_html_footer, render_html_head
from .launchpad_runtime import launchpad_runtime_js
# IIFE build (derived at vendor-publish time). See
# trinity_local.launchpad_template:PETITE_VUE_IIFE for the rationale —
# Chrome treats every file:// URL as a unique origin, so the ES module
# `import` form silently fails CORS and the page renders raw `{{ }}`
# templates. The IIFE form loads via plain <script src> with no CORS.
PETITE_VUE_IIFE = "../portal_pages/vendor/petite-vue.iife.js"
# The live council page shows the SAME loading register as the launchpad — import
# the single source instead of hand-maintaining a second copy. launchpad_data
# doesn't import council_review, so this module-level import can't cycle.
from .launchpad_data import COUNCIL_LOADING_MESSAGES as LIVE_COUNCIL_LOADING_MESSAGES
# The macOS-Shortcut dispatch tier retired 2026-05-17 (Native Messaging
# via the Chrome extension's capture_host took over). These constants
# stay as harmless data-attr defaults the JS dispatch knows to skip
# when the URL is empty. See retired_names.py: `shortcuts_integration`.
DEFAULT_SHORTCUT_NAME = "Trinity Dispatch"
_EMPTY_SHORTCUT_URL = ""
from .state_paths import review_pages_dir


def _esc(value: str | None) -> str:
    return html.escape(value or "")


def write_unified_council_page(bundle: PromptBundle, outcome: CouncilOutcome) -> Path:
    """Write a tiny redirect file pointing at the unified `live_council.html`
    page, parameterised with this outcome's `?council_id=`. The unified page
    loads the outcome JSONP and renders. This keeps existing links to
    `{council_run_id}.html` working without per-outcome HTML duplication."""
    path = review_pages_dir() / f"{outcome.council_run_id}.html"
    target = f"live_council.html?council_id={outcome.council_run_id}"
    path.write_text(
        f"<!doctype html><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"refresh\" content=\"0; url={target}\">"
        f"<title>Trinity — Council {outcome.council_run_id}</title>"
        f"<script>window.location.replace({json.dumps(target)});</script>"
        f"<a href=\"{target}\">Open council review</a>",
        encoding="utf-8",
    )
    # Make sure the unified page itself exists; idempotent.
    write_live_council_page()
    return path


def render_live_council_page() -> str:
    from .launchpad_data import _browser_extension as _ext_config
    head = render_html_head("Trinity — Council")
    footer = render_html_footer()
    page_data = {
        # Relative paths so the same HTML works under both file:// and http://
        # localhost (see render_html_head note above for rationale).
        "statusScriptBaseUrl": "../portal_pages/status",
        "outcomeScriptBaseUrl": "../council_outcomes",
        "loadingMessages": LIVE_COUNCIL_LOADING_MESSAGES,
        "shortcutName": DEFAULT_SHORTCUT_NAME,
        "launchpadUrl": "../portal_pages/launchpad.html",
        "reviewPagesBaseUrl": ".",
        # Threaded so __TRINITY_DISPATCH__ on this page can route
        # refine/iterate clicks through the Chrome extension. Without
        # this, dispatcher.state='absent' and the refine button can't
        # find a path to actually run council-iterate.
        "browserExtension": _ext_config(),
    }
    return f"""{head}
  <style>
    .live-shell {{
      display: grid;
      gap: 24px;
    }}

    .task-collapsible {{
      margin: 0 0 12px;
      padding: 14px 16px;
      background: rgba(79, 144, 149, 0.04);
      border-left: 3px solid rgba(79, 144, 149, 0.3);
      border-radius: 0 6px 6px 0;
    }}
    .task-collapsible > summary {{
      cursor: pointer;
      font-size: 17px;
      font-weight: 600;
      color: #1a1a1a;
      list-style: none;
      /* The summary is now a fixed disclosure LABEL ("Your question"), so it no
         longer slices the pasted task — a long unbreakable token (URL / path /
         regex) can only reach the expanded <p> body, which carries its own
         overflow-wrap:anywhere. Keep break-anywhere here as a cheap guard in case
         the label ever changes; a <summary> inherits NO break rule otherwise. */
      overflow-wrap: anywhere;
    }}
    .task-collapsible > summary::-webkit-details-marker {{ display: none; }}
    .task-collapsible > summary::before {{
      content: "▸ ";
      color: rgba(79, 144, 149, 0.6);
      margin-right: 4px;
    }}
    .task-collapsible[open] > summary::before {{
      content: "▾ ";
    }}


    .launch-status {{
      display: grid;
      gap: 12px;
      padding: 18px;
      border: 1px solid rgba(79, 144, 149, 0.18);
      border-radius: 18px;
      background: rgba(79, 144, 149, 0.05);
    }}

    .spinner-row {{
      display: inline-flex;
      align-items: center;
      gap: 12px;
    }}

    .spinner {{
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 2px solid rgba(79, 144, 149, 0.18);
      border-top-color: var(--action);
      animation: trinity-spin 0.8s linear infinite;
    }}

    .status-message {{
      font-weight: 500;
      min-height: 24px;
      /* --action-hover #34666b (5.2:1), NOT --action #3f777c: the status message
         + the Stop-council ghost button sit ON the .launch-status teal wash
         (rgba(79,144,149,0.05) over the page), where --action composites to only
         4.08:1 — below AA 4.5. The deep teal clears it on the wash. The
         action-teal-on-its-own-tint class (sibling of the .meta-on-tinted-card fix). */
      color: var(--action-hover);
    }}
    /* The Stop-council / Open-page ghost buttons live on the same teal launch
       wash; the global .button.ghost (--action) is 4.08:1 there. Deepen them to
       --action-hover so the running-council controls clear AA on the wash. */
    .launch-status .button.ghost {{
      color: var(--action-hover);
    }}

    .answers-grid {{
      display: grid;
      /* min(380px, 100%) floor so a 2-member grid can't force a track wider than
         the viewport — on a 357px phone a flat 380px floor overflowed horizontally. */
      grid-template-columns: repeat(auto-fit, minmax(min(380px, 100%), 1fr));
      gap: 24px;
      margin-top: 24px;
    }}

    .answers-grid-three {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}

    @media (max-width: 1200px) {{
      .answers-grid-three {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    /* Intermediate tablet step: between the 1200px→2col and 768px→1col rules a
       ~900px tablet was stuck on the 3-up cramped track. Drop the three-up grid
       to two columns here so each card has room before collapsing to one. */
    @media (max-width: 1023px) {{
      .answers-grid-three {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}

    @media (max-width: 768px) {{
      .answers-grid,
      .answers-grid-three {{
        grid-template-columns: 1fr;
      }}
      /* Align the topbar's horizontal padding with `main` on mobile (both 18px)
         so the back link doesn't sit 2px inset from the content below it. */
      .trinity-topbar {{
        padding-left: 18px;
        padding-right: 18px;
      }}
    }}

    .provider-status-row {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 18px 20px;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--surface);
      font-size: 15px;
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
      /* Same grid-item min-width:auto trap as .answer-card on the static review
         page: a streaming member output with a wide code block / long token
         stretches this grid item past a phone viewport. min-width:0 lets it
         shrink; the pre rule below scrolls wide code WITHIN the row. The live
         council page is the streaming "watch it" companion link people open on
         phones, so it needs the same containment as the unified review page. */
      min-width: 0;
    }}

    .provider-status-row.clickable {{
      cursor: pointer;
      outline: none;
    }}

    .provider-status-row.clickable:hover {{
      transform: translateY(-2px);
      border-color: var(--action);
      box-shadow: 0 8px 24px rgba(79, 144, 149, 0.12);
    }}

    .provider-status-row.clickable:focus-visible {{
      border-color: var(--action);
      box-shadow: 0 0 0 3px rgba(79, 144, 149, 0.14);
    }}

    .provider-status-row.selected {{
      border-color: var(--success);
      background: rgba(45, 106, 79, 0.06);
      box-shadow: 0 0 0 3px rgba(45, 106, 79, 0.1), 0 8px 24px rgba(79, 144, 149, 0.12);
    }}

    .confirmation-box {{
      margin-top: 24px;
      background: rgba(45, 106, 79, 0.06);
      border-color: var(--success);
    }}
    .confirmation-box.save-failed {{
      background: rgba(139, 30, 30, 0.06);
      border-color: rgba(139, 30, 30, 0.3);
    }}
    .confirmation-box.save-failed .eyebrow {{
      color: #8b1e1e;
    }}

    .provider-status-header {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .provider-status-name {{
      font-weight: 600;
      flex: 1;
    }}

    .provider-status-response {{
      color: var(--text-primary);
      line-height: 1.55;
      padding: 10px 12px;
      background: var(--surface-muted);
      border-radius: 8px;
      font-size: 14px;
      white-space: pre-wrap;
      word-wrap: break-word;
    }}

    /* Base markdown-body wrap — the synthesis section (a plain `.markdown-body`,
       NOT a `.provider-status-response`) renders chairman prose that can carry a
       long unbreakable token (URL / path / hash); without this it overflowed the
       320px live-companion the same way the question <h1> did. Mirrors the static
       review page's base `.markdown-body` rule. */
    .markdown-body {{
      min-width: 0;
      overflow-wrap: break-word;
    }}

    .provider-status-response.markdown-body {{
      white-space: normal;
      font-family: inherit;
    }}

    .provider-status-response.markdown-body pre {{
      white-space: pre-wrap;
      max-width: 100%;
      overflow-x: auto;
      overflow-wrap: anywhere;
    }}

    .provider-status-response.markdown-body table {{
      max-width: 100%;
      overflow-x: auto;
      display: block;
    }}

    .provider-status-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 74px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: var(--surface-muted);
      color: var(--text-secondary);
      border: 1px solid var(--border);
    }}

    .provider-status-badge.done {{
      background: rgba(45, 106, 79, 0.1);
      /* --success-text (deep green) not --success (#4f9095 teal): this 12px/700 "Done"
         pill is small text; teal on the green .done tint was 2.92:1 (below AA 4.5:1).
         #2d6a4f → 5.1:1 and reads green to match the tint+border. UX sweep 2026-06-21. */
      color: var(--success-text);
      border-color: rgba(45, 106, 79, 0.28);
    }}

    .provider-status-badge.running {{
      background: rgba(79, 144, 149, 0.08);
      color: var(--action);
      border-color: rgba(79, 144, 149, 0.22);
    }}

    .provider-status-badge.pending {{
      background: var(--surface-muted);
      color: var(--text-muted);
      border-color: var(--border);
    }}

    .provider-status-badge.failed {{
      background: rgba(139, 30, 30, 0.08);
      color: #8b1e1e;
      border-color: rgba(139, 30, 30, 0.2);
    }}

    .quote-member-btn {{
      margin-left: auto;
      /* The live council page is the streaming "watch it" companion link people
         open on PHONES (see .provider-status-row comment), so Quote ↓ — a real
         content-action control — must clear the 44px touch target like every
         other action button (WCAG 2.5.5 / Apple HIG; founder: "every action
         button clear 44px on touch widths"). At 11px/1.4 + 2px padding the chip
         was ~21px tall — HALF the floor — a fat-finger miss on the exact surface
         it ships to. Flex-center floors the HIT AREA to 44px while the compact
         font/padding keep the chip visually small (the .button + sidepanel
         icon-button pattern), so the row header doesn't bloat. */
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-secondary);
      font-size: 11px;
      padding: 2px 10px;
      border-radius: 999px;
      cursor: pointer;
      font-family: inherit;
      line-height: 1.4;
    }}
    .quote-member-btn:hover {{
      background: var(--surface-muted);
      color: var(--text-primary);
    }}
    .quote-member-btn:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}

    .provider-status-detail {{
      color: var(--text-secondary);
      line-height: 1.4;
      /* A running/failed member's reasoning_summary (council_status
         ._extract_reasoning_summary, truncated to 120 chars but NOT
         space-broken) can be a separator-free path / URL / error token
         (e.g. ECONNREFUSED:/Users/.../python-3.10-aaaa…). On the live
         council page — the streaming "watch it" companion people open on
         PHONES — that token forced the document ~112px past a 320px
         viewport (the answers-grid → provider-status-row → MAIN chain
         all horizontal-scrolled; a SHORT detail at the same 3-member grid
         fits with zero overflow, so the token is the sole cause). The
         launchpad's twin .provider-status-detail already breaks this exact
         class ("the 320px running-council horizontal-scroll class"); this
         streaming page — the one people actually watch on a phone — was the
         asymmetric miss. */
      overflow-wrap: anywhere;
      min-width: 0;
    }}

    .provider-status-detail.empty {{
      color: var(--text-muted);
    }}

    .live-actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}

    .live-actions .button {{
      text-decoration: none;
    }}

    .status-error {{
      color: #8b1e1e;
      /* seg.errorText is the raw runner error (status.error = str(exc) /
         why[:200]) — arbitrary output that can carry a separator-free path /
         URL / hash / stack-trace token. Without a break rule that token forces
         the launch-status card (and the document) wider than the viewport:
         the "Council failed" line streamed off-screen right and the 320px live
         companion horizontal-scrolled to ~1535px. Same class the launchpad
         running card (.provider-status-detail) and the popup .panel-tip already
         break; this was the asymmetric live-page miss. */
      overflow-wrap: anywhere;
      min-width: 0;
    }}

    /* Synthesis prose reads at a comfortable measure on wide screens; the
       answer-card grid is a separate full-width section and stays untouched. */
    .synthesis-section {{
      max-width: 720px;
    }}

    .routing-label-grid {{
      display: grid;
      /* minmax(0, 1fr), NOT the implicit `auto` column, which sizes to the
         widest content — a claim / why_matters carrying a long unbreakable token
         (dev identifier / URL) then overflowed the 375px live-companion by ~510px
         (found 2026-06-07, sibling of the static-page claims fix 8190f702).
         minmax(0,...) lets the column shrink; overflow-wrap breaks the token. */
      grid-template-columns: minmax(0, 1fr);
      overflow-wrap: break-word;
      gap: 14px;
      margin-top: 8px;
      /* Cap the verdict prose at the same reading measure as the synthesis. */
      max-width: 720px;
    }}

    .routing-label-grid ul {{
      margin: 6px 0 0 0;
      padding-left: 20px;
    }}

    .chain-actions {{
      margin-top: 24px;
    }}

    .chain-button-row {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 16px;
    }}

    .chain-refine-row {{
      display: flex;
      gap: 12px;
      margin-top: 16px;
      align-items: stretch;
    }}

    /* A TEXTAREA, not a single-line input: quoteMember() builds a multi-line
       markdown blockquote ("> [Claude]: …") and stacks several joined by blank
       lines, and that refinePrompt is sent verbatim as the next-round directive
       (where the markdown IS meaningful). A single-line <input> silently strips
       every newline, jamming stacked quotes into one unreadable run — found
       driving Quote ↓ in the 2026-06-17 UX sweep. Compact (2 rows) + button
       submit; plain Enter now inserts a newline (Cmd/Ctrl+Enter submits). */
    .chain-refine-input {{
      flex: 1;
      padding: 10px 14px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 14px;
      font-family: inherit;
      line-height: 1.4;
      background: var(--surface);
      color: var(--text-primary);
      min-height: 44px;
      resize: vertical;
      white-space: pre-wrap;
    }}

    .chain-refine-input:focus {{
      outline: none;
      border-color: var(--action);
      box-shadow: 0 0 0 3px rgba(79, 144, 149, 0.12);
    }}

    /* On a narrow phone the refine input + Refine button don't both fit on one
       row; let the row wrap and the input take the full width above the button. */
    @media (max-width: 480px) {{
      .chain-refine-row {{ flex-wrap: wrap; }}
      .chain-refine-input {{ flex: 1 1 100%; }}
    }}

    .chain-loading {{
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 16px;
      padding: 14px 18px;
      background: var(--surface-muted);
      border-radius: 10px;
    }}

    .chain-loading .spinner {{
      display: inline-block;
      width: 18px;
      height: 18px;
      border: 2px solid var(--border);
      border-top-color: var(--action);
      border-radius: 50%;
    }}

    @keyframes trinity-spin {{
      to {{
        transform: rotate(360deg);
      }}
    }}

    /* Winner reveal — the chairman's pick glows in once the council lands. */
    @keyframes winner-glow {{
      0%   {{ box-shadow: 0 0 0 0 rgba(212, 160, 23, 0); }}
      25%  {{ box-shadow: 0 0 0 4px rgba(212, 160, 23, 0.35), 0 6px 26px rgba(212, 160, 23, 0.22); }}
      100% {{ box-shadow: 0 0 0 2px rgba(212, 160, 23, 0.30); }}
    }}
    .provider-status-row.winner-reveal {{
      border-color: rgba(212, 160, 23, 0.55);
      animation: winner-glow 1.6s ease-out forwards;
    }}
    @keyframes verdict-rise {{
      from {{ opacity: 0; transform: translateY(6px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}
    .winner-verdict {{
      margin: 6px 0 18px;
      padding: 12px 16px;
      font-size: 1.05rem;
      font-weight: 600;
      color: var(--text-primary);
      background: linear-gradient(90deg, rgba(212, 160, 23, 0.13), rgba(212, 160, 23, 0.03));
      border: 1px solid rgba(212, 160, 23, 0.40);
      border-radius: 10px;
      animation: verdict-rise 0.5s ease-out both;
    }}
    .winner-verdict .trophy {{ margin-right: 8px; }}

    .chain-segment-divider {{
      margin: 32px 0 8px;
      padding: 12px 18px;
      background: rgba(79, 144, 149, 0.04);
      border-left: 3px solid rgba(79, 144, 149, 0.3);
      border-radius: 0 8px 8px 0;
    }}

    .chain-segment-divider.clickable {{
      cursor: pointer;
      user-select: none;
      transition: background 0.15s ease;
    }}

    .chain-segment-divider.clickable:hover {{
      background: rgba(79, 144, 149, 0.09);
    }}

    .chain-segment-divider.clickable:focus-visible {{
      /* SOLID --action ring, not the old rgba(79,144,149,0.2) teal: at 0.2 alpha
         that ring composited to ~1.21:1 over the page body (and ~1.17:1 over the
         divider's own teal tint) — far under the WCAG 2.4.7 / 1.4.11 non-text
         3:1 floor, so a keyboard-only user tabbing a multi-round chain got no
         visible focus indicator on the round dividers. The global
         :focus-visible outline is suppressed (outline:none) so the box-shadow IS
         the only indicator; --action (#3f777c) solid clears 3:1 on the body
         (4.29:1), the divider's card tint (4.12:1) AND its hover tint (3.94:1),
         where the lighter --accent default would fail (2.83–2.96:1) over those
         teal-tinted backgrounds. Box-shadow (not outline) so the ring follows
         the divider's rounded right corners. */
      outline: none;
      box-shadow: 0 0 0 3px var(--action);
    }}

    .segment-toggle-chevron {{
      display: inline-block;
      width: 14px;
      color: rgba(79, 144, 149, 0.6);
    }}

    .refinement-prompt {{
      font-style: italic;
      color: var(--text-primary);
    }}
  </style>
  <div id="live-council-app" v-scope="LiveCouncilApp(pageData)" v-cloak @vue:mounted="init">
    <header class="trinity-topbar">
      <a class="topbar-back" :href="pageData.launchpadUrl">← Launchpad</a>
      <h1 class="topbar-title">Council</h1>
      <span class="topbar-spacer"></span>
      <a class="topbar-action" v-if="threadViewUrl" :href="threadViewUrl">View full thread</a>
    </header>
    <main>
      <!-- Persistent visually-hidden status mirror — present from first render so
           the live region is reliably announced when its text mutates. Every
           poll transition (a round running, the council completing, a failure or
           a stop) flows through liveAnnouncement, so a screen-reader user is told
           what a sighted user sees in the spinner / verdict (WCAG 4.1.3). -->
      <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{{{ liveAnnouncement }}}}</div>
      <section class="card mb-lg">
        <!-- The task header is the BIGGEST visible text on the page (the hero
             question, design-system h1 size), but it is NOT the page-level
             heading — the topbar's <h1 class="topbar-title">Council</h1> already
             owns level 1. Two literal <h1>s put a screen-reader user navigating
             by heading on TWO competing level-1 landmarks (WCAG 1.3.1 Info &
             Relationships / 2.4.6) — a broken outline with no single top. Demote
             the ANNOUNCED level to 2 with aria-level (keep the <h1> tag so the
             clamp(38px..56px) hero-question size is untouched) — the exact pattern
             the memory viewer uses so a content "# Lens" doesn't become a second
             h1 competing with its topbar "Your lens" (memory_viewer.py demote).
             Outline is now h1 Council → h2 task → h2 sections. -->
        <h1 aria-level="2" v-if="threadTaskTextDisplay && threadTaskTextDisplay.length <= 240">{{{{ threadTaskTextDisplay }}}}</h1>
        <details v-if="threadTaskTextDisplay && threadTaskTextDisplay.length > 240" class="task-collapsible" :open="threadTaskTextDisplay.length <= 600">
          <!-- The <summary> is a fixed disclosure LABEL, not a slice of the task.
               It used to be `threadTaskTextDisplay.slice(0, 200)…`, but for a task
               in the open-by-default band (241–600 chars) the details renders OPEN,
               so the slice summary AND the full-text <p> below it both painted —
               printing the first ~200 chars of the user's question TWICE, stacked,
               nearly identically (the bold teaser then the body restarting with the
               same words). The slice only ever doubled as a teaser for the collapsed
               (>600) case; a fixed label carries the disclosure affordance without
               duplicating the body in either state. The ▸/▾ ::before triangle conveys
               open/collapsed; the body <p> is the single source of the task text. -->
          <summary>Your question</summary>
          <p style="white-space: pre-wrap; overflow-wrap: anywhere; margin: 12px 0 0;">{{{{ threadTaskTextDisplay }}}}</p>
        </details>
        <p class="lede" v-if="anyBusy">Each round fills in below as it completes. You can leave and come back without losing the run.</p>
      </section>

      <div class="chain-segment" v-for="(seg, segIndex) in segments" :key="seg.key" :data-seg-key="seg.key">
        <section
          class="card chain-segment-divider clickable"
          v-if="segments.length > 1 || seg.refinementText"
          @click="toggleSegment(seg.key)"
          role="button"
          :tabindex="0"
          :aria-expanded="seg.expanded ? 'true' : 'false'"
          @keydown.enter.prevent="toggleSegment(seg.key)"
          @keydown.space.prevent="toggleSegment(seg.key)"
        >
          <div class="eyebrow">
            <span class="segment-toggle-chevron">{{{{ seg.expanded ? '▾' : '▸' }}}}</span>
            Round {{{{ seg.roundNumber || (segIndex + 1) }}}}{{{{ seg.converged ? ' · models converged' : '' }}}}{{{{ !seg.expanded ? ' · collapsed' : '' }}}}
          </div>
          <p v-if="seg.refinementText" class="meta refinement-prompt" style="margin: 6px 0 0;">↳ {{{{ seg.refinementText }}}}</p>
        </section>

        <section class="card launch-status mb-lg" v-if="seg.expanded && (seg.busy || seg.failed || seg.canceled)"
                 role="status" aria-live="polite">
          <!-- role=status/aria-live=polite: this card is the PRIMARY dynamic
               status surface — "Council running", the rotating status-message
               ("Synthesizing the strongest answer…"), "Council failed/stopped",
               and the "Stopping…" ack all update here as the poll advances. Without
               a live region a screen-reader user heard NOTHING when a member
               landed / the council stopped (WCAG 4.1.3 Status Messages). -->
          <div class="spinner-row" v-if="seg.busy">
            <span class="spinner" aria-hidden="true"></span>
            <strong>Council running</strong>
          </div>
          <strong v-if="seg.failed" class="status-error">Council failed</strong>
          <strong v-if="seg.canceled" class="status-error">Council stopped</strong>
          <p class="status-message" v-if="seg.busy">{{{{ currentStatusMessageFor(seg) }}}}</p>
          <p class="status-error" v-if="seg.errorText">{{{{ seg.errorText }}}}</p>
          <div class="live-actions" v-if="seg.busy && segIndex === segments.length - 1">
            <button type="button" class="button ghost" :disabled="stopRequested" @click="stopCouncil">{{{{ stopRequested ? 'Stopping…' : 'Stop council' }}}}</button>
          </div>
          <!-- Try again — the FAILED council's only way forward. Without it the page
               dead-ends on the failure banner: the chain composer ("Continue the
               thread") gates on canChainNext (last.completed && last.councilId), which
               a failed council has NEITHER of, so it never renders. Re-runs the SAME
               task via a fresh launch-council dispatch (canRetryFailed gates on the
               task text being present). -->
          <div class="live-actions" v-if="canRetryFailed(seg, segIndex)">
            <button type="button" class="button primary" :disabled="chainBusy" @click="retryFailedCouncil">{{{{ chainBusy ? 'Re-running…' : 'Try again' }}}}</button>
          </div>
        </section>

        <section class="card synthesis-section mb-lg" v-if="seg.expanded && !seg.failed && !seg.canceled && analysisRowFor(seg)">
          <!-- analysisRowFor() ALWAYS returns a (stub) row, so without the
               !failed/!canceled guard a council that FAILED to load still
               renders the optimistic "Analysis · QUEUED · Ready to start final
               comparison" tracker right beside the "Council failed" card — a
               contradiction (found 2026-06-02 driving a missing council_id in
               the browser). On a terminal failure/cancel, show ONLY the failure
               card. The routing-label + member sections below are already
               guarded by real runState data, so they correctly stay hidden. -->
          <div class="provider-status-header">
            <h2 style="margin: 0;">{{{{ analysisRowFor(seg).label }}}}</h2>
            <div class="provider-status-badge" :class="analysisRowFor(seg).statusClass" v-if="analysisRowFor(seg).statusClass !== 'done'">{{{{ analysisRowFor(seg).statusLabel }}}}</div>
          </div>
          <div class="markdown-body" v-if="analysisRowFor(seg).responseHtml" v-html="analysisRowFor(seg).responseHtml" style="margin-top: 12px;"></div>
          <p class="meta" v-else style="margin-top: 8px;">{{{{ analysisRowFor(seg).detail }}}}</p>
        </section>

        <!-- Winner verdict — the chairman's pick framed as "the answer you'd
             have picked". Suppressed on a SOLO council (1 responder): a single
             model has no contest to win, so the trophy framing overclaims. The
             solo line below states what actually happened instead (mirrors the
             share card's "One model — no council"). -->
        <div class="winner-verdict" v-if="seg.completed && lensPickProviderFor(seg) && !isSoloFor(seg)"
             role="status" aria-live="polite">
          <!-- role=status/aria-live=polite: the completion verdict appears only
               when the council finishes (the .launch-status live region has
               disappeared by then), so without its OWN live region a screen-reader
               user was never told the council completed or who won (WCAG 4.1.3). -->
          <span class="trophy">🏆</span>{{{{ formatProviderLabel(lensPickProviderFor(seg)) }}}} — the answer you'd have picked.
        </div>
        <!-- The solo verdict literally claims "One model answered" — it must NOT
             fire when ZERO models answered (a completed-but-all-failed status: the
             runner raises before persisting, but a hand-edited / corrupt status JSON
             can carry status:'completed' with every member failed — the #258
             hand-editable-state class). On that 0-responder council the all-failed
             line below ("Every provider attempted but failed to respond") is the
             honest verdict; "One model answered" sat ON TOP of it as a flat
             self-contradiction. Require >= 1 responder for the SOLO claim. -->
        <div class="winner-verdict solo-verdict" v-if="isSoloFor(seg) && respondedMembersFor(seg) >= 1" style="font-weight: 500;"
             role="status" aria-live="polite">
          One model answered — no council. Enable a second provider to run a real contest.
        </div>
        <!-- A real MULTI-provider contest that the chairman left without a winner
             (routing_label.winner === "" — a genuine tie / no clear pick, a real
             tolerated state: council_runner resolves an empty winner to None). The
             trophy verdict above and the solo line both suppress here, so WITHOUT
             this branch the completed page showed agreed/disagreed claims with NO
             top-line verdict at all — a sighted user reads a finished multi-model
             contest and is never told who won OR that nobody did. The PERSISTENT
             static review page (council_review._render_routing_label_section) and
             the share card both state "No winner" explicitly for this state; this
             is the live-page twin that drifted unfixed (the #35 honest-degradation
             twin of the solo line right above). -->
        <div class="winner-verdict no-winner-verdict" v-if="seg.completed && !isSoloFor(seg) && !lensPickProviderFor(seg)" style="font-weight: 500;"
             role="status" aria-live="polite">
          No clear winner — the models split and the chairman didn't pick one. See where they agreed and disagreed below.
        </div>

        <section class="card mb-lg" v-if="seg.expanded && routingLabelFor(seg)">
          <div class="eyebrow">Routing label</div>
          <div class="routing-label-grid">
            <!-- Winner / Agreed-claims suppressed on a SOLO council (1 responder):
                 a single model can't "win" against no one or "agree" with itself,
                 so the chairman's degenerate winner+consensus framing overclaims
                 (the share card's solo branch suppresses the identical blocks). -->
            <div v-if="routingLabelFor(seg).winner && !isSoloFor(seg)">
              <strong>Winner:</strong> {{{{ formatProviderLabel(routingLabelFor(seg).winner) }}}}<span v-if="routingLabelFor(seg).runner_up"> · runner-up: {{{{ formatProviderLabel(routingLabelFor(seg).runner_up) }}}}</span>
              <span v-if="routingLabelFor(seg).confidence"> · confidence: {{{{ routingLabelFor(seg).confidence }}}}</span>
            </div>
            <div v-if="isSoloFor(seg) && respondedMembersFor(seg) >= 1" class="meta">
              <strong>One model — no council.</strong> Only one model responded, so there's no winner and nothing to agree on yet.
            </div>
            <!-- 0 responders (completed-but-all-failed corrupt/hand-edited
                 status): "Only one model responded" is false here — every member
                 failed. State it honestly instead of folding into the solo block. -->
            <div v-if="isSoloFor(seg) && respondedMembersFor(seg) === 0" class="meta">
              <strong>No responses.</strong> Every model attempted but failed to respond, so there's no winner and no synthesis.
            </div>
            <!-- Multi-provider contest, no winner picked (winner === ""). The
                 "Winner:" line above suppresses; this states the verdict honestly
                 instead of leaving the grid headed straight into the claims with
                 no top-line outcome (mirrors the static page's "No winner"). -->
            <div v-if="!routingLabelFor(seg).winner && !isSoloFor(seg)" class="meta">
              <strong>No clear winner.</strong> The models split and the chairman didn't pick one.
            </div>
            <div v-if="routingLabelFor(seg).agreed_claims && routingLabelFor(seg).agreed_claims.length && !isSoloFor(seg)">
              <strong>Agreed claims</strong>
              <ul><li v-for="c in routingLabelFor(seg).agreed_claims">{{{{ c }}}}</li></ul>
            </div>
            <div v-if="routingLabelFor(seg).disagreed_claims && routingLabelFor(seg).disagreed_claims.length && !isSoloFor(seg)">
              <strong>Disagreed claims</strong>
              <ul>
                <li v-for="d in routingLabelFor(seg).disagreed_claims">
                  <span>{{{{ d.claim }}}}</span>
                  <span v-if="d.providers_for && d.providers_for.length" class="meta"> — for: {{{{ formatProviders(d.providers_for) }}}}</span>
                  <span v-if="d.providers_against && d.providers_against.length" class="meta"> · against: {{{{ formatProviders(d.providers_against) }}}}</span>
                  <div v-if="d.why_matters" class="meta">{{{{ d.why_matters }}}}</div>
                </li>
              </ul>
            </div>
            <div v-if="routingLabelFor(seg).user_likely_values && routingLabelFor(seg).user_likely_values.length">
              <strong>User-fit signals (from /me):</strong> <span class="meta">{{{{ routingLabelFor(seg).user_likely_values.join(', ') }}}}</span>
            </div>
            <div v-if="routingLabelFor(seg).routing_lesson">
              <strong>Routing lesson:</strong> <span class="meta">{{{{ (routingLabelFor(seg).routing_lesson || '').replace(/_/g, ' ') }}}}</span>
            </div>
            <div v-if="routingLabelFor(seg).eval_seed">
              <strong>How to verify next time:</strong> <span class="meta">{{{{ routingLabelFor(seg).eval_seed }}}}</span>
            </div>
          </div>
        </section>

        <section class="mb-lg" v-if="seg.expanded && memberRowsFor(seg).length">
          <h2>Full Responses</h2>
          <!-- The "Lens pick badge marks the chairman's pick" caption references a
               badge that isSoloFor suppresses on a 1-responder council (no contest,
               no pick over the others). Pointing the user at a badge that isn't on
               the page is the same solo overclaim — gate the badge-explainer on the
               non-solo case and state the honest solo framing instead. -->
          <p class="meta" v-if="seg.completed && !isSoloFor(seg)">Each member's full response below. The <strong>Lens pick</strong> badge marks the chairman's pick — synthesis is conditioned on your lens.</p>
          <!-- "The one model that answered" requires >= 1 responder — on a
               completed-but-all-failed (0-responder) council it's a flat lie
               (no model answered). The all-failed disclosure line below carries
               that state honestly. -->
          <p class="meta" v-if="seg.completed && isSoloFor(seg) && respondedMembersFor(seg) >= 1">The one model that answered. With a second provider enabled, the chairman would mark a <strong>Lens pick</strong> here.</p>
          <!-- Honest partial-council disclosure (#238 lineage). The lifecycle has
               THREE states, not two, and conflating "not completed" with "terminal"
               was a green-while-degraded bug: an IN-FLIGHT council (chairman still to
               run) is !seg.completed AND !seg.failed AND !seg.canceled. b4cbbae4 gated
               the terminal copy on bare !seg.completed, so a running 2-of-3 council
               with one live failure rendered "the council failed before the chairman
               ran" while it was genuinely still running (caught by
               test_sidepanel_failed_member_disclosure_browser). Four branches, each
               keyed on responder count × real lifecycle state:
                 1. COMPLETED + partial → synthesis is real but PARTIAL ("over the N
                    that responded").
                 2. TERMINAL (failed/canceled) + partial → the synthesis section is
                    suppressed for failed/canceled (v-if !seg.failed && !seg.canceled
                    above), so name what happened: answers landed, never synthesized.
                 3. RUNNING (in-flight) + partial → NEW: honest present tense, "the
                    council is running on the N that responded" — no completed-synthesis
                    claim, no failure claim.
                 4. 0 responders + TERMINAL → total failure, "no synthesis to show"
                    (gated on terminal so it can't false-fire mid-flight before the
                    survivors land). -->
          <p class="meta status-error" v-if="failedMembersFor(seg) > 0 && respondedMembersFor(seg) > 0 && seg.completed">⚠ {{{{ failedMembersFor(seg) }}}} provider{{{{ failedMembersFor(seg) === 1 ? '' : 's' }}}} attempted but failed and {{{{ failedMembersFor(seg) === 1 ? 'was' : 'were' }}}} excluded — this synthesis is over the {{{{ respondedMembersFor(seg) }}}} that responded.</p>
          <p class="meta status-error" v-if="failedMembersFor(seg) > 0 && respondedMembersFor(seg) > 0 && (seg.failed || seg.canceled)">⚠ {{{{ failedMembersFor(seg) }}}} provider{{{{ failedMembersFor(seg) === 1 ? '' : 's' }}}} failed and the council {{{{ seg.canceled ? 'was stopped' : 'failed' }}}} before the chairman ran — the {{{{ respondedMembersFor(seg) }}}} answer{{{{ respondedMembersFor(seg) === 1 ? '' : 's' }}}} below {{{{ respondedMembersFor(seg) === 1 ? 'was' : 'were' }}}} never synthesized.</p>
          <p class="meta status-error" v-if="failedMembersFor(seg) > 0 && respondedMembersFor(seg) > 0 && !seg.completed && !seg.failed && !seg.canceled">⚠ {{{{ failedMembersFor(seg) }}}} provider{{{{ failedMembersFor(seg) === 1 ? '' : 's' }}}} attempted but failed and {{{{ failedMembersFor(seg) === 1 ? 'was' : 'were' }}}} excluded — the synthesis will run over the {{{{ respondedMembersFor(seg) }}}} that responded.</p>
          <p class="meta status-error" v-if="failedMembersFor(seg) > 0 && respondedMembersFor(seg) === 0 && (seg.completed || seg.failed || seg.canceled)">⚠ Every provider attempted but failed to respond — there's no synthesis to show.</p>
          <div :class="memberRowsFor(seg).length === 3 ? 'answers-grid answers-grid-three' : 'answers-grid'">
            <article
              class="provider-status-row"
              :class="{{ 'winner-reveal': seg.completed && isLensPick(seg, row) }}"
              v-for="row in memberRowsFor(seg)"
              :key="row.provider"
            >
              <div class="provider-status-header">
                <div class="provider-status-name">{{{{ row.label }}}}</div>
                <div class="provider-status-badge" :class="row.statusClass" v-if="row.statusClass !== 'done'">{{{{ row.statusLabel }}}}</div>
                <div class="provider-status-badge done" v-if="isLensPick(seg, row)">Lens pick</div>
                <!-- Quote ↓ feeds the shared `.chain-refine-input` (the "Continue
                     the thread" composer), which only exists when `canChainNext &&
                     !chainBusy`. Gate the button on the SAME condition: while a
                     chain round is still running (canChainNext false) or a refine
                     is in flight (chainBusy), the composer is hidden, so a visible
                     Quote button would silently append to an OFF-SCREEN textarea and
                     dead-end — the tooltip promises "into the refinement input below"
                     but there is no input below (driven 2026-06-19: clicking it on a
                     prior completed round mid-chain changed nothing the user could
                     see). No composer → no Quote button. -->
                <button
                  type="button"
                  class="quote-member-btn"
                  v-if="canChainNext && !chainBusy && row.statusClass === 'done' && (row.responseText || row.responseHtml)"
                  @click.stop="quoteMember(row.provider, row)"
                  title="Quote this answer into the refinement input below"
                >Quote ↓</button>
              </div>
              <div class="provider-status-response markdown-body" v-if="row.responseHtml" v-html="row.responseHtml"></div>
              <pre class="provider-status-response" v-else-if="row.responseText">{{{{ row.responseText }}}}</pre>
              <div class="provider-status-detail" v-else :class="{{ empty: !row.detail }}">{{{{ row.detail }}}}</div>
            </article>
          </div>
        </section>
      </div>

      <!-- chainError banner — hoisted OUT of the chain-actions section
           because that section gates on `canChainNext` (only post-
           completion), which would hide Stop council's dispatch-failure
           banner during the running state when it's most needed.
           Stuck-launch sibling fix shipped 2026-05-26. -->
      <section class="card" v-if="chainError"
               style="margin-bottom: 16px; border-left: 3px solid #4f9095; background: rgba(79, 144, 149, 0.08); color: #714824; padding: 16px; overflow-wrap: anywhere; word-break: break-word; min-width: 0;"
               role="alert">
        <strong style="display: block; margin-bottom: 4px;">{{{{ chainErrorHeading }}}}</strong>
        <span style="display: block; font-size: 13px;">{{{{ chainError }}}}</span>
        <a href="#" @click.prevent="dismissChainError" style="display: inline-block; margin-top: 6px; color: #34666b; font-size: 12px;">Dismiss</a>
      </section>

      <section class="card chain-actions" v-if="canChainNext">
        <h2 v-if="!chainBusy" style="margin-top: 0;">Continue the thread</h2>
        <h2 v-if="chainBusy" style="margin-top: 0;">{{{{ chainStatusHeading }}}}</h2>
        <p class="meta" v-if="!chainBusy" style="margin-top: 4px;">
          Run another round where each model sees the others' answers and refines, or add a new directive to push the conversation in a new direction. Each round is a fresh full council — every model runs again — so it uses your subscription quota each time. <strong>Auto-chain</strong> runs up to 3 such rounds (stopping early once the models converge), so it can cost up to 3× a single round — useful when you suspect the first round missed something none of them flagged alone.
        </p>
        <div class="chain-loading" v-if="chainBusy" role="status" aria-live="polite">
          <span class="spinner" aria-hidden="true"></span>
          <span class="meta">{{{{ chainStatusDetail }}}}</span>
        </div>
        <div class="chain-button-row" v-if="!chainBusy">
          <button type="button" class="button primary" @click="startContinue">Continue (one round)</button>
          <button type="button" class="button ghost" @click="startAutoChain">Auto-chain (up to 3 rounds)</button>
        </div>
        <div class="chain-refine-row" v-if="!chainBusy">
          <textarea
            class="chain-refine-input"
            rows="2"
            aria-label="Refine directive for the next council round"
            v-model="refinePrompt"
            placeholder="Or refine with a new directive… (Quote ↓ stacks each member's answer here; ⌘/Ctrl+Enter to send)"
            @keydown.enter.meta.prevent="startRefine"
            @keydown.enter.ctrl.prevent="startRefine"
          ></textarea>
          <button type="button" class="button" :disabled="!refinePrompt.trim()" @click="startRefine">
            Refine
          </button>
        </div>
      </section>
    </main>
  </div>

  <script type="application/json" id="page-data">{page_data_script_json(page_data)}</script>
  <script src="{PETITE_VUE_IIFE}"></script>
  <script>
    const {{ createApp }} = window.__TRINITY_VUE__;
    const pageData = JSON.parse(document.getElementById('page-data').textContent);

    {launchpad_runtime_js()}

    function getParams() {{
      const params = new URLSearchParams(window.location.search);
      return {{
        statusToken: params.get('status_token') || '',
        councilId: params.get('council_id') || '',
        threadId: params.get('thread_id') || '',
        taskText: params.get('task') || '',
        fallbackMembers: (params.get('members') || '')
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
      }};
    }}

    window.__TRINITY_COUNCIL_THREAD__ = window.__TRINITY_COUNCIL_THREAD__ || {{}};

    function loadThreadScript(threadId, onComplete) {{
      if (typeof __trinityHostFetch === 'function' && __trinityHostFetch()) {{
        if (!threadId) {{ onComplete(null); return; }}
        __trinityHostQuery('thread_manifest', {{ thread_id: threadId }}, onComplete);
        return;
      }}
      const base = pageData.outcomeScriptBaseUrl || '';
      if (!base || !threadId) {{ onComplete(null); return; }}
      delete window.__TRINITY_COUNCIL_THREAD__[threadId];
      const script = document.createElement('script');
      // file:// URLs can't carry query strings — see launchpad_runtime.js comment.
      // Use the document's protocol instead of sniffing the (now-relative) base.
      const isFile = window.location.protocol === 'file:';
      const cacheBuster = isFile ? '' : '?t=' + Date.now();
      script.src = base + '/_thread_' + encodeURIComponent(threadId) + '.js' + cacheBuster;
      script.async = true;
      script.onload = () => {{
        // Coerce a wrong-type manifest root to null (sibling of the status /
        // outcome loader guards) — a string/number manifest would otherwise reach
        // `manifest?.segments` consumers with a truthy non-object.
        const manifest = __trinityCoerceObj(window.__TRINITY_COUNCIL_THREAD__?.[threadId]);
        onComplete(manifest);
        script.remove();
      }};
      script.onerror = () => {{ onComplete(null); script.remove(); }};
      document.body.appendChild(script);
    }}

    function outcomeToRunState(outcome) {{
      if (!outcome || typeof outcome !== 'object' || Array.isArray(outcome)) return null;
      // A corrupt/old-schema outcome can carry member_results as a wrong-TYPE
      // (string/number/object) or hold non-object elements. The loader coerces a
      // wrong-type ROOT to null, but an OBJECT outcome with a wrong-type
      // member_results still reaches here — `(outcome.member_results || []).map`
      // then throws "(...).map is not a function" (string outcome → undefined .map)
      // and strands the ?council_id= render BLANK with no honest "Could not load"
      // banner. Coerce to an array of objects up front.
      const memberList = Array.isArray(outcome.member_results)
        ? outcome.member_results.filter((m) => m && typeof m === 'object')
        : [];
      const memberOrder = memberList.map((m) => m.provider);
      const members = {{}};
      for (const m of memberList) {{
        members[m.provider] = {{
          status: 'done',
          model: m.model || '',
          response_text: m.output_text || '',
          response_html: m.output_html || '',
        }};
      }}
      const metadata = Object.assign({{}}, outcome.metadata || {{}});
      metadata.chairman_provider = outcome.primary_provider || metadata.chairman_provider || '';
      metadata.chairman_model = outcome.primary_model || metadata.chairman_model || '';
      metadata.council_id = outcome.council_run_id || metadata.council_id || '';
      // Outcome JSON has no top-level task_text — the writer puts it in
      // metadata.task_text so the post-hoc page can render it without
      // needing a second fetch of the bundle.
      return {{
        status: 'completed',
        statusToken: '',
        taskText: outcome.task_text || metadata.task_text || '',
        memberOrder,
        members,
        synthesis: {{
          status: 'done',
          response_text: outcome.synthesis_output_clean || outcome.synthesis_output || '',
          response_html: outcome.synthesis_html || '',
          routing_label: outcome.routing_label || null,
        }},
        metadata,
        review_path: '',
        error: '',
      }};
    }}

    // Translate a dispatch result into a message that's actually true for the
    // user's situation. The old fallback always said "is the Chrome extension
    // installed? Run install-extension" — which is wrong and frustrating when
    // the extension IS installed (founder report 2026-05-31: the dispatcher
    // had refused on a stale 'absent' probe flag, now fixed in
    // launchpad_runtime). Reason codes come from the dispatcher's onResult.
    function dispatchErrorMessage(r) {{
      const reason = r && r.reason;
      const respErr = r && r.response && r.response.error;
      if (reason === 'native-host-unavailable') {{
        return "Trinity's extension is connected, but its native messaging host isn't running. Re-wire it: run `trinity-local install-extension`.";
      }}
      if (respErr === 'rejected-sender') {{
        // Installed extension predates the council-page sender allowance
        // (v0.2.21). It's installed — it just needs a RELOAD, not a reinstall.
        return "Your installed Trinity extension is out of date and rejected this page. Reload it at chrome://extensions (it's already installed), then try again.";
      }}
      if (reason === 'extension-unreachable') {{
        return "Couldn't reach the Trinity extension — it may be disabled or its background worker didn't wake. Click again, or reload it at chrome://extensions, then retry.";
      }}
      if (r && r.response && r.response.error) return String(r.response.error);
      return "Couldn't start the next round — the Trinity extension didn't respond. Click again, or reload it at chrome://extensions.";
    }}

    function normalizeStatus(raw, fallback = null) {{
      if (!raw) {{
        return fallback;
      }}
      const memberMap = raw.members || fallback?.members || {{}};
      const fallbackOrder = fallback?.memberOrder || [];
      const rawOrder = raw.memberOrder || raw.metadata?.members || Object.keys(memberMap);
      return {{
        ...fallback,
        ...raw,
        statusToken: raw.statusToken || raw.status_token || fallback?.statusToken || '',
        taskText: raw.taskText || raw.task_text || fallback?.taskText || '',
        activeProvider: raw.activeProvider || raw.active_provider || fallback?.activeProvider || null,
        activeProviders: raw.activeProviders || raw.active_providers || fallback?.activeProviders || [],
        memberOrder: rawOrder?.length ? rawOrder : fallbackOrder,
        members: memberMap,
        synthesis: raw.synthesis || fallback?.synthesis || {{}},
        reviewPath: raw.reviewPath || raw.review_path || fallback?.reviewPath || '',
        error: raw.error || fallback?.error || '',
      }};
    }}

    // Provider slug normalizer — the harness rename gemini → antigravity
    // happened 2026-05-20, but historical council_outcomes/*.json files on
    // disk still carry provider="gemini". Centralize the alias here so the
    // canonical labels map keys on "antigravity" only. Delete this helper
    // when historical outcomes are far enough in the past to stop caring.
    function normalizeProviderSlug(slug) {{
      // Canonicalize web-era capture slugs to their CLI provider so member /
      // winner labels resolve to one model brand (Claude/GPT/Gemini, #275), not
      // "Chatgpt" / "Claude Ai". 514 of the founder's councils carry these
      // capture slugs. Mirrors council_schema._LEGACY_PROVIDER_ALIASES (the
      // Python on-disk→canonical boundary) for the three capture slugs.
      const aliases = {{ gemini: 'antigravity', chatgpt: 'codex', claude_ai: 'claude' }};
      return aliases[slug] || slug;
    }}

    function formatProviderLabel(provider) {{
      if (!provider) {{
        return '';
      }}
      const normalized = normalizeProviderSlug(String(provider).trim().toLowerCase());
      // #275: provider/winner labels read as the MODEL BRAND (Claude / GPT /
      // Gemini), matching the launchpad panel + the eval surfaces (folded in
      // 2026-06-06, founder call) — so a council launched in the popup reads the
      // same brand here on its review page, not the old harness trio.
      const labels = {{
        claude: 'Claude',
        antigravity: 'Gemini',
        codex: 'GPT',
        mlx: 'MLX',
        openai: 'GPT',
      }};
      if (labels[normalized]) {{
        return labels[normalized];
      }}
      return normalized
        .split(/[_\\s-]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
    }}

    function makeSegment({{statusToken='', councilId='', taskText='', refinementText='', members=[], roundNumber=1}}) {{
      const status = statusToken ? 'running' : 'pending';
      return {{
        key: 'seg_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8),
        councilId,
        statusToken,
        taskText,
        refinementText,
        runState: normalizeStatus({{
          status,
          statusToken,
          taskText,
          memberOrder: members,
          members: Object.fromEntries(members.map((p) => [p, {{ status: 'pending' }}])),
          synthesis: {{ status: 'pending' }},
        }}, null),
        busy: !!statusToken,
        completed: false,
        failed: false,
        canceled: false,
        errorText: '',
        roundNumber: roundNumber || 1,
        converged: false,
        currentStatusIndex: 0,
        expanded: true,
      }};
    }}

    function LiveCouncilApp(pageData) {{
      const params = getParams();
      return {{
        threadTaskText: params.taskText,
        threadId: params.threadId || '',
        threadViewUrl: '',
        segments: [],
        statusPollHandle: null,
        statusRotateHandle: null,
        chainBusy: false,
        chainStatusHeading: '',
        chainStatusDetail: '',
        refinePrompt: '',
        // Stuck-launch sibling fix shipped 2026-05-26: chainError surfaces
        // dispatch failures outside the chainBusy guard so the user
        // actually sees them; _pendingChainSegmentToken tracks the
        // optimistic segment for rollback on failure.
        chainError: '',
        // The banner heading. Defaults to the REACTIVE failure phrasing
        // ("Could not start next round") for the Continue/Auto-chain/Refine
        // dispatch failures that set chainError after a user action. The
        // PROACTIVE on-load staleness probe (init(), below) overrides it —
        // it fires before any round is attempted, so "Could not start next
        // round" would assert a failure that never happened (UX sweep: a
        // user opening a completed council with an out-of-date extension saw
        // "Could not start next round" before touching anything).
        chainErrorHeading: 'Could not start next round',
        _pendingChainSegmentToken: '',
        // Immediate-ACK flag for Stop council (sibling of the launchpad fix
        // shipped 2026-06-17). The stop dispatch SUCCEEDS but the actual cancel
        // lands LATER — the host writes a 'canceled' status the poller is waiting
        // on. Without this flag, for that whole gap the button still reads "Stop
        // council", isn't disabled, and the spinner keeps cycling its witty
        // messages — so the click reads as a no-op and the user clicks again (the
        // founder's NO-FEEDBACK lineage). Set true the instant Stop is clicked:
        // the button flips to "Stopping…" (disabled, no double-fire) and the
        // status pins "Stopping the council…" until the poller finalizes to
        // canceled (startPolling / clearPolling / a new chain round reset it).
        stopRequested: false,
        formatProviderLabel(provider) {{ return formatProviderLabel(provider); }},
        formatProviders(names) {{
          if (!Array.isArray(names)) return '';
          return names.map((n) => formatProviderLabel(n)).join(', ');
        }},
        get anyBusy() {{
          return this.segments.some((s) => s.busy);
        }},
        get liveAnnouncement() {{
          // Persistent visually-hidden aria-live mirror (WCAG 4.1.3 Status
          // Messages). The visible status cards are created/destroyed by v-if as
          // the poll advances (.launch-status → .winner-verdict on completion), and
          // a live region that is INSERTED already-populated is not reliably
          // announced by screen readers. This region exists from first render and
          // only its TEXT mutates, so every transition — a round starting, the
          // council completing, a failure/stop — is spoken. Mirrors the visible
          // copy so sighted and SR users hear the same thing.
          const segs = this.segments;
          if (!segs.length) return '';
          const seg = segs[segs.length - 1];
          const round = seg.roundNumber || segs.length;
          if (seg.busy) {{
            return `Council round ${{round}} running. ${{this.currentStatusMessageFor(seg)}}`;
          }}
          if (seg.failed) return `Council round ${{round}} failed.`;
          if (seg.canceled) return `Council round ${{round}} stopped.`;
          if (seg.completed) {{
            // 0 responders (a completed-but-all-failed corrupt/hand-edited status):
            // "One model answered" is a flat lie spoken to a screen-reader user
            // (every member failed). Announce the total failure honestly instead.
            if (this.isSoloFor(seg) && this.respondedMembersFor(seg) === 0) return `Council round ${{round}} complete. Every model failed to respond — no synthesis.`;
            if (this.isSoloFor(seg)) return `Council round ${{round}} complete. One model answered — no council.`;
            const pick = this.lensPickProviderFor(seg);
            if (pick) return `Council round ${{round}} complete. ${{this.formatProviderLabel(pick)}} — the answer you'd have picked.`;
            return `Council round ${{round}} complete.`;
          }}
          return '';
        }},
        get threadTaskTextDisplay() {{
          // Strip the `---` fence lines from the prior-context block so the
          // review page reads as prose instead of leftover Markdown. The raw
          // text with fences is what the models see — this is display only.
          const raw = (this.threadTaskText || '').trim();
          if (!raw) return '';
          return raw.split('\\n').filter((line) => line.trim() !== '---').join('\\n').trim();
        }},
        get canChainNext() {{
          if (this.segments.length === 0) return false;
          const last = this.segments[this.segments.length - 1];
          // SOLO suppression (the isSoloFor class, applied to the chain composer):
          // when only ONE provider responded, the "Continue the thread" composer's
          // entire value proposition is a multi-model dynamic that can't happen —
          // its copy promises "each model sees the OTHERS' answers and refines" and
          // "every model runs again", and the Quote ↓ buttons stack "each member's
          // answer". With one model there are no others to see, nothing to stack,
          // and Auto-chain would re-run the same single model up to 3× (burning
          // quota) for a dynamic that requires a second voice. The solo-verdict line
          // right above already tells the user the honest next step ("Enable a
          // second provider to run a real contest"), so offering a multi-model
          // composer below it is a direct self-contradiction — the live-page twin of
          // the winner/agreed-claims/routing-label solo suppression already on this
          // page (isSoloFor). Gate the composer (and its Quote ↓ buttons, which share
          // canChainNext) off on a solo council; re-enable once a real contest exists.
          if (this.isSoloFor(last)) return false;
          return !!(last.completed && last.councilId);
        }},
        currentStatusMessageFor(seg) {{
          // Immediate, honest status after Stop is clicked — the cancel is
          // dispatched but lands later (the poller writes 'canceled'); pin the
          // message so the spinner stops cycling witty lines that read like the
          // click did nothing. Only the LAST busy segment carries the Stop button,
          // so only it shows the stopping override.
          if (this.stopRequested && seg === this.segments[this.segments.length - 1]) {{
            return 'Stopping the council…';
          }}
          const message = pageData.loadingMessages[seg.currentStatusIndex % pageData.loadingMessages.length] || 'Working...';
          const synthesisStatus = seg.runState?.synthesis?.status;
          if (synthesisStatus === 'running') {{
            return 'Synthesizing the strongest answer...';
          }}
          const active = seg.runState?.activeProvider;
          if (active) {{
            return `${{formatProviderLabel(active)}}: ${{message}}`;
          }}
          return message;
        }},
        failedMembersFor(seg) {{
          // Providers that were dispatched but errored — surface a count so a
          // partial council reads honestly (the walkover says "Sole entrant",
          // the eval card discloses excluded_runs — the #238 honest-degradation
          // lineage). Count-only (no provider names) to stay clear of the #275
          // slug-vs-brand display call.
          //
          // The casualty count lives in TWO disjoint channels depending on the
          // render path, so read BOTH and take the MAX:
          //   • OUTCOME path (?council_id=): outcomeToRunState builds the members
          //     map ONLY from member_results (all status:'done'), so the failed
          //     provider has no row — the count must come from
          //     metadata.failed_members (which the runner writes into the final
          //     outcome).
          //   • POLL path (?status_token=): update_member_failure flips the
          //     member to status:'failed' so its row IS in the map, but the status
          //     payload's metadata is seeded at launch and NEVER gets
          //     failed_members (the runner only writes that into the persisted
          //     outcome, not the status sidecar). So on a live 2-of-3 poll the
          //     "⚠ 1 provider attempted but failed" note SILENTLY VANISHED even
          //     though the page rendered a visible "Failed" badge row — the
          //     honest-degradation disclosure contradicted what the same page
          //     already showed. Counting failed rows recovers it from the SAME
          //     source the row badge already trusts.
          // MAX (not sum): the two channels are mutually exclusive by
          // construction (a failed provider is in metadata.failed_members on the
          // outcome path OR a failed ROW on the poll path, never both), so MAX
          // can't double-count while staying correct if a future state carries
          // both.
          const fm = seg.runState?.metadata?.failed_members;
          const fromMeta = Array.isArray(fm) ? fm.length : 0;
          const fromRows = this.memberRowsFor(seg).filter((row) => row.statusClass === 'failed').length;
          return Math.max(fromMeta, fromRows);
        }},
        respondedMembersFor(seg) {{
          // The count for "this synthesis is over the N that responded".
          // MUST be the members that actually RESPONDED (status === 'done'),
          // NOT memberRowsFor().length — on the status (poll) render path the
          // members map RETAINS the failed member (update_member_failure writes
          // status:'failed'), so memberRowsFor includes the casualty's row.
          // Using the row count made the banner read "1 failed and was excluded
          // — this synthesis is over the 3 that responded" on a 2-of-3 council
          // (a self-contradicting count: excluding 1 of 3 leaves 2). The
          // ?council_id= path was already correct because outcomeToRunState
          // builds members only from member_results (responders) — this aligns
          // the poll path with it. (#238 honest-degradation lineage.)
          return this.memberRowsFor(seg).filter((row) => row.statusClass === 'done').length;
        }},
        isSoloFor(seg) {{
          // A council needs at least TWO voices to have a contest. When only
          // ONE member responded (a single provider was enabled, OR every
          // other member failed leaving one), there's no one to "win" against
          // and no one to "agree" with — so the chairman's winner-verdict
          // ("X — the answer you'd have picked"), the "Winner:" line, and the
          // "Agreed claims" block are degenerate competition framing that
          // OVERCLAIMS (the same #35 green-while-degenerate the SHARE CARD
          // suppresses in council_card.py's `solo = len(members) <= 1` branch —
          // this is the live-page twin that drifted unfixed). Gate on the
          // RESPONDER count (status:'done'), not the row count: on the poll
          // path the members map retains failed rows, so a 2-attempted/1-
          // responded council must still read as solo. Only applies once the
          // segment is completed — a mid-poll single 'done' row isn't yet a
          // verdict, just the first member landing.
          return seg.completed && this.respondedMembersFor(seg) <= 1;
        }},
        memberRowsFor(seg) {{
          const memberMap = seg.runState?.members || {{}};
          const providers = Object.keys(memberMap).length ? Object.keys(memberMap) : (seg.runState?.memberOrder || []);
          // On a TERMINAL segment (council failed or was stopped), a member
          // that's still 'pending'/'running' in the status map never finished —
          // it didn't run / was cut off. Rendering it as "Queued · Queued."
          // under a "Council failed" / "Council stopped" banner is a flat
          // contradiction (it reads as "still working"). Coerce the effective
          // status so the badge + detail tell the truth (founder symptom: stale
          // Queued rows on a dead council). 'done' and 'failed' members are
          // already terminal — keep them (a stopped council can still have one
          // real answer landed; an all-fail council shows Failed per provider).
          const terminal = seg.failed ? 'failed-council' : (seg.canceled ? 'canceled-council' : '');
          return providers.map((provider, idx) => {{
            const item = memberMap[provider] || {{}};
            let status = item.status || 'pending';
            if (terminal && (status === 'pending' || status === 'running')) {{
              status = terminal === 'failed-council' ? 'didnt-run' : 'stopped';
            }}
            const baseLabel = formatProviderLabel(provider);
            // Per-run captured model (from the status JSON, written by
            // council_runner). Falls back to the static config model
            // map so the chip still appears during pre-run states
            // (QUEUED) and for runs that didn't write model to the
            // status (legacy state files). Effort isn't yet captured
            // per-run; fall back to static config + agy slash-command
            // persistence map. Chip renders ONLY when a value resolves
            // — agy users without `/model` set show just the base label
            // (rather than a "(unknown)" placeholder that looks like a bug).
            const model = item.model || (pageData.providerModels && pageData.providerModels[provider]) || '';
            const effort = (pageData.providerEfforts && pageData.providerEfforts[provider]) || '';
            // Compose: "Claude · opus-4-7 · effort:high"
            // (model only) or (effort only) variants supported.
            const chipBits = [baseLabel];
            if (model) chipBits.push(model);
            if (effort) chipBits.push(`effort:${{effort}}`);
            const label = chipBits.join(' · ');
            return {{
              provider,
              answerLabel: String.fromCharCode(65 + idx),
              label,
              statusLabel: status === 'done' ? 'Done' : status === 'failed' ? 'Failed' : status === 'running' ? 'Running' : status === 'didnt-run' ? "Didn't run" : status === 'stopped' ? 'Stopped' : 'Queued',
              // 'didnt-run' / 'stopped' carry the muted 'pending' badge style — a
              // never-ran provider is NOT an error (no red), and keeping them OUT
              // of statusClass:'failed' keeps failedMembersFor() honest (the
              // "N attempted but failed" disclosure counts genuine errors only).
              statusClass: status === 'done' ? 'done' : status === 'failed' ? 'failed' : status === 'running' ? 'running' : 'pending',
              responseHtml: status === 'done' ? (item.response_html || '') : '',
              responseText: status === 'done' ? (item.response_text || item.reasoning_summary || '') : '',
              // The detail line only renders when BOTH responseHtml AND
              // responseText are empty (template: v-else after the two response
              // branches). For a 'done' member that means it ANSWERED but
              // returned no usable text — a rc0/empty-stdout success the lenient
              // result_hard_failed() lets through as a recorded member_result
              // (council_runner: `output_text = result.stdout or result.stderr
              // or ""`). The old ternary had NO 'done' branch, so an empty-output
              // responder fell through to 'Queued.' — a flat contradiction on a
              // COMPLETED council (verdict already shown, the member card reads
              // "still queued"). The launchpad running-card detail (launchpad_
              // template.py: `status === 'done' ? 'Response ready.'`) already
              // carried a 'done' branch; this live-page sibling was the one that
              // drifted. Tell the truth: it returned an empty response.
              detail: status === 'done'
                ? 'Returned an empty response.'
                : status === 'failed'
                ? (item.reasoning_summary || 'Provider failed.')
                : status === 'running'
                  ? (item.reasoning_summary || 'Working...')
                  : status === 'didnt-run'
                    ? "Didn't run — the council failed before this provider responded."
                    : status === 'stopped'
                      ? 'Stopped before this provider answered.'
                      : 'Queued.',
            }};
          }});
        }},
        routingLabelFor(seg) {{
          return seg.runState?.synthesis?.routing_label || null;
        }},
        analysisRowFor(seg) {{
          const synthesisStatus = seg.runState?.synthesis?.status || 'pending';
          const memberPending = this.memberRowsFor(seg).some((row) => row.statusClass === 'pending' || row.statusClass === 'running');
          const chairmanProvider = seg.runState?.metadata?.chairman_provider || '';
          const chairmanModel = seg.runState?.metadata?.chairman_model || '';
          const chairmanLabel = chairmanProvider
            ? formatProviderLabel(chairmanProvider) + (chairmanModel ? ` (${{chairmanModel}})` : '')
            : '';
          const analysisLabel = chairmanLabel ? `Analysis · ${{chairmanLabel}}` : 'Analysis';
          const synthesisHtml = seg.runState?.synthesis?.response_html || '';
          const synthesisText = seg.runState?.synthesis?.response_text || '';
          return {{
            label: analysisLabel,
            statusLabel: synthesisStatus === 'done' ? 'Done' : synthesisStatus === 'failed' ? 'Failed' : synthesisStatus === 'running' ? 'Running' : 'Queued',
            statusClass: synthesisStatus === 'done' ? 'done' : synthesisStatus === 'failed' ? 'failed' : synthesisStatus === 'running' ? 'running' : 'pending',
            responseHtml: synthesisStatus === 'done' ? synthesisHtml : '',
            responseText: synthesisStatus === 'done' && !synthesisHtml ? synthesisText : '',
            detail: synthesisStatus === 'done'
              ? (synthesisHtml || synthesisText ? '' : 'Final comparison complete.')
              : synthesisStatus === 'failed'
                ? 'Final comparison failed.'
                : synthesisStatus === 'running'
                  ? 'Comparing responses and writing the final recommendation.'
                  : memberPending
                    ? 'Waiting for member responses.'
                    : 'Ready to start final comparison.',
          }};
        }},
        toggleSegment(key) {{
          const idx = this.segments.findIndex((s) => s.key === key);
          if (idx === -1) return;
          this._patchSegment(key, {{ expanded: !this.segments[idx].expanded }});
        }},
        lensPickProviderFor(seg) {{
          // Source the "Lens pick" badge from the chairman's
          // routing_label.winner — chairman synthesis is conditioned on
          // the user's lens, so the chairman's pick IS the supervision
          // signal. Empty string when the routing_label is missing.
          return this.routingLabelFor(seg)?.winner || '';
        }},
        isLensPick(seg, row) {{
          // SOLO suppression (the isSoloFor class). The "Lens pick" badge AND the
          // `winner-reveal` row highlight (both gate on this getter) frame a row as
          // the chairman's pick OVER THE OTHERS — degenerate competition framing on
          // a 1-responder council, where there are no others to be picked over. The
          // chairman still emits routing_label.winner = the lone provider on a solo
          // council, so without this gate the badge + highlight rendered on the lone
          // member directly under the page's own "One model answered — no council"
          // verdict (the winner-verdict / Winner: line / Agreed claims already
          // suppress via isSoloFor; the badge was the solo-blind sibling that
          // drifted unfixed — the share card never paints a per-member winner mark
          // at all in its `solo` branch). One source of truth: gate here, so BOTH
          // the badge and the highlight collapse together.
          if (this.isSoloFor(seg)) return false;
          // MUST normalize BOTH sides: the winner (routingLabelFor.winner) is the
          // canonical-on-disk slug, but member rows carry RAW web-capture slugs
          // (claude_ai / gemini / chatgpt) on 41% of the founder's real councils. A
          // raw `winner === row.provider` compares 'claude' === 'claude_ai' → false,
          // so the badge silently vanished on every web-capture council (found by
          // the 2026-06-09 trust-defect sweep). normalizeProviderSlug is in closure.
          const win = normalizeProviderSlug(String(this.lensPickProviderFor(seg)).trim().toLowerCase());
          const prov = normalizeProviderSlug(String(row.provider || '').trim().toLowerCase());
          return !!win && win === prov;
        }},
        quoteMember(provider, row) {{
          // Append a quoted fragment of this member's response into the
          // shared refinement input. The user's hand-rolled flow on
          // bundle_42f8cea9c9e705e5 was: see Gemini's "Own your context",
          // see Claude's response, type a merged directive freehand.
          // This shortcut lets multi-quote stack — each click adds one
          // attribution block, the user types the merge instruction on
          // top. Chairman still runs the synthesis; only the input UX
          // changes. Quote is capped at 300 chars so the textarea stays
          // legible; the user can edit the truncation if they want more.
          const text = (row && (row.responseText || (row.responseHtml || '').replace(/<[^>]+>/g, ''))) || '';
          if (!text.trim()) return;
          const QUOTE_CAP = 300;
          const trimmed = text.trim().length > QUOTE_CAP
            ? text.trim().slice(0, QUOTE_CAP).trimEnd() + '…'
            : text.trim();
          const block = '> [' + formatProviderLabel(provider) + ']: ' + trimmed + '\\n';
          // Preserve any existing user-typed directive; append the
          // quote with a blank-line separator if there's prior content.
          this.refinePrompt = (this.refinePrompt && this.refinePrompt.trim())
            ? this.refinePrompt.replace(/\\s+$/, '') + '\\n\\n' + block
            : block;
          // Drop focus on the refine input so the user lands ready to
          // type their merge directive.
          this.$nextTick && this.$nextTick(() => {{
            const input = document.querySelector('.chain-refine-input');
            if (input) input.focus();
          }});
        }},
        dismissChainError() {{
          // Clearing chainError removes the `v-if`-gated <section role=alert> that
          // CONTAINS the Dismiss link the user just activated — so the focused
          // element vanishes and focus falls to <body>, stranding a keyboard user
          // at the top of the document (WCAG 2.4.3, the same focus-loss class as
          // the launchpad _restoreTriggerFocus). Re-home to a stable, always-present
          // anchor (the back-nav) after the DOM patch so they land somewhere
          // sensible instead of nowhere.
          this.chainError = '';
          requestAnimationFrame(() => {{
            const target = document.querySelector('.topbar-back');
            if (target && typeof target.focus === 'function') target.focus();
          }});
        }},
        init() {{
          if (params.threadId) {{
            this.loadThread(params.threadId);
          }} else if (params.statusToken) {{
            const seg = makeSegment({{
              statusToken: params.statusToken,
              taskText: params.taskText,
              members: params.fallbackMembers,
            }});
            this.segments.push(seg);
            this.startPolling();
          }} else if (params.councilId) {{
            const seg = makeSegment({{ councilId: params.councilId, taskText: params.taskText }});
            this.segments.push(seg);
            this._loadOutcomeIntoSegment(seg, params.councilId);
          }} else {{
            // No URL params. Over file:// (the popup's "Open council page" path)
            // macOS strips the ?status_token=… query, so the page lands bare and
            // renders nothing (founder report 2026-06-12). Recover which council
            // to show from the sidecar the host wrote next to this page.
            loadActiveCouncilScript((active) => {{
              if (active && active.status_token) {{
                const seg = makeSegment({{
                  statusToken: active.status_token,
                  taskText: active.task || '',
                  members: Array.isArray(active.members) ? active.members : [],
                }});
                this.segments.push(seg);
                this.startPolling();
              }}
            }});
          }}
          // Proactively warn if the installed extension is too OLD to accept
          // this council page as a dispatch sender (it rejects with
          // 'rejected-sender' → dispatcher state 'stale'). Surface the reload
          // hint on load — before the user clicks Refine and hits the rejection
          // (founder report 2026-05-31). A reload, not a reinstall, fixes it.
          const _disp = window.__TRINITY_DISPATCH__;
          if (_disp) {{
            const _STALE_HEADING = 'Your Trinity extension is out of date';
            const _onState = (st) => {{
              if (st === 'stale' && !this.chainError) {{
                // Proactive, fires on page load BEFORE any round is attempted —
                // so the heading must NOT claim a round failed. Lead with the
                // actual situation (the extension is out of date).
                this.chainErrorHeading = _STALE_HEADING;
                this.chainError = "Refine / Continue won't work until you reload it at chrome://extensions.";
              }} else if (st !== 'stale' && this.chainErrorHeading === _STALE_HEADING && this.chainError) {{
                // The user did exactly what the warning prescribed — reloaded the
                // extension at chrome://extensions — and a refocus re-probe now
                // reports a healthy state. CLEAR the proactive stale warning so it
                // doesn't keep lying after it's been fixed (the NO-FEEDBACK stale-
                // state class: a corrective action succeeded but the UI kept the
                // now-false banner up). Gated on the stale HEADING so a reactive
                // round-start failure banner (a different heading) is never wiped
                // by an unrelated state transition.
                this.chainError = '';
                this.chainErrorHeading = 'Could not start next round';
              }}
            }};
            _disp.onStateChange(_onState);
            Promise.resolve(_disp.probe(true)).then(_onState).catch(() => {{}});
          }}
        }},
        loadThread(threadId) {{
          loadThreadScript(threadId, (manifest) => {{
            // A corrupt manifest can carry segments as a wrong-TYPE (string/number);
            // `.forEach` below would throw. Array.isArray-gate it (sibling of the
            // outcomeToRunState member_results guard).
            const manifestSegments = Array.isArray(manifest?.segments) ? manifest.segments : [];
            if (!manifestSegments.length) {{
              // Fallback: treat threadId as a council_id
              const seg = makeSegment({{ councilId: threadId }});
              this.segments.push(seg);
              this._loadOutcomeIntoSegment(seg, threadId);
              return;
            }}
            // Build segments in manifest order. Pending entries (running, no
            // council_id yet) become live-polling segments. Completed entries
            // load outcome JSONP. Mixed manifests get both — so opening the
            // thread tile mid-round shows prior completed rounds AND the
            // currently-streaming round in the right slot.
            let pendingPolledForKey = null;
            manifestSegments.forEach((entry, idx) => {{
              // The manifest POSITION (0-based idx) is authoritative for chain
              // order — segments are built in manifest order, so the round number
              // IS the 1-based position. The per-entry `round_number` FIELD is
              // only best-effort: real threads carry round_number == 1 for EVERY
              // segment (a manifest-writer bug — outcome.metadata.round_number is
              // also absent), which made a 5-round thread mis-render as
              // "Round 1 ×5" (verified 2026-06-05). So take the position as the
              // floor and trust round_number only when it runs AHEAD (a gap, e.g.
              // a deleted middle round); never let a stale/degenerate field
              // collapse the whole thread to Round 1.
              const roundNo = Math.max(entry.round_number || 0, idx + 1);
              if (entry.running && entry.status_token) {{
                const seg = makeSegment({{
                  statusToken: entry.status_token,
                  taskText: this.threadTaskText,
                  members: [],
                }});
                seg.roundNumber = roundNo;
                this.segments.push(seg);
                pendingPolledForKey = seg.key;
              }} else if (entry.council_id) {{
                const seg = makeSegment({{
                  councilId: entry.council_id,
                  roundNumber: roundNo,
                }});
                this.segments.push(seg);
                this._loadOutcomeIntoSegment(seg, entry.council_id);
              }}
            }});
            // Start polling the latest pending segment so it streams live.
            if (pendingPolledForKey) {{
              this.startPolling();
            }}
          }});
        }},
        _loadOutcomeIntoSegment(seg, councilId) {{
          loadOutcomeScript(councilId, (outcome) => {{
            const idx = this.segments.findIndex((s) => s.key === seg.key);
            if (idx === -1) return;
            // Replace the segment object via splice so petite-vue's array
            // proxy fires reactivity. Direct property mutation on the
            // existing object is unreliable when the object was created in
            // a sync push and only nested-property-mutated from async load.
            const current = this.segments[idx];
            if (!outcome) {{
              // The outcome JSON is a SECONDARY re-hydration to the canonical
              // record. The status poll already delivered the full completed
              // runState (synthesis prose + routing_label + member answers) and
              // flipped the segment to completed before this fires (see the
              // status==='completed' branch in startPolling). The outcome file
              // is a SEPARATE write that races the status flip and is pruned
              // after 14d — so a 404/transient failure here is NORMAL and must
              // NOT clobber an already-good result. Without this guard a
              // SUCCESSFUL completed council rendered a red "Council failed /
              // Could not load council outcome" banner ABOVE its own synthesis
              // (driven 2026-06-17). Only surface the failure when the outcome
              // was this segment's PRIMARY source (the ?council_id= init path,
              // which has no prior status poll) — i.e. it isn't already
              // completed with a done synthesis to show.
              const alreadyHasResult = !!(
                current.completed && current.runState
                && current.runState.synthesis
                && current.runState.synthesis.status === 'done'
              );
              if (alreadyHasResult) {{
                return;
              }}
              this.segments.splice(idx, 1, Object.assign({{}}, current, {{
                failed: true,
                errorText: 'Could not load council outcome.',
                busy: false,
              }}));
              return;
            }}
            const rs = outcomeToRunState(outcome);
            if (!rs) return;
            // The "Lens pick" badge is sourced from
            // routingLabelFor(seg).winner via lensPickProviderFor — no
            // per-segment selection state needed since the rating
            // click-flow was retired 2026-05-22 (Phase 3d).
            // user_refinement is the directive the user typed to launch
            // this round (council-iterate / launchpad refinement input).
            // Source: outcome.metadata.user_refinement (written by
            // council_runner when chaining). The eyebrow row renders it
            // as "↳ <text>" so the user's contribution to the chain is
            // visible on reload — without this, every round past 1
            // looked like it had no human input.
            const refinementText = (
              rs.metadata?.user_refinement ||
              current.refinementText ||
              ''
            );
            const next = Object.assign({{}}, current, {{
              runState: rs,
              taskText: rs.taskText || current.taskText,
              councilId: rs.metadata?.council_id || councilId,
              refinementText,
              busy: false,
              failed: false,
              canceled: false,
              completed: true,
              // Floor the round number on the manifest POSITION (current.roundNumber,
              // already set to Math.max(entry.round_number, idx+1) at segment build),
              // never let the outcome's metadata.round_number collapse it. Real
              // threads carry round_number == 1 on EVERY segment's outcome metadata
              // (the same manifest-writer bug the build path defends at line ~2087),
              // so trusting `rs.metadata.round_number` FIRST clobbered a correctly
              // numbered Round 3 back to "Round 1" — a 3-round chain mis-rendered
              // "Round 1 ×3" the instant each completed segment loaded its outcome.
              // Mirror the build-path discipline: take the field only when it runs
              // AHEAD of the position floor (a gap), never when it's behind.
              roundNumber: Math.max(rs.metadata?.round_number || 0, current.roundNumber || 1),
              converged: !!rs.metadata?.converged,
            }});
            this.segments.splice(idx, 1, next);
            if (!this.threadTaskText) {{
              this.threadTaskText = rs.taskText || this.threadTaskText;
            }}
            // Track the chain root id so we can show "View full thread"
            // when this single-council page is part of a multi-segment chain.
            // Only probe when chain_root_id is EXPLICITLY present — a real
            // threaded council carries it (a `bundle_<hash>` id, so the manifest
            // is `_thread_bundle_<hash>.js`). The old `|| next.councilId`
            // fallback probed `_thread_council_<id>.js` for standalone councils
            // (555/562 = 98.8% have no chain_root_id), a file that can NEVER
            // exist — logging a 404 on the core product page for nearly every
            // council view. Standalones simply have no thread to offer.
            // ...AND it must be a thread-manifest key (a `bundle_<hash>`), not a
            // council id. A stale/legacy outcome can carry chain_root_id ==
            // parent_council_id (a `council_<hash>`, from before the bundle-keyed
            // migration); probing `_thread_council_<id>.js` for those ALSO 404s
            // forever (manifests are NEVER keyed by a council id) — the same
            // dead-path class this guard already fixes for the standalone case.
            // Verified 2026-06-05 driving a real chain council whose .js still
            // carried the legacy council-id root.
            const chainRoot = rs.metadata?.chain_root_id;
            const chainRootIsThreadKey = chainRoot && chainRoot.indexOf('council_') !== 0;
            if (chainRootIsThreadKey && this.segments.length === 1 && !this.threadId) {{
              this._maybeOfferThreadLink(chainRoot);
            }}
          }});
        }},
        _maybeOfferThreadLink(chainRootId) {{
          // Probe the thread manifest. If it has more than one segment,
          // surface the "View full thread" button.
          loadThreadScript(chainRootId, (manifest) => {{
            const count = (Array.isArray(manifest?.segments) ? manifest.segments : []).length;
            if (count > 1) {{
              const url = new URL(window.location.href);
              url.search = '?thread_id=' + encodeURIComponent(chainRootId);
              this.threadViewUrl = url.toString();
            }}
          }});
        }},
        canRetryFailed(seg, segIndex) {{
          // A terminal FAILED council (not canceled — a stop was deliberate) is a
          // DEAD END otherwise: the chain composer ("Continue the thread") gates on
          // `canChainNext` = last.completed && last.councilId, which a failed council
          // has NEITHER of — so the page offered no way forward and the user was
          // stranded on the failure banner (founder symptom: "Council failed" with
          // zero buttons). Re-running the SAME task is the obvious next step, and the
          // page already holds everything it needs: threadTaskText (the poll reads it
          // off the status sidecar before the failed branch). Only the LAST segment
          // can retry — re-running an earlier failed round in a thread is incoherent.
          return !!(seg && seg.failed && !seg.canceled && segIndex === this.segments.length - 1
                    && (this.threadTaskText || '').trim());
        }},
        retryFailedCouncil() {{
          // Re-dispatch the FAILED council as a FRESH launch of the same task. NOT a
          // chain-iterate: a failed council has no responses to iterate FROM, so it
          // fires `launch-council` (capture_host's ACTION_ALLOWLIST 'launch-council'
          // — only `task` is required; goal/primary_provider fall back to config), the
          // same self-contained action the launchpad's Launch button uses. Mirrors the
          // _startChainAction honesty contract: ack instantly, surface a real error if
          // the dispatcher is absent / the dispatch fails (the Iter-112/115 NO-FEEDBACK
          // class — no silent no-op when the extension vanishes), and roll the segment
          // back to its FAILED state so the retry CTA stays available.
          const task = (this.threadTaskText || '').trim();
          if (!task) return;
          const seg = this.segments[this.segments.length - 1];
          if (!seg || !seg.failed || this.chainBusy) return;
          const segKey = seg.key;
          const newToken = this._newStatusToken();
          this.chainError = '';
          this.chainErrorHeading = 'Could not start next round';
          this.chainBusy = true;
          this.stopRequested = false;
          this.clearPolling();
          // Flip the dead segment to a fresh running attempt in place (same task, new
          // token). Re-seed the members as pending so the spinner + "Council running"
          // replace the failure banner immediately — the optimistic ACK.
          const memberOrder = Object.keys(seg.runState?.members || {{}});
          this._patchSegment(segKey, {{
            statusToken: newToken,
            busy: true, failed: false, canceled: false, completed: false,
            errorText: '', currentStatusIndex: 0,
            runState: normalizeStatus({{
              status: 'running', statusToken: newToken, taskText: task,
              memberOrder,
              members: Object.fromEntries(memberOrder.map((p) => [p, {{ status: 'pending' }}])),
              synthesis: {{ status: 'pending' }},
            }}, null),
          }});
          const rollback = (r) => {{
            // Restore the FAILED state so the user isn't stranded on a spinner that
            // will never resolve (no status file will ever be written) and the retry
            // CTA reappears. chainError carries the honest dispatcher-side reason.
            this.chainBusy = false;
            this.clearPolling();
            this._patchSegment(segKey, {{
              statusToken: seg.statusToken, busy: false, failed: true, canceled: false,
              errorText: seg.errorText || 'Council failed.',
            }});
            this.chainError = dispatchErrorMessage(r);
          }};
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (!dispatcher) {{
            this.chainBusy = false;
            this.clearPolling();
            this._patchSegment(segKey, {{
              statusToken: seg.statusToken, busy: false, failed: true, canceled: false,
              errorText: seg.errorText || 'Council failed.',
            }});
            this.chainError = 'Trinity dispatcher not loaded on this page. Reload the launchpad and try again.';
            return;
          }}
          dispatcher.dispatch({{
            extensionAction: {{ kind: 'launch-council', task, status_token: newToken }},
            onResult: (r) => {{
              if (!r || !r.ok) {{ rollback(r); return; }}
            }},
          }});
          if (this.chainError) return;
          this.startPolling();
          setTimeout(() => {{ this.chainBusy = false; }}, 800);
        }},
        stopCouncil() {{
          const last = this.segments[this.segments.length - 1];
          if (!last?.statusToken) return;
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (!dispatcher) {{
            this.chainError = 'Trinity dispatcher not loaded on this page. Reload the launchpad and try again.';
            return;
          }}
          // Immediate ACK the instant the click lands: the button flips to
          // "Stopping…" (disabled, no double-fire) and the status message pins
          // "Stopping the council…". The actual cancel lands later when the
          // poller reads the host's 'canceled' status (which clears the segment's
          // busy flag, so the whole stop card disappears). Without this the click
          // looked like a no-op for the whole gap (the launchpad's 2026-06-17
          // NO-FEEDBACK lineage — the live council page had its OWN Stop button
          // that was missed).
          this.stopRequested = true;
          dispatcher.dispatch({{
            extensionAction: {{ kind: 'stop-council', status_token: last.statusToken }},
            onResult: (r) => {{
              // Silent-failure fix shipped 2026-05-26 — sibling of the
              // Refine/Continue/Auto-chain dispatch-failure surface.
              // Before, Stop council swallowed all errors (empty arrow
              // function), so a click with no extension installed left
              // the council polling indefinitely with no visible
              // feedback. Now the banner explains the failure; the
              // polling loop's stuck-token timeout (commit 6d6052b)
              // is the secondary safety net.
              if (!r || !r.ok) {{
                // The stop dispatch FAILED — re-enable the button so the user can
                // retry instead of being stranded on a disabled "Stopping…" that
                // will never finalize (the cancel never reached the host).
                this.stopRequested = false;
                this.chainErrorHeading = 'Could not stop the council';
                this.chainError = dispatchErrorMessage(r);
              }}
            }},
          }});
        }},
        _newStatusToken() {{
          return 'chain_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
        }},
        _startChainAction(actionName, additionalArgs, refinementText, heading, detail) {{
          const last = this.segments[this.segments.length - 1];
          if (!last?.councilId) return;
          const newToken = this._newStatusToken();
          const args = additionalArgs || {{}};
          this.chainBusy = true;
          this.chainStatusHeading = heading;
          this.chainStatusDetail = detail;
          // A brand-new round starts fresh — clear any stale stop-ACK so the new
          // segment's Stop button reads "Stop council", not a leftover "Stopping…".
          this.stopRequested = false;

          // Same migration as the council-review page above: all
          // chain actions (refine / continue / auto-chain) route to
          // `trinity-local council-iterate` via the Chrome extension
          // dispatcher. Shortcuts:// path retired pre-launch.
          const dispatcher = window.__TRINITY_DISPATCH__;
          const extensionAction = {{
            kind: 'council-iterate',
            council: last.councilId,
            // Underscore key (NOT the hyphen CLI-flag spelling) — capture_host's
            // ACTION_ALLOWLIST reads payload.get('status_token'). A hyphen here
            // silently dropped the token so the chain round wrote status under
            // the bundle_id, not the chain token this page polls → the page
            // 404'd forever ("council never started"). Founder 2026-06-12.
            status_token: newToken,
          }};
          if (args.prompt) extensionAction.prompt = args.prompt;
          if (args.max_rounds) extensionAction.rounds = String(args.max_rounds);

          // Sequencing matters: when the Chrome-extension state is already
          // 'absent', dispatcher.dispatch() calls onResult SYNCHRONOUSLY
          // inside the same tick. If we set _pendingChainSegmentToken or
          // chainError AFTER that call, the failure handler would (a)
          // miss the token (never set yet) and (b) get its chainError
          // wiped by the post-dispatch reset. So: do all state setup
          // BEFORE dispatching, and the onResult handler just consumes
          // the pre-populated state.
          this.clearPolling();
          this.refinePrompt = '';
          this.chainError = '';  // clear any prior dispatch error on retry
          this.chainErrorHeading = 'Could not start next round';  // reset from any proactive stale-warning heading
          // Append a NEW segment for the next round; prior rounds stay
          // visible above so the page reads as a scrollable thread.
          const memberOrder = Object.keys(last.runState?.members || {{}});
          const newSeg = makeSegment({{
            statusToken: newToken,
            taskText: this.threadTaskText,
            members: memberOrder,
            refinementText: refinementText || '',
          }});
          // Track this segment's token so onResult's failure path can
          // find + remove it for rollback. MUST be set before dispatch
          // so a synchronous failure handler sees the right token.
          this._pendingChainSegmentToken = newToken;
          // Push the segment BEFORE dispatch so a synchronous failure
          // (dispatch state already 'absent', no extension) can find +
          // splice it. If the push happened after dispatch, the
          // rollback's findIndex would return -1 in the sync-fail case.
          newSeg.roundNumber = (last.roundNumber || 1) + 1;
          this.segments.push(newSeg);

          if (dispatcher) {{
            dispatcher.dispatch({{
              extensionAction,
              onResult: (r) => {{
                if (!r || !r.ok) {{
                  this.chainBusy = false;
                  // chainStatusDetail only shows while chainBusy=true, so it
                  // gets hidden the moment we flip chainBusy to false above.
                  // Use chainError instead — rendered in a persistent ribbon
                  // outside the chainBusy guard. Silent-failure fix shipped
                  // 2026-05-26 alongside the launchpad stuck-launch rollback;
                  // before, the user clicked Refine and saw absolutely
                  // nothing for ~800ms then the action panel returned with
                  // no error indication.
                  this.chainError = dispatchErrorMessage(r);
                  // Roll back the optimistic new segment so the polling
                  // loop doesn't hammer a status file that will never
                  // exist + the thread visual stays accurate.
                  if (this._pendingChainSegmentToken) {{
                    const idx = this.segments.findIndex((s) => s.statusToken === this._pendingChainSegmentToken);
                    if (idx !== -1) this.segments.splice(idx, 1);
                    this._pendingChainSegmentToken = '';
                  }}
                  this.clearPolling();
                  // Restore the prompt the user typed so they can fix +
                  // retry without retyping (mirrors launchpad pendingPrompt).
                  if (refinementText) this.refinePrompt = refinementText;
                  return;
                }}
              }},
            }});
          }} else {{
            // No dispatcher loaded — also roll back the segment we just
            // pushed; the polling loop has nothing to talk to.
            const idx = this.segments.findIndex((s) => s.statusToken === newToken);
            if (idx !== -1) this.segments.splice(idx, 1);
            this._pendingChainSegmentToken = '';
            this.chainBusy = false;
            this.chainError = 'Trinity dispatcher not loaded on this page. Reload the launchpad and try again.';
            if (refinementText) this.refinePrompt = refinementText;
            return;
          }}
          // If the synchronous dispatch path already failed (chainError
          // set), don't continue with the polling + scroll affordance —
          // the segment has already been spliced out above.
          if (this.chainError) return;
          // Auto-scroll the new segment into view after render.
          requestAnimationFrame(() => {{
            const el = document.querySelector('[data-seg-key="' + newSeg.key + '"]');
            el?.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
          }});
          this.startPolling();
          setTimeout(() => {{ this.chainBusy = false; }}, 800);
        }},
        startContinue() {{
          if (this.chainBusy) return;
          this._startChainAction('council_continue', null, '', 'Starting next round…',
            "Each model is reading the others' answers and refining.");
        }},
        startAutoChain() {{
          if (this.chainBusy) return;
          this._startChainAction('council_auto_chain', {{ max_rounds: 3 }}, '',
            'Auto-chaining…',
            'Models will iterate up to 3 rounds, stopping when the chairman declares convergence.');
        }},
        startRefine() {{
          if (this.chainBusy) return;
          const prompt = (this.refinePrompt || '').trim();
          if (!prompt) return;
          this._startChainAction('council_refine', {{ prompt }}, prompt, 'Refining…',
            'Each model is incorporating your new directive into a refined answer.');
        }},
        clearPolling() {{
          if (this.statusPollHandle) {{
            clearInterval(this.statusPollHandle);
            this.statusPollHandle = null;
          }}
          if (this.statusRotateHandle) {{
            clearInterval(this.statusRotateHandle);
            this.statusRotateHandle = null;
          }}
        }},
        _patchSegment(key, patch) {{
          const idx = this.segments.findIndex((s) => s.key === key);
          if (idx === -1) return null;
          const next = Object.assign({{}}, this.segments[idx], patch);
          this.segments.splice(idx, 1, next);
          return next;
        }},
        startPolling() {{
          const seg0 = this.segments[this.segments.length - 1];
          if (!seg0?.statusToken) {{
            if (seg0) {{
              this._patchSegment(seg0.key, {{ busy: false, failed: true, errorText: 'Missing council status token.' }});
            }}
            return;
          }}
          const segKey = seg0.key;
          this.statusRotateHandle = window.setInterval(() => {{
            const cur = this.segments.find((s) => s.key === segKey);
            if (!cur) return;
            this._patchSegment(segKey, {{ currentStatusIndex: (cur.currentStatusIndex || 0) + 1 }});
          }}, 2500);
          // Track consecutive 404s on the status JSONP fetch so we can
          // surface "council never started" when the user lands on a
          // status_token URL that has no backing file. This is exactly
          // the user-reported `launch_mpm0bght_gx1y9v` symptom — clicked
          // Launch, the JS-side optimistic UI built the URL, but the
          // dispatcher couldn't reach an extension so no status file
          // was ever written. The page sat polling indefinitely showing
          // "Council running / Generating witty dialog…" forever with
          // zero feedback. After MAX_MISSING_POLLS consecutive failures
          // (~12s @ 1.5s/poll) we declare the council didn't start.
          let missingPollCount = 0;
          const MAX_MISSING_POLLS = 8;
          const check = () => {{
            const cur = this.segments.find((s) => s.key === segKey);
            if (!cur?.statusToken) {{ this.clearPolling(); return; }}
            loadStatusScript(cur.statusToken, (status) => {{
              if (!status) {{
                missingPollCount += 1;
                if (missingPollCount >= MAX_MISSING_POLLS) {{
                  this.clearPolling();
                  this._patchSegment(segKey, {{
                    busy: false, failed: true, canceled: false,
                    errorText: "This council never started. The launch URL " +
                               "was constructed but no status file was ever " +
                               "written, so dispatch likely failed silently. " +
                               "Common cause: the Chrome extension isn't " +
                               "installed in this browser. Run " +
                               "`trinity-local install-extension --extension-id <ID>` " +
                               "and click Launch again from the launchpad.",
                  }});
                }}
                return;
              }}
              // Reset the missing-poll counter on any successful fetch — the
              // status file showed up, so the dispatch eventually worked.
              missingPollCount = 0;
              if (!this.threadTaskText) this.threadTaskText = status.task_text || this.threadTaskText;
              const ref = this.segments.find((s) => s.key === segKey);
              if (!ref) return;
              if (status.status === 'running') {{
                this._patchSegment(segKey, {{
                  busy: true, failed: false, canceled: false, errorText: '',
                  runState: normalizeStatus(status, ref.runState),
                }});
                return;
              }}
              if (status.status === 'completed') {{
                this.clearPolling();
                const next = this._patchSegment(segKey, {{
                  busy: false, failed: false, canceled: false, completed: true,
                  runState: normalizeStatus(status, ref.runState),
                  councilId: status.council_id || ref.councilId,
                  // Floor on the segment's existing (position-derived) round number —
                  // the status sidecar's metadata.round_number is the SAME degenerate
                  // "1 on every round" field the build path defends against, so
                  // trusting it FIRST clobbered a live-streaming Round 3 back to
                  // "Round 1" the instant it completed (and the follow-on
                  // _loadOutcomeIntoSegment then sees the clobbered 1 as its floor).
                  // Take it only when it runs AHEAD of the position floor.
                  roundNumber: Math.max((status.metadata && status.metadata.round_number) || 0, ref.roundNumber || 1),
                  converged: !!(status.metadata && status.metadata.converged),
                }});
                if (next?.councilId) {{
                  this._loadOutcomeIntoSegment(next, next.councilId);
                }}
                return;
              }}
              if (status.status === 'failed') {{
                this.clearPolling();
                // Fold the terminal payload's member states INTO runState — the
                // running/completed branches normalize, but failed/canceled used
                // to skip it, so the segment's runState kept the STALE pre-failure
                // member map (every member still 'pending'). The "Full Responses"
                // section then rendered three "QUEUED · Queued." rows directly
                // BELOW the "Council failed — all members failed to respond"
                // banner — a flat contradiction the user reads as "it's still
                // working" (founder symptom: stale Queued rows on a dead council).
                this._patchSegment(segKey, {{
                  busy: false, failed: true, canceled: false,
                  errorText: status.error || 'Council failed.',
                  runState: normalizeStatus(status, ref.runState),
                }});
                return;
              }}
              if (status.status === 'canceled') {{
                this.clearPolling();
                this._patchSegment(segKey, {{
                  busy: false, failed: false, canceled: true,
                  errorText: status.error || 'Council stopped.',
                  runState: normalizeStatus(status, ref.runState),
                }});
              }}
            }});
          }};
          check();
          this.statusPollHandle = window.setInterval(check, 1500);
        }},
      }};
    }}

    createApp({{ LiveCouncilApp, pageData }}).mount();
  </script>
{footer}"""


def write_live_council_page(*, force: bool = False) -> Path:
    """Write `~/.trinity/review_pages/live_council.html`.

    The unified live-council HTML is essentially a static asset — it doesn't
    depend on per-call data, just on the package's render code. If the file
    already exists, `force=False` (default) skips the write so a long-lived
    MCP server running stale in-memory code can't overwrite it with old
    template HTML.

    The CLI's `portal-html` (refresh_launchpad) passes `force=True` to refresh
    on demand. That's the only path that should ever rewrite this file once
    it exists. See: every "blank council page" / "QUEUED stuck" report has
    been this overwrite class.
    """
    path = review_pages_dir() / "live_council.html"
    if path.exists() and not force:
        return path
    path.write_text(render_live_council_page(), encoding="utf-8")
    return path
