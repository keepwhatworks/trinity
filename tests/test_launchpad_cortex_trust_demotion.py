"""launchpad picks card: visually demote thin-margin picks (#298 collapse).

Same noise-vs-signal honesty rule that opacity-demoted low-n axis bars
(commit 0c20656). POST-COLLAPSE the picks card renders a per-basin chairman-
winner tally; a near-tie (thin margin) shouldn't render with the same visual
authority as a decisive winner. The fix opacity-demotes rows where
`margin < cortexRules.winner_margin_floor` — the SAME `WINNER_MARGIN_FLOOR`
(0.15) gate `ask._try_cortex_route` uses, threaded into the payload so the card
and the router agree on which basins are decisive. (Earlier this was a hardcoded
`< 0.2`, which mislabeled a real 0.16–0.17-margin routed basin as "abstains" —
an honesty inversion caught dogfooding the real launchpad post-#298.)

Pin shape: rendered HTML carries the conditional opacity binding tied to the
per-pick margin AND the threaded gate; the payload carries the real floor.
"""
from __future__ import annotations


def _row(basin_id, winner, margin, count=4, evidence=None):
    """A post-collapse picks-card row (what _load_cortex_rules emits)."""
    return {
        "basin_id": basin_id,
        "winner": winner,
        "margin": margin,
        "count": count,
        "n_episodes": count,
        "evidence": list(evidence or []),
    }


def test_thin_margin_row_carries_opacity_binding():
    """A pick with a thin margin should render with a Vue conditional :style
    binding that compares the per-row margin to the near-tie threshold and
    applies opacity when below."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(
        page_data={
            "cortexRules": {
                "rules": [_row("b00", "claude", 0.1)],  # near-tie
                "total_basins": 1,
                "winner_margin_floor": 0.15,
            },
        },
    )
    # The binding compares r.margin against the THREADED routing gate
    # (cortexRules.winner_margin_floor), NOT a hardcoded number — so the dimmed
    # rows are exactly the ones ask() abstains on. A future refactor must keep
    # this card↔router contract.
    assert "r.margin < cortexRules.winner_margin_floor" in html, (
        "Picks card row missing the gate-threaded opacity binding for thin-"
        "margin picks. Demote at the real WINNER_MARGIN_FLOOR, not a guess."
    )
    # And NOT the old hardcoded threshold (the honesty-inversion regression).
    assert "r.margin < 0.2" not in html, (
        "Hardcoded 0.2 demote threshold is back — it mislabels routed basins "
        "(margin 0.15–0.2) as 'abstains'. Use cortexRules.winner_margin_floor."
    )
    # Tooltip explains why
    assert "Thin margin" in html
    assert "kNN" in html  # the remedy is named


def test_wide_margin_row_keeps_full_opacity():
    """When a pick is above the gate, the conditional style resolves to null (no
    opacity demotion). Template-shape test; Vue evaluates the condition at
    runtime against the threaded floor."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(
        page_data={
            "cortexRules": {
                "rules": [_row("b00", "claude", 0.85, count=12)],
                "total_basins": 1,
                "winner_margin_floor": 0.15,
            },
        },
    )
    # The conditional binding is present (template doesn't branch on data — Vue
    # evaluates at runtime). Confirm it's the gate-threaded form.
    assert "r.margin < cortexRules.winner_margin_floor" in html


