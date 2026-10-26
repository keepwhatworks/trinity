"""Codex cuts off an MCP tool call after 300 s unless the server registration sets `tool_timeout_sec`
(measured 2026-10-20, codex-cli 0.155.1: a 90 s tool returned, a 420 s tool failed with "timed out
awaiting tools/call after 300s"). A council often runs longer, so install-mcp writes a timeout and
`install --check` flags a registration without one. The flag's fix must clear it (advice closure)."""
from __future__ import annotations

from pathlib import Path

from trinity_local import setup_check
from trinity_local.commands import install


def _home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".codex").mkdir()
    return tmp_path / ".codex" / "config.toml"


def test_install_writes_a_timeout_codex_honours(tmp_path, monkeypatch):
    target = _home(monkeypatch, tmp_path)
    assert install._write_codex_toml_mcp_config(target, "/x/trinity-local", ["--mcp"])
    assert f"tool_timeout_sec = {install.CODEX_TOOL_TIMEOUT_S}" in target.read_text()
    assert install.CODEX_TOOL_TIMEOUT_S >= setup_check.CODEX_TIMEOUT_FLOOR_S > 300


def test_a_registration_without_a_timeout_is_flagged_and_install_mcp_clears_it(tmp_path, monkeypatch):
    target = _home(monkeypatch, tmp_path)
    target.write_text('model = "gpt"\n\n[mcp_servers.trinity-local]\ncommand = "/x"\nargs = ["--mcp"]\n')
    assert setup_check.codex_tool_timeout() is None
    assert setup_check.codex_timeout_fix() == "trinity-local install-mcp"
    # the fix it names, run for real:
    assert install._write_codex_toml_mcp_config(target, "/x/trinity-local", ["--mcp"])
    assert setup_check.codex_tool_timeout() == install.CODEX_TOOL_TIMEOUT_S
    assert setup_check.codex_timeout_fix() is None
    assert 'model = "gpt"' in target.read_text(), "the user's other settings survive"


def test_a_short_timeout_is_flagged(tmp_path, monkeypatch):
    target = _home(monkeypatch, tmp_path)
    target.write_text('[mcp_servers.trinity-local]\ncommand = "/x"\nargs = []\ntool_timeout_sec = 300\n')
    assert setup_check.codex_tool_timeout() == 300
    assert setup_check.codex_timeout_fix() == "trinity-local install-mcp"
