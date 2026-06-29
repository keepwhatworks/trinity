"""guard_shape_not_just_parse for single-file readers OFF the launchpad render path.

The portal-html corruption sweep (two iterations ago) covered readers ON the launchpad
render path; these run elsewhere and were unverified:

  * council_status.load_council_status — read by the get_council_status MCP tool AND
    the live-council poll. A valid-JSON-but-non-dict status file (partial write /
    hand-edit) crashed _coerce_stale_running_status's `.get` with an uncaught
    AttributeError (verified: `'list' object has no attribute 'get'`). This is USER
    STATE under ~/.trinity, written at runtime — genuinely corruptible.
  * global_benchmarks.get_global_benchmarks / get_reference_evals_meta — read the
    bundled data/reference_evals.json; `.get` past the `except (JSONDecodeError,
    OSError)` crashed on a non-dict. Lower risk (bundled data ships valid), but the
    `fetched_at` field implies it can be refreshed → a bad refresh could corrupt it.

Both now degrade (None / hardcoded fallback / {}). Mutation-proven: drop the relevant
isinstance guard → the non-dict file crashes → reds.
"""
from __future__ import annotations

import json

import pytest

_JUNK = ["[1, 2, 3]", "42", '"a bare string"', "null"]


@pytest.mark.parametrize("bad", _JUNK)
def test_council_status_non_dict_returns_none(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.council_status import load_council_status, council_status_json_path

    p = council_status_json_path("tok")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(bad, encoding="utf-8")
    assert load_council_status("tok") is None, "a non-dict status file must read back as None, not crash"


def test_council_status_valid_dict_passes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local.council_status import load_council_status, council_status_json_path

    p = council_status_json_path("tok")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"status": "completed", "members": {}}), encoding="utf-8")
    out = load_council_status("tok")
    assert out is not None and out["status"] == "completed"


@pytest.mark.parametrize("bad", _JUNK)
def test_global_benchmarks_non_dict_degrades(tmp_path, monkeypatch, bad):
    """Monkeypatch the path to a TEMP file — never touch the real repo
    data/reference_evals.json (a non-isolated write would corrupt a tracked file)."""
    import trinity_local.global_benchmarks as gb

    fake = tmp_path / "reference_evals.json"
    fake.write_text(bad, encoding="utf-8")
    monkeypatch.setattr(gb, "_reference_evals_path", lambda: fake)
    # get_global_benchmarks falls back to the hardcoded baseline (non-empty dict).
    cats = gb.get_global_benchmarks()
    assert isinstance(cats, dict) and cats, "should fall back to the hardcoded baseline, not crash"
    # get_reference_evals_meta returns {} on a non-dict.
    assert gb.get_reference_evals_meta() == {}
