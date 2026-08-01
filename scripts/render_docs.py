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
import functools
import json
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


# ───────────────────────────────────────────────────────────────────────
# Canonical-value extractors
# ───────────────────────────────────────────────────────────────────────

# ── Measured test counts ───────────────────────────────────────────────
#
# HISTORY (fixed 2026-07-31): both counts used to be FABRICATED. The
# extractor ran `pytest --collect-only -q`, scraped "N tests collected",
# then searched the SAME output for "(\d+) skipped" and fell back to a
# hardcoded 4. `--collect-only` never emits a skip summary — skips are a
# RUNTIME outcome — so the regex never matched, the constant always
# fired, and every doc surface published `collected - 4` as "N tests
# passing + 4 skipped". At the time of the fix that shipped 4389/4 while
# a real `pytest -q` read 3877 passed / 516 skipped: both published
# numbers were wrong, and 113 doc-consistency guards were green on them
# because they only ever compared doc-to-doc.
#
# The only thing that KNOWS a run's outcome is the run. `tests/conftest.py`
# writes RUN_SNAPSHOT after every whole-suite run; these extractors read
# it and RAISE when it is missing, red, or stale. There is deliberately
# no fallback value — an unmeasured count is an error, not a default.

RUN_SNAPSHOT = REPO / "test-run-snapshot.json"

_RUN_SNAPSHOT_HELP = (
    "Re-measure with:\n"
    "  TRINITY_HOME=$(mktemp -d) PYTHONPATH=src .venv/bin/python -m pytest -q\n"
    "then re-run scripts/render_docs.py."
)


class UnmeasuredCountError(RuntimeError):
    """A published count has no observed test run behind it."""


