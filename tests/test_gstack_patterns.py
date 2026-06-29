"""Reliability patterns lifted from garrytan/gstack (2026-05-27 audit).

Three patterns from gstack's `test/` directory map cleanly onto Trinity's
own reliability pain points:

  1. **Executable retirement denylist** (mirrors `test/docs-config-keys.test.ts`):
     scan source + active docs for present-tense references to anything
     in `retired_names.RETIRED`. Trinity already has a denylist (see
     `tests/test_doc_count_consistency.py::TestDroppedTermsAreNotReintroduced`)
     but it's hand-maintained — this test wires the registry directly so
     adding a retirement entry instantly grows the guard.

  2. **CLI capability-coverage audit** (mirrors `test/e2e-harness-audit.test.ts`):
     filesystem scan of `src/trinity_local/commands/*.py` registers every
     argparse subcommand and asserts each has at least one test file that
     mentions it. Catches the "shipped a verb without a test" failure mode
     before it ships.

  3. **Two-tier test split** (mirrors gstack's free vs `EVALS=1` shards):
     a marker-based split between cheap (always-run) and expensive
     (opt-in via `TRINITY_SLOW=1`) tests. Tests that hit real Chrome, real
     MLX, or real provider subprocesses get the `slow` marker. Default
     `pytest -q` stays fast; CI runs both shards.

The shapes are stolen; the wiring is Trinity-specific (Python + pytest
instead of TypeScript + bun:test). See docs/historical/sweep-patterns.md
for the prior catch-22: every doc-drift bug a hand-written guard catches
is a fix-once shape; every registry-driven guard catches *future* drifts
of the same shape too.
"""
from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


# ─── Pattern 1: registry-driven retirement denylist ────────────────────

