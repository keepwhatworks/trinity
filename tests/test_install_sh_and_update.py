"""Tests for scripts/install.sh + trinity-local update.

The git-clone-led distribution (no PyPI, no npm) hinges on:
  - scripts/install.sh being valid bash + idempotent
  - bin/trinity-local wrapper resolving to the cloned repo
  - trinity-local update implementing --check / --json / ff-only pull
  - doctor surfacing staleness when behind origin
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


# ─── scripts/install.sh ────────────────────────────────────────────


def test_install_sh_exists_and_is_executable():
    assert INSTALL_SH.exists()
    mode = INSTALL_SH.stat().st_mode & 0o777
    assert mode & 0o100, (
        f"scripts/install.sh must be user-executable (got {oct(mode)})"
    )


def test_install_sh_passes_bash_syntax_check():
    """`bash -n` parses without executing — catches typos that would
    break the curl|sh install before the user notices."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"bash syntax check failed:\n{result.stderr}"
    )


def test_install_sh_references_canonical_paths():
    """The installer must point at the canonical install directory and
    bin directory. Drift here means the docs and the script diverge —
    user gets one path from README, installer puts files elsewhere.

    Post-2026-05-19 pivot: canonical install lives at ~/.trinity/code/.
    Legacy ~/.claude/skills/trinity/ is kept as a back-compat symlink.

    Distribution invariant: Trinity itself is NOT installed via pip/npm
    (the script clones the repo and writes shell wrappers). Runtime
    Python deps (Pillow, mcp) ARE installed via pip — they're third-
    party and we don't vendor them. The forbidden form is specifically
    `pip install trinity-local` / `pipx install trinity-local`."""
    content = INSTALL_SH.read_text()
    # Canonical post-pivot path must be referenced.
    assert ".trinity/code" in content, (
        "install.sh must reference ~/.trinity/code/ (post-2026-05-19 pivot "
        "canonical install location)."
    )
    # Legacy still referenced as symlink target.
    assert ".claude/skills/trinity" in content, (
        "install.sh must still reference ~/.claude/skills/trinity/ for the "
        "back-compat symlink (Claude Code /trinity skill alias)."
    )
    assert ".local/bin" in content
    assert "pip install trinity-local" not in content
    assert "pipx install trinity-local" not in content
    assert "npm install trinity-local" not in content
    assert "npm install -g trinity-local" not in content


def test_install_sh_default_install_target_is_canonical():
    """The TRINITY_SKILL_DIR default value must be ~/.trinity/code (the
    canonical post-pivot location), NOT ~/.claude/skills/trinity. New
    installs land at the clean footprint; the legacy path becomes a
    symlink alias."""
    content = INSTALL_SH.read_text()
    # Match the assignment line specifically.
    import re
    match = re.search(
        r'TRINITY_SKILL_DIR=\s*"\$\{TRINITY_SKILL_DIR:-(\$HOME[^}"]+)\}"',
        content,
    )
    assert match is not None, (
        "install.sh must define TRINITY_SKILL_DIR with an explicit default."
    )
    default = match.group(1)
    assert default == "$HOME/.trinity/code", (
        f"TRINITY_SKILL_DIR default must be $HOME/.trinity/code (canonical "
        f"post-2026-05-19 location); got {default!r}. New installs landing at "
        f"the legacy location would silently regress the footprint collapse."
    )


def test_install_sh_creates_legacy_symlink():
    """For new installs, the legacy ~/.claude/skills/trinity/ path must
    be created as a symlink to the canonical install — so Claude Code's
    `/trinity` skill alias keeps working without forcing two copies of
    the repo. The script must NOT clobber an existing real directory."""
    content = INSTALL_SH.read_text()
    # Symlink creation must happen.
    assert "ln -s" in content, (
        "install.sh must `ln -s` the legacy skill path to the canonical "
        "install when the legacy path doesn't exist (or is already a symlink)."
    )
    # Must guard against clobbering an existing real dir.
    assert (
        "exists as a real directory" in content
        or "leaving it alone" in content
    ), (
        "install.sh must NOT overwrite ~/.claude/skills/trinity/ if it's a "
        "real directory (existing pre-pivot install) — that would silently "
        "destroy the user's checkout."
    )


def test_install_sh_writes_two_wrappers():
    """The installer should drop trinity-local + trinity-local-capture-host
    wrappers into ~/.local/bin/ — the only two CLI surfaces the user
    invokes by name."""
    content = INSTALL_SH.read_text()
    assert "TRINITY_BIN_DIR/trinity-local" in content
    assert "TRINITY_BIN_DIR/trinity-local-capture-host" in content


