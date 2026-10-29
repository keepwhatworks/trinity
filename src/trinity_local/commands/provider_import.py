"""Shared front-end for the provider-side import loops (`lens-import` +
`eval-import`).

Both loops are the same shape — publish a prompt, the provider pastes back JSON,
ingest it — and historically duplicated ~45 lines of byte-identical input
handling: read raw (path / `--from-json` stdin), parse + dict-guard the JSON,
resolve the `source_provider` (a `--provider` CLI flag overrides the payload),
and FAIL LOUD on any list field that's present-but-not-a-list (coercing it to []
would report success while silently dropping every tension/rejection — data loss
on the taste signal). Only the map / dedup / save / output steps differ, so only
THAT scaffolding is unified here; the kind-specific middle stays in each handler.

A fix to the shape-guard now lands in both loops at once. `import_provider_memory`
(the MCP tool) reaches these through the CLI handlers, so it inherits this too.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Union


def read_provider_import(args, *, list_fields: tuple[str, ...]) -> Union[tuple[dict, str], int]:
    """Read + validate a provider-import payload.

    Returns ``(payload, source_provider)`` on success, or an ``int`` exit code
    (1 = file not found, 2 = bad input / shape) AFTER printing the error — so the
    caller does ``r = read_provider_import(...); if isinstance(r, int): return r``.

    `list_fields` are the payload keys that MUST be a list when present (e.g.
    ``("rejections",)`` / ``("tensions", "orderings")``); a present-but-wrong-type
    one fails loudly (exit 2), honouring ``args.as_json`` for that structured error.
    """
    raw: str | None = None
    if getattr(args, "from_json", False):
        raw = sys.stdin.read()
    elif getattr(args, "path", None):
        p = Path(args.path).expanduser()
        if not p.exists():
            print(f"error: file not found: {p}", file=sys.stderr)
            return 1
        raw = p.read_text(encoding="utf-8")
    else:
        print("error: pass a path positional arg or --from-json (stdin).", file=sys.stderr)
        return 2

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: input is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(payload, dict):
        print("error: top-level JSON must be an object", file=sys.stderr)
        return 2

    # --provider CLI flag wins over the payload's source_provider field: provider
    # JSON sometimes omits it, and the user may want to re-attribute. A non-string
    # source_provider (number/list) would crash .strip(), so coerce defensively.
    cli_override = getattr(args, "provider", None)
    sp = payload.get("source_provider")
    source_provider = (
        cli_override or (sp if isinstance(sp, str) else None) or "unknown"
    ).strip().lower()

    # A list field PRESENT but the WRONG TYPE (an agent emitted a JSON string where
    # an array was expected) must not be silently coerced to [] — that reports
    # success while dropping every item, indistinguishable from a legitimately-empty
    # payload (guard_shape_not_just_parse). Absent → fine; present-but-not-a-list →
    # fail loudly so the caller fixes the shape.
    for field in list_fields:
        value = payload.get(field)
        if value is not None and not isinstance(value, list):
            err = {
                "ok": False,
                "source_provider": source_provider,
                "error": f"`{field}` must be a list, got {type(value).__name__}",
            }
            if getattr(args, "as_json", False):
                print(json.dumps(err, indent=2))
            else:
                print(f"error: {err['error']}", file=sys.stderr)
            return 2

    return payload, source_provider


def packaged_prompt(name: str) -> Path | None:
    """Path to a prompt shipped as package data (trinity_local/data/prompts/<name>), or None."""
    from importlib import resources
    try:
        ref = resources.files("trinity_local").joinpath("data/prompts", name)
        return Path(str(ref)) if ref.is_file() else None
    except (ModuleNotFoundError, FileNotFoundError):
        return None


# The heading in each packaged prompt doc that starts the paste-ready body.
PROMPT_BODY_MARKER = "## The prompt — copy below this line"


def print_provider_prompt(doc_name: str, env_var: str, *, with_instructions: bool) -> int:
    """Print a packaged provider prompt for `lens-prompt` / `eval-prompt`.

    `$env_var` overrides the packaged doc. By default prints only the body after
    PROMPT_BODY_MARKER, so `lens-prompt | pbcopy` is paste-ready;
    `with_instructions` prints the whole doc. Returns the exit code.
    """
    override = os.environ.get(env_var)
    if override:
        p = Path(override).expanduser()
        doc = p if p.exists() else None
    else:
        doc = packaged_prompt(doc_name)
    if doc is None:
        print(
            f"error: couldn't find {doc_name}. Set {env_var} to its absolute "
            "path if you're running from a non-checkout install.",
            file=sys.stderr,
        )
        return 1
    text = doc.read_text(encoding="utf-8")
    if with_instructions:
        sys.stdout.write(text)
        return 0
    idx = text.find(PROMPT_BODY_MARKER)
    if idx < 0:
        # The doc lost its marker: print it whole rather than nothing, and say so.
        print(
            "warning: prompt-body marker not found in doc; emitting full file. "
            f"Update {doc_name} to restore the '{PROMPT_BODY_MARKER}' anchor.",
            file=sys.stderr,
        )
        sys.stdout.write(text)
        return 0
    # Skip past the marker line itself + the blank line after it.
    after = text[idx:].split("\n", 2)
    body = after[-1] if len(after) == 3 else text[idx:]
    sys.stdout.write(body)
    return 0
