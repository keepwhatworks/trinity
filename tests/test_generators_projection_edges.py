"""res_027 — the projection edges must be bounded references, not free integers.

`generators.md` carries lines like *"Projects task-tensions: 2, 3, 8, 10…"*.
Those numbers are ORDINALS into the numbered tension list the model was shown,
and until 2026-08-15 nothing checked them:

  * `parse_generators` cast `task_tensions` to int and kept whatever came back,
    so a model citing item 51 of a 23-item list had the reference admitted and
    rendered as fact;
  * `render_generators_cards` labelled the positions "task-tensions" and its
    docstring called them ids, while lens.md renumbers its headings on every
    build — measured, zero of 23 positions in the surviving 2026-06-11 snapshot
    still carry the same tension text today.

Together those made an unauditable artifact: the live generators.md publishes
51 edges that cannot be resolved, and no lens.md from its own build date
survives to check them against. Third instance of amd_0176 (dead joins
masquerade as null effects) after res_024 and res_026.

Mutation-proven 2026-08-15: deleting the range filter REDs
`test_out_of_range_edges_are_dropped`; deleting the `tension_ids` branch REDs
both render guards.
"""
from __future__ import annotations

import json

from trinity_local.me import generators as gen


def _payload(task_tensions):
    return "```json\n" + json.dumps({"generators": [{
        "name": "N", "imperative": "Do, don't wait", "tension": "a over b",
        "projections": {"software": "x", "finance": "y", "materials": "z"},
        "task_tensions": task_tensions}]}) + "\n```"


def test_out_of_range_edges_are_dropped():
    """The exact live failure: citing past the end of the list the model saw."""
    parsed = gen.parse_generators(_payload([2, 3, 51]), 23)
    assert parsed is not None
    assert parsed[0]["task_tensions"] == [2, 3], "51 is not a member of a 23-item list"

    # and the lower bound, which a 0-indexed model would trip
    assert gen.parse_generators(_payload([0, 1]), 23)[0]["task_tensions"] == [1]


def test_unbounded_call_still_parses_but_keeps_everything():
    """Without n_tensions there is no list to check against, so nothing is
    dropped — the caller that HAS the list is responsible for passing it."""
    assert gen.parse_generators(_payload([2, 51]))[0]["task_tensions"] == [2, 51]


def test_render_emits_stable_ids_when_they_resolve():
    gens = [{"name": "N", "imperative": "Do, don't wait", "tension": "a over b",
             "projections": {"software": "x"}, "task_tensions": [1, 3]}]
    md = gen.render_generators_cards(gens, ["tension_aaa", "tension_bbb", "tension_ccc"])
    assert "tension_aaa, tension_ccc" in md
    assert "Projects task-tensions: tension_aaa" in md


def test_render_says_positions_out_loud_when_ids_are_absent():
    """The artifact must never again claim a reference it does not hold."""
    gens = [{"name": "N", "imperative": "Do, don't wait", "tension": "a over b",
             "projections": {"software": "x"}, "task_tensions": [1, 3]}]
    md = gen.render_generators_cards(gens)
    assert "not stable ids" in md, "positions must be labelled as positions"
    assert "*Projects task-tensions: 1, 3*" not in md, "the old lying label is gone"


def test_current_tension_ids_refuses_a_partial_map(tmp_path, monkeypatch):
    """A half-resolved map is worse than none — it would silently mis-key the
    edges it did resolve. Empty list is the honest answer."""
    home = tmp_path / "trinity"
    (home / "memories").mkdir(parents=True)
    (home / "me").mkdir(parents=True)
    (home / "memories" / "lens.md").write_text(
        "### 1. alpha ↔ beta\n\n### 2. gamma ↔ delta\n", encoding="utf-8")
    (home / "me" / "lens_registry.json").write_text(json.dumps({"tensions": [
        {"tension_id": "t_alpha", "pole_a": "alpha", "pole_b": "beta"}]}), encoding="utf-8")
    monkeypatch.setenv("TRINITY_HOME", str(home))
    assert gen.current_tension_ids() == [], "one of two resolved is not a map"

    (home / "me" / "lens_registry.json").write_text(json.dumps({"tensions": [
        {"tension_id": "t_alpha", "pole_a": "alpha", "pole_b": "beta"},
        {"tension_id": "t_gamma", "pole_a": "gamma", "pole_b": "delta"}]}), encoding="utf-8")
    assert gen.current_tension_ids() == ["t_alpha", "t_gamma"]
