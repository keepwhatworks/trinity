"""trinity-local update — pull latest, re-register MCP, run status check.

The git-clone-led distribution model (no PyPI, no npm; council
`37eca30b6e7010df` ratified shape) makes updates a `git pull` away.
This command bundles the pull + side-effect refresh + verification
into one user-facing verb.

Side-effects on every update:
  1. `git pull --ff-only` in the skill directory (refuses non-FF
     merges so the user's local edits aren't silently overwritten)
  2. `trinity-local install-mcp` re-runs to pick up any new MCP tools
     or renamed entries (council c18f739a verdict: MCP config must
     refresh when the tool surface changes)
  3. `trinity-local install-extension --extension-id <prior-id>`
     re-runs if the user previously configured the Chrome extension
     (Native Messaging manifest may need refresh)
  4. `trinity-local status` to verify the new state (the former
     `doctor` command was retired 2026-05-18; its health checks live
     under `status` now — `commands/status.handle_status` calls
     `doctor.run_doctor()` internally and surfaces the one-line
     verdict)

Trust positioning: explicit user invocation only. v1.0 does NOT
auto-pull. `trinity-local status` runs a cached staleness check
(_check_skill_freshness) on every invocation and surfaces a stderr
banner when behind origin/main — that's the only "automatic"
notification surface. No background processes, no scheduled jobs.
The user runs `trinity-local update` when they're ready to apply.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "update",
        help="Pull latest, refresh MCP configs, verify with status check.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Don't pull — just report how many commits behind origin.",
    )
    parser.add_argument(
        "--skill-dir", default=None,
        help=(
            "Override skill directory. Default lookup: ~/.trinity/code/ "
            "(post-2026-05-19 canonical), then ~/.claude/skills/trinity/ "
            "(pre-pivot legacy). The actual resolution lives in _skill_dir() "
            "below."
        ),
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of a human-readable report.",
    )
    parser.add_argument(
        "--deps", action="store_true",
        help=(
            "Refresh pip-installed runtime deps (Pillow, mcp, numpy) via "
            "pip install --upgrade. Use after a Trinity upgrade that requires "
            "newer dep versions — the rare case; deps are stable across most "
            "releases."
        ),
    )
    parser.set_defaults(handler=handle_update)


def _skill_dir(override: str | None) -> Path:
    """Locate the Trinity Python source dir for update purposes.

    Lookup order:
      1. Explicit override (--skill-dir) — for tests and custom installs.
      2. ~/.trinity/code/ — the post-2026-05-19-pivot canonical location.
      3. ~/.claude/skills/trinity/ — pre-pivot legacy.

    Chrome Web Store extension installs are intentionally NOT probed here
    — Chrome auto-updates the extension dir on its own ~5h cadence; the
    user shouldn't (and can't) git pull inside an Extensions/ subdir.
    The `update` command short-circuits with a clear message in that
    case via the _is_git_checkout() check downstream.
    """
    if override:
        return Path(override).expanduser().resolve()
    canonical = Path.home() / ".trinity" / "code"
    if (canonical / ".git").exists():
        return canonical
    return Path.home() / ".claude" / "skills" / "trinity"


def _git(*args: str, cwd: Path) -> tuple[int, str, str]:
    """Run git with the given args in `cwd`. Returns (rc, stdout, stderr).

    Uses a 5-minute timeout so a stalled network (offline / DNS hang /
    captive portal) doesn't wedge `trinity-local update` indefinitely.
    On timeout we synthesize an rc=124 (the conventional `timeout(1)`
    exit code) + a clear stderr message so the caller's existing
    rc-based error paths surface it as a network failure rather than
    a silent hang.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "git operation timed out after 5 minutes (network stall?)"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _is_git_checkout(path: Path) -> bool:
    return (path / ".git").exists()


def _fetch_and_compute_lag(skill_dir: Path) -> tuple[int, int, str | None]:
    """Fetch origin + return (commits_behind, commits_ahead, error).

    error is None on success, a human-readable string on failure
    (network down, no remote, etc.). Network failures degrade gracefully —
    we treat them as "couldn't check" rather than "no updates".
    """
    rc, _, err = _git("fetch", "--quiet", "origin", cwd=skill_dir)
    if rc != 0:
        return 0, 0, f"git fetch failed: {err or '(no message)'}"

    rc, out, _ = _git("rev-list", "--count", "--left-right",
                      "HEAD...@{upstream}", cwd=skill_dir)
    if rc != 0:
        # Maybe no upstream tracking branch; try origin/main explicitly.
        rc, out, _ = _git("rev-list", "--count", "--left-right",
                          "HEAD...origin/main", cwd=skill_dir)
        if rc != 0:
            return 0, 0, "could not compute lag — no tracking branch?"
    parts = out.split()
    if len(parts) != 2:
        return 0, 0, f"unexpected rev-list output: {out!r}"
    ahead, behind = int(parts[0]), int(parts[1])
    return behind, ahead, None


