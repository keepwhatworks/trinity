"""Guard: a missing/typo'd TASK id (and the council `--outcome` branch) degrades to a
one-line error, NOT a traceback — the task-loader sibling of
test_council_outcome_missing.py.

Found extending the CLI bad-arg dogfood: `review --task <id>`, `open-review --task
<id>`, and `open-review --outcome <id>` all crashed with a raw `FileNotFoundError`
(stack trace + leaked absolute `~/.trinity/todos/<id>.json` / `council_outcomes/…`
path) on a nonexistent id. `load_task_record` was called RAW (like `load_council_
outcome` was), and `open-review --outcome` bypassed the `_or_exit` helper.

Fix: `load_task_record` now raises a clean message + a `load_task_record_or_exit`
CLI helper SystemExits; the three handlers route through the `_or_exit` helpers.
Mutation-proven: revert a handler to the raw loader → FileNotFoundError escapes the
`pytest.raises(SystemExit)` → reds.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.task_runtime import load_task_record, load_task_record_or_exit


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_task_record_missing_clean_message():
    with pytest.raises(FileNotFoundError) as exc:
        load_task_record("task_does_not_exist")
    msg = str(exc.value)
    assert "task_does_not_exist" in msg
    # No leaked absolute path (the prior bare pathlib error dumped ~/.trinity/todos/…).
    assert "/todos/" not in msg and "/tasks/" not in msg, f"error leaks the path: {msg!r}"


@pytest.mark.usefixtures("patch_trinity_home")
def test_load_task_record_or_exit_systemexits():
    with pytest.raises(SystemExit) as exc:
        load_task_record_or_exit("task_does_not_exist")
    assert "task_does_not_exist" in str(exc.value)


@pytest.mark.usefixtures("patch_trinity_home")
def test_review_bad_task_id_exits_clean():
    from trinity_local.commands.review import handle_review

    # The handler loads the task on its FIRST line — a bad id must SystemExit, not
    # raise FileNotFoundError. (reviewer/cwd are never reached.)
    with pytest.raises(SystemExit) as exc:
        handle_review(SimpleNamespace(task="task_nope", reviewer="claude", cwd=None, config=None))
    assert "task_nope" in str(exc.value)


@pytest.mark.usefixtures("patch_trinity_home")
def test_open_review_bad_task_and_outcome_exit_clean():
    from trinity_local.commands.portal import handle_open_review

    with pytest.raises(SystemExit) as exc_task:
        handle_open_review(SimpleNamespace(path=None, task="task_nope", outcome=None))
    assert "task_nope" in str(exc_task.value)

    with pytest.raises(SystemExit) as exc_out:
        handle_open_review(SimpleNamespace(path=None, task=None, outcome="council_nope"))
    assert "council_nope" in str(exc_out.value)
