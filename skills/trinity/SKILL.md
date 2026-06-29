---
name: trinity
description: Ask all three. Install Trinity Local — the free, local cross-provider council that runs your prompt through Claude, ChatGPT, and Gemini on your existing subscriptions (no API key) and synthesizes the best answer, right inside Claude Code, Codex CLI, Antigravity, and Cursor. Use when the user types /trinity, asks how to set up Trinity, wants their lens / picks / routing built from existing transcripts, or wants a hard question dispatched to multiple models with synthesis in their voice.
argument-hint: [optional first-council prompt]
allowed-tools: Bash(curl *) Bash(bash *) Bash(trinity-local *) Bash(command -v *) Bash(which *) Bash(open *) Read
---

# Trinity Local — your taste, ported

Trinity reads transcripts already on the user's machine (Claude Code, Codex CLI, Antigravity, claude.ai, chatgpt.com, gemini takeout — Cursor is an install target, not yet an ingest source), learns the pattern in how the user rephrases / judges / decides, then runs hard questions through all three frontier providers in their voice and picks the answer they would have picked.

This skill is the orchestration layer that drives the `trinity-local` CLI from inside Claude Code via the bash tool. The CLI is the engine; this file is the user-facing contract. Every command below can be run by hand — the skill is a transparent driver, not a hidden one.

**Three tiers** the user can run Trinity at (this skill is Tier 1):

- **Tier 1 — Skill** (this file): markdown + JSON + tiny scripts at `~/.claude/skills/trinity/`. Trust mode read by Claude Code, no daemon, no listening port. What runs when the user types `/trinity`.
- **Tier 2 — Pip** (the `trinity-local` wheel — install path shown in section 2 below): same engine, performance + convenience upgrade. The `trinity-local` CLI this skill calls.
- **Tier 3 — Chrome Extension**: cross-surface capture (web chats) + one-click launchpad. Optional. See `trinity-local install-extension`.

All three tiers write to `~/.trinity/`. The data format is the contract; the tiers are surfaces.

## 1. Pre-flight: is Trinity installed?

!`command -v trinity-local || echo "NOT_INSTALLED"`

If `NOT_INSTALLED`, run section 2. Otherwise skip to section 3.

## 2. Install

Trinity ships as a git clone, not a published package. The installer drops the skill in `~/.claude/skills/trinity/`, writes thin shell wrappers in `~/.local/bin/`, registers MCP in every harness you have, and runs `status`.

!`curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash`

If the user wants to inspect first (recommended for trust-cautious users):

```
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh -o /tmp/trinity-install.sh
less /tmp/trinity-install.sh
bash /tmp/trinity-install.sh
```

The installer needs `git` + `python3.10+` on PATH. If Python is missing, recommend `brew install python@3.12` (macOS) or the distro equivalent (`apt install python3.12` etc.) — Trinity doesn't manage Python versions itself; too many opinions on how to do that.

## 3. Pre-flight checks

`status` verifies provider CLIs (Claude / Codex / Antigravity) are installed + authenticated, the MCP dep is present, the Trinity home directory is writable, the launchpad dispatch tier is wired, and embeddings can run. Each ✗ surfaces a one-line fix. (The legacy `doctor` CLI was collapsed into `status` pre-launch — `status` is the single health-check entry point.)

!`trinity-local status`

If any required check fails, walk the user through the surfaced fix. Don't proceed until `trinity_home_writeable`, `config_loadable`, and `mcp_available` are green. Provider CLIs are required for councils but not for setup itself.

**Cold-start callout**: the first embedding call downloads `modernbert-embed-base` from Hugging Face (~600 MB, one-time, ~3 minutes on a fast connection). Mention this if the user is about to run their first ingest.

## 4. Register the MCP server in every harness

The installer in section 2 already ran this. If MCP needs re-registration (after a `trinity-local update`, or to wire a newly-installed harness), run:

