"""Prompt content and model answers stay on the machine; only categorical telemetry may leave (founder lock 5).
tests/test_telemetry_no_pii.py pins the telemetry payload, but nothing stopped a new egress channel elsewhere in
src/ (internal/rule-enforcement.md, 2026-10-26). Every module that imports a network library is listed here with
the reason it may, and the two local ones are pinned to the loopback interface; a new one fails until someone
reviews it and lists it. The provider CLIs (claude, codex, agy) are the council channel by design: they run as
subprocesses on the user's own subscriptions and are not network imports."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import urlparse

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
_NETWORK = ("urllib.request", "http.client", "http.server", "socket", "socketserver", "ssl", "requests", "httpx",
            "aiohttp", "urllib3", "websocket", "websockets", "smtplib", "ftplib", "xmlrpc.client")
ALLOWED = {
    "telemetry.py": "the categorical telemetry send; its payload is pinned by tests/test_telemetry_no_pii.py",
    "core_gate.py": "the local build-time judge (Ollama on localhost); pinned to loopback below",
    "commands/portal.py": "the launchpad server; pinned to 127.0.0.1 below",
}


def _network_imports(source: str) -> set[str]:
    found = set()
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            # `from urllib import request` and `from http import client` name the network module in the alias
            names = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
        else:
            continue
        found |= {m for m in names if any(m == x or m.startswith(x + ".") for x in _NETWORK)}
    return found


def test_the_detector_sees_a_network_import():
    assert _network_imports("import urllib.request\n") == {"urllib.request"}
    assert _network_imports("from urllib import request\n") == {"urllib.request"}
    assert _network_imports("import socket as s\n") == {"socket"}
    assert _network_imports("import urllib.parse\nimport json\n") == set()


def test_only_reviewed_modules_import_a_network_library():
    scanned, found = 0, {}
    for p in sorted(SRC.rglob("*.py")):
        scanned += 1
        hits = _network_imports(p.read_text(encoding="utf-8"))
        if hits:
            found[p.relative_to(SRC).as_posix()] = sorted(hits)
    assert scanned > 100, scanned
    unreviewed = {m: h for m, h in found.items() if m not in ALLOWED}
    assert not unreviewed, (f"new network code in src/: {unreviewed}. Prompt content and model answers must not "
                            "leave the machine; review the channel and add the module to ALLOWED with its reason.")
    assert set(ALLOWED) <= set(found), f"listed but no longer networked, drop them: {set(ALLOWED) - set(found)}"


def test_the_local_exceptions_stay_on_loopback():
    from trinity_local import core_gate

    assert urlparse(core_gate.STANCE_URL).hostname in {"localhost", "127.0.0.1"}, core_gate.STANCE_URL
    portal = (SRC / "commands" / "portal.py").read_text(encoding="utf-8")
    binds = re.findall(r"TCPServer\(\(\s*([^,]+),", portal)
    assert binds and all(b.strip() in {'"127.0.0.1"', "'127.0.0.1'"} for b in binds), binds
