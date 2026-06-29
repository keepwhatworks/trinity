---
class: live
---

# Install Trinity (curl-bash → MCP-primary install)

> MCP is the primary tier per the 2026-05-19 pivot
> (see [`three-tier-architecture.md`](three-tier-architecture.md) L15-22).
> Trinity registers as an MCP server in every MCP-capable harness
> (Claude Code, Codex CLI, Antigravity, Cursor) — the agent calls
> tools like `mcp__trinity-local__run_council` inline; no `/trinity`
> invocation needed for new users.
>
> The skill at `~/.claude/skills/trinity/SKILL.md` is kept as a
> back-compat alias for users who already typed `/trinity` in Claude
> Code before the pivot — `/trinity` still resolves to a friendly
> walkthrough that drives the same `trinity-local` CLI via Claude
> Code's bash tool. New users never need to know it exists.
>
> This doc explains the install path (curl-bash) — the same install
> wires MCP across all harnesses AND preserves the `/trinity`
> back-compat alias path in one step.

## What you get

Type `/trinity` in Claude Code. The skill walks the install (if
needed), runs `status` (the pre-flight checks formerly in `doctor`),
registers the MCP server, ingests your existing CLI transcripts,
dreams your core memories, and dispatches your first council. Every
command Trinity runs is visible (Claude Code shows the bash
invocations) and audited (`~/.trinity/audit.log`).

## Install path

One curl-bash. Trinity is a git clone, not a published package. The
installer drops the code at `~/.trinity/code/` (with a back-compat
symlink at `~/.claude/skills/trinity/` so `/trinity` in Claude Code
keeps working for users who already had it wired), writes thin
shell wrappers in `~/.local/bin/`, registers MCP in every harness it
finds, and runs `status` to verify:

```bash
<!-- canonical:install_command -->curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash<!-- /canonical -->
```

Prefer to inspect before piping to bash? Same install in two steps:

```bash
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh -o install.sh
less install.sh    # ~150 lines of plain bash; read it
bash install.sh
```

What the installer does:
1. Verifies Python 3.10+ is on PATH (doesn't try to install Python
   for you — too many opinions on how it should be managed)
2. `git clone` the repo to `~/.trinity/code/` (or `git pull` if
   already present — idempotent). Creates `~/.claude/skills/trinity/`
   as a symlink to that path so `/trinity` in Claude Code keeps
   resolving for users who had the legacy install location.
3. Drops `~/.local/bin/trinity-local` + `~/.local/bin/trinity-local-capture-host`
   as thin shell wrappers (~5 lines each; PYTHONPATH-set + exec)
4. Runs `trinity-local install-mcp` to register the MCP server in
   Claude Code, Codex CLI, Antigravity, and Cursor (non-destructive
   edits to each harness's config)
5. Runs `trinity-local status` to verify

No PyPI, no npm. Updates: `trinity-local update` — pulls + refreshes.

## After install

```bash
# Verify everything's wired:
trinity-local status

# In Claude Code, the skill is now active:
# /trinity
```

The skill walks the rest. If you want to script it instead of using
Claude Code:

```bash
trinity-local ingest-recent             # auto-discovers ~/.claude, ~/.codex, ~/.gemini transcripts
trinity-local dream                     # ~5-15 minutes
trinity-local portal-html --open-browser
```

## Audit log

Trinity's audit log at `~/.trinity/audit.log` is the append-only JSONL
ledger that `scripts/_runtime.audit_log()` writes every operation
through. Inspect with `tail -20 ~/.trinity/audit.log`.

The trust-*gating* library (`trinity_local.trust`) was retired
2026-05-22 (iter #117 of the post-launch sweep, see
[`historical/retirement-log.md`](historical/retirement-log.md)) —
nothing currently reads `~/.trinity/trust.toml`. The gating config +
the `trust-init` / `trust-show` / `audit-show` CLI surface return as
a fresh implementation in v1.1. Until then, operations don't
pre-grant from a config; the Claude Code permission dialog (skill
tier) is the gating surface.

See [`historical/trust-mode.md`](historical/trust-mode.md) for the
original design (council `c18f739a0234aa58`, 2026-05-16); preserved
as the historical record of the substrate Trinity moved away from
when the library was retired.

## What runs locally vs. what doesn't

- **Local-first**: every embedding, k-means, geometric median,
  descriptor extraction, signature distillation, and audit-log write
  runs on your machine. Trinity never makes outbound HTTP except for
  the one-time `modernbert-embed-base` download from Hugging Face on
  first run (~600 MB; afterwards `HF_HUB_OFFLINE=1` is pinned).
- **Provider CLIs**: council dispatches ride your existing Claude /
  Codex / Antigravity subscriptions. Trinity calls those CLIs as
  subprocesses — your credentials never leave your machine via
  Trinity, only via the provider CLIs themselves (the same way they
  always have).
- **Telemetry is credential-gated and no-PII.** The public build has
  categorical telemetry enabled in config, but it sends nothing unless
  GA4 credentials or a custom collector are explicitly present in the
  environment. When configured, the wire boundary allowlists only
  categorical / numeric routing fields and provider win-rate snapshots
  — never prompt content, lens text, or user substitutes. Disable with
  `trinity-local telemetry-disable`.

## See also

- `INSTALL-pip.md` — Python-library access for users who want
  `from trinity_local import council_runtime` in their own code
  (uses the same git clone; `pip install -e .` from there)
- `INSTALL-extension.md` — install the Chrome extension for cross-
  surface capture + one-click UI
- `three-tier-architecture.md` — full architecture
- `historical/trust-mode.md` — trust + audit substrate (HISTORICAL, retired 2026-05-22)
