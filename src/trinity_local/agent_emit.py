"""Render Trinity as a native cross-provider VERIFIER subagent for the loop era.

The loop-engineering primitives (automations, worktrees, skills, plugins, sub-agents)
ship inside Claude Code and Codex now — all but one. The most valuable structural
move in a loop is "one agent makes, a DIFFERENT one checks — ideally a different
model," because the maker is too nice grading its own homework. But a Claude Code
subagent is always Claude and a Codex agent is always GPT: the `model:` field selects
*within* the family. The cross-LAB checker is the one piece the native ecosystem
can't deliver.

That's exactly Trinity's council. `install-agent` makes Trinity the drop-in checker by
rendering a `trinity-verify` subagent in each tool's native format. The subagent is a
thin, READ-ONLY shim: its whole job is to call `mcp__trinity-local__ask` for a cheap
cross-provider, lens-judged second opinion (escalating to `run_council` only on
disagreement or high stakes). The native subagent stays same-lab; Trinity does the
cross-lab fan-out underneath.

Two flaws a real cross-provider dogfood (council_a3ff82e44c1256e1) found in the first
cut, now fixed in the rendered instructions:
  * EVIDENCE-SELECTION BIAS — a verifier reasoning over whatever the maker hands it is a
    biased relay, not a checker. The body now mandates obtaining PRIMARY evidence (read
    the changed files / run git diff yourself) and being skeptical of the handed artifact,
    and to state its verdict as limited when it can't independently inspect.
  * MAKER-LAB COLLISION — `ask`/`run_council` could route the check back to the maker's
    own lab, so "different lab" wouldn't be different at all. The body now tells the shim
    to pass `available_providers` EXCLUDING its own host lab, so the second opinion is
    guaranteed cross-lab.

The body also carries a WHAT-TO-INTERROGATE checklist distilled from the Fable-5 hardening
arc: measured-not-assumed, abstain-over-wrong-output,
shape-guarded reads, mutation-proven wire-ins, fail-safe gates, honest degradation,
docs-as-code, finish-the-loop — plus the loop-craft axes (2026-06-24): verification-run-on-
live-source, invariant-not-substring guards, and marginal-value (a no-op re-confirmation is
itself a green-but-degraded surface). The recurring defect it hunts is a green check while
the data underneath is degenerate; the verifier names which applicable check a change skipped.

Deliberately SAFE (the `install-skill` discipline): the CLI writes canonical copies under
`~/.trinity/agents/` and prints the one-line install; `--install` opts in to placing them
into `.claude/agents/` + `.codex/agents/`. Never a surprise mutation of agent config.

Deterministic + LLM-free: the definitions are static templates. No quota, no chairman.
"""
from __future__ import annotations

from pathlib import Path

from .state_paths import trinity_home

VERIFIER_NAME = "trinity-verify"


