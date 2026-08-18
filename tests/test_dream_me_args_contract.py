"""`lens --deep` must hand handle_me_build every field it reads directly.

A `lens --deep` run on 2026-08-18 spent 2.3 hours and 419 chairman calls and
changed 87 bytes. Its me/ stage — the one that writes lens_registry.json,
memories/lens.md and topics.json — died immediately with

    AttributeError: 'types.SimpleNamespace' object has no attribute 'sample_size'

because commands/dream.py builds a partial args namespace
(provider/limit/stages/force) while handle_me_build reads args.sample_size and
args.k_basins DIRECTLY. Everything else it touches goes through getattr with a
default, so those two were the entire gap. The build then reported
discover/synthesize/vocabulary/distill and completed "successfully" with its
central stage dead (res_068).

Producer asserts a shape, consumer expects another, nothing checks — the same
failure as res_045 (provenance written where the ledger never reads) and res_051
(the ledger's writer skipping dispatched_model). This is the third instance.

The required set is derived by AST rather than hardcoded, so ADDING a new
`args.X` to handle_me_build fails this test until dream.py supplies it. A
hardcoded list would pass forever while the contract rotted.

Mutation-proven 2026-08-18: removing sample_size from dream.py's me_args REDs
this test.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "trinity_local" / "commands"


def _fn(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _required_args_fields() -> set[str]:
    """EVERY field handle_me_build reads as `args.X`.

    The first version returned `direct - guarded`, subtracting fields also read via
    getattr(args, "X", default). That is wrong and it cost a second failed run: a
    field read BOTH ways still crashes on the direct read. `dry_run` is read via
    getattr in one branch and as `args.dry_run` at me.py:393, so the subtraction
    erased it from the required set and the guard passed while the contract was
    still broken.

    A getattr elsewhere is not a shield for a direct read somewhere else. Any
    `args.X` at all makes X required.
    """
    fn = _fn(SRC / "me.py", "handle_me_build")
    return {n.attr for n in ast.walk(fn)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "args"}


def _dream_supplies() -> set[str]:
    """Keyword names on the SimpleNamespace dream.py hands to handle_me_build."""
    src = (SRC / "dream.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)):
            continue
        if getattr(n.value.func, "id", "") != "SimpleNamespace":
            continue
        if any(getattr(t, "id", "") == "me_args" for t in n.targets):
            return {k.arg for k in n.value.keywords if k.arg}
    raise AssertionError("no `me_args = SimpleNamespace(...)` found in dream.py — "
                         "this guard cannot see the contract it is checking")


def test_dream_supplies_every_field_me_build_reads_directly():
    required, supplied = _required_args_fields(), _dream_supplies()
    missing = required - supplied
    assert not missing, (
        f"commands/dream.py's me_args is missing {sorted(missing)}, which "
        "handle_me_build reads as args.X without a getattr default. A `lens --deep` "
        "run will crash its me/ stage and still report success — 2.3 hours and 419 "
        "chairman calls for an 87-byte change (res_068). Either add the field to "
        "me_args, or read it via getattr(args, ..., default) in handle_me_build."
    )


def test_the_guard_can_actually_see_both_sides():
    """A contract test that finds nothing on either side passes vacuously."""
    assert _required_args_fields(), "no directly-read args fields found — parser broke"
    assert _dream_supplies(), "no me_args keywords found — parser broke"
