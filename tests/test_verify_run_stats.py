"""The verify run log has a reader: `status` reports how many measured green tests still passed
with the change reverted. Silent until something was measured."""
from __future__ import annotations

import json
from types import SimpleNamespace


def _log(home, rows):
    d = home / "analytics"
    d.mkdir(parents=True, exist_ok=True)
    (d / "verify_runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_run_stats_sums_the_measured_statuses(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.verify import run_stats
    assert run_stats() is None
    _log(tmp_path, [{"differential": {"vacuous": 2, "detects": 1}}, {"differential": None},
                    {"differential": {"no_load": 1, "not_reproduced": 3}}])
    assert run_stats() == {"runs": 3, "measured_tests": 4, "vacuous": 2, "detects": 2, "not_measured": 3}


def test_status_prints_the_verify_line_only_when_measured(tmp_path, monkeypatch, capsys):
    """MUTATION: drop the Verify line from status and this reds."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.commands import status
    _log(tmp_path, [{"differential": {"vacuous": 1, "detects": 3}}])
    status.handle_status(SimpleNamespace(as_json=False, config=None))
    assert "Verify:    1 of 4 green tests across 1 logged runs still passed with the change reverted" \
        in capsys.readouterr().out
    _log(tmp_path, [{"differential": None}])
    status.handle_status(SimpleNamespace(as_json=False, config=None))
    assert "Verify:" not in capsys.readouterr().out


def test_status_json_is_silent_when_nothing_was_measured(tmp_path, monkeypatch, capsys):
    import json as _json
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.commands import status
    _log(tmp_path, [{"differential": None}])
    status.handle_status(SimpleNamespace(as_json=True, config=None))
    assert _json.loads(capsys.readouterr().out)["verify_runs"] is None
    _log(tmp_path, [{"differential": {"vacuous": 1}}])
    status.handle_status(SimpleNamespace(as_json=True, config=None))
    assert _json.loads(capsys.readouterr().out)["verify_runs"]["vacuous"] == 1
