"""Tests for the canonical CLI command-count helper in scripts/render_docs.py.

Wires the live argparse surface into the canonical-source renderer so
docs claiming "N commands" auto-update when commands are added or
dropped. Foundation for Area 5 (CLI consolidation 21→5): when a
future tick drops commands, the canonical count auto-decreases in
every doc surface that uses the placeholder.

Doesn't actually require any CLAUDE.md placeholder yet — that's a
separate doc-pass tick. This test only pins the helper itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def render_docs_module():
    """Import scripts/render_docs.py without making scripts/ a package."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import render_docs  # type: ignore[import-not-found]
        return render_docs
    finally:
        sys.path.pop(0)


class TestCanonicalCliCommandCount:
    def test_returns_positive_int(self, render_docs_module):
        """Sanity: the helper must return a non-zero count. Zero means
        the import chain broke (module objects vs name strings — the
        bug caught and fixed mid-implementation)."""
        count = render_docs_module.canonical_cli_command_count()
        assert isinstance(count, int)
        assert count > 0, (
            "CLI surface count must be positive — zero usually means "
            "_iter_command_modules() didn't yield real modules. Check "
            "imports in scripts/render_docs.py:canonical_cli_command_count."
        )

    def test_count_matches_live_argparse_surface(self, render_docs_module):
        """The canonical helper's count must match what the real
        argparse parser exposes — they share `_iter_command_modules`,
        so any drift is a bug in the helper, not a doc / code mismatch."""
        import argparse
        import importlib

        sys.path.insert(0, str(REPO_ROOT / "src"))
        try:
            main_mod = importlib.import_module("trinity_local.main")
        finally:
            sys.path.pop(0)

        # Build the same parser the helper builds.
        parser = argparse.ArgumentParser(prog="trinity-local")
        sub = parser.add_subparsers(dest="command")
        for module in main_mod._iter_command_modules():
            register = getattr(module, "register", None)
            if register is None:
                continue
            try:
                register(sub)
            except Exception:
                continue
        choices_count = 0
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                choices_count = len(action.choices)
                break

        assert render_docs_module.canonical_cli_command_count() == choices_count, (
            f"Helper count must match live argparse choices count "
            f"({choices_count}); helper returned "
            f"{render_docs_module.canonical_cli_command_count()}."
        )

    def test_registered_in_canonical_table(self, render_docs_module):
        """The helper must be plumbed into the CANONICAL dict so
        `render_docs.py` actually uses it on the next render pass.
        Adding the helper but forgetting to register it is the silent
        failure this test catches."""
        assert "cli_command_count" in render_docs_module.CANONICAL, (
            "canonical_cli_command_count must be registered in the "
            "CANONICAL dict — otherwise the renderer can't substitute "
            "<!-- canonical:cli_command_count --> placeholders."
        )
        # Confirm it points at the right function.
        assert (
            render_docs_module.CANONICAL["cli_command_count"]
            is render_docs_module.canonical_cli_command_count
        )

    def test_handler_failures_dont_zero_the_count(
        self, render_docs_module, monkeypatch
    ):
        """If one command module's register() raises, the helper must
        still count the others — not return zero. Defensive: an in-flight
        refactor breaking one module shouldn't tank the doc render."""
        import importlib
        sys.path.insert(0, str(REPO_ROOT / "src"))
        try:
            main_mod = importlib.import_module("trinity_local.main")
        finally:
            sys.path.pop(0)

        original_iter = main_mod._iter_command_modules

        def _iter_with_one_broken():
            """Yield original modules but wrap one to raise on register()."""
            modules = list(original_iter())
            assert len(modules) >= 2, "need ≥2 modules to exercise the failure path"
            # Wrap the FIRST module so its register raises.
            class _Broken:
                __name__ = "broken_for_test"
                def register(self, _subparsers):
                    raise RuntimeError("simulated broken register")
            yield _Broken()
            for m in modules:
                yield m

        monkeypatch.setattr(main_mod, "_iter_command_modules", _iter_with_one_broken)
        count = render_docs_module.canonical_cli_command_count()
        assert count > 0, (
            "One broken register() must not zero the count — the helper "
            "must catch and continue."
        )
