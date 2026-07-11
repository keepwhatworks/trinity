

def test_launchpad_data_counts_surface_open(tmp_path, monkeypatch):
    """Browser-surface instrumentation (council_25c534c5f1bf826c): every
    launchpad_data serve appends one PII-free line — the usage evidence any
    future launchpad/stats simplification must read first."""
    import json
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.capture_host import _query_launchpad_data
    _query_launchpad_data({})
    p = tmp_path / "analytics" / "surface_opens.jsonl"
    assert p.exists()
    row = json.loads(p.read_text().splitlines()[0])
    assert row["surface"] == "launchpad" and "at" in row and len(row) == 2
