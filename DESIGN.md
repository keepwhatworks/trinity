---
class: live
---

# Trinity Design System

## Overview

Trinity is a local intelligence layer, not a SaaS dashboard. The UI should feel calm, trustworthy, and slightly editorial: more like a calm field notebook or control desk than an analytics product. Favor legibility, hierarchy, and restraint over visual novelty.

The primary surfaces are static HTML pages opened locally in the browser:
- **Launchpad** (autofill suggestions; personal routing table card with provider scores per `task_type`; **pair-wise `/me` lenses card with copy-to-socials buttons** — title + why-it-matters per principle, no verbatim prompts; recent councils)
- **Unified council page** — single page handling both in-flight (`?status_token=...`) and post-hoc (`?council_id=...`) views. Member responses stream as each finishes; chairman synthesis status; structured Routing label section; per-member rating buttons (the rating interaction lives inside this page, not a separate Signal/Rating page)
- Future surfaces: weekly Elo report (deferred); local leaderboard view over `compute_personal_routing_table()` (currently rendered as the launchpad card)

These surfaces share one visual language so the product feels cohesive even though the pages are generated independently. The `/me` lens cards are the **shareable social artifact** — pair-wise model/user context stays local for the chairman; only the principle (title + why-it-matters) ships to clipboard.

## Frontend Stack Contract

Trinity's frontend stack is:

- **static HTML** for structure and artifact durability
- **`petite-vue`** for interactive islands
- **`Chart.js`** for radar, Elo, and report visuals
- **`marked`** for markdown rendering on the memory viewer
- **`d3-force` (split modules)** for the topics graph (`d3-selection`,
  `d3-dispatch`, `d3-timer`, `d3-quadtree`, `d3-drag`, `d3-force`
  — ~80 KB total, not the full ~250 KB d3 bundle)

All JS is vendored locally under `~/.trinity/vendor/` (12 JS files —
petite-vue, chart.js, marked, 9 d3-* modules — plus the Space Grotesk
display font: 2 woff2 weights + its OFL license — published once on
`install-mcp` by `vendor.publish_vendor_files()`). No NPM bundling and
no third-party CDN at runtime — the "nothing leaves your machine" claim
is structural, enforced by `tests/test_no_cdn_in_rendered_html.py` (and
`tests/test_vendor_integrity.py` pins every vendored byte, JS + woff2).
Memory content is inlined at render time (`portal-html`); pages never
`fetch()` at runtime so they work under `file://`.

Design choices should assume:

- pages open from disk
- pages may be bookmarked and reopened later
- interaction is page-local, not SPA-global
- visuals may need to be screenshotable and shareable

## Visual Theme

- Tone: grounded, intelligent, high-trust, local-first
- Density: medium to airy
- Layout feel: document-first, not dashboard-first
- Motion: minimal; prefer static clarity over animated ornament
- Atmosphere: cool mist neutrals, soft cool-charcoal ink, ONE muted-teal accent, a faint slow breathing aura, subtle depth

## Color Palette

### Core roles

