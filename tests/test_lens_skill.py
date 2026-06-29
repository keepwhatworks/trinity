"""Guard the SKILL.md emitter (`trinity-local lens-skill`) — the SkillOpt-shaped
"lens = ambient" move.

The lens is Trinity's trainable document (the SkillOpt paradigm: train the doc,
not the model). This emits it in the skills ecosystem's format so any skill-aware
harness loads the user's taste ambiently. Two things MUST hold:
  1. The output is a VALID, loadable SKILL.md (frontmatter name+description + the
     prescriptive body) composed from the built lens — deterministic, no LLM.
  2. SAFETY: it writes under ~/.trinity/, NEVER silently into ~/.claude/skills/.
     Making the skill ambient is a deliberate user step (the printed symlink),
     not a surprise mutation of the user's agent config.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local.me.skill import render_lens_skill, skill_path, write_lens_skill


def _seed_lens(home: Path) -> None:
    (home / "me").mkdir(parents=True, exist_ok=True)
    (home / "core.md").write_text(
        "You ship the thing, not the write-up. You verify over trust.", encoding="utf-8"
    )
    (home / "me" / "taste_signature.json").write_text(
        json.dumps({"adjectives": ["decisive", "concrete", "terse"], "n": 172}),
        encoding="utf-8",
    )
    (home / "me" / "lenses.json").write_text(
        json.dumps({"lenses": [
            {"pole_a": "concrete", "pole_b": "abstract", "failure_a": "vague", "failure_b": "brittle"},
            {"pole_a": "action", "pole_b": "description", "failure_a": "reckless", "failure_b": "inert"},
        ]}),
        encoding="utf-8",
    )


def test_render_produces_valid_skill_md(tmp_path: Path) -> None:
    home = tmp_path / "trinity"
    _seed_lens(home)
    text = render_lens_skill(home)
    assert text is not None
    # YAML frontmatter the skill loaders parse: leading ---, name, description.
    assert text.startswith("---\nname: your-taste\n"), "missing/!malformed frontmatter"
    front = text.split("---", 2)[1]
    assert "description:" in front
    # the relevance signal carries the taste signature (so the agent knows when to load)
    assert "decisive · concrete · terse" in front
    # the prescriptive body + grounded tensions + the lens portrait + provenance
    assert "## How to answer them" in text
    assert "concrete ↔ abstract" in text and "vague" in text
    assert "You ship the thing" in text
    assert "172 distilled decisions" in text
    # The FIX (dep-free, runs everywhere incl. CI): the description is emitted as a
    # YAML double-quoted scalar, so a colon/quote/leading-special in it can't break
    # the frontmatter. A plain unquoted scalar is the silent-load-failure path.
    assert 'description: "' in front, "description must be double-quoted for YAML safety"


def test_frontmatter_is_parseable_yaml(tmp_path: Path) -> None:
    """The frontmatter MUST parse as a YAML mapping with name + description — a
    SKILL.md whose frontmatter doesn't parse silently never loads (the silent-
    failure shape). Parse it the way a skill loader would. PyYAML is NOT a declared
    dep (the product doesn't need it — the emitter escapes manually), so this skips
    where it's absent; the dep-free quoting assertion above guards the fix on CI."""
    yaml = pytest.importorskip("yaml")

    home = tmp_path / "trinity"
    _seed_lens(home)
    text = render_lens_skill(home)
    assert text is not None
    front = text.split("---", 2)[1]
    meta = yaml.safe_load(front)
    assert isinstance(meta, dict), f"frontmatter did not parse to a mapping: {meta!r}"
    assert meta.get("name") == "your-taste"
    desc = meta.get("description")
    assert isinstance(desc, str) and desc.strip().endswith("."), "description truncated/empty"
    assert len(desc) <= 1536, "description exceeds the default skill-listing cap"


def test_frontmatter_survives_yaml_hostile_signature(tmp_path: Path) -> None:
    """ADVERSARIAL: a taste-signature adjective carrying YAML-breaking characters
    (a `: ` mapping tell, a quote, a leading `#`) must NOT break the frontmatter —
    the description is double-quoted, so it still parses to the full string. An
    unquoted scalar would silently lose everything after the colon."""
    yaml = pytest.importorskip("yaml")

    home = tmp_path / "trinity"
    (home / "me").mkdir(parents=True)
    (home / "core.md").write_text("You ship the thing.", encoding="utf-8")
    (home / "me" / "taste_signature.json").write_text(
        json.dumps({"adjectives": ['blunt: no-fluff', '"quoted"', "#hashy"], "n": 9}),
        encoding="utf-8",
    )
    text = render_lens_skill(home)
    assert text is not None
    front = text.split("---", 2)[1]
    meta = yaml.safe_load(front)  # must not raise
    assert isinstance(meta, dict) and meta.get("name") == "your-taste"
    # the hostile signature survived intact inside the description (not truncated)
    assert "blunt: no-fluff" in meta["description"]
    assert '"quoted"' in meta["description"]


def test_write_lands_in_trinity_not_claude(tmp_path: Path) -> None:
    """SAFETY INVARIANT: the emitter writes under the Trinity home, and NEVER into
    ~/.claude/skills/. Making the skill ambient is the user's deliberate symlink."""
    home = tmp_path / "trinity"
    _seed_lens(home)
    res = write_lens_skill(home)
    assert res["ok"] is True
    out = Path(res["path"])
    assert out.exists()
    # under the given Trinity home...
    assert str(home) in str(out)
    assert out == skill_path(home)
    # ...and NOT under any .claude/skills path (the silent-mutation guard).
    assert ".claude/skills" not in str(out)
    assert res.get("ambient") is False
    # the deliberate install step is surfaced, not performed.
    assert "ln -s" in res["install_hint"] and ".claude/skills" in res["install_hint"]


def test_cold_home_refuses_to_emit_empty_skill(tmp_path: Path) -> None:
    """No lens (no core.md) → return None / ok:False, NOT an empty misleading
    SKILL.md. (Green-gate discipline: don't emit a skill that attests a taste
    that isn't there.)"""
    home = tmp_path / "trinity"
    home.mkdir(parents=True)
    assert render_lens_skill(home) is None
    res = write_lens_skill(home)
    assert res["ok"] is False
    assert not skill_path(home).exists()


def test_cli_verb_registered() -> None:
    import argparse

    from trinity_local.commands import me as me_cmd

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    me_cmd.register(sub)
    args = parser.parse_args(["lens-skill"])
    assert getattr(args, "handler", None) is me_cmd.handle_lens_skill


def test_lens_skill_enabled_reads_flag(monkeypatch) -> None:
    """The auto-emit gate (mirrors generators_enabled): default OFF, on for the
    truthy spellings, off for falsy."""
    from trinity_local.me.skill import lens_skill_enabled

    monkeypatch.delenv("TRINITY_LENS_SKILL", raising=False)
    assert lens_skill_enabled() is False
    for v in ("1", "true", "YES", "on", "True"):
        monkeypatch.setenv("TRINITY_LENS_SKILL", v)
        assert lens_skill_enabled() is True, v
    for v in ("0", "", "off", "no", "  "):
        monkeypatch.setenv("TRINITY_LENS_SKILL", v)
        assert lens_skill_enabled() is False, v


def test_lens_build_auto_emits_skill_under_flag() -> None:
    """The lens-build pipeline auto-emits the SKILL.md when TRINITY_LENS_SKILL is
    set (the 'lens = ambient' self-refresh) — gated like the generators pass, and
    NOT on the preserved-existing degenerate path. Source-wiring guard: a refactor
    that drops the call silently breaks self-refresh with no error."""
    import inspect

    from trinity_local import me_builder

    src = inspect.getsource(me_builder.build_me_via_lens_pipeline)
    assert "lens_skill_enabled" in src and "write_lens_skill" in src, (
        "build_me_via_lens_pipeline no longer auto-emits the lens-skill under the flag"
    )
    # the auto-emit must sit behind the same `not preserved_existing` guard as
    # generators (never re-skin a degenerate/preserved lens).
    assert "preserved_existing" in src


def test_lens_build_output_surfaces_lens_skill() -> None:
    """Discoverability: a successful `lens-build` must point the user at
    `lens-skill` (the ambient option) — otherwise the feature is invisible and
    nobody makes their taste ambient. Source guard on the success-output handler."""
    import inspect

    from trinity_local.commands import me as me_cmd

    src = inspect.getsource(me_cmd.handle_me_build)
    assert "lens-skill" in src and "ambient" in src.lower(), (
        "lens-build's success output no longer surfaces the lens-skill ambient option"
    )
