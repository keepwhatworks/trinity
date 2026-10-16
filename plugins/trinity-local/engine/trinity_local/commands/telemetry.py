"""Handlers for telemetry settings and inspection."""
from __future__ import annotations

import json

from ..refresh import refresh_launchpad
from ..telemetry import (
    build_elo_snapshot,
    disable_telemetry,
    enable_telemetry,
    launchpad_telemetry_state,
    load_telemetry_settings,
    reset_share_install_id,
)


def register(subparsers):
    show = subparsers.add_parser("telemetry-show", help="Show telemetry settings and current local snapshot")
    show.add_argument("--wire", action="store_true",
                      help="Show the EXACT bytes a council would send, and what stays local")
    show.set_defaults(handler=handle_telemetry_show)

    enable = subparsers.add_parser("telemetry-enable", help="Enable anonymous telemetry sharing")
    enable.add_argument("--without-usage-events", action="store_true")
    enable.set_defaults(handler=handle_telemetry_enable)

    disable = subparsers.add_parser("telemetry-disable", help="Disable anonymous telemetry sharing")
    disable.set_defaults(handler=handle_telemetry_disable)

    reset = subparsers.add_parser("telemetry-reset-id", help="Reset the anonymous share install id")
    reset.set_defaults(handler=handle_telemetry_reset_id)


    # Auto-chain / polish-auto / auto-open settings retired 2026-05-17.
    # The auto-chain "always iterate" setting was killed per the
    # simplification arc — users click the auto-chain button on the
    # council page when they want it, no global toggle. Auto-open
    # (open council review page on completion) stays as default-on
    # via the auto_open_council setting, no CLI to flip it.


def handle_telemetry_show(args):
    settings = load_telemetry_settings()
    if getattr(args, "wire", False):
        return _print_wire(settings)
    payload = {
        "settings": settings.to_dict(),
        "snapshot": build_elo_snapshot(),
        "launchpad": launchpad_telemetry_state(),
    }
    print(json.dumps(payload, indent=2))


def _print_wire(settings) -> None:
    """The literal outbound payload, beside the things that never leave.

    A privacy guarantee nobody can inspect is a promise, not a property. This
    prints the exact JSON a council_complete would put on the wire — and, just
    as importantly, names what is NOT in it, because the reassuring half of the
    contract is the absence.
    """
    from ..telemetry import (DISCLOSED_EVENT_PARAMS, build_outbound_event_payload,
                             outbound_client_id)

    example = {"task_type": "coding", "winner": "claude", "member_count": 3,
               "mode": "parallel", "prompt": "THIS WOULD BE STRIPPED",
               "lens_tension": "THIS WOULD BE STRIPPED"}
    wire = {"client_id": outbound_client_id(settings.share_install_id or "«unset»"),
            **build_outbound_event_payload("council_complete", example)}
    print("What a completed council puts on the wire (exact bytes):\n")
    print(json.dumps(wire, indent=2))
    print(f"\nThe allowlist — the ONLY params any event may carry:\n  "
          f"{', '.join(sorted(DISCLOSED_EVENT_PARAMS))}")
    print("\nThe example above deliberately included `prompt` and `lens_tension`.")
    print("They are absent from the payload because the allowlist strips anything")
    print("not named — the guarantee is structural, not a promise to be careful.\n")
    print("What NEVER leaves this machine:")
    for line in ("your prompts and every word of your transcripts",
                 "your lens, tensions, vocabulary, and core.md",
                 "council answers, chairman synthesis, and disagreement text",
                 "file paths, project names, hostnames, IP-derived identifiers",
                 "the local install id itself — only a hash of it and the current month ships"):
        print(f"  · {line}")
    print("\nThe outbound id ROTATES every month, so events from this install")
    print("cannot be linked across months by anyone, including us.")
    # Both switches: telemetry-disable clears `sharing_enabled`, which is what the send path
    # checks; this line read only `share_usage_events` and said ON after a disable.
    on = settings.sharing_enabled and settings.share_usage_events
    print(f"\nSharing is currently: {'ON' if on else 'OFF'}"
          "   (trinity-local telemetry-disable turns it off; "
          "telemetry-reset-id starts a new anonymous id)")


def handle_telemetry_enable(args):
    settings = enable_telemetry(share_usage_events=not args.without_usage_events)
    portal_path = refresh_launchpad()
    print(json.dumps({"settings": settings.to_dict(), "portal_path": str(portal_path)}, indent=2))


def handle_telemetry_disable(args):
    settings = disable_telemetry()
    portal_path = refresh_launchpad()
    print(json.dumps({"settings": settings.to_dict(), "portal_path": str(portal_path)}, indent=2))


def handle_telemetry_reset_id(args):
    settings = reset_share_install_id()
    portal_path = refresh_launchpad()
    print(json.dumps({"settings": settings.to_dict(), "portal_path": str(portal_path)}, indent=2))
