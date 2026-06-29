"""Shared design system CSS from DESIGN.md.

Colors, typography, spacing, and component styles used across all
Trinity HTML pages (launchpad, council, memory viewer). Was
"portal, council, digest" pre-launch — `portal_*` renamed to
`launchpad_*` per task #93, `digest_pages/` (weekly digest feature)
retired pre-launch. The memory viewer (`memory_viewer.py`) ships
with the launchpad and reuses the same SHARED_CSS.
"""
from __future__ import annotations

# "Calm / Muted Teal" palette (v1.7.309) — cool mist neutrals + ONE muted-teal
# accent + Hanken Grotesk. Soothing, document-first, distinct from both the warm-
# cream/serif "Claude default" AND the dev-terminal look. No green, no cream, no
# serif, no orange. Mirrors design-system/colors_and_type.css. Keys are stable
# (SHARED_CSS + the DESIGN.md<->COLORS pin key off the VALUES).
COLORS = {
    "bg_base": "#eaecef",
    "bg_wash": "#eff1f4",
    "surface": "#fafbfc",
    "surface_muted": "#f1f3f6",
    "border": "#dde1e6",
    "text_primary": "#2f363c",
    "text_secondary": "#5b646b",
    "text_muted": "#5e666f",           # darkened #8b939b→#616a73→#5e666f for AA on the rgba-TINTED accent cards: .meta on rgba(43,80,112,0.04)-over-bg-base was 4.38 (<4.5); #5e666f→4.64 (2026-06-19)
    "action_primary": "#3f777c",       # deepened #4d8b90→AA: white text 4.96:1 (was 3.8, faked via bold)
    "action_primary_hover": "#34666b",
    "action_text": "#fbfdfc",
    "accent_warm": "#4f9095",  # muted teal — the one accent (eyebrows, links, marks, emphasis)
    "success": "#4f9095",      # shares the calm teal family — the FILL token (dots/bars/border-left); NOT readable as text on the green success tint (2.9:1)
    "success_text": "#2d6a4f", # the TEXT green — readable on the green success tint (.done badge / .badge.success): teal #4f9095 on rgba(45,106,79,…) was 2.9:1 (unreadable). #2d6a4f → 5.1:1 AA. Mirrors --warning/--warning-text. UX sweep 2026-06-21.
    "warning": "#bd9658",      # the FILL amber — borders/backgrounds/icons (NOT readable text: 2.3–2.6:1 on light)
    "warning_text": "#79591b", # the TEXT amber — deepened so failure-message body copy clears AA 4.5:1 on light (mirrors --accent/--accent-deep). #bd9658 as 12–13px text was 2.49:1 (unreadable-grade) — UX sweep 2026-06-20.
    "danger": "#bd6a5a",       # the FILL terracotta — borders/backgrounds/icons (NOT readable text: 2.8–3.3:1 on the danger tint)
    "danger_text": "#99392c",  # the TEXT terracotta — deepened so small status labels clear AA 4.5:1 on the danger tint (mirrors --success-text/--warning-text). #bd6a5a as 11px label was 2.98:1 (.viewer-trust-label "degraded") / 3.26:1 (.badge.danger) — unreadable-grade — UX sweep 2026-06-22.
    "info": "#7fa0ad",
}

# Brand @font-face block — Hanken Grotesk (everything) + JetBrains Mono
# (commands/paths/JSON), vendored under portal_pages/vendor/ (OFL, published by
# vendor.publish_vendor_files). The `../portal_pages/vendor/` path resolves
# identically from BOTH portal_pages/ pages (launchpad, memory viewer) and
# review_pages/ sub-pages (live council) — matching the PETITE_VUE_IIFE convention.
# file://-safe; no CDN. Extracted as a constant (v1.7.320) so the memory viewer —
# which builds its OWN <head>/<style>, not render_html_head/SHARED_CSS — stays on
# the brand fonts instead of falling back to system fonts.
FONT_FACE_CSS = """
@font-face { font-family:"Hanken Grotesk"; font-style:normal; font-weight:400; font-display:swap; src:url("../portal_pages/vendor/HankenGrotesk-400.woff2") format("woff2"); }
@font-face { font-family:"Hanken Grotesk"; font-style:normal; font-weight:500; font-display:swap; src:url("../portal_pages/vendor/HankenGrotesk-500.woff2") format("woff2"); }
@font-face { font-family:"Hanken Grotesk"; font-style:normal; font-weight:600; font-display:swap; src:url("../portal_pages/vendor/HankenGrotesk-600.woff2") format("woff2"); }
@font-face { font-family:"Hanken Grotesk"; font-style:normal; font-weight:700; font-display:swap; src:url("../portal_pages/vendor/HankenGrotesk-700.woff2") format("woff2"); }
@font-face { font-family:"JetBrains Mono"; font-style:normal; font-weight:400; font-display:swap; src:url("../portal_pages/vendor/JetBrainsMono-400.woff2") format("woff2"); }
@font-face { font-family:"JetBrains Mono"; font-style:normal; font-weight:500; font-display:swap; src:url("../portal_pages/vendor/JetBrainsMono-500.woff2") format("woff2"); }
"""

