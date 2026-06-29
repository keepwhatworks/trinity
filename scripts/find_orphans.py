"""Module-level reachability finder for src/trinity_local/.

Walks imports from production entry points (main.py, mcp_server.py,
plus the commands modules main.py dynamically loads via
CORE_COMMAND_MODULES / OPTIONAL_COMMAND_MODULES strings) and reports
modules under src/trinity_local/ that aren't reached.

What this catches that pyflakes/vulture miss:
- Whole-module orphans (vulture sees per-function use within a
  module; can't see whether the module itself is reachable from any
  entry point)
- Chain orphans (modules that call each other heavily but the chain
  has no live caller — the knn → ranker → nothing pattern)

What this DOES NOT catch:
- Features that exist + are called but produce empty output (the
  moves-substrate dormancy bug). See #185 — distribution tests.

Limitations / handled-explicitly:
- Conditional imports inside functions (try/except) — walked via
  ast.walk, so `from .backend_mlx import X` inside a function body
  resolves to the same module name as a top-level import.
- Re-exports through package __init__.py — relative imports inside
  __init__.py are resolved against the package's OWN name, not the
  parent. This is the bug the /tmp/orphan_finder.py prototype had
  during #187; fixed here.
- Modules referenced only from tests, only from comments, or via
  string-based dynamic import paths other than CORE_COMMAND_MODULES
  show up as orphan. Use `known_orphans.txt` to whitelist with
  reason (gstack ratchet pattern).

Exit codes:
- 0 — orphan set matches the whitelist exactly
- 1 — orphans found that aren't whitelisted, OR whitelist entries
  that are no longer orphan (they've been wired up — remove from
  whitelist)

Run: python scripts/find_orphans.py [--verbose]
"""
from __future__ import annotations
import argparse
import ast
import sys
from collections import deque
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "src" / "trinity_local"
PACKAGE = "trinity_local"
WHITELIST = REPO / "scripts" / "known_orphans.txt"


