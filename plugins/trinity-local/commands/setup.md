---
description: Check that Trinity is set up for Claude Code, Codex and agy, and say the one thing to fix.
argument-hint: (no arguments)
---

If `trinity-local` is on PATH, run `trinity-local install --check` and relay its
report: for each CLI whether it is installed, signed in and registered, how many of
its transcripts Trinity reads, and which model its council member runs. It changes
nothing. Each gap comes with the command that fixes it; offer to run those.

If `trinity-local` is not on PATH, this plugin is running Trinity's MCP server on
its own, so the tools still work here. Say so, and that `trinity-local install`
(from the one-line installer on keepwhatworks.com) adds the CLI and registers
Trinity in Codex and agy too.

Do not run a council or verify here: this command only checks the setup. End with
the single most important next step, not a wall of checks.