def _refresh_pip_deps(json_mode: bool) -> int:
    """Re-install Pillow / mcp / numpy via pip --upgrade. Same packages
    install.sh writes; the user runs this on the rare upgrade that
    requires newer dep versions.

    Targets the DEDICATED venv install.sh creates at ``~/.trinity/venv``
    (v1.7.316). `pip install --user` is BLOCKED by PEP 668 on Homebrew /
    Debian / Ubuntu 23.04+ / Fedora system pythons
    (`externally-managed-environment`), so the venv is the only reliable
    target — refreshing into it matches install.sh's CURRENT behavior (it
    no longer uses --user). Via the installed wrapper, ``sys.executable``
    IS already that venv python (``in_venv``); we also probe the venv path
    directly so a non-wrapper invocation still lands in it. Only when NO
    venv exists anywhere do we fall back to the legacy ``--user`` path —
    and say so, since it can fail on a PEP-668 python.
    """
    in_venv = sys.prefix != sys.base_prefix
    dedicated_venv_py = Path.home() / ".trinity" / "venv" / "bin" / "python3"
    use_user = False
    if in_venv:
        pip_py = sys.executable
    elif dedicated_venv_py.exists():
        pip_py = str(dedicated_venv_py)
    else:
        pip_py = sys.executable
        use_user = True
    pip_args = [pip_py, "-m", "pip", "install", "--quiet", "--upgrade"]
    if use_user:
        pip_args.append("--user")
    pip_args.extend(["Pillow>=10", "mcp>=1.0", "numpy>=1.26"])

    result = subprocess.run(pip_args, capture_output=True, text=True, check=False)
    ok = result.returncode == 0
    target = "user site-packages" if use_user else "venv"
    if json_mode:
        print(json.dumps({
            "deps_updated": ok,
            "venv": not use_user,
            "target": target,
            "returncode": result.returncode,
            "stderr": result.stderr.strip(),
        }, indent=2))
    else:
        if ok:
            print(f"✓ Refreshed pip deps (Pillow + mcp + numpy) in {target}.")
        else:
            hint = (
                "\n  No ~/.trinity/venv found — `--user` can fail on PEP 668 "
                "'externally managed' pythons. Re-run install.sh to create the venv."
                if use_user else ""
            )
            print(
                f"✗ pip install --upgrade failed (returncode "
                f"{result.returncode}).{hint}\nstderr:\n{result.stderr}",
                file=sys.stderr,
            )
    return 0 if ok else 1


