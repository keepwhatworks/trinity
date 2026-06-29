# Trinity Local — Claude Code plugin

Run a cross-provider council from inside Claude Code — ask Claude, ChatGPT, and
Gemini in parallel and the chairman synthesizes the verdict, free and local. This
plugin registers the Trinity MCP server and adds three native slash commands — in one
marketplace install, no manual `install-mcp` step. Over time, your lens personalizes
the synthesis.

## Install

```
/plugin marketplace add keepwhatworks/trinity
/plugin install trinity-local@trinity
/reload-plugins
```

That's it. The plugin registers the Trinity MCP server automatically. Trinity itself
must be installed once on your machine (the plugin's launcher finds it):

```
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
```

The installer clones Trinity to `~/.trinity/code`, builds a venv at `~/.trinity/venv`,
and drops wrappers in `~/.local/bin`. The bundled launcher (`bin/trinity-mcp`)
resolves Trinity in order: the `trinity-local` console script → `python3 -m
trinity_local`. If neither is found it prints the one-line install hint above
instead of hanging.

## Slash commands

| Command | What it does | MCP tool |
|---|---|---|
| `/trinity-local:council <question>` | Run a multi-model council (Claude + GPT + Gemini) and report the chairman's verdict — winner + `agreed_claims` / `disagreed_claims`. | `run_council` |
| `/trinity-local:ask <question>` | Route one question to the single best provider for it — cheap, one call. | `ask` |
| `/trinity-local:lens [focus]` | Read your taste lens so this session's answers match how you decide. | `get_persona` |

The full Trinity MCP surface (7 tools + resources) is available to the agent once the
server is connected; these three commands are the everyday verbs.

## Notes

- **No review-gate / Stop hooks.** This plugin only adds commands + the MCP server —
  it never intercepts or gates your responses, so it can't run away with your
  subscription quota.
- **Local-first.** Trinity reads your transcripts and runs councils on *your* machine
  via *your* existing CLI subscriptions. Nothing about the plugin changes that.
- Already ran `trinity-local install-mcp`? The plugin's MCP registration coexists with
  a manually-added one (Claude Code namespaces them); you don't need both, but it's
  harmless.

See <https://keepwhatworks.com> and the main repo for the full picture.
