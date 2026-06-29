"""End-to-end (real stdio JSON-RPC): get_picks must tell the calling agent which
picks ask() actually ROUTES on vs which are sub-floor near-ties.

picks.json keeps every basin with >=2 real-contest councils, but ask() only routes
on a basin whose winner-margin clears lens_routing.WINNER_MARGIN_FLOOR (0.15) — below
it the winner is a near-tie and ask falls to kNN. On the real corpus the margin
median is ~0.17 and ~11/23 picks are sub-floor, so an agent that reads get_picks and
treats every `{winner, margin}` as a firm "use X" rule is acting on coin-flips. The
tool returned the raw margin with NO routing signal and defaulted min_trust=0.0
(everything), so the agent had no way to tell a routed pick from a near-tie — the
agent-facing analog of the launchpad #299 demote and the memory-viewer overclaim
fix. (The in-process handler test would also catch the field, but the wire test pins
it on the surface an agent actually calls — a serialization/wire regression that
drops the new field still leaves every handler test green.)

This spawns `python -m trinity_local.main --mcp` exactly as the install configs do,
seeds one sub-floor basin (b00, margin 0.08) and one routed basin (b01, margin 0.42),
and over a real initialize → tools/call get_picks handshake asserts each pick carries
`routes` (b00 False, b01 True), the response surfaces `winner_margin_floor` and a
`routed` count, and `min_trust` still filters. Mutation-proven: drop the `routes`
annotation → the per-pick assertion reds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _read_response(proc: subprocess.Popen, timeout: float = 40.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return None
            time.sleep(0.02)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg and ("result" in msg or "error" in msg):
            return msg
    return None


def test_get_picks_marks_subfloor_picks_advisory_over_stdio(tmp_path: Path):
    home = tmp_path / "trinity"
    (home / "scoreboard").mkdir(parents=True)
    # b00 is BELOW WINNER_MARGIN_FLOOR (0.15) → near-tie ask routes via kNN.
    # b01 is ABOVE → a real route. Flat post-#298 schema.
    (home / "scoreboard" / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 8, "margin": 0.08, "n_episodes": 8, "evidence": ["c1"]},
        "b01": {"winner": "codex", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["c2"]},
    }), encoding="utf-8")

    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [sys.executable, "-m", "trinity_local.main", "--mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    def send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}}})
        init = _read_response(proc)
        assert init is not None and "result" in init, f"initialize failed: {init}"
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # full map (no min_trust) — the documented default an agent uses
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "get_picks", "arguments": {}}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"get_picks errored over the wire: {r}"
        assert not r["result"].get("isError"), f"get_picks reported an error: {r['result']}"
        payload = json.loads(r["result"]["content"][0]["text"])

        assert payload.get("winner_margin_floor") == 0.15, (
            f"get_picks didn't surface the routing floor over the wire: {payload.get('winner_margin_floor')!r} "
            "— the agent can't tell which picks ask actually routes on"
        )
        rules = payload["rules"]
        assert rules["b00"]["routes"] is False, (
            "sub-floor near-tie (margin 0.08) was NOT flagged routes=False — an agent "
            "would treat a coin-flip as a firm 'use claude' rule"
        )
        assert rules["b01"]["routes"] is True, (
            f"routed basin (margin 0.42) was not flagged routes=True: {rules['b01']!r}"
        )
        assert payload.get("routed") == 1, f"expected routed=1 of 2, got {payload.get('routed')!r}"

        # min_trust still filters to the routed basin only.
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "get_picks", "arguments": {"min_trust": 0.15}}})
        r2 = _read_response(proc)
        payload2 = json.loads(r2["result"]["content"][0]["text"])
        assert set(payload2["rules"].keys()) == {"b01"}, (
            f"min_trust=0.15 should drop the sub-floor pick, got {sorted(payload2['rules'])}"
        )
        assert all(p["routes"] for p in payload2["rules"].values()), (
            "every pick above the floor must be routes=True"
        )

        # The picks RESOURCE (handshake-time push, read FIRST by an agent) must
        # carry the SAME routes annotation as the tool — else the resource and the
        # tool disagree on whether a basin routes. (The catalog `name` is a human
        # title, so the server gates the annotation on the URI; a regression to a
        # name-based gate silently drops it.)
        send({"jsonrpc": "2.0", "id": 4, "method": "resources/read",
              "params": {"uri": "trinity://scoreboard/picks.json"}})
        r3 = _read_response(proc)
        assert r3 is not None and "result" in r3, f"resources/read picks errored: {r3}"
        rsrc = json.loads(r3["result"]["contents"][0]["text"])
        assert rsrc["b00"].get("routes") is False, (
            f"picks RESOURCE didn't flag the sub-floor basin routes=False: {rsrc.get('b00')!r} "
            "— the handshake-time push overclaims while the tool doesn't (sibling-surface drift)"
        )
        assert rsrc["b01"].get("routes") is True, (
            f"picks RESOURCE didn't flag the routed basin routes=True: {rsrc.get('b01')!r}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
