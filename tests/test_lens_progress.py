"""#242(a) live lens-build progress + cooperative cancel."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import trinity_local.state_paths as sp
    monkeypatch.setattr(sp, "state_dir", lambda: tmp_path)
    import trinity_local.lens_progress as _lp
    monkeypatch.setattr(_lp, "state_dir", lambda: tmp_path)
    return tmp_path


def test_write_read_progress_roundtrip():
    import trinity_local.lens_progress as lp
    lp.write_progress("stage3")
    p = lp.read_progress()
    assert p is not None
    assert p.stage == "stage3"
    assert p.label == "Finding your taste tensions"
    assert p.pct == 85
    assert p.status == "running"
    assert p.started_at and p.updated_at


def test_terminal_status_and_pct_table():
    import trinity_local.lens_progress as lp
    lp.write_progress("done", status="complete")
    p = lp.read_progress()
    assert p.status == "complete" and p.pct == 100


def test_cancel_flag_and_raise():
    import trinity_local.lens_progress as lp
    assert not lp.is_canceled()
    lp.request_cancel()
    assert lp.is_canceled()
    with pytest.raises(lp.LensBuildCanceled):
        lp.raise_if_canceled()
    lp.clear_cancel()
    assert not lp.is_canceled()
    lp.raise_if_canceled()  # no raise when cleared


def test_started_at_stable_across_running_writes():
    import trinity_local.lens_progress as lp
    lp.write_progress("basins")
    first = lp.read_progress().started_at
    lp.write_progress("stage3")
    assert lp.read_progress().started_at == first  # same build, started_at frozen


def test_launchpad_card_shows_running_hides_old_terminal(monkeypatch):
    import trinity_local.lens_progress as lp
    import trinity_local.launchpad_data as ld
    # running -> card shows
    lp.write_progress("stage2")
    card = ld._lens_build_for_launchpad()
    assert card and card["building"] and card["pct"] == 60
    # a terminal state long ago -> hidden
    import json
    p = lp.read_progress()
    raw = p.to_dict()
    raw["status"] = "complete"
    raw["updated_at"] = "2020-01-01T00:00:00+00:00"  # ancient
    lp.progress_path().write_text(json.dumps(raw), encoding="utf-8")
    assert ld._lens_build_for_launchpad() is None


def test_lens_stop_cli_sets_cancel(capsys):
    from types import SimpleNamespace
    from trinity_local.commands.me import handle_lens_stop
    import trinity_local.lens_progress as lp
    handle_lens_stop(SimpleNamespace())
    assert lp.is_canceled()


def test_first_build_gate_requires_no_lens(monkeypatch):
    import trinity_local.cold_start as cs
    # no lens, scan complete, embeddings present -> would build
    monkeypatch.setattr(cs, "_autoscan_disabled", lambda: False)
    monkeypatch.setattr(cs, "_no_lens_yet", lambda: False)  # lens exists
    ok, reason = cs.should_build_first_lens()
    assert ok is False and "lens exists" in reason
