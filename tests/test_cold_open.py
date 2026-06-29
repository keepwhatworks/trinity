"""#212 cold-start aha: cold_open_tension() surfaces ONE surprising true
tension the instant the lens has signal — the differentiated wow, before the
user learns a verb. Surfaced on the launchpad hero, `status`, and MCP."""
from __future__ import annotations

import pytest


@pytest.mark.usefixtures("patch_trinity_home")
class TestColdOpenTension:
    def test_none_on_cold_install(self):
        from trinity_local.cold_start import cold_open_tension
        assert cold_open_tension() is None

    def test_surfaces_top_registry_tension(self):
        from trinity_local.cold_start import cold_open_tension
        from trinity_local.me.lens_registry import RegistryEntry, save_registry
        from trinity_local.utils import now_iso

        ts = now_iso()
        save_registry([
            RegistryEntry(
                tension_id="t1", pole_a="ship velocity", pole_b="polish",
                evidence_ids=["e1", "e2", "e3", "e4"],  # support 4 ≥ LOW_CONFIDENCE
                first_seen=ts, last_confirmed=ts,
            ),
            RegistryEntry(
                tension_id="t2", pole_a="depth", pole_b="speed",
                evidence_ids=["x1"], first_seen=ts, last_confirmed=ts,
            ),
        ])
        line = cold_open_tension()
        assert line is not None
        # Highest-support tension leads (ship velocity / polish, support 4).
        assert "ship velocity" in line and "polish" in line
        assert "4 of your decisions" in line  # provenance shown for n>=3
        assert "depth" not in line  # only ONE tension surfaced

    def test_cold_open_picks_recency_weighted_winner_with_its_own_n(self):
        """#256 recency-weighting reaches the rendered cold-open: the hero must
        name the RECENCY-WEIGHTED strongest tension (a fresh tension outranks a
        STALER one of higher RAW support) AND report that winner's OWN support
        count — not the staler tension's larger n.

        The #212 cold-open is the differentiated wow shown on the hero, `status`,
        and MCP. Two regressions ship a wrong value here and no prior test bites:
          (a) `active_tensions_sorted` reverting to raw `support_count` ordering
              (the #256 fix is in the PRIMITIVE `_recency_weighted_support`, whose
              own test never calls `active_tensions_sorted`, so the SORT-KEY wiring
              is unguarded) → the hero names the stale tension; and
          (b) the proof line taking n from the wrong (higher-raw-support) tension.

        Both surface as a wrong rendered claim. Construct the discriminating case:
        a STALE high-raw-support tension (support 4, confirmed 80d ago →
        recency-weighted 4·0.5^(80/120) ≈ 2.52) vs a FRESH low-raw-support tension
        (support 3, confirmed today → 3·0.5^0 = 3.0). 3.0 > 2.52, so the FRESH
        tension wins, and its n is 3 — strictly LESS than the stale tension's 4,
        so an n-from-the-wrong-tension bug is detectable (it would read 4)."""
        from datetime import datetime, timezone, timedelta

        from trinity_local.cold_start import cold_open_tension
        from trinity_local.me.lens_registry import (
            RANK_HALFLIFE_DAYS, RECENCY_DAYS, RegistryEntry, save_registry,
        )

        now = datetime.now(timezone.utc)

        def days_ago(d: int) -> str:
            return (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

        # Both inside the RECENCY_DAYS active window (no robustness override in play),
        # so the ONLY thing that orders them is recency-weighted support.
        stale_age = 80
        assert stale_age < RECENCY_DAYS  # stale tension is still ACTIVE, just decayed
        stale_rw = 4 * 0.5 ** (stale_age / RANK_HALFLIFE_DAYS)
        fresh_rw = 3 * 0.5 ** (0 / RANK_HALFLIFE_DAYS)
        assert fresh_rw > stale_rw, "fixture must make the fresh tension rank first"

        save_registry([
            RegistryEntry(  # STALE, higher RAW support (4)
                tension_id="stale", pole_a="speed", pole_b="correctness",
                evidence_ids=["e1", "e2", "e3", "e4"],  # support 4
                first_seen=days_ago(200), last_confirmed=days_ago(stale_age),
            ),
            RegistryEntry(  # FRESH, lower RAW support (3) — the recency-weighted WINNER
                tension_id="fresh", pole_a="simple", pole_b="complete",
                evidence_ids=["x1", "x2", "x3"],  # support 3
                first_seen=days_ago(40), last_confirmed=days_ago(0),
            ),
        ])

        line = cold_open_tension()
        assert line is not None
        # The hero names the FRESH (recency-weighted) winner, not the stale tension.
        assert "simple" in line and "complete" in line, (
            "#256 regression: cold-open named the wrong tension — it surfaced the "
            f"STALER higher-raw-support axis instead of the recency-weighted winner: {line!r}"
        )
        assert "speed" not in line and "correctness" not in line, (
            "#256 regression: cold-open leaked the staler tension's poles: " + repr(line)
        )
        # The proof line's n is the WINNER's support (3), never the stale tension's 4.
        assert "3 of your decisions" in line, (
            "cold-open reported the wrong support count — the proof n must be the "
            f"recency-weighted winner's own support (3), got: {line!r}"
        )
        assert "4 of your decisions" not in line, (
            "cold-open mixed the winner's poles with the OTHER tension's support "
            f"count (4) — a value drawn from the wrong tension: {line!r}"
        )

    def test_anchor_fallback_before_lens_built(self):
        """#242 — no lens yet (embeddings still backfilling), but the
        cold-start scan cached pure-text anchors → first paint shows the
        recurring topics instead of nothing."""
        from trinity_local.cold_start import cold_open_tension, _write_state
        from trinity_local.utils import now_iso

        _write_state({
            "status": "complete", "started_at": now_iso(),
            "finished_at": now_iso(), "sources_detected": ["claude"],
            "added": 120, "scanned": 120,
            "early_anchors": ["LDK", "Kitchen", "Bath", "Entry", "Loft Bedroom"],
        })
        line = cold_open_tension()
        assert line is not None
        assert "recurring topics" in line.lower()
        assert "LDK" in line and "Kitchen" in line
        # Only the top 4 shown (keeps the line tight).
        assert "Loft Bedroom" not in line

    def test_lens_tension_beats_anchor_fallback(self):
        """When a real lens tension exists, it wins over the anchor fallback —
        the anchors are only the pre-lens first-run stopgap."""
        from trinity_local.cold_start import cold_open_tension, _write_state
        from trinity_local.me.lens_registry import RegistryEntry, save_registry
        from trinity_local.utils import now_iso

        ts = now_iso()
        _write_state({"status": "complete", "early_anchors": ["LDK", "Kitchen"]})
        save_registry([RegistryEntry(
            tension_id="t1", pole_a="concrete", pole_b="abstract",
            evidence_ids=["e1", "e2", "e3"], first_seen=ts, last_confirmed=ts,
        )])
        line = cold_open_tension()
        assert "concrete" in line and "LDK" not in line


@pytest.mark.usefixtures("patch_trinity_home")
class TestColdOpenLaunchpad:
    def test_page_data_carries_cold_open(self):
        from trinity_local.launchpad_data import _cold_open_for_launchpad
        from trinity_local.me.lens_registry import RegistryEntry, save_registry
        from trinity_local.utils import now_iso

        ts = now_iso()
        save_registry([RegistryEntry(
            tension_id="t1", pole_a="A", pole_b="B",
            evidence_ids=["e1"], first_seen=ts, last_confirmed=ts,
        )])
        assert _cold_open_for_launchpad() is not None

    def test_hero_renders_cold_open_binding(self):
        from trinity_local.launchpad_template import render_launchpad_html
        html = render_launchpad_html(page_data={})
        assert "pageData.coldOpen" in html
        assert "cold-open" in html


@pytest.mark.usefixtures("patch_trinity_home")
class TestColdOpenSignature:
    """#254 — when the cached taste adjectives exist, the cold-open leads with
    the three-word signature, then the dominant tension as proof."""

    def _registry(self):
        from trinity_local.me.lens_registry import RegistryEntry, save_registry
        from trinity_local.utils import now_iso
        ts = now_iso()
        save_registry([RegistryEntry(
            tension_id="t1", pole_a="executable artifact",
            pole_b="explanatory description",
            evidence_ids=[f"e{i}" for i in range(17)],
            basins_spanned=["b00", "b01", "b02"],
            first_seen=ts, last_confirmed=ts,
        )])

    def test_signature_proof_when_cached(self):
        import json
        from trinity_local.me.correction_lens import _taste_signature_path
        from trinity_local.cold_start import cold_open_tension
        self._registry()
        p = _taste_signature_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"adjectives": ["terse", "decisive", "action"], "n": 41}), encoding="utf-8")

        line = cold_open_tension()
        assert line.startswith("Your taste in three words: terse, decisive, action.")
        assert "executable artifact" in line and "17 decisions" in line
        assert "comes back in your voice" in line or "your voice" in line

    def test_falls_back_to_tension_line_without_signature(self):
        from trinity_local.cold_start import cold_open_tension
        self._registry()  # registry but NO taste_signature.json cached
        line = cold_open_tension()
        assert line.startswith("One axis your lens already surfaces:")
        assert "executable artifact" in line
