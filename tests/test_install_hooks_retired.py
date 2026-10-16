"""`install-hooks` is retired and touches nothing (council_850cff5fe89c1d81, 2026-10-16).

It wrote a Stop hook to `.claude.json`, which Claude Code does not read hooks from, replaced the
whole file when it could not parse it (a malformed config with an MCP server entry came back as
just the hook), appended a duplicate hook on every run, and printed "✓ Installed" each time.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import trinity_local

# The subprocess runs from tmp_path, so it must be told where this package lives; a relative
# PYTHONPATH would fall through to whatever editable install the venv carries.
_PKG_PARENT = str(Path(trinity_local.__file__).resolve().parents[1])


def test_install_hooks_changes_nothing_and_says_it_is_retired(tmp_path, patch_trinity_home):
    proj = tmp_path / "project"
    proj.mkdir()
    cfg = proj / ".claude.json"
    original = '{"mcpServers": {"x": {"command": "y"}},, broken'
    cfg.write_text(original, encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", "install-hooks", "--path", str(proj)],
                       capture_output=True, text=True, timeout=60, cwd=proj,
                       env={**os.environ, "PYTHONPATH": _PKG_PARENT})
    assert r.returncode == 0, r.stderr        # retired verbs exit 0 (a scheduler may still call one)
    assert "retired" in r.stderr and "Nothing was changed" in r.stderr, r.stderr
    assert cfg.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in proj.iterdir()) == [".claude.json"]


def test_install_no_longer_offers_it():
    from trinity_local.commands import install_umbrella
    assert "install-hooks" not in {verb for verb, _ in install_umbrella._INSTALL_VERBS}
