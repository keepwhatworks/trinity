"""#251 — the Auto-Dream-style stale pass (usage-triggered ingest + embed heal).

Pins the four load-bearing properties:
  1. GATE: due on missing/old/wrong-shape marker, not-due on a fresh one,
     hard-off under TRINITY_AUTOSCAN_DISABLED (so suite councils never ingest
     the developer's real transcripts).
  2. LOCK: concurrent kicks don't double-run; a crashed pass's stale lock is
     taken over.
  3. HEAL: embed_backfill writes embeddings onto previously-unembedded nodes
     via last-wins upsert, with the SAME `search_document:` prefix as
     flush_chunk (vectors must share the space) and WITHOUT bumping
     created_at (healing must not make old prompts look recent). It ABSTAINS
     when the embedder model isn't downloaded — never TF-IDF-fallback vectors.
  4. WIRE-IN: run_council actually kicks the gate (mutation: remove the call
     -> reds).
"""
from __future__ import annotations

import json
import threading
import time

import pytest


@pytest.fixture()
def stale_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    # The kick must be LIVE in these tests — explicitly clear the suite-wide
    # kill switch (other fixtures/tests set it).
    monkeypatch.delenv("TRINITY_AUTOSCAN_DISABLED", raising=False)
    monkeypatch.delenv("TRINITY_STALE_PASS_HOURS", raising=False)
    # The stale ingest+embed pass is lens upkeep, now gated behind the opt-in
    # add-on (lens_addon). These tests exercise the kick, so enable the lens.
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
    return tmp_path


class TestGate:
    def test_due_when_no_marker(self, stale_home):
        from trinity_local.stale_pass import stale_pass_is_due

        due, reason = stale_pass_is_due()
        assert due and "no prior pass" in reason

    def test_not_due_when_fresh(self, stale_home):
        from trinity_local.stale_pass import marker_path, stale_pass_is_due
        from trinity_local.utils import now_iso

        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(json.dumps({"completed_at": now_iso()}))
        due, reason = stale_pass_is_due()
        assert not due and "fresh" in reason

    def test_due_when_marker_old(self, stale_home):
        from trinity_local.stale_pass import marker_path, stale_pass_is_due

        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(json.dumps({"completed_at": "2026-01-01T00:00:00+00:00"}))
        due, reason = stale_pass_is_due()
        assert due and "window" in reason

    @pytest.mark.parametrize("bad", ["[1,2,3]", '"str"', "null", "{not json"])
    def test_due_on_corrupt_or_non_dict_marker(self, stale_home, bad):
        # guard_shape_not_just_parse: a wrong-shape marker must read as DUE,
        # never crash the council launch that consulted it.
        from trinity_local.stale_pass import marker_path, stale_pass_is_due

        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(bad)
        due, _ = stale_pass_is_due()
        assert due

    def test_kick_disabled_by_autoscan_kill_switch(self, stale_home, monkeypatch):
        from trinity_local import stale_pass

        monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
        fired = []
        monkeypatch.setattr(stale_pass, "run_stale_pass", lambda **kw: fired.append(kw))
        assert stale_pass.maybe_kick_stale_pass("test") is False
        assert not fired, "kick ran despite TRINITY_AUTOSCAN_DISABLED"


class TestKickAndLock:
    def test_kick_runs_pass_and_releases_lock(self, stale_home, monkeypatch):
        from trinity_local import stale_pass

        ran = threading.Event()
        monkeypatch.setattr(stale_pass, "run_stale_pass", lambda trigger: ran.set())
        assert stale_pass.maybe_kick_stale_pass("test") is True
        assert ran.wait(timeout=15), "pass thread never ran"
        # Lock release races the Event by a hair — poll briefly, generously.
        deadline = time.monotonic() + 15
        while stale_pass.lock_path().exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not stale_pass.lock_path().exists(), "lock not released after the pass"

    def test_second_kick_noops_while_lock_held(self, stale_home, monkeypatch):
        from trinity_local import stale_pass

        stale_pass.lock_path().parent.mkdir(parents=True, exist_ok=True)
        stale_pass.lock_path().write_text(json.dumps({"pid": 1}))
        fired = []
        monkeypatch.setattr(stale_pass, "run_stale_pass", lambda trigger: fired.append(trigger))
        assert stale_pass.maybe_kick_stale_pass("test") is False
        assert not fired, "kick double-ran while another pass held the lock"

    def test_stale_lock_is_taken_over(self, stale_home, monkeypatch):
        import os

        from trinity_local import stale_pass

        lock = stale_pass.lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": 1}))
        old = time.time() - stale_pass._LOCK_STALE_S - 60
        os.utime(lock, (old, old))
        ran = threading.Event()
        monkeypatch.setattr(stale_pass, "run_stale_pass", lambda trigger: ran.set())
        assert stale_pass.maybe_kick_stale_pass("test") is True, "stale lock not taken over"
        assert ran.wait(timeout=15)

    def test_kick_not_due_is_a_noop(self, stale_home, monkeypatch):
        from trinity_local import stale_pass
        from trinity_local.utils import now_iso

        stale_pass.marker_path().parent.mkdir(parents=True, exist_ok=True)
        stale_pass.marker_path().write_text(json.dumps({"completed_at": now_iso()}))
        fired = []
        monkeypatch.setattr(stale_pass, "run_stale_pass", lambda trigger: fired.append(trigger))
        assert stale_pass.maybe_kick_stale_pass("test") is False
        assert not fired