def test_payload_carries_real_routing_gate(monkeypatch, tmp_path):
    """The card↔router single source of truth: _load_cortex_rules threads the
    ACTUAL WINNER_MARGIN_FLOOR into the payload, so the card demotes exactly the
    basins ask() abstains on. Regression for the dogfood find: a hardcoded 0.2
    demote threshold mislabeled b09 (margin 0.167, which ask ROUTES) as a
    near-tie that 'falls back to kNN'."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local import launchpad_data
    from trinity_local.lens_routing import WINNER_MARGIN_FLOOR
    import trinity_local.cortex
    monkeypatch.setattr(launchpad_data, "_task_to_topology_basin", lambda: {})
    monkeypatch.setattr(
        trinity_local.cortex, "load_routing_patterns",
        lambda: {"b09": {"winner": "codex", "count": 6, "margin": 0.167,
                         "n_episodes": 6, "evidence": []}},
    )
    payload = launchpad_data._load_cortex_rules()
    assert payload is not None
    assert payload["winner_margin_floor"] == WINNER_MARGIN_FLOOR, (
        "payload must carry the real routing gate, not a card-local guess"
    )
    # The b09 row (margin 0.167) is ABOVE the 0.15 gate → ask routes it → the
    # card must NOT demote it. With the threaded floor the template compares
    # 0.167 < 0.15 → False, so no dimming. Assert the data supports that.
    row = payload["rules"][0]
    assert row["margin"] >= payload["winner_margin_floor"], (
        "b09 routes (margin 0.167 >= 0.15 gate) — the card must treat it as a "
        "decisive pick, not a dimmed near-tie"
    )


def test_evidence_chip_label_is_an_ordinal_not_an_id_fragment():
    """The cortex evidence chip must NOT render an id fragment as its label.

    History: the 2026-05-31 fix sliced ``cid`` to an 8-char hash (git short-SHA
    style) after stripping ``council_|bundle_``. But that hash (e.g. "1a5b74fb")
    is an OPAQUE internal-id fragment — it reads as a leaked id and tells a touch
    user NOTHING (the full id hid in the hover-only :title, unreachable on the
    side panel / phones; the same opaque-id-as-label class as the Iter-184 basin
    chip). The chip now LEADS WITH WHAT IT IS — an ordinal "council {{ ei + 1 }}"
    scoped to the pick's row — with the full id preserved in the :title + href.

    Pin the corrected contract at the source: the template must render the ordinal
    expression and must NOT have regressed to the id-slicing form."""
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data={"cortexRules": {"rules": []}})
    assert "council {{ ei + 1 }}" in html, (
        "the cortex evidence chip must label itself with a human ordinal "
        "('council 1', 'council 2', …), not an opaque id fragment"
    )
    # The id-slicing form (the opaque-hex-fragment symptom) must be GONE — its
    # return reintroduces the leaked-id label this fix removed.
    assert "council_|bundle_" not in html and ".slice(0, 8)" not in html, (
        "the evidence chip regressed to slicing the council id into an opaque "
        "hex fragment ('1a5b74fb') as its visible label"
    )


def test_load_cortex_rules_dedupes_evidence(monkeypatch, tmp_path):
    """A basin can cite the same council more than once (one council can
    contribute multiple episodes to a basin). The launchpad evidence chips must
    show DISTINCT councils, not the same link 3×."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local import launchpad_data
    import trinity_local.cortex
    monkeypatch.setattr(launchpad_data, "_task_to_topology_basin", lambda: {})
    # Same council cited 3× + two distinct others (post-collapse flat tally).
    pick = {
        "winner": "claude",
        "count": 6,
        "margin": 0.8,
        "n_episodes": 6,
        "evidence": [
            "bundle_c1c521b116432093",
            "bundle_c8a34624c9009669",
            "bundle_c8a34624c9009669",
            "bundle_07c10f3c1b9da59e",
            "bundle_c8a34624c9009669",
        ],
    }
    monkeypatch.setattr(trinity_local.cortex, "load_routing_patterns",
                        lambda: {"product_research": pick})
    payload = launchpad_data._load_cortex_rules()
    assert payload is not None, "picks payload is None"
    rule = next(r for r in payload["rules"] if r["basin_id"] == "product_research")
    ev = rule["evidence"]
    assert ev == list(dict.fromkeys(ev)), f"evidence not deduped: {ev}"
    # The triple-cited council appears exactly once; order preserved.
    assert ev == [
        "bundle_c1c521b116432093",
        "bundle_c8a34624c9009669",
        "bundle_07c10f3c1b9da59e",
    ], ev
