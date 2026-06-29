"""Security guard: the capture-host dispatch passes the extension-supplied `task`
to the CLI as a LITERAL argv element — never interpolated into a shell string — so
an adversarial task can't execute. The auto-dispatch sibling of the copy-paste
escapeBashArg guard (test_topology_launch_chip_shell_safe_browser), at a
HIGHER-severity sink: this path runs automatically on dispatch — no user paste.

`capture_host` is the untrusted-input boundary ([[capture_host_untrusted_boundary]]):
the Chrome extension hands it an `{kind, task, ...}` payload that becomes a local
subprocess. `_run_action` builds `argv = [bin, subcommand]` and appends
`["--<arg>", str(value)]` per allowlisted field, then `subprocess.Popen(argv)` /
`subprocess.run(argv)` — a LIST, never `shell=True`. So `task="$(rm -rf ~)"` reaches
trinity-local as one inert argv string, not a shell command.

Existing capture_host tests cover path-traversal + spoofed-KIND rejection
(test_capture_host_stdio, test_phase8_integration) but never assert that a VALID
kind's attacker-influenceable `task` stays a single literal argv element — i.e. the
argv-not-shell invariant itself. A refactor to `subprocess.run(f"… {task}",
shell=True)` would pass every existing test while opening RCE on the user's box.

Strategy: monkeypatch subprocess.Popen to CAPTURE argv without executing, dispatch
a `launch-council` with a shell-injection-shaped task, and assert (a) the task is
exactly one consecutive argv element after `--task` (unsplit, byte-for-byte), and
(b) no `shell=True` was passed. Plus the primitive type-guard (a list `task` is
rejected, so a payload can't smuggle extra argv elements), and a source-level
defense-in-depth check that NO capture_host path uses `shell=True`.

Mutation-proven: switch the Popen call to `shell=True` with an interpolated string
→ the "one literal element" / "no shell=True" assertions red. (Verified by hand:
the live dispatch puts the task verbatim at argv index after '--task'.)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trinity_local import capture_host

# Shell-injection-shaped but harmless if (correctly) treated as a literal: command
# substitution, a statement separator, a quote, and a comment.
_EVIL_TASK = '$(touch /tmp/trinity_pwned_xyz); rm -rf / # " `id`'


class _FakePopen:
    """Captures argv + kwargs instead of spawning. Mimics the bits _run_action
    reads off the returned child (just `.pid`)."""

    captured: dict = {}

    def __init__(self, argv, **kwargs):
        type(self).captured = {"argv": argv, "kwargs": kwargs}
        self.pid = 4242


@pytest.fixture
def capture_argv(monkeypatch):
    _FakePopen.captured = {}
    # _run_action does a local `import subprocess` → same module object, so patching
    # the stdlib attribute intercepts the dispatch without spawning anything.
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    # Don't let runtime-env construction influence the test.
    monkeypatch.setattr(capture_host, "build_runtime_env", lambda: {}, raising=False)
    return _FakePopen


def test_dispatch_passes_task_as_single_literal_argv_element(capture_argv):
    result = capture_host._run_action(
        {"kind": "launch-council", "task": _EVIL_TASK, "status_token": "tok_1"}
    )
    assert result.get("ok") is True, f"valid launch-council dispatch failed: {result}"

    cap = capture_argv.captured
    assert cap, "subprocess.Popen was never called — the dispatch didn't reach the CLI"
    argv = cap["argv"]
    assert isinstance(argv, list), (
        f"the CLI was invoked with a non-list argv ({type(argv).__name__}) — a shell "
        "string would mean the task is interpolated into a command (injection)"
    )
    # The task must appear EXACTLY once, as the single element right after '--task'.
    assert "--task" in argv, f"--task flag missing from argv: {argv}"
    ti = argv.index("--task")
    assert argv[ti + 1] == _EVIL_TASK, (
        "the task was not passed as one literal argv element — it was split or "
        f"interpolated (argv[{ti + 1}]={argv[ti + 1]!r}, expected the verbatim task)"
    )
    # And it must not leak into any OTHER element (no duplication/splitting).
    assert sum(1 for a in argv if _EVIL_TASK in a) == 1, (
        f"the task text appears in more than one argv element (split/duplicated): {argv}"
    )
    # No shell=True anywhere in the dispatch kwargs.
    assert capture_argv.captured["kwargs"].get("shell") is not True, (
        "subprocess.Popen was called with shell=True — the argv would be parsed by a "
        "shell and the task's $(...) / ; / ` would execute (RCE on dispatch)"
    )


def test_dispatch_rejects_non_primitive_task(capture_argv):
    # The primitive type-guard stops a payload from smuggling a LIST that would
    # expand into multiple argv elements (argv-splat injection).
    result = capture_host._run_action(
        {"kind": "launch-council", "task": ["a", "; rm -rf /", "b"], "status_token": "t"}
    )
    assert result.get("ok") is False, "a non-primitive (list) task must be rejected"
    assert "primitive" in (result.get("error") or "").lower(), (
        f"rejection should name the primitive-type requirement: {result}"
    )
    assert not capture_argv.captured, "a rejected payload must NOT reach subprocess"


def test_no_capture_host_path_uses_shell_true():
    # Defense-in-depth, source-level: not one dispatch path may use shell=True.
    src = (Path(capture_host.__file__)).read_text(encoding="utf-8")
    assert "shell=True" not in src, (
        "capture_host introduced a shell=True subprocess — every dispatch must use "
        "an argv LIST so extension-supplied fields can't be shell-interpreted"
    )
