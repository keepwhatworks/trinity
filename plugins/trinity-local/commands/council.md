---
description: Run a multi-model council (Claude + GPT + Gemini) and synthesize the answer your taste would pick.
argument-hint: <a hard question or task>
---

Run a Trinity council on the following, then report the chairman's verdict — the
picked winner, the `agreed_claims`, and the `disagreed_claims` (with why each one
matters):

$ARGUMENTS

Call the `mcp__trinity-local__run_council` tool from the connected Trinity MCP
server. Treat the chairman's `agreed_claims` / `disagreed_claims` as the source of
truth. If the argument is empty, ask the user what to run the council on first.