def _seed_node(node_id: str, *, embedded: bool):
    from trinity_local.memory import PromptNode, upsert_prompt_node

    upsert_prompt_node(PromptNode(
        id=node_id,
        transcript_id="t1",
        provider="claude",
        source_path="/dev/null",
        turn_index=0,
        text=f"synthetic prompt body for {node_id}",
        embedding=[0.5] * 8 if embedded else [],
        created_at="2026-01-02T03:04:05+00:00",
    ))


class TestEmbedBackfill:
    def test_heals_unembedded_preserves_created_at_and_prefix(self, stale_home, monkeypatch):
        import trinity_local.embeddings as emb
        import trinity_local.ingest_helpers as ih
        from trinity_local.memory.store import iter_prompt_nodes
        from trinity_local.stale_pass import embed_backfill

        _seed_node("node_has_emb", embedded=True)
        _seed_node("node_needs_emb", embedded=False)

        monkeypatch.setattr(emb, "require_embedder_ready", lambda: None)
        seen_texts: list[str] = []

        def fake_embed(texts, *, dim, batch_size):
            seen_texts.extend(texts)
            return [[0.25] * dim for _ in texts]

        monkeypatch.setattr(ih, "_embed_in_batches", fake_embed)

        out = embed_backfill()
        assert out["healed"] == 1 and out["remaining"] == 0, out
        # The healed text must carry flush_chunk's prefix — same vector space.
        assert seen_texts and all(t.startswith("search_document: ") for t in seen_texts)

        nodes = {n.id: n for n in iter_prompt_nodes()}
        assert nodes["node_needs_emb"].embedding, "node not healed in the store"
        assert nodes["node_needs_emb"].created_at == "2026-01-02T03:04:05+00:00", (
            "healing bumped created_at — old prompts would read as recent"
        )

    def test_abstains_when_embedder_not_ready(self, stale_home, monkeypatch):
        import trinity_local.embeddings as emb
        from trinity_local.memory.store import iter_prompt_nodes
        from trinity_local.stale_pass import embed_backfill

        _seed_node("node_needs_emb", embedded=False)

        def not_ready():
            raise emb.EmbedderNotReadyError("model not downloaded")

        monkeypatch.setattr(emb, "require_embedder_ready", not_ready)
        out = embed_backfill()
        assert out["healed"] == 0 and "not ready" in out.get("skipped", ""), out
        nodes = {n.id: n for n in iter_prompt_nodes()}
        assert not nodes["node_needs_emb"].embedding, (
            "backfill wrote vectors despite the embedder being unavailable — "
            "TF-IDF fallback garbage in a 768d space"
        )


class TestWireIn:
    def test_run_council_kicks_the_gate(self, stale_home, monkeypatch):
        """run_council must consult the stale-pass gate on EVERY launch.
        Chain mode with an empty explicit sequence reaches the kick (it fires
        before the chain branch) then fails loudly in _run_chain — so this
        asserts the wire-in without dispatching any provider."""
        from trinity_local import stale_pass
        from trinity_local.council_runner import run_council
        from trinity_local.config import AppConfig
        from trinity_local.council_schema import PromptBundle
        from pathlib import Path

        kicked = []
        monkeypatch.setattr(stale_pass, "maybe_kick_stale_pass", lambda trigger: kicked.append(trigger))
        bundle = PromptBundle(bundle_id="b1", task_cluster_id="c1", task_text="t", goal="g")
        with pytest.raises(Exception):
            run_council(
                config=AppConfig(max_turns=1, notifications=False, providers={}, task_preferences={}),
                bundle=bundle,
                member_providers=[],
                primary_provider="claude",
                cwd=Path("."),
                mode="chain",
                sequence=[],
            )
        assert kicked == ["run_council"], (
            "run_council launched without consulting the stale-pass gate"
        )