def handle_update(args: SimpleNamespace) -> int:
    skill_dir = _skill_dir(getattr(args, "skill_dir", None))
    json_mode = bool(getattr(args, "json", False))
    check_only = bool(getattr(args, "check", False))
    deps_only = bool(getattr(args, "deps", False))

    # --deps short-circuits the git-pull path. Pip-dep refresh is
    # decoupled from source updates; the user can refresh deps without
    # touching the source dir at all.
    if deps_only:
        return _refresh_pip_deps(json_mode)

    if not skill_dir.exists():
        msg = f"skill directory not found at {skill_dir}"
        return _emit_error(msg, json_mode)
    if not _is_git_checkout(skill_dir):
        # Two cases to disambiguate for the user:
        # (a) Trinity is running from inside a Chrome extension dir,
        #     in which case Chrome auto-update handles refresh.
        # (b) Partial install — no git checkout anywhere.
        from ..launchpad_data import dispatch_readiness
        try:
            extension_ready = dispatch_readiness().get("ready", False)
        except Exception:
            extension_ready = False
        if extension_ready:
            msg = (
                f"{skill_dir} is not a git checkout — Trinity may be "
                f"running from the Chrome extension package, which "
                f"auto-updates via the Web Store every ~5h. No manual "
                f"update needed for the Python side. To verify the "
                f"resolver picks the right source, run "
                f"`~/.local/bin/trinity-path-resolver.sh`."
            )
        else:
            msg = (
                f"{skill_dir} is not a git checkout — update requires the "
                f"source to be installed via scripts/install.sh (which "
                f"clones the repo to ~/.trinity/code/)."
            )
        return _emit_error(msg, json_mode)

    behind, ahead, fetch_err = _fetch_and_compute_lag(skill_dir)
    if fetch_err is not None:
        return _emit_error(fetch_err, json_mode, soft=True)

    if check_only:
        return _emit_check(behind, ahead, skill_dir, json_mode)

    if behind == 0:
        return _emit_already_up_to_date(ahead, json_mode)

    if ahead > 0:
        # User has local commits. ff-only would fail; we surface the
        # divergence rather than auto-rebasing.
        msg = (f"{skill_dir} has {ahead} local commit(s) not on origin; "
               f"refusing to fast-forward over them. Resolve manually "
               f"with `cd {skill_dir} && git status`.")
        return _emit_error(msg, json_mode)

    # Pull (fast-forward only).
    rc, _, err = _git("merge", "--quiet", "--ff-only", "@{upstream}",
                      cwd=skill_dir)
    if rc != 0:
        rc, _, err = _git("merge", "--quiet", "--ff-only", "origin/main",
                          cwd=skill_dir)
    if rc != 0:
        return _emit_error(f"git merge --ff-only failed: {err}", json_mode)

    # Re-run install-mcp to refresh any new MCP tool entries.
    mcp_rc = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "install-mcp"],
        capture_output=True, text=True, check=False,
    ).returncode

    # Re-run status (former `doctor` command) to verify. `doctor` was
    # retired 2026-05-18; its health checks live under `status` now —
    # invoking the old name here would error with "invalid choice".
    status_rc = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "status"],
        capture_output=True, text=True, check=False,
    ).returncode

    return _emit_updated(behind, mcp_rc, status_rc, json_mode)


def _emit_check(behind: int, ahead: int, skill_dir: Path,
                json_mode: bool) -> int:
    if json_mode:
        print(json.dumps({
            "behind": behind, "ahead": ahead,
            "skill_dir": str(skill_dir),
            "up_to_date": behind == 0,
        }, indent=2))
    else:
        if behind == 0:
            print(f"Trinity is up to date ({skill_dir}).")
        else:
            print(f"Trinity is {behind} commit(s) behind origin/main. "
                  f"Run `trinity-local update` to apply.")
            if ahead > 0:
                print(f"  ({ahead} local commit(s) ahead — will block the pull.)")
    return 0


def _emit_already_up_to_date(ahead: int, json_mode: bool) -> int:
    if json_mode:
        print(json.dumps({"updated": False, "ahead": ahead,
                          "message": "already up to date"}, indent=2))
    else:
        print("Trinity is already up to date.")
        if ahead > 0:
            print(f"  ({ahead} local commit(s) ahead of origin/main.)")
    return 0


def _emit_updated(applied: int, mcp_rc: int, status_rc: int,
                  json_mode: bool) -> int:
    if json_mode:
        print(json.dumps({
            "updated": True,
            "commits_applied": applied,
            "install_mcp_returncode": mcp_rc,
            "status_returncode": status_rc,
        }, indent=2))
        return 0

    print(f"✓ Pulled {applied} commit(s) from origin")
    if mcp_rc == 0:
        print("✓ Refreshed MCP configs")
    else:
        print(f"⚠ install-mcp returned {mcp_rc} — "
              "run `trinity-local install-mcp` to debug")
    if status_rc == 0:
        print("✓ status green")
    else:
        print(f"⚠ status returned {status_rc} — "
              "run `trinity-local status` to see what's wrong")
    print()
    # "new MCP tools" undersells the restart need — the MCP server
    # process loads Trinity's Python source ONCE at child-spawn time;
    # any code change (new tools, fixed tool behavior, bug patches in
    # existing handlers, retired hint surfaces) only takes effect on
    # the next spawn. Without a harness restart, users hit yesterday's
    # bugs against today's source.
    print("Note: restart Claude Code / Codex CLI / Antigravity / Cursor to "
          "pick up the new MCP server code. Each harness spawns the MCP "
          "child once at startup; without a restart, tool calls hit the "
          "previous Trinity version's handlers.")
    print("Note: Chrome extension users may need to reload the unpacked "
          "extension at chrome://extensions.")
    return 0


def _emit_error(msg: str, json_mode: bool, soft: bool = False) -> int:
    if json_mode:
        print(json.dumps({"updated": False, "error": msg}, indent=2),
              file=sys.stderr)
    else:
        prefix = "warning" if soft else "error"
        print(f"{prefix}: {msg}", file=sys.stderr)
    return 0 if soft else 1
