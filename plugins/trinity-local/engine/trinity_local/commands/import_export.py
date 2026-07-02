"""``trinity-local import-export PATH`` — bulk Takeout / web-export import (#148).

The retired ``seed-from-taste-terminal`` command (sunset 2026-05-27)
required a personal-rig directory layout
(``~/projects/taste-terminal/data/exports/...``). End users with their
own Takeout / ChatGPT-export downloads have arbitrary paths, so they
couldn't use it. This is its replacement.

This command auto-detects the export type by probing structure:

- File ``conversations.json`` with array of dicts containing
  ``mapping`` → ChatGPT export
- File ``conversations.json`` with array of dicts containing
  ``chat_messages`` → Claude.ai export
- File ``MyActivity.html`` (or directory containing one under
  ``My Activity/Gemini Apps/``) → Gemini Takeout

If the path is a directory, walks it looking for any of the above
patterns. Each detected source is parsed and the SessionRecords are
indexed via the shared Stage 0–1 pipeline in
``src/trinity_local/ingest_helpers.py`` (``existing_prompt_node_ids``
+ ``stage_session`` + ``flush_chunk``).

This is the backend primitive the launchpad bulk-import UI will call.
Slice 1 of task #148 ships the CLI; the launchpad UI follows as
slice 2.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def register(subparsers):
    parser = subparsers.add_parser(
        "import-export",
        help="Bulk-import a Takeout / web-export at any path. Auto-detects ChatGPT / Claude.ai / Gemini-Takeout (#148).",
    )
    # Positional preserved for CLI ergonomics; `--path` mirror added so
    # the capture-host action dispatcher (--flag VALUE shape) can fire
    # this from the launchpad without a positional-arg special case.
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="File OR directory containing the export. For a directory, recursively probes for known export files. Same as --path.",
    )
    parser.add_argument(
        "--path",
        dest="path_flag",
        default=None,
        help="Alias for the positional path arg. Used when invoked via capture-host action dispatch.",
    )
    parser.add_argument(
        "--source", default=None,
        choices=["claude_ai", "chatgpt", "gemini_takeout"],
        help="Force a specific parser instead of auto-detecting. Useful when probe heuristics get it wrong.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Probe + print detection result without ingesting. Useful for debugging which parser would run.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max sessions per detected source (default: all).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=64,
        help="Embedding batch size (default 64).",
    )
    parser.add_argument(
        "--dim", type=int, default=768,
        help="Embedding dimension (default 768 — Nomic).",
    )
    parser.add_argument(
        "--progress", action="store_true",
        help=(
            "Stream per-chunk progress lines to stderr "
            "(default off — JSON on stdout stays clean for "
            "the launchpad dispatch path)."
        ),
    )
    parser.set_defaults(handler=handle_import_export)


def detect_exports(root: Path) -> list[dict[str, Any]]:
    """Probe ``root`` for known export shapes.

    Returns a list of ``{source, path, hint}`` dicts describing each
    detected export. Multiple may be returned when a directory contains
    several Takeout extracts (e.g., zip1 + zip2). Empty list when
    nothing matches — caller surfaces a "no exports found" hint.

    Detection precedence (when ambiguous, both are returned):
    1. Claude.ai: ``conversations.json`` whose first element has
       ``chat_messages`` key
    2. ChatGPT: ``conversations.json`` whose first element has
       ``mapping`` key
    3. Gemini: ``MyActivity.html`` under ``My Activity/Gemini Apps/``
       (full Takeout layout) or directly at root
    """
    detected: list[dict[str, Any]] = []

    if root.is_file():
        kind = _detect_single_file(root)
        if kind:
            detected.append({"source": kind, "path": str(root), "hint": "explicit file"})
        return detected

    # Directory walk — bounded depth so we don't traverse the user's
    # whole home dir if they pass /. Cap at 6 levels (enough for
    # nested Takeout zips: Takeout/My Activity/Gemini Apps/MyActivity.html)
    for path in _bounded_walk(root, max_depth=6):
        if path.is_file():
            kind = _detect_single_file(path)
            if kind:
                detected.append({"source": kind, "path": str(path), "hint": str(path.relative_to(root))})

    return detected


def _detect_single_file(path: Path) -> str | None:
    """Return the export kind for a single file, or None if unknown."""
    name = path.name.lower()
    if name == "conversations.json":
        return _detect_conversations_json(path)
    if name == "myactivity.html" or "myactivity" in name and name.endswith(".html"):
        return "gemini_takeout"
    return None


def _detect_conversations_json(path: Path) -> str | None:
    """conversations.json is used by both ChatGPT and Claude.ai exports
    — distinguish by inspecting first element's keys."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            head = fh.read(8192)  # enough to see first conversation's keys
    except (OSError, UnicodeDecodeError):
        # A non-UTF8 file named conversations.json (a coincidental name, a
        # corrupt export) is not a valid export — skip it, don't crash the whole
        # auto-detect walk on it (UnicodeDecodeError is a ValueError, NOT an
        # OSError — guard_shape_not_just_parse).
        return None
    # Cheap structural check: look for distinguishing keys in the
    # first ~8KB. Both exports start with `[{...`; we just need to see
    # which key shows up first.
    chatgpt_pos = head.find('"mapping"')
    claude_pos = head.find('"chat_messages"')
    if chatgpt_pos >= 0 and (claude_pos < 0 or chatgpt_pos < claude_pos):
        return "chatgpt"
    if claude_pos >= 0:
        return "claude_ai"
    return None


