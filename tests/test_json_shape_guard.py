"""Static ratchet guard for the parse-then-trust anti-pattern (`guard_shape_not_just_parse`).

The recurring bug shape in this codebase: a reader does `data = json.loads(raw)` then
immediately `data.get(...)` / `data[k]` / `for x in data` — and a valid-JSON-of-the-
WRONG-TYPE (a list where a dict was expected, a bare scalar) sails past the
`json.JSONDecodeError` handler and crashes the caller on `.get`/index/iterate. It has bitten
launchpad render (#304 corrupt topics.json), the JSONL readers (the v1.7.202 7-site sweep),
and the per-line corpus/drift readers. The fix is always the same: `isinstance`-check the
parsed value before trusting its shape.

This guard is a forward-looking RATCHET (green-gate discipline — see docs/green-gate-checklist.md
and test_green_gate_registry.py). The 31 baseline sites were BURNED DOWN 2026-06-15 — every
json.loads of state/corpus/external data now isinstance-checks before .get/index/iterate — so
the baseline is empty and the ratchet starts from zero: any NEW unguarded reader is a hard fail.

A site counts as GUARDED (and is not flagged) when, within the same function, the parsed name
is either `isinstance`-checked, or the parse + trust both sit inside a `try` whose handler is
broad enough to catch a wrong-type access (Exception/AttributeError/TypeError/KeyError/...).
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"

# Member accesses that assume a dict (the "trust" half of parse-then-trust).
_TRUST_ATTRS = {"get", "items", "keys", "values", "setdefault", "pop", "update"}
# Exception types whose handler WOULD catch a wrong-type access, so a parse+trust wrapped
# in such a try is safe even without an explicit isinstance.
_BROAD_EXC = {
    "Exception", "BaseException", "AttributeError", "TypeError",
    "KeyError", "IndexError", "LookupError",
}


def _is_json_parse(call: ast.Call) -> bool:
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr in {"loads", "load"}
        and isinstance(f.value, ast.Name)
        and f.value.id == "json"
    )


def _handler_is_broad(handlers) -> bool:
    for h in handlers:
        if h.type is None:  # bare `except:`
            return True
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        if any(isinstance(t, ast.Name) and t.id in _BROAD_EXC for t in types):
            return True
    return False


def _broad_try_lines(fn: ast.AST) -> set[int]:
    """Line numbers sitting inside a try-body whose handler catches wrong-type access."""
    covered: set[int] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Try) and _handler_is_broad(n.handlers):
            for stmt in n.body:
                for sub in ast.walk(stmt):
                    ln = getattr(sub, "lineno", None)
                    if ln is not None:
                        covered.add(ln)
    return covered


def _unguarded_functions(source: str) -> set[str]:
    """Return the names of functions in `source` that parse JSON and then trust its
    shape (member/index/iterate) without an isinstance gate or a broad-except wrap."""
    tree = ast.parse(source)
    flagged: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parsed: dict[str, int] = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and _is_json_parse(n.value):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        parsed[t.id] = n.lineno
        if not parsed:
            continue
        guarded = {
            n.args[0].id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "isinstance"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in parsed
        }
        broad = _broad_try_lines(fn)
        for n in ast.walk(fn):
            name = None
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in parsed
                and n.attr in _TRUST_ATTRS
            ):
                name = n.value.id
            elif isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in parsed:
                name = n.value.id
            if name and name not in guarded:
                access_line = getattr(n, "lineno", -1)
                if not (parsed[name] in broad and access_line in broad):
                    flagged.add(fn.name)
                    break
    return flagged


def _scan_tree() -> set[str]:
    """All genuinely-unguarded sites across src/, keyed `relpath::function`."""
    sites: set[str] = set()
    for p in sorted(SRC.rglob("*.py")):
        try:
            source = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for fn_name in _unguarded_functions(source):
            sites.add(f"{p.relative_to(SRC).as_posix()}::{fn_name}")
    return sites


_BASELINE_UNGUARDED = frozenset()  # burned down 2026-06-15 — all 31 parse-then-trust
# sites guarded (isinstance before .get/index/iterate). The ratchet now starts from
# zero: any NEW unguarded reader is a hard fail.


def test_no_new_unguarded_json_parse():
    """A NEW reader that does json.loads → .get/index/iterate without an isinstance gate
    must shape-check the result (or be a conscious, justified addition to the baseline).
    The recurring crash class is a corrupt/wrong-type state file taking down the caller."""
    new = _scan_tree() - _BASELINE_UNGUARDED
    assert not new, (
        "New parse-then-trust site(s) without an isinstance shape-guard:\n  "
        + "\n  ".join(sorted(new))
        + "\n\nFix: isinstance-check the json.loads result before .get/index/iterate "
        "(see guard_shape_not_just_parse). Do NOT just widen the baseline."
    )


def test_baseline_allowlist_only_shrinks():
    """The baseline may only shrink. If a site was fixed or removed, its entry is now
    STALE and must be deleted — otherwise a future regression at the same function would
    be silently re-allowed by a dead allowlist entry (the retirement-guard-rot lesson)."""
    stale = _BASELINE_UNGUARDED - _scan_tree()
    assert not stale, (
        "Stale baseline entries (site fixed/removed — delete them from _BASELINE_UNGUARDED):\n  "
        + "\n  ".join(sorted(stale))
    )


def test_scanner_is_not_vacuous():
    """The live tree is clean now (debt burned down 2026-06-15), so non-vacuity is proven
    against a synthetic unguarded function rather than a live count — this still catches an
    AST-walk bug that would make the scanner silently match nothing."""
    bad = (
        "import json\n"
        "def reader(path):\n"
        "    d = json.loads(path.read_text())\n"
        "    return d.get('x')\n"
    )
    assert _unguarded_functions(bad) == {"reader"}


def test_scanner_flags_the_bad_pattern():
    """Mutation-proof the detector itself: a bare parse-then-trust IS flagged."""
    bad = (
        "import json\n"
        "def reader(path):\n"
        "    data = json.loads(path.read_text())\n"
        "    return data.get('x')\n"
    )
    assert _unguarded_functions(bad) == {"reader"}


def test_scanner_clears_isinstance_guard():
    """An isinstance gate before the trust clears the site."""
    good = (
        "import json\n"
        "def reader(path):\n"
        "    data = json.loads(path.read_text())\n"
        "    if not isinstance(data, dict):\n"
        "        return None\n"
        "    return data.get('x')\n"
    )
    assert _unguarded_functions(good) == set()


def test_scanner_clears_broad_except():
    """A parse+trust wrapped in a try whose handler catches a wrong-type access is safe."""
    good = (
        "import json\n"
        "def reader(path):\n"
        "    try:\n"
        "        data = json.loads(path.read_text())\n"
        "        return data.get('x')\n"
        "    except Exception:\n"
        "        return None\n"
    )
    assert _unguarded_functions(good) == set()


def test_scanner_narrow_except_still_flags():
    """A try that only catches JSONDecodeError does NOT catch a wrong-type access — the
    valid-JSON-wrong-type value still crashes on .get, so the site stays flagged."""
    bad = (
        "import json\n"
        "def reader(path):\n"
        "    try:\n"
        "        data = json.loads(path.read_text())\n"
        "    except json.JSONDecodeError:\n"
        "        return None\n"
        "    return data.get('x')\n"
    )
    assert _unguarded_functions(bad) == {"reader"}
