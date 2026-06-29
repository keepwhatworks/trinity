---
class: aspirational
---

# Trinity Local — Product Spec

> **Trajectory note (2026-05-19):** v1.0 shipped May 13–15, 2026 with the council-centric
> mechanic + Routing JSON ledger described below. **v1.5 shipped June 3, 2026** with
> the MCP-primary routing surface (`ask` cheap default + `run_council` for hard
> questions) + hippocampus + cortex two-tier memory + local model dispatch + rate-
> limit dodge. The trained-coordinator v2 path is **sunset**.

## The One-Liner

> *v1.7 (current):* *"Ask all three — Claude, ChatGPT, and Gemini answer one prompt in parallel; the chairman keeps what works and shows where they split. Free and local. Over time it learns your taste."*
>
> *v1.0 (shipped May 13–15, 2026):* *"Trinity asks all your AIs at once, tells you when they agree, and remembers which one you actually trusted."*
>
> *v1.5 (shipped June 3):* *"Trinity is what Claude Code reaches for when it needs a different provider, a fresh perspective, or a way to dodge a rate limit. Learns at two levels — specific past decisions (hippocampus) and extracted patterns across them (cortex). Gets sharper, not slower, with use."*

Trinity **convenes Claude, ChatGPT, and Gemini in a structured council** when you need the strongest answer — the chairman synthesizes the verdict and surfaces where they split. Underneath, it's a **routing substrate** that learns which model wins your kind of work and runs the cheap single-call path once it has a track record.

**The point is trust calibration, not cost optimization.** Cost savings become a real claim with local model dispatch — *which shipped as of 2026-05-20*: any Ollama / MLX model the user has pulled appears in the routing pool automatically as `ollama:<model>` / `mlx:<model>` via `local_models.py`'s auto-discovery probe. The consumer-visible primitive remains *"these models agreed on these claims, disagreed on these, here's why the disagreement matters."* The cost-savings narrative is now claimable post-2026-05-20, pending HN-demo-day resilience verification.

**Product hierarchy** (consumer pitch order):

1. **Trust layer** (the value) — *"models agreed on X, disagreed on Y, here's why."*
2. **Verifier** (the engine) — chairman synthesis emitting structured Routing JSON.
3. **Personal preference graph** (the differentiator) — every council's chairman pick (lens-governed, so it already reflects the user's taste) aggregates on demand into a `task_type → strongest provider` table. No durable state file; the council outcomes directory IS canonical. Post task #134 (rating retirement 2026-05-22) `compute_personal_routing_table()` aggregates from `routing_label.winner` directly — no user-rating step.
4. **Router** (the implementation) — `route()` / chain mode / cost optimization.
5. **Closed loop with state and replay** — the v2 product: agents that don't drift over long horizons because Trinity retains state and can replay failures.

The router is the implementation. The verifier is the value. The personal preference graph is the differentiator. The closed loop with state and replay is the product that doesn't exist yet.

**Reading list (research context, not current shipped identity):** RouteLLM (router baseline), LLM-Blender (generator-verifier asymmetry), Conductor / Fugu (recursive self-orchestration). These are the research patterns Trinity Local's *evidence ledger* generates supervision signal for — when a learned controller is eventually trained against the ledger (Phase 9 future work; not in this repo), the ledger is the right shape to feed it. **Today's repo ships the ledger and the synthesizer, not the controller.**

---

## Architectural Philosophy

### Load-bearing commitments (non-negotiable)

1. **No LLM calls outside councils.** Ingest, embedding, theme assignment, search ranking, and clustering use pure heuristics + metadata. The only LLM invocations Trinity makes are council member calls and chairman synthesis — both ride the user's existing CLI subscriptions.
2. **Prompt content never uploads.** Even with future opt-in aggregation, only categorical routing labels (`task_type`, `provider_scores`, `winner`) leave the machine. `lens` content is treated as sensitive derived prompt content; it stays local by default.
3. **Local-first inference.** Phase 9's learned router runs on the user's hardware. No hosted controller, no per-call API billing.
4. **Consumer subscriptions as cost basis.** Trinity dispatches via the user's CLI subscriptions (Claude Code, Codex CLI, Antigravity). If anyone proposes a hosted API tier, push back hard — that destroys both the cost basis and the privacy commitment. (Cowork — Anthropic Managed Agents — is an *ingest source* via `parse_cowork_session`, not a dispatch target; the live `config.json` providers dict is `claude / codex / antigravity / mlx`.)
5. **Hosted components may only be registries, not controllers.** Trinity may host public registry metadata, public cold-start priors, and curated public/famous persona documents. It must not host ordinary users' private `/lens.md`, raw prompts, transcript derivatives, per-call inference, or a routing controller.

