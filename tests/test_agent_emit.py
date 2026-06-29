"""The cross-provider verifier subagent emitter (`agent-emit`).

These guard the two ways this feature silently breaks: a malformed definition that
won't LOAD in the target tool (the YAML-colon bug, found by parsing not eyeballing),
and a safety slip (the checker granted Edit/Write, or files written into the user's
.claude/.codex without opt-in). Plus the load-bearing behavior: the body must mandate
an actual cross-provider Trinity call — a verifier that reasons alone is the same lab
as the maker and defeats the entire point.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trinity_local import agent_emit


def test_install_agent_verb_is_registered_and_dispatches():
    """The `install-agent` subcommand (sibling of install-mcp) registers with a
    handler and parses its flags — also gives the CLI-coverage guard the verb string."""
    import argparse

    from trinity_local.commands.agent_emit import handle_agent_emit, register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["install-agent", "--print"])
    assert args.handler is handle_agent_emit
    assert args.print_only is True


def test_trinity_verify_excludes_its_own_host_lab():
    """Cross-lab guarantee (dogfood council_a3ff82e44c1256e1): the Claude shim must
    route its second opinion AWAY from claude, the Codex shim AWAY from codex — else
    a 'different-lab' checker isn't different-lab at all."""
    claude_md = agent_emit.render_claude_subagent()
    codex_toml = agent_emit.render_codex_agent()
    assert "EXCLUDE `claude`" in claude_md and "available_providers" in claude_md
    assert "EXCLUDE `codex`" in codex_toml
    # Each routes to the OTHER two labs.
    assert "codex or antigravity" in claude_md
    assert "claude or antigravity" in codex_toml


def test_trinity_verify_mandates_independent_evidence():
    """The other dogfood finding: a verifier reasoning over the handed artifact is a
    biased relay. The body must mandate getting primary evidence + skepticism."""
    for text in (agent_emit.render_claude_subagent(), agent_emit.render_codex_agent()):
        assert "do not trust what you were handed" in text.lower()
        assert "git diff" in text


def test_codex_toml_parses_with_documented_keys():
    tomllib = pytest.importorskip("tomllib")  # stdlib 3.11+; CI runs 3.12
    data = tomllib.loads(agent_emit.render_codex_agent())
    assert data["name"] == "trinity-verify"
    assert data["sandbox_mode"] == "read-only"  # checker can't make
    assert "description" in data
    assert "CROSS-PROVIDER" in data["developer_instructions"]
    assert "mcp__trinity-local__ask" in data["developer_instructions"]


def test_claude_frontmatter_description_is_quoted():
    """REGRESSION (2026-06-08): the description contains 'any loop: it gets…'. An
    UNQUOTED YAML scalar with a colon-space is parsed as a nested mapping and the
    whole frontmatter fails to load. The description line must be double-quoted."""
    md = agent_emit.render_claude_subagent()
    fm_lines = md.split("---", 2)[1].strip().splitlines()
    desc_line = next(l for l in fm_lines if l.startswith("description:"))
    value = desc_line[len("description:"):].strip()
    assert value.startswith('"') and value.endswith('"'), f"description must be quoted: {value!r}"
    assert "loop:" in value, "the colon-bearing phrase is the whole reason it must be quoted"


def test_claude_frontmatter_is_valid_yaml_if_parser_available():
    yaml = pytest.importorskip("yaml")
    fm = yaml.safe_load(agent_emit.render_claude_subagent().split("---", 2)[1])
    assert fm["name"] == "trinity-verify"
    assert fm["model"] == "inherit"
    assert "loop:" in fm["description"]  # colon survived the round-trip


def test_checker_cannot_make_no_edit_write():
    md = agent_emit.render_claude_subagent()
    tools_line = next(l for l in md.splitlines() if l.startswith("tools:"))
    assert "Edit" not in tools_line and "Write" not in tools_line, "a verifier must not be able to make"
    assert "mcp__trinity-local__ask" in tools_line
    # Codex enforces the same via sandbox_mode read-only (asserted above).


