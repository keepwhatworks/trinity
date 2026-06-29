"""Guard: a missing/typo'd council id degrades to a one-line error, NOT a traceback.

Found dogfooding the CLI surface with a nonexistent id (a user typos or references a
deleted council): `review-link`, `council-share`, and `council-iterate` all called
`load_council_outcome(<id>)` RAW, so a missing council raised a bare
`FileNotFoundError` — a Python stack trace dumped at the user, leaking the absolute
`~/.trinity/council_outcomes/<id>.json` path. Same first-run-robustness class as the
eval-audit cold-home fix.

Fix: `load_council_outcome` now raises a CLEAN FileNotFoundError (the id, no leaked
path), and CLI handlers route through `load_council_outcome_or_exit`, which catches it
and `SystemExit`s with a one-line message. The library function keeps raising so glob-
scanning callers (personal_routing._scan_outcomes) can still catch + skip.

Mutation-proven: revert a handler to the raw `load_council_outcome` and the
`pytest.raises(SystemExit)` reds (a FileNotFoundError escapes).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.council_runtime import (
    load_council_outcome,
    load_council_outcome_or_exit,
    load_prompt_bundle,
    load_prompt_bundle_or_exit,
)


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_council_outcome_missing_raises_clean_message():
    with pytest.raises(FileNotFoundError) as exc:
        load_council_outcome("council_does_not_exist")
    msg = str(exc.value)
    assert "council_does_not_exist" in msg, "the error should name the missing id"
    # No leaked absolute path (the prior bare pathlib error dumped the full
    # ~/.trinity/council_outcomes/<id>.json — a path + username leak).
    assert "/council_outcomes/" not in msg, f"error leaks the absolute path: {msg!r}"


@pytest.mark.usefixtures("patch_trinity_home")
def test_or_exit_helper_raises_systemexit_not_filenotfound():
    with pytest.raises(SystemExit) as exc:
        load_council_outcome_or_exit("council_does_not_exist")
    # SystemExit carries the message (a string), not a numeric crash code.
    assert "council_does_not_exist" in str(exc.value)


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_prompt_bundle_missing_clean_and_or_exit():
    # The third entity loader (sibling of council_outcome + task_record): a missing
    # bundle raises a clean message (no leaked ~/.trinity/prompt_bundles/<id> path),
    # and the _or_exit helper SystemExits — so council-start --bundle <bad> and a
    # review-link whose outcome outlived its bundle degrade, not traceback.
    with pytest.raises(FileNotFoundError) as exc:
        load_prompt_bundle("bundle_does_not_exist")
    assert "bundle_does_not_exist" in str(exc.value)
    assert "/prompt_bundles/" not in str(exc.value), f"error leaks path: {exc.value!r}"

    with pytest.raises(SystemExit) as sx:
        load_prompt_bundle_or_exit("bundle_does_not_exist")
    assert "bundle_does_not_exist" in str(sx.value)


@pytest.mark.usefixtures("patch_trinity_home")
def test_review_link_bad_id_exits_clean_not_traceback():
    from trinity_local.commands.portal import handle_review_link

    # A documented command + a bad id must exit cleanly, NOT raise FileNotFoundError.
    # (SystemExit(str) is printed by the interpreter at exit — pytest.raises catches
    # it first, so the message lives on the exception, not captured stdout.)
    with pytest.raises(SystemExit) as exc:
        handle_review_link(SimpleNamespace(council_id="council_nope", as_json=False))
    assert "council_nope" in str(exc.value), (
        f"the SystemExit didn't carry an actionable message: {exc.value!r}"
    )


# --- TRUNCATED / 0-byte corruption (a crash or `kill -9` mid-save) -------------
# A council whose outcome file got cut off mid-write (or `touch`ed-but-never-
# written → 0 bytes) EXISTS on disk, so the missing-file FileNotFoundError branch
# never fires — instead the bare `json.loads(path.read_text())` raised a raw
# `json.JSONDecodeError`. That escaped `load_council_outcome_or_exit` (which only
# caught FileNotFoundError), so `trinity-local council-share <id>` /
# `council-iterate <id>` on a crash-corrupted council printed a Python stack trace
# instead of a one-line error (the #43102d25 raw-exception-leak class). The
# missing-id sibling above is the SAME loader + SAME commands; this is the
# truncated/0-byte shape of the same robustness bug.


def _seed_corrupt_outcome(home, council_id: str, body: str) -> None:
    from trinity_local.state_paths import council_outcomes_dir

    cdir = council_outcomes_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / f"{council_id}.json").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "council_id,body",
    [
        # Truncated mid-write: a `kill -9` between the open and the close.
        ("council_trunc", '{"council_run_id": "council_trunc", "winner_provider'),
        # 0-byte: `touch`ed / opened-but-never-written.
        ("council_empty", ""),
    ],
)
@pytest.mark.usefixtures("patch_trinity_home")
def test_load_council_outcome_truncated_raises_clean_not_jsondecodeerror(
    patch_trinity_home, council_id, body
):
    import json

    _seed_corrupt_outcome(patch_trinity_home, council_id, body)
    # The loader must NOT leak a raw json.JSONDecodeError — it must raise the same
    # CLEAN, catchable error the wrong-type guard uses, so glob-scanners skip and
    # the CLI helper exits one-line.
    with pytest.raises(ValueError) as exc:
        load_council_outcome(council_id)
    # The raw json.JSONDecodeError SUBCLASSES ValueError, so `pytest.raises(ValueError)`
    # alone is vacuous — pin the message + assert it is NOT the raw decoder error.
    assert not isinstance(exc.value, json.JSONDecodeError), (
        "REGRESSION: a truncated/0-byte council_outcomes file leaked a raw "
        f"json.JSONDecodeError out of load_council_outcome ({council_id}) — "
        "`council-share`/`council-iterate` on a crash-corrupted council tracebacks. "
        f"Got: {exc.value!r}"
    )
    msg = str(exc.value)
    assert council_id in msg, f"the error should name the council id: {msg!r}"
    # TYPE-ONLY honest message — no leaked absolute path (the #43102d25 class).
    assert "/council_outcomes/" not in msg, f"error leaks the absolute path: {msg!r}"

    # The glob scanners (_scan_outcomes / _load_recent_councils) call the loader
    # with the FULL PATH, not the bare id — and the message interpolated whatever
    # was passed, so the path-call form leaked the absolute FS path while the
    # id-call form above did not. Pin the path-call form too (uses path.stem).
    from trinity_local.state_paths import council_outcomes_dir

    full = council_outcomes_dir() / f"{council_id}.json"
    with pytest.raises(ValueError) as exc_p:
        load_council_outcome(str(full))
    pmsg = str(exc_p.value)
    assert not isinstance(exc_p.value, json.JSONDecodeError), f"path-call leaked raw decoder: {pmsg!r}"
    assert council_id in pmsg, f"path-call should name the council id: {pmsg!r}"
    assert "/council_outcomes/" not in pmsg and str(full) not in pmsg, (
        f"path-call form leaks the absolute FS path: {pmsg!r}"
    )


@pytest.mark.parametrize(
    "council_id,body",
    [
        ("council_trunc", '{"council_run_id": "council_trunc", "winner_provider'),
        ("council_empty", ""),
    ],
)
@pytest.mark.usefixtures("patch_trinity_home")
def test_or_exit_helper_systemexits_on_truncated_outcome(
    patch_trinity_home, council_id, body
):
    _seed_corrupt_outcome(patch_trinity_home, council_id, body)
    with pytest.raises(SystemExit) as exc:
        load_council_outcome_or_exit(council_id)
    assert council_id in str(exc.value), (
        f"the SystemExit didn't carry an actionable message: {exc.value!r}"
    )


@pytest.mark.usefixtures("patch_trinity_home")
def test_council_share_on_truncated_outcome_exits_clean_not_traceback(
    patch_trinity_home,
):
    # The end-to-end founder symptom: a documented CLI command on a crash-corrupted
    # council outcome must exit one-line, NOT dump a json.JSONDecodeError stack trace.
    from trinity_local.commands.council import handle_council_share

    _seed_corrupt_outcome(
        patch_trinity_home,
        "council_trunc",
        '{"council_run_id": "council_trunc", "winner_provider',
    )
    args = SimpleNamespace(council="council_trunc", out=None, open_after=False)
    with pytest.raises(SystemExit) as exc:
        handle_council_share(args)
    assert "council_trunc" in str(exc.value), (
        f"council-share on a truncated outcome didn't exit cleanly: {exc.value!r}"
    )
