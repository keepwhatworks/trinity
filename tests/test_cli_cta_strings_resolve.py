"""CTA-validity class guard — every backtick-wrapped `trinity-local <verb>`
command a user is TOLD TO RUN, anywhere in the live engine source, must
resolve against the real argparse parser's subcommand choices.

Founder symptom (the Iter 265 → 266 CTA-validity arc): a user-facing
command STRING drifts to a verb that no longer exists while every test
stays green, so a one-click / error-path CTA dead-ends on
`error: argument {...}: invalid choice: '<verb>'`.

  - Iter 265 (tip ladder): the `view-lens` rung told the user to run
    `trinity-local portal` — the module is `portal`, the verb is
    `portal-html`. Guarded by `test_every_tip_cta_is_a_real_cli_subcommand`
    (the tip-ladder instance).
  - Iter 266 (this guard): the lens-build "no enabled provider" RuntimeError
    told the user to run `trinity-local config show` — there is NO `config`
    subcommand (the diagnostic verb is `status`). That message is surfaced
    verbatim by `lens-setup` (`✗ Lens build failed: {exc}`), so the user
    was nudged at a real failure toward a command that errors again.

This guard GENERALIZES the tip-ladder check across EVERY live surface
module: it parses each `src/trinity_local/**/*.py` file's string LITERALS
(not comments — those legitimately reference retired verbs in historical
NOTEs) and resolves every backtick-wrapped `trinity-local <verb>` against
the live parser. Backtick-wrapping is the in-codebase convention for "this
is a runnable command to copy/paste" — it distinguishes a CTA from prose
like "remove trinity-local from X" or a docstring's historical reference.

Behavioral, render-independent. Mutation-proven: reverting the Iter 266
fix (`status` → `config show`) makes this RED with
`DRIFT: 'config' at me_builder.py`.
"""
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"

# A backtick-wrapped command starting with `trinity-local <verb>`. The
# backtick is load-bearing: it's how the codebase marks "this is a command
# the user runs" (CTAs, error-path "Run `...`" nudges), separating it from
# prose ("trinity-local from X") and bare-word mentions.
_CTA = re.compile(r"`trinity-local[ \t]+([a-z][a-z0-9-]*)")
# JS line comments live inside the memory-viewer's big template literals;
# they reference retired verbs historically and are never rendered.
_JS_COMMENT = re.compile(r"//[^\n]*")


def _live_subcommands() -> set[str]:
    from trinity_local.main import build_parser

    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    pytest.fail("build_parser() exposes no _SubParsersAction")


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """ids() of Constant nodes that are module/class/function docstrings —
    docstrings legitimately carry historical `trinity-local <retired-verb>`
    NOTEs (e.g. `decision-log`) and are never shown to an end user."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _scan_cta_verbs() -> list[tuple[str, str, int]]:
    """(verb, relpath, lineno) for every backtick-wrapped `trinity-local
    <verb>` CTA in a string LITERAL across the live engine source —
    docstrings + JS line-comments excluded."""
    out: list[tuple[str, str, int]] = []
    repo = SRC.parents[1]
    for path in SRC.rglob("*.py"):
        if path.name == "retired_names.py":
            # The retired-name registry deliberately enumerates dead verbs.
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        docs = _docstring_constant_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docs:
                continue
            text = _JS_COMMENT.sub("", node.value)
            for m in _CTA.finditer(text):
                verb = m.group(1)
                if verb == "install":
                    # `install` is a real subcommand AND a prose noun; the
                    # tip/skill guards drop it for the same reason.
                    continue
                out.append(
                    (verb, str(path.relative_to(repo)), getattr(node, "lineno", 0))
                )
    return out


def test_every_cli_cta_string_resolves_to_a_real_subcommand():
    """Every backtick-wrapped `trinity-local <verb>` a user is told to run,
    anywhere in the live engine, must be a verb the parser accepts.

    Mutation: revert Iter 266 (`trinity-local status` →
    `trinity-local config show` in me_builder.py) → this RED on
    `DRIFT: config`. RED on the Iter 265 instance too if the tip CTA grew a
    backticked form.
    """
    valid = _live_subcommands()
    # (A) the surface is present + the parser is non-vacuous — guard bites.
    assert "status" in valid and "portal" not in valid, (
        "live parser sanity broke: `status` must exist, `portal` must not "
        "(it's `portal-html`)"
    )
    ctas = _scan_cta_verbs()
    assert ctas, (
        "found ZERO backtick-wrapped `trinity-local <verb>` CTAs in the live "
        "engine source — the scan regressed (it should find error-path nudges "
        "like the lens-build `Run `trinity-local status`` message)."
    )
    # (B) the discriminating assertion: every CTA verb resolves.
    drifted = sorted(
        {(verb, loc, ln) for verb, loc, ln in ctas if verb not in valid}
    )
    assert not drifted, (
        "user-facing CTA(s) name a `trinity-local <verb>` that is NOT a real "
        "subcommand — running it errors `invalid choice` (the Iter 265 "
        "`portal` / Iter 266 `config show` dead-end class):\n  "
        + "\n  ".join(f"`trinity-local {v}`  at {loc}:{ln}" for v, loc, ln in drifted)
        + f"\nValid subcommands: {sorted(valid)}"
    )
