"""Shared shebang-script runtime: venv bootstrap + audit log.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).
Every script in scripts/ uses these helpers so the dual interface
(shebang-runnable + importable) shares one implementation of the
venv bootstrap and audit-log contract.

Standalone use (the skill tier path):

    # In scripts/embed.py:
    from scripts._runtime import bootstrap_or_continue, audit_log

    if __name__ == "__main__":
        bootstrap_or_continue(
            script_name="embed",
            requirements=["sentence-transformers>=2.7", "torch>=2.1", "numpy>=1.26"],
        )
        # ... CLI body ...

When run directly via `python3 scripts/embed.py`, `bootstrap_or_continue`
checks for `~/.trinity/.venvs/embed/`. If missing OR if `requirements`
are not satisfied, it creates the venv + installs deps, then re-execs
the script with the venv's python. The CLI body runs with deps available.

Importable use (the pip tier path):

    from scripts.embed import embed_batch
    vecs = embed_batch(["hello", "world"])

When imported, the venv bootstrap is never hit (the `__name__ == "__main__"`
guard short-circuits). Deps are assumed to be installed in the current
interpreter (pip installs them at wheel install time).

Audit log contract:

    Every CLI invocation calls `audit_log(...)` exactly once before exit.
    Format: append-only JSONL at `~/.trinity/audit.log`. Lines are atomic
    via POSIX O_APPEND for writes < PIPE_BUF (~512B); each record is
    designed to fit comfortably.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _trinity_home() -> Path:
    """Source of truth: $TRINITY_HOME or ~/.trinity/."""
    override = os.environ.get("TRINITY_HOME")
    return Path(override) if override else Path.home() / ".trinity"


def _venvs_dir() -> Path:
    d = _trinity_home() / ".venvs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audit_log_path() -> Path:
    home = _trinity_home()
    home.mkdir(parents=True, exist_ok=True)
    return home / "audit.log"


def audit_log(
    *,
    script: str,
    operation: str,
    args: dict[str, Any] | None = None,
    outcome: str = "ok",
    detail: str | None = None,
    tier: str | None = None,
    trust_mode: str = "default",
) -> None:
    """Append one line to ~/.trinity/audit.log.

    `args` is shallow-sanitized: string/int/float/bool/None pass through;
    anything else is stringified + length-capped to 200 chars. Don't put
    user prompt content here — only categorical operation metadata.

    Cross-tier propagation: `tier` is the ORIGINATING tier (council
    `c18f739a0234aa58` verdict), read from `TRINITY_ORIGIN_TIER` env
    when set. Native-host spawned subprocesses are stamped tier=extension
    even though they run trinity-local (= pip-tier process).

    Failure surfacing: if audit append fails (disk full, perms,
    file deleted) we MUST NOT silently swallow — the audit log IS the
    moat. Emit a one-line stderr warning so the user/agent notices.
    Per-process rate-limited to avoid spam if every call fails.

    Atomic-append: use os.open + O_APPEND + single os.write. PIPE_BUF
    bounds atomicity ONLY for pipes/FIFOs; for regular files, atomic
    append is the kernel's commitment. Single write() call → single
    atomic append (per POSIX O_APPEND semantics).
    """
    # Originating-tier resolution: env var wins (set by extension/skill
    # spawners), explicit `tier` arg is the fallback, "skill" default.
    if tier is None:
        tier = os.environ.get("TRINITY_ORIGIN_TIER", "skill")
    origin_action = os.environ.get("TRINITY_ORIGIN_ACTION")
    invocation_id = os.environ.get("TRINITY_INVOCATION_ID")

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "script": script,
        "operation": operation,
        "tier": tier,
        "trust_mode": trust_mode,
        "outcome": outcome,
    }
    if origin_action:
        record["origin_action"] = origin_action[:120]
    if invocation_id:
        record["invocation_id"] = invocation_id[:120]
    if detail:
        record["detail"] = detail[:200]
    if args:
        sanitized: dict[str, Any] = {}
        for k, v in args.items():
            if v is None or isinstance(v, (str, int, float, bool)):
                sanitized[k] = v if not isinstance(v, str) else v[:120]
            else:
                sanitized[k] = str(type(v).__name__)
        record["args"] = sanitized

    line = json.dumps(record, separators=(",", ":")) + "\n"
    encoded = line.encode("utf-8")

    try:
        # Low-level atomic append. O_APPEND on regular files = each write
        # appends at the current end-of-file as one indivisible op.
        path = _audit_log_path()
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
    except OSError as exc:
        # LOUD failure (council c18f739a verdict): the audit log IS the
        # moat. If we can't write to it, the user must see it. Rate-limit
        # per-process to avoid spamming when every call fails.
        if not getattr(audit_log, "_failure_warned", False):
            try:
                import sys as _sys
                print(
                    f"⚠ Trinity audit log write FAILED ({type(exc).__name__}): "
                    f"{exc}. Operations will continue but Trinity's "
                    "supervision ledger has a gap.",
                    file=_sys.stderr,
                )
            except Exception:
                pass
            audit_log._failure_warned = True  # type: ignore[attr-defined]


def _script_venv_dir(script_name: str) -> Path:
    return _venvs_dir() / script_name


def _venv_python(venv_dir: Path) -> Path:
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_venv(script_name: str, requirements: list[str]) -> Path:
    """Create or reuse a venv at ~/.trinity/.venvs/<script_name>/.

    Returns the path to the venv's Python interpreter. Idempotent: if
    the venv exists AND all `requirements` are satisfied (checked
    cheaply via `pip show`), returns immediately.
    """
    venv_dir = _script_venv_dir(script_name)
    python = _venv_python(venv_dir)

    if not python.exists():
        # First-time create.
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True, capture_output=True,
        )

    # Check whether all requirements are installed. Cheap: `pip show
    # <pkg>` returns non-zero if missing.
    needs_install: list[str] = []
    for req in requirements:
        # Strip version specifiers for the show check.
        pkg = req.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].strip()
        result = subprocess.run(
            [str(python), "-m", "pip", "show", pkg],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            needs_install.append(req)

    if needs_install:
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", *needs_install],
            check=True, capture_output=True,
        )

    return python


def bootstrap_or_continue(*, script_name: str, requirements: list[str]) -> None:
    """Idempotent dual-mode entry point.

    Called from `if __name__ == "__main__":` blocks. Behavior:

    - If a sentinel env var (`TRINITY_SCRIPT_BOOTSTRAPPED=<script_name>`)
      is already set, we ARE running under the venv — return immediately
      and let the CLI body run.

    - Otherwise, ensure the venv exists with `requirements` installed,
      then re-exec the current script with the venv's python. The
      sentinel env var is set on the child to prevent infinite re-exec.

    Important: when imported (not run as `__main__`), this function is
    never called — the pip tier's deps are pre-installed.
    """
    sentinel = "TRINITY_SCRIPT_BOOTSTRAPPED"
    if os.environ.get(sentinel) == script_name:
        return

    # Skip the bootstrap entirely if the user has explicitly opted out
    # (e.g., they're running inside a venv that already has the deps).
    if os.environ.get("TRINITY_SKIP_VENV_BOOTSTRAP") == "1":
        return

    python = _ensure_venv(script_name, requirements)
    env = {**os.environ, sentinel: script_name}
    # Re-exec the current script with the venv python. argv[0] is the
    # script path; everything after passes through.
    os.execvpe(str(python), [str(python), *sys.argv], env)


def read_input_json(arg: str | None = None) -> Any:
    """Read JSON from stdin (no arg) or a file path (arg given).

    Standard input contract: every script accepts a JSON object on
    stdin OR a path to a JSON file as positional arg.
    """
    if arg and arg != "-":
        return json.loads(Path(arg).read_text(encoding="utf-8"))
    return json.loads(sys.stdin.read())


def write_output_json(data: Any, out_path: str | None = None) -> None:
    """Write JSON to stdout (no path) or a file (path given)."""
    payload = json.dumps(data, separators=(",", ":")) + "\n"
    if out_path and out_path != "-":
        Path(out_path).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
        sys.stdout.flush()