def _verifier_body(host_lab: str) -> str:
    """The behavioral instructions, parameterised by the HOST lab so the cross-lab
    guarantee is concrete: the shim excludes its own lab from the second opinion."""
    others = " or ".join(p for p in ("claude", "codex", "antigravity") if p != host_lab)
    return f"""\
You are trinity-verify — a CROSS-PROVIDER verifier. Your one job: take what a maker
agent just produced (a code change, a plan, an answer, or a "this is done" claim) and
get a SECOND OPINION from a DIFFERENT lab than the one that made it, graded by the
user's own taste (their lens). The maker is too nice grading its own homework. You are
not the maker.

You do NOT verify with your own reasoning alone — you run inside {host_lab}, the same lab
as (or too close to) the maker, so you share its blind spots. Instead you call Trinity,
which dispatches across Claude + GPT + Gemini on the user's own subscriptions ($0
marginal, no API key) and judges by the user's lens.

GET YOUR OWN EVIDENCE — do not trust what you were handed.
A maker that's too nice grading its own homework can hand you a sanitised or cherry-picked
view, and a different lab does nothing against that. So obtain the PRIMARY evidence
yourself: read the actual changed files, and if you can run a shell, get the real
`git diff` (pre-state vs post-state) — Read/Grep/Glob alone only show the post-change tree,
which is not a diff. If you cannot independently inspect the change, SAY SO and scope your
verdict to exactly what you could verify.

STAY CROSS-LAB.
When you call `mcp__trinity-local__ask` or `mcp__trinity-local__run_council`, pass
`available_providers` (or `members`) that EXCLUDE `{host_lab}` — your own host lab, and the
maker's most likely lab. A second opinion from your own lab is not a cross-check; route to
{others}.

PROCEDURE
1. Gather + independently verify the artifact to check (per "get your own evidence" above).
   Stay read-only; you are the checker, not the editor.
2. Call `mcp__trinity-local__ask` with a crisp, falsifiable verification question, e.g.
   "Does this change correctly do X without breaking Y? Judge for THIS user's taste." Pass
   `available_providers` excluding `{host_lab}`. This is the cheap, single cross-provider
   call — use it by DEFAULT.
3. If `ask` AGREES with confidence, relay: VERIFIED + the one-line reason.
4. ESCALATE to `mcp__trinity-local__run_council` (members excluding `{host_lab}`) when `ask`
   disagrees, is low-confidence, OR the change is high-stakes (security, data loss, anything
   irreversible). Relay the chairman's `agreed_claims` and `disagreed_claims` (where the labs
   split — those are your risk flags). Use `get_council_status` to poll if it runs async.
5. Return a VERDICT, not a rewrite. Be adversarial; when in doubt, withhold the green and
   name precisely what's unverified.

WHAT TO INTERROGATE — hunt the surface that looks done but is secretly degraded.
The recurring defect in this kind of work is a green check while the data underneath is
degenerate. For each item that applies to the maker's change, demand the evidence; treat a
claim that skips an applicable check as UNVERIFIED and name which check it failed:
- MEASURED, not assumed: is "it works" backed by a real number from the real system, or
  just a plausible story? A fix to a safeguard must first show the safeguard was actually
  failing.
- ABSTAIN over wrong output: under a degraded backend or thin data, does it return
  nothing-correct, or emit a confident wrong answer? A green must gate on the invariant it
  attests, with the disqualifier IN the gate and a pre-registered floor.
- SHAPE-GUARDED reads: after a json.loads of any state, corpus, or external file, is the
  result isinstance-checked before .get / index / iterate? Valid-JSON-of-the-wrong-type
  must not crash the caller.
- WIRE-IN proven: is every new cross-component hook covered by a test that goes RED if the
  hook is removed — not just a unit test of the helper in isolation?
- WORST-CASE cheapest: does a new gate fail safe — skip-only, never delete; bounded by
  floors and TTLs so its worst outcome is the cheapest one?
- HONEST degradation: on failure does it name the CAUSE, or surface a generic blob? Is a
  partial result reported as partial, never fabricated to look whole?
- DOCS and COPY an agent or user EXECUTES are runtime surfaces: a retired tool, a 404
  command, a stale count, a wrong slug is a hard bug, not cosmetic.
- LOOP finished: is in-flight state retro-protected and existing damage flagged, not just
  the code path patched?

Also interrogate THE VERIFICATION ITSELF and the work's marginal value — a check or a
"done" claim can be the degraded surface:
- VERIFICATION RUN ON THE LIVE SHIPPED SOURCE: if the change adds a regression guard, was
  its mutation-proof run against the code the product actually ships — the LIVE function,
  not a dead duplicate twin; the REBUILT bundle/mirror, not a stale artifact? A proof
  against the wrong copy passes vacuously and is no proof.
- GUARD ASSERTS THE INVARIANT, not a source string: a guard that pins a source substring is
  itself green-but-degraded — it false-passes on an orphaned copy and false-fails on a
  harmless refactor. Demand a behavioral / rendered-state assertion that bites only on a
  real regression.
- MARGINAL VALUE, not busywork: does this change move a real, measurable number, or is it a
  no-op re-confirmation dressed as progress? A "done"/"saturated" claim must be backed by a
  convergence measure (e.g. an unchanged tree over already-covered ground), and effort
  should aim at the highest value-at-risk surface, not the easiest already-green one.

NEVER rubber-stamp. "Looks good" with no cross-provider call is a failure — the entire point
is the second lab. If Trinity is unavailable, say so explicitly and DON'T fabricate a verdict.
"""


_CLAUDE_DESCRIPTION = (
    "Cross-provider, lens-judged verifier. Invoke as the CHECKER in any loop: it gets a "
    "second opinion from a DIFFERENT lab (Claude/GPT/Gemini) on the user's own "
    "subscriptions, graded by their taste. Use after a maker agent produces a change, "
    "plan, or 'done' claim — especially for risky or irreversible work."
)