SHARED_CSS = FONT_FACE_CSS + """
/* Hide the un-mounted petite-vue app so the raw template (literal {{ }} +
   every v-if section expanded) never flashes before mount. petite-vue removes
   v-cloak on mount. This matters most in the side panel, where navigating
   back to the launchpad RELOADS the sandbox iframe VISIBLY and the app waits on
   an async host fetch before mounting (founder-caught: a multi-second raw-
   template flash on back-nav). The shell also covers the swap with its spinner. */
[v-cloak] { display: none !important; }
/* Visually-hidden but screen-reader-available — for persistent aria-live status
   mirrors (WCAG 4.1.3). NOT display:none (that removes it from the a11y tree);
   the clip+1px technique keeps it spoken while taking no visual space. */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
:root {
  --accent: """ + COLORS["accent_warm"] + """;
  --accent-deep: """ + COLORS["action_primary_hover"] + """;
  --accent-soft: #e1edec;
  --display: "Hanken Grotesk", "SF Pro Text", system-ui, -apple-system, sans-serif;
  --sans: "Hanken Grotesk", "SF Pro Text", system-ui, -apple-system, sans-serif;
  --mono: "JetBrains Mono", "SF Mono", ui-monospace, monospace;
  --bg-base: """ + COLORS["bg_base"] + """;
  --bg-wash: """ + COLORS["bg_wash"] + """;
  --surface: """ + COLORS["surface"] + """;
  --surface-muted: """ + COLORS["surface_muted"] + """;
  --border: """ + COLORS["border"] + """;
  --text-primary: """ + COLORS["text_primary"] + """;
  --text-secondary: """ + COLORS["text_secondary"] + """;
  --text-muted: """ + COLORS["text_muted"] + """;
  --action: """ + COLORS["action_primary"] + """;
  --action-hover: """ + COLORS["action_primary_hover"] + """;
  --action-text: """ + COLORS["action_text"] + """;
  --success: """ + COLORS["success"] + """;
  --success-text: """ + COLORS["success_text"] + """;
  --warning: """ + COLORS["warning"] + """;
  --warning-text: """ + COLORS["warning_text"] + """;
  --danger: """ + COLORS["danger"] + """;
  --danger-text: """ + COLORS["danger_text"] + """;
  --info: """ + COLORS["info"] + """;
  /* Two-tier responsive width model (chat-UI pattern): a page container and a
     narrower focal column for the composer / reading width. Plus the councils
     side-nav width. @media can't read vars, so the breakpoints hard-code 1024. */
  --page-max: 1080px;
  --composer-max: 720px;
  --rail-w: 288px;
}

* {
  box-sizing: border-box;
}

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: var(--sans);
  line-height: 1.55;
  font-size: 16px;
}

/* Placeholder text carries INSTRUCTIONAL copy on Trinity's primary inputs — the
   main composer ("Ask a council question…"), the council search box, the refine
   directive textarea, the Takeout import path — so it must be readable, not
   decorative. The browser DEFAULT placeholder color (#757575) composites to only
   4.45:1 on --surface and 3.89:1 on a tinted input, BELOW the WCAG AA 4.5:1 body
   floor the rest of the palette was pushed to clear (2026-06-16 AA push). No
   ::placeholder color was ever set, so EVERY input across launchpad / council /
   memory-viewer inherited the failing default. Pin it to --text-muted (#5e666f →
   5.62:1 on --surface, 4.92:1 on the #eaecef import-input tint) and force
   opacity:1 so Firefox's default placeholder opacity can't re-lighten it below
   the floor. */
::placeholder {
  color: var(--text-muted);
  opacity: 1;
}

/* Minimalist-editorial baseline (taste-skill #318) — anchor-nav glides instead
   of jumping; reduced-motion-safe. */
html { scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  /* WCAG 2.3.3 — honor the OS "reduce motion" setting (a vestibular-disorder
     accommodation) for the IN-CONTENT animations, not just the shell. The
     launchpad/council `.spinner` spins `infinite` for the ENTIRE multi-minute
     council run (the longest-lived motion on the page), and the chain loader,
     winner-glow, and verdict-rise all animate too — yet the only prior
     reduced-motion guard was scroll-behavior + one rail transition, so the
     side-panel shell spinner stopped while the in-page council spinner kept
     spinning. Stop every keyframe animation (the spinner freezes to a static
     busy ring — still a legible "working" indicator) and collapse the decorative
     entrance/glow transitions to instant. Targets the SHARED-CSS surfaces
     (launchpad, council review, review pages). */
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}
/* Keyboard focus ring — visible on tab, never on mouse. Was missing entirely:
   an a11y gap on every interactive element across launchpad / council / viewer. */
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* Headings break into balanced lines instead of stranding an orphan word.
   overflow-wrap:break-word is the load-bearing half: text-wrap:balance only
   distributes the BREAK POINTS it already has, so a heading carrying a long
   UNBREAKABLE token (a council question with a URL / file path / hash / dev
   identifier, a memory-file name) would otherwise blow the page out
   horizontally — the live-council question <h1> stretched a 320px phone to
   1524px (found 2026-06-18 driving the 3-member completed council at 320px;
   the sibling of the 2026-06-07 routing-label-grid token fix). Breaking the
   token wraps it inside the heading and never touches normal-prose layout. */
h1, h2, h3 { text-wrap: balance; overflow-wrap: break-word; }
/* Tabular figures so digit columns line up in the data surfaces (routing table,
   eval leaderboard, stat readouts) — scoped to tables + mono so prose is untouched. */
table, code, pre, kbd { font-variant-numeric: tabular-nums; }

main {
  max-width: var(--page-max, 1080px);
  margin: 0 auto;
  padding: 32px;
}

@media (max-width: 768px) {
  main {
    padding: 18px;
  }
}

/* Typography */
h1 {
  font-family: var(--display);
  font-size: clamp(38px, 8vw, 56px);
  font-weight: 700;
  line-height: 0.95;
  margin: 0 0 24px 0;
  color: var(--text-primary);
}

h2 {
  font-family: var(--display);
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
  margin: 0 0 16px 0;
  color: var(--text-primary);
}

@media (max-width: 768px) {
  h2 {
    font-size: 20px;
  }
}

h3 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 12px 0;
  color: var(--text-primary);
}

p {
  margin: 0 0 12px 0;
  color: var(--text-secondary);
}

.lede {
  font-size: 18px;
}

.eyebrow {
  font-family: var(--sans);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  /* --accent-deep (#34666b), not --accent (#4f9095): the small uppercase eyebrow
     is TEXT and needs AA 4.5:1, which the brand-mark accent misses on light bg
     (3.5:1). --accent stays the mark/decorative teal; eyebrows get the deep one. */
  color: var(--accent-deep);
  margin: 0 0 8px 0;
}

code, pre {
  font-family: var(--mono);
  font-size: 14px;
  background: var(--surface-muted);
  padding: 2px 6px;
  border-radius: 4px;
}

pre {
  padding: 12px;
  overflow-x: auto;
  margin: 12px 0;
}

/* Layout */
.grid {
  display: grid;
  gap: 24px;
}

.grid-2 {
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
}

.grid-cards {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
}

.grid-members {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.hero {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
  align-items: center;
}

@media (max-width: 768px) {
  .hero {
    grid-template-columns: 1fr;
  }
}

/* Shared topbar — sub-pages all use this shape.
   Launchpad is the root and uses the hero pattern instead (no topbar). */
.trinity-topbar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 28px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}
.trinity-topbar .topbar-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  text-decoration: none;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-base);
  transition: background 0.12s, border-color 0.12s;
}
.trinity-topbar .topbar-back:hover {
  background: var(--surface-muted);
  border-color: var(--text-muted);
}
.trinity-topbar .topbar-title {
  font-family: var(--sans);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--text-primary);
  margin: 0;
}
.trinity-topbar .topbar-spacer { flex: 1; }
.trinity-topbar .topbar-action {
  font-size: 13px;
  color: var(--text-secondary);
  text-decoration: none;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
}
.trinity-topbar .topbar-action:hover {
  background: var(--surface-muted);
  color: var(--text-primary);
}
@media (max-width: 768px) {
  .trinity-topbar { padding: 12px 16px; gap: 10px; }
  .trinity-topbar .topbar-title { font-size: 14px; }
}

/* Cards and surfaces */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 24px;
  box-shadow: 0 10px 30px rgba(60, 72, 86, 0.08);
}
/* Card chrome steps down with the viewport so every surface (launchpad, /stats,
   council, viewer) reads tight on a phone without a per-surface one-off. */
@media (max-width: 767px) {
  .card { border-radius: 16px; padding: 16px; }
}
@media (max-width: 560px) {
  .card { border-radius: 14px; padding: 14px; }
}

.card-muted {
  background: var(--surface-muted);
}

/* Shared off-canvas-nav primitives (the councils side-nav drawer; reusable by
   any surface that hosts the drawer). Chat-UI pattern: a fixed hamburger toggles
   a translateX drawer over a dimmed scrim. The .rail-toggle / .rail-scrim are
   inert on pages that don't render their markup (council/memory pages), so this
   is safe to keep in SHARED_CSS. */
.rail-toggle {
  position: fixed;
  top: 12px;
  left: 12px;
  /* WCAG 2.5.5 / 2.5.8 minimum touch target: this fixed hamburger is the ONLY
     way to open the council-history drawer on mobile / the side panel, so it
     must clear 44×44 for a thumb (was 40×40 → 4px short). */
  width: 44px;
  height: 44px;
  z-index: 60;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text-primary);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(60, 72, 86, 0.10);
}
.rail-toggle:hover { border-color: var(--text-secondary); }
.rail-scrim {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 55;
  border: none;
  margin: 0;
  padding: 0;
  cursor: pointer;
}

.member pre {
  font-size: 13px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

/* Buttons */
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 12px padding + 14px/1.5 text + 1px borders ≈ 43px — 1px under the 44px
     touch-target minimum (WCAG 2.5.5 / Apple HIG). Floor it so Launch Council
     and every action button clear 44px on touch widths (founder-flagged). */
  min-height: 44px;
  padding: 12px 20px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-primary);
  text-decoration: none;
  cursor: pointer;
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  transition: all 0.2s ease;
}

.button:hover {
  border-color: var(--text-secondary);
}

.button.primary {
  background: var(--action);
  color: var(--action-text);
  border-color: var(--action);
  /* Bold for emphasis on the primary CTA. (The action teal was deepened to
     #3f777c on 2026-06-16 so white text clears AA-NORMAL 4.5:1 (4.96:1) on its
     OWN — the bold is no longer load-bearing for contrast, just weight.) */
  font-weight: 700;
}

.button.primary:hover {
  background: var(--action-hover);
  border-color: var(--action-hover);
}

.button.secondary {
  background: var(--surface);
  border-color: var(--border);
}

.button.secondary:hover {
  background: var(--surface-muted);
}

.button.ghost {
  background: transparent;
  border-color: transparent;
  color: var(--action);
}

.button.ghost:hover {
  background: var(--bg-wash);
}

.button.danger {
  background: var(--danger);
  color: white;
  border-color: var(--danger);
}

/* Metadata and secondary info */
.meta {
  font-size: 15px;
  color: var(--text-muted);
  font-family: var(--sans);
}

.label {
  font-family: var(--sans);
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-muted);
}

/* Status badges */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  background: var(--bg-wash);
  color: var(--text-secondary);
}

.badge.success {
  background: rgba(45, 106, 79, 0.12);
  /* --success-text (deep green), not --success (#4f9095 teal): the 12px badge label is
     small text and must clear AA 4.5:1 — teal on this green tint is 2.9:1. */
  color: var(--success-text);
}

.badge.warning {
  background: rgba(79, 144, 149, 0.12);
  /* --warning-text (deep amber), not --warning (#bd9658): the 12px badge label is
     small text and must clear AA 4.5:1 — #bd9658 on this tint is 2.6:1. */
  color: var(--warning-text);
}

.badge.danger {
  background: rgba(163, 60, 47, 0.12);
  /* --danger-text (deep terracotta), not --danger (#bd6a5a): the small badge label
     is small text and must clear AA 4.5:1 — #bd6a5a on this danger tint is 3.26:1.
     Mirrors .badge.success/.badge.warning, which were already deepened. */
  color: var(--danger-text);
}

.badge.info {
  background: rgba(43, 80, 112, 0.12);
  color: var(--info);
}

/* Action groups */
.actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.pillbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0 0;
}

.pill {
  display: inline-block;
  padding: 4px 12px;
  background: var(--surface-muted);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-muted);
}

/* Spacing utilities */
.gap-xs { gap: 4px; }
.gap-sm { gap: 8px; }
.gap-md { gap: 12px; }
.gap-lg { gap: 24px; }
.gap-xl { gap: 32px; }

.mb-xs { margin-bottom: 4px; }
.mb-sm { margin-bottom: 8px; }
.mb-md { margin-bottom: 12px; }
.mb-lg { margin-bottom: 24px; }
.mb-xl { margin-bottom: 32px; }

/* Responsive video/iframe */
.video-container {
  position: relative;
  width: 100%;
  padding-bottom: 56.25%;
  margin: 24px 0;
}

.video-container iframe,
.video-container video {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  border-radius: 14px;
  border: 1px solid var(--border);
}

.video-shell {
  position: relative;
  width: 100%;
  padding-bottom: 56.25%;
}

.video-shell video,
.video-shell iframe {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  border-radius: 14px;
  border: 1px solid var(--border);
}

/* Details/summary */
details {
  margin: 12px 0;
  padding: 12px;
  background: var(--surface-muted);
  border: 1px solid var(--border);
  border-radius: 14px;
}

summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--action);
  user-select: none;
}

details[open] {
  background: var(--surface);
}

/* Utilities */
.text-muted {
  color: var(--text-muted);
}

.text-secondary {
  color: var(--text-secondary);
}

.align-center {
  text-align: center;
}

.align-right {
  text-align: right;
}

.hidden {
  display: none !important;
}

.summary-stat {
  text-align: center;
  padding: 16px;
}

.summary-stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--action);
}

.summary-stat-label {
  font-size: 12px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: 4px;
}

.alert-box {
  padding: 12px;
  margin-bottom: 8px;
  border-radius: 0 4px 4px 0;
  color: var(--text-primary);
  /* Alert boxes carry LLM-emitted prose (post-hoc review issues/suggestions) —
     the exact place a URL / file path / regex / dev identifier shows up. Without
     a break rule a single unbreakable token blows the page out horizontally (the
     review page's `.alert-box.success` stretched a 393px phone to 1054px,
     found 2026-06-22 driving the post-hoc review page; the same class as the
     h1/h2/h3 + code/pre break rules above). */
  overflow-wrap: break-word;
  word-break: break-word;
}

.alert-box.danger {
  background: rgba(163,60,47,0.12);
  border-left: 3px solid var(--danger);
}

.alert-box.success {
  background: rgba(45,106,79,0.12);
  border-left: 3px solid var(--success);
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
}

th, td {
  text-align: left;
  border-bottom: 1px solid var(--border);
  padding: 8px;
  font-size: 14px;
}

th {
  background: var(--surface-muted);
  font-weight: 600;
}
"""

