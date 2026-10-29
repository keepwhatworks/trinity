"""Shared helpers used by multiple command modules."""
from __future__ import annotations

from pathlib import Path


def read_text_file(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).expanduser().read_text(encoding="utf-8")


