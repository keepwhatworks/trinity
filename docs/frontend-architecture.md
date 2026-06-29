---
class: aspirational
---

# Trinity Frontend Architecture

## One-Liner

Trinity’s frontend is:

- **static HTML** for durable local artifacts
- **`petite-vue`** for interactive islands
- **`Chart.js`** for radar, Elo, and report visuals
- **`DESIGN.md`** as the visual contract

This keeps the product local-first, `file://` friendly, and compatible with the
Shortcuts / local-helper execution model.

---

## Why This Stack

Trinity is not building a hosted app shell. It is generating local artifacts:

- **Launchpad** (`portal_pages/launchpad.html`) — autofill, personal routing table card, **pair-wise `lens` cardses card with copy buttons**, recent councils, settings modal
- **Unified council page** (`portal_pages/status/...`) — single page handling both in-flight (`?status_token=`) and post-hoc (`?council_id=`) views. Member streaming, chairman synthesis, structured Routing label section, **rating buttons inline** (no separate Signal/Rating page)
- Future surfaces (deferred): weekly Elo report, leaderboard view, radar/battle/taste-profile social cards. The shipped social object is the `lens` card (see DESIGN.md Social Artifact Guidance)

Those pages need:

1. to open directly from disk
2. to remain useful when reopened later
3. to support light interaction without a server
4. to stay simple enough for generated HTML

That rules out a SPA-first architecture.

---

## Stack Decisions

## 1. Static HTML is the shell

All core surfaces should continue to be generated as static HTML files.

Why:

- works with `file://`
- works with browser bookmarks
- keeps pages durable and inspectable
- fits the no-server architecture
- makes review pages and reports feel like artifacts, not temporary UI

Static HTML is the product shell, not a fallback.

### Responsibilities

- semantic structure
- embedded JSON data payloads
- durable local artifact output
- links to Shortcuts / local execution bridges

### Not responsible for

- global client-side routing
- app bootstrapping across many pages
- long-lived browser state

---

## 2. `petite-vue` is the interaction layer

Use `petite-vue` only for page-local interaction.

Why `petite-vue`:

- no build step required
- optimized for progressive enhancement
- DOM is the template
- small enough for generated static pages
- more structured than hand-rolled DOM mutation
- better fit than a SPA framework

### Use `petite-vue` for

- Launchpad prompt entry
- example prompt selection
- council progress states
- answer selection / rating flows
- show/hide evidence sections
- local filters / sort toggles
- compact interactive cards

### Do not use `petite-vue` for

- global routing
- app-wide state containers
- recreating a SPA
- complex async orchestration logic that belongs in Trinity itself

### Mount pattern

Prefer one app per page or one app per major region.

Example:

```html
<script type="application/json" id="page-data">{ ... }</script>
<div id="launchpad-app" v-scope="LaunchpadApp()">
  ...
</div>
<script type="module">
  import { createApp } from '../../vendor/petite-vue.es.js'

  const pageData = JSON.parse(document.getElementById('page-data').textContent)

  function LaunchpadApp() {
    return {
      pageData,
      prompt: '',
      launch() { ... },
    }
  }

  createApp({ LaunchpadApp }).mount()
</script>
```

---

## 3. `Chart.js` is the visualization layer

Use `Chart.js` for first-generation charts and shareable social artifacts.

Why:

- easy to embed in static HTML
- low ceremony
- enough for radar, bar, line, and small comparison charts
- strong fit for screenshotable outputs

### Primary uses

- personal model radar chart
- provider Elo chart
- weekly trend chart
- comparison summary chart

### Future social pages

- radar chart page
- battle card page
- weekly model report page

### Data pattern

Charts should render from embedded JSON, not fetch data dynamically.

Example:

```html
<script type="application/json" id="chart-data">{ ... }</script>
<canvas id="radar-chart"></canvas>
<script src="../../vendor/chart.umd.min.js"></script>
<script>
  const data = JSON.parse(document.getElementById('chart-data').textContent)
  const ctx = document.getElementById('radar-chart')
  new Chart(ctx, { type: 'radar', data, options: {} })
</script>
```

---

## 4. `DESIGN.md` is the UI contract

`DESIGN.md` is the system-level design source of truth.

It should define:

- visual direction
- typography
- color roles
- spacing
- card hierarchy
- motion limits
- chart styling rules
- social-card export rules

Generated HTML and shared CSS should follow `DESIGN.md`, not drift page by page.

### `DESIGN.md` should govern

- Launchpad
- Council review page
- Signal / rating page
- Weekly digest
- Radar and battle-card pages

---

## Page-by-Page Architecture

## Launchpad

### Purpose

The local action surface and first-run entry point.

### Stack

- static HTML shell
- `petite-vue` for interaction
- optional `Chart.js` for compact “your model performance” visuals later

### Responsibilities

- council-first entry
- prompt input
- example selection
- recent council history
- suggested next actions

### Notes

- the Launchpad should feel like a document with actions, not a web app dashboard
- the first primary CTA should be `Run Your First Council`

---

## Council Review Page

### Purpose

Show the council result:

- winner
- agreement
- differences
- raw outputs
- next action

### Stack

- static HTML shell
- light `petite-vue` for collapsible sections and interaction