- Background base: `#eaecef`
- Background wash: `#eff1f4`
- Surface: `#fafbfc`
- Surface muted: `#f1f3f6`
- Border: `#dde1e6`
- Text primary: `#2f363c`
- Text secondary: `#5b646b`
- Text muted: `#5e666f` (AA: ≥4.5:1 on every light bg it renders on, incl. the rgba-tinted accent cards over `--bg-base`)
- Primary action: `#3f777c` (AA: white text 4.96:1)
- Primary action hover: `#34666b`
- Primary action text: `#fbfdfc`
- Accent (muted teal): `#4f9095`
- Success: `#4f9095` (FILL only — bars/dots/border-left/washes; 2.9:1 as text on the green success tint, never readable copy)
- Success text: `#2d6a4f` (the readable deep green for the "Done" member pill / `.badge.success` label — AA ≥4.5:1 on the `rgba(45,106,79,…)` success tint; mirrors the `--warning`/`--warning-text` split)
- Warning: `#bd9658` (FILL only — borders/icons/washes; 2.3–2.6:1 as text, never readable copy)
- Warning text: `#79591b` (the readable deep amber for failure-message body copy — AA ≥4.5:1 on every light bg; mirrors the `--accent`/`--accent-deep` split)
- Danger: `#bd6a5a` (FILL only — the 3px border-left / icon on a danger tint; 2.8–3.5:1 as text, never readable copy)
- Danger text: `#99392c` (the readable deep terracotta for small status labels — AA ≥4.5:1 on the danger tint, e.g. the memory viewer's `.viewer-trust-label` "DEGRADED" tag / `.badge.danger` label; mirrors the `--success-text`/`--warning-text` split)
- Info: `#7fa0ad`

### Usage rules

- Primary (filled) actions are muted teal; teal is the ONE accent (eyebrows, the `>` mark, rules, active states, one-word title accents). Soft slate `#94a3ad` is the only quiet secondary.
- Status colors (success=teal family / warning / danger / info) appear ONLY to encode status, as soft washes — never as decoration.
- Avoid introducing additional saturated colors unless they encode status.
- Prefer contrast through ink, paper, and border relationships before using color.

## Typography

### Font stacks

- Display / body sans: `"Hanken Grotesk", "SF Pro Text", system-ui, -apple-system, sans-serif` (vendored woff2 400/500/600/700, OFL — self-hosted under `~/.trinity/vendor/` + `docs/fonts/`, no CDN)
- UI sans: `"SF Pro Text", "Segoe UI", system-ui, sans-serif`
- Monospace: `"JetBrains Mono", "SF Mono", ui-monospace, monospace` (vendored woff2 400/500, OFL — commands/paths only)

### Hierarchy

- Eyebrow / label:
  - Font: UI sans
  - Size: 12px
  - Weight: 700
  - Case: uppercase
  - Letter spacing: 0.14em
- Page title:
  - Font: Hanken Grotesk (weight 600)
  - Size: 56px desktop / 38px mobile
  - Weight: 700
  - Line height: 0.95 to 1.0
- Section title:
  - Font: Hanken Grotesk (weight 600)
  - Size: 24px desktop / 20px mobile
  - Weight: 700
  - Line height: 1.1
- Body:
  - Font: UI sans
  - Size: 18px desktop / 16px mobile
  - Weight: 400
  - Line height: 1.55
- Secondary body:
  - Font: UI sans
  - Size: 15px
  - Weight: 400
  - Line height: 1.5
- Code / paths / commands:
  - Font: monospace
  - Size: 14px

### Typography rules

- Use Hanken Grotesk for everything (titles weight 600, body, UI); a one-word title accent uses teal, never italics. JetBrains Mono only for commands/paths.
- Use sans for all explanatory copy, metadata, buttons, and labels.
- Do not mix more than two font families on a page.

## Spacing and Layout

### Spacing scale

- 4px
- 8px
- 12px
- 16px
- 24px
- 32px
- 48px
- 64px

### Layout principles

- Center a single primary column with generous outer padding.
- Prefer stacked sections over multi-panel dashboard grids.
- Use width to create calm, not to cram more controls on screen.
- Keep action groups close to the content they affect.

### Container rules

- Max content width: `1080px`
- Default page padding: `32px`
- Mobile page padding: `18px`
- Section gap: `24px` to `36px`

## Shapes and Elevation

- Large card radius: `24px`
- Medium control radius: `14px`
- Small radius: `10px`

### Elevation

- Cards should use soft, low-contrast shadows:
  - `0 10px 40px rgba(60, 72, 86, 0.10)`
- Borders should remain visible even when shadows are present.
- Avoid harsh dark shadows or glassmorphism effects.

## Components

### Page shell

- A subtle neutral wash background is allowed, but quiet; the ground is cool mist, not warm paper.
- Content should sit on light surfaces with strong readable contrast.
- Top-of-page identity should be clear and stable across all generated pages.

### Primary button

- Filled with muted teal (primary action)
- Light text
- Rounded pill or soft capsule
- Strong enough to anchor the page visually

### Secondary button

- Surface-colored fill or near-white fill
- Border visible
- Text in primary ink
- Must read as secondary, not disabled

### Action cards

- This is the most important Trinity component.
- Each card should show:
  - action title
  - brief rationale
  - source/provider or task metadata
  - one clear primary CTA
- Use stronger visual emphasis for the primary action card than for supporting content.

### Status cards

- Used for “No pending actions”, health summaries, or passive information.
- Quieter than action cards.
- Should never visually compete with a real action.

### Metadata rows

- Use compact sans text
- Muted color
- Prefer one-line summaries over verbose labels

## Launchpad Guidance

- The launchpad is a local action surface, not an app homepage.
- Keep the page short and scannable.
- Put pending actions ahead of explanatory prose.
- If there are no actions, make the empty state calm and concise.
- Avoid long onboarding text once the core setup is complete.

## Council Review Guidance

- Lead with:
  - task
  - recommendation / winner
  - agreement
  - differences
- Raw model outputs should be expandable or visually subordinate.
- The primary decision should be obvious without scrolling far.

## Council Rating Interaction

The rating UI lives **inside the unified council review page**, not a separate Signal page. Inline guidance:

- Answer cards are equally weighted until the user selects a winner.
- Confirmation state should feel rewarding but not gamified.
- The chairman's pick on the council outcome is the supervision signal — no user-rating step. The "Preferred" rating chip + CLI `council-rate` + MCP `record_outcome` + macOS Shortcut dispatch were all retired in the 2026-05-21/22 rating-UX sunset (see `src/trinity_local/retired_names.py` for the canonical registry). Council pages now carry a "Lens pick" badge sourced from `routing_label.winner`; the refinement prompt below each council captures "what should it have been instead" inline — the post-pivot signal embedded in the user's natural flow.
- Disagreed-claims block reads as a passage, not a table — the structured Routing JSON is the substrate, the prose is the experience.

## Chart Guidance

- Charts only when they add immediate comprehension.
- Favor:
  - compact bar charts (provider scores per task_type on the personal routing table card)
  - simple line charts (Elo trajectory)
- Avoid chart junk, legends that dominate the page, or analyst-dashboard density.
- Charts inherit the paper-and-ink visual language:
  - muted gridlines
  - dark labels
  - restrained accent palette

## Social Artifact Guidance

The shipped social artifact is the **`/me` lens card** — title + why-it-matters per principle, copyable to socials with one click.

- Per-card Copy emits one principle as a short, paste-ready block.
- Section-level "Copy all" emits every principle in one bundle, headed with "The principles I encode by what I redirect away from:".
- Verbatim model/user prompt content is **never** in the share text — it stays local for the chairman.
- Cards should read clearly in a crop; supporting copy is short.

(Future surfaces — radar/battle/taste-profile pages — are deferred. The /me lens cards subsume the "shareable taste artifact" use case for now.)

## Navigation pattern (sub-pages)

The launchpad is the root and uses the hero pattern (eyebrow + H1 +
lede + settings gear) — no topbar, no back arrow.

Every other page is a sub-page and uses the shared `.trinity-topbar`:

- 14 px / 28 px padding, surface background, 1 px bottom border
- Pill-shaped `← Launchpad` back link on the left (`.topbar-back`)
- Page title (16 px, semibold) immediately after the back link
- `.topbar-spacer` to push secondary actions to the right
- Optional `.topbar-action` pills on the right (e.g. "View full thread")

CSS lives in `design_system.SHARED_CSS` so a single tweak applies to
all sub-pages. Pages must:

- Place the `<header class="trinity-topbar">` **outside** `<main>` so
  the bar spans the viewport while content respects the 1080 px column
- Use "← Launchpad" exactly — the arrow conveys the back-direction
  without the verbose "Back to" prefix
- Set the page title to a single noun ("Your lens", "Council",
  "Council review") — no breadcrumbs, no dynamic titles

Live council, council review, and memory viewer all conform.

## Memory Viewer Guidance

The memory viewer (`~/.trinity/portal_pages/memory.html`) is the canonical
hand-inspection surface for your lens — one page that renders the four
levels of the cognitive hierarchy (`core.md` manifesto → `lens.md`
tensions → `topics.json` basins → `vocabulary.md` anchors/homonyms/
synonyms). Operational scoreboards (`scoreboard/picks.json`,
`scoreboard/routing.json`) surface elsewhere on the launchpad — not in
this cognitive page. Conventions:

- **Two-column shell**: 240 px nav of memory chips on the left, content
  on the right. Active memory tinted with `rgba(37, 88, 71, 0.10)`
  (low-alpha primary green) — never use a saturated background.
- **Page palette**: identical to the launchpad. Cool mist bg
  (`#eaecef`), muted-teal primary (`#3f777c`), muted-teal accent
  (`#4f9095`). No green, no cream, no indigo/violet/tailwind-blue.
- **Per-memory header**: filename in monospace + muted-teal accent,
  one-line brain-analog + tagline below. Reads like a document header,
  not a dashboard card.
- **Reader / Raw toggle** for JSON memories. Reader is schema-aware
  (cards for picks, table for routing, force graph for topics);
  Raw is pretty-printed syntax-highlighted JSON. Default to Reader.
- **Topic graph** is the one place dark canvas is allowed —
  `#221c18 → #14110f` radial gradient, matching `--fg` ink hue but
  inverted. Node fill uses the brown→green gradient by basin size
  (small = warm brown, large = forest green). Edge stroke is
  warm-brown at low alpha; strong-similarity edges promote to higher
  alpha, never to a new color.
- **Representatives, not labels**: when surfacing what a basin/cluster
  contains, prefer the actual prompts (centroid-nearest) over
  TF-IDF top terms. Top terms are vocabulary surfacing; the prompts
  are intent.
- **Pre-rendered HTML, no fetch**: like the council pages, the viewer
  inlines memory content at `portal-html` time into a
  `window.__TRINITY_MEMORIES__` global. Works under `file://`, no
  `trinity-local serve` required.
- **DOMParser, not innerHTML**: rendered markdown (via the bundled
  `marked`) flows through `DOMParser` → adopted nodes. Never
  innerHTML the live tree — even for trusted content. Strips
  `<script>/<style>/<iframe>/<object>/<embed>` as defense-in-depth.

## Responsive Behavior

- Collapse to a single-column layout on narrow screens.
- Maintain strong title presence on mobile, but reduce size and spacing.
- Buttons should remain finger-friendly.
- Do not rely on hover-only affordances.

## Do

- Use strong hierarchy and quiet surfaces.
- Make actions obvious.
- Keep the overall tone warm and local, not corporate SaaS.
- Let whitespace create clarity.
- Favor summary-first layouts.

## Do Not

- Do not use generic Tailwind-blue product styling.
- Do not turn pages into dashboard widget grids by default.
- Do not overload the page with explanatory copy.
- Do not introduce purple or neon accents.
- Do not use heavy gradients, frosted glass, or loud AI-brand visuals.
- Do not make everything look interactive; reserve emphasis for true actions.
