"""trinity-local install — discovery surface for install verbs.

Symmetric with `commands/debug.py`. The cron spec's 5-user-facing list
mentions `install` as a single verb, but today the install module
registers five separate subparsers (install-mcp, install-hooks,
install-extension, install-launcher, uninstall). This umbrella
advertises them so `trinity-local install` is the discoverable entry
point; the bare names still work (the launchpad's install-extension
dispatch and the README's `install-mcp` examples don't break).

Module name is `install_umbrella` to avoid colliding with the
existing `install` module that registers the *-mcp/*-hooks/etc.
subparsers.
"""
from __future__ import annotations

from types import SimpleNamespace


_INSTALL_VERBS: list[tuple[str, str]] = [
    # The three loop-primitive install verbs — connector / skill / sub-agent —
    # are the surface that fits Trinity into Claude Code + Codex natively.
    (
        "install-mcp",
        "Register Trinity's MCP server (the CONNECTOR) in installed harnesses "
        "(Claude Code, Codex CLI, Antigravity, Cursor). The most "
        "common first-run verb.",
    ),
    (
        "install-skill",
        "Register your lens as an agent-loadable SKILL.md — the artifact other "
        "agents load to answer in your voice (~/.trinity/skills/your-taste/).",
    ),
    (
        "install-agent",
        "Register the cross-provider `trinity-verify` SUB-AGENT into "
        ".claude/agents + .codex/agents — the loop's checker (a second opinion "
        "from a different lab, graded by your lens).",
    ),
    (
        "install-extension",
        "Register the Chrome extension's Native Messaging manifest "
        "for browser capture + auto-update.",
    ),
    (
        "install-hooks",
        "Optional. Install Claude Code hooks for richer captures.",
    ),
    (
        "install-launcher",
        "Optional. Drop a platform launcher (Linux .desktop, "
        "Windows Start Menu .url) for the launchpad.",
    ),
    (
        "uninstall",
        "Remove the MCP registrations + wrappers. Data in "
        "~/.trinity/ is preserved.",
    ),
]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "install",
        help="Install verbs: install-mcp (the connector, most common), "
             "install-skill (lens→skill), install-agent (the cross-provider "
             "checker), install-extension, install-hooks, install-launcher, uninstall.",
    )
    parser.set_defaults(handler=handle_install_umbrella)


def handle_install_umbrella(args: SimpleNamespace) -> int:
    """No subcommand → list the install verbs. The user picks one and
    runs `trinity-local <verb>` directly."""
    print("Trinity install verbs (run directly by name):")
    for name, summary in _INSTALL_VERBS:
        print(f"  trinity-local {name}")
        print(f"    {summary}")
    print()
    print(
        "Most users only need `trinity-local install-mcp` on first "
        "install. The other verbs are situational."
    )
    return 0
