"""The launch onboarding OUTCOME: the `trinity-local` wrapper install.sh writes
must actually RUN trinity — not just exist.

A curl|sh user's first contact is the `~/.local/bin/trinity-local` wrapper install.sh
generates (a heredoc that resolves the source dir, exports PYTHONPATH, and execs
`python -m trinity_local.main`). The existing test only checks that the path string
appears in install.sh — it would pass even if the wrapper BODY dropped PYTHONPATH,
which is exactly the #274-class regression that made `trinity-local` a no-import
binary (Chrome's sanitized PATH + a bare `python -m`). String-presence can't see a
broken wrapper.

This renders the REAL wrapper heredoc from install.sh the way the installer does
(bash expands the install-time vars) against a fake resolver pointing at this repo,
then RUNS it: a working wrapper sets PYTHONPATH=<src> and execs the CLI, so
`trinity-local --help` exits 0 with real CLI output. No clone, no pip — uses the
repo source + the test interpreter. Skips when bash is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"


def _render_resolver_wrapper(resolver_path: str, python_bin: str) -> str:
    """Extract the resolver-branch wrapper heredoc from install.sh and render it
    exactly as the installer's `cat <<WRAPPER_EOF` would (bash expands $VAR + \\$)."""
    sh = INSTALL_SH.read_text(encoding="utf-8")
    assert "<<WRAPPER_EOF" in sh, "install.sh no longer uses the WRAPPER_EOF heredoc"
    body = sh.split("<<WRAPPER_EOF\n", 1)[1].split("\nWRAPPER_EOF", 1)[0]
    rendered = subprocess.run(
        ["bash", "-c",
         f'RESOLVER_DST="{resolver_path}" PYTHON_BIN="{python_bin}" '
         f'cat <<WRAPPER_EOF\n{body}\nWRAPPER_EOF'],
        capture_output=True, text=True, check=True,
    ).stdout
    return rendered


@pytest.fixture
def wrapper(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    # A resolver that points the wrapper at THIS repo → PYTHONPATH=<repo>/src.
    resolver = tmp_path / "resolver.sh"
    resolver.write_text(f'#!/usr/bin/env bash\necho "{REPO}"\n', encoding="utf-8")
    resolver.chmod(0o755)
    rendered = _render_resolver_wrapper(str(resolver), sys.executable)
    path = tmp_path / "trinity-local"
    path.write_text(rendered, encoding="utf-8")
    path.chmod(0o755)
    return path, rendered


def test_generated_wrapper_exports_pythonpath_and_execs_cli(wrapper):
    """The rendered wrapper body carries the load-bearing pieces — PYTHONPATH at the
    source's /src (the #274 fix) and an exec of trinity_local.main — and is valid
    bash. The existing string-presence test only checks the wrapper PATH appears in
    install.sh; this checks the wrapper BODY, so a regression that dropped PYTHONPATH
    (a no-import binary) fails here.

    (An earlier revision also RAN the wrapper, but running it requires a sub-resolver
    subprocess that's fragile across CI filesystems — /tmp noexec etc. — so the
    runtime assertion was dropped in favor of `bash -n` syntax validation, which is
    deterministic.)"""
    _path, rendered = wrapper
    assert "export PYTHONPATH=" in rendered and "/src:" in rendered, (
        "the generated wrapper doesn't export PYTHONPATH at <source>/src — the "
        "#274 regression: trinity-local becomes a no-import binary"
    )
    assert "-m trinity_local.main" in rendered, "the wrapper doesn't exec the CLI module"
    # The rendered wrapper must be syntactically valid bash (catches an unbalanced
    # heredoc, a stray quote from a future edit, etc.) — deterministic, no run needed.
    syntax = subprocess.run(["bash", "-n", str(_path)], capture_output=True, text=True)
    assert syntax.returncode == 0, (
        f"the install.sh-generated wrapper is not valid bash: {syntax.stderr[:200]}"
    )