def _bounded_walk(root: Path, *, max_depth: int) -> Iterator[Path]:
    """rglob with depth cap. Skips common noise dirs."""
    skip_names = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
    base_depth = len(root.parts)
    try:
        for item in root.rglob("*"):
            try:
                depth = len(item.parts) - base_depth
            except ValueError:
                continue
            if depth > max_depth:
                continue
            if any(part in skip_names for part in item.parts):
                continue
            yield item
    except (OSError, PermissionError):
        return


def _parse_for_source(source: str, path: Path):
    """Dispatch to the right parser from ingest.py based on detected source."""
    from ..ingest import parse_chatgpt_export, parse_claude_ai_export, parse_gemini_takeout_html

    if source == "claude_ai":
        return parse_claude_ai_export(path)
    if source == "chatgpt":
        return parse_chatgpt_export(path)
    if source == "gemini_takeout":
        return parse_gemini_takeout_html(path)
    raise ValueError(f"unknown source: {source}")


def _zero_yield_warnings(
    detected: list[dict[str, Any]],
    per_source_counts: dict[str, int],
    limit: int | None,
    prompts_indexed: int = 0,
) -> list[dict[str, Any]]:
    """Honest-degradation (#238) warnings for zero-yield imports.

    Two shapes produce the "green while degenerate" trap where ``ok: true`` +
    all-zero totals reads as a successful import when nothing actually landed:

    1. DETECTED-but-empty — the filename / structure signature matched yet the
       source parsed to 0 sessions (an empty-activity export, or vendor HTML
       drift the parser doesn't recognize). One warning per 0-seen source.
    2. PARSED-but-nothing-staged — sessions parse fine (seen > 0) but 0 prompts
       index: every thread is already in the index (a re-import), or the
       threads carry no user turns to stage. Found by the 360-loop live probe
       2026-07-02: this shape previously returned bare zeros with NO warning,
       the exact silent dead-end shape 1 was built to prevent.

    Suppressed entirely when ``limit <= 0`` (a 0/negative ``--limit``
    legitimately yields nothing, so a 0-count there is expected, not a
    degradation).
    """
    if limit is not None and limit <= 0:
        return []
    warnings: list[dict[str, Any]] = []
    for source, seen in per_source_counts.items():
        if seen:
            continue
        warnings.append({
            "source": source,
            "paths": [e["path"] for e in detected if e["source"] == source],
            "message": (
                f"Detected a {source} export but extracted 0 sessions — the "
                "file may have no activity, or its structure isn't one this "
                "parser recognizes. Nothing was imported for this source. Try "
                "--source to force a parser, or confirm the export isn't empty."
            ),
        })
    total_seen = sum(per_source_counts.values())
    if total_seen > 0 and prompts_indexed == 0:
        warnings.append({
            "source": "all",
            "message": (
                f"Parsed {total_seen} session(s) but indexed 0 prompts — "
                "either everything here is already in the index (a re-import "
                "adds nothing new) or these threads carry no user prompts to "
                "stage. Nothing new was imported."
            ),
        })
    return warnings


