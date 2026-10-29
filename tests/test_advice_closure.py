"""Advice-closure guards — a warning's recommended fix must be able to CLEAR it.

THE AUDIT CLASS (named 2026-07-04, two live instances found within 24h):
a staleness/health warning recommends a command, but the command's writers
don't touch the signal the checker reads — so the warning survives its own
advice forever, and the user learns to distrust the product's self-reports.

Instance 1 (fixed 2026-07-03): `status` said "corpus stale — run
`trinity-local ingest-recent`", but the checker read ONLY the background
stale-pass marker, which the CLI never writes. Guard:
tests/test_stale_pass.py::test_cli_ingest_cursors_clear_stale_without_marker.

Instance 2 (fixed 2026-07-04): `status` said vocabulary.md was stale and
recommended a refresh, but `trinity-local lens` — the natural refresh verb —
didn't write vocabulary.md (only dream's vocabulary phase did). Fixed by
folding the LLM-free vocabulary scan into the lens post-build hooks; guards
here + tests/test_distill.py::TestVocabularyFoldHooks.

The closure proof for lens_freshness decomposes as:
  (a) the check's fix names `trinity-local lens`          → asserted here
  (b) that handler invokes the writers                    → spy-proven in
      TestVocabularyFoldHooks (vocabulary) + the lens pipeline (topics.json)
  (c) refreshing the files those writers write clears the
      check                                               → asserted here

Instance 3 (pre-empted 2026-07-31): per-source ingest staleness computed from
the cursor's `last_mtime`. That field is a content watermark and the scan
boundary is inclusive, so a source whose newest file is old and fully drained
pins it forever — the live `gemini` source read "86.5 days behind" while being
perfectly current, and `trinity-local ingest-recent` could not move it by a
second. No surface had shipped that number yet; the guards below keep it that
way by pinning the clearable clock (`source_scan_ages`) as the one a freshness
surface may read. Full reproduction: tests/test_ingest_cursor_fixed_point.py.

When you add a NEW staleness warning with a `fix=` command, add its closure
test here.
"""
from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path


def _age(path, days: float) -> None:
    past = time.time() - days * 86400
    os.utime(path, (past, past))