class TestRetirementRegistryDrivesDenylist:
    """The retired_names registry is the source of truth for what's been
    retired. Active source + active docs must never refer to a retired
    name as if it's live. Adding a retirement entry instantly extends
    the guard — no need to update a separate denylist constant.
    """

    # Active surfaces — what readers see as "the way Trinity works today."
    # CHANGELOG + docs/historical/ are exempt (they describe the
    # retirement events themselves).
    ACTIVE_SOURCE_GLOBS = (
        "src/trinity_local/**/*.py",
    )
    ACTIVE_DOC_GLOBS = (
        "*.md",
        "docs/*.md",
        "skills/**/*.md",
    )
    EXEMPT_PREFIXES = (
        "src/trinity_local/retired_names.py",  # the registry itself
        "src/trinity_local/data/skills/",  # mirror; byte-identical to top
        "CHANGELOG.md",
        "docs/historical/",
        "docs/MIGRATION.md",  # tells users what changed
    )

    # Per-retirement allowlist: contexts where a retired name is OK
    # because the prose explicitly documents the retirement.
    RETIREMENT_CONTEXT_MARKERS = (
        "retired",
        "Retired",
        "RETIRED",
        "sunset",
        "deprecated",
        "removed",
        "no longer",
        "previously",
        "formerly",
        "0 production",
        "0 usage",
        "absorbed",
        # The "return as" / "Re-introduce" patterns name a retired CLI as
        # part of a "this came back / could come back" callout.
        "return as",
        "Re-introduce",
        "would return",
        "could return",
        # Migration / install docs sometimes list the retired CLI surface
        # being reintroduced under a different name — context-sensitive.
        "migration",
        "Migration",
    )

    def _iter_active_files(self) -> list[Path]:
        """Walk ACTIVE_SOURCE_GLOBS + ACTIVE_DOC_GLOBS, drop EXEMPT_PREFIXES."""
        files: list[Path] = []
        for pattern in self.ACTIVE_SOURCE_GLOBS + self.ACTIVE_DOC_GLOBS:
            files.extend(REPO.glob(pattern))
        # Stable order for deterministic test output
        out: list[Path] = []
        for f in sorted(set(files)):
            rel = f.relative_to(REPO).as_posix()
            if any(rel.startswith(p) for p in self.EXEMPT_PREFIXES):
                continue
            if f.is_file():
                out.append(f)
        return out

    def _has_retirement_context(self, text: str, line_no: int) -> bool:
        """Check whether the line carrying the retired name is wrapped
        in retirement-explaining context. Looks at the matching line +
        the surrounding paragraph (±3 lines) for any marker word."""
        lines = text.splitlines()
        start = max(0, line_no - 4)
        end = min(len(lines), line_no + 3)
        window = "\n".join(lines[start:end]).lower()
        return any(m.lower() in window for m in self.RETIREMENT_CONTEXT_MARKERS)

    def test_no_retired_cli_verbs_in_active_surfaces(self):
        """Retired CLI verb names (e.g. `handoff`, `council-rate`) must
        only appear in active surfaces inside retirement-explaining
        context. A bare reference is a sign that prose drifted past the
        retirement event."""
        from trinity_local.retired_names import RETIRED

        # Pull CLI-kind retirements only — module / file / mcp_tool
        # retirements get their own structural guards elsewhere.
        retired_clis = sorted(
            name for name, rec in RETIRED.items()
            if rec.kind == "cli"
            # Skip verbs that are too generic to grep cleanly (will
            # produce false positives on unrelated prose).
            and len(name) >= 5
            # Skip verbs that share substring with active verbs.
            # (`council-rate` substring `rate` would match too much.)
            and "-" in name
        )
        # Bound the scan when retired_clis is huge (~30+ entries today).
        violations: list[tuple[str, str, int, str]] = []
        for verb in retired_clis:
            # Match `trinity-local <verb>` (most common doc form) or
            # backticked `<verb>` (referenced as a command).
            pattern = re.compile(
                r"(?:trinity-local\s+|`)" + re.escape(verb) + r"(?:`|\s|$)",
                re.MULTILINE,
            )
            for f in self._iter_active_files():
                try:
                    text = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for m in pattern.finditer(text):
                    line_no = text[: m.start()].count("\n") + 1
                    if self._has_retirement_context(text, line_no):
                        continue
                    violations.append((
                        verb,
                        f.relative_to(REPO).as_posix(),
                        line_no,
                        text.splitlines()[line_no - 1].strip()[:120],
                    ))

        assert not violations, (
            "Active surfaces reference retired CLI verbs as if they're live. "
            "Either the prose drifted past the retirement, or the surrounding "
            "context needs a retirement-explaining marker (one of: "
            f"{', '.join(self.RETIREMENT_CONTEXT_MARKERS[:6])}, ...).\n\n"
            "Add `class: historical` frontmatter to the file (moves it to "
            "the exempt set), or rewrite the line to explain the retirement.\n\n"
            "Violations:\n  "
            + "\n  ".join(
                f"{verb} → {path}:{line} :: {snippet}"
                for verb, path, line, snippet in violations[:20]
            )
            + (f"\n  ... and {len(violations) - 20} more" if len(violations) > 20 else "")
        )

    def test_no_retired_modules_imported(self):
        """Retired module names must not appear in active import
        statements. A live import of a retired module means a deletion
        was incomplete (the module file would FileNotFoundError at
        runtime anyway, but catching it at test time is cheaper).

        Module-name forms handled:
          - bare top-level: `tasks` → `from .tasks import` / `from trinity_local.tasks`
          - subdir-qualified: `commands.ingest` → `from .commands.ingest import`
                                                 / `from trinity_local.commands.ingest`
        """
        from trinity_local.retired_names import RETIRED

        retired_modules = sorted(
            name for name, rec in RETIRED.items()
            if rec.kind == "module"
        )
        violations: list[tuple[str, str, int]] = []
        for module in retired_modules:
            # Build the exact relative-import / absolute-import form for
            # this specific module name. Dotted names like
            # `commands.ingest` only match imports that carry the full
            # dotted form — never a bare `from .ingest` of a different
            # live module.
            if "." in module:
                # Subdir form: `commands.ingest`. Relative is
                # `from .commands.ingest`; absolute is
                # `from trinity_local.commands.ingest`.
                escaped = re.escape(module)
                pattern = re.compile(
                    r"^\s*(?:from\s+(?:\.|trinity_local\.)" + escaped
                    + r"\s+import|import\s+trinity_local\." + escaped + r"(?:\s|$))",
                    re.MULTILINE,
                )
            else:
                # Top-level form: `tasks`, `trust`. Relative is
                # `from .tasks`; absolute is `from trinity_local.tasks`.
                # Anchor on the import boundary so we don't catch
                # `from .tasks_dir` etc.
                escaped = re.escape(module)
                pattern = re.compile(
                    r"^\s*(?:from\s+\." + escaped + r"\s+import"
                    + r"|from\s+trinity_local\." + escaped + r"\s+import"
                    + r"|import\s+trinity_local\." + escaped + r"(?:\s|$))",
                    re.MULTILINE,
                )
            for f in REPO.glob("src/trinity_local/**/*.py"):
                if f.name == "retired_names.py":
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for m in pattern.finditer(text):
                    line_no = text[: m.start()].count("\n") + 1
                    violations.append((
                        module,
                        f.relative_to(REPO).as_posix(),
                        line_no,
                    ))

        assert not violations, (
            "Active source imports retired modules. The deletion was "
            "incomplete — either the module deletion needs to land, or "
            "the import sites need to migrate to the replacement.\n\n"
            "Violations:\n  "
            + "\n  ".join(
                f"{module} → {path}:{line}" for module, path, line in violations
            )
        )


