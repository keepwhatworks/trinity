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
    save_telemetry_settings,
)


def register(subparsers):
    show = subparsers.add_parser("telemetry-show", help="Show telemetry settings and current local snapshot")
    show.set_defaults(handler=handle_telemetry_show)

    enable = subparsers.add_parser("telemetry-enable", help="Enable anonymous telemetry sharing")
    enable.add_argument("--endpoint", default=None)
    enable.add_argument("--without-usage-events", action="store_true")
    enable.add_argument("--without-elo", action="store_true")
    enable.set_defaults(handler=handle_telemetry_enable)

    disable = subparsers.add_parser("telemetry-disable", help="Disable anonymous telemetry sharing")
    disable.set_defaults(handler=handle_telemetry_disable)

    reset = subparsers.add_parser("telemetry-reset-id", help="Reset the anonymous share install id")
    reset.set_defaults(handler=handle_telemetry_reset_id)

    endpoint = subparsers.add_parser("telemetry-endpoint", help="Set or clear the telemetry endpoint")
    endpoint.add_argument("--url", default=None)
    endpoint.add_argument("--clear", action="store_true")
    endpoint.set_defaults(handler=handle_telemetry_endpoint)

    # Auto-chain / polish-auto / auto-open settings retired 2026-05-17.
    # The auto-chain "always iterate" setting was killed per the
    # simplification arc — users click the auto-chain button on the
    # council page when they want it, no global toggle. Auto-open
    # (open council review page on completion) stays as default-on
    # via the auto_open_council setting, no CLI to flip it.


def handle_telemetry_show(args):
    settings = load_telemetry_settings()
    payload = {
        "settings": settings.to_dict(),
        "snapshot": build_elo_snapshot(),
        "launchpad": launchpad_telemetry_state(),
    }
    print(json.dumps(payload, indent=2))


def handle_telemetry_enable(args):
    settings = enable_telemetry(
        endpoint=args.endpoint,
        share_usage_events=not args.without_usage_events,
        share_elo_summaries=not args.without_elo,
    )
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


def handle_telemetry_endpoint(args):
    settings = load_telemetry_settings()
    if args.clear:
        settings.endpoint = None
    elif args.url:
        settings.endpoint = args.url
    path = save_telemetry_settings(settings)
    portal_path = refresh_launchpad()
    print(json.dumps({"settings": settings.to_dict(), "path": str(path), "portal_path": str(portal_path)}, indent=2))




