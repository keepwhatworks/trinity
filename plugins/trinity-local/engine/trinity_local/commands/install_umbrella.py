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

import json

from types import SimpleNamespace


_INSTALL_VERBS: list[tuple[str, str]] = [
    # `install` itself does the setup; these are the pieces it is made of, plus
    # the optional extras. One plain line each.
    ("install-mcp", "Register Trinity's MCP server in Claude Code, Codex, agy and Cursor (install runs this)."),
    ("install-agent", "Add a trinity-verify sub-agent to .claude/agents and .codex/agents."),
    ("install-skill", "Write your lens as a SKILL.md other agents can load."),
    ("install-extension", "Connect the Chrome extension, which saves chats from claude.ai, chatgpt.com and gemini."),
    ("install-hooks", "Add Claude Code hooks that capture each turn as it happens."),
    ("install-launcher", "Add a desktop launcher for the local launchpad (Linux, Windows)."),
    ("uninstall", "Remove Trinity's registrations and wrappers; your data in ~/.trinity stays."),
]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "install",
        help="Set Trinity up for Claude Code, Codex and agy (registers it in each, then "
             "checks). --check only reports. Optional verbs: install-skill, install-agent, "
             "install-extension, install-hooks, install-launcher, uninstall.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Read-only: for Claude Code, Codex and agy, show whether each is installed, "
             "signed in and registered, how many transcripts Trinity reads, and which "
             "model each council seat runs. Changes nothing.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="With --check: print the report as JSON.")
    parser.set_defaults(handler=handle_install_umbrella)


def handle_install_umbrella(args: SimpleNamespace) -> int:
    """`install` sets Trinity up for Claude Code, Codex and agy: registers the MCP
    server in each, seeds a config for a new user, and prints the check.
    `--check` only reports (setup_check.py). The optional verbs are listed last."""
    if getattr(args, "check", False):
        from ..setup_check import format_check, run_check
        report = run_check()
        print(json.dumps(report, indent=2) if getattr(args, "as_json", False) else format_check(report))
        return 0
    # No flag: do the setup. Register Trinity in every CLI's MCP config (the same
    # writer as install-mcp, idempotent, backs up before writing), seed a config
    # for a brand-new user, then show the read-only check of what is now true.
    from types import SimpleNamespace as _NS

    from ..setup_check import ensure_new_user_config, format_check, run_check
    from .install import handle_install_mcp
    handle_install_mcp(_NS(scope="user"))
    note = ensure_new_user_config()
    if note:
        print(note)
    print()
    print(format_check(run_check()))
    print()
    print("Optional (run by name):")
    for name, summary in _INSTALL_VERBS[1:]:
        print(f"  trinity-local {name:<18} {summary}")
    print()
    print(
        "Setup is done; these verbs are optional. Re-check any time "
        "(read-only): `trinity-local install --check`."
    )
    return 0
