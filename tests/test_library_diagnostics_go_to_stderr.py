"""Library modules write diagnostics to stderr; stdout belongs to CLI and MCP JSON contracts (AGENTS.md). A
long-lived MCP server speaks JSON-RPC on stdout, and a lens build runs inside it: me_builder's progress output
once corrupted MCP parsing that way. Only `status --json` was checked (internal/rule-enforcement.md, 2026-10-26),
and five bare print() calls had reached library code: the regression gate, the eval runner and the share-card
install hint. Every print() outside the command layer now names its stream."""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
COMMAND_LAYER = {"commands"}                     # a verb's handler owns its own stdout
ENTRY_POINTS = {"main.py", "__main__.py"}


def _bare_prints(source: str) -> list[int]:
    return [n.lineno for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
            and not any(k.arg == "file" for k in n.keywords)]


def test_the_detector_sees_a_bare_print():
    assert _bare_prints("print('x')\n") == [1]
    assert _bare_prints("import sys\nprint('x', file=sys.stderr)\n") == []


def test_no_library_module_prints_to_stdout():
    scanned, bad = 0, []
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC)
        if rel.parts[0] in COMMAND_LAYER or rel.name in ENTRY_POINTS:
            continue
        scanned += 1
        bad += [f"{rel}:{line}" for line in _bare_prints(p.read_text(encoding="utf-8"))]
    assert scanned > 100, scanned                # the scan saw the real package
    assert not bad, ("library modules print to stdout, which the MCP server's JSON-RPC stream shares; "
                     "pass file=sys.stderr:\n  " + "\n  ".join(bad))
