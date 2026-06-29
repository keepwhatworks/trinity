---
class: live
---

# Three-tier architecture

> Ratified 2026-05-16 by `council_ff3da1fa84906791` (chairman codex, winner claude).
> Stop-light: **ship with modifications** — partial three-tier for v1.0,
> shared substrate in v1.1.

Trinity ships as three tiers, each fully functional standalone. The data
format in `~/.trinity/` is invariant across tiers; the tiers differ in
*how* you invoke Trinity, not *what* Trinity computes.

## Tier 1 — MCP server (primary, 2026-05-19 pivot)

Trinity registers as an MCP server in every harness that supports MCP
(Claude Code, Codex CLI, Antigravity, Cursor, Claude Desktop). The
agent calls tools like `mcp__trinity-local__run_council` inline —
no separate UI, no `/skill` invocation needed. The tool docstrings
ARE the contract; the agent reads them at MCP handshake and dispatches
based on the user's natural-language ask.

**Why MCP-first** (was: skill-first). The Chrome extension first-run UX
is "paste this brief into Claude Code / Claude Desktop." The agent
reads MCP tools, not a markdown skill file, when it decides what to
call. Anyone with one MCP-capable harness gets Trinity; skill files
require Claude Code specifically. Audience expansion + simpler mental
model + one source of truth (the docstrings, no SKILL.md drift).

The skill at `~/.claude/skills/trinity/` is **kept as a back-compat
alias** for users who already type `/trinity` in Claude Code — it
now points at the same MCP tools. New users never have to know it
exists.

## Tier 2 — Engine

The Python engine MCP tools call. Lives in
`~/.claude/skills/trinity/src/trinity_local/` after the curl-bash
installer clones it. The `trinity-local` shell wrapper at
`~/.local/bin/trinity-local` resolves to this engine via PYTHONPATH.

The engine contains both the CLI ergonomics (`trinity-local status`,
`dream`, `update`, …) AND the heavy ops (embeddings, k-means,
geometric median, descriptor pipeline). The MCP server (Tier 1) imports
from here too — same code path, different surface.

**No PyPI publish.** Python-library users who want
`from trinity_local import council_runtime` in their own code do
`pip install -e ~/.claude/skills/trinity/` from the cloned repo — see
[`INSTALL-pip.md`](INSTALL-pip.md) for the rationale.

## Tier 3 — Chrome Extension (discovery + capture sidecar)

The extension is the **non-technical-user entry point**. From the
Web Store install, the popup's setup card copies a paste-into-agent
brief that runs `install.sh` end-to-end — the user never touches a
terminal. Once installed:

* **Browser capture**: streams web chats from claude.ai / chatgpt.com
  / gemini.google.com into `~/.trinity/conversations/` via Native
  Messaging. No listening port, no upload — Chrome spawns a local
  capture host on demand.
