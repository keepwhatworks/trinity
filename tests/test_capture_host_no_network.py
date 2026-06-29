"""Regression guard: capture_host.py must not import networking modules.

The v1.6 spec invariant is "no outbound network from the host process"
— the capture host's only job is to write a captured payload to disk.
If anyone adds ``requests``/``httpx``/``urllib.request`` etc. to that
module, the "your data, your machine" claim breaks silently.

Same shape as the existing AST-based guards in tests/.
"""

from __future__ import annotations

import ast
from pathlib import Path



BANNED_TOP_LEVEL = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "socket",
    "ssl",
    "http",
    "urllib",
}


def _gather_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def test_capture_host_has_no_network_imports():
    repo_root = Path(__file__).resolve().parent.parent
    capture_host = repo_root / "src" / "trinity_local" / "capture_host.py"
    assert capture_host.exists(), f"expected {capture_host} to exist (v1.6 capture host)"
    imports = _gather_imports(capture_host.read_text())
    banned_present = imports & BANNED_TOP_LEVEL
    assert not banned_present, (
        f"capture_host.py must not import networking modules; found {banned_present}. "
        "The v1.6 'no outbound network from the host' invariant depends on this."
    )
