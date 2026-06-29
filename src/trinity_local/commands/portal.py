"""Launchpad-page (`portal-html`) and council-review command handlers.

The CLI verb stays `portal-html` for back-compat with the
pre-task-#93 rename (portal_*.py → launchpad_*.py); the underlying
write produces `~/.trinity/portal_pages/launchpad.html` (directory
name kept stable for on-disk back-compat with existing user
installs). The module name `commands/portal.py` matches the CLI
verb prefix. See task #93 (Tier 2 #4 rename) for the rationale.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote

from ..council_review import write_unified_council_page
from ..council_runtime import load_council_outcome, load_council_outcome_or_exit, load_prompt_bundle, load_prompt_bundle_or_exit
from ..notifications import open_path
from ..refresh import refresh_launchpad
from ..state_paths import review_pages_dir, trinity_home
from ..task_runtime import load_task_record, load_task_record_or_exit


def register(subparsers):
    pp = subparsers.add_parser("portal-html", help="Generate a bookmarkable static launchpad page")
    pp.add_argument("--title", default="Trinity · Ask all three")
    pp.add_argument("--open-browser", action="store_true")
    pp.set_defaults(handler=handle_portal_html)

    orp = subparsers.add_parser("open-review", help="Open an existing review page for a task or outcome")
    orp.add_argument("--task", default=None)
    orp.add_argument("--outcome", default=None)
    orp.add_argument("--path", default=None)
    orp.set_defaults(handler=handle_open_review)

    rlp = subparsers.add_parser(
        "review-link",
        help="Print mobile-safe links for a council review page",
    )
    rlp.add_argument("council_id", help="Council run id, e.g. council_abc123")
    rlp.add_argument("--json", dest="as_json", action="store_true")
    rlp.set_defaults(handler=handle_review_link)

    sp = subparsers.add_parser(
        "serve",
        help="Serve ~/.trinity over HTTP so the launchpad works at http://localhost:PORT (alternative to file://)",
    )
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open-browser", action="store_true", help="Open the launchpad in a browser tab")
    sp.set_defaults(handler=handle_serve)


def handle_portal_html(args):
    from ..launchpad_data import dispatch_readiness

    path = refresh_launchpad(title=args.title)
    readiness = dispatch_readiness()
    opened = open_path(path) if args.open_browser else False

    # Phase 5: surface the dispatch hint when nothing is wired AND the
    # user asked us to open the page. The launchpad's own banner (Phase 4)
    # is the in-page version; this is the second visibility lane so
    # headless `portal-html --open-browser` runs aren't silent. Hint
    # goes to stderr to keep stdout JSON-parseable.
    if args.open_browser and not readiness["ready"] and readiness["recommended_action"]:
        print(f"hint: {readiness['recommended_action']}", file=sys.stderr)

    print(json.dumps({
        "path": str(path),
        "opened": opened,
        "dispatch": readiness,
    }, indent=2))


def handle_open_review(args):
    target = None
    if args.path:
        target = Path(args.path).expanduser().resolve()
    elif args.task:
        task = load_task_record_or_exit(args.task)
        if task.review_page_path:
            target = Path(task.review_page_path).expanduser().resolve()
    elif args.outcome:
        outcome = load_council_outcome_or_exit(args.outcome)
        target = review_pages_dir() / f"{outcome.council_run_id}.html"
    if target is None:
        raise SystemExit("error: open-review requires --path, --task, or --outcome with a recorded review page")
    opened = open_path(target)
    print(json.dumps({"path": str(target), "opened": opened}, indent=2))


def _review_link_payload(council_id: str, review_path: Path) -> dict:
    safe_id = quote(council_id, safe="")
    resolved = review_path.expanduser().resolve()
    return {
        "council_id": council_id,
        "review_path": str(resolved),
        "file_url": resolved.as_uri(),
        "deep_link": f"trinity://review/{safe_id}",
        "content_source": "local_review_artifact_or_paired_desktop",
        "url_privacy": "URLs contain only the council id; review content stays local unless the user explicitly exports it.",
    }


def handle_review_link(args):
    outcome = load_council_outcome_or_exit(args.council_id)
    bundle = load_prompt_bundle_or_exit(outcome.bundle_id)
    review_path = write_unified_council_page(bundle, outcome)
    payload = _review_link_payload(outcome.council_run_id, review_path)
    if args.as_json:
        print(json.dumps(payload, indent=2))
        return

    print(f"Review file: {payload['file_url']}")
    print(f"App link:    {payload['deep_link']}")
    print("Privacy:     URL carries only council_id; content loads from local review artifact or paired desktop.")


class _NoCacheHTMLHandler(http.server.SimpleHTTPRequestHandler):
    """Same as SimpleHTTPRequestHandler but disables HTTP caching for
    .html (and .json status) responses. Without this, Chrome aggressively
    caches the launchpad page and ships of new code (rollback fixes,
    refreshed page_data) appear stale until the user hits Cmd+Shift+R.
    Static assets (vendor JS, share PNGs) keep their default caching.
    """

    def end_headers(self):
        path = self.path.split("?", 1)[0]
        if path.endswith((".html", ".json")):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()


def handle_serve(args):
    """Serve ~/.trinity over HTTP. Same launchpad/review pages render under
    file:// (double-click) AND http://localhost:PORT — page-data URLs are
    relative so both contexts resolve correctly. Useful when the user wants
    a stable URL to share with screen recorders, devtools, or playwright."""
    home = trinity_home()
    refresh_launchpad()  # ensure pages are fresh before serving

    handler = lambda *a, **kw: _NoCacheHTMLHandler(*a, directory=str(home), **kw)
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", args.port), handler)
    except OSError as exc:
        print(f"error: could not bind 127.0.0.1:{args.port} — {exc}", file=sys.stderr)
        raise SystemExit(1)

    url = f"http://127.0.0.1:{args.port}/portal_pages/launchpad.html"
    print(f"Trinity is serving {home} at http://127.0.0.1:{args.port}")
    print(f"Launchpad: {url}")
    print("Press Ctrl-C to stop.")
    # serve_forever() blocks forever, so flush NOW — otherwise the URL (the whole
    # point of `serve`'s output) sits in stdout's block buffer until Ctrl-C in any
    # non-tty context (`serve > log &`, piped, or a harness capturing output), and
    # the user can't find where Trinity is serving (observed 2026-06-06: redirected
    # serve logged nothing while answering HTTP 200s).
    sys.stdout.flush()
    if args.open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.shutdown()
