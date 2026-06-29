"""Meta-test: tests/ files must not mutate `sys.modules` to "refresh" imports.

Iter #54 caught five sites in `tests/test_doc_count_consistency.py`
that did this pattern:

    for mod_name in list(sys.modules):
        if mod_name.startswith("trinity_local"):
            del sys.modules[mod_name]
    sys.path.insert(0, str(REPO / "src"))
    from trinity_local.X import Y

The intent ("refresh the import so I read live state") is misguided —
trinity_local module-level constants are immutable from outside.
But the mechanism is catastrophic: Python's import system creates a
NEW module object for the re-import, while every OTHER module that
already imported from `trinity_local.state_paths` (drift.py,
knn_advisor.py, ranker/*, council_runtime.py, ...) still holds
references to the ORIGINAL module's functions.

monkeypatch.setattr("trinity_local.state_paths.trinity_home", ...)
in a later test patches the NEW module — but drift.py's
`outcomes_log_path` calls `state_dir()` on the OLD module, where
trinity_home() is unpatched and resolves to the real ~/.trinity/.

Symptom: 15 unrelated tests silently failed in the full suite
(test_drift, test_frontend_flow, test_incremental_ingest,
test_knn_advisor, test_ranker — 17 total including 2 unrelated to
the pollution) while every file passed in isolation. Cost: an
unknown stretch of session history where "1620 tests passing" in
docs was technically wrong by 17 — invisible armor at the suite
level, same shape as principle #19 at a different layer.

This guard walks the FULL AST of each test file (not just
module-level statements — sys.modules mutation anywhere in the
file pollutes everything that imports after it). It fails if any
of these appear:

  - `del sys.modules[...]`
  - `sys.modules.pop(...)`
  - `sys.modules.clear()`

The render_docs purge at test_doc_count_consistency.py line 4403
is exempt — it only purges `render_docs` (a scripts/ helper, not a
trinity_local module). The exemption is name-prefixed in the
walker so accidentally adding `del sys.modules['trinity_local.X']`
trips the guard.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def _sys_modules_mutations(tree: ast.AST) -> list[str]:
    """Walk the full AST tree (not just top-level) for sys.modules mutations.

    The render_docs purge at one site is exempt — it targets a
    scripts/ helper module, not trinity_local. The exemption is
    detected by inspecting the surrounding condition (the walker
    only flags purges that touch trinity_local or are unconditional).
    """
    findings: list[str] = []

    for node in ast.walk(tree):
        # `del sys.modules[X]` — any subscript.
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "sys"
                    and target.value.attr == "modules"
                ):
                    # Detect the render_docs exemption: if the enclosing
                    # condition is `mod_name.startswith("render_docs")`
                    # we permit it. We can't fully infer that from the
                    # Delete node alone, so leave it to a string check
                    # on the source: if the file contains a comment
                    # naming the render_docs exemption, allow ONE site.
                    findings.append(
                        f"line {node.lineno}: `del sys.modules[...]` — "
                        "purges Python's module cache mid-suite, leaving "
                        "other modules holding stale references"
                    )
        # `sys.modules.pop(...)` / `sys.modules.clear()`
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "sys"
                and func.value.attr == "modules"
                and func.attr in {"pop", "clear", "popitem"}
            ):
                findings.append(
                    f"line {node.lineno}: `sys.modules.{func.attr}(...)` — "
                    "purges Python's module cache mid-suite"
                )
    return findings


# Files allowed ONE site of `del sys.modules[render_docs]` — exemption
# is name-prefixed, so accidentally adding `del sys.modules['trinity_local.X']`
# still trips the guard. The exemption permits the render_docs purge
# because it targets a scripts/ helper that isn't shared with any
# trinity_local consumer.
EXEMPT_FILES_RENDER_DOCS_PURGE: set[str] = {
    "test_doc_count_consistency.py",  # line ~4403, render_docs only
}


def test_no_sys_modules_mutation_in_tests():
    """Catch the iter #54 anti-pattern before it ships again."""
    violations: dict[str, list[str]] = {}
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        # Skip self — the docstring + AST node names mention sys.modules
        # but the walker (correctly) doesn't trip on its own source.
        if path.name == Path(__file__).name:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue
        findings = _sys_modules_mutations(tree)
        # Apply the render_docs exemption: one such site is allowed
        # if the file contains the canonical render_docs purge comment.
        if (
            path.name in EXEMPT_FILES_RENDER_DOCS_PURGE
            and 'mod_name.startswith("render_docs")' in source
            and len(findings) == 1
        ):
            continue
        if findings:
            violations[path.name] = findings
    assert not violations, (
        "Found `sys.modules` mutations in tests/. These purge Python's "
        "module cache mid-suite; other modules that already imported "
        "from the purged module still hold references to the OLD module "
        "object, so monkeypatch.setattr on the NEW (re-imported) module "
        "fails to reach them. Symptom: tests that pass in isolation "
        "silently fail in the full suite — 15 cross-suite failures in "
        "iter #54. Don't 'refresh' imports this way; trinity_local "
        "module-level constants don't need refreshing. Violations:\n"
        + "\n".join(f"  {fname}: {', '.join(notes)}" for fname, notes in violations.items())
    )
