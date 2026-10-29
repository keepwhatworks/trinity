"""No monkeypatch.setattr in tests passes raising=False.

With raising=False, a setattr whose target attribute does not exist creates it and
patches nothing the code reads, and the test still passes. Running the suite with a
logger on those calls (2026-10-27) found six: five sat beside the patch that does take
effect, because the module imports the name inside the function, and one was the only
patch of its intent and never applied. With the default raising=True the same mistake
fails on the first run with an AttributeError.

To patch a name a module imports inside a function, patch the module it is imported
from. If a target can really be absent, check hasattr() first and patch it plainly.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _raising_false_setattrs(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
        and any(
            kw.arg == "raising" and isinstance(kw.value, ast.Constant) and kw.value.value is False
            for kw in node.keywords
        )
    ]


def test_no_setattr_passes_raising_false():
    offenders = [
        f"{path.name}:{line}"
        for path in sorted(TESTS.glob("*.py"))
        for line in _raising_false_setattrs(path)
    ]
    assert not offenders, (
        "monkeypatch.setattr(..., raising=False) silently patches nothing when the target "
        f"is missing; drop the flag (see this file's docstring): {offenders}"
    )


def test_the_scan_sees_a_raising_false_setattr(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(\n"
        "        'pkg.mod.name', lambda: 1, raising=False\n"
        "    )\n"
        "    monkeypatch.delenv('X', raising=False)\n",
        encoding="utf-8",
    )
    assert _raising_false_setattrs(sample) == [2]
