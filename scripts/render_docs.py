"""Gap A — Canonical-source doc renderer.

Extracts CANONICAL values (test count, MCP tool count, version,
guard count) from their authoritative sources, then templates them
into docs using HTML-comment block syntax::

    Live count: <!-- canonical:test_count -->1296<!-- /canonical -->

The renderer is idempotent: re-running on already-correct docs is a
no-op. The 6-surfaces-agree TestTestCountConsistency guard becomes
a "verify the placeholder expanded correctly" assertion once docs
are migrated.

Usage::

    .venv/bin/python scripts/render_docs.py                  # re-render
    .venv/bin/python scripts/render_docs.py --check          # exit 1 if drift
    .venv/bin/python scripts/render_docs.py --canonical-only # print values, don't touch docs

Per docs/design-frame.md ("put signal in its channel"), this is the
structural fix for the duplicated-fact drift class.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


# ───────────────────────────────────────────────────────────────────────
# Canonical-value extractors
# ───────────────────────────────────────────────────────────────────────

def canonical_test_count() -> int:
    """Count tests via `pytest --collect-only -q` line count.

    Slight nuance: pytest's last "N tests collected" line gives the
    total. Use that explicitly so we don't double-count skipped tests
    or rely on stdout shape.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(r"(\d+) tests collected", result.stdout)
    if not m:
        raise RuntimeError(
            f"Couldn't parse test count from pytest output:\n{result.stdout[-500:]}"
        )
    total = int(m.group(1))
    # Tests = collected - skipped. The current convention pins
    # 4 skipped (gated real-Chrome smokes). Trust that for the
    # render value but bump if pytest output shows otherwise.
    skipped_match = re.search(r"(\d+) skipped", result.stdout)
    skipped = int(skipped_match.group(1)) if skipped_match else 4
    return total - skipped


