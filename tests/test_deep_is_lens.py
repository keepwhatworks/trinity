"""`lens --deep` and `dream` run `lens`, and old virtual councils leave the tallies (lens batch
L2, council_6a9187cd799419e7, coherence review 2026-10-15).

`--deep` used to cluster similar past prompts and spend one chairman call per cluster on a
"virtual council" (about 430 calls and three hours on a real history; 1,048 of one install's
1,564 saved councils). The lens and the trust ledger excluded them; the launchpad tallies,
council counts and telemetry did not. On disk they are indistinguishable from a live
run_council(responses=...), so live ones are stamped from now on and an unstamped synthesis-only
council is treated as legacy hindsight synthesis everywhere outcomes are tallied or counted.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


def test_lens_deep_runs_the_plain_build(monkeypatch, capsys):
    from trinity_local.commands import me
    built = []
    monkeypatch.setattr("trinity_local.embeddings.require_real_embedder", lambda: None)
    monkeypatch.setattr(me, "build_me_via_lens_pipeline",
                        lambda **kw: (built.append(kw), ("/tmp/lens.md", {"skipped": True, "reason": "x"}))[1])
    monkeypatch.setattr(me, "_post_build_hooks", lambda dry_run: {})
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    me.handle_me_build(SimpleNamespace(deep=True, only_distill=False, dry_run=False, legacy=False,
                                       sample_size=80, k_basins=None, force=False))
    assert len(built) == 1, "lens --deep must run the lens build, once"
    assert "same as `lens`" in capsys.readouterr().err


def test_dream_is_lens(monkeypatch):
    from trinity_local.commands import dream, me
    seen = []
    monkeypatch.setattr(me, "handle_me_build", lambda a: seen.append(a) or 0)
    assert dream.handle_dream(SimpleNamespace(only_distill=False, dry_run=False)) == 0
    assert len(seen) == 1 and seen[0].deep is False and seen[0].dry_run is False


def test_only_distill_calls_distill_once(monkeypatch, patch_trinity_home):
    from trinity_local import distill
    from trinity_local.commands import dream
    calls = []
    monkeypatch.setattr(distill, "distill_via_chairman", lambda provider="claude", **k: calls.append(provider) or {"ok": True})
    assert dream.handle_dream(SimpleNamespace(only_distill=True, primary_provider=None)) == 0
    assert calls == ["claude"]


def test_the_predicate():
    from trinity_local.council_schema import RESPONSES_FROM_CALLER, is_hindsight_synthesis
    assert is_hindsight_synthesis({"mode": "synthesis_only"}) is True
    assert is_hindsight_synthesis({"mode": "synthesis_only", "responses_from": RESPONSES_FROM_CALLER}) is False
    assert is_hindsight_synthesis({"mode": "parallel"}) is False
    assert is_hindsight_synthesis(None) is False


@pytest.fixture
def live_and_legacy(monkeypatch, patch_trinity_home):
    """One live run_council(responses=...) through the real MCP path, and a copy of it with
    the stamp removed: what a legacy `lens --deep` virtual council looks like on disk."""
    __import__("importlib").import_module("trinity_local.council_runner")  # bind before patching
    import trinity_local.providers as P
    from trinity_local.mcp_server import handle_call_tool
    from trinity_local.providers import ProviderResult
    from trinity_local.state_paths import council_outcomes_dir

    class Chair:
        def run(self, prompt, cwd=None):
            return ProviderResult(provider="claude", stdout=(
                'Agreed claims\n- one\n\n```json\n{"winner": "codex", "confidence": "high", '
                '"task_type": "coding", "agreed_claims": [], "disagreed_claims": []}\n```\n'),
                stderr="", returncode=0)
    monkeypatch.setattr(P, "make_provider", lambda cfg: Chair())
    asyncio.run(handle_call_tool("run_council", {
        "task": "pick", "primary_provider": "claude",
        "responses": [{"provider": "codex", "content": "A long enough answer " * 20},
                      {"provider": "antigravity", "content": "Another long answer " * 20}]}))
    files = list(council_outcomes_dir().glob("council_*.json"))
    assert len(files) == 1, files
    live = json.loads(files[0].read_text(encoding="utf-8"))
    assert live["metadata"]["responses_from"] == "caller", "a live responses= council must be stamped"
    legacy = json.loads(json.dumps(live))
    legacy["council_run_id"] = "council_legacyvirtual0"
    legacy["metadata"].pop("responses_from")
    (council_outcomes_dir() / "council_legacyvirtual0.json").write_text(json.dumps(legacy), encoding="utf-8")
    return live["council_run_id"], "council_legacyvirtual0"


def test_every_tally_keeps_the_live_council_and_drops_the_legacy_one(live_and_legacy):
    from trinity_local import council_analytics, telemetry
    from trinity_local.launchpad_data import _load_recent_councils
    live, legacy = live_and_legacy
    records, _ = council_analytics._scan_outcomes()
    assert {r["council_run_id"] for r in records} == {live}
    assert {p.get("council_run_id") for p in telemetry._iter_council_payloads()} == {live}
    council_analytics._COUNT_CACHE.clear()
    assert council_analytics.council_count() == 1
    shown = {c.get("council_id") or c.get("id") or c.get("council_run_id") for c in _load_recent_councils()}
    assert legacy not in json.dumps(_load_recent_councils()), shown
