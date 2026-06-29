"""Detect locally-installed model runtimes (Ollama, MLX) for v1.5 dispatch.

Trinity v1.5 promises "and local models" in the pitch when this works
reliably. This module is the detector layer — finds Ollama / MLX installs
on the user's machine, enumerates available models, and emits ProviderConfig
entries the dispatch layer can use.

Two design choices:

1. **Probe at runtime, not at install.** The user may install Ollama or
   pull new models after Trinity is set up. We detect on demand so the
   moment a new local model is available, the next `ask` call can route
   to it without re-running install-mcp.

2. **Cost = 0 for ALL local models.** That's what makes them attractive
   to the Conductor: for easy subtasks where the user's flagship sub is
   overkill, a local Qwen / DeepSeek call is free. The Conductor sees
   the cost metadata and routes accordingly.

The probes are cheap (one `ollama list`, one filesystem check). Cached
in-process so repeat calls don't re-shell. Cache invalidates on
TTL_SECONDS = 30 since the user might pull a new model mid-session.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Detection cache TTL — short enough that a user who just ran `ollama pull
# qwen3:32b` sees the new model on their next ask without restarting Trinity.
_TTL_SECONDS = 30
_cache: dict[str, tuple[float, object]] = {}


@dataclass
class LocalModel:
    """One detected local model — runtime + name + estimated capability."""

    runtime: str  # "ollama" | "mlx"
    name: str  # provider model ID, e.g. "qwen3:32b" or "deepseek-r1-distill-qwen-32b"
    size_bytes: int | None = None  # if reported by the runtime; helps the Conductor pick

    @property
    def provider_name(self) -> str:
        """Stable Trinity-side identifier for the routing table."""
        return f"{self.runtime}:{self.name}"

    def to_dict(self) -> dict:
        return {
            "runtime": self.runtime,
            "name": self.name,
            "provider_name": self.provider_name,
            "size_bytes": self.size_bytes,
        }


def detect_local_models() -> list[LocalModel]:
    """Return all locally available models across runtimes. Cached for TTL."""
    cached = _cache.get("models")
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]  # type: ignore[return-value]
    models: list[LocalModel] = []
    models.extend(_detect_ollama())
    models.extend(_detect_mlx())
    _cache["models"] = (time.monotonic(), models)
    return models


def _detect_ollama() -> list[LocalModel]:
    """`ollama list` → parse the table. Returns [] if Ollama isn't installed
    or the daemon isn't running. The latter case is silent — Ollama prints to
    stderr but we don't surface it; the user'll see the missing local-model
    routes empty and can investigate. (The launchpad's pool composition view
    will surface this when v1.5 Week 5 lands.)
    """
    if shutil.which("ollama") is None:
        return []
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    # Output looks like:
    #   NAME              ID            SIZE      MODIFIED
    #   qwen3:32b         abc123        20 GB     2 weeks ago
    # First line is the header; subsequent lines are models. Parse loose.
    # The SIZE column shows up as either "20GB" (one token) or "20 GB" (two)
    # depending on Ollama version; handle both.
    models: list[LocalModel] = []
    lines = result.stdout.strip().splitlines()
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        size_bytes = None
        if len(parts) >= 4:
            # Try "20 GB" (value + unit as two tokens).
            size_bytes = _parse_size(f"{parts[2]} {parts[3]}")
        if size_bytes is None and len(parts) >= 3:
            # Fall back to "20GB" (single-token value+unit).
            size_bytes = _parse_size(parts[2])
        models.append(LocalModel(runtime="ollama", name=name, size_bytes=size_bytes))
    return models


def _detect_mlx() -> list[LocalModel]:
    """Look for MLX-compatible models in the standard cache paths. MLX models
    live under ~/.cache/huggingface/hub/ when downloaded via mlx_lm.utils. We
    detect by checking common paths; we don't actually probe MLX itself yet
    (that needs the `mlx-lm` Python package and an import probe; deferred to
    v1.6 alongside MLX dispatch infrastructure).

    For v1.5 ship: MLX detection returns [] if the cache dir doesn't exist.
    When the user has actually populated MLX models, the launchpad shows a
    "Detect MLX models? Set TRINITY_MLX_PATH=..." hint.
    """
    # MLX dispatch shim isn't wired in v1.5 — return empty until v1.6 unless
    # the env var override is set to opt in early.
    import os

    override = os.environ.get("TRINITY_MLX_PATH")
    if not override:
        return []
    path = Path(override)
    if not path.is_dir():
        return []
    # Scan one level deep for model dirs matching mlx_lm naming conventions.
    models: list[LocalModel] = []
    for entry in path.iterdir():
        if entry.is_dir() and (entry / "config.json").exists():
            models.append(LocalModel(runtime="mlx", name=entry.name))
    return models


def _parse_size(text: str) -> int | None:
    """Best-effort parse of `ollama list` size column like "20 GB" or "4.2GB"."""
    text = text.strip().upper()
    multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for suffix, mult in multipliers.items():
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)].strip()) * mult)
            except ValueError:
                return None
    return None


def clear_detection_cache() -> None:
    """Forced cache invalidation. Tests use this; production rarely needs it."""
    _cache.clear()
