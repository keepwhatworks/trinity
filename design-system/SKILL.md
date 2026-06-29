---
name: trinity-local-design
description: Use this skill to generate well-branded interfaces and assets for Trinity Local (and its marketing home keepwhatworks.com), either for production or throwaway prototypes/mocks/etc. Trinity is a local-first AI-council product; this system styles it in a calm, soothing direction — cool mist neutrals, a soft humanist sans (Hanken Grotesk), and one muted-teal accent. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the `README.md` file within this skill, and explore the other available files
(`colors_and_type.css` for tokens, `assets/` for the brand mark, `ui_kits/` for
high-fidelity component recreations, `preview/` for the design-system cards).

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets
out and create static HTML files for the user to view. If working on production code,
copy assets and read the rules here to become an expert in designing with this brand.

**Active theme — Calm / Muted Teal** (this is THE direction; do not revert to the
warm-cream/serif look the product currently ships):
- **Cool mist neutrals, one teal accent.** Base `#eaecef`, surface `#fafbfc`,
  border `#dde1e6`, cool-charcoal ink `#2f363c`. Muted teal `#4f9095` (deep
  `#34666b`, fill `#3f777c`, soft wash `#e1edec`) is the ONLY accent; soft slate
  `#94a3ad` is a quiet secondary. **No green, no cream, no serif, no terracotta,
  no purple/neon, no decorative gradients.**
- **One soft humanist sans** — **Hanken Grotesk** for everything (titles weight 600,
  body, UI). A one-word title accent uses teal, never italics. Mono (JetBrains Mono)
  only for commands/paths.
- **Soothing, document-first.** Calm centered column, soft 22px rounded cards with
  visible 1px borders + gentle cool shadow, airy spacing, summary-first layouts.
  Slow gentle motion only (entrance rise, a faint breathing aura, the die settling in);
  respect `prefers-reduced-motion`.
- **Voice:** grounded, plain-spoken, second person, sentence case, em-dashes,
  UPPERCASE tracked eyebrows. Emoji almost never.

The Launchpad UI kit (`ui_kits/launchpad/`) is the reference implementation — match it.

If the user invokes this skill without any other guidance, ask them what they want to
build or design, ask some clarifying questions, and act as an expert designer who
outputs HTML artifacts _or_ production code, depending on the need.
