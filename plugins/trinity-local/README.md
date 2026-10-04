# Trinity Local: Claude Code plugin

Ask all three. Keep what works. Put plans in front of Claude, Codex and Gemini before you commit, and check changes with your own tests before you call them done. Local-first: your transcripts stay on your machine, on the subscriptions you already pay for.

This plugin registers Trinity's MCP server in Claude Code and adds three commands.
It carries its own copy of Trinity and, on first start, installs three Python
packages (numpy, mcp 1.x, Pillow) into `~/.trinity/venv`. It needs Python 3.10 or
newer; it looks for `python3.13` down to `python3.10` before plain `python3`.

## Install

```
/plugin marketplace add keepwhatworks/trinity
/plugin install trinity-local@trinity
/reload-plugins
```

To also use Trinity from Codex or agy, and to get the `trinity-local` command, run
the one-line installer from <https://keepwhatworks.com> instead (or as well; the
plugin then uses that install).

## Commands

| Command | What it does | MCP tool |
|---|---|---|
| `/trinity-local:council <plan>` | Before you commit to a plan: three labs answer independently; you get a decision, what would change it, and where they split. | `run_council` |
| `/trinity-local:verify [what it's for]` | Before you call a change done: runs your tests, then the other labs read the diff as a second opinion. | `verify` |
| `/trinity-local:setup` | Checks Claude Code, Codex and agy are set up and names the one thing to fix. | `trinity-local install --check` |

The server also tells Claude when to reach for the first two on its own.

## Notes

- **No hooks, no gating.** The plugin adds commands and the MCP server; it never
  intercepts your responses or spends quota on its own.
- **Local-first.** Councils run on your machine through your own CLI subscriptions.
- Already ran `trinity-local install`? The two registrations coexist; you don't need
  both, but it's harmless.