def handle_import_export(args):
    # Accept either the positional path OR --path (capture-host
    # action-dispatch path uses --path because the host's allowlist
    # entry format is --flag VALUE pairs only).
    path_arg = args.path or getattr(args, "path_flag", None)
    if not path_arg:
        print(json.dumps({"ok": False, "error": "path is required (pass as positional or --path)"}, indent=2))
        raise SystemExit(2)
    root = Path(path_arg).expanduser().resolve()
    if not root.exists():
        print(json.dumps({"ok": False, "error": f"path not found: {root}"}, indent=2))
        raise SystemExit(1)

    # Detection phase
    if args.source:
        # User forced a parser — skip auto-detect, treat path as
        # explicit input to that parser
        detected = [{"source": args.source, "path": str(root), "hint": "forced via --source"}]
    else:
        detected = detect_exports(root)

    if not detected:
        print(json.dumps({
            "ok": False,
            "error": "no exports detected",
            "hint": (
                "Expected: a file conversations.json (ChatGPT or Claude.ai), "
                "or a Gemini Takeout extract containing My Activity/Gemini "
                "Apps/MyActivity.html. Pass --source to force a parser if "
                "auto-detect gets it wrong."
            ),
            "path": str(root),
        }, indent=2))
        raise SystemExit(1)

    if args.dry_run:
        print(json.dumps({
            "ok": True,
            "mode": "dry-run",
            "detected": detected,
        }, indent=2))
        return

    # Ingest phase — shared chunked indexer in ingest_helpers.py
    from ..ingest_helpers import (
        existing_prompt_node_ids,
        flush_chunk,
        stage_session,
    )

    existing_ids = existing_prompt_node_ids()
    chunk: list[dict] = []
    prompts_indexed = 0
    windows_indexed = 0
    transcripts_indexed = 0
    sessions_indexed = 0
    sessions_seen = 0

    def _flush():
        nonlocal prompts_indexed, windows_indexed, transcripts_indexed, sessions_indexed
        if not chunk:
            return
        p, w, t = flush_chunk(chunk, existing_ids, dim=args.dim, batch_size=args.batch_size)
        prompts_indexed += p
        windows_indexed += w
        transcripts_indexed += t
        sessions_indexed += sum(
            1 for s in chunk
            if not s["already_indexed"] and s["keepers"]
        )
        if args.progress:
            import sys
            print(
                f"  ingested {sessions_indexed} sessions / "
                f"{prompts_indexed} prompts",
                file=sys.stderr,
                flush=True,
            )
        chunk.clear()

    chunk_size_target = 32
    per_source_counts: dict[str, int] = {}
    for entry in detected:
        source = entry["source"]
        path = Path(entry["path"])
        n_for_source = 0
        for session in _parse_for_source(source, path):
            if args.limit is not None and n_for_source >= args.limit:
                break
            sessions_seen += 1
            n_for_source += 1
            staged = stage_session(session, existing_ids)
            if staged is None:
                continue
            chunk.append(staged)
            if len(chunk) >= chunk_size_target:
                _flush()
        per_source_counts[source] = per_source_counts.get(source, 0) + n_for_source

    _flush()  # tail

    result: dict[str, Any] = {
        "ok": True,
        "detected": detected,
        "per_source_sessions_seen": per_source_counts,
        "totals": {
            "sessions_seen": sessions_seen,
            "sessions_indexed": sessions_indexed,
            "prompts_indexed": prompts_indexed,
            "windows_indexed": windows_indexed,
            "transcripts_indexed": transcripts_indexed,
        },
    }
    # After a successful import, point the user at the next step. Building the
    # lens is the actual value, and the bare index counts don't tell a non-coder
    # what to do with their freshly-indexed history — the acquisition path
    # otherwise dead-ends on a JSON object. Only emit when something landed; a
    # zero-yield import gets the honest `warnings` below instead (#238), which is
    # the more useful signal there.
    if prompts_indexed > 0:
        result["next_steps"] = [
            "trinity-local lens         — build your taste lens from the imported history",
            "trinity-local portal-html  — open the launchpad to see your lens and run a council",
        ]
    warnings = _zero_yield_warnings(detected, per_source_counts, args.limit, prompts_indexed)
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, indent=2))
