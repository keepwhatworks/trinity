---
description: Verify + self-heal the Trinity install — engine on PATH, MCP registered, and what to restart.
argument-hint: (no arguments)
---

Verify the Trinity-Local install end to end and fix what you can, then tell the
user the one or two things only they can do. Work through these in order and
report a short checklist (✅ / ⚠️ / ❌ per item):

1. **Engine on PATH.** Run `trinity-local --version` (or `command -v trinity-local`).
   - If it's missing, the runtime isn't installed (there is **no PyPI/npm package**).
     Tell the user to run the one-line installer and stop:
     `curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash`
   - If it runs but only via an absolute path, `~/.local/bin` likely isn't on PATH —
     tell the user to add `export PATH="$HOME/.local/bin:$PATH"` to their shell rc
     (this is the one step the installer can't do for them).

2. **Health.** Run `trinity-local status`. Surface any check that reports a `fix`
   field verbatim. If it reports the embedder isn't downloaded, note that real
   embeddings (and the personalized taste read) need a one-time
   `trinity-local download-embedder` (~600 MB, local).

3. **MCP registered.** Run `trinity-local install-mcp` to (re)write the MCP server
   config into every harness it detects (Claude Code, Codex CLI, Antigravity,
   Cursor). It's idempotent — safe to re-run.

4. **Restart reminder.** Adding or re-registering MCP tools needs the harness to
   reload. Tell the user to **restart this harness** (or run `/reload-plugins` in
   Claude Code) so the `mcp__trinity-local__*` tools appear — this is the second
   step the CLI can't do for them.

5. **Smoke.** If the MCP tools are already visible, call
   `mcp__trinity-local__get_persona` once to confirm the server answers; otherwise
   say it'll be available after the restart.

Do NOT run a council or `eval-run` here (that spends quota) — this command only
verifies and repairs the install. End with the single most important next action
for the user (install the runtime / fix PATH / restart / download the embedder),
not a wall of green checks.