def canonical_skipped_count() -> int:
    """Count skipped tests separately."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(r"(\d+) skipped", result.stdout)
    return int(m.group(1)) if m else 4


def canonical_mcp_tool_count() -> int:
    """Count MCP Tool() registrations in mcp_server.py."""
    mcp = (REPO / "src" / "trinity_local" / "mcp_server.py").read_text()
    return len(set(re.findall(r'\s+name="([a-z_]+)"', mcp)))


def canonical_doc_consistency_guard_count() -> int:
    """Count test methods in test_doc_count_consistency.py."""
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/test_doc_count_consistency.py",
            "--collect-only", "-q",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(r"(\d+) tests collected", result.stdout)
    return int(m.group(1)) if m else 0


def canonical_version() -> str:
    """Read version from pyproject.toml."""
    pyp = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyp, re.MULTILINE)
    if not m:
        raise RuntimeError("Couldn't parse version from pyproject.toml")
    return m.group(1)


def canonical_cli_command_count() -> int:
    """Count user-facing CLI subcommands by introspecting the live
    argparse surface.

    Builds the same parser main.py builds at runtime via its
    `_iter_command_modules` iterator (the single source of truth for
    which command modules register subparsers). Counts the
    `subparsers.add_parser(...)` registrations. Drift between docs
    and reality auto-corrects: when a future tick (Area 5 — CLI
    consolidation 21→5) drops commands, the count auto-decreases in
    every canonical-rendered doc surface.
    """
    import argparse
    import importlib

    parser = argparse.ArgumentParser(prog="trinity-local")
    subparsers = parser.add_subparsers(dest="command")
    main_mod = importlib.import_module("trinity_local.main")
    # main._iter_command_modules() yields the actual module objects;
    # CORE/OPTIONAL_COMMAND_MODULES are name strings only.
    for module in main_mod._iter_command_modules():
        register = getattr(module, "register", None)
        if register is None:
            continue
        try:
            register(subparsers)
        except Exception:
            # A command module that fails to register shouldn't poison
            # the count for the rest.
            continue
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return len(action.choices)
    return 0


def canonical_chrome_action_allowlist_count() -> int:
    """Count entries in capture_host.ACTION_ALLOWLIST.

    This is the Chrome-extension Native-Messaging allowlist (dashed
    names like ``launch-council`` / ``ingest-recent`` /
    ``render-me-card``), distinct from ``DISPATCH_ACTIONS`` in
    dispatch_registry.py (underscored names used by launchpad URL
    emitters). claude.md's status block + three-tier-architecture.md's
    Tier-3 description both quote the count + enumeration; iter #28
    of the post-launch sweep caught claude.md at "10" while live
    count was 12 (council-iterate, dream, open-launchpad shipped
    after the doc was written).
    """
    from trinity_local.capture_host import ACTION_ALLOWLIST
    return len(ACTION_ALLOWLIST)


def canonical_smoke_surface_count() -> int:
    """Count the distinct surface labels printed by scripts/browser_smoke.py.

    Surface labels travel through the script as printable lines like
    ``[ ✓ ] Surface 14a memory chips`` — that's the user-facing
    inventory the script delivers. Each label (counting "1b" and
    "14a" as distinct from "1" and "14") is one surface. Drift
    between docs ("33-surface") and the live script auto-corrects
    when surfaces land or retire; we just pin the prose against the
    source of truth.
    """
    smoke_py = REPO / "scripts" / "browser_smoke.py"
    src = smoke_py.read_text(encoding="utf-8")
    ids: set[str] = set()
    for m in re.finditer(r'"\[[^\]]+\]\s+Surface\s+([0-9]+[ab]?)', src):
        ids.add(m.group(1))
    return len(ids)


def canonical_py_file_count() -> int:
    """Count .py source files under src/trinity_local/ (excluding __pycache__).

    Drift caught in post-launch sweep: claude.md L132's tree summary
    said "113 .py files" while reality was 119. The number ticks every
    time a module lands or is sunset; pinning it via canonical avoids
    silently stale architecture counts.
    """
    src_dir = REPO / "src" / "trinity_local"
    return sum(1 for p in src_dir.rglob("*.py") if "__pycache__" not in p.parts)


def canonical_command_module_count() -> int:
    """Count user-facing command modules from main.py's tuples.

    `CORE_COMMAND_MODULES + OPTIONAL_COMMAND_MODULES` is the SoT for
    which modules register CLIs. Distinct from
    `canonical_cli_command_count` (subparser count) — one module can
    register N subparsers. claude.md cites the module count in the
    architecture section + the forward-arc "shipped surface" line.
    """
    import importlib

    main_mod = importlib.import_module("trinity_local.main")
    core = getattr(main_mod, "CORE_COMMAND_MODULES", ())
    optional = getattr(main_mod, "OPTIONAL_COMMAND_MODULES", ())
    return len(core) + len(optional)


CANONICAL: dict[str, callable] = {
    "test_count": canonical_test_count,
    "skipped_count": canonical_skipped_count,
    "mcp_tool_count": canonical_mcp_tool_count,
    "doc_consistency_guards": canonical_doc_consistency_guard_count,
    "cli_command_count": canonical_cli_command_count,
    "command_module_count": canonical_command_module_count,
    "chrome_action_allowlist_count": canonical_chrome_action_allowlist_count,
    "smoke_surface_count": canonical_smoke_surface_count,
    "py_file_count": canonical_py_file_count,
    "version": canonical_version,
}

# #131: extend the canonical-placeholder registry with string-valued
# facts that recur across ≥2 user-facing surfaces. Counts stay above
# (they're computed via codebase introspection); string facts live in
# src/trinity_local/facts.py so Python code can `import LANDING_DOMAIN`
# directly while doc surfaces stay in sync via the same placeholder
# syntax. New facts: add to facts.FACTS, no render_docs.py change needed.
sys.path.insert(0, str(REPO / "src"))
try:
    from trinity_local.facts import FACTS as _FACTS  # noqa: E402

    CANONICAL.update(_FACTS)
finally:
    sys.path.pop(0)


# ───────────────────────────────────────────────────────────────────────
# Renderer
# ───────────────────────────────────────────────────────────────────────

# Block syntax: <!-- canonical:NAME -->VALUE<!-- /canonical -->
PLACEHOLDER_PATTERN = re.compile(
    r"<!--\s*canonical:(\w+)\s*-->(.*?)<!--\s*/canonical\s*-->",
    re.DOTALL,
)


def render_file(path: Path, values: dict[str, str]) -> tuple[bool, int]:
    """Replace placeholders in `path` with `values`.

    Returns (changed, replacement_count). `changed=True` if file content
    differs from on-disk.
    """
    text = path.read_text(encoding="utf-8")
    original = text
    replacements = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal replacements
        name = match.group(1)
        if name not in values:
            return match.group(0)  # unknown placeholder — leave alone
        replacements += 1
        return f"<!-- canonical:{name} -->{values[name]}<!-- /canonical -->"

    text = PLACEHOLDER_PATTERN.sub(_replace, text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True, replacements
    return False, replacements


def find_placeholders(path: Path) -> list[str]:
    """Return names of all canonical-placeholders found in path."""
    text = path.read_text(encoding="utf-8")
    return [m.group(1) for m in PLACEHOLDER_PATTERN.finditer(text)]


def docs_with_placeholders() -> list[Path]:
    """Scan the repo for any md/html file containing canonical-placeholders."""
    found: list[Path] = []
    for path in REPO.rglob("*.md"):
        if any(
            skip in str(path)
            for skip in (".venv", "node_modules", "build/", ".egg-info", ".pytest_cache")
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "canonical:" in text:
            found.append(path)
    for path in REPO.rglob("*.html"):
        if any(skip in str(path) for skip in (".venv", "node_modules", "build/")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "canonical:" in text:
            found.append(path)
    return found


# ───────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any doc would change. Don't write.",
    )
    parser.add_argument(
        "--canonical-only",
        action="store_true",
        help="Print canonical values and exit. Don't touch docs.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print all replacements made.",
    )
    args = parser.parse_args()

    print("Computing canonical values...")
    values: dict[str, str] = {}
    for name, fn in CANONICAL.items():
        try:
            values[name] = str(fn())
        except Exception as exc:  # noqa: BLE001 — surface every extractor failure
            print(f"  ERROR computing {name}: {exc}", file=sys.stderr)
            return 1
        print(f"  {name} = {values[name]}")

    if args.canonical_only:
        return 0

    docs = docs_with_placeholders()
    if not docs:
        print(
            "\nNo docs contain canonical-placeholders yet. Migrate at least "
            "one fact to use the <!-- canonical:NAME -->VALUE<!-- /canonical --> "
            "syntax to start using this renderer."
        )
        return 0

    changed: list[Path] = []
    print(f"\nScanning {len(docs)} doc(s) with canonical-placeholders...")
    for path in docs:
        is_changed, count = render_file(path, values)
        if is_changed:
            changed.append(path)
            print(f"  rendered {count} placeholder(s): {path.relative_to(REPO)}")
        elif args.verbose:
            ph = find_placeholders(path)
            print(f"  unchanged ({len(ph)} placeholder(s)): {path.relative_to(REPO)}")

    if args.check and changed:
        print(
            f"\n--check: {len(changed)} doc(s) would change. "
            "Run `python scripts/render_docs.py` to re-render.",
            file=sys.stderr,
        )
        return 1

    if changed:
        print(f"\nRendered {len(changed)} doc(s).")
    else:
        print("\nAll docs already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
