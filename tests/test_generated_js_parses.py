"""The JavaScript we generate must parse — checked WITHOUT a browser.

On 2026-08-18 the entire memory viewer was dead. A removal commit (res_022, a184e15a)
deleted the opening of a JS block -- `if (focusTask && ...) {` through its click handler
-- and left the trailing `});` and two appendChild calls behind. Unbalanced delimiters,
SyntaxError at load, every file view stuck on "Loading…" forever. It also orphaned two
reads of `focusTask`, whose declaration lived in the deleted block.

The guard that catches it existed and could not fire: it is a browser test, and browser
tests skip without Chrome under the gate command this repo mandates. Measured that same
day across two disjoint stratified draws, ~30% of this repo's fix commits put their
ENTIRE guard in that tier. A defect protected only by an unrun guard is unprotected.

So these run in the DEFAULT tier with no browser and no node required, over the pages as
actually generated rather than over a fixture copy of them.

Mutation-proven 2026-08-18: reinstating the orphaned `});` in memory_viewer.py REDs
`test_memory_viewer_script_parses`; making `unbalanced` return None unconditionally REDs
the detection tests.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Loaded by path: a module-level sys.path.insert runs at COLLECTION and leaks into every
# other test module, which test_no_module_level_env_mutation.py exists to catch.
_spec = importlib.util.spec_from_file_location(
    "check_generated_js", REPO / "scripts" / "check_generated_js.py")
gjs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gjs)


def _memory_viewer_html(home: Path) -> str:
    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "core.md").write_text("# Core\n\nhello\n", encoding="utf-8")
    from trinity_local.memory_viewer import write_memory_viewer
    return write_memory_viewer().read_text(encoding="utf-8")


class TestGeneratedPagesParse:
    def test_memory_viewer_script_parses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        scripts = gjs.inline_scripts(_memory_viewer_html(tmp_path))
        assert scripts, "memory.html must carry an inline script — nothing to check means vacuous"
        for i, js in enumerate(scripts):
            assert gjs.unbalanced(js) is None, (
                f"memory.html inline script[{i}] has unbalanced delimiters: "
                f"{gjs.unbalanced(js)}. The page will not parse and every view will hang.")

    def test_launchpad_script_parses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        from trinity_local.launchpad_template import render_launchpad_html
        html = render_launchpad_html(page_data={})
        scripts = gjs.inline_scripts(html)
        assert scripts, "launchpad must carry an inline script"
        for i, js in enumerate(scripts):
            assert gjs.unbalanced(js) is None, f"launchpad script[{i}]: {gjs.unbalanced(js)}"

    def test_live_council_page_script_parses(self, tmp_path, monkeypatch):
        """The live council page is a user surface with FIVE inline script blocks.

        Added after noticing the first version of this guard covered only the memory
        viewer and the launchpad -- while six modules emit inline <script>. A guard that
        protects two of the surfaces a bug class can hit is not protecting against the
        bug class; the memory viewer died for a week from exactly this, and the live
        council page had the same exposure and no check.
        """
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        from trinity_local.council_review import render_live_council_page
        scripts = gjs.inline_scripts(render_live_council_page())
        assert scripts, "the live council page must carry inline script"
        for i, js in enumerate(scripts):
            assert gjs.unbalanced(js) is None, (
                f"live_council.html script[{i}]: {gjs.unbalanced(js)} — the page will not "
                "parse and the council view will hang")

    def test_every_generator_that_emits_script_is_covered_here(self):
        """A coverage ratchet on the guard ITSELF.

        The gap this closes was invisible because nothing counted the generators. If a
        new module starts emitting inline <script>, this fails and forces a decision:
        add it above, or record why it is exempt. Without this, the guard silently
        protects a shrinking fraction of the surface it is named for.
        """
        import re
        srcdir = REPO / "src" / "trinity_local"
        emitters = {f.stem for f in srcdir.glob("*.py")
                    if re.search(r"<script(?![^>]*\ssrc=)", f.read_text(encoding="utf-8"))}
        covered = {"memory_viewer", "launchpad_template", "council_review"}
        # Exempt, with reasons -- not a silent allowlist:
        exempt = {
            "design_system": "emits the shared <head>/<footer> fragments, which the three "
                             "covered pages already include and parse as part of themselves",
            "vendor": "publishes third-party vendor files verbatim; not Trinity-authored JS",
            "capture_host": "Native Messaging host — writes no user-facing page",
            "council_runner": "the string is a template fragment consumed by a covered page",
            "launchpad_runtime": "runtime helpers inlined INTO launchpad_template's page",
        }
        uncovered = emitters - covered - set(exempt)
        assert not uncovered, (
            f"these modules emit inline <script> and no parse guard covers them: "
            f"{sorted(uncovered)}. Add a test above, or add an explicit reason to `exempt`.")

    @pytest.mark.skipif(not shutil.which("node"), reason="node absent — balance check still ran")
    def test_node_agrees_when_available(self, tmp_path, monkeypatch):
        """Additional evidence, never a substitute: the balance check above runs
        everywhere, so a machine without node still gets a real verdict."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        for i, js in enumerate(gjs.inline_scripts(_memory_viewer_html(tmp_path))):
            assert gjs.node_check(js, tmp_path / f"s{i}.js") is None


class TestTheCheckerItself:
    """A checker that never fires is decoration; one that fires on valid code is worse."""

    def test_detects_the_exact_defect_that_shipped(self):
        assert gjs.unbalanced("function f() {\n  if (a) {\n  }\n});\n") is not None

    def test_detects_an_unclosed_opener(self):
        assert "unclosed" in (gjs.unbalanced("function f() { if (a) {") or "")

    def test_braces_inside_strings_are_not_delimiters(self):
        assert gjs.unbalanced('const a = "}}}}"; const b = \'{{\';') is None

    def test_braces_inside_comments_are_not_delimiters(self):
        assert gjs.unbalanced("// }\n/* ) ] } */\nconst a = 1;") is None

    def test_template_literals_including_nested_interpolation(self):
        assert gjs.unbalanced("const a = `x ${ {k: [1,2]} } y`; const b = `}`;") is None

    def test_regex_literals_are_not_division(self):
        assert gjs.unbalanced("const re = /[}{)(]/; const n = 4 / 2;") is None

    def test_apostrophe_escapes_do_not_swallow_the_file(self):
        assert gjs.unbalanced("const s = 'hasn\\'t'; if (a) { b(); }") is None
