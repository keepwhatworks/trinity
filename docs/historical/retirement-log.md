---
class: historical
---

# Retirement log

> Historical context relocated from `claude.md` on 2026-05-22 during
> the v1.7.5 cleanup pass. This file records what Trinity *used to
> have*, why it was retired, and when. Canonical registry is still
> `src/trinity_local/retired_names.py` — that file is what the
> regression guards read. This document is the prose companion.

## Pre-launch simplification (2026-05-18, v1.7.4)

Removed ~5,000 LOC of features that didn't earn their keep:

- **Trinity.app** — osacompile-generated .app bundle was retired
  pre-launch because it was only a launchpad wrapper. The next
  desktop surface has to be a real local cockpit over `~/.trinity/`,
  not a bookmark-shaped app.
- **macOS Shortcut dispatcher** — `shortcut_setup.py` +
  `dispatch_runner.py` + `commands/shortcuts.py` retired
  2026-05-17 in favor of the cross-platform Chrome extension.
  `shortcuts_integration.py` survives only as an inert
  backward-compat shim so older renderers don't break before their
  JS surgery lands.
- **Watcher subsystem** — `watch-once` / `watch-loop` CLIs retired
  pre-launch. Tool-triggered ingest (MCP `ask` fires
  `ingest_recent()` with a 1s deadline) replaces the daemon model.
- **Embedding cache** (`cache/embeddings.jsonl`) — retired
  2026-05-17. Offline rebuild passes re-encode their corpus per run
  (~2 min on 50k prompts); cold-start UX unchanged.
- **Source-side `src/trinity_local/research/` package** — the
  package was deleted in the 2026-05-18 simplification pass. The
  user-state `~/.trinity/research/` directory is NOT retired — it
  remains live as the k-NN advisory corpus (read via
  `state_paths.research_dir()`).
- **10+ internal CLI commands** — see CHANGELOG v1.7.4 entry for
  the list. Pre-launch simplification (Passes A–BB) collapsed
  task/bundle/launch/watch/distill/cache/depth/metric/trust/
  shortcut/council-last/auto-chain/auto-open.

## CLI surface drops

Live CLI surface after the simplification pass: 22 user-facing
command modules. 4 more (`bootstrap_pairs`, `distill`, `helpers`,
`trust`) survive as importable utilities for tests + internal
callers but no longer register CLIs.

Retired CLI subcommands:

- `auto-chain` → replaced by `council-iterate --rounds N`
- `auto-open` → launchpad opens automatically
- `bootstrap-pairs` → subsumed by `dream`
- `cache-*` → embedding cache retired entirely
- `council-last` → use launchpad (was previously `→ use unrated`; unrated retired 2026-05-22)
- `council-rate` → retired 2026-05-22 with full rating-surface sunset; chairman's `routing_label.winner` IS the supervision signal now (lens governs council selection — no human rating step in the loop)
- `daemon` / `watch-loop` / `watch-once` → tool-triggered ingest
- `depth-show` → orphan module (commands.depth retired tick #85)
- `distill` → subsumed by `dream`
- `metric` → orphan module
- `shortcut-*` → macOS Shortcut dispatcher retired 2026-05-17
- `stats` → never load-bearing
- `task-*` / `bundle-*` / `launch-*` → collapsed in pre-launch passes
- `unrated` → retired 2026-05-22 with rating-surface sunset (was Pillar-4 funnel-widening for ratings; whole purpose moot post-retirement)

Retired internal functions:

- `doctor._check_verdict_rate()` → retired 2026-05-22; metric is structurally 0% post-rating-retirement = misleading noise
- `launchpad_data._verdict_stats()` → retired 2026-05-22; sole remaining consumer was the doctor check above; ~55 LOC dead function + 511 LOC dead tests removed
- `trust-init` / `trust-show` / `audit-show` → deferred to v1.1

## MCP tool retirements

- **`record_outcome`** — retired 2026-05-21 alongside rest of the
  rating UX. The chairman's `routing_label.winner` is the
  supervision signal now, computed into the personal routing table
  automatically via `compute_personal_routing_table()` walking
  `~/.trinity/council_outcomes/*.json` on demand.
- **`get_eval_summary`** — retired 2026-05-18 in commit `6a7f176`.
  Agents ground via `ask` + `get_picks`; the eval-summary surface
  remains on the launchpad card and `eval-show`.
- **`judge`** — subsumed by `run_council(responses=[...])`.
  Pre-supplied member outputs go straight to chairman synthesis,
  one model call instead of N+1.
- **Legacy tools dropped from public MCP surface**: `get_status`,
  `get_elo`, `get_recent_councils`, `watch_once`. These remain
  importable for the launchpad but are NOT exposed via MCP.

## Retired ~/.trinity/ directories

These may still exist on older installs (Trinity no longer reads
or writes them):

- `tasks/` (→ `todos/`)
- `memory/` (→ `prompts/`)
- `watcher/` — cursor files for watch-loop, retired with the
  watcher subsystem.
- `shortcut_setup/` + `bin/trinity-dispatch` — macOS Shortcut
  dispatcher, retired in favor of the Chrome extension.
- `cache/embeddings.jsonl` — offline rebuild passes re-encode now.
- `models/` — was created as a side-effect of the retired
  `models_dir()` helper but never written to; actual nomic weights
  live in `~/.cache/huggingface/hub/` (retired 2026-05-20, tick #28).
- `cortex/` — was created as a side-effect of the retired
  `cortex_dir()` helper; spec-v1.5 originally described
  `cortex/failure_modes.json` + `cortex/successful_prompts.json`
  but the shipped `picks.json` embeds both inline (retired
  2026-05-20, tick #51).
- `digest_pages/` — weekly digest feature deleted pre-launch.

`~/.trinity/research/` is NOT retired — only the source-side
`src/trinity_local/research/` package was deleted in the 2026-05-18
simplification pass. The user-state directory remains live as the
k-NN advisory corpus.

## Rating UX sunset (2026-05-21)

The 6-stage rate-capture defense was sunset 2026-05-21 alongside
the rest of the rating UX — `rate_action`, `record_outcome`, and
`pending_ratings` all retired (see `retired_names.py`). The new
mechanism: the chairman's `routing_label.winner` field IS the
supervision signal, written automatically to
`~/.trinity/council_outcomes/<id>.json` on every council.
`compute_personal_routing_table()` walks the outcomes directory on
demand — no user-click step in the loop. This removes the "council
count grows faster than verdict count" drift the rating mechanism
was vulnerable to: every council is a verdict now.

Open question: whether the chairman pick alone is a strong-enough
signal at low n vs the human-veto-able rating that was retired.
Refinement prompts on the council page carry the "what user wanted
differently" signal that user_winner ratings used to. The personal
ledger of cross-model preferences is what makes Trinity work — empty
ledger = nothing to learn from.

## Loop Constitution substrate (2026-05-18)

The double-loop substrate (`frame` / `run` / `verify_web`, formerly
`src/trinity_local/loop/`) was **removed from the codebase** as
pre-launch simplification — 1,396 lines of v2-trajectory code. The
mechanic — *execute → verify → cull → re-verify → commit* — was
queued to be rebuilt leaner inside a `plan_and_execute` MCP tool
(task #128). Originally v1.6 → v1.7 deferral; v1.7 shipped without
it (v1.6 turned out to be browser-extension capture, v1.7 became
MCP-primary + post-launch consistency sweep). **Formally sunset
2026-05-22** in the v1.7.5 cleanup pass: running v1.5 on real
data showed the `ask` + `run_council` ceiling didn't bind hard
enough to justify rebuilding the orchestration layer; the harness
(Claude Code / Codex / Antigravity) owns multi-step orchestration
better than Trinity should. Task #128 deleted.

Git history preserves the prior implementation + the ratifying
council outcomes as architectural reference.

## Renames before shipping

Trinity hasn't shipped a stable API yet (Python 3.10+ engine; the
MCP server contract is the public surface). Pre-launch renames
happened without deprecation aliases — see Principle #7 in
`docs/historical/principles.md`. Notable renames:

- `memory/` → `prompts/` (Tier 1 #1; automatic one-time migration
  inside `prompts_dir()` itself)
- `me-build` → `lens-build`
- `portal_*.py` → `launchpad_*.py`
- `task_kind` → `task_type` (task #92; enforced by
  `TestNoStaleTaskKindInCode`)
- `picks.json` / `routing.json` moved from
  `~/.trinity/memories/` → `~/.trinity/scoreboard/` (v1.7; on-disk
  migration in `state_paths._migrate_legacy_scoreboard_paths()`)
- "seat" vs "member": Tier 2 #6 (task #95) tried `member` → `seat`
  as a table metaphor but was unwound. Code structures like
  `members=[...]` made the rename costly without payoff. Canonical
  term is `member` (across code AND copy).

## Where to go for more

- `src/trinity_local/retired_names.py` — canonical registry (what
  the regression guards read).
- `docs/historical/principles.md` — the 35 meta-principles
  extracted from the fixes (the rules that earned their place by
  costing time).