# ─── Pattern 2: CLI capability-coverage audit ──────────────────────────

class TestCliCapabilityCoverage:
    """Every CLI subcommand registered via argparse must have at least
    one test file that exercises or references it. Pure filesystem scan —
    no LLM, no subprocess.

    Mirrors gstack's `test/e2e-harness-audit.test.ts`. Trinity has 51
    subcommands today across 26 modules; coverage was historically
    uneven (some verbs got dedicated tests, others rode on smoke).
    This guard turns "no test" into a red CI signal.
    """

    # Subcommand → minimum test-file marker mapping. A test "covers" a
    # subcommand if any test file contains the verb name as a string
    # (CLI invocation, importing the handler, or naming a test method
    # after it). Tolerant — false positives are fine; the guard exists
    # to catch the "literally no test mentions this verb" case.

    # Verbs exempt from coverage-required because they're trivial
    # wrappers or compound umbrella commands handled by sub-guards.
    EXEMPT_VERBS = frozenset({
        "debug",  # umbrella for replay-history / consolidate / vocabulary / seed
        "install",  # umbrella for install-mcp / install-extension / etc.
        # Telemetry verbs are tested via the GA4 wiring tests in
        # test_telemetry.py (when it exists) — for now they're plumbing
        # against the live GA4 Measurement Protocol endpoint, exercised
        # via the integration smoke.
        "telemetry-disable",
        "telemetry-endpoint",
        "telemetry-reset-id",
    })

    def _registered_verbs(self) -> list[tuple[str, str]]:
        """Walk commands/ + introspect argparse registrations.

        Returns list of (verb_name, command_module) pairs.
        Same logic the live CLI dispatcher uses, exercised at test
        time so verb additions are auto-discovered.
        """
        from trinity_local.main import CORE_COMMAND_MODULES, OPTIONAL_COMMAND_MODULES

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        verbs: list[tuple[str, str]] = []
        for mod_name in CORE_COMMAND_MODULES + OPTIONAL_COMMAND_MODULES:
            try:
                mod = importlib.import_module(f"trinity_local.commands.{mod_name}")
            except ImportError:
                continue
            register_fn = getattr(mod, "register", None)
            if register_fn is None:
                continue
            before = set(subparsers.choices.keys())
            try:
                register_fn(subparsers)
            except Exception:
                # Some registrations require args we don't have at test
                # time — skip cleanly. They'll be caught by the
                # smoke tests separately.
                continue
            new_verbs = set(subparsers.choices.keys()) - before
            for v in new_verbs:
                verbs.append((v, mod_name))
        return verbs

    def test_every_cli_verb_has_test_coverage(self):
        """Each registered verb must be mentioned in at least one test
        file in tests/. The mention can be (a) an invocation in a test
        body, (b) an import of the handler, (c) a test method name
        containing the verb. Pure substring match — tolerant by design."""
        verbs = self._registered_verbs()
        assert verbs, "Failed to enumerate any verbs — CLI dispatcher import broken?"

        test_dir = REPO / "tests"
        # Read every test file once.
        test_corpus: dict[Path, str] = {}
        for f in test_dir.rglob("test_*.py"):
            try:
                test_corpus[f] = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

        uncovered: list[tuple[str, str]] = []
        for verb, mod_name in sorted(verbs):
            if verb in self.EXEMPT_VERBS:
                continue
            # Coverage signals (any one matches → covered):
            #   - verb name as standalone string (e.g. "moves-build")
            #   - underscored handler name (e.g. "handle_moves_build")
            #   - module import (e.g. "commands.moves")
            verb_underscore = verb.replace("-", "_")
            patterns = (
                f'"{verb}"',
                f"'{verb}'",
                f"handle_{verb_underscore}",
                f"commands.{mod_name}",
                f"commands/{mod_name}.py",
            )
            found = False
            for text in test_corpus.values():
                if any(p in text for p in patterns):
                    found = True
                    break
            if not found:
                uncovered.append((verb, mod_name))

        assert not uncovered, (
            "These CLI verbs have ZERO test coverage. Add at least one "
            "test file that invokes or imports the handler.\n\n"
            "Coverage signals checked (any one matches): the verb name "
            "in quotes, the handle_<verb_underscore> handler name, or "
            "the commands.<module> import path.\n\n"
            "Uncovered:\n  "
            + "\n  ".join(f"{verb} (from commands/{mod}.py)" for verb, mod in uncovered)
            + "\n\n"
            "Each uncovered verb is a shipped feature with no tests. "
            "Add the verb to EXEMPT_VERBS only if it's intentionally "
            "a thin wrapper (with a one-line justification comment)."
        )


