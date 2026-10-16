"""Errors that tell the user what to do (coherence review 2026-10-16).

- A long-lived MCP server keeps the code it started with. When an update deletes a module that
  code imports lazily, every call reaching the import failed with a bare 500 (servers started
  before dispatch_health.py was deleted: a KeyError from the editable finder on every run_council
  and verify, reported by a peer session). It now says to reconnect.
- A mistyped command printed every registered verb, the ~58 hidden ones included.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys

import pytest

from trinity_local import mcp_server


def _call(monkeypatch, exc) -> str:
    async def boom(name, arguments):
        raise exc
    monkeypatch.setattr(mcp_server, "_dispatch_tool", boom)
    with pytest.raises(mcp_server.ToolError) as e:
        asyncio.run(mcp_server.handle_call_tool("run_council", {"task": "x"}))
    return str(e.value)


@pytest.mark.parametrize("exc", [
    ModuleNotFoundError("No module named 'trinity_local.dispatch_health'", name="trinity_local.dispatch_health"),
    KeyError("trinity_local.dispatch_health"),
])
def test_a_module_deleted_after_start_says_reconnect(monkeypatch, exc):
    # dispatch_health.py was on disk when the peer's servers started and is gone now.
    monkeypatch.setattr(mcp_server, "_STARTUP_MODULES",
                        mcp_server._STARTUP_MODULES | {"trinity_local.dispatch_health"})
    text = _call(monkeypatch, exc)
    assert "Reconnect it" in text and "dispatch_health" in text, text


def test_a_module_rewritten_after_start_says_reconnect(monkeypatch):
    exc = ImportError("cannot import name 'gone' from 'trinity_local.council_schema'",
                      name="trinity_local.council_schema")
    monkeypatch.setattr(mcp_server, "_STARTED_AT", 0.0)   # every file is newer than this start
    assert "Reconnect it" in _call(monkeypatch, exc)


def test_a_version_change_says_reconnect_and_names_both(monkeypatch):
    monkeypatch.setattr(mcp_server, "APP_VERSION", "1.7.390")
    text = _call(monkeypatch, ModuleNotFoundError("x", name="trinity_local.never_existed"))
    assert "Reconnect it" in text and "1.7.390" in text, text


def test_after_an_update_a_text_only_keyerror_stays_generic(monkeypatch):
    # The version moved, but this KeyError names no module: the message would read
    # "needs missing trinity_local.x in the cache".
    monkeypatch.setattr(mcp_server, "APP_VERSION", "1.7.390")
    assert "Reconnect" not in _call(monkeypatch, KeyError("missing trinity_local.x in the cache"))


@pytest.mark.parametrize("exc", [
    # Same version, a module that was never there: a bug, which reconnecting would not fix.
    ModuleNotFoundError("No module named 'trinity_local.never_existed'", name="trinity_local.never_existed"),
    # A file that has not changed since the server started.
    ImportError("cannot import name 'gone' from 'trinity_local.council_schema'", name="trinity_local.council_schema"),
    # A KeyError that only mentions a module in its text.
    KeyError("missing trinity_local.dispatch_health in the cache"),
    ModuleNotFoundError("No module named 'numpy'", name="numpy"),
])
def test_without_evidence_of_an_update_the_error_stays_generic(monkeypatch, exc):
    monkeypatch.setattr(mcp_server, "_STARTUP_MODULES",
                        mcp_server._STARTUP_MODULES | {"trinity_local.dispatch_health"})
    monkeypatch.setattr(mcp_server, "_STARTED_AT", 4e9)   # nothing on disk is newer than this
    assert "Reconnect" not in _call(monkeypatch, exc)


def test_a_mistyped_command_is_one_line_not_the_hidden_verb_list(patch_trinity_home):
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", "nosuchverb"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "unknown command 'nosuchverb'" in r.stderr and "install-agent" not in r.stderr, r.stderr


def test_real_commands_still_parse(patch_trinity_home):
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", "status", "--json"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and json.loads(r.stdout)
