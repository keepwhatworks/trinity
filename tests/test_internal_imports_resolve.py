"""Every `from trinity_local... import name` in src/ and scripts/ names something that exists.

pyflakes cannot see this: an import inside a function is checked only when that line runs.
scripts/signature.py imported vocabulary.find_synonyms from inside its synonyms operation for
five months after the function was deleted (81bc1587, 2026-05-30), and the operation raised
ImportError the whole time. This test imports each module named and looks each name up,
wherever the import sits. An import inside a `try` whose handler catches ImportError (a bare
`except:`, or ImportError, ModuleNotFoundError, Exception or BaseException) is an intentional
fallback that cannot crash, and is skipped: e.g. telemetry's _ga4_bundled, present only in
release builds. (On 2026-10-28 that was the only guarded import that did not resolve.)
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PKG = "trinity_local"
_CATCHES_IMPORT = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _guarded(parents: list[ast.AST]) -> bool:
    for p in parents:
        if isinstance(p, ast.Try):
            for h in p.handlers:
                if h.type is None:
                    return True
                types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
                if any(ast.unparse(t) in _CATCHES_IMPORT for t in types):
                    return True
    return False


def _imports(path: Path):
    """Yield (lineno, module, names) for each package import in `path`, unguarded ones only."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    in_src = SRC in path.parents
    package: list[str] = list(path.relative_to(SRC).with_suffix("").parts)[:-1] if in_src else []
    stack: list[tuple[ast.AST, list[ast.AST]]] = [(tree, [])]
    while stack:
        node, parents = stack.pop()
        for child in ast.iter_child_nodes(node):
            stack.append((child, parents + [node]))
        if not isinstance(node, ast.ImportFrom) or _guarded(parents):
            continue
        if node.level:
            if not in_src:
                continue
            base = package[: len(package) - (node.level - 1)]
            module = ".".join(base + ([node.module] if node.module else []))
        elif node.module and node.module.startswith(PKG):
            module = node.module
        else:
            continue
        yield node.lineno, module, [a.name for a in node.names if a.name != "*"]


def _unresolved(paths, root: Path = REPO) -> list[str]:
    bad = []
    for path in paths:
        for lineno, module, names in _imports(path):
            where = f"{path.relative_to(root)}:{lineno}"
            try:
                mod = importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001 - report, don't crash the scan
                bad.append(f"{where} cannot import {module}: {type(exc).__name__}")
                continue
            for name in names:
                if hasattr(mod, name):
                    continue
                try:
                    importlib.import_module(f"{module}.{name}")
                except Exception:  # noqa: BLE001
                    bad.append(f"{where} from {module} import {name}: no such name")
    return bad


def test_every_package_import_resolves():
    paths = sorted((SRC / PKG).rglob("*.py")) + sorted((REPO / "scripts").glob("*.py"))
    bad = _unresolved(paths)
    assert not bad, "imports of names that do not exist (they fail only when the line runs):\n" + "\n".join(bad)


def test_the_scan_sees_a_deleted_name_inside_a_function(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def op():\n"
        "    from trinity_local.vocabulary import find_synonyms\n"
        "    return find_synonyms\n"
        "\n"
        "def fallback():\n"
        "    try:\n"
        "        from trinity_local import _not_in_this_build\n"
        "    except ImportError:\n"
        "        return None\n",
        encoding="utf-8",
    )
    bad = _unresolved([sample], root=tmp_path)
    assert bad ==["sample.py:2 from trinity_local.vocabulary import find_synonyms: no such name"]
