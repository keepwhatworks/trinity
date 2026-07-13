

class TestLaunchParserToleratesLeadingNoise:
    """Stale-server class (2026-07-13): pre-fix code printed lens-kick progress
    to stdout AHEAD of the launch JSON, and the strict parse failed a launch
    that had actually succeeded (council_run_id was in the buffer). The parser
    now recovers the LAST {...} block. Mutation: drop the tail-recovery → red."""

    def test_last_json_block_recovered(self):
        import json
        captured = ('Stage 0: turn-pair rejection extraction (chairman: claude)…\n'
                    '           → delta: 20 new pair(s), 180 already extracted\n'
                    '{\n  "council_run_id": "council_abc123",\n  "opened": false\n}')
        tail = captured[captured.rfind("\n{") + 1:] if "\n{" in captured else captured
        raw = json.loads(tail)
        assert raw["council_run_id"] == "council_abc123"

    def test_mcp_handler_source_carries_the_recovery(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "src/trinity_local/mcp_server.py").read_text()
        idx = src.find('council launch produced unparseable output')
        assert idx != -1
        window = src[max(0, idx - 900):idx]
        assert 'rfind' in window and "json.loads(tail)" in window, (
            "the leading-noise recovery was removed — a stale server's stdout "
            "prints would again fail launches that succeeded"
        )
