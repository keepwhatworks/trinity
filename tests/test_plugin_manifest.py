"""Guard the Claude Code plugin artifacts (v1.7.306).

The plugin under `plugins/trinity-local/` + the repo-root marketplace at
`.claude-plugin/marketplace.json` are what `/plugin marketplace add
keepwhatworks/trinity` reads. They're plain JSON/markdown the test suite never
imported, so a rename/typo could silently break the one-command install. This pins
the load-bearing shape: manifests parse, the MCP server is declared via the bundled
launcher, the three verb commands exist and point at the right MCP tools, and the
launcher is executable. `claude plugin validate --strict` covers the schema; this
covers Trinity-specific wiring.
"""
from __future__ import annotations

import json
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "trinity-local"
MARKET = REPO / ".claude-plugin" / "marketplace.json"


def _json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_marketplace_lists_the_plugin():
    mk = _json(MARKET)
    assert mk["name"] == "trinity", "marketplace name drift breaks /plugin install <name>@trinity"
    names = {p["name"]: p for p in mk["plugins"]}
    assert "trinity-local" in names
    assert names["trinity-local"]["source"] == "./plugins/trinity-local"


def test_plugin_manifest_declares_commands_and_mcp_server():
    pj = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert pj["name"] == "trinity-local"
    assert pj["commands"] == ["./commands/"]
    assert pj["mcpServers"] == "./.mcp.json"
    # version must agree with the marketplace entry (a user sees one of them; drift
    # = a confusing "which version did I install" mismatch).
    mk = _json(MARKET)
    market_ver = next(p["version"] for p in mk["plugins"] if p["name"] == "trinity-local")
    assert pj["version"] == market_ver, (
        f"plugin.json version {pj['version']} != marketplace {market_ver} — bump both together."
    )