def _live_collected_count() -> int:
    """Live `pytest --collect-only -q` total — the staleness anchor.

    Cheap (~8s, collects but runs nothing) and it is the ONE property of
    the suite that can be checked without a 4-minute run. If it no longer
    matches what the snapshot recorded, tests have been added or removed
    since the last measurement and the snapshot's pass/skip counts no
    longer describe this tree.
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
        raise UnmeasuredCountError(
            "Couldn't parse a collected-test count from pytest output:\n"
            f"{result.stdout[-500:]}"
        )
    return int(m.group(1))


def load_run_snapshot(path: Path | None = None) -> dict:
    """Return the last whole-suite run's OBSERVED counts, or raise.

    Refuses — never substitutes a default — when:
      * the snapshot is absent          → nothing was ever measured
      * the run was red or interrupted  → the counts describe a broken run
      * `collected` no longer matches a live collect → the test set moved
    """
    snapshot_path = RUN_SNAPSHOT if path is None else path
    if not snapshot_path.exists():
        raise UnmeasuredCountError(
            f"No measured test run on disk ({snapshot_path.name} is missing), so "
            f"the published test counts cannot be derived from anything observed. "
            f"{_RUN_SNAPSHOT_HELP}"
        )
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnmeasuredCountError(
            f"{snapshot_path.name} is unreadable ({exc}). {_RUN_SNAPSHOT_HELP}"
        ) from exc
    if not isinstance(data, dict):
        raise UnmeasuredCountError(
            f"{snapshot_path.name} is not an object. {_RUN_SNAPSHOT_HELP}"
        )
    required = ("collected", "passed", "skipped", "failed", "errors", "exit_status")
    missing = [k for k in required if not isinstance(data.get(k), int)]
    if missing:
        raise UnmeasuredCountError(
            f"{snapshot_path.name} is missing measured field(s) {missing}. "
            f"{_RUN_SNAPSHOT_HELP}"
        )
    if data["exit_status"] != 0 or data["failed"] or data["errors"]:
        raise UnmeasuredCountError(
            f"The last measured run was RED (exit_status="
            f"{data['exit_status']}, failed={data['failed']}, "
            f"errors={data['errors']}). Publishing 'N tests passing' off a red "
            f"run would be the same fabrication this file was built to stop. "
            f"{_RUN_SNAPSHOT_HELP}"
        )
    if data["passed"] <= 0:
        raise UnmeasuredCountError(
            f"The last measured run recorded passed={data['passed']} — a "
            f"degenerate run. {_RUN_SNAPSHOT_HELP}"
        )
    # A SLOW-shard snapshot cannot back the canonical counts, and must not be
    # misreported as staleness. `_live_collected_count()` collects WITHOUT
    # TRINITY_SLOW, so a `TRINITY_SLOW=1` run always mismatches it — and the
    # staleness message below would then blame "tests were added or removed",
    # which is false and sends the reader looking for a diff that does not
    # exist. The canonical claim in CLAUDE.md is explicitly `pytest -q`, the
    # DEFAULT shard, so the right answer is to name the real cause and the real
    # fix. (Found 2026-08-01 by running TRINITY_SLOW=1 for the first time.)
    if data.get("trinity_slow"):
        raise UnmeasuredCountError(
            f"{snapshot_path.name} was written by the SLOW shard "
            f"(invocation: {data.get('invocation', 'TRINITY_SLOW=1')}), which "
            f"collects a different set than the canonical `pytest -q` claim. "
            f"Nothing is wrong with the tree — re-measure with the DEFAULT "
            f"shard so the published counts describe what the docs actually "
            f"claim:\n"
            f"  TRINITY_HOME=$(mktemp -d) PYTHONPATH=src "
            f".venv/bin/python -m pytest -q"
        )
    live = _live_collected_count()
    if live != data["collected"]:
        raise UnmeasuredCountError(
            f"Stale measurement: {snapshot_path.name} recorded "
            f"{data['collected']} collected, this tree now collects {live}. "
            f"Tests were added or removed since the last run, so its "
            f"passed/skipped counts no longer describe this tree. "
            f"{_RUN_SNAPSHOT_HELP}"
        )
    return data


@functools.lru_cache(maxsize=1)
def _run_snapshot_cached() -> dict:
    return load_run_snapshot()


def canonical_test_count() -> int:
    """Tests that were OBSERVED to pass in the last whole-suite run."""
    return int(_run_snapshot_cached()["passed"])


def canonical_skipped_count() -> int:
    """Tests that were OBSERVED to skip in the last whole-suite run."""
    return int(_run_snapshot_cached()["skipped"])


def canonical_collected_count() -> int:
    """Total tests collected in the last whole-suite run."""
    return int(_run_snapshot_cached()["collected"])


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
    "collected_count": canonical_collected_count,
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
# Evidence claims — the second, CONDITIONAL canonical class
# ───────────────────────────────────────────────────────────────────────
#
# The extractors above are unconditional: they introspect the repo, so they
# always produce a value. Evidence claims are recomputed from the USER's
# ~/.trinity/disagreement_ledger/summary.json, which does not exist in CI or on
# a fresh clone. They therefore travel with a THREE-state status
# (verified / refused / absent) instead of a value, and this renderer plants
# them ONLY when verified. In the other two states the placeholders are left
# byte-identical on disk (render_file leaves unknown names alone) and the
# renderer says so loudly — an unrendered evidence claim must never read as a
# rendered-and-agreeing one. See src/trinity_local/evidence_claims.py.

EVIDENCE_UNVERIFIED_EXIT = 2


def evidence_exit_code(state: str, require_evidence: bool) -> int | None:
    """Exit code for `--require-evidence`, or None to keep going.

    A separate, pure function so the control can be tested without a 4-minute
    render: exit 2 is distinct from `--check`'s exit 1 on purpose, so a caller
    can tell "docs drifted" from "the evidence numbers were never confirmed".
    Both non-verified states trip it — REFUSED and ABSENT differ in what they
    mean, but neither is a basis for publishing."""
    if require_evidence and state != "verified":
        return EVIDENCE_UNVERIFIED_EXIT
    return None


def evidence_state() -> tuple[str, dict[str, str], str]:
    sys.path.insert(0, str(REPO / "src"))
    try:
        from trinity_local.evidence_claims import evidence_status  # noqa: E402

        return evidence_status()
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


def render_file(
    path: Path, values: dict[str, str], write: bool = True
) -> tuple[bool, int]:
    """Replace placeholders in `path` with `values`.

    Returns (changed, replacement_count). `changed=True` if the rendered
    content differs from what is on disk.

    `write=False` makes this a pure comparison — it reports the drift it
    would fix without touching the file. `--check` passes it. Until
    2026-07-31 check mode called this with the write unconditional, so the
    "read-only" verification step silently RE-RENDERED every doc and then
    reported the drift it had just erased; a second `--check` would then
    pass. A verifier that repairs what it measures cannot fail twice.
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
        if write:
            path.write_text(text, encoding="utf-8")
        return True, replacements
    return False, replacements


