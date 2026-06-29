"""Tests for the install.sh CLI-wrapper rewrite that wires
launcher_path_resolver.sh into the Trinity binary.

End state: `~/.local/bin/trinity-local` is a small bash script that
calls `~/.local/bin/trinity-path-resolver.sh` to find the source dir,
then exec's Python with the right PYTHONPATH. The resolver probes
Chrome Web Store extension dirs first (auto-update path), falling
back to ~/.trinity/code/ then the legacy skill location.

This means the moment Chrome auto-updates the extension, the next
`trinity-local` invocation picks up new Python — zero user action.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


@pytest.fixture(scope="module")
def install_script() -> str:
    return INSTALL_SH.read_text()


class TestInstallScriptCopiesResolver:
    def test_install_copies_resolver_to_bin_dir(self, install_script):
        """install.sh must copy the resolver to ~/.local/bin/ so the
        wrappers can find it via a stable path. Leaving it in the
        skill dir creates a chicken-and-egg lookup."""
        assert "trinity-path-resolver.sh" in install_script, (
            "install.sh must drop the resolver alongside the wrappers — "
            "the wrappers depend on a stable resolver path."
        )
        # cp from the source location to the bin dir.
        assert re.search(
            r"cp\s+\"?\$RESOLVER_SRC\"?\s+\"?\$RESOLVER_DST\"?",
            install_script,
        ), "install.sh must `cp` the resolver to the bin dir, not symlink."

    def test_resolver_marked_executable(self, install_script):
        """The resolver is invoked directly — must be chmod +x in
        install.sh or it can't run."""
        assert re.search(
            r"chmod\s+\+x\s+\"?\$RESOLVER_DST\"?",
            install_script,
        ), "install.sh must chmod +x the resolver after copying."


class TestTrinityLocalWrapper:
    def test_wrapper_calls_resolver(self, install_script):
        """The trinity-local wrapper must call the resolver to find
        the source dir. Drift here = the wrapper goes back to the
        old static-path approach and Chrome auto-update breaks."""
        # The wrapper heredoc should reference the resolver.
        # Find the trinity-local wrapper heredoc (between WRAPPER_EOF markers).
        match = re.search(
            r"trinity-local\"?\s*<<WRAPPER_EOF\s*(.+?)\nWRAPPER_EOF",
            install_script,
            re.DOTALL,
        )
        assert match, (
            "install.sh must have a `cat > .../trinity-local <<WRAPPER_EOF` "
            "heredoc — the resolver-aware wrapper."
        )
        wrapper_body = match.group(1)
        assert "RESOLVER" in wrapper_body, (
            "trinity-local wrapper must invoke the resolver — it doesn't."
        )
        # The wrapper should pipe the resolver's stdout into SOURCE_DIR.
        assert "SOURCE_DIR" in wrapper_body
        # And it must exec python with that SOURCE_DIR as PYTHONPATH.
        assert "PYTHONPATH=" in wrapper_body
        assert "trinity_local.main" in wrapper_body

    def test_wrapper_fails_loud_when_no_source(self, install_script):
        """When the resolver returns nothing, the wrapper must print
        a clear error AND a re-install command. Silently exec'ing
        python with a broken PYTHONPATH would be much worse."""
        match = re.search(
            r"trinity-local\"?\s*<<WRAPPER_EOF\s*(.+?)\nWRAPPER_EOF",
            install_script,
            re.DOTALL,
        )
        assert match
        wrapper_body = match.group(1)
        # The wrapper should error out when resolver fails (||).
        assert "exit 1" in wrapper_body or "exit \\$?" in wrapper_body
        # And mention re-install.
        assert "Re-install" in wrapper_body, (
            "Resolver failure should hand the user the curl|bash re-install "
            "command so they can recover."
        )


class TestCaptureHostWrapper:
    def test_capture_host_wrapper_also_uses_resolver(self, install_script):
        """The capture-host wrapper must use the same resolver as the
        main CLI — otherwise Chrome auto-update updates the CLI but
        the Native Messaging host stays stale."""
        match = re.search(
            r"trinity-local-capture-host\"?\s*<<CAPTURE_EOF\s*(.+?)\nCAPTURE_EOF",
            install_script,
            re.DOTALL,
        )
        assert match, (
            "install.sh must write the capture-host wrapper via a "
            "CAPTURE_EOF heredoc."
        )
        body = match.group(1)
        # Same resolver path resolution pattern.
        assert "RESOLVER" in body or "trinity-path-resolver" in body, (
            "capture-host wrapper must use the resolver too — drift here "
            "means CLI auto-updates but Native Messaging stays stale."
        )
        assert "SOURCE_DIR" in body
        assert "trinity_local.capture_host" in body


