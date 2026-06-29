"""The Codex CLI install over a POPULATED config must yield valid TOML that
preserves the user's OTHER mcp_servers — re-install is a strip-then-reappend, and
nothing parses that path today.

`_write_codex_toml_mcp_config` doesn't depend on a TOML writer: on re-install it
regex-strips any prior `[mcp_servers.trinity-local]` block (`_CODEX_MCP_BLOCK_RE`)
and the inline-table form, then appends a fresh block. That regex is the fragile
part — a strip that eats one char of a NEIGHBORING table (the next mcp_server, a
`[projects."…"]` trust table) or leaves a dangling fragment produces a config that
still has `count("[mcp_servers.trinity-local]") == 1` and the right substrings, but
no longer PARSES. And because Codex merges Trinity into the user's shared
`~/.codex/config.toml`, an unparseable result kills EVERY MCP server in that
harness, not just Trinity — a silent, blast-radius-of-the-whole-file corruption.

Coverage gap this fills. The existing Codex tests in `test_install_mcp.py`:
  • `test_codex_toml_windows_path_round_trips_through_tomllib` — DOES tomllib-parse,
    but only a FRESH write (no prior content → the strip path never runs);
  • `test_codex_toml_preserves_existing_config` — substring checks only, NO parse,
    and no neighboring mcp_server to be eaten;
  • `test_codex_toml_idempotent_no_duplicate_blocks` — `content.count(...) == 1`
    only, NO parse.
So the re-install-over-existing-config path — the common case (every `update`
re-runs install-mcp) — is never proven to yield valid TOML, and neighbor
*mcp_servers* preservation is never tested at all. This pins both.

Mutation-proven: widen `_CODEX_MCP_BLOCK_RE`'s lookahead bound so the strip runs to
end-of-file (greedy) instead of stopping at the next `[table]` — the neighbor
server + projects table get consumed → the parse loses them / the file truncates →
these reds. (Verified by hand during authoring: the robust non-greedy bound keeps
both; a greedy bound drops them.)
"""
from __future__ import annotations

import pytest

from trinity_local.commands.install import _write_codex_toml_mcp_config

ARGS = ["-m", "trinity_local.main", "--mcp"]


def _existing_with_neighbor_after_trinity() -> str:
    """A realistic populated Codex config: top-level model keys, a STALE trinity
    block, a DIFFERENT mcp_server immediately after it (the one most at risk of
    being eaten by an over-greedy strip), and a projects trust table."""
    return (
        'model = "gpt-5.5"\n'
        'model_reasoning_effort = "xhigh"\n'
        "\n"
        "[mcp_servers.trinity-local]\n"
        'command = "/old/python"\n'
        'args = ["-m", "trinity_local.main", "--mcp"]\n'
        "\n"
        "[mcp_servers.playwright]\n"
        'command = "npx"\n'
        'args = ["@playwright/mcp@latest"]\n'
        "\n"
        '[projects."/Users/me/work"]\n'
        'trust_level = "trusted"\n'
    )


def test_codex_reinstall_over_populated_config_stays_valid_toml(tmp_path):
    """Re-install (strip prior trinity block + append fresh) over a config that has
    a neighboring mcp_server must PARSE and preserve every neighbor — not just leave
    one trinity block and the right substrings."""
    tomllib = pytest.importorskip("tomllib")  # 3.11+; CI runs 3.12

    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_existing_with_neighbor_after_trinity(), encoding="utf-8")

    assert _write_codex_toml_mcp_config(target, "/new/python", ARGS) is True

    raw = target.read_text(encoding="utf-8")
    assert raw.count("[mcp_servers.trinity-local]") == 1, "duplicate trinity blocks"

    # The assertion the substring/count tests can't make: the merged file is VALID
    # TOML. A strip that ate a neighbor's header byte or left a fragment fails here.
    parsed = tomllib.loads(raw)

    servers = parsed["mcp_servers"]
    # Trinity updated to the new command (the strip removed the stale /old/python).
    assert servers["trinity-local"]["command"] == "/new/python"
    assert servers["trinity-local"]["args"] == ARGS
    # The NEIGHBOR mcp_server survives intact — the core of the blast-radius risk.
    assert "playwright" in servers, (
        "the neighboring mcp_server was lost — the strip regex consumed past the "
        "trinity block into the next [mcp_servers.*] table (every Codex MCP server "
        "would break, not just Trinity)"
    )
    assert servers["playwright"]["command"] == "npx"
    assert servers["playwright"]["args"] == ["@playwright/mcp@latest"]
    # Non-mcp neighbor tables + top-level keys survive too.
    assert parsed["projects"]["/Users/me/work"]["trust_level"] == "trusted"
    assert parsed["model"] == "gpt-5.5"
    assert parsed["model_reasoning_effort"] == "xhigh"


