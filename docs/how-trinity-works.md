---
class: live
---

# How Trinity works

> **Ask all three. The chairman keeps what works — and over time, what you'd have kept.**

Trinity is a cross-provider council. You send one prompt and Claude, ChatGPT, and Gemini answer in parallel. Then one of them, the chairman, synthesizes the verdict: what they agreed on, where they split, which answer wins. It runs free and local, on the subscriptions you already pay for, no API key, and your transcripts never leave your machine. That's day one, no setup, no warm-up.

Underneath, Trinity is also a cross-provider memory layer. It reads the transcripts you already have on your machine, from Claude Code, Codex CLI, Antigravity, plus claude.ai / chatgpt.com / Gemini web chats and exports, and extracts the pattern in how you rephrase, judge, and decide. (It also runs *inside* Cursor as an MCP server; ingesting Cursor's own chat history is on the roadmap.) Over a handful of councils that pattern becomes a lens, and the chairman starts synthesizing toward the answer you would have picked. The council is the lead; the lens is the layer that personalizes it as your corpus deepens.

This doc walks the pipeline end-to-end. If you want the install path, see [README](../README.md). If you want the on-disk contract, see [`three-tier-architecture.md`](three-tier-architecture.md).

## The category, and why no lab can ship it

Claude can't recommend ChatGPT. OpenAI can't recommend Claude. Google can't recommend either. The three frontier labs are commercially prevented from looking across each other's traffic.

That asymmetry is the whole point. Trinity reads your transcripts from all three locally and learns one model of *you*. Anyone outside the labs can build this layer. No lab can. The architectural ideas (recursive gates, file-based memory, dreaming as out-of-band curation) are reproducible. The cross-provider corpus isn't.

## Where your transcripts come from (all local)

| Source | How Trinity gets to it |
|---|---|
| **Claude Code / Codex / Antigravity** | Each CLI writes session logs under `~/.claude/projects/`, `~/.codex/sessions/`, etc. Trinity walks them directly. |
| **Cursor** | Install target only. Trinity runs *inside* Cursor as an MCP server, but Cursor stores chats in a SQLite `state.vscdb`, not the JSONL these parsers read, so its own history isn't ingested yet (a `state.vscdb` reader is on the roadmap). |
| **claude.ai / chatgpt.com / gemini.google.com** | The Chrome extension hooks the XHR responses on those tabs and posts the conversation JSON to a local Native-Messaging host. Files land in `~/.trinity/conversations/`. No upload, no listening port. |
| **Bulk exports** (Claude.ai webapp export, ChatGPT export zip, Gemini Takeout) | `trinity-local import-export <path>` auto-detects the format and ingests. Claude.ai users: Settings → Privacy → Export data. |
| **Provider-side memory loop** | The agent inside Claude Code / Codex / Cursor sees your full conversation history on its side. The `import_provider_memory` MCP tool lets it pipe extracted tensions or rejections to Trinity directly. No scraping, no re-ingest. |

**Trust boundary on the provider-side loop.** It trusts the agent inside the harness to extract honestly. A compromised agent could inject fabricated tensions or rejections. Mitigations: the same dedup logic catches duplicates, and the ≥3-basin lens-build threshold raises the bar (a poisoning attack needs to span unrelated topics to take). Single-basin signal is exploitable in principle.

Nothing ever leaves the machine. The MCP server, the embedding model (~600 MB, one-time download), the chairman calls, all run on your hardware or your existing CLI subscriptions.

## From files to embeddings (ingest → index → embed)

Each transcript turn becomes a `PromptNode` row in `~/.trinity/prompts/prompt_nodes.jsonl`: id, provider, role, text, parent_id, created_at. Consecutive nodes get paired into `TurnWindow` records (user → assistant pair) so the analyzer can see what the model said, and what you said next.

Cursors in `~/.trinity/prompts/cursors.json` track the newest ingested node per source so re-runs are incremental. You can ingest a 50,000-turn corpus once and then top up each day in seconds.

Every node gets passed through `modernbert-embed-base` when a real embedding runtime is available. Apple Silicon uses the MLX-native path, and other platforms can use the torch / sentence-transformers fallback. The model is cached locally via Hugging Face Hub and Trinity pins offline mode during normal startup. If no real embedder is available, Trinity falls back to a stable SHA-1 TF-IDF projection for lexical surfaces, but semantic flows now abstain instead of pretending TF-IDF geometry is meaningful. This is the only model-shaped call in ingest. No LLM, pure local vector math.

## Dream, the offline synthesis pass

`trinity-local dream` walks six phases:

**Phase 1, Discover.** Find pairs of nodes across providers that are semantically the SAME question (cosine ≥ 0.85). That's how Trinity knows you asked "the same thing" of Claude AND Codex AND Gemini.

**Phase 2, Topics / basins.** k-means over embeddings writes subject basins to `~/.trinity/memories/topics.json`. The default basin count now scales with corpus size instead of staying fixed at 20. Each basin gets a centroid vector and representative verbatim prompts. Basins are the substrate the lens grades against.

**Phase 2.5, Vocabulary.** Vocabulary now keeps the measured signal: recurring anchors and homonyms. The old synonyms section was cut after real-corpus review showed it mostly captured template co-occurrence, not synonymy.

**Phase 3, Rejection signals.** For each TurnWindow where you turned around and asked a follow-up, extract WHAT you changed: REFRAME (different question), REDIRECT (different output shape), COMPRESSION (shorter), SHARPENING (more precise). These rejection signals (stored in `~/.trinity/me/preference_acts.jsonl` with trigger=model_miss) are the empirical signal of your taste. The load-bearing input the chairman trains against.

**Phase 4, Lens-build.** Synthesize the rejections into PAIRED TENSIONS in `~/.trinity/memories/lens.md`. On a working install: things like `mechanism inspection ↔ speculative inference under uncertainty`, `concrete specificity ↔ abstract pattern recognition`. A tension must span ≥3 basins to make the lens. Otherwise it's preserved as "ordering" (topic-local preference) or dropped. That floor is why one weird question can't pollute the lens.

**Phase 5, Distill.** One-paragraph `~/.trinity/core.md`, the identity-level summary the chairman reads first at runtime. Drill-down to the other files happens on demand.

**(Phase 6, Moves substrate, retired 2026-05-27.)** Trinity originally shipped a procedural-memory layer (4-tier Bayesian gate, alpha/beta posteriors, SKILL.md emission). Running it on real data showed the substrate was structurally dormant: the gate's lexical T1 filtered 100% of candidates because lens tensions and basin patterns live at different vocabulary registers. The conceptual fix was simpler than retuning. **The chairman LLM bridges declarative→procedural at inference time** when it reads `lens.md` during synthesis. Pre-computing moves was JIT-cache for a free operation. Substrate deleted in #184 (-4,400 LOC). See [`retired_names.py`](../src/trinity_local/retired_names.py).

### Phase 1.5, Trajectory lens

Stage 0 extracts preferences from **single adjacent turn-pairs**, synchronic signal. The trajectory lens adds **arcs across multiple turns within a thread**, diachronic signal. If you pulled a conversation toward concrete examples three times across ten turns, that's a directional preference Stage 0 can't see.

This shipped in v1.7.55. Detection is deterministic: group model-miss preference acts by originating transcript and record a `TurnArc` when the same rejection kind recurs at least three times in one thread. Aggregated `Trajectory` records land in `~/.trinity/me/arcs.jsonl` and `~/.trinity/me/trajectories.jsonl`. The lens renders a trajectories section when the corpus has enough signal.

## What you end up with

After a few dream cycles, your `~/.trinity/` looks like:

```
~/.trinity/
├── core.md                       ← one paragraph: who you are
├── memories/
│   ├── lens.md                   ← paired tensions: how you decide
│   ├── topics.json               ← subject basins + centroids
│   └── vocabulary.md             ← anchors + homonyms
├── scoreboard/
│   ├── picks.json                ← extracted "claude wins on REFRAME" rules
│   └── routing.json              ← per-task-type win-rate
└── me/
    └── preference_acts.jsonl     ← the empirical training corpus (model_miss + self_expressed acts)
```

The folder is the API. Everything in it is human-readable Markdown or JSON. Trinity disappearing tomorrow leaves your taste capture intact.

## Runtime, using Trinity

You drop a hard question into any harness (Claude Code, Codex, Cursor, Antigravity). The agent there sees Trinity registered as an MCP server and calls one of <!-- canonical:mcp_tool_count -->8<!-- /canonical --> MCP tools. The three that matter most, council first, routing later:

- **`run_council(task)`**, the flagship and the day-one lead. Dispatches the question to Claude + ChatGPT + Gemini in parallel, collects three responses, runs chairman synthesis. The chairman emits a Routing JSON: agreed claims, disagreed claims, winner, why. It reads `core.md` first and drills to `lens.md` only if a lens exists yet, so the council is full-fidelity on minute one. Once a lens has warmed up, the chairman synthesizes toward the answer you would have picked. **The council is the value; the lens makes it yours.**
- **`ask(query)`**, the cheap 90% path that *emerges* once you have a track record of councils. Pulls a high-trust extracted pick if one exists ("looks like a COMPRESSION task → codex wins on those for you") and returns a single answer instead of running a full council. With no council history yet it falls back to a heuristic, so routing is the layer that accrues after the council, not a prerequisite for it.
- **`get_persona()`**, hands the lens to the agent at session handshake so it can tailor responses without an MCP round-trip per call.

## The eval surface, score any model against YOUR rejections

When Claude 5 or GPT-5.5 lands, the question isn't "how does it score on MMLU." It's "does this new model handle MY rejections better than the previous one?" Trinity ships the loop:

```
~/.trinity/me/preference_acts.jsonl    (your empirical preferences, trigger=model_miss acts)
              │
              ▼
   trinity-local eval-build            (corpus → eval_set)
              │
              ▼
   ~/.trinity/evals/<set_id>/
              │
              ▼
   trinity-local eval-run --target <new-model>
              │
              ▼
   per-axis scores: REFRAME, REDIRECT, COMPRESSION, SHARPENING
              │
              ▼
   trinity-local eval-show --compare    (leaderboard)
```

The eval is **structurally asymmetric** in your favor. Only Trinity sees your cross-provider rejection signal. A public benchmark can rank models on average user behavior. Trinity's eval ranks them on *you*. The same architecture that makes the corpus impossible for a single lab to reproduce also makes the eval personal.

## When does dream run

**Today: manual.** You run `trinity-local dream` when you want a refresh. The launchpad surfaces a "lens is stale" indicator when the rejection-corpus delta vs the last dream crosses a threshold.

**Roadmap: auto-trigger.** Mirroring Anthropic's Auto-Dream cadence (24h + 5-sessions) plus a rejection-corpus-delta trigger. Trinity and Auto-Dream share the same primitive (offline synthesis from session transcripts) but differ in scope: Trinity is cross-provider, Auto-Dream is single-provider. Same idea, asymmetric data access.

## Schema versioning, today and tomorrow

**Today:** schema versioning is live. `~/.trinity/.trinity-version` stores the recorded schema version, and `run_migrations()` executes at CLI / MCP startup. The first migration backfilled the unified `preference_acts.jsonl` ledger from legacy split stores. Future migrations follow the same forward-only pattern and leave the marker at the last successful version if a migration fails.

## The closed loop, how Trinity gets better

The chairman reads your `lens.md` during every synthesis. Each council emits a `routing_label` (winner, agreed/disagreed claims, why). Those outcomes feed the per-basin **routing** tally: `cortex consolidate` places each council into its nearest lens basin and records the recency-weighted winner in `picks.json`, so the next `ask` can route a similar question on that basin's track record. The lens *itself* refreshes separately. Each dream re-reads new turn-pair signal from your raw prompts (not from how councils turned out; that council→lens re-extraction edge isn't wired). The lens isn't static. It's the slow-changing layer your fast-changing conversations refine.

The taste you build with Trinity is the persistent thing across model generations. When the next frontier model ships, your lens still grades it. That's the layer this thing is.

---

**Related reading:**

- [README](../README.md), install + the 30-second pitch
- [`three-tier-architecture.md`](three-tier-architecture.md), on-disk contract (MCP server / pip engine / Chrome extension)
- [`PREFERENCE_CORPUS_SPEC.md`](PREFERENCE_CORPUS_SPEC.md), historical v2 substrate draft; the live survivor is the unified `preference_acts.jsonl` eval/lens corpus