def _rel(path: Path) -> str:
    """Repo-relative label for logging, tolerant of paths outside the repo.

    `Path.relative_to` RAISES on a non-subpath, which turned a logging line
    into a crash whenever the doc set was pointed anywhere else.
    """
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


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
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help=(
            "Exit 2 unless every evidence claim was recomputed from the live "
            "disagreement ledger. Off by default because the ledger artifact "
            "lives in ~/.trinity and is absent in CI — turn it on locally when "
            "you are about to publish a doc that quotes those numbers."
        ),
    )
    parser.add_argument(
        "--allow-unmeasured",
        action="store_true",
        help=(
            "Check only the placeholders that CAN be computed right now, and "
            "report the measured test counts as UNMEASURED instead of failing "
            "when no green whole-suite run backs them. For callers that run "
            "INSIDE the suite that produces the measurement — never for "
            "publishing (launch-check.sh deliberately omits it)."
        ),
    )
    args = parser.parse_args()

    print("Computing canonical values...")
    values: dict[str, str] = {}
    unmeasured: dict[str, str] = {}
    for name, fn in CANONICAL.items():
        try:
            values[name] = str(fn())
        except UnmeasuredCountError as exc:
            # NOT an extractor failure: the extractor worked and correctly
            # refused, because nothing observed this number. Keep it out of
            # `values` so render_file leaves the placeholder byte-identical
            # (same contract as a REFUSED evidence claim) and decide below
            # whether that is fatal for THIS caller.
            unmeasured[name] = str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 — surface every extractor failure
            print(f"  ERROR computing {name}: {exc}", file=sys.stderr)
            return 1
        print(f"  {name} = {values[name]}")

    if unmeasured:
        print(
            "\nMeasured counts [UNMEASURED] — no green whole-suite run backs "
            "these, so they keep whatever value is already on disk and nothing "
            "here confirms it:"
        )
        for name in sorted(unmeasured):
            print(f"    · {name}")
        print(f"  reason: {sorted(unmeasured.values())[0]}")
        if not args.allow_unmeasured:
            print(
                "\nRefusing to report a clean render while the published test "
                "counts have no measurement behind them. Re-measure (see above) "
                "or pass --allow-unmeasured to check only what is computable.",
                file=sys.stderr,
            )
            return 1

    # Evidence claims: planted only in the VERIFIED state. In `refused` /
    # `absent` the placeholders stay byte-identical and the state is printed
    # under a heading that cannot be misread as agreement.
    ev_state, ev_values, ev_reason = evidence_state()
    print(f"\nEvidence claims [{ev_state.upper()}]: {ev_reason}")
    if ev_state == "verified":
        values.update(ev_values)
        for name in sorted(ev_values):
            print(f"  {name} = {ev_values[name]}")
    else:
        sys.path.insert(0, str(REPO / "src"))
        try:
            from trinity_local.evidence_claims import CLAIM_NAMES  # noqa: E402
        finally:
            sys.path.pop(0)
        print(
            "  NOT CHECKED — the following placeholders keep whatever value is "
            "already on disk; nothing confirmed them:"
        )
        for name in CLAIM_NAMES:
            print(f"    · {name}")
    ev_exit = evidence_exit_code(ev_state, args.require_evidence)
    if ev_exit is not None:
        print(
            f"\n--require-evidence: evidence claims are {ev_state.upper()}, not "
            "verified. Refusing to report a clean render.",
            file=sys.stderr,
        )
        return ev_exit

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
        # write=not args.check — `--check` must observe drift, not repair it.
        is_changed, count = render_file(path, values, write=not args.check)
        if is_changed:
            changed.append(path)
            print(f"  rendered {count} placeholder(s): {_rel(path)}")
        elif args.verbose:
            ph = find_placeholders(path)
            print(f"  unchanged ({len(ph)} placeholder(s)): {_rel(path)}")

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
