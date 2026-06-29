"""Guard the SOURCE-OF-TRUTH routing gate: ask's _try_cortex_route must REFUSE to
route on a basin whose chairman-winner margin is below WINNER_MARGIN_FLOOR — it falls
to kNN — and MUST route when the margin clears it.

This is the gate every picks DISPLAY surface describes: the launchpad cheat-sheet
demote (#299), the memory-viewer "Leans X · near-tie" caveat, the get_picks `routes`
flag, and the picks resource annotation all tell the user/agent that a sub-floor basin
is advisory because ASK DOESN'T ROUTE ON IT. But those describe the routing; the
routing itself (ask.py `if margin < WINNER_MARGIN_FLOOR: return None`) was unguarded.
test_lens_routing covers place_query's match/placement floors and compute_basin_routing's
tally; test_ask monkeypatches _try_cortex_route AWAY. So a regression dropping the
winner-margin gate would make the router crown a coin-flip basin's winner as a confident
route while all four display surfaces still say "advisory" — the routing and the displays
would silently DIVERGE, turning every one of those honesty fixes into a lie, with no
test red.

Drives _try_cortex_route directly. place_query / mlx_actually_loaded are imported inside
the function, so they're patched at their source modules: mlx is forced "loaded" (skip
the no-[mlx] abstain) and place_query is pinned to return our basin (so the decision
turns purely on the seeded pick's margin — no real embedder needed). Seeds picks.json
with one sub-floor basin and one confident basin and asserts the routing decision.

Mutation-proven: drop `if margin < WINNER_MARGIN_FLOOR: return None` in ask._try_cortex_
route → the sub-floor basin routes → the None assertion reds.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.ask import _try_cortex_route
from trinity_local.lens_routing import WINNER_MARGIN_FLOOR

_PROVIDERS = ["claude", "codex", "antigravity"]


def _seed(home, *, margin: float):
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": [{"id": "b00", "centroid": [1.0, 0.0, 0.0], "label": "x"}]}),
        encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True, exist_ok=True)
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps({"b00": {"winner": "codex", "count": 10, "margin": margin,
                            "n_episodes": 10, "evidence": ["c1"]}}),
        encoding="utf-8")


def _patch_routing_internals(monkeypatch, home):
    monkeypatch.setenv("TRINITY_HOME", str(home))
    # Force past the no-[mlx] abstain so the margin gate (not the embedder gate) is
    # what decides — imported inside the function, so patch the source module.
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)
    # Pin placement to b00 so the decision turns purely on the seeded margin.
    monkeypatch.setattr("trinity_local.lens_routing.place_query", lambda q, basins, embed: "b00")


def test_subfloor_basin_winner_is_not_routed(tmp_path, monkeypatch):
    home = tmp_path / "trinity"
    _seed(home, margin=round(WINNER_MARGIN_FLOOR - 0.07, 3))  # 0.08 — a coin flip
    _patch_routing_internals(monkeypatch, home)

    decision = _try_cortex_route("which model for this api design question?", _PROVIDERS)
    assert decision is None, (
        "ask routed on a sub-floor (coin-flip) basin — the routing diverged from every "
        "picks display that marks it advisory; the winner-margin gate regressed"
    )


def test_confident_basin_winner_is_routed(tmp_path, monkeypatch):
    home = tmp_path / "trinity"
    _seed(home, margin=round(WINNER_MARGIN_FLOOR + 0.27, 3))  # 0.42 — decisive
    _patch_routing_internals(monkeypatch, home)

    decision = _try_cortex_route("which model for this api design question?", _PROVIDERS)
    assert decision is not None, "a confident basin (margin >> floor) failed to route"
    assert decision.routed_to == "codex", f"routed to the wrong winner: {decision.routed_to!r}"
    assert decision.trust_score == pytest.approx(WINNER_MARGIN_FLOOR + 0.27)


def test_exactly_at_floor_routes(tmp_path, monkeypatch):
    """Boundary: the gate is `< floor` → exactly AT the floor must ROUTE (the demote
    surfaces dim only strictly-below, so routing must match: >= floor is decisive)."""
    home = tmp_path / "trinity"
    _seed(home, margin=WINNER_MARGIN_FLOOR)
    _patch_routing_internals(monkeypatch, home)

    decision = _try_cortex_route("which model for this api design question?", _PROVIDERS)
    assert decision is not None and decision.routed_to == "codex", (
        "a basin exactly AT the floor must route (gate is strict `< floor`) — a "
        "boundary regression to `<=` would drop it"
    )


def test_thin_tally_below_min_count_not_routed(tmp_path, monkeypatch):
    """A basin with a confident margin but too few episodes (count < MIN_COUNT) is one
    or two lucky councils, not a learned preference — must abstain. Mutation: drop the
    `count < MIN_COUNT` gate → routes on n=1 → reds."""
    from trinity_local.lens_routing import MIN_COUNT
    home = tmp_path / "trinity"
    _seed(home, margin=0.42)  # decisive margin, so ONLY the count gate can stop it
    # overwrite the pick with a sub-MIN_COUNT count
    (home / "scoreboard" / "picks.json").write_text(
        json.dumps({"b00": {"winner": "codex", "count": MIN_COUNT - 1, "margin": 0.42,
                            "n_episodes": MIN_COUNT - 1, "evidence": ["c1"]}}),
        encoding="utf-8")
    _patch_routing_internals(monkeypatch, home)
    assert _try_cortex_route("api design?", _PROVIDERS) is None, (
        f"routed on a thin tally (count < MIN_COUNT={MIN_COUNT})"
    )


def test_unavailable_winner_not_routed(tmp_path, monkeypatch):
    """A confident basin whose chairman-winner isn't among the available providers must
    fall to kNN (you can't route to a model the caller can't dispatch). Mutation: drop
    the `winner not in available_providers` gate → returns an undispatchable route → reds."""
    home = tmp_path / "trinity"
    _seed(home, margin=0.42)  # winner is "codex"
    _patch_routing_internals(monkeypatch, home)
    assert _try_cortex_route("api design?", ["claude", "antigravity"]) is None, (
        "routed to 'codex' when it wasn't an available provider"
    )


def test_no_mlx_abstains(tmp_path, monkeypatch):
    """Under the SHA-1 TF-IDF fallback (no [mlx]) the placement is word-overlap, not
    meaning — routing it would assert a learned preference the degraded embedder can't
    support. _try_cortex_route abstains → kNN/heuristic. Mutation: drop the
    `if not mlx_actually_loaded(): return None` gate → routes on TF-IDF → reds."""
    home = tmp_path / "trinity"
    _seed(home, margin=0.42)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: False)  # no real embedder
    monkeypatch.setattr("trinity_local.lens_routing.place_query", lambda q, b, e: "b00")
    assert _try_cortex_route("api design?", _PROVIDERS) is None, (
        "cortex-routed under the TF-IDF fallback — the no-[mlx] abstain gate regressed"
    )
