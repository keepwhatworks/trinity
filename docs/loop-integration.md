# Trinity in the loop — the cross-provider checker the native tools can't ship

"Loop engineering" (Steinberger, Cherny, Osmani) is the move from prompting an agent
to designing the system that prompts it. The primitives have converged — both Claude
Code and Codex now ship: **automations** (the heartbeat), **worktrees** (parallel
isolation), **skills** (project knowledge), **plugins/connectors** (reach your tools),
and **sub-agents** (one makes, a different one checks). Plus a **memory file** that
survives between runs.

Four of those five are commodity infra. The fifth is where Trinity lives.

## The gap: the checker can't be a different lab

The single most valuable structural move in a loop is "one agent makes, a *different*
one checks — ideally a different model," because the maker is too nice grading its own
homework. But:

- A **Claude Code** subagent is always Claude. The `model:` field selects within the family.
- A **Codex** agent is always GPT. Same story.

So the one thing the loop most needs from primitive #5 — a checker from a *different
lab* — is the one thing neither native tool can do. That's Trinity's entire reason to
exist. The council is "spawn members across Claude + GPT + Gemini, synthesize," riding
the user's own subscriptions at $0 marginal, and graded by *their* taste (the lens) —
not generic correctness.

> **Trinity is the verifier sub-agent the maker can't talk past — because it's a different lab.**

## `trinity-local install-agent` — drop Trinity into any loop as the checker

The sibling of `install-mcp` (registers the connector) and `install-skill` (installs
your lens as a skill): `install-agent` registers Trinity as the cross-provider checker.

```bash
trinity-local install-agent            # write the definitions to ~/.trinity/agents/ + print the install
trinity-local install-agent --install  # also place them in ./.claude/agents/ + ./.codex/agents/
trinity-local install-agent --print    # just print both definitions
```

It writes a **`trinity-verify`** sub-agent in each tool's native format:

- `.claude/agents/trinity-verify.md` (YAML frontmatter + system prompt)
- `.codex/agents/trinity-verify.toml` (`developer_instructions`, `sandbox_mode = "read-only"`)

The sub-agent is a thin, **read-only** shim — no Edit/Write, so the checker can't
become the maker. Its whole job is to call `mcp__trinity-local__ask` for a cheap,
single cross-provider, lens-judged second opinion, and escalate to
`mcp__trinity-local__run_council` only on disagreement or high stakes (security, data
loss, anything irreversible). The native sub-agent stays same-lab; **Trinity does the
cross-lab fan-out underneath.**

Two safeguards a real cross-provider dogfood surfaced (a single-model review missed
both; only a different lab caught the second):

- **It gets its own evidence.** A verifier reasoning over whatever the maker hands it is
  a biased relay — a curated diff defeats it even across labs. The shim is instructed to
  obtain the primary evidence itself (read the changed files / run `git diff`) and to
  scope its verdict honestly when it can't.
- **It stays genuinely cross-lab.** `ask`/`run_council` could route the check back to the
  maker's own lab. The shim passes `available_providers` **excluding its own host lab**, so
  the second opinion is always from a different lab than the one that wrote the code.

In a loop, the checker step is then just:

- Claude Code: `@trinity-verify` (or it auto-delegates on description match)
- Codex: spawns by description match

and the verification transparently crosses labs.

### Safety

Following the `install-skill` discipline, `install-agent` writes canonical copies to
Trinity's own `~/.trinity/agents/` and **prints** the one-line install; it never
silently mutates your `.claude`/`.codex`. `--install` is the explicit opt-in. The
verifier also ships inside the **Trinity plugin** (`plugins/trinity-local/agents/`), so
`/plugin install trinity-local@trinity` auto-provides `@trinity-verify` alongside the
MCP connector — the ecosystem's native share unit. (Your lens is private and generated
locally, so `install-skill` ships the *installer*, never a static taste artifact.)

## Where Trinity sits across all the primitives

| Primitive | Trinity |
|---|---|
| Automations / heartbeat | A target: `dream`, the activity-gated lens refresh, nightly `eval-run` |
| Worktrees | Orthogonal — members can run worktree-aware |
| Skills | `install-skill` registers your taste as an ambient `SKILL.md` |
| Plugins | the **Trinity plugin** bundles the MCP connector + verifier agent (+ the skill installer) |
| Plugins / connectors | Trinity **is** an MCP connector + ships as a plugin |
| **Sub-agents** | **`install-agent` — the cross-provider checker** (this doc) |
| Memory / state file | `~/.trinity/` (lens + scoreboards + council outcomes) — the loop's memory, but cross-provider and taste-weighted |

The verifier is only worth walking away from if you trust it. A second opinion from a
different lab, graded by your own taste, is a checker you can actually trust — which is
the whole reason loop engineering lets you leave the room.
