"""Guards for state_paths.embedder_install_command — the HONEST 'install the real
embedder deps' command (founder-greenlit 2026-06-07).

`pip install 'trinity-local[mlx]'` 404s pre-PyPI (the package isn't published).
The only install path today is the curl|sh script, which clones the source to
~/.trinity/code and creates a venv at ~/.trinity/venv. So the honest fix resolves
the [mlx] extra from the LOCAL source: pyproject's platform markers install mlx
only on Apple Silicon and sentence-transformers/torch everywhere, so the one
command is correct on every platform. The 404 PyPI form is only emitted for a
future pip/uvx install (no local source dir present).
"""
from __future__ import annotations

from trinity_local.state_paths import embedder_install_command


def test_script_install_uses_local_source_and_venv_pip(tmp_path, monkeypatch):
    """A script install (the 100% pre-PyPI population) has ~/.trinity/code +
    ~/.trinity/venv — the command must target the venv pip and resolve the [mlx]
    extra from the local source, and must NEVER emit the 404 PyPI form."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "code").mkdir()
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "pip").write_text("")

    cmd = embedder_install_command()
    assert "trinity-local[mlx]" not in cmd, (
        f"the 404 pre-PyPI form must not be handed to a script-installed user: {cmd!r}"
    )
    assert str(tmp_path / "venv" / "bin" / "pip") in cmd, cmd
    assert f"{tmp_path / 'code'}[mlx]" in cmd, cmd


def test_script_install_without_venv_falls_back_to_bare_pip(tmp_path, monkeypatch):
    """Source present but no venv → still resolve from the local source (bare
    pip), never the 404 PyPI form."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "code").mkdir()

    cmd = embedder_install_command()
    assert cmd == f"pip install '{tmp_path / 'code'}[mlx]'", cmd
    assert "trinity-local[mlx]" not in cmd


def test_pip_install_future_uses_published_extra(tmp_path, monkeypatch):
    """No local source dir (a future pip/uvx install) → the published-extra form
    is correct: it resolves once the package is on PyPI."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))  # no code/ dir
    assert embedder_install_command() == "pip install 'trinity-local[mlx]'"
