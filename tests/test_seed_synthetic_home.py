"""Guard: scripts/seed_synthetic_home.py produces a GATE-READY synthetic home.

The seeder is the PII-free input to the 36-surface browser_smoke gate (it lets the
gate run without the founder's real corpus — see [[browser_smoke_gate_silently_rots]]
+ the v1.7.372 $TRINITY_HOME fix). If the seeder silently stops producing the data a
surface needs, the gate would skip/red on synthetic data and the PII-free path rots
unnoticed. These pin the load-bearing artifacts — in particular routing.json, which
is ONLY written when the seeded councils carry `provider_scores` (the exact field
whose absence left the routing cheat-sheet empty when this was first dogfooded).

Pure in-process (no browser): runs the data half of the seed and asserts the files
exist + parse. Mutation-proven: drop `provider_scores` from the seeded councils and
freeze_routing_to_disk writes nothing → the routing.json assertion reds.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_synthetic_home_under_test", SEEDER)
    assert spec and spec.loader, "could not load scripts/seed_synthetic_home.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_produces_gate_ready_home(tmp_path, monkeypatch):
    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))

    mod = _load_seeder()
    counts = mod.seed(home)

    assert counts["councils"] >= 2, "need >=2 councils for the ELO head-to-head (S1)"

    # Council outcomes written (recent rail S10, painkiller S35, ELO S1).
    outcomes = list((home / "council_outcomes").glob("council_*.json"))
    assert len(outcomes) == counts["councils"], (
        f"expected {counts['councils']} council outcome files, got {len(outcomes)}"
    )

    # routing.json — the canary. freeze_routing_to_disk ONLY writes it when the
    # councils' routing labels carry provider_scores (populating by_task_type, the
    # routing cheat-sheet S3). Empty by_task_type → no file → S3 renders 0 rows.
    routing = home / "scoreboard" / "routing.json"
    assert routing.exists(), (
        "scoreboard/routing.json was not written — the seeded councils lost their "
        "provider_scores, so by_task_type is empty and the routing cheat-sheet (S3) "
        "would render 0 rows"
    )
    by_task = json.loads(routing.read_text())["by_task_type"]
    repeated = [tt for tt in by_task if tt in ("design", "debug")]
    assert repeated, f"expected the repeated task_types in by_task_type, got {list(by_task)}"

    # topics.json basins + picks.json with matching ids (topology + pick→topology
    # identity cross-links, S19/21/22/26).
    topics = json.loads((home / "memories" / "topics.json").read_text())
    basin_ids = {b["id"] for b in topics["basins"]}
    assert len(basin_ids) >= 2, "need basins for the topology graph"
    picks = json.loads((home / "scoreboard" / "picks.json").read_text())
    assert set(picks) <= basin_ids, (
        f"picks basin ids {set(picks)} must be a subset of topics basin ids "
        f"{basin_ids} or the pick→topology cross-link can't resolve"
    )

    # core.md must exceed the memory viewer's >50-char real-body check (S14b).
    assert len((home / "core.md").read_text()) > 50, "core.md too short for S14b"

    # me/lenses.json backs the launchpad TASTE card + its "Copy as text" button
    # (browser_smoke Surface 4). Without valid LensPair rows _load_taste_lenses()
    # returns None, the card shows the "Run lens" empty CTA, the copy button never
    # renders, and S4 hard-fails on the synthetic gate. Assert the seeder writes
    # rows that actually load as LensPairs (a schema drift that makes load_lenses()
    # return [] would silently re-dark the taste card — the value-prop surface).
    # TRINITY_HOME is already monkeypatched to `home` at the top of the test.
    from trinity_local.me.pair_mining import load_lenses
    lenses = load_lenses()
    assert len(lenses) >= 1, (
        "seeder's me/lenses.json produced no loadable LensPairs — the taste card "
        "would show the empty 'Run lens' CTA and browser_smoke Surface 4 would fail"
    )
    assert lenses[0].pole_a and lenses[0].pole_b, "seeded lens missing its poles"

    # The launchpad actually rendered (the gate serves this).
    assert (home / "portal_pages" / "launchpad.html").exists(), (
        "portal_pages/launchpad.html was not rendered — the gate has nothing to serve"
    )