def test_mcp_server_points_at_the_bundled_launcher():
    mcp = _json(PLUGIN / ".mcp.json")
    server = mcp["mcpServers"]["trinity-local"]
    assert server["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/trinity-mcp", (
        "the MCP server must launch via the bundled bin/trinity-mcp so install is "
        "one-command (it resolves trinity-local / python -m / uvx itself)."
    )


def test_launcher_exists_and_is_executable():
    launcher = PLUGIN / "bin" / "trinity-mcp"
    assert launcher.exists(), "bin/trinity-mcp missing — the .mcp.json command would 404"
    assert os.access(launcher, os.X_OK), "bin/trinity-mcp not executable (chmod +x)"
    body = launcher.read_text(encoding="utf-8")
    # The resolution tiers + the honest failure must be present. Tier 1: an existing
    # console-script install. Tier 2: the BUNDLED engine (vendored at ../engine by
    # scripts/bundle_engine.sh) run via a python that has the deps — bootstrapping a
    # private ~/.trinity/venv with numpy/mcp/Pillow on first boot when needed. This
    # is what makes "install the plugin → working Trinity" true with no curl.
    assert "trinity-local --mcp" in body
    assert "-m trinity_local --mcp" in body  # the bundled-engine tier (venv or system python)
    assert "/engine" in body, "must run the engine VENDORED in the plugin (scripts/bundle_engine.sh)"
    assert "venv" in body and "pip install" in body, "must bootstrap deps on first boot"
    assert "exit 1" in body  # fails loudly with an install hint, never hangs silently
    # The stuck-user hint must NOT point at a dead PyPI page — there is no wheel
    # yet, so `pip install trinity-local` / `uvx --from trinity-local` 404 (same
    # invariant as test_install_sh_and_update's pip-ban). A user who reaches this
    # branch is ALREADY stuck; sending them to a 404 is the worst moment to lie
    # (verified e2e 2026-06-09: the old pip lead-line 404'd). The curl installer
    # is the only path that resolves. Re-add the uvx tier + a pip hint — and flip
    # these asserts back — the day a wheel ships to PyPI. Checked against the
    # EXECUTABLE lines only (comments may name the banned commands to explain why).
    code_lines = "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "pip install trinity-local" not in code_lines
    assert "uvx --from trinity-local" not in code_lines
    assert "scripts/install.sh | bash" in code_lines, "must offer the working curl installer"


def test_three_verb_commands_target_the_right_mcp_tools():
    expected = {
        "council": "mcp__trinity-local__run_council",
        "ask": "mcp__trinity-local__ask",
        "lens": "mcp__trinity-local__get_persona",
    }
    for verb, tool in expected.items():
        md = (PLUGIN / "commands" / f"{verb}.md").read_text(encoding="utf-8")
        assert md.startswith("---"), f"{verb}.md missing frontmatter"
        assert "description:" in md, f"{verb}.md frontmatter needs a description"
        assert tool in md, f"{verb}.md must invoke {tool}"


def test_launcher_is_valid_bash():
    """`bin/trinity-mcp` is the MCP server command — a bash syntax error there means
    every `/plugin install` produces a server that can't start. Static content checks
    (above) don't catch a broken `if`/quote. `bash -n` parses without executing
    (deterministic, no env fragility). The full launcher→server→tools/prompts
    handshake was verified end-to-end manually (v1.7.306, quota-safe MCP init +
    tools/list + prompts/list) and the server-start path itself is guarded by
    tests/test_mcp_stdio_server.py."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash:  # pragma: no cover - bash present on every supported platform
        import pytest
        pytest.skip("bash not available to syntax-check the launcher")
    launcher = PLUGIN / "bin" / "trinity-mcp"
    r = subprocess.run([bash, "-n", str(launcher)], capture_output=True, text=True)
    assert r.returncode == 0, f"bin/trinity-mcp has a bash syntax error:\n{r.stderr}"


def test_plugin_bundles_trinity_verify_agent_without_drift():
    """The Trinity plugin ships the cross-provider verifier as an auto-discovered
    subagent (plugins/trinity-local/agents/trinity-verify.md), so `/plugin install`
    provides @trinity-verify alongside the MCP connector. The bundled copy is a STATIC
    file, so guard it against drift from the renderer (`install-agent` regenerates it)."""
    from trinity_local.agent_emit import VERIFIER_NAME, render_claude_subagent

    bundled = PLUGIN / "agents" / f"{VERIFIER_NAME}.md"
    assert bundled.exists(), "plugin must bundle agents/trinity-verify.md (auto-discovered)"
    assert bundled.read_text(encoding="utf-8") == render_claude_subagent(), (
        "plugin agents/trinity-verify.md drifted from the renderer — regenerate with "
        "`trinity-local install-agent --print` or re-run the bundling step."
    )


def test_no_review_gate_hook_shipped():
    """Founder decision: skip the review-gate. The plugin must NOT ship a Stop hook
    (codex-plugin-cc's gate can drain quota in a Claude/Codex loop)."""
    pj = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert "hooks" not in pj, "plugin must not declare hooks — no review-gate by design"
    assert not (PLUGIN / "hooks").exists(), "no hooks/ dir — review-gate intentionally skipped"


def test_launcher_actually_boots_a_working_mcp_server(tmp_path):
    """The store-install critical path: `bin/trinity-mcp` is the command
    `${CLAUDE_PLUGIN_ROOT}/bin/trinity-mcp` that Claude Code execs when the plugin
    is installed. The other plugin tests check its bash SYNTAX and the manifest
    WIRING, but nothing proved the launcher's resolve-and-exec chain actually
    produces a server that speaks MCP — a broken chain means every store install
    fails with "MCP server failed to start". This boots it over real stdio (the
    way a harness does), against an isolated empty home (TRINITY_AUTOSCAN_DISABLED
    so no scan, no model calls, no quota), and asserts the canonical 8-tool
    surface comes back. Verified manually 2026-06-02; codified here so a launcher
    or entry-point regression can't ship green."""
    import json as _json
    import shutil
    import subprocess
    import sys
    import time

    if sys.platform == "win32":
        import pytest
        pytest.skip("bin/trinity-mcp is a bash launcher; native Windows uses WSL2/Git-Bash")
    if shutil.which("bash") is None:
        import pytest
        pytest.skip("bash unavailable")

    launcher = PLUGIN / "bin" / "trinity-mcp"
    home = tmp_path / "trinity"
    home.mkdir()

    env = dict(os.environ)
    # Make the launcher's resolver land on THIS interpreter (which can import the
    # package): its dir holds the `trinity-local` console script (tier 1) and a
    # `python3` (tier 2); PYTHONPATH=src covers an uninstalled source checkout.
    bindir = str(pathlib.Path(sys.executable).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN)

    proc = subprocess.Popen(
        [str(launcher)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
    )

    def send(obj):
        proc.stdin.write(_json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read_id(target, timeout=40.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    return None
                continue
            line = line.strip()
            if not line:
                continue
            try:
                msg = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if msg.get("id") == target and ("result" in msg or "error" in msg):
                return msg
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "plugin-boot-test", "version": "0"}}})
        init = read_id(1)
        assert init is not None and "result" in init, (
            "bin/trinity-mcp did not boot a server that answered initialize — the "
            "resolve-and-exec chain is broken, so every store install would fail. "
            "stderr:\n" + (proc.stderr.read() if proc.stderr else "")
        )
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = read_id(2)
        assert tools is not None and "result" in tools, f"tools/list failed: {tools}"
        names = sorted(t["name"] for t in tools["result"]["tools"])
        assert names == sorted([
            "ask", "get_council_status", "get_persona", "get_picks",
            "import_provider_memory", "lens_generators", "run_council", "run_eval",
        ]), f"launcher booted, but the tool surface is wrong: {names}"
    finally:
        try:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
