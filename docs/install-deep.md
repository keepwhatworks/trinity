---
class: live
---

# Install — deep dive

> The README has the one-line install. This file is the long-form
> companion: prereqs, the three install paths, the embedding model
> download, what `uninstall` removes, and how to drive Trinity from
> inside Claude Code.

## Prereqs

Trinity needs **Python 3.10+** (macOS Sequoia ships with 3.9 by default —
check with `python3 --version`). If you need to upgrade:

```bash
brew install python   # 3.12 or newer
```

Plus the provider CLIs you want in the council: `claude`, `codex`, and/or
`agy` — each authenticated to your subscription. `trinity-local status`
will tell you which are missing.

## Local models — free, optional, auto-discovered

Any model you've pulled with [Ollama](https://ollama.com) appears in
Trinity's routing pool automatically. No config edit required, no MCP
tools to install per model — Trinity probes `ollama list` every 30
seconds and adds detected models as candidates named
`ollama:<model>` (e.g. `ollama:qwen3:32b`, `ollama:deepseek-r1`).

```bash
ollama pull qwen3:32b      # pull whatever you want to run locally
ollama pull deepseek-r1    # multiple is fine — Trinity sees them all
```

Now ask in Claude Code:

> Run a Trinity council on X with claude, codex, and ollama:qwen3:32b.

Cost for the Ollama member: $0. The chairman still synthesizes through
your lens. MLX-on-mac models are also detected (provider name
`mlx:<model>`) if you have the runtime installed. Local models are
graceful-degrade — if the Ollama daemon isn't running, Trinity silently
skips them and the council runs with the cloud members alone.

The MCP surface stays at <!-- canonical:mcp_tool_count -->8<!-- /canonical --> tools regardless of how many local models
you pull. Providers are *parameters* to `ask` / `run_council`, not
their own tools — the architecture scales to dozens of locals without
bloating the agent's tool list.

## Quickstart (desktop first)

```bash
# One-liner — clones to ~/.trinity/code/ (with a back-compat symlink
# at ~/.claude/skills/trinity/), drops wrappers in ~/.local/bin/,
# registers MCP in every harness you have, runs status.
<!-- canonical:install_command -->curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash<!-- /canonical -->

# Then, in Claude Code, just type:  /trinity
# The skill walks you through status + ingest + dream + your first council.
```

The `/trinity` skill is the primary entry point; it teaches the full CLI
after the first council. For the raw command reference, run
`trinity-local --help`.

## Two install paths, two audiences

The CLI (`install-mcp`) is the engine — every other surface wraps it.

- **Skill** (primary; `/trinity`) — what you reach for inside Claude Code.
- **Chrome extension** (`install-extension`) — also the launchpad host.
  Pin the toolbar icon, click it to open the static launchpad; Native
  Messaging dispatches launchpad buttons back to the local CLI. Your
  claude.ai and chatgpt.com conversations grow the corpus passively.

Neither gates the wow: the moment `install-mcp` lands, the first MCP
spawn auto-scans the CLI transcripts you already have on disk, so your
first council is personalized.

## Removing Trinity

```bash
trinity-local uninstall                # dry-run: lists what would be removed
trinity-local uninstall --yes          # actually remove MCP configs + skill
trinity-local uninstall --yes --include-data   # also delete ~/.trinity/ (irreversible)
```

The default uninstall removes Trinity from `~/.claude.json`,
`~/.gemini/settings.json` (and the legacy `~/.gemini.json` if it exists
from an older install), `~/.cursor/mcp.json`, the `[mcp_servers.trinity-local]`
block from `~/.codex/config.toml`, the Chrome Native Messaging manifest,
and the bundled `/trinity` skill — but **preserves `~/.trinity/`** (your
corpus, lens, scoreboard, council outcomes) unless you explicitly pass
`--include-data`. Owning your data cuts both ways: it also means you
decide when to delete it.

`trinity-local status` checks each provider CLI is installed +
authenticated, the MCP server dependency is present, and your Trinity
directory is writable — surfaces a one-line fix for each ✗ before you hit
a live council.

## Offline by default — and a one-time embedding model download

Trinity pins `HF_HUB_OFFLINE=1` at startup, so the running system never
makes outbound HuggingFace calls during normal operation. The embedding
model (`modernbert-embed-base`, ~600 MB) ships once via an explicit, opt-in
download:

```bash
HF_HUB_OFFLINE=0 huggingface-cli download nomic-ai/modernbert-embed-base
```

After that the model lives at `~/.cache/huggingface/hub/` (or `$HF_HOME`)
and Trinity loads it from cache — no Hub contact. Override per-invocation
if you ever need to pull a new version (`HF_HUB_OFFLINE=0 trinity-local …`);
otherwise the offline guarantee holds across every CLI call and every MCP
child process.

## Drive it from inside Claude Code

`trinity-local install-mcp` also drops a `/trinity` skill into
`~/.trinity/code/skills/trinity/` (resolved through the back-compat
symlink at `~/.claude/skills/trinity/` so Claude Code's skill loader
finds it). Once the install-mcp step above ran, type `/trinity` at the Claude
Code prompt to redo the install + status + first-council on a fresh
machine without touching your shell. The skill respects local edits — if
you've customized the file, future `install-mcp` runs leave it alone.
