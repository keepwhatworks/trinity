---
class: live
---

# Your lens — what `dream` generates from your prompts

> Long-form companion to the README. The README leads with the fusion
> council; the **lens** is the opt-in upsell — this file says **what's in
> it** and **how to inspect it**.

## Four levels of cognitive shape

`trinity-local dream` synthesizes your prompts into your lens — one
hierarchical artifact the chairman reads top-down on every council. Four
levels, generated bottom-up from your prompt corpus:

| level | file | what's in it | brain analog |
|---|---|---|---|
| identity | `~/.trinity/core.md` | one-paragraph manifesto subsuming the rest | distillation |
| tensions | `~/.trinity/memories/lens.md` | paired tensions you'd reject vs accept | value |
| basins | `~/.trinity/memories/topics.json` | subject clusters + evidence map for lens | semantic |
| language | `~/.trinity/memories/vocabulary.md` | anchors + homonyms | linguistic |

## Scoreboards live alongside (but aren't cognitive memory)

Two files alongside the four cognitive levels are derived from your council
outcomes (the verdicts you log) and feed Trinity's model picker — not the
chairman's identity context:

| file | what's in it |
|---|---|
| `~/.trinity/scoreboard/picks.json` | extracted model-selection rules per task_type |
| `~/.trinity/scoreboard/routing.json` | per-task-type provider track record |

## Inspect your lens

Open the launchpad's lens card; it links to a local viewer at
`~/.trinity/portal_pages/memory.html`. The four cognitive levels render
together as one document:

- `core.md` manifesto at the top
- `lens.md` tensions below it (with `basins_spanned` chips per pair)
- `topics.json` rendered as an Obsidian-style force graph over centroid
  cosine similarity, with each basin's most-representative prompts on click
- `vocabulary.md` anchors + homonyms tables

Scoreboards (`picks.json` / `routing.json`) surface as schema-aware Reader
views on the launchpad's routing card, not in the lens viewer.

All inlined at `portal-html` time — works under `file://`, no server needed.
