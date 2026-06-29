"""File-change watchdog for the MCP server.

The MCP server caches imports at process boot. After in-session edits to any
module under `src/trinity_local/`, the running server keeps serving stale code
— and there's no MCP-protocol way to ask it to reload. Three sessions of
real testing today were poisoned by this.

This watchdog polls source files in a daemon thread; when any `.py` mtime
changes, it logs to stderr and calls `os._exit(0)`. Claude Code's MCP
launcher auto-respawns the server, which loads current code on startup.

Caveats:
  - Claude Code may cache the tool list from the first connection. Adding
    NEW tools (vs. modifying existing ones) may still require a Claude Code
    restart to make the new tools visible to the harness.
  - We don't try `importlib.reload()` — Python's module cache + already-
    bound references make in-place reload unreliable. Process exit is
    cleaner and Trinity's startup is fast (<1s typically).

Disabled by default. Enable by setting `TRINITY_MCP_WATCH=1` (only safe in
development; never enable for shipped users).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path


# Poll interval: 1s feels responsive for save → reload cycles, low CPU.
_POLL_SECONDS = 1.0


def _src_root() -> Path:
    """Path to `src/trinity_local/`. Calibrated against this file's location."""
    return Path(__file__).resolve().parent


def _snapshot_mtimes(root: Path) -> dict[str, float]:
    """Collect mtime of every .py file under root (recursive)."""
    result: dict[str, float] = {}
    for path in root.rglob("*.py"):
        try:
            result[str(path)] = path.stat().st_mtime
        except OSError:
            # File deleted between rglob and stat — fine, skip.
            continue
    return result


def _watch_loop(root: Path) -> None:
    initial = _snapshot_mtimes(root)
    print(f"[trinity-mcp-watchdog] watching {len(initial)} files under {root}", file=sys.stderr)
    while True:
        time.sleep(_POLL_SECONDS)
        current = _snapshot_mtimes(root)

        # Any file added, removed, or modified triggers a restart.
        added = set(current) - set(initial)
        removed = set(initial) - set(current)
        modified = {p for p in set(current) & set(initial) if current[p] != initial[p]}

        if added or removed or modified:
            change_summary = []
            if modified:
                change_summary.append(f"modified={len(modified)}")
            if added:
                change_summary.append(f"added={len(added)}")
            if removed:
                change_summary.append(f"removed={len(removed)}")
            sample = next(iter(modified or added or removed), "?")
            print(
                f"[trinity-mcp-watchdog] source change detected ({', '.join(change_summary)}); "
                f"sample: {os.path.basename(sample)}; exiting for fresh restart",
                file=sys.stderr,
            )
            # os._exit so we bypass any pending stdio writes; Claude Code's
            # MCP launcher reconnects to a fresh process. sys.exit would
            # raise SystemExit and let stdio_server's context manager wait
            # for the connection to drain — that's the wrong behavior here.
            os._exit(0)


def start_watchdog_if_enabled() -> None:
    """Start the watchdog thread when TRINITY_MCP_WATCH=1.

    Safe to call multiple times — only one thread starts."""
    if os.environ.get("TRINITY_MCP_WATCH", "").strip().lower() not in ("1", "true", "yes"):
        return

    if getattr(start_watchdog_if_enabled, "_started", False):
        return

    root = _src_root()
    thread = threading.Thread(
        target=_watch_loop,
        args=(root,),
        name="trinity-mcp-watchdog",
        daemon=True,
    )
    thread.start()
    start_watchdog_if_enabled._started = True  # type: ignore[attr-defined]
