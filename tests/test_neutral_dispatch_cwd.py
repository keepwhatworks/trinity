"""Data-producing stages dispatch from a neutral cwd, not the project.

res_090, measured 2026-08-24. A `claude -p` spawned with cwd inside the project
inherits that project's Claude Code context — its CLAUDE.md, its memory files,
and its git history. On an open-ended prompt (the shape distill sends) the
subprocess opened with a model-disclosure header taken from a founder
instruction meant for interactive REPLIES, and recited recent commit messages as
if they were its own knowledge. From a neutral cwd it did neither.

That is fine for a council answering a question about the repo. It is wrong for
a stage whose output becomes a stored artifact: core.md is supposed to distil
the FOUNDER, not the working notes of whoever last committed.

Both polluted candidates were caught by the fail-closed core gate for an
unrelated reason, which is luck standing in for a guard. This is the guard.
"""
from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "trinity_local"


class TestLensStagesDoNotDispatchFromTheProject:
    def test_me_builder_has_no_project_cwd_dispatch(self):
        s = (SRC / "me_builder.py").read_text()
        bad = [ln.strip() for ln in s.splitlines()
               if "Path.cwd()" in ln and ("run(" in ln or "_stage_run" in ln)]
        assert not bad, (
            "a lens stage dispatches from the project cwd, so the subprocess "
            f"inherits CLAUDE.md, memory and git history: {bad}")

    def test_distill_dispatches_neutral(self):
        s = (SRC / "distill.py").read_text()
        assert "neutral_dispatch_dir()" in s
        assert not re.search(r'runner\.run\(prompt,\s*_Path\("\."\)\)', s), (
            "distill is back to dispatching from '.', which is the project when "
            "invoked from it — the res_090 vector")

    def test_every_lens_stage_uses_the_helper(self):
        s = (SRC / "me_builder.py").read_text()
        assert s.count("neutral_dispatch_dir()") >= 4, (
            "a stage was added or reverted without the neutral cwd; all four "
            "lens-build dispatches must use it")


class TestTheHelperIsSafeAndActuallyNeutral:
    def test_it_creates_a_real_empty_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.state_paths import neutral_dispatch_dir

        d = neutral_dispatch_dir()
        assert d.is_dir()
        assert not list(d.iterdir()), "the dispatch dir must stay empty to stay neutral"

    def test_it_is_not_inside_the_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.state_paths import neutral_dispatch_dir

        d = neutral_dispatch_dir().resolve()
        repo = pathlib.Path(__file__).resolve().parent.parent
        assert repo not in d.parents and d != repo, (
            "a dispatch dir inside the repo still inherits the project's "
            "CLAUDE.md and git history through parent-directory search")

    def test_it_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.state_paths import neutral_dispatch_dir

        assert neutral_dispatch_dir() == neutral_dispatch_dir()