def test_install_sh_installs_runtime_python_deps():
    """Trinity's runtime deps (Pillow, mcp) are pyproject-declared but
    NOT auto-installed by the git clone. The installer must pip-install
    them or doctor's first run flags two failures the user has to fix
    manually — a "vibes-coded" first-impression smell."""
    content = INSTALL_SH.read_text()
    assert "Pillow>=10" in content, (
        "install.sh must install Pillow — without it me-card PNG "
        "rendering fails and doctor flags it on first run."
    )
    assert "mcp>=1.0" in content, (
        "install.sh must install the mcp package — without it the MCP "
        "server can't start and Claude Code can't see Trinity's tools."
    )
    assert "numpy>=1.26" in content, (
        "install.sh must install numpy — without it the embedding "
        "matmul fast-path + k-means clustering + vocabulary stats all "
        "fail at import time. CI launch-eve catch: fresh ubuntu-latest "
        "+ python3.12 environment can't find numpy because it wasn't "
        "in pyproject.toml main deps."
    )


def test_install_sh_handles_venv_active_state():
    """pip refuses `--user` when run inside an active virtualenv. The
    installer must detect this case (e.g. a contributor smoking install
    from inside their venv-active shell) and install into the venv
    instead. Sandboxed smoke (May 17) caught this: the warning fired
    silently, doctor only went green because mcp happened to be in the
    dev's system Python — on a truly fresh box that path doesn't exist
    and the install would surface a real failure downstream."""
    content = INSTALL_SH.read_text()
    # The venv detector must check sys.prefix vs sys.base_prefix — that's
    # the canonical Python idiom for "am I inside a venv right now."
    assert "sys.prefix" in content and "sys.base_prefix" in content, (
        "install.sh must detect virtualenv state via sys.prefix vs "
        "sys.base_prefix and drop the --user flag inside a venv."
    )


def test_install_sh_wrapper_uses_resolved_python():
    """The wrapper that ~/.local/bin/trinity-local writes must use the
    Python binary the install script validated, not a raw `python3`.
    On systems where `python3` is older than the candidate the script
    picked (e.g. `python3.13` passes, `python3` is 3.9), the wrapper
    would silently break."""
    content = INSTALL_SH.read_text()
    # #274: the resolved interpreter is baked as TRINITY_PY's default
    # ($PYTHON_BIN expands at script time — the PRIMARY, v1.7.56), with a PATH
    # fallback below; the exec goes through it. NOT a bare `python3 -m`.
    assert 'TRINITY_PY="\\${TRINITY_PYTHON:-$PYTHON_BIN}"' in content, (
        "wrapper should bake the resolved Python binary as TRINITY_PY's default "
        "so it can't drift if the user later installs a stale python3 ahead of it."
    )
    assert 'exec "\\$TRINITY_PY" -m trinity_local.main' in content


# ─── trinity-local update ──────────────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_update_check_command_registered():
    """`trinity-local update --check` must be a registered subcommand."""
    import argparse
    from trinity_local.commands.update import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register(subparsers)

    args = parser.parse_args(["update", "--check"])
    assert getattr(args, "handler", None) is not None
    assert args.check is True


