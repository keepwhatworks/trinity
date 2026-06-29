from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def project_venv_root(python_executable: str | None = None) -> Path:
    executable = Path(python_executable or sys.executable)
    return executable.expanduser().parent.parent.resolve()


def project_venv_bin(python_executable: str | None = None) -> Path:
    return project_venv_root(python_executable) / "bin"


def runtime_bin_paths(python_executable: str | None = None) -> list[str]:
    candidates = [
        str(project_venv_bin(python_executable)),
        str((Path.home() / ".local" / "bin").expanduser()),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def runtime_path_prefix(python_executable: str | None = None) -> str:
    return ":".join(runtime_bin_paths(python_executable))


def build_runtime_env(
    env: dict[str, str] | None = None,
    *,
    python_executable: str | None = None,
) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    current_path = merged.get("PATH", "")
    parts = runtime_bin_paths(python_executable)
    if current_path:
        parts.append(current_path)
    merged["PATH"] = ":".join(part for part in parts if part)
    # Some entry points (notably the Chrome Native Messaging host) are
    # spawned with a minimal env that omits TERM. Provider CLIs like
    # Gemini sniff TERM to decide color support and print
    # "Warning: 256-color support not detected" when it's missing. Use
    # setdefault so anything the caller already set wins.
    merged.setdefault("TERM", "xterm-256color")
    return merged


def which_on_runtime_path(
    binary: str, *, python_executable: str | None = None
) -> str | None:
    """Resolve ``binary`` against the SAME enriched PATH ``run_with_runtime_env``
    executes with (the venv bin, ``~/.local/bin``, ``/opt/homebrew/bin``,
    ``/usr/local/bin``, then the inherited PATH).

    Use this — NOT a bare ``shutil.which`` — for any "is this provider
    dispatchable?" check, so the gate matches the execution. A bare
    ``shutil.which`` rejects a provider that IS installed and runnable: e.g. a
    Homebrew CLI under a GUI-launched IDE (Cursor/VS Code desktop) whose MCP
    server inherits a stripped PATH lacking ``/opt/homebrew/bin``. The injection
    that lets the execution find it is then defeated by the stricter gate, so the
    council can't dispatch to a provider the user actually has.
    """
    import shutil

    return shutil.which(
        binary, path=build_runtime_env(python_executable=python_executable).get("PATH")
    )


def run_with_runtime_env(
    args: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    input: str | None = None,
    env: dict[str, str] | None = None,
    python_executable: str | None = None,
) -> subprocess.CompletedProcess[Any]:
    resolved_cwd = str(cwd) if isinstance(cwd, Path) else cwd
    return subprocess.run(
        args,
        cwd=resolved_cwd,
        timeout=timeout,
        capture_output=capture_output,
        text=text,
        check=check,
        input=input,
        env=build_runtime_env(env, python_executable=python_executable),
    )
