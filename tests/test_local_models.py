"""Tests for v1.5 local model detection (Ollama + MLX) and dispatch.

Detection happens via subprocess (ollama list) or filesystem probe (MLX
cache). Tests patch subprocess.run / shutil.which so they don't depend on
the test machine actually having Ollama or MLX installed.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from trinity_local.local_models import (
    _parse_size,
    clear_detection_cache,
    detect_local_models,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with a clean detection cache."""
    clear_detection_cache()
    yield
    clear_detection_cache()


class TestOllamaDetection:
    def test_missing_ollama_binary_returns_no_models(self, monkeypatch):
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: None)
        # MLX path also empty by default
        monkeypatch.delenv("TRINITY_MLX_PATH", raising=False)
        assert detect_local_models() == []

    def test_parses_ollama_list_output(self, monkeypatch):
        fake_output = (
            "NAME            ID            SIZE      MODIFIED\n"
            "qwen3:32b       abc123        20 GB     2 weeks ago\n"
            "deepseek-r1     def456        4.2 GB    1 day ago\n"
        )
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: "/usr/bin/ollama")
        monkeypatch.setattr(
            "trinity_local.local_models.subprocess.run",
            lambda *args, **kwargs: MagicMock(returncode=0, stdout=fake_output, stderr=""),
        )
        monkeypatch.delenv("TRINITY_MLX_PATH", raising=False)
        models = detect_local_models()
        assert len(models) == 2
        names = {m.name for m in models}
        assert "qwen3:32b" in names
        assert "deepseek-r1" in names
        # First model should have size populated.
        qwen = next(m for m in models if m.name == "qwen3:32b")
        assert qwen.size_bytes == 20 * 1024**3
        # Provider name is stable runtime-prefixed identifier.
        assert qwen.provider_name == "ollama:qwen3:32b"

    def test_ollama_daemon_failure_returns_empty(self, monkeypatch):
        """If `ollama list` exits non-zero (daemon not running), we get []."""
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: "/usr/bin/ollama")
        monkeypatch.setattr(
            "trinity_local.local_models.subprocess.run",
            lambda *args, **kwargs: MagicMock(returncode=1, stdout="", stderr="Error: connection refused"),
        )
        monkeypatch.delenv("TRINITY_MLX_PATH", raising=False)
        assert detect_local_models() == []

    def test_ollama_timeout_returns_empty(self, monkeypatch):
        """Timeout on `ollama list` shouldn't crash the dispatcher."""
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: "/usr/bin/ollama")
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="ollama list", timeout=5)
        monkeypatch.setattr("trinity_local.local_models.subprocess.run", raise_timeout)
        monkeypatch.delenv("TRINITY_MLX_PATH", raising=False)
        assert detect_local_models() == []

    def test_cache_hit_skips_second_subprocess(self, monkeypatch):
        """Within TTL, second call doesn't re-shell."""
        call_count = {"n": 0}
        def counting_run(*args, **kwargs):
            call_count["n"] += 1
            return MagicMock(returncode=0, stdout="NAME\nqwen3:32b foo 1 GB old\n", stderr="")
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: "/usr/bin/ollama")
        monkeypatch.setattr("trinity_local.local_models.subprocess.run", counting_run)
        monkeypatch.delenv("TRINITY_MLX_PATH", raising=False)
        detect_local_models()
        detect_local_models()
        assert call_count["n"] == 1


