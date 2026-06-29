"""End-to-end smoke of the SPAWNED MCP server over real stdio JSON-RPC.

Every other MCP test calls the handlers in-process (test_mcp_tools.py,
test_mcp_resources.py). That can't catch the class of bug that breaks every
harness silently: the server process failing to START or to speak the wire
protocol — a bad import at module load, the HF_HUB_OFFLINE env pin throwing,
a transport-framing regression, or `run_stdio_server` / the mcp library wiring
breaking. Those pass all the handler tests and still leave Claude Code / Codex /
Cursor unable to connect.

This spawns `python -m trinity_local.main --mcp` exactly as the install configs
do (install.py:105) and drives a real initialize → list → read → call handshake
over stdin/stdout, against an isolated TRINITY_HOME. It pins, at the transport
layer, behaviours that the in-process handler tests can't see fail end-to-end:
  - core.md reads from the TOP-LEVEL path, not the cold-start stub (v1.7.136)
  - topics.json is projected — no 768-dim centroid vectors on the wire (v1.7.137)
  - the `logging` capability is advertised (#264 — set_logging_level wired)
  - an UNSEEDED resource returns the actionable cold-start stub, not a 404
    (the cold-install agent UX)
  - a scoreboard resource folds web-era slugs to dispatchable CLI slugs over the
    wire, matching the get_picks tool (v1.7.166)
  - logging/setLevel is accepted, and an unknown resource URI errors cleanly
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _read_response(proc: subprocess.Popen, timeout: float = 40.0) -> dict | None:
    """Read newline-delimited JSON-RPC from the server until a response with an
    `id` arrives. Notifications / log lines (no id, or no result/error) are
    skipped — the server may emit them via the logging capability."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return None  # server exited
            time.sleep(0.02)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # stray non-JSON line
        if "id" in msg and ("result" in msg or "error" in msg):
            return msg
    return None