def test_update_emits_error_when_skill_dir_missing(tmp_path, capsys):
    """Pointing update at a non-existent skill dir must error cleanly,
    not crash."""
    from trinity_local.commands.update import handle_update

    args = SimpleNamespace(
        skill_dir=str(tmp_path / "nonexistent"),
        check=False, json=False,
    )
    rc = handle_update(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_update_emits_error_when_skill_dir_not_a_git_checkout(
    tmp_path, capsys, monkeypatch
):
    """Pointing update at a directory that exists but isn't a git
    checkout must error cleanly — that means the skill was installed
    via something other than scripts/install.sh (manual copy?) and
    update can't apply."""
    from trinity_local.commands.update import handle_update

    # Create the dir but no .git/
    (tmp_path / "fake_skill").mkdir()

    # Force the "not a Chrome extension install" branch so the error
    # message references the curl|bash install path, not the Chrome
    # auto-update one.
    monkeypatch.setattr(
        "trinity_local.launchpad_data.dispatch_readiness",
        lambda: {"ready": False, "extension_configured": False},
    )

    args = SimpleNamespace(
        skill_dir=str(tmp_path / "fake_skill"),
        check=False, json=False,
    )
    rc = handle_update(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a git checkout" in err


def test_update_skill_dir_prefers_canonical_when_present(tmp_path, monkeypatch):
    """Post-2026-05-19 pivot: ~/.trinity/code/ is the canonical install
    location. _skill_dir() must prefer it over the legacy
    ~/.claude/skills/trinity/ when both could exist."""
    from trinity_local.commands.update import _skill_dir

    # Patch Path.home to point at tmp_path.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    # Seed both the canonical location and the legacy location,
    # with .git/ markers so the canonical-preferred check fires.
    canonical = tmp_path / ".trinity" / "code"
    canonical.mkdir(parents=True)
    (canonical / ".git").mkdir()
    legacy = tmp_path / ".claude" / "skills" / "trinity"
    legacy.mkdir(parents=True)
    (legacy / ".git").mkdir()

    resolved = _skill_dir(None)
    assert resolved == canonical, (
        f"_skill_dir() must prefer ~/.trinity/code/ over legacy when "
        f"both have .git/; got {resolved}"
    )


def test_update_skill_dir_falls_back_to_legacy(tmp_path, monkeypatch):
    """When ~/.trinity/code/ doesn't exist (or isn't a git checkout),
    _skill_dir falls back to the legacy ~/.claude/skills/trinity/
    path — for pre-pivot users mid-migration."""
    from trinity_local.commands.update import _skill_dir

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    # NO ~/.trinity/code/.git — only legacy.
    legacy = tmp_path / ".claude" / "skills" / "trinity"
    legacy.mkdir(parents=True)
    (legacy / ".git").mkdir()

    resolved = _skill_dir(None)
    assert resolved == legacy


class TestUpdateDepsFlag:
    """`trinity-local update --deps` refreshes pip-installed runtime
    deps (Pillow, mcp, numpy) without touching the git source. Used
    on the rare upgrade where dep versions need to advance — most
    Trinity bumps don't.
    """

    def test_deps_flag_registered(self):
        """The --deps flag must be on the argparse surface so users
        can discover it via --help."""
        import argparse
        from trinity_local.commands.update import register

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        register(subparsers)
        args = parser.parse_args(["update", "--deps"])
        assert getattr(args, "deps", False) is True

    def test_deps_flag_short_circuits_git_pull(
        self, tmp_path, monkeypatch, capsys
    ):
        """When --deps is set, the handler must NOT touch the git
        source — pip-dep refresh is decoupled from source updates."""
        from trinity_local.commands.update import handle_update
        import subprocess as real_subprocess

        # Spy: track which subprocess calls fire.
        calls: list[list[str]] = []

        def _fake_run(argv, **kwargs):
            calls.append(list(argv))
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(real_subprocess, "run", _fake_run)
        args = SimpleNamespace(
            deps=True, check=False, json=False, skill_dir=None,
        )
        rc = handle_update(args)
        assert rc == 0
        # No `git ...` calls fired — proves we short-circuited.
        assert not any(c and c[0] == "git" for c in calls), (
            f"--deps path must NOT shell out to git; got: {calls}"
        )
        # Did fire a pip install.
        assert any(
            "pip" in c and "install" in c for c in calls
        ), f"--deps path must call pip install; got: {calls}"

    def test_deps_refreshes_expected_packages(
        self, tmp_path, monkeypatch
    ):
        """The pip install argv must include Pillow, mcp, and numpy —
        same packages install.sh installs, with the same version pins."""
        from trinity_local.commands.update import handle_update
        import subprocess as real_subprocess

        captured_argv: list[list[str]] = []

        def _fake_run(argv, **kwargs):
            captured_argv.append(list(argv))
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(real_subprocess, "run", _fake_run)
        args = SimpleNamespace(
            deps=True, check=False, json=False, skill_dir=None,
        )
        handle_update(args)
        assert captured_argv, "pip install must fire"
        argv = captured_argv[0]
        joined = " ".join(argv)
        for required_pkg in ("Pillow>=10", "mcp>=1.0", "numpy>=1.26"):
            assert required_pkg in joined, (
                f"`update --deps` must install {required_pkg!r} (matching "
                f"install.sh's pin); got argv: {argv}"
            )

    def test_deps_prefers_dedicated_venv_over_user_when_not_in_venv(
        self, tmp_path, monkeypatch
    ):
        """v1.7.318: outside a venv, --deps must target the DEDICATED
        ~/.trinity/venv (install.sh v1.7.316 creates it), NOT `pip install
        --user` — which is BLOCKED by PEP 668 on Homebrew/Debian/Ubuntu/Fedora
        system pythons. Same bug fixed in install.sh, previously un-migrated
        to update --deps."""
        from pathlib import Path
        from trinity_local.commands import update as _update
        import subprocess as real_subprocess

        # Simulate running OUTSIDE a venv.
        monkeypatch.setattr(_update.sys, "prefix", "/usr")
        monkeypatch.setattr(_update.sys, "base_prefix", "/usr")
        # A dedicated venv exists under a fake home.
        home = tmp_path / "home"
        venv_py = home / ".trinity" / "venv" / "bin" / "python3"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("")
        monkeypatch.setattr(Path, "home", lambda: home)

        captured: list[list[str]] = []

        def _fake_run(argv, **kwargs):
            captured.append(list(argv))
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(real_subprocess, "run", _fake_run)
        rc = _update.handle_update(SimpleNamespace(
            deps=True, check=False, json=False, skill_dir=None,
        ))
        assert rc == 0
        argv = captured[0]
        assert argv[0] == str(venv_py), (
            f"--deps must target the dedicated ~/.trinity/venv python, not the "
            f"system interpreter; got {argv[0]!r}"
        )
        assert "--user" not in argv, (
            f"--deps must NOT use `pip install --user` when the dedicated venv "
            f"exists (PEP 668 blocks --user on system pythons); got {argv}"
        )

    def test_deps_emits_json_when_requested(self, monkeypatch, capsys):
        """--deps --json emits a machine-readable result for agent
        consumption (Claude Code can parse it inline)."""
        from trinity_local.commands.update import handle_update
        import json
        import subprocess as real_subprocess

        def _fake_run(argv, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(real_subprocess, "run", _fake_run)
        args = SimpleNamespace(
            deps=True, check=False, json=True, skill_dir=None,
        )
        rc = handle_update(args)
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["deps_updated"] is True
        assert "venv" in parsed
        assert parsed["returncode"] == 0

    def test_deps_failure_returns_nonzero(self, monkeypatch, capsys):
        """If pip itself fails, --deps must return rc=1 + report the
        error (NOT crash, NOT pretend success)."""
        from trinity_local.commands.update import handle_update
        import subprocess as real_subprocess

        def _fake_run(argv, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(returncode=1, stdout="", stderr="network down")

        monkeypatch.setattr(real_subprocess, "run", _fake_run)
        args = SimpleNamespace(
            deps=True, check=False, json=False, skill_dir=None,
        )
        rc = handle_update(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "pip install" in err or "failed" in err.lower()


def test_update_explains_chrome_auto_update_when_not_git(
    tmp_path, capsys, monkeypatch
):
    """If the user's source dir isn't a git checkout AND the Chrome
    extension is wired, the error should explain that Chrome auto-
    updates the extension and no manual update is needed for the
    Python side — NOT scold them to re-install."""
    from trinity_local.commands.update import handle_update

    (tmp_path / "fake_source").mkdir()
    monkeypatch.setattr(
        "trinity_local.launchpad_data.dispatch_readiness",
        lambda: {"ready": True, "extension_configured": True},
    )

    args = SimpleNamespace(
        skill_dir=str(tmp_path / "fake_source"),
        check=False, json=False,
    )
    rc = handle_update(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "Chrome" in err or "auto-update" in err, (
        f"Update must explain Chrome auto-update for extension users; got: {err}"
    )


def test_update_check_on_real_repo_returns_up_to_date_or_lag(capsys):
    """Run update --check against the actual repo (the test environment's
    own checkout). Should either report "up to date" or a specific lag
    count — never crash."""
    from trinity_local.commands.update import handle_update

    args = SimpleNamespace(
        skill_dir=str(REPO_ROOT),
        check=True, json=True,
    )
    rc = handle_update(args)
    # rc==0 on success OR soft-fail (network issue). The point is no crash.
    assert rc == 0
    out = capsys.readouterr().out
    # Either we got JSON with the expected shape, or we got the soft-fail
    # path (which writes to stderr, not stdout — stdout is empty).
    if out.strip():
        data = json.loads(out)
        assert "behind" in data or "error" in data


def test_update_json_mode_format(capsys):
    """`--json` mode emits machine-readable output, not human prose."""
    from trinity_local.commands.update import handle_update

    args = SimpleNamespace(
        skill_dir=str(REPO_ROOT),
        check=True, json=True,
    )
    handle_update(args)
    out = capsys.readouterr().out.strip()
    if out:  # might be empty if soft-fail to stderr
        # JSON parses without error.
        parsed = json.loads(out)
        assert isinstance(parsed, dict)


def test_update_fast_forwards_a_behind_clone(tmp_path, monkeypatch):
    """LAUNCH-CRITICAL: the actual git-pull path, not just --check. Every
    curl|sh user's only route to fixes is `trinity-local update` doing a
    fast-forward of the cloned repo. The existing suite covers --check + the
    error/timeout paths but never the real behind→pull→at-HEAD round-trip; a
    break in the `git merge --ff-only` logic would keep --check tests green
    while shipping a Trinity that can never update itself. Verified live
    2026-05-31, now guarded. Passes git through; intercepts only the
    install-mcp/status verify-spawns so the test has no global side effects."""
    import subprocess as _sp
    from trinity_local.commands.update import handle_update

    def git(*a, cwd):
        return _sp.run(["git", *a], cwd=str(cwd), capture_output=True,
                       text=True, check=False)

    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    git("config", "user.email", "t@t.dev", cwd=origin)
    git("config", "user.name", "t", cwd=origin)
    (origin / "f.txt").write_text("v1")
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "c1", cwd=origin)

    clone = tmp_path / "clone"
    git("clone", "-q", str(origin), str(clone), cwd=tmp_path)

    # Advance origin one commit → the clone is now behind by 1.
    (origin / "f.txt").write_text("v2")
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "c2", cwd=origin)
    origin_head = git("rev-parse", "HEAD", cwd=origin).stdout.strip()
    assert git("rev-parse", "HEAD", cwd=clone).stdout.strip() != origin_head

    # Intercept ONLY the verify-spawns (install-mcp / status); pass git through
    # so the fetch + merge run for real against the local origin.
    real_run = _sp.run

    def fake_run(argv, *a, **k):
        if argv and argv[0] == "git":
            return real_run(argv, *a, **k)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_sp, "run", fake_run)

    rc = handle_update(SimpleNamespace(
        skill_dir=str(clone), check=False, json=True, deps=False,
    ))
    assert rc == 0
    clone_head = real_run(
        ["git", "rev-parse", "HEAD"], cwd=str(clone),
        capture_output=True, text=True,
    ).stdout.strip()
    assert clone_head == origin_head, (
        "update did not fast-forward the clone to origin HEAD — the curl|sh "
        "update lifeline is broken"
    )


def test_install_sh_uses_a_venv_for_deps_before_pip_install():
    """PEP 668 launch blocker (v1.7.316): Homebrew / Debian / Ubuntu 23.04+ / Fedora
    system pythons are "externally managed", so `pip install --user` FAILS there
    (`error: externally-managed-environment`) and Trinity's deps (mcp especially)
    never install — the CLI's `status` looks fine but the MCP server can't
    `import mcp` and never starts. Found by booting the installed wrapper under a
    sanitized env. The fix: create a dedicated venv and repoint PYTHON_BIN to it
    BEFORE the pip dep-install (so deps land in the venv, not a doomed --user) AND
    before the wrappers are written (so they bake the venv interpreter). This guard
    pins that structure so the venv can't be dropped or reordered after the pip
    step."""
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "-m venv" in text, (
        "install.sh must create a dependency venv — `pip install --user` is blocked "
        "by PEP 668 on Homebrew/Debian/Ubuntu/Fedora system pythons, leaving mcp "
        "uninstalled and the MCP server unable to start."
    )
    # The venv interpreter must become PYTHON_BIN so the dep install + the wrappers
    # both use it.
    repoint_idx = text.find('PYTHON_BIN="$TRINITY_VENV/bin/python3"')
    assert repoint_idx != -1, (
        "install.sh must repoint PYTHON_BIN to the venv interpreter "
        '(PYTHON_BIN="$TRINITY_VENV/bin/python3") so deps install into the venv '
        "and the wrappers bake it."
    )
    # ORDERING: the venv repoint must precede the dep-install step, or the deps
    # fall back to the broken --user path on a system python.
    dep_step_idx = text.find("Installing Python runtime deps")
    assert dep_step_idx != -1, "the dep-install step marker moved — update this guard"
    assert repoint_idx < dep_step_idx, (
        "the venv must be created + PYTHON_BIN repointed BEFORE the pip dep-install, "
        "else deps install via --user on a PEP-668 python and the MCP server dies."
    )
    # And before the wrappers are written (they bake $PYTHON_BIN as the runtime).
    wrapper_idx = text.find("Installing CLI wrappers")
    assert wrapper_idx != -1, "the wrapper-install step marker moved — update this guard"
    assert repoint_idx < wrapper_idx, (
        "the venv repoint must precede wrapper writing so the wrappers bake the venv "
        "interpreter (which has the deps), not a bare system python."
    )
