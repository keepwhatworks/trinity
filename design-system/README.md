# Trinity Local — Design System

A design system for **Trinity Local** and its marketing home **keepwhatworks.com** —
distilled from the product's own source of truth so any agent can produce
on-brand interfaces, pages, and shareable artifacts.

> **Ask all three. Keep what works.** Trinity Local is a free, local-first AI council. Ask once; it fans
> your question out to Claude, GPT, and Gemini in parallel, shows you exactly where
> they split, and a local *chairman* synthesizes the verdict the way **your** taste
> would — read from a "lens" distilled out of the transcripts already on your disk.
> No app, no cloud, no API key. MIT, local, free for individuals.

---

## Sources (the inputs this system was built from)

Everything here was lifted from a single public repository — read it end-to-end to go deeper:

- **GitHub — `keepwhatworks/trinity`** · <https://github.com/keepwhatworks/trinity>
  - `DESIGN.md` — the written design system (colors, type, components, do/don't). The spine of this folder.
  - `src/trinity_local/design_system.py` — the **canonical** `COLORS` dict + `SHARED_CSS` shipped into every product page. Tokens in `colors_and_type.css` are verbatim from here.
  - `docs/index.html` + `docs/style.css` — the **keepwhatworks.com** marketing site (hero + featured product + essays).
  - `docs/articles/*.html` — five long-form essays ("The Architecture of Becoming," etc.) that name the principles behind the product.
  - `docs/launchpad_example.png` — screenshot of the real Launchpad (the local product UI).
  - `docs/launch_assets/eval_card_*.png` — the shareable "personal benchmark" eval cards.

The reader is encouraged to explore that repository directly to build richer,
more accurate designs than this snapshot can capture.

> **Two surfaces, one language.** The marketing site (`docs/style.css`) is
> deliberately matched to the product UI (`design_system.py`) so the site
> "visually descends from the launchpad." This system treats them as one brand
> with two contexts.

---

## The product, in brief

| | |
|---|---|
| **Name** | Trinity Local (wordmark often `> Trinity Local`) |
| **Maker** | Vishi Gondi (solo dev) · [@vishigondi](https://x.com/vishigondi) |
| **Home** | keepwhatworks.com — "Notes from the arena" |
| **What it is** | A local MCP server + Chrome extension. Runs "councils" across Claude / GPT / Gemini, synthesizes through *your* lens, and learns which model wins which kind of question. |
| **License / model** | MIT, free for individuals forever. Hosted "Trinity for Teams" is the eventual revenue path. |
| **Wedge** | Privacy + cross-lab neutrality: "Anthropic can't recommend ChatGPT; OpenAI can't recommend Claude." The cross-provider memory layer has to come from outside the labs. |

**Primary surfaces** (all static HTML opened locally under `file://`):
- **Launchpad** — the root local app: council composer, personal routing table, `/me` lens cards, recent councils. Hero pattern, no topbar.
- **Council page** — member answers stream in; chairman synthesizes; agreed/disagreed claims; "Lens pick" badge. Uses the shared `.trinity-topbar`.
- **Memory viewer** — inspect the four-tier lens (core → tensions → basins → vocabulary).
- **Eval card / `/me` lens card** — the shareable social artifacts.

---

## CONTENT FUNDAMENTALS — how Trinity writes

The voice is **grounded, literary, and a little combative** — "written from the arena."
It earns trust by being plain-spoken and specific, then occasionally swings for a
big editorial line. Two registers coexist: **terse product UI copy** and
**essayistic long-form**.

**Person & address.** Second person, direct. *"Ask all three. Keep what works." "Stop copy-pasting
prompts between tabs like an animal." "The answer you'd pick."* The product talks
**to you** about **your** judgment — "you," "your taste," "your lens," "YOUR kind of
question." First person appears only as the maker's aside (*"Objections (the ones I
had)"*).

**Casing.** Sentence case everywhere for headlines and body. Eyebrows/labels are the
exception: **UPPERCASE, letter-spaced** (`COUNCIL`, `ROUTING`, `NEW MODEL`). Em-dashes
are the signature punctuation — used for asides, pivots, and rhythm. Caps for
**emphasis on a single word** ("YOUR," "you'd") rather than italics in UI copy.

**Tone moves to copy:**
- **Painkiller then moat.** State the immediate value, then the compounding one. *"The council is the painkiller; the lens is the moat."*
- **Name the wedge bluntly.** *"They literally can't."* Confident, structural claims.
- **Specificity over adjectives.** Real numbers, real commands: *"~62 MB resident," "45 prompts, 4 axes," `trinity-local council --task "…"`.*
- **Anti-hype.** Explicitly rejects SaaS gloss. *"No new app. No service. No API key."*

**Essay register** (keepwhatworks.com) is more lyrical and aphoristic:
*"Reality isn't a noun — it's a verb." "Keep what works. Throw the rest."
"A good life is falling in love with a problem and living with it for a decade."*
Headlines pair a bold claim with a serif italic accent word (*works*, *becoming*).

**Emoji.** Almost never. One sanctioned spot: a single 🎉 on the **"NEW MODEL"**
card, and a rare winking 🤪 to close an essay. Treat emoji as a once-per-page
seasoning at most — never as bullets or iconography. Status and structure are
carried by type and color, not emoji.

**Vocabulary anchors** (use these exact terms): *council, chairman, lens, the
split / where they split, agreed claims, disagreed claims, why_matters, routing
table, eval, taste, basin, the arena.*

---

## VISUAL FOUNDATIONS — the brand's visual logic

> **ACTIVE THEME — Calm / Muted Teal.** This design system uses a deliberately
> *soothing* direction (cool mist neutrals, a soft humanist sans, one muted-teal
> accent) rather than the warm-paper + serif look the product ships today — that
> template reads as the generic "Claude / Opus" house style. The canonical tokens
> live in `colors_and_type.css`; the launchpad UI kit is the reference
> implementation. The original warm-paper brand is preserved below only as
> historical reference — **do not mix the two**; build in the calm/teal theme.

**Atmosphere.** Cool soothing mist, soft ink, gentle depth. Calm, restful,
high-trust, local-first. Density airy and unhurried; **document-first, not
dashboard-first.** A faint, slow "breathing" aura may drift behind the page; motion
is slow and minimal (no bounces, no parallax).

**Color (calm/teal).** A cool neutral spine (`#eaecef` base → `#fafbfc` surface →
`#dde1e6` border → `#2f363c` cool-charcoal ink) with **exactly one** accent: muted
teal `#4f9095` (deep `#34666b` for hover/text, fill `#3f777c`, soft wash `#e1edec`).
Teal carries primary actions, active states, eyebrow dashes, and one-word title
accents. Soft slate `#94a3ad` is the only quiet secondary. Status colors
(success=teal family / warning / danger / info) appear **only to encode status**,
as soft washes. **No green, no warm cream, no serif, no terracotta, no purple/neon,
no decorative gradients.**

**Type.** **One soft humanist sans** — **Hanken Grotesk** (SF Pro Text / system-ui
fallback) for *everything*: titles (weight 600, `letter-spacing` -0.02em), body,
metadata, buttons, labels. **No serif anywhere.** A one-word title accent uses teal
(`.accent`), never italics. **Mono** (JetBrains Mono) is reserved strictly for
commands / paths / inline code. Titles up to 52px; body 15.5px; secondary 14.5px;
code 13.5px; eyebrow 12px (uppercase, tracked, with a small teal dash).

**Backgrounds.** Flat cool mist with a soft top highlight — *no* full-bleed
gradients, *no* frosted glass.
The marketing site allows a "subtle paper-like wash" at most. Essays are the one
place imagery appears: **full-bleed magazine-cover photos** at the top of each
article (edge-to-edge, no radius). The memory viewer's topic graph is the **only**
place a dark canvas is allowed (`#221c18 → #14110f` radial). Otherwise everything
sits on light surfaces.

**Cards.** Surface fill (`#fafbfc`), 1px visible border (`#dde1e6`), large radius
(**22px** product cards, 18px controls, 14px inputs/site cards), and a soft cool
shadow `0 10px 40px rgba(60,72,86,0.10)` (or the lighter `0 4px 18px …,0.06`).
**Borders stay visible even under shadow** — never borderless floating cards. Two
special card patterns:
- **Left-rail accent** — a 3px soft teal (or `info` slate-blue) bar inset on the
  card's left edge, used sparingly and meaningfully, *not* as default decoration.
- **Tinted optional card** — a soft teal-wash gradient (`--accent-soft` → surface)
  for the "new model" / deeper-memory cards.

**Borders & radii.** Radii: 22 (card) / 18 (control) / 14 (input, chip) / 999 (pill).
Hairline borders everywhere; tables and rows separated by 1px `--border`, never heavy
rules.

**Buttons.** Soft pill. **Primary** = filled muted teal, light text, soft teal
shadow — anchors the page. **Secondary** = surface fill + visible border + ink text
(must read as secondary, never disabled). **Ghost** = transparent, teal text. Hover
deepens the teal (`#34666b`) / lifts 1px / deepens the secondary surface — never a glow.

**Motion.** Slow and soothing. Transitions short (0.15–0.2s ease) on hover/border/
background; cards rise gently on entrance (staggered) and lift ~1–2px on hover. A
faint **breathing aura** may drift behind the page (~26s) and the die mark settles
in on load. **No bounces, no infinite spinners, no parallax.** All gated behind
`prefers-reduced-motion`. Press states are quiet (color shift, not shrink).

**Transparency & blur.** Effectively none. Low-alpha *fills* are used (status washes,
active tints) but never backdrop-blur or glass. Depth comes from the paper/surface/
border stack, not blur.

**Imagery vibe.** Largely imagery-free — the calm comes from space, type, and the
teal accent. If a photo is needed (e.g. an essay hero), prefer soft, cool, airy
imagery; avoid warm-amber stock and people-at-laptops.

**Layout rules.** One centered primary column (1000px product / 960px site / ~680px
essays) with generous outer padding. Stacked sections over multi-panel grids. Width
is used "to create calm, not to cram." The Launchpad keeps a slim left **councils
rail** (~280px) over the mist; sub-pages get a full-width topbar with a pill
`← Launchpad` back link. Summary-first: lead with task → winner → agreement →
differences; raw model output is subordinate/expandable.

---

## ICONOGRAPHY

Trinity is **almost icon-free by design** — it leans on typography, the teal eyebrow
dashes, and monospace command chips instead of an icon set. There is **no icon font
and no SVG icon library** in the product.

- **Brand mark — the die.** The favicon (`assets/favicon.png`) is a **rounded square
  die showing three pips** (recolored to teal in this theme): the "trinity" of three
  models. This is the closest thing to a logo. Reproduce it as the favicon / app mark.
- **Wordmark prompt — `>`.** The site brand renders as a monospace teal terminal
  prompt before the wordmark (`> keepwhatworks`, `> Trinity Local`), signaling "lives
  in your shell." Set in CSS (`::before { content: ">" }`), not an image.
- **Braille flourish — `⠕`.** The README title uses `⠕ Trinity Local` — a stippled
  braille glyph echoing the three pips. Decorative, optional.
- **Unicode arrows as affordances.** Direction and lists use Unicode glyphs, not
  icons: `→` for feature-list bullets and "see more" links, `←` for the Launchpad
  back pill. The settings control in the Launchpad is the lone exception — a small
  **gear glyph** (⚙) top-right.
- **Emoji.** Reserved to a single 🎉 on the "NEW MODEL" card (see Content
  Fundamentals). Not used as iconography.

**Guidance for new work:** prefer **type + Unicode arrows (`→ ←`) + the teal eyebrow
dash** to carry structure. If a genuine icon is unavoidable, use a **thin
line/stroke** style at the weight of the gear glyph and keep it ink-colored, not
teal. *Do not introduce a heavy filled icon set, emoji bullets, or decorative SVGs.*
The pips/die and the `>` prompt are the only marks that should feel "branded."

---

## CONTENT INDEX — what's in this folder

**Root**
- `README.md` — this file: product context, content + visual foundations, iconography, manifest.
- `colors_and_type.css` — canonical **calm/teal** color + type tokens, spacing, radii,
  shadow, semantic type classes.
- `SKILL.md` — Agent-Skill front-matter so this system can be invoked in Claude Code.

**`assets/`** — brand imagery
- `favicon.png` — the die / brand mark (64×64).
- `launchpad_example.png` — reference screenshot of the real product UI.
- `eval_card_claude.png` — reference for the shareable "personal benchmark" artifact.

**`preview/`** — Design-System tab cards (colors, type, spacing, components, brand).

**`ui_kits/`** — high-fidelity, interactive recreations
- `launchpad/` — the **local product UI**: Launchpad (council composer, routing
  table, lens cards, councils rail) + a Council review page. `index.html` is a
  click-through; components are small reusable JSX.
- `website/` — the **keepwhatworks.com marketing site**: hero, featured-product
  card, essay grid, and a long-form essay page.

Each `ui_kits/<product>/` has its own `README.md` describing its screens + components.

---

## How to design with this system

1. **Pick the context.** Product UI (Launchpad/Council) → 1000px column, hero or
   topbar, 22px cards, teal for action only. Marketing → 960px column,
   `> keepwhatworks` brand.
2. **Reach for tokens, not new values.** Use `colors_and_type.css`. If you need a new
   color, derive it in `oklch` near the cool-mist / muted-teal family — never
   a fresh hue, and never green/cream/purple/neon.
3. **Lead with the summary.** Task → winner → agreement → differences. Raw detail subordinate.
4. **Stay quiet.** Reserve emphasis (the one teal accent, a soft left rail) for true
   actions. Let whitespace and the mist/ink/border stack do the work.