### What runs and when

- **MCP server** (`trinity-local --mcp`): a stdio child of whatever harness launched it (Claude Code, Codex, Antigravity). Lives only while the harness is connected; dies on EOF. ~62MB resident, no embedding model loaded eagerly.
- **CLI** (`trinity-local <command>`): one-shot subprocess. No persistent state.
- **Council launches**: spawned as background subprocesses by `council-launch`. They run member providers in parallel, write outcomes to `~/.trinity/council_outcomes/`, and exit.
- **`lens-build`**: cron-friendly. Runs once on demand or on a schedule (typical: daily). Loads the nomic embedding model (~22s ramp), samples ~80 diverse prompts via embedding-MMR, fires one chairman call, writes `~/.trinity/memories/lens.md`. The only place embeddings are loaded on the product path.

The launchpad and search/autofill paths are **embedding-free** (substring + recency + replay-value heuristics). Cold-start is <300ms; warm queries are <200ms across 5000 cached PromptNodes.

### Only surface what a single provider cannot

A single CLI already tells you what it thinks. Trinity's value is the **delta**:

- Provider A gave a different answer than Provider B (council `agreed_claims` / `disagreed_claims`)
- You keep switching from A to B for this type of task (personal routing table)
- You've done this exact workflow N times across N tools — it should be automated
- Provider B would have been faster and cheaper for this task kind (latency-aware routing)
- This task kind looks different in your taste than in the population (chairman reads `lens`)

If a single CLI can already surface the information, Trinity should not duplicate it.

---

## What Trinity Uniquely Solves

These are problems that **require multi-provider observation** and cannot be solved by any individual CLI, no matter how good it gets:

### 1. Personalized Trust Calibration Through Council

> "These models agreed on these claims, disagreed on these, here's why the
> disagreement matters — and which answer fits *you*."

When a task matters, you want more than one opinion. Trinity runs the same prompt through multiple providers and a chairman synthesizes a structured Routing JSON: structured `agreed_claims`, `disagreed_claims` (with `why_matters`), `winner`, `runner_up`, `routing_lesson`, and `user_likely_values` derived from your `lens` profile.

The chairman is loaded with `~/.trinity/memories/lens.md`, so it doesn't pick the universally best answer — it picks the answer that fits *this* user. Members generate broad; chairman condenses through your taste.

Each council run produces `(prompt, response_A, response_B, response_C, chairman_synthesis, routing_label, your_verdict)`. The Routing JSON is the supervision signal that feeds the personal routing table and, eventually, the Phase 9 learned controller.

