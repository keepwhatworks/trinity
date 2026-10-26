"""The method never imports its measurement (plan "Hardening from external repos" item 5, after
google-research/harness2: "Nothing in the method imports" the judge).

Trinity's instruments live in internal/experiments/ (the un-fix census, Gate T's read, the
verdict gate). Shipped code may READ their result files (skill_loop checks that a cited id exists)
but must never import their code: an optimizer that can call its own scorer can learn the scorer.
And build mode, the one place Trinity picks between model outputs, must not import the eval
judge or scorer.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "trinity_local"
EXPERIMENTS = REPO / "internal" / "experiments"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def test_no_shipped_module_imports_an_instrument():
    if not EXPERIMENTS.is_dir():
        return                                     # the public export carries no internal/
    instruments = {p.stem for p in EXPERIMENTS.glob("*.py")}
    offenders = []
    for f in SRC.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        if "sys.path.insert" in text or "sys.path.append" in text:
            offenders.append(f"{f.relative_to(REPO)}: edits sys.path")
        for n in _imports(f):
            if n.startswith(".") or n.split(".")[0] == "trinity_local":
                continue                           # the package's own modules
            parts = n.split(".")
            # Every dotted component, not just the first: `internal.experiments.scorer` must
            # not slip through as `internal` (verify's Codex read).
            if any(p in instruments or p in ("internal", "experiments") for p in parts):
                offenders.append(f"{f.relative_to(REPO)}: imports {n}")
    assert not offenders, offenders


def test_build_mode_does_not_import_the_eval_judge():
    names = _imports(SRC / "build_mode.py")
    banned = [n for n in names if "evals" in n or "judge" in n or "scorer" in n]
    assert not banned, f"build mode picks between model outputs; it must not import scoring: {banned}"
