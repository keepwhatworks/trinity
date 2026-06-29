# UI Kit — Launchpad (Calm / Muted Teal)

A high-fidelity, interactive recreation of **Trinity Local's** local product surface —
the Launchpad (council composer + routing + lens) and the Council review page.

> **Viewing.** This kit is React + in-browser Babel, so it must be **served** — over
> `file://` the `.jsx` won't load and the page shows a short "serve this kit" note
> instead. Run `python3 -m http.server` in this directory and open
> `http://localhost:8000`. (The sibling `ui_kits/website/` kit is pure HTML/CSS and
> opens by double-click.)

## ⚠︎ Direction note — this kit intentionally diverges from the shipped brand

Trinity's *shipped* product UI is warm-paper + serif + green (see
`trinity-local/DESIGN.md`). In practice that template reads as the generic
**"Claude / Opus default house style"** (warm cream, serif display, italic accents,
terracotta) — exactly what users now flag as AI-default. This kit re-skins the same
product into a **calm, soothing** language that's both distinct from that trope and
restful to use. Trinity's DNA is preserved — the die mark, the `>` prompt, the
three-model council — but the execution is softened:

| | Shipped brand (Claude-adjacent) | This kit (Calm / Muted Teal) |
|---|---|---|
| Canvas | warm cream `#f5efe3` | cool mist `#eaecef` + soft top highlight |
| Display type | Iowan **serif**, italic accents | **Hanken Grotesk** sans, teal word-accent |
| Accent | forest green `#255847` | **muted teal** `#4f9095` (+ slate quiet) |
| Geometry | 24px pillows, warm shadow | soft 22px cards, gentle cool shadow |
| Labels | uppercase tracked eyebrow | eyebrow with a small teal dash |
| Motion | fades, hover-lift | slow rise, breathing aura, die-settle |
| Feel | editorial warmth | calm, soothing, unhurried |

**Accent switcher.** The bottom-right "Try an accent" control swaps the accent live
(muted teal = default; slate blue, dusty mauve, and sage included for comparison).
The whole UI reads its accent from the `--green*` CSS vars, so one swap reskins
everything. Remove the `<AccentSwitcher>` from `app.jsx` for a production build.

## Screens & interactions (`index.html`)

A click-through, not a storybook:
- **Launchpad** — a calm presence line (provider / lens / router readout), a hero with
  the die mark (tumbles + settles on load), a soft composer, an optional
  cross-bootstrap card (slate rail), personal routing bars, `/me` lens cards
  (copy-to-clipboard), and the "new model" eval card.
- **Launch a council** → it appears in the left rail as *running*, routes to the
  **Council review** page where the three members stream in, the chairman
  synthesizes, and the verdict resolves (winner + lens-pick badge, agreed claims,
  the split with `why_matters`, inline refine field).
- Click any council in the rail to reopen it; "Ask a new council" returns to the composer.

## Files

- `index.html` — app shell + full Calm/Teal stylesheet + React/Babel mounts.
- `launchpad_parts.jsx` — `Die`, `Eyebrow`, `StatusBar`, `Sidebar`, `Hero`,
  `InfoCard`, `RoutingCard`, `NewModelCard`, `LensCards`.
- `council_view.jsx` — `MemberCard`, `CouncilReview`.
- `app.jsx` — `App` shell: view routing, council state, toast, accent switcher.
- `favicon.png` — the die brand mark.

Components export to `window` so each Babel script shares scope.
