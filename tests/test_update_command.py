"""Regression guard for `trinity-local update` post-doctor-retirement.

`doctor` was retired 2026-05-18 (collapsed into `status`). Before this
test landed, `commands/update.py` still spawned
`python -m trinity_local.main doctor` as a subprocess at the end of
`trinity-local update`. The argparse surface no longer knows that
name, so update.py would receive a non-zero returncode AND print an
error message telling the user to run `trinity-local doctor` — a
command that doesn't exist.

Zero test coverage on update.py let it slip. This file pins:

1. The verify-step subprocess invokes `status`, not the retired
   `doctor` name. Static check on the source string — cheap,
   doesn't require running the whole update flow.
2. Anywhere update.py mentions a CLI subcommand by name, that name
   resolves in the live argparse surface. Catches the next case
   shape (someone retires `status` someday; the update flow has to
   migrate to whatever absorbs it).

Same shape as principle #4 ("when you fix a bug, audit for its
shape"): the immediate fix is the subprocess argv string; the
durable fix is a guard that watches every retired-name leak in this
specific file.
"""
from __future__ import annotations

import re
from pathlib import Path



UPDATE_PY = (
    Path(__file__).resolve().parents[1]
    / "src" / "trinity_local" / "commands" / "update.py"
)


class TestUpdateUsesStatusNotDoctor:
    def test_subprocess_invokes_status_subcommand(self):
        """The verify-step spawn must call `status`, not `doctor`."""
        src = UPDATE_PY.read_text(encoding="utf-8")
        # Look for argv lists that pass a subcommand to
        # `trinity_local.main`. Both forms tolerated; we want the
        # subcommand string itself to be `status`.
        spawns = re.findall(
            r'\[\s*sys\.executable\s*,\s*"-m"\s*,\s*"trinity_local\.main"\s*,'
            r'\s*"([a-z_-]+)"',
            src,
        )
        assert spawns, (
            "update.py no longer spawns `python -m trinity_local.main "
            "<subcommand>`. If the spawn pattern was rewritten, update "
            "this test to match the new shape."
        )
        # The verify-step spawn (the one that replaced `doctor`)
        # MUST be present. update.py also spawns `install-mcp`; that's
        # fine. What we don't tolerate is any retired-name spawn that
        # would dead-end at argparse.
        from trinity_local.retired_names import all_names as retired_all
        retired = set(retired_all())
        leaked = [c for c in spawns if c in retired]
        assert not leaked, (
            f"update.py spawns retired subcommand(s) {leaked!r} via "
            f"`python -m trinity_local.main <cmd>`. The argparse "
            f"surface no longer registers these names; the spawn will "
            f"return non-zero and the user-facing message will point "
            f"at a non-existent command. Replace with the absorbing "
            f"command (e.g. `doctor` → `status` per the 2026-05-18 "
            f"collapse)."
        )
        # And the specific verify-step shape: `status` MUST be one of
        # the spawns (it's the doctor replacement). Loose check that
        # tolerates future re-orderings of the install-mcp / status
        # subprocess sequence.
        assert "status" in spawns, (
            f"update.py no longer spawns `trinity_local.main status` "
            f"as the verify step. `doctor` was retired and its checks "
            f"live under `status` now; the verify step needs to call "
            f"one of them. Spawns observed: {spawns!r}."
        )


class TestUpdateUserFacingMessagesPointAtLiveCommands:
    def test_no_user_facing_doctor_invocations(self):
        """Catch any leaked `trinity-local doctor` string in error
        messages, help text, or docstrings. Same retirement-registry
        shape as test_retired_names_registry, but scoped to this
        single file so failures are surgical."""
        src = UPDATE_PY.read_text(encoding="utf-8")
        # Strip historical-marker comments so we tolerate "former
        # `doctor` command was retired" framings.
        # Paragraph-split: any block with a historical marker is OK.
        paragraphs = re.split(r"\n\s*\n", src)
        HISTORICAL = (
            "retired", "was retired", "no longer exists", "former",
            "absorbed", "collapsed",
        )
        leaks: list[str] = []
        for para in paragraphs:
            lower = para.lower()
            if any(marker in lower for marker in HISTORICAL):
                continue
            # Look for `trinity-local doctor` in user-facing strings.
            if "trinity-local doctor" in para:
                leaks.append(para.strip()[:160])
        assert not leaks, (
            "update.py mentions `trinity-local doctor` in a "
            "non-historical paragraph. The user will see this in "
            "stderr / help / status messages and try to run a command "
            "that doesn't exist. Either past-tense the framing "
            "(\"the former `doctor` command\") or replace with "
            "`status`.\n\n" + "\n---\n".join(leaks[:5])
        )