# Inline favicon shared by EVERY served page — the launchpad / council / review
# pages (via render_html_head below) AND the memory viewer (memory_viewer.py builds
# its own <head>, so it imports this constant). Without it every page 404s on
# /favicon.ico (noise in the HTTP-log health signal the smoke gate reads) and the
# tab shows no brand mark. Data-URI = no extra file, no request, can't itself 404.
# The brand mark is the braille ⠕ (U+2815, dots-1-3-5): three pips at
# top-left / middle-right / bottom-left — the keepwhatworks.com favicon and the
# extension toolbar icon (render_extension_icons.py draws the SAME arrangement).
# It encodes the whole product — three providers, one roll, a judgment call — as
# a pure-geometry SVG (the ARRANGEMENT, drawn, not the glyph — so no ⠕-style
# tofu), and it's the recurring motif across favicon / launchpad eyebrow /
# share-card footer. Verified in a real browser 2026-06-02/03 (single-pip
# predecessor); die updated 2026-06-10; recolored-then-realigned to the ⠕
# arrangement 2026-06-12 (founder: "use this everywhere"). Pip x/y mirror the
# icon's fractions (0.36,0.28 / 0.64,0.50 / 0.36,0.72) on the 32-grid.
FAVICON_LINK = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='7' fill='%234f9095'/>"
    "<circle cx='11.5' cy='9' r='3.1' fill='%23eaecef'/>"
    "<circle cx='20.5' cy='16' r='3.1' fill='%23eaecef'/>"
    "<circle cx='11.5' cy='23' r='3.1' fill='%23eaecef'/></svg>\" />"
)