Trinity ships <!-- canonical:mcp_tool_count -->8<!-- /canonical --> MCP tools:
- **canonical three** — `run_council`, `get_persona`, `get_council_status`
- **v1.5 pair** — `ask` (cheap default), `get_picks` (introspection)
- **in-protocol provider loop** — `import_provider_memory` (write-back path: agent pipes its own extracted lens tensions / rejection signals into Trinity)
- **lens-lift** — `lens_generators` (run the generators pass in-session; returns cross-domain generating invariants)
  (`search_prompts` retired 2026-05-17; `get_eval_summary` retired 2026-05-18; `record_outcome` retired 2026-05-21 — chairman's pick is the supervision signal now, refinement prompts carry the "what differently" signal. `handoff` retired 2026-05-26 — 0 usage events in production; lens-via-MCP-Resources is the cross-provider continuity path. `route` removed 2026-06-08 — routing is now `ask(mode="route")`.)

!`trinity-local install-mcp`

## 5. First-run flow

If the user has CLI transcripts in `~/.claude/projects/`, `~/.codex/sessions/`, or `~/.gemini/antigravity-cli/brain/` (likely — Trinity is for power users who already polyharness), the cheapest first-run is to seed from those:

!`trinity-local ingest-recent`

That produces `~/.trinity/prompts/prompt_nodes.jsonl` — the indexed corpus. Then dream the core memories (one command, ~5-15 minutes depending on corpus size):

!`trinity-local dream`

`dream` runs cross-provider pair mining → consolidation → lens build → core distillation end-to-end. Output: `~/.trinity/core.md`, `~/.trinity/memories/lens.md`, `~/.trinity/memories/topics.json`, `~/.trinity/memories/vocabulary.md`. Open the launchpad to inspect:

!`trinity-local portal-html --open-browser`

## 6. Council on a hard question

For complex questions where two senior engineers could reasonably disagree (architecture, API shape, refactor scope), dispatch all three providers in parallel and chairman-synthesize the result:

```
mcp__trinity-local__run_council(task="$ARGUMENTS", mode="parallel")
```

The chairman reads `~/.trinity/memories/lens.md` and synthesizes the members' answers through the user's taste. Output: structured Routing JSON with `agreed_claims`, `disagreed_claims`, `winner`, `runner_up`, `provider_scores`, `routing_lesson`. Persisted at `~/.trinity/council_outcomes/<id>.json`.

The chairman's pick is automatically the supervision signal — `routing_label.winner` is what `compute_personal_routing_table()` aggregates from, no rating call needed (`record_outcome` retired 2026-05-21). what sets Trinity apart is the personal ledger of cross-model preferences; the chairman writes to it every council. If the user wants to refine ("I would have picked X because Y"), they click Refine on the council page — the refinement prompt itself is the post-pivot signal of "what should it have been instead." No rating step in the loop; the lens governs selection.

If the question doesn't warrant a full 3-provider council (lookups, syntax, mechanical refactors), prefer the cheaper single-call route:

```
mcp__trinity-local__ask(task="...")
```

## 7. Lens-flow via MCP Resources

Trinity's lens (`~/.trinity/memories/lens.md`, `core.md`, `topics.json`, `vocabulary.md`) is exposed as MCP Resources at session handshake — the harness lists them, the agent reads on demand. URIs:

- `trinity://memories/core.md` — one-paragraph identity
- `trinity://memories/lens.md` — paired tensions (the load-bearing personalization)
- `trinity://memories/topics.json` — subject basins
- `trinity://memories/vocabulary.md` — anchors + homonyms
- `trinity://scoreboard/picks.json` — extracted cortex routing rules
- `trinity://scoreboard/routing.json` — per-task-type provider track record

The agent reading these BEFORE the user types is the wedge — every response is conditioned on the user's taste without an extra round-trip.

## 8. Personalized evals (Trinity vs Claude vs Codex vs Gemini on YOUR kind of question)

After enough model-miss preference acts accumulate in `~/.trinity/me/preference_acts.jsonl` (roughly 50+ strong entries), build a personal eval suite scored against the user's actual lens:

```
trinity-local eval-build
trinity-local eval-run --target antigravity
trinity-local eval-show
```

The marketing-headline form: "Model X scored 0.YZ on YOUR kind of question." Empirically grounded, structurally asymmetric — only Trinity has cross-provider rejection signal.

## 9. Memory maintenance

- `trinity-local lens` — rebuild paired-tension lenses from `me/preference_acts.jsonl` plus prompt history (`lens-build` remains an alias)
- `trinity-local dream` — full memory-rebuild pass (Phase 5 refreshes `~/.trinity/core.md` automatically)
- `trinity-local consolidate` — extract cortex routing patterns from council outcomes (with `--audit` for second-flagship drift check)
- `trinity-local me-card --open` — render the strongest lens as a 1200×630 PNG for sharing

## 10. Trust + privacy (current state, v1.0)

Trinity does NOT make outbound network calls during normal operation. The embedding model is pulled once from Hugging Face on first run; afterwards `HF_HUB_OFFLINE=1` is pinned in process env. Every dispatch rides the user's own provider CLI subscriptions — no API keys leave the machine.

The personal ledger (`~/.trinity/council_outcomes/`) never uploads. If the user opts into telemetry (`trinity-local telemetry-enable`), only categorical routing labels (`task_type`, `provider_scores`, `winner`) leave — never prompt content.

**v1.1 will add** a uniform trust-mode + audit-log substrate (see `docs/three-tier-architecture.md`). v1.0 invariant: single active tier per `~/.trinity/` directory; concurrent multi-tier use is v1.1.

## 11. Common follow-ups

Suggest these to the user after section 5 succeeds:

> Trinity is set up. Next steps:
> - `/council <a hard question>` — dispatch through all three providers, synthesized in your voice
> - `trinity-local portal-html --open-browser` — the launchpad: recent councils, lens preview, eval leaderboard, topic graph
> - `trinity-local me-card --open` — render your strongest lens as a sharable PNG

## 12. Agent behavior — write back what you learn (the post-launch leverage point)

If you're inside Claude Code / Cursor / Codex and `mcp__trinity-local__import_provider_memory` is available in your tool list, you (the agent) have the user's conversation history on YOUR side. That's the same vantage the lens-prompt / eval-prompt verbs ask claude.ai / chatgpt to take — but the loop closes inside the harness, no copy-paste. Trinity's lens + rejection signal compounds every time you push back what you learn.

Four trigger conditions — call `import_provider_memory(kind, payload, dry_run?)` when you see any of these in the current chat:

1. **REFRAME / REDIRECT in the user's last turn.** The user said "no, actually…" / "skip that, just…" / "stop summarizing, give me the diff" — anything where your prior suggestion was replaced with a different one. Push it via `kind="eval"`, payload `{rejections: [{type: "REFRAME" | "REDIRECT", model_quote, user_substitute, why_signal, confidence}]}`. Your reaction to the substitution IS the signal. Captures preferences no static prompt can.

2. **A new paired tension just crystallized.** Across the last ~5 turns you noticed the user trades off X against Y in a way that wouldn't be obvious from any single message (e.g. "shipping velocity over polish unless the polish blocks composition"). Push via `kind="lens"`, payload `{tensions: [{pole_a, pole_b, failure_a, failure_b, horizon: "tactical" | "strategic" | "philosophical", evidence: [...], confidence, why_matters}]}`. Don't push surface preferences — push the GENERATOR (the variable that gates when X wins vs Y).

3. **Post-council reaction.** The user said something like "yeah, claude was right" or "no, the chairman missed the point" about a `run_council` outcome. Even when they don't click the launchpad's refinement button, their verbal reaction is signal — push it as `kind="eval"` with `type: "REFRAME"` and `why_signal` naming the chairman's miss.

4. **You're not sure if it's signal.** Use `dry_run=true` first. Trinity returns the merge plan (new vs duplicate vs malformed) without writing. If the plan says it would dedup against existing signal, the user has already taught Trinity that pattern — no need to push again.

**Verifiable test** that you actually called the tool successfully:

- For `kind="eval"`: `trinity-local lens-acts` shows `ledger: ~/.trinity/me/preference_acts.jsonl`, and `total` increments by the number of new non-duplicate signals.
- For `kind="lens"`: `cat ~/.trinity/me/lenses.json | jq '.lenses | length'` increments by the number of net-new tensions; existing pole-pairs get evidence-augmented in place.

**Why this compounds**: every iteration of the user's `/loop` (or any session) becomes a signal-engine. The chairman reads `lens.md` + `core.md` on every council; richer lens → sharper future verdicts. Surface-minimal: requires zero new tools beyond the one already shipped (the lane added `import_provider_memory`; the count is now 7 — `handoff` and the standalone `route` tool both retired).

## 13. Agent behavior — ask Trinity before you ask the user

When you (the agent) hit a decision you'd normally surface to the user — a product / architectural / tooling choice between options (which database, which approach, how to structure this), NOT a personal or irreversible one — **consult Trinity first instead of interrupting them**:

- **Quick choice** → call `mcp__trinity-local__ask`. It routes on what the user's own councils have shown wins their kind of question and answers in their voice — one provider, one call.
- **Genuinely hard / high-stakes** → call `mcp__trinity-local__run_council` and act on the chairman's `agreed_claims` / `disagreed_claims`.

Then proceed on Trinity's answer. **Escalate to the user only when** Trinity has no signal (cold lens), the call is genuinely novel with no precedent in their history, it's irreversible / high-stakes, or it's a *personal* preference that's theirs to make (not inferable from taste). Reserve `AskUserQuestion` for exactly those.

This is the "ask Trinity, not me" contract: the user gets interrupted less, and the calls made on their behalf are the ones their own history supports — never a blind autopilot. It depends on a built lens — on a cold install Trinity has nothing to answer from and will just escalate (same as asking you directly), and it sharpens as the lens fills.

---

**Tier-equivalence invariant** (v1.0 commitment): Trinity tiers produce the same outputs under a pinned configuration (model `nomic-ai/modernbert-embed-base`, tokenizer pinned, numpy ≥ 1.26). Embedding cosine ≥ 0.9999, identical k-means cluster assignments at production N, identical chairman picker output. NOT bit-equality — float-order differs across MLX vs torch CPU vs torch CUDA by SIMD scheduling. The pinned-config equivalence is the falsifiable form.
