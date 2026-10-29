"""A new health warning with a fix= cannot ship without an advice-closure test.

AGENTS.md: "Any warning with `fix=` needs an advice-closure test proving the
command clears it." Nothing enforced it, and on 2026-10-26 two shipped fixes
failed to clear their own warnings (config_loadable named install-mcp, which
never writes config.json; dispatch_ready named an install-extension that could
not find a host the check could see). Both are now closed in
tests/test_advice_closure.py.

This scans every function that builds a CheckResult or LensCheck with a fix=
and requires tests/test_advice_closure.py to name it (module and function).
Naming is a floor, not proof: the closure test itself is what has to run the
fix and watch the warning clear. The functions that predate this guard are
frozen below; the set only shrinks.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "trinity_local"
CLOSURE = REPO / "tests" / "test_advice_closure.py"
ADVICE_TYPES = {"CheckResult", "LensCheck"}

WITHOUT_CLOSURE = frozenset({
    "health_checks:_check_core_distilled",
    "health_checks:_check_council_breadth",
    "health_checks:_check_data_degeneracy",
    "health_checks:_check_embedding_backend",
    "health_checks:_check_embedding_coverage",
    "health_checks:_check_lens_built",
    "health_checks:_check_mcp_available",
    "health_checks:_check_provider",
    "health_checks:_check_telemetry_destination",
    "lens_health:_basins",
    "lens_health:_corpus_and_coverage",
    "lens_health:_embedding_backend",
    "lens_health:_freshness",
    "lens_health:_known_issues",
    "lens_health:_ledger_contamination",
    "lens_health:_lens_structure",
    "lens_health:_palate_prospective",
    "lens_health:_preference_collapse",
    "lens_health:_semantic_noise",
})


def _gives_advice(fn: ast.AST) -> bool:
    for call in ast.walk(fn):
        if isinstance(call, ast.Call) and any(k.arg == "fix" for k in call.keywords):
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name in ADVICE_TYPES:
                return True
    return False


def _advice_functions() -> set[str]:
    out = set()
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).with_suffix("").as_posix().replace("/", ".")
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _gives_advice(fn):
                out.add(f"{module}:{fn.name}")
    return out


def _covered(key: str, closure: str) -> bool:
    module, func = key.split(":")
    return module.rsplit(".", 1)[-1] in closure and re.search(rf"\b{re.escape(func)}\b", closure) is not None


def test_a_new_fix_warning_comes_with_a_closure_test():
    closure = CLOSURE.read_text(encoding="utf-8")
    missing = {k for k in _advice_functions() if not _covered(k, closure)} - WITHOUT_CLOSURE
    assert not missing, (
        f"these build a warning with fix= but {CLOSURE.name} never names them:\n  "
        + "\n  ".join(sorted(missing))
        + "\nAdd a closure test there that runs the fix and shows the warning clears.")


def test_the_grandfathered_set_only_shrinks():
    closure = CLOSURE.read_text(encoding="utf-8")
    found = _advice_functions()
    stale = {k for k in WITHOUT_CLOSURE if k in found and _covered(k, closure)}
    gone = WITHOUT_CLOSURE - found
    assert not stale, f"now covered, strike them from WITHOUT_CLOSURE: {sorted(stale)}"
    assert not gone, f"no longer give fix= advice (renamed or removed), strike them: {sorted(gone)}"
    # A count, not just the two checks above: a new uncovered warning added to
    # the set together with its function would satisfy both.
    assert len(WITHOUT_CLOSURE) <= 19, (
        f"WITHOUT_CLOSURE grew to {len(WITHOUT_CLOSURE)}; it was frozen at 23 on 2026-10-26 and only shrinks")


def test_the_scan_sees_the_warnings_it_guards():
    found = _advice_functions()
    assert {"health_checks:_check_config", "health_checks:_check_dispatch_ready",
            "lens_health:_basins"} <= found, sorted(found)
    assert "replay_tasks:find_tasks" not in found, "a ReplayTask's fix is a commit, not advice"
    assert not _covered("health_checks:_check_lens_built", "_check_lens_built_extra health_checks")
    assert not _covered("lens_health:_freshness", "lens_health _check_lens_freshness")