def test_spawned_mcp_server_initialize_list_and_read(tmp_path: Path):
    # Isolated state so the spawned server never reads the real ~/.trinity.
    home = tmp_path / "trinity"
    (home / "memories").mkdir(parents=True)
    # Seed the TOP-LEVEL core.md (where core_path() resolves) so the read
    # returns real content — the decoy memories/core.md is intentionally absent,
    # which is exactly the v1.7.136 regression: pointing there returns the stub.
    (home / "core.md").write_text("# Core\nthe-real-identity-paragraph\n", encoding="utf-8")
    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": [{
            "id": "b00", "label": "demo", "top_terms": ["a"],
            "representatives": ["hello"], "size": 3,
            "centroid": [0.01 * i for i in range(768)],      # must be stripped
            "prompt_ids": [f"p{i}" for i in range(50)],        # must be stripped
        }]}),
        encoding="utf-8",
    )
    # lens.md backs the get_persona tool (called at session start by every
    # harness). Seed a unique marker so the wire-level get_persona assertion
    # below proves the real file content round-trips over stdio.
    (home / "memories" / "lens.md").write_text(
        "# Lens\nUNIQUE-LENS-MARKER tension: ship-speed vs. verified-correctness\n",
        encoding="utf-8",
    )
    # Seed a scoreboard pick carrying a WEB-ERA capture slug (chatgpt/gemini) so
    # the resource read below proves the slug canonicalization happens OVER THE
    # WIRE: the raw file keeps web-era slugs until the next consolidate, but the
    # RESOURCE must fold them to dispatchable CLI slugs (codex/antigravity) to
    # match the get_picks TOOL — else an agent reads an un-dispatchable primary
    # (v1.7.166). vocabulary.md is intentionally LEFT UNSEEDED so the cold-start
    # stub path is exercised over the wire too.
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps({"architecture_decision": {
            "routing_rule": {"primary": "chatgpt", "challenger": "gemini"}}}),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    # Make the package importable the same way the suite runs.
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
        # initialize
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}}})
        init = _read_response(proc)
        assert init is not None, (
            "spawned MCP server never answered initialize — it failed to start "
            "or to speak stdio. stderr:\n" + (proc.stderr.read() if proc.stderr else "")
        )
        assert "result" in init, f"initialize errored: {init}"
        # serverInfo.version is Trinity's APP_VERSION AT THE WIRE — not the mcp
        # LIBRARY's version. The in-process test_mcp_server_reports_trinity_version
        # checks the server OBJECT; this proves the handshake actually carries it
        # (the bug — serverInfo.version regressing to the mcp lib version — was
        # FOUND over real stdio, so the regression guard belongs over real stdio).
        from trinity_local.telemetry import APP_VERSION
        server_info = init["result"].get("serverInfo", {})
        assert server_info.get("name") == "trinity-local", (
            f"serverInfo.name over the wire was {server_info.get('name')!r}, "
            "expected 'trinity-local'"
        )
        assert server_info.get("version") == APP_VERSION, (
            f"serverInfo.version over stdio was {server_info.get('version')!r}, "
            f"expected APP_VERSION {APP_VERSION!r} — the handshake regressed to the "
            "mcp lib version (the in-process guard wouldn't catch a wire-only break)"
        )
        caps = init["result"]["capabilities"]
        assert "tools" in caps and "resources" in caps and "prompts" in caps
        # `logging` is the #264 capability — advertised ONLY because the
        # set_logging_level handler is registered. A tools/resources/prompts
        # check wouldn't notice it silently dropping (a harness that filters log
        # levels would then break), so pin it explicitly.
        assert "logging" in caps, f"logging capability not advertised: {sorted(caps)}"

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # tools/list — the canonical 8
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        r = _read_response(proc)
        names = sorted(t["name"] for t in r["result"]["tools"])
        assert names == sorted([
            "ask", "get_council_status", "get_persona", "get_picks",
            "import_provider_memory", "lens_generators", "run_council", "run_eval",
        ]), f"tool surface drifted over the wire: {names}"

        # resources/list — the 6 canonical
        send({"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
        r = _read_response(proc)
        assert len(r["result"]["resources"]) == 6

        # prompts/list — council / ask / lens
        send({"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}})
        r = _read_response(proc)
        assert len(r["result"]["prompts"]) == 3

        # prompts/get — the ACTUAL slash-command invocation path (Claude Code
        # calls this when a user types `/council ship friday?`). prompts/list +
        # the in-process handle_get_prompt test both pass without this round-
        # tripping through the wire's GetPromptResult serialization, so a shape
        # regression there would break the slash menu while every other test
        # stays green. Assert the arg is rendered into the returned message.
        send({"jsonrpc": "2.0", "id": 14, "method": "prompts/get",
              "params": {"name": "council", "arguments": {"task": "ship friday?"}}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"prompts/get errored over the wire: {r}"
        msg_text = r["result"]["messages"][0]["content"]["text"]
        assert "ship friday?" in msg_text, "prompts/get didn't inject the task arg over the wire"
        assert "run_council" in msg_text, "the council prompt body didn't round-trip over the wire"

        # resources/read core.md → real content, NOT the cold-start stub (v1.7.136)
        send({"jsonrpc": "2.0", "id": 5, "method": "resources/read",
              "params": {"uri": "trinity://memories/core.md"}})
        r = _read_response(proc)
        core_txt = r["result"]["contents"][0]["text"]
        assert "the-real-identity-paragraph" in core_txt, (
            "core.md resource didn't deliver the top-level file over the wire — "
            "the v1.7.136 path regression is back."
        )
        assert "_(empty" not in core_txt

        # resources/read topics.json → projected, no vectors on the wire (v1.7.137)
        send({"jsonrpc": "2.0", "id": 6, "method": "resources/read",
              "params": {"uri": "trinity://memories/topics.json"}})
        r = _read_response(proc)
        topics_txt = r["result"]["contents"][0]["text"]
        assert '"centroid"' not in topics_txt and '"prompt_ids"' not in topics_txt, (
            "topics.json resource shipped raw embedding vectors over the wire — "
            "the v1.7.137 projection regressed."
        )
        # the meaningful fields still round-trip
        basin = json.loads(topics_txt)["basins"][0]
        assert basin["label"] == "demo" and basin["representatives"] == ["hello"]

        # tools/call — the path harnesses exercise most. List/read above prove
        # the server talks; this proves a real TOOL CALL round-trips over the
        # wire. ask(mode="route") is the cheap no-dispatch routing decision
        # (no model call), so it returns deterministically even in an empty
        # isolated home (heuristic fallback — no cortex consolidation here).
        send({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "ask",
            "arguments": {
                "query": "refactor this Python function to remove duplication",
                "mode": "route",
                "available_providers": ["claude", "antigravity", "codex"],
            }}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"tools/call ask errored: {r}"
        assert not r["result"].get("isError"), f"tool reported an error: {r['result']}"
        payload = json.loads(r["result"]["content"][0]["text"])
        # The route decision shape the spec promises — proves the tool ran and
        # serialized a real result, not just that the transport is alive.
        assert payload["primary"] in ("claude", "antigravity", "codex")
        assert payload["mode"] in ("single", "council")
        assert payload["confidence"] in ("high", "medium", "low")

        # prompts/get — list above only proves the prompts ADVERTISE; harnesses
        # render them as slash-menu entries via get. A get that returns empty /
        # malformed messages breaks the slash menu in every harness while
        # prompts/list still passes. Pin a real expansion over the wire: the
        # `task` argument must thread into the rendered user message.
        send({"jsonrpc": "2.0", "id": 8, "method": "prompts/get", "params": {
            "name": "council",
            "arguments": {"task": "UNIQUE-COUNCIL-TASK ship the beta now?"}}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"prompts/get errored: {r}"
        msgs = r["result"]["messages"]
        assert msgs, "prompts/get returned no messages — slash menu would be empty"
        prompt_text = msgs[0]["content"]["text"]
        assert "UNIQUE-COUNCIL-TASK" in prompt_text, (
            "prompts/get didn't thread the `task` argument into the rendered "
            f"message over the wire: {prompt_text[:120]!r}"
        )

        # tools/call get_persona — the harness calls this at session start to
        # tailor responses to the lens. Prove the real lens.md content (not a
        # stub) round-trips over the wire as a serialized tool result.
        send({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
            "name": "get_persona", "arguments": {}}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"get_persona errored: {r}"
        assert not r["result"].get("isError"), f"get_persona reported error: {r['result']}"
        persona = json.loads(r["result"]["content"][0]["text"])
        assert persona.get("available") is True, "get_persona: lens reported unavailable"
        assert "UNIQUE-LENS-MARKER" in persona.get("text", ""), (
            "get_persona didn't deliver the seeded lens.md content over the wire"
        )

        # resources/read an UNSEEDED resource → the cold-start STUB, not an error.
        # The cold-install agent UX depends on a missing resource returning
        # actionable markdown ("run trinity-local dream") rather than a dead 404 —
        # and that only matters over the wire, where the harness renders it. The
        # populated reads above never exercise this branch.
        send({"jsonrpc": "2.0", "id": 10, "method": "resources/read",
              "params": {"uri": "trinity://memories/vocabulary.md"}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"cold-start resource read errored: {r}"
        stub = r["result"]["contents"][0]["text"]
        assert "does not exist" in stub and "trinity-local dream" in stub, (
            "an unseeded resource didn't return the actionable cold-start stub "
            f"over the wire (cold-install UX): {stub[:120]!r}"
        )

        # resources/read picks.json → the web-era slug folded to the dispatchable
        # CLI slug, so an agent reading the scoreboard resource sees the same
        # provider the get_picks TOOL would return (resource/tool consistency).
        send({"jsonrpc": "2.0", "id": 11, "method": "resources/read",
              "params": {"uri": "trinity://scoreboard/picks.json"}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"picks.json resource read errored: {r}"
        picks_txt = r["result"]["contents"][0]["text"]
        assert '"chatgpt"' not in picks_txt and '"gemini"' not in picks_txt, (
            "the scoreboard resource shipped a web-era slug an agent can't "
            f"dispatch to (the v1.7.166 canonicalization regressed): {picks_txt[:160]!r}"
        )
        assert '"codex"' in picks_txt and '"antigravity"' in picks_txt

        # logging/setLevel → accepted, proving the #264 logging capability is LIVE
        # (handler wired), not merely advertised. A harness that sets a level must
        # get a clean result, not an error.
        send({"jsonrpc": "2.0", "id": 12, "method": "logging/setLevel",
              "params": {"level": "debug"}})
        r = _read_response(proc)
        assert r is not None and "result" in r, f"logging/setLevel errored over the wire: {r}"

        # resources/read an unknown URI → a clean JSON-RPC error, not a hang or a
        # crash that drops the whole connection for every later call.
        send({"jsonrpc": "2.0", "id": 13, "method": "resources/read",
              "params": {"uri": "trinity://bogus/nope.md"}})
        r = _read_response(proc)
        assert r is not None and "error" in r, (
            f"an unknown resource uri should error cleanly over the wire: {r}"
        )
    finally:
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_mcp_wire_survives_malformed_tool_calls(tmp_path: Path):
    """The MCP server is a long-lived stdio CHILD of the harness; a real client
    WILL send calls Trinity doesn't expect — an unknown tool name, a tool call
    missing a required argument, a malformed argument type, an unknown method.
    The integration contract is twofold: each returns a CLEAN error (a JSON-RPC
    error OR a tool result flagged ``isError``), AND the server stays alive. An
    unhandled exception that escapes a tool handler and crashes the stdio loop
    takes down the harness's ENTIRE Trinity tool menu for the rest of the
    session, silently. The happy-path wire test only sends well-formed calls, and
    #286 pinned the error path for RESOURCES — this pins it for ``tools/call`` +
    unknown methods + post-error SURVIVAL. Verified robust 2026-06-06."""
    home = tmp_path / "trinity"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "lens.md").write_text("# Lens\nMARKER tension\n", encoding="utf-8")
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

    def is_clean_error(r: dict | None) -> bool:
        # A JSON-RPC error OR an isError tool result = handled cleanly.
        # None (timeout / the server exited) is NOT — that's a hang or a crash.
        if not r:
            return False
        if "error" in r:
            return True
        result = r.get("result")
        return isinstance(result, dict) and bool(result.get("isError"))

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}}})
        init = _read_response(proc)
        assert init is not None and "result" in init, (
            "spawned MCP server never answered initialize. stderr:\n"
            + (proc.stderr.read() if proc.stderr else "")
        )
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # 1) Unknown tool name.
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "no_such_tool_xyz", "arguments": {}}})
        assert is_clean_error(_read_response(proc)), "unknown tool didn't error cleanly"

        # 2) Required argument missing (`ask` needs `query`).
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "ask", "arguments": {}}})
        assert is_clean_error(_read_response(proc)), "missing-required-arg didn't error cleanly"

        # 3) Malformed `arguments` (a string where an object is required).
        send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "get_picks", "arguments": "not-an-object"}})
        assert is_clean_error(_read_response(proc)), "malformed arguments didn't error cleanly"

        # 4) Unknown method.
        send({"jsonrpc": "2.0", "id": 5, "method": "totally/unknown", "params": {}})
        assert is_clean_error(_read_response(proc)), "unknown method didn't error cleanly"

        # THE load-bearing assertion: a VALID call after the error storm must
        # still succeed — the stdio loop survived. A crash here is invisible to
        # the happy-path test but lethal to the live harness integration.
        send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "get_persona", "arguments": {}}})
        r = _read_response(proc)
        assert r is not None and "result" in r and not r["result"].get("isError"), (
            "the MCP server did not SURVIVE the malformed calls — a valid "
            f"get_persona after the error storm failed: {r}. A crashed stdio loop "
            "takes down the harness's entire Trinity tool menu for the session."
        )
    finally:
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
