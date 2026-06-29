"""`trinity-local install-agent` — register Trinity as a native cross-provider
verifier subagent for Claude Code (`.claude/agents/`) and Codex (`.codex/agents/`),
the sibling of `install-mcp` / `install-skill`.

The loop-engineering "checker that's a different model" the native tools can't do:
a Claude Code subagent is always Claude, a Codex agent always GPT. This writes a
`trinity-verify` shim whose job is to call Trinity (which fans across labs by the
user's lens). See `agent_emit.py` for the rendering + safety discipline.
"""
from __future__ import annotations

import json


def register(subparsers):
    p = subparsers.add_parser(
        "install-agent",
        help="Register Trinity as a native cross-provider 'trinity-verify' subagent for Claude Code + Codex (the loop's checker) — sibling of install-mcp",
    )
    p.add_argument(
        "--print", dest="print_only", action="store_true",
        help="Print the rendered subagent definitions to stdout instead of writing files.",
    )
    p.add_argument(
        "--install", action="store_true",
        help="Also copy the definitions into ./.claude/agents/ + ./.codex/agents/ (opt-in; otherwise prints the one-line install).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Print the result as JSON.",
    )
    p.set_defaults(handler=handle_agent_emit)


def handle_agent_emit(args):
    """Render + write the trinity-verify cross-provider verifier subagent. Default:
    canonical copies under ~/.trinity/agents/ + printed install hints (never silently
    mutates .claude/.codex). --install opts in to placing them in the project."""
    import sys

    from ..agent_emit import (
        VERIFIER_NAME,
        render_claude_subagent,
        render_codex_agent,
        write_verifier_agents,
    )

    if getattr(args, "print_only", False):
        print(f"# === .claude/agents/{VERIFIER_NAME}.md ===\n")
        print(render_claude_subagent())
        print(f"\n# === .codex/agents/{VERIFIER_NAME}.toml ===\n")
        print(render_codex_agent())
        return 0

    result = write_verifier_agents(install=getattr(args, "install", False))

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    print(f"  Wrote the cross-provider verifier subagent '{VERIFIER_NAME}':")
    print(f"    Claude Code : {result['claude_source']}")
    print(f"    Codex       : {result['codex_source']}")
    if result.get("installed"):
        print("\n  Installed into the project — spawn it as your loop's checker:")
        for path in result["installed"]:
            print(f"    {path}")
        print(f"\n  Claude Code:  @{VERIFIER_NAME}   (or it auto-delegates on description match)")
        print("  Codex:        spawns by description match")
    else:
        print("\n  Install it as your loop's checker (deliberate, one-time):", file=sys.stderr)
        for hint in result.get("install_hints", []):
            print(f"    {hint}", file=sys.stderr)
    return 0 if result.get("ok") else 1