def test_codex_apostrophe_path_round_trips_through_tomllib(tmp_path):
    """A path containing an apostrophe (e.g. a Windows/macOS user named O'Brien)
    forces `_toml_str` off the literal-string branch (single quotes can't hold a
    `'`) onto the basic-string fallback, which must ALSO escape backslashes. The
    existing parse-tests only exercise the literal branch (Windows path, no
    apostrophe), so a regression dropping the fallback's `\\\\`-escape — or
    re-quoting it wrong — would emit invalid TOML for every apostrophe-path user
    while every test stayed green. Worse, an unparseable config.toml breaks ALL of
    Codex's MCP servers, not just Trinity (one shared file).

    Mutation: change `_toml_str`'s fallback to drop `.replace("\\\\", "\\\\\\\\")`
    (or always use a literal string) → tomllib.loads raises here → reds."""
    tomllib = pytest.importorskip("tomllib")  # 3.11+; CI runs 3.12
    target = tmp_path / ".codex" / "config.toml"

    for cmd in (r"C:\Users\O'Brien\python.exe",   # backslash + apostrophe (fallback + escape)
                "/Users/o'neil/bin/trinity-local"):  # apostrophe only (plain fallback)
        assert _write_codex_toml_mcp_config(target, cmd, ARGS) is True
        parsed = tomllib.loads(target.read_text(encoding="utf-8"))  # MUST parse
        assert parsed["mcp_servers"]["trinity-local"]["command"] == cmd, (
            f"apostrophe path didn't round-trip through TOML: {cmd!r}"
        )
        assert parsed["mcp_servers"]["trinity-local"]["args"] == ARGS


def test_codex_writer_backs_up_existing_config(tmp_path):
    """Parity with _write_json_mcp_config: config.toml is regex-stripped + rewritten
    in full, so the writer must back it up first — a strip/append mishap on an exotic
    config can't be allowed to silently destroy the user's model settings / trust
    tables / OTHER mcp servers. Pre-fix the Codex writer made no backup.

    Mutation: remove the shutil.copy2 backup in _write_codex_toml_mcp_config → no
    .bak is produced → this reds."""
    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = _existing_with_neighbor_after_trinity()
    target.write_text(original, encoding="utf-8")

    assert _write_codex_toml_mcp_config(target, "/new/python", ARGS) is True

    baks = list(target.parent.glob("config.toml.*.bak"))
    assert len(baks) == 1, f"expected exactly one .bak backup, found {baks}"
    # The backup is the UNMODIFIED original (taken before the strip+rewrite).
    assert baks[0].read_text(encoding="utf-8") == original, (
        "the backup isn't the pre-write original — backup taken after the rewrite"
    )
    # A fresh (non-existent) config gets NO spurious backup.
    fresh = tmp_path / "fresh" / "config.toml"
    assert _write_codex_toml_mcp_config(fresh, "/x/python", ARGS) is True
    assert not list(fresh.parent.glob("*.bak")), "backed up a config that didn't exist"


def test_codex_reinstall_strips_quoted_and_nested_prior_forms_and_parses(tmp_path):
    """The quoted-key + nested-.env prior forms (some toolchains emit them) must be
    stripped AND the result must parse with a neighbor preserved — the
    `test_codex_toml_strips_quoted_and_nested_prior_blocks` sibling checks the
    string is gone but never parses, and has no neighbor server."""
    tomllib = pytest.importorskip("tomllib")

    target = tmp_path / ".codex" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        'model = "gpt-5.5"\n'
        "\n"
        '[mcp_servers."trinity-local"]\n'  # quoted-key form
        "command = '/old/python'\n"
        'args = ["-m", "trinity_local.main", "--mcp"]\n'
        "\n"
        '[mcp_servers."trinity-local".env]\n'  # nested subtable, quoted parent
        'TRINITY_HOME = "/tmp/old"\n'
        "\n"
        "[mcp_servers.trinity-local.env]\n"  # nested subtable, dotted parent
        'STALE = "true"\n'
        "\n"
        "[mcp_servers.github]\n"  # neighbor that must survive
        "command = 'gh-mcp'\n"
        'args = ["serve"]\n',
        encoding="utf-8",
    )

    assert _write_codex_toml_mcp_config(target, "/new/python", ARGS) is True

    raw = target.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)  # the parse the sibling test omits

    servers = parsed["mcp_servers"]
    assert servers["trinity-local"]["command"] == "/new/python"
    # The stale trinity env subtables are gone (no STALE / old TRINITY_HOME leaked
    # into the fresh trinity server).
    assert servers["trinity-local"].get("env", {}).get("STALE") is None
    assert servers["trinity-local"].get("env", {}).get("TRINITY_HOME") != "/tmp/old"
    # Neighbor survives.
    assert servers["github"]["command"] == "gh-mcp"
    assert servers["github"]["args"] == ["serve"]
    assert parsed["model"] == "gpt-5.5"