class TestConsolidateOnUsage:
    """#316: the stale pass rebuilds picks.json from the lens basins on a usage
    event (council launch) so `ask`'s routing compounds AUTOMATICALLY — no manual
    `consolidate`/`dream`, no cron (founder usage-gate rule). This makes the
    README's "the right model picked automatically / gets sharper as you use it"
    claim literally true.
    """

    def test_run_stale_pass_rebuilds_routing_picks(self, stale_home, monkeypatch):
        """A routable lens result is tallied into picks.json during the pass.
        Mutation: drop the consolidate block in run_stale_pass -> no save, reds."""
        import trinity_local.stale_pass as sp

        # No-op the heavy phases so we isolate the consolidate wiring.
        monkeypatch.setattr(
            "trinity_local.incremental_ingest.ingest_recent",
            lambda **k: type("R", (), {"to_dict": lambda self: {}})(),
        )
        monkeypatch.setattr(sp, "embed_backfill", lambda **k: {})
        routing = {"b00": {"winner": "claude", "count": 3, "margin": 0.2,
                           "n_episodes": 3, "evidence": []}}
        monkeypatch.setattr(
            "trinity_local.lens_routing.consolidate_via_lens_basins", lambda: routing
        )
        saved: dict = {}
        monkeypatch.setattr(
            "trinity_local.cortex.save_routing_patterns",
            lambda patterns, **k: saved.update(patterns),
        )

        summary = sp.run_stale_pass("test")
        assert summary["consolidate"] == {"routable_basins": 1}
        assert saved == routing, "picks.json must receive the lens-derived routing"

    def test_cold_home_consolidate_is_a_noop_not_a_crash(self, stale_home, monkeypatch):
        """No lens yet -> {} basins -> save is skipped (dodging the clobber guard),
        and the pass still completes."""
        import trinity_local.stale_pass as sp

        monkeypatch.setattr(
            "trinity_local.incremental_ingest.ingest_recent",
            lambda **k: type("R", (), {"to_dict": lambda self: {}})(),
        )
        monkeypatch.setattr(sp, "embed_backfill", lambda **k: {})
        monkeypatch.setattr(
            "trinity_local.lens_routing.consolidate_via_lens_basins", lambda: {}
        )
        called = {"saved": False}
        monkeypatch.setattr(
            "trinity_local.cortex.save_routing_patterns",
            lambda *a, **k: called.update(saved=True),
        )

        summary = sp.run_stale_pass("test")
        assert summary["consolidate"] == {"routable_basins": 0}
        assert called["saved"] is False, "empty routing must NOT call save (clobber-safe)"


class TestIngestFreshness:
    """ingest_freshness() surfaces a STALLED ingest pass — the trigger stopped
    firing, not merely 'due' — so `status` warns the corpus is missing recent
    transcripts (search/ask/k-NN then read stale data), distinct from the lens
    freeze. Found 2026-06-29: last pass 13d ago, invisible until surfaced."""

    def test_absent_when_no_marker(self, stale_home):
        from trinity_local.stale_pass import ingest_freshness
        assert ingest_freshness()[0] == "absent"

    def test_current_when_recent(self, stale_home):
        import json
        from trinity_local.stale_pass import ingest_freshness, marker_path
        from trinity_local.utils import now_iso
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(json.dumps({"completed_at": now_iso()}))
        assert ingest_freshness()[0] == "current"

    def test_stale_when_significantly_overdue(self, stale_home):
        import json
        from datetime import datetime, timezone, timedelta
        from trinity_local.stale_pass import ingest_freshness, marker_path
        old = (datetime.now(timezone.utc) - timedelta(days=13)).isoformat()
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(json.dumps({"completed_at": old}))
        state, reason = ingest_freshness()
        assert state == "stale", (state, reason)
        assert "missing recent transcripts" in reason

    def test_due_but_not_yet_stale_is_current(self, stale_home):
        """30h ago is DUE (>24h, refires on normal activity) but NOT 'stale'
        (<3× window) — the discriminator that stops crying wolf on the daily
        due-window. THE key bite: a stalled trigger (multi-day) vs normal due."""
        import json
        from datetime import datetime, timezone, timedelta
        from trinity_local.stale_pass import ingest_freshness, marker_path
        d = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(json.dumps({"completed_at": d}))
        assert ingest_freshness()[0] == "current"