**No single provider can do this.** Each provider only sees its own output. Only your `lens` knows which answers actually match your taste (the chairman's pick on each council, lens-governed, IS the verdict — task #134 retired the separate user-rating step).

### 2. Personal Routing Table (the differentiator)

> "For your code-refactor prompts, codex wins 7.8/10. For research synthesis, claude wins 8.1. For data-pipeline prompts the split is closer — your routing table shows which model the chairman picks where, accumulated across your councils."

Aggregation by `task_type` is computed on demand by walking `~/.trinity/council_outcomes/*.json` — no separate state file, can't drift. Every council the user runs adds a row; the table sharpens organically. The launchpad surfaces the table; `chairman_picker` reads it before every council. Manual `--primary-provider` overrides everything. (The `replay-history` CLI verb that backfilled this table from existing outcomes was retired 2026-05-27 — see `retired_names.py`; normal council usage populates it now.)

**No frontier provider can replicate this** because none of them see the cross-model preference data.

### 3. Behavioral Routing Intelligence

> "You tried this in Claude and switched to Codex. That's the 4th time."

Trinity watches transcript histories across all tools. When it detects a pattern — repeated switches, abandoned sessions, tasks that always end up in a particular provider — it learns which tool is actually best for each task shape.

**No single provider tracks your cross-tool behavior.**

### 4. Latency-Aware Routing

> "GPT-5.5 xhigh wins on quality but takes 30+ seconds. Claude Opus 4.8 is 90% as good in 3 seconds for this task. You said you wanted fast."

External benchmarks (artificialanalysis.ai's Intelligence/Coding/Agentic indices) capture quality but ignore latency. Trinity records per-provider latency and routes against your `(budget, latency)` constraints — the most underrated lever once gpt-5.5 xhigh becomes the default-quality winner.

**No single provider benchmarks itself against competitors on your hardware.**

### 5. Persona-Conditioned Synthesis

> "Two senior engineers could reasonably disagree about this — and you've already made similar architectural choices three times. Here's what fits you."

The chairman reads `lens` (composed by `lens-build` from sampled diverse prompts) and conditions every synthesis on your demonstrated taste, vocabulary, implicit rejections, and abstract lenses. The persona is built locally, never uploads.

---

## Architecture Today

### CLI dispatcher

`src/trinity_local/main.py` is a thin dispatcher. Command modules live under `commands/`. Selected commands shown below — the complete live surface (<!-- canonical:cli_command_count -->57<!-- /canonical --> subcommands across <!-- canonical:command_module_count -->23<!-- /canonical --> command modules) is enumerated by `trinity-local --help`.

| Module | Key commands |
|--------|-------------|
| `commands/council.py` | `council-start`, `council-launch`, `council-stop`, `council-share`, `council-iterate` (former `council-html` retired 2026-05-17 with the verifier→synthesis rename; `council-rate` retired 2026-05-22 alongside the rest of the rating UX — chairman pick is the auto-recorded supervision signal now) |
| `commands/me.py` | `lens-build` (chairman-driven), `lens-show` |
| ~~`commands/seed.py`~~ | ~~`seed-from-taste-terminal`~~ — retired 2026-05-27 (required a personal-rig directory layout no end user has; `import-export` auto-detects type at any path; helpers consolidated into `src/trinity_local/ingest_helpers.py`) |
| `commands/import_export.py` | `import-export` (auto-detect + bulk-ingest ChatGPT / Claude.ai / Gemini Takeout from any path, task #148) |
| ~~`commands/replay.py`~~ | ~~`replay-history`~~ — retired 2026-05-27 (routing table now populates from normal council usage; see `retired_names.py`) |
| `commands/portal.py` | `portal-html` (regenerates the launchpad), `open-review`, `review-link`, `serve` |
| `commands/install.py` | `install-mcp`, `install-hooks`, `install-extension`, `install-launcher`, `uninstall` |

### MCP — canonical 3 + v1.5 trio + `import_provider_memory`

`src/trinity_local/mcp_server.py` exposes these tools to any MCP-compatible harness (Claude Code, Codex CLI, Antigravity, Cursor). Canonical 3; v1.5 adds two — `ask` for cheap single-call routing, `get_picks` for agent-facing introspection; in-protocol provider loop adds `import_provider_memory`; the lens-lift adds `lens_generators`; the in-session eval adds `run_eval` — for 8 total. Plus MCP Resources at session handshake exposing the lens hierarchy + scoreboards as readable URIs (`trinity://memories/lens.md` etc.). (`get_eval_summary` shipped post-#122 then retired 2026-05-18 in commit `6a7f176`. `record_outcome` retired 2026-05-21 per "we are sunsetting user ratings" — chairman's pick is the supervision signal now. `handoff` retired 2026-05-26 — 0 production usage; lens flows via MCP Resources. `mark_pick_wrong` retired 2026-06-05 — the user-pick/veto layer was removed; the chairman's pick stands alone. `route` removed 2026-06-08 in the loop-primitive surface cut — `ask(mode="route")` is the one routing entrypoint.)

1. **`ask(query, available_providers?, thread_id?, top_k?, mode?)`** *(v1.5)* — kNN + cortex match → single dispatched call → concise structured return `{answer, routed_to, trust_score, latency_ms, escalate_hint?}`. The 90%-of-consults tool. Returns `escalate_hint=run_council` when trust < 0.55 so the calling agent can choose to fan out instead. `mode="route"` returns the routing decision only (no model call) — the one routing entrypoint after the standalone `route` tool was cut 2026-06-08.
2. **`run_council(task, members, mode, sequence, primary_provider, responses)`** — spawns a council; runs members in parallel (or chain) then chairman. **When `responses=[...]` is provided**, skips dispatch and runs chairman synthesis only — the structured verdict path. Subsumes the former `judge` tool.
3. **`get_persona()`** — returns `~/.trinity/memories/lens.md`.
4. **`get_council_status(council_run_id)`** — in-protocol polling for async councils.
5. **`get_picks(basin_id?, min_trust?)`** *(v1.5)* — returns the per-lens-basin routing tally (winner, count, margin (the confidence proxy), n_episodes, evidence). Post-#298 collapse the cortex IS this flat lens-basin tally — no separate trust-band/centroid engine — so this lets a calling agent reason about *why* `ask` would route a given basin to a given provider. `min_trust` filters on `margin`.
6. **`import_provider_memory(kind, payload, provider?, dry_run?)`** *(in-protocol provider loop)* — the agent inside Claude Code / Cursor has the user's conversation history on its side; this tool lets it pipe extracted lens tensions (kind=`lens`) or REFRAME/REDIRECT/SHARPENING/COMPRESSION rejection signals (kind=`eval`) straight into `~/.trinity/me/` without copy-paste. Same dedup/append-only semantics as the CLI mirrors (`lens-import`, `eval-import`).
7. **`lens_generators()`** *(lens-lift)* — runs the generators pass in-session and returns the user's cross-domain GENERATING invariants (the deep preferences the task-level lens tensions are projections of). Writes `~/.trinity/memories/generators.md`. CLI mirror: `lens-generators`.

(`record_outcome` was tool #4 until 2026-05-21 — retired alongside the rest of the rating UX. Chairman's `routing_label.winner` IS the supervision signal now, fed into `compute_personal_routing_table()` automatically.)

### State layout

```
~/.trinity/
├── todos/                          # Durable todo records (renamed from tasks/)
├── council_outcomes/               # Council outcome JSON (structured routing_label + chain_steps) — CANONICAL store
├── reviews/                        # Post-hoc review JSON
├── review_pages/                   # Static HTML review pages
├── portal_pages/                   # Static launchpad surface (always file://)
│   ├── launchpad.html              #   The launchpad
│   └── status/                     #   Live council progress (JSON + JS) for polling
├── settings/                       # Telemetry settings
├── prompts/                        # Raw prompt index (renamed from memory/)
│   ├── prompt_nodes.jsonl          #   PromptNode index (atomic retrieval unit)
│   ├── turn_windows.jsonl          #   TurnWindow index (local context)
│   └── cursors.json                #   Per-source ingest cursors
├── memories/
│   └── lens.md                     # Built by `lens-build` via chairman call
├── analytics/
│   └── routing_label_events.jsonl  # Chairman parse-success rate
├── outcomes.jsonl                  # Per-session outcome records (drift)
└── council_runs.jsonl              # Council outcome log
```

The **personal routing table is computed on demand** from `council_outcomes/*.json` — no separate state file. Cached in-process by directory mtime.

### What was deliberately deleted from earlier specs

Each was on a previous version of this spec; each was cut to keep the surface honest:

- **Peer review (anonymous member-on-member ranking)** — replaced by chairman Routing JSON. Doubled latency for empty-equivalent data.
- **TranscriptNode tier** — its only call site was the search re-ranker; PromptNode + TurnWindow already covered the same retrieval surface.
- **`personal_routing_table.json` durable state** — computed on demand from `council_outcomes/`.
- **`global_benchmarks.py` separate path** — read-time blend shipped per Tier 2 #7 (task #52, 2026-05-12). The file structure stayed split (no merge into one module) but `ranker/chairman_picker.py:_blended_pick` reads both `global_benchmarks` + `personal_routing_table` and sigmoid-blends them at decision time — the only consumer that matters. The structural separation has zero behavioral cost.
- **`prompt_shape.py` standalone module** — folded into `ranker/heuristic.py` (`prompt_calls_for_council` lives there now).
- **`judge` MCP tool** — collapsed into `run_council(responses=[...])`. One fewer public surface.
- **Embedding-powered search on the hot path** — replaced with substring + recency + replay-value heuristics. Search cold-start dropped from 22s to ~300ms.
- **Daemon / always-resident server** — the MCP server is a stdio child of whatever harness launched it; dies on EOF. No background process, no launchd, no systemd.
- **`run` command** (Thinker/Worker/Verifier loop) — replaced by council; the user's CLI is the agent loop.
- **Cost aggregation per provider** — flat-rate subscriptions = roughly fixed per-task cost; latency is the actual scarce resource.
- **`~/.taste/` dependency** — `lens-build` now builds /me from sampled PromptNodes via a chairman call. taste-terminal is no longer a runtime dependency.

---

## What Trinity Is NOT

- **Not a CLI wrapper.** Trinity does not replace `claude`, `gemini`, or `codex`. It does not own the terminal session.
- **Not an orchestrator.** The council is a one-shot multi-provider comparison; chain mode is sequential refinement of one task. Long-horizon orchestration belongs to the harness's agent loop.
- **Not a server.** No `localhost:8080`. No WebSocket. No database. The MCP stdio server is a child process of the harness, not an independent service.
- **Not an always-on daemon.** Nothing runs when no harness is connected.
- **Not a hosted /me warehouse.** The opt-in registry will store metadata + curated public personas only — never private `/lens.md`.

---

## What's working today (v1.1)

1. **Memory index live.** `trinity-local import-export <path>` auto-detects ChatGPT / Claude.ai / Gemini Takeout exports at any path and populates `~/.trinity/prompts/`. 768d nomic embeddings written at ingest, used by `lens-build` only. (The earlier personal-rig `seed-from-taste-terminal` verb retired 2026-05-27; engine lives in `ingest_helpers.py` now.)
2. **Embedding-free hot path.** Launchpad autofill + MCP `ask` k-NN lookup. Substring + recency + replay-value heuristics. ~150ms warm query over 5000 cached recent PromptNodes. (`search_prompts` MCP tool was retired 2026-05-17 — the hot path now lives inside `ask`. `replay-history` retired 2026-05-27 — routing table populates from normal council usage.)
3. **Personal routing table (on-demand).** Computed by walking `council_outcomes/*.json`, mtime-cached. Read by chairman_picker + launchpad.
4. **Chairman auto-selection.** `predict_strongest_chairman(task)` consults personal table → global priors → default order.
5. **Structured chairman output.** Every council emits Routing JSON with `agreed_claims`, `disagreed_claims` (with `why_matters`), `winner`, `runner_up`, `provider_scores`, `routing_lesson`, `eval_seed`.
6. **Chain mode.** `run_council(mode="chain", sequence=[...])` runs sequential refinement; chain steps persisted on `CouncilOutcome.chain_steps`.
7. **MCP tool surface (canonical 3 + v1.5 pair + `import_provider_memory`).** Canonical: `run_council` (subsumes `judge`), `get_persona`, `get_council_status`. v1.5: `ask` (cheap single-call default; `mode="route"` is the one routing entrypoint after the standalone `route` tool was cut), `get_picks` (agent-facing introspection). In-protocol provider loop: `import_provider_memory` (the agent pipes its own extracted signals into Trinity). Lens-lift: `lens_generators` (the cross-domain "lift"). In-session eval: `run_eval` (score a model against the user's taste so the judge rides MCP sampling, not `claude -p`). 8 total. Plus MCP Resources at session handshake. (`get_eval_summary` shipped post-#122 then retired 2026-05-18 in the simplification pass, commit `6a7f176`; `record_outcome` retired 2026-05-21 alongside the rest of the rating UX — chairman's pick is the supervision signal now. `handoff` retired 2026-05-26 — 0 production usage; lens flows via MCP Resources. `mark_pick_wrong` retired 2026-06-05 — the user-pick/veto layer was removed; the chairman's pick stands alone.)
8. **`lens-build` IS a council.** Embedding-MMR sampling picks ~80 quality-weighted diverse prompts; chairman synthesizes 5-section `/lens.md` (recurring topics, vocabulary, implicit rejections, cross-domain analogies, abstract lenses).
9. **Streaming live council page + cherry-pick chips.** Member responses render full markdown as soon as their status flips to `done`, while chairman is still synthesizing. Refinement directive ("↳ <text>") surfaces in the eyebrow row for any round carrying `outcome.metadata.user_refinement`. Each completed member card carries a `Quote ↓` chip that appends `> [Provider]: <text>` to the refinement input — multi-quote stacks, the user types the merge directive on top; chairman still does synthesis, only the input UX changes. Solves the hand-rolled cherry-pick flow observed on `bundle_42f8cea9c9e705e5` (Gemini's "Own your context" merged with Claude's response).
10. **Lens-derived routing (the `cortex collapse`, #298).** `trinity-local consolidate` is a cheap, deterministic, LLM-FREE pass: it places each real-contest council into its nearest **lens** basin (topics.json's live 768-d centroids, rebuilt daily with the lens) and tallies the recency-weighted chairman winner per basin → writes `~/.trinity/scoreboard/picks.json` as a flat `{basin_id: {winner, count, margin, n_episodes, evidence}}`. `ask`/`route` place an incoming query through the SAME lens centroids and route (via `ask(mode="route")`) on the basin's winner, gating on a match floor (out-of-domain → kNN), a margin floor (near-tie → kNN), and a min-count (thin tally → kNN); the margin is the confidence proxy. This REPLACED the old v1.5 cortex engine — the flagship-LLM rule extractor, the 6-component `trust_score`, the chairman `--audit` pass, the structured geometric prior, and the **separate** cortex `basin_centroid`. Because the only centroids are the lens's (always in the live embedding space), the #277 stale-orthogonal-space failure that made the old cortex routing go silently inert is structurally impossible. (The geometric primitives — Weiszfeld median, participation ratio, excess kurtosis — survive in `cortex_geometry.py` for the PCA tooling, just no longer in the routing path.)
11. **Test suite green.** Cortex consolidation (geometric median + coherence + bimodality + bimodal cortex fall-through + chairman-audit-mode); cortex math factored into `cortex_geometry.py` (dependency-free stdlib) with direct property-based regression tests in `test_cortex_geometry.py` covering Weiszfeld L1-outlier robustness, mean-cosine identity/antipodal, participation-ratio dimensionality, and excess-kurtosis bimodality thresholds. `ask` orchestration + MCP handler, tool-triggered incremental ingest, HF offline pinning, chairman-pick personal routing table (no user-verdict blend — the user-pick layer was retired 2026-06-05), sigmoid-blended chairman picker (cold start → personalization), launchpad personalization-% + Health columns. Plus the v1.0 base. <!-- canonical:smoke_surface_count -->35<!-- /canonical -->-surface browser smoke gate green (includes the memory viewer surfaces 14a/14b — chip-link presence + click-through render — the memory-health drift-surfacing row at Surface 15, the per-file health banner that travels into the viewer at Surface 16, the persistent rebuild chip in viewer headers at Surface 18, the topic-graph launch-council chip at Surface 19, the per-representative replay chip at Surface 20, the topology→picks centroid-matched cross-link at Surface 21, the pick-basin SVG node styling at Surface 22, the picks→topology cross-link with ?basin= deep-link handling at Surface 23, the routing→topology chip that completes the routing/picks/topology cross-link triangle at Surface 24, the launchpad recent-card → topology chip at Surface 25, the cortex picks card → topology chip at Surface 26, the lens card → basins_spanned chips at Surface 27, and the stale-basin banner for `?basin=` mismatches at Surface 28).

---

## What's deferred to v1.2+

- **Tier 2 #7 — merge global_benchmarks + personal table into one read-time blend.** Currently two launchpad cards; collapse to one with sigmoid-alpha confidence as personal `n` grows.
- **Aggregation endpoint (§8.9).** Cloudflare Worker for live global priors + public leaderboard. Read access free for all; upload opt-in only with anonymous categorical labels (no prompt content). Ship after Routing JSON parse-success ≥85% sustained and ≥50 opt-in users.
- **Profile-DB / shared personas (v1.2).** Architecture D — opt-in registry of metadata + curated famous-person personas built from public sources. **Never** hosts ordinary user `/lens.md`. (Originally cited a council outcome that doesn't survive in any artifact; citation dropped per the phantom-citation sweep — claim itself is unchanged.)
- **Harness auto-invocation.** `route()` returns `should_auto_council: bool` based on 5 trigger conditions (irreversible state, multi-architecture choice, load-bearing commitment edits, low confidence, user-override history). Rate-limited at 1 per 10 user turns / 1 per 20 min.
- **Tool-triggered cursor-based incremental ingest.** Cursors live at `~/.trinity/prompts/cursors.json`; fires on MCP tool invocation rather than relying on one-off bulk imports. (Originally this entry contrasted against `seed-from-taste-terminal`, retired 2026-05-27 — same idea, replacement is `import-export`.)
- **Phase 9 learned tiny coordinator.** v1 collects the personal data; Phase 9 trains a per-user adapter against it.

---

## Who it's for and how it spreads

### Target user

**Power users who already use 2+ AI coding CLIs daily.** They:

- Have Claude Code, Codex CLI, and/or Antigravity installed
- Switch between tools based on gut feeling
- Don't have data on which tool is actually better for what
- Lose time re-trying tasks in a second tool when the first fails
- Can install a Python package and run a terminal command

### Positioning

> "Your AI tools don't talk to each other. Trinity watches all of them and tells you things none of them can."

### Launch — council-first

The blog post writes itself:

> "I ran 50 of my favorite prompts through 3 AI coding tools. The chairman synthesized structured verdicts personalised to my `lens`. The rankings surprised me. Here's the data — run it on your own prompts."

Why this order:

1. **Council is generative.** It produces a new artifact (cross-provider verdict) that didn't exist before.
2. **Council extracts constitutional data.** Every run feeds Routing JSON into the local evidence ledger; the personal routing table aggregates on demand.
3. **Council is the proof.** Multi-provider chairman synthesis with structured Routing JSON (agreed_claims / disagreed_claims with why_matters) already shows the multi-provider idea working.
4. **Pair-wise `lens` cards are the shareable social object.** Pair-wise principles distilled from your prompt history (title + why-it-matters per implicit rejection); copyable to socials with one click. Verbatim prompts stay local — only the principle ships.

### Distribution

1. **Cowork-style desktop launch for non-coders** — the durable acquisition
   surface is a real desktop app with an app icon, menu bar entry, hotkey, and
   setup/status UI. The CLI remains the engine, not the required daily launch
   gesture. The Chrome extension is the v1 bridge for browser capture and
   launchpad dispatch while the desktop cockpit is built.
2. **GitHub repo** — `curl -fsSL .../install.sh | bash` clones the repo into `~/.trinity/code/` (with a back-compat symlink at `~/.claude/skills/trinity/` for users on the pre-2026-05-19 path) and wires everything. Contributors `pip install -e .` from a clone for the editable dev path.
3. **MCP-default install** — `trinity-local install-mcp` registers Trinity with Claude Code's MCP host. Codex CLI / Antigravity follow the same pattern.
4. **Word of mouth** — the multi-CLI power-user community is small and tight.
5. **Blog post** — "I spent a month tracking which AI coding tool is actually best for what. Here's the data."

### Launch arc (v1.0 → v1.1): distribution beats elegance

Five workstreams that build on each other while consumer AI is still
taking shape (see tasks #114–118):

1. **MCP-dropdown distribution** — submit Trinity to the curated MCP
   server registries for Claude Desktop, Codex CLI, Cursor, Cline,
   Continue. Being in the dropdown beats being technically perfect.
2. **First-run wow — cross-provider continuity** (reframed 2026-05-14;
   delivery path updated 2026-05-26): the 60-second demo is "ask
   Claude a complex question, switch to Antigravity, watch the lens
   carry the context through MCP Resources." ONE answer that visibly
   knew the prior context — the "wait, how did it know?" moment IS
   the demo working. Structurally non-refutable: only Trinity has the
   cross-provider prompt index. Antigravity is especially strong
   because it ALSO brings Google data (Gmail/Drive/Calendar)
   Claude/GPT can't see. (The earlier `handoff` CLI + MCP tool were
   retired 2026-05-26 after 0 production usage; agents now pick up
   the lens via `trinity://memories/lens.md` at session handshake,
   same beat without the explicit verb.) Council depth is the
   *quality engine*, not the hook.
3. **Cross-provider benchmarks** — publish Trinity vs. Opus on design,
   vs. GPT-5.5 on coding, vs. Gemini on long-context. Even modest wins
   are structurally non-refutable; only Trinity can run cross-provider
   councils, so only Trinity can publish the comparison.
4. **Schema standardization** — if Aider, Cline, Continue adopt the
   same `~/.trinity/` preference-corpus schema, Trinity becomes a
   standard not a product. Standards outlive products ~10×. Publish
   a JSON Schema for `council_outcomes/*.json` + `memories/*` early,
   while there's still room to shape what the format looks like.
5. **Your taste signal stays yours** — tell users explicitly: the
   preference corpus they build today keeps its value over time and
   stays on their machine. True and worth saying plainly.

Distribution correction (2026-05-14, revised 2026-05-19): for non-coders,
Trinity must launch like a desktop app, Cowork-style: app icon, menu bar entry,
hotkey, and setup/status UI over the same local engine. The Chrome extension is
the v1 bridge for capture and launchpad dispatch, not the long-term cockpit.
Mobile starts as a review-link companion: open the web/deep review link, rate the
winner, mark bad picks, and feed the same ledger through the paired desktop.

The cron loop should prefer ticks that advance one of these workstreams
over internal refactoring during the launch phase.

### Telemetry model

Public benchmarking should use an **opt-in summary-sharing model**, not raw transcript upload:

- Consent during install (default off)
- Editable later from launchpad settings
- Categorical routing labels only (`task_type`, `provider_scores`, `winner`, `routing_lesson`)
- No raw prompts, outputs, file paths, or repo contents — ever

See [telemetry-spec.md](telemetry-spec.md) for the event schema and upload cadence.

---

## Simplicity principles

1. **One file = one entity.** Tasks, actions, bundles, and outcomes are each a single JSON file. No joins. No foreign keys. No schema migrations.
2. **Append-only logs for history.** JSONL files for runs, launches, and council outcomes. Never rewrite history.
3. **Static HTML for UI.** The launchpad is regenerated from file state. No React. No build step. No WebSocket. Open in any browser.
4. **Filesystem is the index.** Computed views (e.g., the personal routing table) walk canonical directories on demand and cache by mtime. No durable secondary state files for derived data.
5. **CLI for power users.** Every operation is a `trinity-local <subcommand>`.
   The desktop app invokes the same commands for non-coders; it does not fork a
   separate product core. The launchpad has minimal in-page settings (telemetry
   sharing toggle, anonymous-id reset, ingest controls, auto-chain
   enable/disable) — no full configuration app.
6. **Minimal dependencies.** Python stdlib + numpy. Optional `[mlx]` extras for embedding writes during seed and embedding-MMR sampling during `lens-build`.

---

## Roadmap

This file stays product-spec-shaped: positioning, who it's for, what's in/out of v1.

The destination matches the convergence of Lottery Ticket → HRM → TRM: a small learned controller (~10K params) over frontier models, with a verifier head, that recursively refines via chain mode. The chairman + `lens` primitive Trinity ships today is the supervision-signal generator that feeds Phase 9; chairman synthesis is `lens`-conditioned, so the Phase 9 router learns `(task_text, lens_embedding) → routing_decision` rather than a generic mapping.

## Trinity and the broader pattern

This architecture is isomorphic to earlier work on spatial taste and pattern selection (Kintsugi / IPCo):

| Layer | Trinity (Coding Taste) | IPCo (Spatial Taste) |
|-------|------------------------|----------------------|
| **Observation** | Watcher scans transcripts | Usage telemetry of pattern books |
| **Pairwise judgment** | Council forces cross-provider comparison + `lens`-personalised chairman | Pattern book curator selection |
| **Constitution extraction** | k-NN learns routing from (prompt, response) pairs | SLM learns plan selection from (site, pattern) pairs |
| **Taste licensing** | `/score` endpoint judges code | Design system judges spatial instantiations |

The pattern: **give away the artifact (transcripts/patterns), license the constitution that learned to evaluate artifacts.** This is why Trinity stays free at the user surface. The differentiator is the taste function, not the router or the digest.
