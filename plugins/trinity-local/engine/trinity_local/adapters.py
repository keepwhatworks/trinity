"""Provider adapter discovery and status.

Checks which provider CLIs are available, their versions, and transcript paths.
Used by ``trinity-local status`` to render the provider-adapter table.
(The dedicated ``adapters`` CLI verb was retired 2026-05-27 — see
``retired_names.py``; this library module survives because status
and launchpad_data both read AdapterStatus.)
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime_env import run_with_runtime_env


@dataclass
class AdapterStatus:
    """Status of a single provider adapter."""
    provider: str
    cli_name: str
    installed: bool
    version: str | None = None
    transcript_root: str | None = None
    transcript_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


_PROVIDER_SPECS: list[dict[str, Any]] = [
    {
        "provider": "claude",
        "cli_name": "claude",
        "version_args": ["claude", "--version"],
        "transcript_root": lambda: Path.home() / ".claude" / "projects",
        "glob": "**/*.jsonl",
    },
    {
        "provider": "codex",
        "cli_name": "codex",
        "version_args": ["codex", "--version"],
        "transcript_root": lambda: Path.home() / ".codex" / "sessions",
        "glob": "**/rollout-*.jsonl",
    },
    {
        "provider": "antigravity",
        "cli_name": "agy",
        "version_args": ["agy", "--version"],
        # Antigravity CLI conversations land here as `.pb` protobuf files
        # (~105KB each, one per conversation). The legacy gemini CLI's
        # `~/.gemini/tmp` JSON sessions are obsolete after the Antigravity
        # rebrand; if a user still has legacy data there, run a one-time
        # `import-export ~/.gemini/tmp` pass to import. (The earlier
        # seed-from-taste-terminal CLI retired 2026-05-27.)
        "transcript_root": lambda: Path.home() / ".gemini" / "antigravity-cli" / "conversations",
        "glob": "*.pb",
    },
    {
        "provider": "cowork",
        "cli_name": "claude-desktop",
        "version_args": None,  # No CLI version command — desktop app
        "transcript_root": lambda: (
            Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
        ),
        "glob": "local_*.json",
    },
]


def _run_version(args: list[str]) -> str | None:
    """Run a version command and return the first line of output."""
    try:
        result = run_with_runtime_env(
            args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0 and output:
            return output.splitlines()[0].strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _count_transcripts(root: Path, glob: str) -> int:
    """Count transcript files under a root directory."""
    if not root.exists():
        return 0
    try:
        return sum(1 for _ in root.glob(glob))
    except OSError:
        return 0


def check_adapter(spec: dict[str, Any]) -> AdapterStatus:
    """Check one provider adapter."""
    provider = spec["provider"]
    cli_name = spec["cli_name"]
    transcript_root: Path = spec["transcript_root"]()
    glob = spec["glob"]

    # Check CLI availability
    version = None
    installed = False
    error = None

    if spec["version_args"]:
        version = _run_version(spec["version_args"])
        installed = version is not None
        if not installed:
            error = f"{cli_name} not found in PATH"
    else:
        # Desktop app — check transcript dir existence
        installed = transcript_root.exists()
        if not installed:
            error = f"Transcript directory not found: {transcript_root}"

    transcript_count = _count_transcripts(transcript_root, glob)

    return AdapterStatus(
        provider=provider,
        cli_name=cli_name,
        installed=installed,
        version=version,
        transcript_root=str(transcript_root) if transcript_root.exists() else None,
        transcript_count=transcript_count,
        error=error,
    )


def check_all_adapters() -> list[AdapterStatus]:
    """Check all known provider adapters.

    Each check_adapter runs a `<cli> --version` subprocess + a
    transcript-dir glob count — independent per provider. Sequential
    cost on the real install: ~290ms (3 subprocs × ~70ms + claude's
    39K-file glob ~100ms). Parallel cost: ~110ms wall (bounded by
    claude's count). Saves ~180ms × 2 callsites (status + launchpad
    render) per process invocation.
    """
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(_PROVIDER_SPECS)) as ex:
        return list(ex.map(check_adapter, _PROVIDER_SPECS))