# ─── Pattern 3: two-tier test split (slow vs fast) ─────────────────────

class TestSlowMarkerDiscipline:
    """The slow/fast test split needs a discipline guard: tests that
    perform real subprocess calls, hit real Chrome, or load MLX weights
    should carry the `@pytest.mark.slow` marker so default `pytest -q`
    skips them. Tests without the marker should never block on external
    resources.

    Mirrors gstack's `EVALS=1` env gate. Without this guard, a single
    slow test silently joins the fast suite and the gate stops being
    fast. Run all tests with `TRINITY_SLOW=1 pytest -q`.
    """

    # Substrings whose presence in a test file argues for `slow` marker.
    SLOW_SIGNALS = (
        # Real provider CLI subprocess calls — would actually hit Claude/
        # Codex/Antigravity and consume credits.
        'subprocess.run(["claude"',
        'subprocess.run(["codex"',
        'subprocess.run(["agy"',
        # Real Chrome / Playwright — needs a browser binary present.
        "from playwright",
        "playwright.async_api",
        "playwright.sync_api",
        # Real MLX weights — multi-GB load.
        "from mlx_lm",
        "import mlx_lm",
        # Real network calls.
        "urllib.request.urlopen",
        "requests.get(",
        "requests.post(",
    )

    # Tests that legitimately exercise these signals INSIDE a mock /
    # patch / monkeypatch context — exempt because the signal isn't
    # the actual behavior, just the test stub.
    MOCK_EXEMPT_MARKERS = (
        "monkeypatch.setattr",
        "with patch(",
        "@patch(",
        "mock_subprocess",
        "mock_chrome",
        "Mock(",
        "MagicMock(",
    )

    def test_slow_signals_carry_slow_marker_or_mock_context(self):
        """Each test file containing a SLOW_SIGNAL must either:
          (a) carry `@pytest.mark.slow` on the test/class, or
          (b) wrap the signal in a mock context.

        Catches the "test hits real Chrome without the marker, blocks
        CI for 60s" pattern from gstack's flaky-suite audit.
        """
        test_dir = REPO / "tests"
        violations: list[tuple[str, str, int]] = []
        for f in test_dir.rglob("test_*.py"):
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for signal in self.SLOW_SIGNALS:
                if signal not in text:
                    continue
                # Find the line(s) where the signal appears.
                for line_no, line in enumerate(text.splitlines(), 1):
                    if signal not in line:
                        continue
                    # Check ±5 lines for mock-context markers OR an
                    # @pytest.mark.slow on the enclosing test.
                    start = max(0, line_no - 6)
                    end = min(len(text.splitlines()), line_no + 5)
                    window = "\n".join(text.splitlines()[start:end])
                    if any(m in window for m in self.MOCK_EXEMPT_MARKERS):
                        continue
                    # Check the whole file for the slow marker — if
                    # any test in the file has it, allow. Accept BOTH the bare
                    # `pytestmark = pytest.mark.slow` and the list form
                    # `pytestmark = [pytest.mark.slow, pytest.mark.browser]`
                    # (any order) — a slow test can also carry other markers.
                    if "@pytest.mark.slow" in text or re.search(
                        r"pytestmark\s*=.*pytest\.mark\.slow", text
                    ):
                        continue
                    violations.append((
                        f.relative_to(REPO).as_posix(),
                        signal,
                        line_no,
                    ))

        assert not violations, (
            "Tests perform expensive external operations without the "
            "@pytest.mark.slow marker AND without a mock context.\n\n"
            "Either:\n"
            "  (a) Add `@pytest.mark.slow` to the test/class so the default "
            "pytest run skips it (CI runs it via `TRINITY_SLOW=1 pytest -m slow`).\n"
            "  (b) Wrap the expensive call in monkeypatch/Mock so the test "
            "doesn't actually hit the external resource.\n\n"
            "Violations:\n  "
            + "\n  ".join(
                f"{path}:{line} contains '{signal}'"
                for path, signal, line in violations[:20]
            )
            + (f"\n  ... and {len(violations) - 20} more" if len(violations) > 20 else "")
        )

    def test_slow_marker_is_registered_in_pyproject(self):
        """The `slow` marker must be registered in pyproject.toml so
        pytest doesn't warn about unknown markers. Catches the case
        where someone adds @pytest.mark.slow but forgets to declare
        it — pytest emits a noisy warning and CI logs fill up."""
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "slow:" in pyproject or '"slow' in pyproject, (
            "@pytest.mark.slow is used by test_gstack_patterns.py but "
            "isn't registered in pyproject.toml [tool.pytest.ini_options] "
            "markers. Add: `slow: marks tests as slow (deselect with "
            "'-m \"not slow\"')` to keep pytest's marker registry happy."
        )