# Inline die mark for in-page use (the launchpad hero eyebrow, etc.) — the same
# braille ⠕ (dots-1-3-5) arrangement as the favicon, sized to sit beside text.
# Teal body + white pips so it reads on any background (a filled tile, where the
# favicon's light tile reads against the browser chrome). Draws the ARRANGEMENT
# as geometry, replacing the ⠕ (U+2815) glyph that tofus on many fonts.
DIE_MARK_INLINE_SVG = (
    "<svg class=\"die-mark\" viewBox=\"0 0 32 32\" width=\"14\" height=\"14\" "
    "role=\"img\" aria-label=\"Trinity\" "
    "style=\"vertical-align:-2px;margin-right:5px\">"
    "<rect width=\"32\" height=\"32\" rx=\"7\" fill=\"#4f9095\"/>"
    "<circle cx=\"11.5\" cy=\"9\" r=\"3.1\" fill=\"#fff\"/>"
    "<circle cx=\"20.5\" cy=\"16\" r=\"3.1\" fill=\"#fff\"/>"
    "<circle cx=\"11.5\" cy=\"23\" r=\"3.1\" fill=\"#fff\"/></svg>"
)


def render_html_head(title: str = "Trinity", *, extra_head: str = "") -> str:
    """Render <head> with shared CSS and optional extra markup."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  {FAVICON_LINK}
  <style>
{SHARED_CSS}
  </style>
{extra_head}
</head>
<body>
"""