def module_name_of(path: Path) -> str:
    """Return the dotted module path for a .py file."""
    rel = path.relative_to(ROOT.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports_from(path: Path) -> set[str]:
    """Extract every dotted module name this file imports from our
    package, resolving relative imports correctly.

    The subtle bit: relative imports inside `__init__.py` are
    resolved against THIS package; relative imports inside a regular
    `.py` file are resolved against the PARENT package. Get this
    wrong and `ranker/__init__.py`'s `from .fallback import X`
    points at `trinity_local.fallback` instead of
    `trinity_local.ranker.fallback`.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return set()

    out: set[str] = set()

    # Determine the package this file belongs to (for relative-import
    # resolution). For `pkg/sub/foo.py` the package is `pkg.sub`.
    # For `pkg/sub/__init__.py` the package is ALSO `pkg.sub` (not
    # `pkg`) — that's the bug the prototype had.
    rel = path.relative_to(ROOT.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        package_parts = parts[:-1]
    else:
        package_parts = parts[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative: peel `node.level - 1` segments off the
                # package's own dotted name (level=1 means same
                # package, level=2 means parent, etc.)
                strip = node.level - 1
                base_parts = (
                    package_parts[:-strip] if strip > 0 else list(package_parts)
                )
                if node.module:
                    base = ".".join(base_parts + node.module.split("."))
                else:
                    base = ".".join(base_parts)
            elif node.module:
                base = node.module
            else:
                continue

            if not base.startswith(PACKAGE):
                continue
            out.add(base)
            # Each imported symbol could be a submodule OR an
            # attribute. Add both possibilities; resolve_to_path
            # filters non-modules out.
            for alias in node.names:
                if alias.name != "*":
                    out.add(f"{base}.{alias.name}")

    return out


def resolve_to_path(mod: str) -> Path | None:
    """Map a dotted module name to its .py file. A name may refer to
    a module OR a symbol inside a module; try both shapes."""
    if not mod.startswith(PACKAGE):
        return None
    parts = mod.split(".")[1:]  # drop "trinity_local"
    if not parts:
        return ROOT / "__init__.py"
    # Try as a module file
    candidate = ROOT.joinpath(*parts).with_suffix(".py")
    if candidate.exists():
        return candidate
    # Try as a package (__init__.py inside the named directory)
    pkg_init = ROOT.joinpath(*parts) / "__init__.py"
    if pkg_init.exists():
        return pkg_init
    # Try as a symbol-inside-a-module (drop the last segment and
    # retry the file/package lookup against the parent)
    if len(parts) > 1:
        candidate = ROOT.joinpath(*parts[:-1]).with_suffix(".py")
        if candidate.exists():
            return candidate
        pkg_init = ROOT.joinpath(*parts[:-1]) / "__init__.py"
        if pkg_init.exists():
            return pkg_init
    return None


def dynamic_entry_points_from_main() -> list[Path]:
    """Resolve commands/*.py registered via CORE_COMMAND_MODULES /
    OPTIONAL_COMMAND_MODULES — real entry points behind dynamic
    import strings rather than top-level imports."""
    main = ROOT / "main.py"
    try:
        tree = ast.parse(main.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    out: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (
                isinstance(target, ast.Name)
                and target.id in (
                    "CORE_COMMAND_MODULES",
                    "OPTIONAL_COMMAND_MODULES",
                )
            ):
                continue
            if isinstance(node.value, (ast.Tuple, ast.List)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(
                        elt.value, str
                    ):
                        p = ROOT / "commands" / f"{elt.value}.py"
                        if p.exists():
                            out.append(p)
    return out


def load_whitelist() -> dict[str, str]:
    """Parse known_orphans.txt: one `module_path : reason` line per
    intentional orphan. Comments (#) and blank lines ignored."""
    if not WHITELIST.exists():
        return {}
    out: dict[str, str] = {}
    for line in WHITELIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            path_str, reason = line.split(":", 1)
            out[path_str.strip()] = reason.strip()
        else:
            out[line] = ""
    return out


def find_orphans() -> tuple[set[Path], list[Path]]:
    """Returns (orphan_paths, entry_points) so callers can render
    either set in their own format."""
    all_files = sorted(
        [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    )

    adj: dict[Path, set[Path]] = {p: set() for p in all_files}
    for p in all_files:
        for mod in imports_from(p):
            tgt = resolve_to_path(mod)
            if tgt and tgt in adj:
                adj[p].add(tgt)
                # Reaching `pkg.submodule` also implicitly imports
                # the chain of `pkg/__init__.py` files on the path
                # down to it — Python executes each parent's
                # __init__ before the submodule. Mark them reached
                # too so package markers don't show as orphan.
                parts = mod.split(".")
                for depth in range(1, len(parts)):
                    parent = ".".join(parts[:depth])
                    parent_init = resolve_to_path(parent)
                    if parent_init and parent_init in adj:
                        adj[p].add(parent_init)

    entry_points: list[Path] = [ROOT / "main.py", ROOT / "mcp_server.py"]
    entry_points.extend(dynamic_entry_points_from_main())
    entry_points = [p for p in entry_points if p.exists()]

    reached: set[Path] = set()
    queue: deque[Path] = deque()
    for ep in entry_points:
        if ep not in reached:
            reached.add(ep)
            queue.append(ep)
    while queue:
        node = queue.popleft()
        for nxt in adj.get(node, set()):
            if nxt not in reached:
                reached.add(nxt)
                queue.append(nxt)

    orphans = {p for p in all_files if p not in reached}
    return orphans, entry_points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print reach count + entry-point list",
    )
    args = parser.parse_args()

    orphans, entry_points = find_orphans()
    whitelist = load_whitelist()

    # Resolve whitelist keys (which are repo-relative strings) to Paths
    whitelist_paths: dict[Path, str] = {}
    for key, reason in whitelist.items():
        p = (REPO / key).resolve()
        whitelist_paths[p] = reason

    # Categorize:
    #   unknown_orphans     — orphan but not whitelisted (FAIL)
    #   stale_whitelist     — whitelisted but no longer orphan (FAIL)
    #   acceptable_orphans  — orphan and whitelisted (PASS, just info)
    orphan_paths_resolved = {p.resolve() for p in orphans}
    unknown = sorted(orphan_paths_resolved - set(whitelist_paths.keys()))
    stale = sorted(set(whitelist_paths.keys()) - orphan_paths_resolved)
    acceptable = sorted(orphan_paths_resolved & set(whitelist_paths.keys()))

    if args.verbose:
        all_files_count = sum(
            1 for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts
        )
        print(f"Total .py files: {all_files_count}")
        print(f"Entry points: {len(entry_points)}")
        for ep in entry_points:
            print(f"  → {ep.relative_to(REPO)}")
        print(
            f"Orphans: {len(orphan_paths_resolved)} "
            f"({len(acceptable)} whitelisted, {len(unknown)} unknown)"
        )
        print()

    if acceptable:
        print("Accepted orphans (in whitelist):")
        for p in acceptable:
            reason = whitelist_paths.get(p, "")
            rel = p.relative_to(REPO) if p.is_relative_to(REPO) else p
            print(f"  ✓ {rel} — {reason}")
        print()

    rc = 0
    if unknown:
        print("UNKNOWN orphans (add to scripts/known_orphans.txt with reason, or wire up):")
        for p in sorted(unknown, key=lambda x: -_loc(x)):
            rel = p.relative_to(REPO) if p.is_relative_to(REPO) else p
            print(f"  ⚠ {_loc(p):>5} LOC  {rel}")
        rc = 1

    if stale:
        print("STALE whitelist entries (no longer orphan — remove from known_orphans.txt):")
        for p in stale:
            rel = p.relative_to(REPO) if p.is_relative_to(REPO) else p
            print(f"  ⚠ {rel}")
        rc = 1

    if rc == 0:
        print("OK: orphan set matches whitelist exactly.")

    return rc


def _loc(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