class TestMlxDetection:
    def test_no_env_var_returns_empty(self, monkeypatch):
        """v1.5 ship: MLX disabled unless explicitly opted in via env var."""
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: None)
        monkeypatch.delenv("TRINITY_MLX_PATH", raising=False)
        assert detect_local_models() == []

    def test_env_var_set_but_path_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: None)
        monkeypatch.setenv("TRINITY_MLX_PATH", str(tmp_path / "nonexistent"))
        assert detect_local_models() == []

    def test_scans_mlx_cache_for_model_dirs(self, tmp_path, monkeypatch):
        """Probe finds dirs that contain a config.json (MLX convention)."""
        monkeypatch.setattr("trinity_local.local_models.shutil.which", lambda _: None)
        mlx_root = tmp_path / "mlx-models"
        mlx_root.mkdir()
        (mlx_root / "qwen-7b-mlx").mkdir()
        (mlx_root / "qwen-7b-mlx" / "config.json").write_text("{}")
        (mlx_root / "deepseek-r1-distill").mkdir()
        (mlx_root / "deepseek-r1-distill" / "config.json").write_text("{}")
        # A dir WITHOUT config.json should be skipped (incomplete download).
        (mlx_root / "incomplete-model").mkdir()
        monkeypatch.setenv("TRINITY_MLX_PATH", str(mlx_root))
        models = detect_local_models()
        names = {m.name for m in models}
        assert names == {"qwen-7b-mlx", "deepseek-r1-distill"}
        for m in models:
            assert m.runtime == "mlx"


class TestSizeParser:
    @pytest.mark.parametrize("input_text,expected", [
        ("20 GB", 20 * 1024**3),
        ("4.2 GB", int(4.2 * 1024**3)),
        ("500 MB", 500 * 1024**2),
        ("1.5 TB", int(1.5 * 1024**4)),
        ("4.2GB", int(4.2 * 1024**3)),  # no space
        ("not_a_size", None),
        ("", None),
    ])
    def test_parses_common_sizes(self, input_text, expected):
        assert _parse_size(input_text) == expected


class TestOllamaProviderDispatch:
    """OllamaProvider.run shells out to `ollama run <model> <prompt>`."""

    def test_run_builds_correct_command(self, monkeypatch, tmp_path):
        from trinity_local.providers import OllamaProvider
        from trinity_local.config import ProviderConfig

        cfg = ProviderConfig(
            name="ollama-qwen",
            type="ollama",
            enabled=True,
            label="Ollama Qwen 3 32B",
            command=["ollama"],
            args=[],
            task_types=set(),
            model="qwen3:32b",
        )
        captured = {}

        def fake_run_command(self, command, cwd, *, timeout=None):
            captured["command"] = command
            from trinity_local.providers import ProviderResult
            return ProviderResult(
                provider=self.config.name,
                stdout="42",
                stderr="",
                returncode=0,
                elapsed_seconds=0.1,
            )

        monkeypatch.setattr(OllamaProvider, "_run_command", fake_run_command)
        prov = OllamaProvider(cfg)
        result = prov.run("what's the capital of France?", tmp_path)
        assert result.stdout == "42"
        assert captured["command"] == ["ollama", "run", "qwen3:32b", "what's the capital of France?"]

    def test_run_raises_when_model_missing(self, tmp_path):
        from trinity_local.providers import OllamaProvider, ProviderError
        from trinity_local.config import ProviderConfig

        cfg = ProviderConfig(
            name="ollama-bad",
            type="ollama",
            enabled=True,
            label="Ollama (bad)",
            command=["ollama"],
            args=[],
            task_types=set(),
            model=None,  # the bug we're guarding
        )
        with pytest.raises(ProviderError, match="model name"):
            OllamaProvider(cfg).run("q", tmp_path)


class TestMakeProviderDispatch:
    """make_provider correctly routes type='ollama' → OllamaProvider."""

    def test_factory_returns_ollama_provider(self):
        from trinity_local.config import ProviderConfig
        from trinity_local.providers import OllamaProvider, make_provider

        cfg = ProviderConfig(
            name="local-qwen",
            type="ollama",
            enabled=True,
            label="Local Qwen 3 32B",
            command=["ollama"],
            args=[],
            task_types=set(),
            model="qwen3:32b",
        )
        provider = make_provider(cfg)
        assert isinstance(provider, OllamaProvider)