class TestLensFreshnessAdviceClosure:
    def _seed(self, tmp_path):
        """Old vocabulary.md + topics.json, fresh council outcome → stale."""
        mem = tmp_path / "memories"
        mem.mkdir(parents=True, exist_ok=True)
        vocab = mem / "vocabulary.md"
        topics = mem / "topics.json"
        vocab.write_text("# Vocabulary\n", encoding="utf-8")
        topics.write_text(json.dumps({"basins": []}), encoding="utf-8")
        _age(vocab, 9)
        _age(topics, 9)
        outcomes = tmp_path / "council_outcomes"
        outcomes.mkdir(parents=True, exist_ok=True)
        (outcomes / "council_x.json").write_text("{}", encoding="utf-8")
        return vocab, topics

    def test_stale_fires_and_fix_names_a_command_that_writes_what_it_reads(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.health_checks import _check_lens_freshness
        vocab, topics = self._seed(tmp_path)

        stale = _check_lens_freshness()
        assert "predate" in stale.detail, stale.detail
        assert stale.fix == "trinity-local lens", (
            f"the lens_freshness fix is {stale.fix!r} — it must name the verb "
            "whose post-build hooks write vocabulary.md + topics.json, or the "
            "advice can never clear the warning"
        )
        # (b) source-coupling: the advised handler invokes the vocabulary
        # writer — via the extracted _post_build_hooks (Ousterhout closure,
        # 2026-07-05), so follow the chain: handler → hooks → writer.
        from trinity_local.commands import me as me_cmd
        handler_src = inspect.getsource(me_cmd.handle_me_build)
        assert "_post_build_hooks" in handler_src, (
            "handle_me_build no longer runs the post-build hooks — the "
            "lens_freshness fix advice points at a command that can't clear it"
        )
        hooks_src = inspect.getsource(me_cmd._post_build_hooks)
        assert "distill_vocabulary" in hooks_src, (
            "_post_build_hooks no longer calls distill_vocabulary — the "
            "lens_freshness fix advice points at a command that can't clear it"
        )

        # (c) refreshing the files the advised command's writers write → clears.
        now = time.time()
        os.utime(vocab, (now, now))
        os.utime(topics, (now, now))
        cleared = _check_lens_freshness()
        assert cleared.ok and "current" in cleared.detail, (cleared.ok, cleared.detail)

    def test_partial_refresh_still_warns(self, tmp_path, monkeypatch):
        """Refreshing only ONE of the two read paths must keep warning —
        the closure has to cover the checker's whole read set."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.health_checks import _check_lens_freshness
        vocab, topics = self._seed(tmp_path)
        now = time.time()
        os.utime(topics, (now, now))  # topics fresh, vocab still 9d old
        still = _check_lens_freshness()
        assert "vocabulary.md" in still.detail and "predate" in still.detail, still.detail


class TestIngestSourceFreshnessAdviceClosure:
    """Instance 3. Whichever number a future surface reports for "how far behind
    is this source", running the advised command must move it."""

    def _pinned_home(self, tmp_path, monkeypatch):
        """A source whose only transcript is old and already fully ingested."""
        home = tmp_path / "home"
        (home / ".claude" / "projects" / "proj").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("TRINITY_HOME", str(home / ".trinity"))
        sess = home / ".claude" / "projects" / "proj" / "s1.jsonl"
        sess.write_text(
            json.dumps({"type": "user", "sessionId": "s1",
                        "timestamp": "2026-05-06T13:48:00Z",
                        "message": {"role": "user", "content": "an old prompt"}}) + "\n",
            encoding="utf-8",
        )
        _age(sess, 86.5)
        return home

    def test_running_ingest_recent_clears_the_source_staleness(self, tmp_path, monkeypatch):
        """(a) the number exists, (b) the advised CLI verb — not some internal
        writer — is what moves it, (c) it lands at ~0."""
        from types import SimpleNamespace
        from trinity_local.commands.watch import handle_ingest_recent
        from trinity_local.incremental_ingest import source_scan_ages

        self._pinned_home(tmp_path, monkeypatch)

        # `trinity-local ingest-recent` IS the advice. Drive its handler.
        handle_ingest_recent(SimpleNamespace(sources=["claude"], deadline=10.0))
        first = source_scan_ages()["claude"]
        assert first < 60, f"the advised command left the source at {first}s stale"

        # And it keeps clearing it on a second run, when there is nothing new at
        # all — the case the watermark could never handle.
        handle_ingest_recent(SimpleNamespace(sources=["claude"], deadline=10.0))
        assert source_scan_ages()["claude"] < 60

    def test_the_unclearable_number_is_still_unclearable(self, tmp_path, monkeypatch):
        """The negative half, and the reason the separate clock exists: no
        amount of running the fix moves `last_mtime`, because the content did
        not change. Anyone tempted to report that field as freshness is
        reporting something their own advice cannot fix."""
        from types import SimpleNamespace
        from trinity_local.commands.watch import handle_ingest_recent
        from trinity_local.state_paths import ingest_cursors_path

        self._pinned_home(tmp_path, monkeypatch)
        for _ in range(3):
            handle_ingest_recent(SimpleNamespace(sources=["claude"], deadline=10.0))
        entry = json.loads(ingest_cursors_path().read_text(encoding="utf-8"))["claude"]
        assert time.time() - entry["last_mtime"] > 80 * 86400, (
            "if the watermark now advances past a drained boundary file, re-check "
            "that equal-mtime siblings can still be picked up before relaxing this"
        )

    def test_no_surface_derives_freshness_from_the_watermark(self):
        """Ratchet. `last_mtime` is a content watermark; only the ingest engine
        may touch it. A freshness surface reads source_scan_ages() instead."""
        src = Path(__file__).resolve().parent.parent / "src" / "trinity_local"
        allowed = {"incremental_ingest.py"}
        offenders = sorted(
            str(p.relative_to(src)) for p in src.rglob("*.py")
            if p.name not in allowed
            and "last_mtime" in p.read_text(encoding="utf-8", errors="replace")
        )
        assert not offenders, (
            "these modules read/write the ingest watermark `last_mtime`: "
            f"{offenders}. It cannot move past a fully-drained boundary file, so "
            "a staleness number derived from it can never be cleared by "
            "`trinity-local ingest-recent`. Use "
            "incremental_ingest.source_scan_ages()."
        )


def test_rechaired_evidence_path_is_not_cwd_relative():
    """`trust --dissent` must read the same evidence wherever it is invoked.

    RECHAIRED was a bare relative path, so it resolved against the process CWD:
    from the repo root it found 634 re-chaired councils, from anywhere else
    `.exists()` was False and the dense evidence source silently vanished while
    the verdict still rendered as if complete. A shipped CLI is almost never run
    from the repo root, so the common case was the degraded one.
    """
    from trinity_local.dissent_outcome import RECHAIRED

    assert RECHAIRED.is_absolute(), (
        "RECHAIRED is CWD-relative — trust --dissent reads a different evidence "
        "corpus depending on where the user happens to be standing"
    )


def _run_fix(fix: str) -> int:
    """Run a check's fix string through the real CLI parser and handler."""
    import shlex

    from trinity_local.main import build_parser
    argv = shlex.split(fix.split("#", 1)[0])
    assert argv[0] == "trinity-local", f"the fix is not a command: {fix!r}"
    args = build_parser().parse_args(argv[1:])
    return args.handler(args)


class TestConfigLoadableAdviceClosure:
    """Instance 4 (fixed 2026-10-26): a config.json that failed to load said
    "run `trinity-local install-mcp` # rewrites a default config". install-mcp
    writes the harnesses' MCP configs, never Trinity's own, and `config --init`
    refused any file that existed, so a hand-edit that broke the JSON had no
    command that cleared it. These run the check's own fix string through the
    real CLI parser, so rewording the advice into something that does not work
    goes red here."""

    _run_fix = staticmethod(_run_fix)

    @staticmethod
    def _home(tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        return tmp_path / "config.json"

    def _assert_fix_clears(self, cfg_path, bad: str):
        from trinity_local.health_checks import _check_config
        cfg_path.write_text(bad, encoding="utf-8")
        before = _check_config()
        assert not before.ok and before.fix, before
        assert self._run_fix(before.fix) == 0
        after = _check_config()
        assert after.ok, f"{before.fix!r} did not clear the warning: {after.detail}"
        assert (cfg_path.parent / "config.json.broken").read_text(encoding="utf-8") == bad, (
            "the repair must keep the user's broken file, not overwrite it")

    def test_broken_json_is_cleared_by_its_own_fix(self, tmp_path, monkeypatch):
        self._assert_fix_clears(self._home(tmp_path, monkeypatch), '{"providers": {')

    def test_json_of_the_wrong_shape_is_cleared_by_its_own_fix(self, tmp_path, monkeypatch):
        self._assert_fix_clears(self._home(tmp_path, monkeypatch), '{"max_turns": 4}')

    def test_a_second_repair_keeps_the_first_broken_file(self, tmp_path, monkeypatch):
        cfg_path = self._home(tmp_path, monkeypatch)
        self._assert_fix_clears(cfg_path, "{first")
        cfg_path.write_text("{second", encoding="utf-8")
        assert self._run_fix("trinity-local config --init") == 0
        assert (tmp_path / "config.json.broken").read_text(encoding="utf-8") == "{first"
        assert (tmp_path / "config.json.broken.2").read_text(encoding="utf-8") == "{second"

    def test_all_providers_off_is_cleared_by_its_own_fix(self, tmp_path, monkeypatch):
        from trinity_local.health_checks import _check_config
        cfg_path = self._home(tmp_path, monkeypatch)
        assert self._run_fix("trinity-local config --init") == 0
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        for provider in raw["providers"].values():
            provider["enabled"] = False
        cfg_path.write_text(json.dumps(raw), encoding="utf-8")
        before = _check_config()
        assert not before.ok and before.fix.startswith("trinity-local config --set"), before
        assert self._run_fix(before.fix) == 0
        assert _check_config().ok

    def test_init_still_leaves_a_working_config_alone(self, tmp_path, monkeypatch):
        cfg_path = self._home(tmp_path, monkeypatch)
        assert self._run_fix("trinity-local config --init") == 0
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        raw["max_turns"] = 7
        cfg_path.write_text(json.dumps(raw), encoding="utf-8")
        assert self._run_fix("trinity-local config --init") == 0
        assert json.loads(cfg_path.read_text(encoding="utf-8"))["max_turns"] == 7
        assert not (tmp_path / "config.json.broken").exists()


class TestDispatchReadyAdviceClosure:
    """Instance 5 (fixed 2026-10-26): dispatch_ready finds the capture host on
    the enriched runtime PATH (venv bin, ~/.local/bin, Homebrew), but the
    install-extension it names looked with a bare shutil.which. Under a
    stripped PATH the check saw the host, the fix said it was missing and
    exited 1, and the warning never cleared."""

    @staticmethod
    def _home(tmp_path, monkeypatch):
        home = tmp_path / "home"
        host = home / ".local" / "bin" / "trinity-local-capture-host"
        host.parent.mkdir(parents=True)
        host.write_text("#!/bin/sh\n", encoding="utf-8")
        host.chmod(0o755)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def test_install_extension_clears_it_under_a_stripped_path(self, tmp_path, monkeypatch):
        from trinity_local.health_checks import _check_dispatch_ready
        self._home(tmp_path, monkeypatch)
        before = _check_dispatch_ready()
        assert "not connected" in before.detail, before
        assert _run_fix(before.fix) == 0, f"{before.fix!r} failed where the check found the host"
        assert _check_dispatch_ready().detail == "launchpad dispatch ready via Chrome extension"

    def test_a_missing_host_is_not_sent_to_install_extension(self, tmp_path, monkeypatch):
        from trinity_local.health_checks import _check_dispatch_ready
        self._home(tmp_path, monkeypatch)
        assert _run_fix("trinity-local install-extension") == 0
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda *a, **k: None)
        before = _check_dispatch_ready()
        assert "not on PATH" in before.detail, before
        assert _run_fix("trinity-local install-extension") == 1, (
            "install-extension cannot install the host; the check must not name it here")
        assert "install.sh" in before.fix and "install-extension" not in before.fix, before.fix


class TestPromptsSeededAdviceClosure:
    """prompts_seeded tells an empty install to run `trinity-local import-export
    <path-to-export>`. This runs that exact fix string (placeholder filled with a
    one-conversation ChatGPT export) through the CLI parser and checks the warning
    clears: the import writes the prompt corpus the check reads."""

    def test_import_export_clears_it(self, tmp_path, monkeypatch, capsys):
        import shlex

        from trinity_local.health_checks import _check_prompts_seeded
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")  # TF-IDF is enough: the check counts nodes
        export = tmp_path / "export"
        export.mkdir()

        def node(nid, role, text, parent, children):
            return nid, {"id": nid, "parent": parent, "children": children, "message": {
                "id": nid, "author": {"role": role}, "create_time": 1718000000.0,
                "content": {"content_type": "text", "parts": [text]}, "metadata": {}}}

        mapping = dict([
            node("root", "system", "", None, ["u1"]),
            node("u1", "user", "How should I structure a recursive descent parser?", "root", ["a1"]),
            node("a1", "assistant", "One function per precedence level.", "u1", []),
        ])
        (export / "conversations.json").write_text(json.dumps([{
            "conversation_id": "conv-closure", "title": "Parsers", "create_time": 1718000000.0,
            "update_time": 1718000100.0, "current_node": "a1", "mapping": mapping,
        }]), encoding="utf-8")

        before = _check_prompts_seeded()
        assert before.fix and "<path-to-export>" in before.fix, before
        fix = " ".join(shlex.quote(str(export)) if tok == "<path-to-export>" else shlex.quote(tok)
                       for tok in shlex.split(before.fix.split("#", 1)[0]))
        _run_fix(fix)
        capsys.readouterr()
        after = _check_prompts_seeded()
        assert not after.fix and after.detail != before.detail, (
            f"{before.fix!r} did not clear prompts_seeded: {after.detail!r}")
        assert after.detail.startswith("1 prompt node indexed"), after.detail  # one node, singular


# A directory name a shell would act on if the advice left it unquoted or in double quotes.
HOSTILE = "tri $(touch PWNED) `touch PWNED2` x"


def _paste(fix: str, cwd: Path) -> None:
    """Run a check's shell advice the way a user does: pasted into sh."""
    import subprocess

    subprocess.run(["sh", "-c", fix], cwd=cwd, check=True)


class TestRetiredDirsAdviceClosure:
    """retired_dirs_reclaimable advises `rm -rf` on the directories it lists. Until
    2026-10-26 it put each path in double quotes, where a shell still runs $(...) and
    backticks: from a TRINITY_HOME of that shape the pasted advice ran a command, and the
    rewritten path sent the rm to a different directory. This pastes the fix into sh and
    checks the warning clears, nothing else ran, and the rm reached only what it listed."""

    def test_rm_fix_clears_it_and_runs_nothing_else(self, tmp_path, monkeypatch):
        import re

        from trinity_local.health_checks import _check_retired_dirs_reclaimable
        home = tmp_path / HOSTILE / "trinity"
        for name in ("cache", "task_sync", "prompts"):
            (home / name).mkdir(parents=True)
            (home / name / "f.json").write_text("{}")
        # Where a double-quoted path lands once the shell has expanded $(...) and `...` to nothing.
        decoy = tmp_path / re.sub(r"\$\([^)]*\)|`[^`]*`", "", HOSTILE) / "trinity" / "cache"
        decoy.mkdir(parents=True)
        (decoy / "keep.txt").write_text("not on the list")
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.setenv("TRINITY_HOME", str(home))

        before = _check_retired_dirs_reclaimable()
        assert before.fix and before.fix.startswith("rm -rf "), before
        _paste(before.fix, cwd)
        assert sorted(p.name for p in cwd.iterdir()) == [], "the pasted advice ran a command"
        assert (decoy / "keep.txt").exists(), "the rm reached a directory it did not list"
        assert (home / "prompts" / "f.json").exists()
        after = _check_retired_dirs_reclaimable()
        assert not after.fix and after.detail == "no retired-feature directories holding disk", after


class TestTrinityHomeAdviceClosure:
    """trinity_home_writeable advises `chmod u+w` on the home it could not write. The path
    went in bare, so a home with a space split into several arguments and one carrying
    $(...) ran it. This makes the home read-only, pastes the fix into sh, and checks the
    warning clears and nothing else ran."""

    def test_chmod_fix_clears_it(self, tmp_path, monkeypatch):
        import os

        import pytest

        from trinity_local.health_checks import _check_trinity_home
        if os.geteuid() == 0:
            pytest.skip("root writes through a read-only mode bit")
        home = tmp_path / HOSTILE / "trinity"
        home.mkdir(parents=True)
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.setenv("TRINITY_HOME", str(home))
        home.chmod(0o500)
        try:
            before = _check_trinity_home()
            assert not before.ok and before.fix, before
            _paste(before.fix, cwd)
            assert sorted(p.name for p in cwd.iterdir()) == [], "the pasted advice ran a command"
            after = _check_trinity_home()
            assert after.ok, after
        finally:
            home.chmod(0o700)


class TestSkillFreshnessAdviceClosure:
    """skill_freshness says "trinity-local update". It read ~/.claude/skills/trinity while
    update pulls ~/.trinity/code whenever that is a checkout, so a pre-pivot install that
    kept its own legacy clone (install.sh leaves a real directory alone) was warned about
    a repository the fix never touched, and a canonical-only install was never checked.
    Both now resolve the checkout through one function. These use real git: an origin,
    the clones an install leaves, upstream commits, then the fix through the real CLI
    parser with only update's post-pull install-mcp and status runs stubbed."""

    @staticmethod
    def _git(*args, cwd):
        import subprocess
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

    def _install(self, tmp_path, monkeypatch, legacy_clone: bool):
        import shutil

        import pytest
        if shutil.which("git") is None:
            pytest.skip("git not available")
        home = tmp_path / "home"
        origin, work = tmp_path / "origin.git", tmp_path / "work"
        self._git("init", "-q", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
        self._git("clone", "-q", str(origin), str(work), cwd=tmp_path)
        self._git("config", "user.email", "t@t.co", cwd=work)
        self._git("config", "user.name", "t", cwd=work)

        def push(msg):
            (work / "f.txt").write_text(msg)
            self._git("add", "f.txt", cwd=work)
            self._git("commit", "-q", "-m", msg, cwd=work)
            self._git("push", "-q", "origin", "HEAD:main", cwd=work)

        push("base")
        clones = [home / ".trinity" / "code"] + ([home / ".claude" / "skills" / "trinity"] if legacy_clone else [])
        for d in clones:
            d.parent.mkdir(parents=True, exist_ok=True)
            self._git("clone", "-q", "--branch", "main", str(origin), str(d), cwd=tmp_path)
        push("fix 1")
        push("fix 2")
        for d in clones:
            self._git("fetch", "-q", "origin", cwd=d)   # the cached refs status reads
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("TRINITY_SKILL_DIR", raising=False)
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        import sys
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))  # not a uv tool install, whatever runs the tests
        return clones

    @staticmethod
    def _stub_post_pull(monkeypatch):
        import subprocess
        import sys
        from types import SimpleNamespace

        real = subprocess.run

        def run(argv, *a, **kw):
            if argv[:3] == [sys.executable, "-m", "trinity_local.main"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return real(argv, *a, **kw)
        monkeypatch.setattr(subprocess, "run", run)

    def _assert_update_clears_it(self, monkeypatch, capsys):
        from trinity_local.health_checks import _check_skill_freshness
        before = _check_skill_freshness()
        assert not before.ok and before.fix.startswith("trinity-local update"), before
        assert "2 commit(s) behind" in before.detail, before
        self._stub_post_pull(monkeypatch)
        assert _run_fix(before.fix) == 0
        capsys.readouterr()
        after = _check_skill_freshness()
        assert after.ok and "up to date" in after.detail, (
            f"{before.fix!r} did not clear skill_freshness: {after.detail!r}")

    def test_update_clears_it_with_a_kept_legacy_clone(self, tmp_path, monkeypatch, capsys):
        self._install(tmp_path, monkeypatch, legacy_clone=True)
        self._assert_update_clears_it(monkeypatch, capsys)

    def test_a_canonical_only_install_is_checked(self, tmp_path, monkeypatch, capsys):
        self._install(tmp_path, monkeypatch, legacy_clone=False)
        self._assert_update_clears_it(monkeypatch, capsys)

    def test_a_uv_install_ignores_a_clone_it_does_not_run(self, tmp_path, monkeypatch):
        """On a uv tool install update re-runs the installer and never pulls a checkout, so
        a clone left behind by an earlier git install, however stale, is not warned about:
        no fix this check could name would clear it."""
        import sys

        from trinity_local.health_checks import _check_skill_freshness
        self._install(tmp_path, monkeypatch, legacy_clone=True)
        assert not _check_skill_freshness().ok   # the clones are behind
        prefix = tmp_path / "uv-tool"
        prefix.mkdir()
        (prefix / "uv-receipt.toml").write_text("")
        monkeypatch.setattr(sys, "prefix", str(prefix))
        result = _check_skill_freshness()
        assert result.ok and not result.fix and "installed with uv" in result.detail, result
