"""The side-panel council page (sandboxed, opaque origin) can't read ~/.trinity
.js files via <script> injection like its file:// twin, so it fetches the same
objects from the host over the bridge. These query handlers read the EXISTING
.js files the runner writes and extract their JSON object VERBATIM — that's how
the two transports stay byte-identical (no re-derivation = no shape drift).

This pins the extraction (string-aware brace matching, last assignment wins) and
each query's file path + key, so a council page rendered from the bridge gets the
same object the <script>-injection path would have set.

ONE deliberate divergence: the `council_status` query routes through
`load_council_status` (the sibling .json + the dead-runner staleness coercion), NOT a
raw .js extraction — so a DEAD+STALE 'running' council reaches the side panel as a
terminal 'failed' card instead of an infinite spinner (the host-RPC sibling of the
already-coerced popup poll + launchpad scan; see
test_sidepanel_dead_council_terminal_in_panel_browser). outcome/thread/active stay
raw-extraction (no staleness notion).
"""
from __future__ import annotations

import json


from trinity_local import capture_host


def test_extract_js_object_basic():
    txt = (
        'window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n'
        'window.__TRINITY_COUNCIL_STATUS__["tok"] = {"status":"completed","n":3};\n'
    )
    assert capture_host._extract_js_object(txt) == {"status": "completed", "n": 3}


def test_extract_js_object_string_with_braces():
    # A brace INSIDE a string value must not end the object early.
    obj = {"task_text": "use a dict like {a: 1} } not {", "ok": True}
    txt = f'window.__TRINITY_COUNCIL_OUTCOME__["c"] = {json.dumps(obj)};\n'
    assert capture_host._extract_js_object(txt) == obj


def test_extract_js_object_single_assignment():
    txt = 'window.__TRINITY_ACTIVE_COUNCIL__ = {"status_token":"launch_x","members":["claude"]};\n'
    assert capture_host._extract_js_object(txt) == {"status_token": "launch_x", "members": ["claude"]}


def test_extract_js_object_none_when_absent():
    assert capture_host._extract_js_object("// no assignment here\n") is None


def _seed(tmp_path, monkeypatch):
    home = tmp_path / ".trinity"
    (home / "portal_pages" / "status").mkdir(parents=True)
    (home / "council_outcomes").mkdir(parents=True)
    (home / "review_pages").mkdir(parents=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    return home


def test_query_council_status_returns_the_status_object(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    # Write via the real writer (the .json + .js pair the runner always writes
    # together). _query_council_status now routes through load_council_status — the
    # SAME coercion the popup's get-council-status poll + the launchpad scan apply —
    # so a dead+stale 'running' council can't reach the side panel as an infinite
    # spinner (test_sidepanel_dead_council_terminal_in_panel_browser). A terminal
    # 'completed' status is returned verbatim (no coercion to apply).
    from trinity_local.council_status import write_council_status

    write_council_status(
        "launch_abc", status="completed", task_text="why?", council_id="council_z",
    )
    r = capture_host._query_council_status({"status_token": "launch_abc"})
    assert r["ok"] is True
    result = r["result"]
    assert result is not None
    assert result["status"] == "completed"
    assert result["task_text"] == "why?"
    assert result["council_id"] == "council_z"


def test_query_council_outcome_returns_the_outcome_object(tmp_path, monkeypatch):
    home = _seed(tmp_path, monkeypatch)
    outcome = {"council_run_id": "council_z", "winner_provider": "claude", "member_results": []}
    (home / "council_outcomes" / "council_z.js").write_text(
        f'window.__TRINITY_COUNCIL_OUTCOME__["council_z"] = {json.dumps(outcome)};\n', encoding="utf-8")
    r = capture_host._query_council_outcome({"council_id": "council_z"})
    assert r == {"ok": True, "result": outcome}


def test_query_thread_manifest_returns_the_manifest(tmp_path, monkeypatch):
    home = _seed(tmp_path, monkeypatch)
    manifest = {"chain_root_id": "bundle_z", "segments": [{"round_number": 1}]}
    (home / "council_outcomes" / "_thread_bundle_z.js").write_text(
        f'window.__TRINITY_COUNCIL_THREAD__["bundle_z"] = {json.dumps(manifest)};\n', encoding="utf-8")
    r = capture_host._query_thread_manifest({"thread_id": "bundle_z"})
    assert r == {"ok": True, "result": manifest}


def test_query_active_council_returns_the_pointer(tmp_path, monkeypatch):
    home = _seed(tmp_path, monkeypatch)
    ptr = {"status_token": "launch_q", "task": "t", "members": ["claude", "codex"]}
    (home / "review_pages" / "_active_council.js").write_text(
        f'window.__TRINITY_ACTIVE_COUNCIL__ = {json.dumps(ptr)};\n', encoding="utf-8")
    r = capture_host._query_active_council({})
    assert r == {"ok": True, "result": ptr}


def test_missing_file_returns_null_result(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert capture_host._query_council_status({"status_token": "launch_nope"}) == {"ok": True, "result": None}


def test_invalid_ids_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert capture_host._query_council_status({"status_token": "../../etc/passwd"})["ok"] is False
    assert capture_host._query_council_outcome({"council_id": "a/b"})["ok"] is False
    assert capture_host._query_thread_manifest({"thread_id": "x;y"})["ok"] is False


def test_all_council_queries_are_registered():
    for k in ("council_status", "council_outcome", "thread_manifest", "active_council"):
        assert k in capture_host.QUERY_KINDS
        assert k in capture_host.QUERY_HANDLERS