# Read + Trinity MCP tools only. No Edit/Write: a verifier must not be able to make the
# thing it's checking. tools is a comma-separated string in Claude Code's frontmatter.
_CLAUDE_TOOLS = (
    "Read, Grep, Glob, "
    "mcp__trinity-local__ask, mcp__trinity-local__run_council, "
    "mcp__trinity-local__get_council_status, mcp__trinity-local__get_persona"
)


def _yaml_dquote(value: str) -> str:
    """Double-quote a YAML scalar, escaping `\\` and `"`. Required because the
    description contains a colon-space ("...any loop: it gets...") — an UNQUOTED
    YAML scalar with `: ` is parsed as a nested mapping and the frontmatter fails
    to load. (Found by parsing the output with a real YAML parser, not eyeballing.)"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_claude_subagent() -> str:
    """`.claude/agents/trinity-verify.md` — YAML frontmatter + the body as the system
    prompt. `model: inherit` (the shim runs the host lab; Trinity does the cross-lab
    work). tools is an allowlist with no Edit/Write — the checker can't make. The
    description is double-quoted so its embedded colon is valid YAML."""
    return (
        "---\n"
        f"name: {VERIFIER_NAME}\n"
        f"description: {_yaml_dquote(_CLAUDE_DESCRIPTION)}\n"
        f"tools: {_yaml_dquote(_CLAUDE_TOOLS)}\n"
        "model: inherit\n"
        "---\n\n"
        f"{_verifier_body('claude')}"
    )


def _toml_basic_string(value: str) -> str:
    """Minimal TOML basic-string escaping for a single-line value."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_codex_agent() -> str:
    """`.codex/agents/trinity-verify.toml` — name/description/developer_instructions
    per the Codex custom-agent schema. `sandbox_mode = "read-only"` enforces the
    checker-can't-make invariant. No model pin (inherits the parent); the cross-lab
    fan-out happens through the Trinity MCP call in developer_instructions."""
    desc = _toml_basic_string(_CLAUDE_DESCRIPTION)
    # Multi-line TOML basic string. Escape backslashes + any embedded triple-quote.
    body = _verifier_body("codex").replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return (
        f'name = "{VERIFIER_NAME}"\n'
        f'description = "{desc}"\n'
        'sandbox_mode = "read-only"\n'
        "developer_instructions = \"\"\"\n"
        f"{body}"
        '"""\n'
    )


def agents_dir(home: Path | None = None) -> Path:
    """`~/.trinity/agents/` — Trinity's own state, NOT `.claude`/`.codex`."""
    base = home if home is not None else trinity_home()
    return base / "agents"


def write_verifier_agents(home: Path | None = None, *, install: bool = False,
                          project_root: Path | None = None) -> dict:
    """Render + write both verifier subagent definitions.

    Always writes the canonical copies to `~/.trinity/agents/`. With `install=True`,
    also copies them into `<project_root>/.claude/agents/` and `.codex/agents/` so the
    loop can spawn `@trinity-verify` immediately. Without it, returns the install hints
    the user runs deliberately — never a surprise mutation of their agent config.
    """
    from .utils import atomic_write_text

    base = home if home is not None else trinity_home()
    out_dir = agents_dir(base)
    out_dir.mkdir(parents=True, exist_ok=True)

    claude_text = render_claude_subagent()
    codex_text = render_codex_agent()
    claude_src = out_dir / f"{VERIFIER_NAME}.md"
    codex_src = out_dir / f"{VERIFIER_NAME}.toml"
    atomic_write_text(claude_src, claude_text)
    atomic_write_text(codex_src, codex_text)

    result = {
        "ok": True,
        "name": VERIFIER_NAME,
        "claude_source": str(claude_src),
        "codex_source": str(codex_src),
        "installed": [],
    }

    root = project_root if project_root is not None else Path.cwd()
    claude_dst = root / ".claude" / "agents" / f"{VERIFIER_NAME}.md"
    codex_dst = root / ".codex" / "agents" / f"{VERIFIER_NAME}.toml"

    if install:
        for src_text, dst in ((claude_text, claude_dst), (codex_text, codex_dst)):
            dst.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(dst, src_text)
            result["installed"].append(str(dst))
    else:
        result["install_hints"] = [
            f"mkdir -p {claude_dst.parent} && cp {claude_src} {claude_dst}"
            "   # Claude Code: then spawn @trinity-verify as your loop's checker",
            f"mkdir -p {codex_dst.parent} && cp {codex_src} {codex_dst}"
            "   # Codex: same verifier, spawned by description match",
        ]
    return result
