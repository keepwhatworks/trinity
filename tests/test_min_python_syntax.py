"""Guard: every src/ module must PARSE on the declared minimum Python.

`pyproject.toml` declares `requires-python = ">=3.10"` and classifies 3.10 / 3.11
/ 3.12. But dev + CI run 3.12, so syntax that only *parses* on a newer interpreter
sails through the whole green suite while being a hard `SyntaxError` — an
unimportable package — for a 3.10/3.11 user. This bit us 2026-06-02: three page
templates inlined `json.dumps(...).replace("<", "\\u003c")` into an f-string, and
a backslash in an f-string replacement field is a SyntaxError before Python 3.12
(PEP 701). `council_review` / `launchpad_template` are imported on every CLI call
and at MCP startup, so Trinity was unimportable on 3.10/3.11 — discovered only by
booting the plugin launcher under an older interpreter.

We can't always run a 3.10/3.11 interpreter in CI, so this guard detects the
known post-3.10 GRAMMAR additions structurally via the 3.12 AST:
  • PEP 701 (3.12): a backslash inside an f-string replacement field.
  • PEP 695 (3.12): `type X = ...` aliases and generic `class C[T]` / `def f[T]`.
  • PEP 654 (3.11): `except*` exception groups.

If this fails, the flagged construct won't parse on the floor we promise. Either
rewrite it to 3.10-compatible form (e.g. hoist the backslash out of the f-string —
see design_system.page_data_script_json) or, if the new syntax is wanted, RAISE
`requires-python` honestly and update the classifiers.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "trinity_local"

# This guard detects post-3.10 grammar via the 3.12 AST — so the AST-based checks
# can ONLY run on a 3.12+ interpreter. ast.TypeAlias (3.12) / ast.TryStar (3.11)
# don't exist on the floor, and a backslash-f-string can't even be parsed there.
# getattr(..., ()) makes isinstance a no-op on older Pythons (so the scan runs but
# simply can't see those constructs — which is fine: on 3.10/3.11 they'd be REAL
# import-time SyntaxErrors, surfaced directly). The backslash-detector mutation
# test parses a 3.12-only sample, so it's skipped below the floor.
_AST_TYPE_ALIAS = getattr(ast, "TypeAlias", ())
_AST_TRY_STAR = getattr(ast, "TryStar", ())


def _violations(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    out: list[str] = []

    for node in ast.walk(tree):
        # PEP 701 — backslash in an f-string replacement field (3.12+ only).
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    seg = ast.get_source_segment(text, value.value) or ""
                    if "\\" in seg:
                        out.append(
                            f"{path.name}:{value.value.lineno} backslash in f-string "
                            f"expression (PEP 701, 3.12+): {{{seg[:60]}}}"
                        )
        # PEP 695 — `type X = ...` alias (3.12+). getattr-guarded: ast.TypeAlias
        # doesn't exist below 3.12, where this check is a no-op (real `type X`
        # use would be an import-time SyntaxError on the floor anyway).
        elif _AST_TYPE_ALIAS and isinstance(node, _AST_TYPE_ALIAS):
            out.append(f"{path.name}:{node.lineno} `type` alias statement (PEP 695, 3.12+)")
        # PEP 695 — generic class/def with `[T]` type params (3.12+).
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if getattr(node, "type_params", None):
                out.append(
                    f"{path.name}:{node.lineno} generic `{node.name}[...]` type params "
                    "(PEP 695, 3.12+)"
                )
        # PEP 654 — `except*` exception groups (3.11+; our floor is 3.10).
        elif _AST_TRY_STAR and isinstance(node, _AST_TRY_STAR):
            out.append(f"{path.name}:{node.lineno} `except*` exception group (PEP 654, 3.11+)")

    return out


def test_src_parses_on_declared_minimum_python():
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 50, f"expected the full src tree, found {len(files)} files"
    violations: list[str] = []
    for f in files:
        violations.extend(_violations(f))
    assert not violations, (
        "src/ uses syntax newer than the declared `requires-python = >=3.10`, so the "
        "package is a hard SyntaxError (unimportable) for 3.10/3.11 users while CI "
        "(3.12) stays green:\n  " + "\n  ".join(violations) + "\n"
        "Rewrite to 3.10-compatible form, or raise requires-python + classifiers honestly."
    )


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="a backslash-in-f-string sample can't be PARSED below 3.12 (the very "
    "SyntaxError this guard detects), so the mutation test only runs on 3.12+",
)
def test_guard_detects_a_backslash_fstring(tmp_path):
    """Mutation guard: prove the scanner bites the exact bug it was written for.
    Build the bad source so its f-string replacement field literally contains a
    backslash — the construct that is a SyntaxError before 3.12."""
    bs = chr(92)  # a single backslash character
    bad = tmp_path / "bad.py"
    bad.write_text(f"s = '<'\nout = f\"{{s.replace('<', '{bs}u003c')}}\"\n", encoding="utf-8")
    # Sanity: the written file really contains a backslash inside the f-string braces.
    assert bs + "u003c" in bad.read_text(encoding="utf-8")
    assert _violations(bad), "scanner failed to flag a backslash-in-f-string expression"

    # And a clean file (backslash only in a plain string literal) must NOT trip it.
    good = tmp_path / "good.py"
    good.write_text(f"ESC = '{bs}u003c'\nout = f\"{{s.replace('<', ESC)}}\"\n", encoding="utf-8")
    assert not _violations(good), "scanner false-flagged a backslash in a plain string literal"


# stdlib names/attributes added AFTER the 3.10 floor. Using them is valid SYNTAX
# (so test_src_parses_on_declared_minimum_python can't see them) but
# ImportErrors/AttributeErrors at RUNTIME on 3.10/3.11 — while passing on dev/CI's
# 3.12 AND the 3.14 forward-compat run. Curated, qualified patterns to avoid false
# positives; extend when stdlib grows. (tomllib is the realistic temptation for the
# Codex-config writer — keep using string/regex or guard with a tomli fallback.)
_POST_310_STDLIB = {
    "import tomllib":     "3.11 (tomllib — use tomli + try/except, or string/regex)",
    "tomllib.":           "3.11 (tomllib)",
    "datetime.UTC":       "3.11 (datetime.UTC — use timezone.utc)",
    "itertools.batched":  "3.12 (itertools.batched)",
    "asyncio.TaskGroup":  "3.11 (asyncio.TaskGroup)",
    "typing.override":    "3.12 (typing.override)",
    "(StrEnum)":          "3.11 (enum.StrEnum)",
    "import StrEnum":     "3.11 (enum.StrEnum)",
    ".process_cpu_count": "3.13 (os.process_cpu_count)",
    "contextlib.chdir":   "3.11 (contextlib.chdir)",
}


def test_src_uses_no_stdlib_newer_than_declared_floor():
    """Runtime complement to the SYNTAX guard above: the OTHER way to break
    requires-python='>=3.10' is to call a stdlib name added in 3.11/3.12+ — it
    ImportErrors/AttributeErrors on 3.10/3.11 while passing on dev's 3.12 and the
    3.14 job (both have it). The AST scan can't see runtime calls, so this curated
    text scan covers them. Verified clean 2026-06-03 (full suite also passed on
    3.12 + 3.14). Mutation-verified by injecting `import tomllib`."""
    violations: list[str] = []
    for f in sorted(SRC.rglob("*.py")):
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines()
        for pat, ver in _POST_310_STDLIB.items():
            if pat in text:
                for i, line in enumerate(lines, 1):
                    if pat in line:
                        violations.append(f"{f.name}:{i}  {pat!r} — added in {ver}")
                        break
    assert not violations, (
        "src/ uses stdlib newer than requires-python='>=3.10' — it breaks 3.10/3.11 at "
        "RUNTIME (CI's 3.12 + the 3.14 forward-compat run won't catch it):\n  "
        + "\n  ".join(violations)
        + "\nUse a 3.10-compatible alternative or a version-guarded fallback."
    )
