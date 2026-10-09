"""Guards for state_paths.embedder_install_command: the command that adds the real-embedder
extras to THIS install. There is no PyPI package, so `pip install 'trinity-local[mlx]'` is never
an answer. Found in a sandboxed run of the one-line installer (2026-10-08): the old command
assumed an install layout (~/.trinity/code + venv) no installer creates, so a new user's lens
refused to build and both printed fixes failed.
"""
from __future__ import annotations

import sys

from trinity_local import state_paths
from trinity_local.state_paths import embedder_fix_command, embedder_install_command
from trinity_local.telemetry import _resolve_app_version

PYPI_404 = "pip install 'trinity-local[mlx]'"


def _no_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr("trinity_local.config.project_root", lambda: tmp_path)


def test_the_one_line_install_reinstalls_its_uv_tool_with_the_extra(tmp_path, monkeypatch):
    _no_checkout(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "prefix", "/Users/x/.local/share/uv/tools/trinity-local")
    cmd = embedder_install_command()
    tag = f"v{_resolve_app_version()}"
    assert cmd == (f"uv tool install --force --python '>=3.10' 'trinity-local[mlx] @ "
                   f"https://github.com/{state_paths.PUBLIC_REPO}/archive/refs/tags/{tag}.tar.gz'"), cmd


def test_a_source_checkout_installs_itself_editable(tmp_path, monkeypatch):
    (tmp_path / "src" / "trinity_local").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("")
    monkeypatch.setattr("trinity_local.config.project_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    assert embedder_install_command() == "pip install -e '.[mlx]'"


def test_anything_else_pips_the_release_into_the_running_interpreter(tmp_path, monkeypatch):
    _no_checkout(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "prefix", "/opt/somewhere")
    cmd = embedder_install_command()
    assert cmd.startswith("pip install 'trinity-local[mlx] @ https://github.com/")
    assert PYPI_404 not in cmd


def test_the_fix_installs_then_downloads(tmp_path, monkeypatch):
    _no_checkout(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "prefix", "/Users/x/.local/share/uv/tools/trinity-local")
    fix = embedder_fix_command()
    assert fix.startswith(embedder_install_command())
    assert fix.endswith("&& HF_HUB_OFFLINE=0 trinity-local download-embedder")
    assert PYPI_404 not in fix


def test_no_shape_shows_a_filesystem_path(tmp_path, monkeypatch):
    for prefix in ("/Users/x/.local/share/uv/tools/trinity-local", "/opt/somewhere"):
        _no_checkout(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "prefix", prefix)
        assert "/Users/" not in embedder_install_command() and str(tmp_path) not in embedder_install_command()
