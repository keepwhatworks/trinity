"""The launch-critical update path: `trinity-local update` on a SHALLOW clone.

install.sh installs the majority of users via `git clone --depth 1` to
``~/.trinity/code/`` (no PyPI). `trinity-local update` is then the ONLY way every
shipped fix reaches them — it `git fetch`es origin, computes the lag with
``rev-list --count --left-right HEAD...@{upstream}``, and `git pull --ff-only`s.

That `...` (symmetric difference) needs a merge-base, and a ``--depth 1`` clone has
a TRUNCATED history — so a naive expectation is that the lag comes back wrong
(no common ancestor → counts everything → a false ``ahead > 0`` that makes
handle_update refuse the fast-forward with "you have local commits"). In practice
`git fetch` auto-deepens enough to connect, so it works — but nothing GUARDED that:
the existing update tests are structural (install.sh syntax/paths) or mock
subprocess (the timeout test). A regression that broke it (install.sh dropping
``--depth 1`` in a way that matters, or update.py's rev-list logic changing) would
silently strand every curl|sh user on old code with a confusing error.

This pins the real path with real git: shallow-clone an origin, push new commits,
and assert update detects the lag correctly (behind=N, ahead=0) and fast-forwards.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from trinity_local.commands import update


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _commit(work: Path, msg: str) -> None:
    (work / "f.txt").write_text(msg, encoding="utf-8")
    _git("add", "f.txt", cwd=work)
    _git("commit", "-q", "-m", msg, cwd=work)


@pytest.fixture
def shallow_install(tmp_path: Path):
    """A bare 'origin' + a `--depth 1` shallow clone (the curl|sh install shape),
    with a helper to push N new commits to origin. Skips if git is unavailable."""
    if shutil.which("git") is None:
        pytest.skip("git not available")

    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)

    work = tmp_path / "work"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    _git("config", "user.email", "t@t.co", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    _commit(work, "base")
    _git("push", "-q", "origin", "HEAD:main", cwd=work)

    shallow = tmp_path / "code"  # mimics ~/.trinity/code/
    _git("clone", "-q", "--depth", "1", "--branch", "main",
         f"file://{origin}", str(shallow), cwd=tmp_path)

    def push(n: int) -> None:
        for i in range(1, n + 1):
            _commit(work, f"fix {i}")
        _git("push", "-q", "origin", "HEAD:main", cwd=work)

    return shallow, push


def test_shallow_clone_is_actually_shallow(shallow_install):
    """Sanity: the install really is a depth-1 shallow clone (the trap's premise)."""
    shallow, _ = shallow_install
    out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=str(shallow),
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "1", f"expected a depth-1 shallow clone, got {out} commits of history"


def test_fetch_and_compute_lag_correct_on_shallow_clone(shallow_install):
    """The core: after upstream commits, the lag is (behind=N, ahead=0) — NOT a
    false `ahead>0` from a missing merge-base, which would make update refuse the
    fast-forward as 'you have local commits'."""
    shallow, push = shallow_install
    push(18)  # a full loop session of fixes
    behind, ahead, err = update._fetch_and_compute_lag(shallow)
    assert err is None, f"lag computation errored on a shallow clone: {err}"
    assert ahead == 0, (
        f"shallow clone falsely reports {ahead} local commit(s) ahead — handle_update "
        "would refuse the fast-forward and strand the curl|sh user on old code"
    )
    assert behind == 18, f"expected 18 commits behind, got {behind}"


def test_handle_update_check_reports_update_available(shallow_install):
    """End-to-end: `update --check` on a shallow clone behind origin reports the
    update is available (exit 0 + behind count), not the 'local commits' refusal."""
    from types import SimpleNamespace

    shallow, push = shallow_install
    push(3)
    args = SimpleNamespace(skill_dir=str(shallow), json=True, check=True, deps=False)
    rc = update.handle_update(args)
    assert rc == 0, "update --check on a behind shallow clone should exit 0"


def test_ff_only_pull_advances_the_shallow_clone(shallow_install):
    """The actual delivery: a fast-forward pull on the shallow clone lands the new
    commits — the fixes reach the user."""
    shallow, push = shallow_install
    push(5)
    # _git is update's own helper (timeout-wrapped) — use it for parity.
    update._git("fetch", "--quiet", "origin", cwd=shallow)
    rc, _, err = update._git("pull", "--ff-only", "--quiet", cwd=shallow)
    assert rc == 0, f"ff-only pull failed on the shallow clone: {err}"
    head_msg = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=str(shallow),
                              capture_output=True, text=True, check=True).stdout.strip()
    assert head_msg == "fix 5", f"shallow clone didn't advance to the new tip: {head_msg!r}"