class TestPythonInterpreterFallback:
    """#274: the wrappers bake the absolute interpreter (v1.7.56 — Chrome's
    sanitized NM PATH needs it) but must fall back to a PATH lookup if Python
    moves (pyenv/asdf/brew relocate), instead of bricking with 'No such file'."""

    def test_wrappers_have_python_fallback(self, install_script):
        # Every generated exec must go through the resilient resolver, not a
        # bare `exec "$PYTHON_BIN"`.
        assert 'exec "$PYTHON_BIN" -m trinity_local' not in install_script, (
            "a wrapper execs the baked interpreter directly with no fallback — "
            "a Python relocation would brick it (#274)."
        )
        assert 'exec "\\$TRINITY_PY" -m trinity_local' in install_script

    def test_absolute_path_stays_primary(self, install_script):
        # TRINITY_PYTHON override > baked absolute path > PATH lookup. The baked
        # path must remain the default (v1.7.56 regression guard).
        assert 'TRINITY_PY="\\${TRINITY_PYTHON:-$PYTHON_BIN}"' in install_script
        # PATH fallback only when the baked path isn't executable.
        assert '[ -x "\\$TRINITY_PY" ] || TRINITY_PY="\\$(command -v python3' in install_script


class TestCaptureHostLaunchRobustness:
    """Two bugs that ONLY bit the extension-first path (the CLI path masked
    them): Chrome launches the Native Messaging host with a SANITIZED PATH
    and the host runs as a script. Both made capture silently dead on a
    fresh install. Guards so they can't regress."""

    def test_capture_host_runs_as_module_not_script(self, install_script):
        """capture_host.py uses relative imports (`from .registry import …`)
        which raise 'attempted relative import with no known parent package'
        when run as a file. The wrapper MUST invoke it as a module under
        `-m` (PYTHONPATH set to .../src), exactly like the CLI wrapper does
        with trinity_local.main."""
        for marker in ("CAPTURE_EOF", "CAPTURE_LEGACY_EOF"):
            m = re.search(rf"<<{marker}\s*(.+?)\n{marker}", install_script, re.DOTALL)
            assert m, f"install.sh must write the capture-host wrapper via {marker}."
            body = m.group(1)
            assert "-m trinity_local.capture_host" in body, (
                f"{marker} wrapper must exec `python -m trinity_local.capture_host`, "
                f"not run capture_host.py as a script — relative imports break "
                f"the moment Chrome launches the host."
            )
            # The exec line must NOT pass the .py file as python's first arg.
            exec_line = next(
                (ln for ln in body.splitlines() if ln.strip().startswith("exec ")),
                "",
            )
            assert "capture_host.py" not in exec_line, (
                f"{marker} exec line still runs the script file directly: {exec_line!r}"
            )

    def test_interpreter_path_is_absolute(self, install_script):
        """The wrappers exec $PYTHON_BIN. Chrome launches the host with a
        sanitized PATH (no Homebrew /opt/homebrew/bin), so a bare
        `python3.12` fails to resolve. install.sh must bake the ABSOLUTE
        interpreter path via `command -v`."""
        assert 'PYTHON_BIN="$(command -v "$candidate")"' in install_script, (
            "install.sh must resolve PYTHON_BIN to an absolute path "
            "(command -v), not store the bare candidate name — Chrome's "
            "sanitized native-messaging PATH can't find a bare python3.12."
        )


class TestLegacyFallbackBranch:
    """If launcher_path_resolver.sh isn't in the cloned repo (partial
    install / future repo reshuffle), install.sh must still write
    working wrappers via the legacy direct-path approach. The Chrome
    auto-update story degrades but Trinity keeps working."""

    def test_fallback_branch_exists(self, install_script):
        """install.sh has an explicit if/else around resolver
        presence so partial installs don't write broken wrappers."""
        # Look for the resolver-presence guard.
        assert re.search(
            r"if\s+\[\[\s+-n\s+\"\$RESOLVER_DST\"\s+\]\]",
            install_script,
        ), (
            "install.sh must gate wrapper choice on resolver presence "
            "and have a legacy-fallback branch — partial installs would "
            "otherwise leave the user with broken wrappers."
        )

    def test_fallback_uses_legacy_static_path(self, install_script):
        """The fallback branch falls back to the previous wrapper
        shape — TRINITY_SKILL_DIR env var pointing at the skill repo."""
        # Find the legacy heredoc.
        assert "LEGACY_EOF" in install_script, (
            "install.sh must define a legacy wrapper heredoc as fallback."
        )
