from __future__ import annotations

import argparse
import importlib

import pytest

from trinity_local import main


def _subparser_choices(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    subparsers_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return subparsers_action.choices


def test_build_parser_registers_core_commands():
    parser = main.build_parser()
    choices = _subparser_choices(parser)
    assert "portal-html" in choices
    assert "council-launch" in choices
    assert "install-mcp" in choices
    assert "telemetry-show" in choices


def test_build_parser_skips_missing_optional_module(monkeypatch: pytest.MonkeyPatch):
    real_import = importlib.import_module
    optional_path = "trinity_local.commands.install"

    def fake_import(name: str, package: str | None = None):
        if name == optional_path:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, package)

    monkeypatch.setattr(main.importlib, "import_module", fake_import)

    parser = main.build_parser()
    choices = _subparser_choices(parser)
    assert "install-mcp" not in choices
    assert "portal-html" in choices


def test_load_mcp_runner_errors_cleanly_when_missing(monkeypatch: pytest.MonkeyPatch):
    real_import = importlib.import_module
    module_path = "trinity_local.mcp_server"

    def fake_import(name: str, package: str | None = None):
        if name == module_path:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, package)

    monkeypatch.setattr(main.importlib, "import_module", fake_import)

    with pytest.raises(SystemExit, match="MCP server support is not available"):
        main._load_mcp_runner()


def test_pin_hf_offline_sets_defaults(monkeypatch: pytest.MonkeyPatch):
    """`_pin_hf_offline` should set HF/transformers offline env vars when
    they are unset, so the running system never makes outbound HF calls."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)

    main._pin_hf_offline()

    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_pin_hf_offline_preserves_user_override(monkeypatch: pytest.MonkeyPatch):
    """A user who explicitly sets HF_HUB_OFFLINE=0 (e.g. to pull a new
    model) should not have it stomped — `setdefault` semantics."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    main._pin_hf_offline()

    import os
    assert os.environ["HF_HUB_OFFLINE"] == "0"


class TestHandlerExitCodePropagation:
    """`main()` must propagate a handler's non-zero return as the process exit
    code. Before this, `main()` discarded `args.handler(args)`, so a handler that
    `return 1`s on failure (e.g. eval-import on a missing file) still exited 0 —
    `import … && next-step` chained straight past the failure (the silent-failure
    anti-pattern: a non-zero result that the shell/CI can't see). Success paths
    (0 / None) must still return normally so callers that invoke main() directly
    are unaffected."""

    def _run_main(self, monkeypatch, tmp_path, handler):
        import argparse

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        ns = argparse.Namespace(mcp=False, command="faketest", handler=handler)

        class _FakeParser:
            def add_argument(self, *a, **k):
                pass

            def parse_args(self):
                return ns

            def print_help(self):
                pass

            def error(self, msg):
                raise SystemExit(2)

        monkeypatch.setattr(main, "build_parser", lambda: _FakeParser())
        main.main()

    def test_nonzero_handler_return_becomes_exit_code(self, monkeypatch, tmp_path):
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, tmp_path, lambda args: 3)
        assert exc.value.code == 3, "a handler's non-zero return must become the exit code"

    def test_zero_return_exits_clean(self, monkeypatch, tmp_path):
        # rc=0 must NOT raise SystemExit — success-path callers (tests) rely on
        # main() returning normally.
        self._run_main(monkeypatch, tmp_path, lambda args: 0)

    def test_none_return_exits_clean(self, monkeypatch, tmp_path):
        self._run_main(monkeypatch, tmp_path, lambda args: None)

    def test_eval_import_missing_file_exits_nonzero_end_to_end(self, tmp_path):
        """The real-world manifestation: a copy-pasted `eval-import` chip pointed at
        a file that doesn't exist must exit non-zero so a shell `&&` chain stops."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        home = tmp_path / "trinity"
        env = dict(
            os.environ,
            TRINITY_HOME=str(home),
            TRINITY_AUTOSCAN_DISABLED="1",
            PYTHONPATH=str(repo / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
        )
        r = subprocess.run(
            [sys.executable, "-m", "trinity_local.main",
             "eval-import", "--provider", "claude", str(tmp_path / "does-not-exist.json")],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert r.returncode != 0, (
            f"eval-import on a missing file must exit non-zero (got {r.returncode}); "
            f"stderr={r.stderr[-200:]}"
        )
