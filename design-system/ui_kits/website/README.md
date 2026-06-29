# UI Kit — Website (Calm / Muted Teal)

A high-fidelity recreation of **keepwhatworks.com** — Trinity Local's marketing home
("Notes from the arena") — in the calm/teal design language. Sibling to
`ui_kits/launchpad/`; same tokens, same motion, same brand marks, marketing context.

## ⚠︎ Direction

Matches `colors_and_type.css` and `ui_kits/launchpad/` exactly: cool mist `#eaecef`,
muted-teal accent `#4f9095`, **Hanken Grotesk** sans for everything, soft 22px cards
with visible 1px borders + gentle cool shadow, the `>`-prompt wordmark and the
three-pip die mark. **No green, no cream, no serif, no terracotta, no purple/neon, no
decorative gradients.** The one-word title accent (`works`, `verb`) uses teal, never
italics. Motion is slow and gentle (breathing aura, die settle, staggered rise),
fully gated behind `prefers-reduced-motion`.

## Screens

### `index.html` — the landing
- **Topbar** — `> keepwhatworks` wordmark (mono teal prompt) + the die mark (three
  teal pips, settles on load) + nav (Essays · Trinity · GitHub).
- **Hero** — eyebrow "Notes from the arena" (teal dash), `Keep what works. Throw the
  rest.` (one-word teal accent), the essay-and-tools lede.
- **Featured product card** — left teal rail; "Ask all three. Keep what works."; the real curl install
  line (monospace, select-all); painkiller→moat copy; a 2-col `→` feature list; a
  filled-teal "See how it works" primary + a bordered "View source" ghost.
- **Essay grid** — the five real essays (Architecture / Gravity of Becoming, Design
  the Affordance, The AI-Native Way, Utopia is a Mechanism) with date · meta, title,
  and a one-paragraph gist; the new essay carries a teal "New" tag.
- **Footer** — quiet, hairline-topped.

### `essay.html` — long-form (~680px column)
- **Sub-page topbar** — pill `← Essays` back link + `> keepwhatworks` wordmark + a
  "View source" action.
- **Essay head** — eyebrow `Essay · date`, large sans title, a lede/dek with a teal
  accent word; hairline divider.
- **Body** — 17px reading measure, generous line height, an `h2` section head, a
  **teal-wash blockquote** (left teal rail).
- **Trinity callout block** — a left-teal-rail card mid-essay ("Ask all three. Keep what works." +
  curl line + "See how it works →") tying the writing back to the product.

## Components (reused from the system)
`die` (animated brand mark) · `eyebrow` / `section-eyebrow` (teal dash) · `product-card`
(left rail) · `install` (mono command chip) · `features` (`→` list) · `btn.primary` /
`btn.ghost` · `essay-card` · `callout` · `topbar-back` pill · breathing aura · `rise`
entrance.

## Notes
- Fonts load from Google Fonts (Hanken Grotesk + JetBrains Mono) per the design
  system's `<head>` snippet — these kit files are design artifacts for viewing. The
  **production** site (`trinity-local/docs/`) must self-host the fonts (no CDN at
  runtime — Trinity's "nothing leaves your machine" guarantee is test-enforced).
- Copy is verbatim/voice-faithful from the source `docs/index.html` and the essays;
  the real install command is preserved exactly.
- Brand marks: the die (`favicon.png`, recolored teal in CSS) and the `>` prompt are
  the only "branded" marks — structure is carried by type, the teal eyebrow dash, and
  Unicode arrows (`→ ←`), per the system's icon-free rule.