### Responsibilities

- make the recommendation obvious
- keep raw outputs available but secondary
- support export / share later

---

## Council Review (inline, not a separate page)

The review UI lives in the **unified council review page**, not a separate Signal page. After members finish and the chairman synthesises, the same page surfaces the chairman's verdict (winner, agreed/disagreed claims with `why_matters`) and refinement affordances.

**Post-2026-05-21/22 supervision signal:** the chairman's `routing_label.winner` is the supervision signal — fed automatically into `compute_personal_routing_table()` via `~/.trinity/council_outcomes/<id>.json`. No agent-side rating call is needed. The rating UX (`record_outcome` MCP tool, `council-rate` CLI, `rate_council` dispatch action, the launchpad "Preferred" click affordance) was retired alongside "we are sunsetting user ratings"; the chairman pick is the entire signal now.

The chairman's pick renders as a **"Lens pick"** badge on the winning member card — this replaced the prior user-clickable "Preferred" affordance. The personal routing table aggregation no longer blends user verdicts at 0.7 weight (commit 44eb934); chairman picks are the entire signal.

### UX pattern

1. read the synthesis (agreed claims / disagreed claims with why_matters)
2. note the "Lens pick" badge on the chairman-chosen member card
3. (optional) click Refine to send the chairman a "I would have picked X because Y" prompt — the post-rating-UX signal path; refines the council, doesn't write a rating

---

## Weekly Digest (deferred)

### Purpose

Editorial summary of:

- model performance shifts
- switches
- recommendations
- drift
- costs

### Stack

- static HTML shell
- mostly document-first
- `Chart.js` for summary visuals

### Responsibilities

- summarize, not overwhelm
- emphasize deltas and trends

---

## Radar / Social Pages

### Purpose

Generate the most shareable output from Trinity:

- personal model radar chart
- battle cards
- taste profile

### Stack

- static HTML shell
- `Chart.js`
- optional `petite-vue` for display mode toggles

### Responsibilities

- be screenshotable
- be legible in a single glance
- feel personal and status-bearing

---

## Data Flow Pattern

The frontend should consume **embedded page data**, not fetch state from a live
backend.

Recommended pattern:

1. Trinity writes a page-specific JSON blob
2. page embeds it in `<script type="application/json">`
3. `petite-vue` and `Chart.js` hydrate from that blob

This keeps pages:

- deterministic
- portable
- archive-friendly
- testable

---

## Interaction Rules

## Keep logic out of the browser when possible

Browser code should:

- render
- reveal
- filter
- capture preference
- launch local actions

Browser code should not:

- own provider orchestration
- compute council results
- maintain business-critical state

That logic belongs in Trinity’s Python side.

## Prefer local launches over in-browser orchestration

Buttons should:

- open review pages
- trigger Shortcuts or a future local helper
- move the user into the next local artifact

They should not attempt to turn the browser into the orchestrator.

---

## Recommended Directory / File Pattern

Keep generated-page code close to the page type. Current shipped modules:

- `launchpad_page.py` — launchpad orchestrator (also writes the memory viewer alongside)
- `launchpad_template.py` — launchpad HTML/CSS/JS template (settings modal, autofill, personal-routing-table card, lens card, memory-chip card)
- `launchpad_data.py` — assembles the JSON payload the launchpad reads
- `launchpad_runtime.py` — refresh + open-in-browser plumbing
- `memory_viewer.py` — generic memory.html viewer (renders lens.md / picks.json / routing.json / topics.json / vocabulary.md / core.md with inlined contents)
- `council_review.py` — unified council page (handles both `?status_token=` in-flight and `?council_id=` post-hoc views; rating UI is inline)
- `me_lenses.py` — parses `~/.trinity/memories/lens.md` into structured taste lenses for the launchpad card
- `council_share.py` — was deleted along with the `--safe` Privacy-Safe Share Card. (The `council-share` CLI command is still in `commands/council.py` and copies the unified review HTML to Desktop.)

Future surfaces (deferred): `digest_page.py`, `radar_page.py`, `battle_card_page.py`. The your `lens` cards subsume the social-artifact role for now.

Keep shared frontend helpers in:

- `design_system.py`
- future optional JS snippets if needed:
  - embedded directly in generated HTML, or
  - shared local assets if you later decide to ship them

---

## What to Avoid

Do not adopt:

- React / Vite / SPA-first architecture
- a global frontend app shell
- htmx-centered request flows
- heavy client-side routing
- Phaser/Kaboom/p5 as the main shell

These all pull Trinity away from:

- local durable artifacts
- `file://` compatibility
- no-server simplicity

---

## Evolution Path

### Now

- static HTML
- shared CSS from `design_system.py`
- `petite-vue` for interaction
- `Chart.js` for radar/report visuals

### Later

- better export pages
- more refined social-card outputs
- possible local `trinity://` native helper instead of Shortcuts

The page model should not need to change when the dispatch bridge changes.

---

## Decision Summary

Use:

- **static HTML** for the shell
- **`petite-vue`** for interaction
- **`Chart.js`** for charts and social artifacts
- **`DESIGN.md`** for visual governance

This is the frontend stack that best matches Trinity’s local-first
architecture.
