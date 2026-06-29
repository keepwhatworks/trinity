"""Guard: `trinity-local eval-audit` on a COLD (empty) home degrades gracefully —
a clean actionable message + clean exit, NOT a raw Python traceback.

Found dogfooding the CLI surface on an empty home (a brand-new user's first run):
`eval-audit` was the one read-only eval command that CRASHED. Its siblings
(`eval-show`, `eval-stats`, `eval-build`) catch the cold-home `FileNotFoundError`
from `build_eval_set` (no `me/preference_acts.jsonl` ledger yet) and print the
"run `trinity-local lens` first" hint; `handle_eval_audit` called `build_eval_set()`
RAW, so the exception propagated as a traceback. A documented command dumping a
stack trace at a new user is the [[eval_card_freshhome_firstrun_dead_end]] /
[[cli_main_dropped_handler_exit_codes]] class.

Fix: wrap the build in `try/except FileNotFoundError` → print the (already
actionable) message + `SystemExit(1)`, matching the sibling `handle_eval_build`.
Mutation-proven: revert to the raw `build_eval_set()` and this reds (a
FileNotFoundError escapes `pytest.raises(SystemExit)`).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.commands.eval import handle_eval_audit


@pytest.mark.usefixtures("patch_trinity_home")
def test_eval_audit_cold_home_exits_clean_not_traceback(capsys):
    # Empty home: no eval set on disk, no preference-act ledger → build_eval_set
    # raises FileNotFoundError. The handler must catch it, not propagate.
    with pytest.raises(SystemExit) as exc:
        handle_eval_audit(SimpleNamespace(eval_id=None, json=False))
    assert exc.value.code == 1

    out = capsys.readouterr().out
    # The actionable next step is surfaced (not swallowed), pointing at the verb
    # that mines the ledger.
    assert "trinity-local lens" in out, (
        f"eval-audit cold-home output is missing the actionable 'run lens' hint: {out!r}"
    )


@pytest.mark.usefixtures("patch_trinity_home")
def test_eval_audit_cold_home_does_not_write_state():
    """A failed cold audit must not leave partial state behind (it dispatches
    nothing and builds nothing)."""
    from trinity_local.state_paths import trinity_home

    with pytest.raises(SystemExit):
        handle_eval_audit(SimpleNamespace(eval_id=None, json=False))
    evals_dir = trinity_home() / "evals"
    # No eval set should have been written by a cold, failed audit.
    assert not list(evals_dir.glob("eval_*.json")) if evals_dir.exists() else True
