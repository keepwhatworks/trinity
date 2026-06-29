"""Pyflakes-as-test: catch undefined-name bugs that mock-heavy unit
tests miss.

Earned 2026-05-16: `me_builder.build_me_via_lens_pipeline` had three
references to `chairman_name` (the variable in scope was `chairman`).
Every unit test that touched lens-build did so via
`monkeypatch.setattr` of the whole function — so the NameError
never fired in tests. Only a real-user `trinity-local lens-build`
would have hit it.

Principle #5 ("Real-data validation is the substantive test") at the
static-analysis tier. Pyflakes catches what mocked unit tests
can't.

Scope: undefined names ONLY. The unused-import / unused-variable
warnings pyflakes also emits are useful but noisier — they can be
cleaned in dedicated passes. Catching `NameError-at-runtime` is the
high-stakes target.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_no_undefined_names_in_src():
    """Run pyflakes against src/trinity_local/ and fail on any
    `undefined name` line. Other pyflakes warnings (unused imports,
    f-strings without placeholders) are tolerated — they're style
    issues, not crashes.
    """
    src = REPO / "src" / "trinity_local"
    result = subprocess.run(
        ["python", "-m", "pyflakes", str(src)],
        capture_output=True,
        text=True,
    )
    # pyflakes returns 1 when it finds issues — that's expected. We only
    # care about the substantive ones.
    undefined_lines = [
        line for line in result.stdout.splitlines()
        if "undefined name" in line
    ]
    assert not undefined_lines, (
        "pyflakes found undefined-name references in src/. These are "
        "runtime NameErrors that fire on the real user's machine but "
        "don't trip unit tests (which mock the offending functions).\n\n"
        + "\n".join(undefined_lines)
        + "\n\nFix the names or pre-define the variables before use."
    )


def test_all_src_modules_import_cleanly():
    """Smoke-import every module under src/trinity_local/.

    Catches module-level crashes that pyflakes misses: type errors
    on annotations evaluated eagerly, circular imports surfacing
    only at import time, dataclass field defaults that depend on
    missing imports, etc. These are the kinds of bugs that would
    crash `trinity-local --help` (and every other entry point)
    without ever showing up in a test that doesn't import the
    affected module.

    Same structural-gate spirit as the pyflakes scan above — but
    catches a wider class (runtime errors at module init time, not
    just static lookup failures).

    Skips `__init__.py` modules (they're imported transitively when
    we import any submodule) and any `__main__` entry points that
    have side effects on import.
    """
    import importlib

    src = REPO / "src" / "trinity_local"
    failures: list[tuple[str, str]] = []
    for py in sorted(src.rglob("*.py")):
        # Skip __init__ — imported transitively when submodules load.
        if py.name == "__init__.py":
            continue
        # Skip capture_host: it has __main__ side effects (reads stdin,
        # writes stdout) that we don't want to trigger.
        if py.name == "capture_host.py":
            continue
        rel = py.relative_to(REPO / "src").with_suffix("")
        mod_name = str(rel).replace("/", ".")
        # Don't `sys.modules.pop(mod_name)` before re-importing — that
        # pollutes the suite (other modules hold refs to the OLD
        # module object; monkeypatch on the NEW one fails to reach
        # them). If the module is already in sys.modules, it loaded
        # cleanly during pytest collection; if it isn't,
        # importlib.import_module loads it fresh. Either covers the
        # invariant this test guards.
        try:
            importlib.import_module(mod_name)
        except Exception as exc:
            failures.append((mod_name, f"{type(exc).__name__}: {exc}"))

    assert not failures, (
        "Module-level import failures in src/trinity_local/. Each "
        "would crash any entry point that touches the broken module:\n\n"
        + "\n".join(f"  {m}: {e}" for m, e in failures)
        + "\n\nLikely causes: undefined name at module top-level, "
        "circular import, missing optional dep imported unconditionally."
    )