def test_body_mandates_a_cross_provider_call():
    # The body is shared verbatim; both wrappers must carry it. A verifier that
    # reasons alone is the same lab as the maker — the cross-provider call IS the point.
    for text in (agent_emit.render_claude_subagent(), agent_emit.render_codex_agent()):
        assert "mcp__trinity-local__ask" in text          # cheap default
        assert "mcp__trinity-local__run_council" in text   # escalation
        assert "NEVER rubber-stamp" in text


def test_body_carries_the_hardening_checklist():
    """The Fable-5 hardening checklist must reach
    BOTH rendered shims — it's the substance of what the verifier interrogates. Drop the
    WHAT-TO-INTERROGATE block from _verifier_body and this goes red. Anchor on the load-
    bearing phrases (one per checklist axis) so a partial deletion is caught, not just a
    whole-block removal."""
    for text in (agent_emit.render_claude_subagent(), agent_emit.render_codex_agent()):
        assert "WHAT TO INTERROGATE" in text
        # green-but-degraded framing (the spine of the whole checklist)
        assert "secretly degraded" in text
        # one anchor per axis
        assert "MEASURED" in text                       # measured-not-assumed
        assert "ABSTAIN over wrong output" in text       # abstain-over-wrong-output
        assert "isinstance-checked before" in text       # shape-guarded reads
        assert "goes RED if the" in text                 # mutation-proven wire-ins
        assert "fail safe" in text                       # worst-case-cheapest
        assert "name the CAUSE" in text                  # honest degradation
        assert "EXECUTES are runtime surfaces" in text   # docs-as-code
        assert "LOOP finished" in text                   # finish-the-loop
        # loop-craft axes (2026-06-24) — the verification itself / the work's value can be
        # the degraded surface. One anchor per added axis so a partial deletion is caught.
        assert "LIVE SHIPPED SOURCE" in text             # verify against the shipped code, not a dead twin / stale bundle
        assert "passes vacuously" in text                # the dead-twin / stale-artifact failure named
        assert "pins a source substring" in text         # guard asserts the invariant, not a string
        assert "MARGINAL VALUE" in text                  # a no-op re-confirmation is itself green-but-degraded
        # the verifier must DOWNGRADE, not pass, when a check is skipped
        assert "treat a claim that skips an applicable check as UNVERIFIED" in text.replace("\n", " ")


def test_default_writes_to_trinity_home_not_dotclaude(tmp_path):
    # Default (install=False): canonical copies under ~/.trinity/agents/, and NOTHING
    # placed into .claude/.codex without opt-in (the lens-skill safety discipline).
    project = tmp_path / "proj"
    project.mkdir()
    res = agent_emit.write_verifier_agents(home=tmp_path / "home", install=False,
                                           project_root=project)
    assert res["ok"] and res["installed"] == []
    assert Path(res["claude_source"]).exists() and Path(res["codex_source"]).exists()
    assert (tmp_path / "home" / "agents" / "trinity-verify.md").exists()
    assert not (project / ".claude").exists(), "must NOT touch .claude without --install"
    assert not (project / ".codex").exists()
    assert res["install_hints"] and len(res["install_hints"]) == 2


def test_install_opt_in_places_in_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    res = agent_emit.write_verifier_agents(home=tmp_path / "home", install=True,
                                           project_root=project)
    claude_dst = project / ".claude" / "agents" / "trinity-verify.md"
    codex_dst = project / ".codex" / "agents" / "trinity-verify.toml"
    assert claude_dst.exists() and codex_dst.exists()
    assert str(claude_dst) in res["installed"] and str(codex_dst) in res["installed"]
    # The installed copy is byte-identical to the canonical source.
    assert claude_dst.read_text() == Path(res["claude_source"]).read_text()
