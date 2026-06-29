"""Per-harness paste-in snippet generator (#166, Phase A).

The popup's `harness-snippets.js` is the single source of truth for the
per-harness MCP config blocks. These guards pin: all six harnesses present,
the curl-primary install reality (module-mode python command, NOT uvx — the
founder went curl-primary 2026-05-29), the right config surface (JSON
mcpServers vs Codex's TOML mcp_servers), the target file paths, and that the
popup actually loads + invokes the module. Drift here silently breaks the
lowest-friction install path.
"""
from __future__ import annotations

from pathlib import Path

EXT = Path(__file__).resolve().parent.parent / "browser-extension"


def _snippets_js() -> str:
    return (EXT / "harness-snippets.js").read_text(encoding="utf-8")


class TestHarnessSnippetModule:
    def test_module_file_exists(self):
        assert (EXT / "harness-snippets.js").exists()

    def test_all_six_harness_labels_present(self):
        js = _snippets_js()
        for label in (
            "Claude Code", "Claude Desktop", "Codex CLI",
            "Cursor", "Antigravity", "Cline (VS Code)",
        ):
            assert label in js, f"harness {label!r} missing from snippet module"

    def test_each_harness_id_present(self):
        js = _snippets_js()
        for hid in ("claude-code", "claude-desktop", "codex", "cursor", "antigravity", "cline"):
            assert f'id: "{hid}"' in js, f"harness id {hid!r} missing"

    def test_no_uvx_anywhere(self):
        # Founder decision 2026-05-29: curl-primary, uvx dropped. The
        # snippet module must not resurrect the retired uvx invocation.
        assert "uvx" not in _snippets_js()

    def test_config_blocks_mirror_install_mcp_module_command(self):
        js = _snippets_js()
        # install-mcp writes `command = <python>, args = ["-m",
        # "trinity_local.main", "--mcp"]`. The paste-in blocks must mirror
        # that exact shape (PYTHON is the placeholder for the user's
        # interpreter), NOT a PyPI-runner shim.
        assert '"command": "PYTHON"' in js   # JSON harnesses
        assert 'command = "PYTHON"' in js    # Codex TOML
        assert '"-m", "trinity_local.main", "--mcp"' in js

    def test_python_placeholder_carries_a_visible_substitution_hint(self):
        """The picker shows `command = "PYTHON"` — a placeholder. Pasting it
        verbatim spawns a non-existent binary and the MCP server SILENTLY never
        starts (the worst onboarding failure: looks wired, does nothing). The
        module's source comment documents the substitution, but that never
        reaches the non-coder pasting the block — the RENDERED picker must
        surface it. Found 2026-06-06 dogfooding the popup setup card in real
        Chrome: the snippet showed PYTHON with zero substitution guidance. Guards
        the visible hint, its actionable command, and its conditional render."""
        js = _snippets_js()
        # A dedicated, user-visible hint element (a DOM render, not just a comment).
        assert "harness-substitute-hint" in js, "substitution hint element missing"
        # Actionable guidance: how to get the real path + why it matters.
        assert "which python3" in js, "hint must tell the user how to find their python"
        assert ("won't start" in js or "non-existent" in js), (
            "hint must warn that pasting PYTHON verbatim breaks the server"
        )
        # Shown ONLY for blocks that actually carry the placeholder.
        assert 'indexOf("PYTHON")' in js, "hint must be gated on the snippet carrying PYTHON"

    def test_curl_bootstrap_is_the_recommended_path(self):
        js = _snippets_js()
        # The recommended install is the one-line bootstrap, surfaced as the
        # module's TRINITY_BOOTSTRAP_CMD + in the picker heading.
        assert "TRINITY_BOOTSTRAP_CMD" in js
        assert "scripts/install.sh" in js
        assert "curl -fsSL" in js

    def test_json_harnesses_use_mcpservers_key(self):
        assert '"mcpServers"' in _snippets_js()

    def test_codex_uses_toml_mcp_servers_table(self):
        # Codex reads TOML, not JSON — the one harness with a different surface.
        assert "[mcp_servers.trinity-local]" in _snippets_js()

    def test_target_file_paths_present(self):
        js = _snippets_js()
        for path in (
            "~/.claude.json",
            "~/.codex/config.toml",
            "~/.cursor/mcp.json",
            "~/.gemini/settings.json",
        ):
            assert path in js, f"target path {path!r} missing"

    def test_exposes_renderer_and_data_globals(self):
        js = _snippets_js()
        assert "renderHarnessPicker" in js
        assert "TRINITY_HARNESS_SNIPPETS" in js

    def test_module_is_chrome_free(self):
        # The whole point: loads standalone (no extension context), so it
        # must not touch chrome.* — otherwise it can't be the single source
        # of truth nor be browser-tested outside the extension.
        assert "chrome." not in _snippets_js()


class TestPopupWiresThePicker:
    def test_popup_html_loads_the_module_before_popup_js(self):
        html = (EXT / "popup.html").read_text(encoding="utf-8")
        # Match the <script src> tags specifically — "popup.js" also appears
        # in the file's top comment, so a bare .index would mis-order.
        assert 'src="harness-snippets.js"' in html
        assert html.index('src="harness-snippets.js"') < html.index('src="popup.js"')

    def test_popup_js_invokes_the_picker_in_setup_card(self):
        js = (EXT / "popup.js").read_text(encoding="utf-8")
        assert "renderHarnessPicker(body)" in js