* **Dispatcher**: narrow action-allowlist (<!-- canonical:chrome_action_allowlist_count -->17<!-- /canonical --> entries
  total — `launch-council`, `ingest-recent`, `stop-council`,
  `render-me-card`, `dream`, `council-iterate`, three settings
  toggles `telemetry-enable` / `telemetry-disable` /
  `telemetry-reset-id`, the in-process popup actions
  `open-council-page`, `get-council-status`, `open-launchpad`, plus
  the launchpad UI surfaces `extension-repair-auto` (task #147 self-
  healing), `import-export` (task #148 bulk Takeout — full ingest),
  `import-export-dry-run` (task #148 — detection-only probe), and the
  'Building your lens' card's `lens-stop` (#242a cancel) + `lens-build`
  (#242a restart, detached)).
  Each entry pins a fixed CLI subcommand and a typed arg list — no
  shell payload, no `run_command` — so spoofed Native-Messaging
  payloads can't trigger arbitrary commands. Cross-platform —
  replaces the macOS Shortcut dispatcher retired 2026-05-17.
* **Auto-update channel** (2026-05-19): planned to bundle the Python
  source inside the extension package so Chrome's ~5h Web Store
  update cadence delivers Python updates too. Today: curl-bash users
  run `trinity-local update` (git pull).

See [`MIGRATION.md`](MIGRATION.md) for the dispatcher migration.

---

## Data invariance contract

The `~/.trinity/` directory is the contract. Every tier writes to the
same files with the same schemas:

| File | Schema | Producer |
|---|---|---|
| `~/.trinity/prompts/prompt_nodes.jsonl` | PromptNode (in-tree) | ingest |
| `~/.trinity/council_outcomes/*.json` | `schemas/council_outcome.schema.json` | council runner |
| `~/.trinity/memories/lens.md` | paired tensions (in-tree) | lens-build |
| `~/.trinity/memories/topics.json` | basins (in-tree) | basins |
| `~/.trinity/memories/vocabulary.md` | anchors (in-tree) | vocabulary |
| `~/.trinity/core.md` | distillation (in-tree) | dream (Phase 5 — the standalone `distill` CLI was retired pre-launch) |
| `~/.trinity/scoreboard/picks.json` | picks (in-tree) | consolidate |
| `~/.trinity/scoreboard/routing.json` | routing (in-tree) | aggregation |
| `~/.trinity/me/preference_acts.jsonl` | `schemas/rejection_signal.schema.json` | turn_pairs |

## Tier-equivalence invariant

Trinity tiers produce **tier-equivalent** outputs under a pinned
configuration — NOT bit-identical. Float-order differs across MLX
vs torch CPU vs torch CUDA by SIMD lane scheduling; claiming
bit-equality would be a launch-credibility bug.

The falsifiable v1.0 invariant:

- Embedding cosine similarity ≥ 0.9999 between any two backends on
  the same input under pinned tokenizer + model hash
- Identical k-means cluster assignments at production N (n ≥ 30
  threads) given the same RNG seed
- Identical chairman picker output for the same `(task_type,
  available_models)` input

Verified by `tests/test_phase8_integration.py` for the launchpad
dispatch contract; the broader cross-backend matrix lands in v1.1.

## v1.0 floor (shipped May 13–15, 2026)

Ratified by the council:

- `src/trinity_local/` unchanged. The test suite stays green.
- `skills/trinity/SKILL.md` (new) — orchestrates the existing CLI via
  Claude Code's bash tool.
- `skills/trinity/schemas/` (new) — copies of the in-repo schemas
  (`council_outcome`, `eval_set`, `rejection_signal`). The 2026-05-26
  v2 additions (`move`, `dream_rejection`, `dream_demotion`) and the
  2026-05-27 addition `dream_calibration` were retired 2026-05-27 with
  the moves substrate teardown (#184) — see `retired_names.py`.
  The `trust.schema.json` that
  shipped 2026-05-18 alongside the trust substrate was deleted
  2026-05-22 (iter #121 of the post-launch sweep) after the library
  was retired — v1.1 will design its own gating schema fresh, see
  [`historical/trust-mode.md`](historical/trust-mode.md) for the
  original design.
- Extension as-is (Phase 4b shipped — see MIGRATION.md).
- `docs/three-tier-architecture.md` (this file) — full vision,
  marks shared `scripts/` substrate as v1.1.
- One new doc-consistency guard:
  `tests/test_skill_md_commands_resolve.py` — every
  `trinity-local <cmd>` mentioned in SKILL.md must exist in
  `trinity-local --help`.

## v1.1 stretch (post-launch)

**Do NOT attempt during a launch window.** Council verdict was
unambiguous: the 70-module refactor under deadline pressure puts
the full-suite green gate at risk for a reframe that doesn't require
code motion.

What v1.1 picks up:

- `scripts/` as importable+executable shared substrate. Each script:
  shebang, own venv at `~/.trinity/.venvs/<script_name>/`, JSON
  stdin/file input, JSON stdout/path output, audit-log append per
  invocation, `--help` documenting interface + deps.
- Engine extraction from `src/trinity_local/` to `scripts/` —
  embeddings, basins, cortex geometry, descriptor, signature,
  anchor. ~25 modules.
- Pip package narrows to CLI ergonomics + installers + optional
  daemon. ~40 modules stay (commands, MCP server, launchpad
  templates).
- Trust-mode rebuild from scratch. The v1.0 substrate (per
  [`historical/trust-mode.md`](historical/trust-mode.md) — `trust.toml`
  schema, `trinity_local.trust` library + 16 tests, `--dangerously-trust-all`
  env-var gate) was retired 2026-05-22 (iter #117 of the post-launch
  sweep, commit `0ad1ca5`) after audit found zero production imports
  — the active audit-log surface is `scripts/_runtime.py::audit_log()`,
  an independent stdlib-only implementation that never went through
  the library. What v1.1 picks up is a clean rebuild: gating config
  (v1.1 will design the shape fresh — the v1.0 `trust.schema.json`
  was deleted 2026-05-22 alongside the library; the v1.0 toml example
  is preserved in [`historical/trust-mode.md`](historical/trust-mode.md)
  if reference is wanted), user-facing CLI (the prior trust-init /
  trust-show / audit-show surface was retired alongside the library;
  v1.1 will design replacements fresh), automatic audit rotation,
  visible trust indicators in launchpad + extension popup, cross-tier
  `TRINITY_ORIGIN_TIER` propagation (the audit-log writer already
  stamps this), `--tier`/`--operation`/`--outcome` filter flags, and
  a top-level `--dangerously-trust-all` flag on `trinity-local`.
- Cross-backend equivalence test harness:
  `tests/test_tier_equivalence.py` covering MLX / torch CPU / (post-
  v1.1) torch CUDA against pinned-config invariants.

## Sequencing rationale

The brand pivot is a story about WHERE Trinity lives (Claude Code,
Codex CLI, Antigravity, Cursor) — not WHAT it's compiled into. A
SKILL.md that calls `trinity-local consolidate` is structurally
indistinguishable from a SKILL.md that calls `python3 scripts/
cortex.py` for the user-visible claim "lives inside Claude Code."

The shared-`scripts/` refactor is an engineering luxury, not a
launch requirement.

## Known limitations (v1.0)

- **Concurrent multi-tier use**: undefined behavior if the user has
  Claude Code (Tier 1) writing to `~/.trinity/` while the extension
  (Tier 3) also writes. v1.0 assumes single active tier per
  directory. v1.1's audit log + file locking lifts this.
- **Real-Chrome smoke gated**: `tests/test_chrome_extension_smoke.py`
  is gated behind `TRINITY_CHROME_SMOKE=1` + optional Stagehand deps in
  `browser-extension/`. It launches local Chrome with the unpacked
  extension. Static contract guard in `test_phase8_integration.py`
  catches manifest drift in CI.
- **HF-Hub cold start**: ~3-minute one-time download of
  `modernbert-embed-base` on first embedding call. Surface this in
  SKILL.md Section 3 before the user runs `dream`.

---

**Council citation**: `council_ff3da1fa84906791` (2026-05-16).
Outcome JSON at `~/.trinity/council_outcomes/council_ff3da1fa84906791.json`.
Routing lesson: "For architecture_decision, prefer claude because it
identifies the one launch-preserving move and cuts the rest."