def render_html_footer() -> str:
    """Render closing tags."""
    return """</body>
</html>
"""


def _finite_json_safe(obj):
    """Recursively replace non-finite floats (NaN / Infinity / -Infinity) with
    None so the result serializes to VALID JSON.

    Python's ``json.dumps`` emits the bare literals ``NaN`` / ``Infinity`` /
    ``-Infinity`` for non-finite floats by default (``allow_nan=True``) — tokens
    that are NOT legal JSON, so the browser's ``JSON.parse(...)`` of the inline
    ``#page-data`` script THROWS a SyntaxError, the petite-vue app never mounts,
    and the WHOLE launchpad / stats / council page renders blank (or flashes raw
    ``{{ }}``). A single non-finite float anywhere in page_data — e.g. a
    corrupted/partially-written ``evals/results/eval_*.json`` whose
    ``aggregate_score`` is ``NaN`` (``json.loads`` accepts the literal), or a
    topics.json centroid poisoned by a NaN embedding — is enough to take the
    surface down. Coercing each non-finite to ``null`` keeps the JSON valid and
    degrades the one bad value gracefully instead of erasing the page.
    """
    import math

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite_json_safe(v) for v in obj]
    return obj


def page_data_script_json(page_data) -> str:
    r"""JSON for an inline ``<script type="application/json" id="page-data">``,
    with every ``<`` escaped to its ``<`` JSON form so a value that contains
    ``</script>`` cannot break out of the element (a markup-injection / stored-XSS
    guard on corpus-derived page data — see tests/test_memory_viewer_xss_browser).

    Non-finite floats are coerced to ``null`` first (``_finite_json_safe``) and
    the dump pins ``allow_nan=False`` so a bare ``NaN`` / ``Infinity`` can NEVER
    leak into the inline ``#page-data`` JSON and crash the client's
    ``JSON.parse`` (which would blank the whole surface — see that helper).

    Deliberately a function, NOT inlined into the page-template f-strings: a
    backslash inside an f-string replacement field is a SyntaxError before Python
    3.12 (PEP 701), and Trinity supports >=3.10. Inlining it (the historical form
    ``f"...{json.dumps(page_data...).replace('<', '<')}..."``) made
    council_review + launchpad_template UNIMPORTABLE on 3.10/3.11 — a hard
    SyntaxError on a core import path, invisible because dev/CI runs 3.12. Caught
    2026-06-02 booting the plugin launcher under an older interpreter.
    """
    import json

    return json.dumps(
        _finite_json_safe(page_data),
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).replace("<", "\\u003c")
