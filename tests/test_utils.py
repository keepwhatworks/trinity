"""Tests for shared utilities."""
from __future__ import annotations

import os
from pathlib import Path

from trinity_local.utils import atomic_write_text, now_iso, stable_id


class TestNowIso:
    def test_format(self):
        ts = now_iso()
        assert ts.endswith("+00:00")
        # Should not have microseconds
        assert "." not in ts

    def test_different_calls_monotonic(self):
        ts1 = now_iso()
        ts2 = now_iso()
        assert ts2 >= ts1


class TestStableId:
    def test_deterministic(self):
        id1 = stable_id("task", "hello", "world")
        id2 = stable_id("task", "hello", "world")
        assert id1 == id2

    def test_prefix(self):
        result = stable_id("task", "test")
        assert result.startswith("task_")

    def test_length(self):
        result = stable_id("action", "test")
        # prefix + _ + 16 hex chars
        assert len(result) == len("action_") + 16

    def test_different_inputs_different_ids(self):
        id1 = stable_id("task", "alpha")
        id2 = stable_id("task", "beta")
        assert id1 != id2

    def test_different_prefixes_different_ids(self):
        id1 = stable_id("task", "hello")
        id2 = stable_id("action", "hello")
        assert id1 != id2


class TestAtomicWriteText:
    def test_basic_write(self, tmp_path):
        target = tmp_path / "out.json"
        atomic_write_text(target, '{"hello": "world"}')
        assert target.read_text() == '{"hello": "world"}'

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "out.txt"
        atomic_write_text(target, "ok")
        assert target.read_text() == "ok"

    def test_overwrite_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("OLD")
        atomic_write_text(target, "NEW")
        assert target.read_text() == "NEW"

    def test_tmp_file_cleaned_up_on_success(self, tmp_path):
        """The PID-stamped tmp file must NOT remain after successful
        rename — otherwise the directory accumulates `.tmp.<pid>`
        orphans on every write."""
        target = tmp_path / "out.txt"
        atomic_write_text(target, "ok")
        tmp_orphans = list(tmp_path.glob("*.tmp*"))
        assert tmp_orphans == [], (
            f"atomic_write_text left tmp orphans: {tmp_orphans}"
        )

    def test_tmp_file_uses_pid_so_concurrent_writers_dont_collide(self, tmp_path):
        """Cross-process safety: two simultaneous writers must use
        DIFFERENT tmp paths so neither sees a half-written file from
        the other. We can't easily fork in a unit test, but we can
        assert the tmp path contains the PID."""
        target = tmp_path / "out.txt"
        # Track tmp paths that get created by intercepting Path.write_text
        # is overkill — easier: monkey-patch tmp.replace to capture the
        # tmp path BEFORE it gets cleaned up.
        captured: dict[str, Path] = {}
        real_replace = Path.replace

        def capturing_replace(self, target_arg):
            captured["tmp"] = self
            return real_replace(self, target_arg)

        Path.replace = capturing_replace
        try:
            atomic_write_text(target, "ok")
        finally:
            Path.replace = real_replace

        tmp_path_used = captured["tmp"]
        assert str(os.getpid()) in tmp_path_used.name, (
            f"tmp path {tmp_path_used.name!r} doesn't carry PID — "
            "two concurrent writers in different processes would "
            "share the same tmp file and clobber each other"
        )

    def test_partial_write_doesnt_leave_corrupt_target(self, tmp_path, monkeypatch):
        """The core invariant: if the write fails mid-stream, the
        canonical path must NOT exist in a half-written state. With
        atomic_write_text, this means tmp.write_text raising leaves
        the target untouched (no rename happened)."""
        target = tmp_path / "out.txt"
        target.write_text("OLD_CONTENT")

        # Force tmp.write_text to fail. Simulate disk full / kill.
        def boom(self, content, encoding="utf-8"):
            # Write half then raise — mimics a real partial write
            # interrupting an OS-level write syscall.
            raise OSError("simulated disk full mid-write")

        monkeypatch.setattr(Path, "write_text", boom)

        try:
            atomic_write_text(target, "NEW_CONTENT_LARGER")
        except OSError:
            pass

        # The canonical path still holds the OLD content — atomic.
        assert target.read_text() == "OLD_CONTENT", (
            "atomic_write_text left the canonical path corrupted "
            "after a failed write — atomicity violated"
        )


class TestNoInlineAtomicWritePattern:
    """Earned 2026-05-16 after Principle #17 audit found the tmp+rename
    pattern inlined in 5 places (cortex, cold_start, capture_host,
    incremental_ingest, council_runtime). After refactor, only
    ``utils.atomic_write_text`` should carry the pattern — anyone
    reintroducing inline ``tmp = path.with_suffix(...".tmp")`` is
    duplicating logic that's already a helper. Same shape as
    test_no_module_level_env_mutation.py: a structural ban prevents
    re-drift after a one-time cleanup.
    """

    def test_only_utils_carries_the_atomic_write_inline(self):
        import re
        from pathlib import Path as P

        repo = P(__file__).resolve().parent.parent
        src = repo / "src" / "trinity_local"
        offenders: list[tuple[str, int, str]] = []
        # Match: path.with_suffix( ... ".tmp" ... )  — the inline
        # signature of the tmp+rename pattern. The utils.py
        # implementation is the only allowed home.
        pattern = re.compile(r'\.with_suffix\([^)]*"\.tmp')
        for py in src.rglob("*.py"):
            if py.name == "utils.py":
                continue
            text = py.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    rel = str(py.relative_to(repo))
                    offenders.append((rel, lineno, line.strip()))
        assert not offenders, (
            "Inline tmp+rename atomic-write pattern reintroduced: "
            f"{offenders}. Use `utils.atomic_write_text(path, content)` "
            "instead — that helper already handles tmp suffix, parent-"
            "dir creation, PID-stamping for cross-process safety, and "
            "tmp cleanup on success."
        )
