"""The lens reads the user's own prompts and writes nothing it was not asked to (lens batch L1,
coherence review 2026-10-15, amd_0395).

- The lens builds fall back to `search_prompt_nodes("")` when the embedding sampler has too
  few nodes, and that read skipped the "only the user" gate: on the real store 683 of the 1,000
  most recent prompts are ones the gate drops (internal/experiments/lens_store_scan.py).
- `lens --dry-run` exists to inspect the topology before committing, and overwrote topics.json.
- Every council appended to council_runs.jsonl and launch_events.jsonl and wrote a task_sync/
  record; nothing read any of them.
- memory.store.record_council_outcome attached council runs and chairman winners to the user's
  prompts: a council -> lens edge (founder lock #1) kept for wiring, with no caller.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from trinity_local.memory.schemas import PromptNode

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"


def _node(nid: str, text: str) -> PromptNode:
    return PromptNode(id=nid, transcript_id=f"t-{nid}", provider="claude", source_path=f"/tmp/{nid}.jsonl",
                      turn_index=0, text=text, embedding=[], created_at="2026-10-01T00:00:00Z",
                      timestamp="2026-10-01T00:00:00Z")


def _write_map(classes: dict, own_text: dict | None = None) -> None:
    from trinity_local.me import authorship
    path = authorship.authorship_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": authorship.MAP_VERSION, "fingerprint": "x", "classes": classes,
                                "own_text": own_text or {}}), encoding="utf-8")


def test_the_fallback_sampler_reads_only_the_user(patch_trinity_home):
    from trinity_local.memory import search_prompt_nodes, upsert_prompt_node
    for nid, text in [("mine", "Should the cache ship before the schema migration or after it lands?"),
                      ("bot", "Run the nightly reconciliation job and report every failing account."),
                      ("paste", "Here is the log I pasted from the server, plus my question about it.")]:
        upsert_prompt_node(_node(nid, text))
    _write_map({"bot": "headless", "paste": "paste"}, {"paste": "my question about it"})
    got = {r.prompt_id: r.text for r in search_prompt_nodes("", top_k=10)}
    assert "bot" not in got, "a prompt the gate drops reached the lens through the fallback sampler"
    assert got.get("paste") == "my question about it", "a paste must reach the lens as the user's own lines"
    assert "mine" in got


def test_dry_run_does_not_write_the_topology(patch_trinity_home, monkeypatch):
    from trinity_local import me_builder
    from trinity_local.me import pipeline
    saved = []
    monkeypatch.setattr(pipeline, "compute_basins",
                        lambda **kw: [SimpleNamespace(id="b1", size=3, top_terms=["cache"])])
    monkeypatch.setattr(pipeline, "save_basins", lambda basins, **kw: saved.append(basins))
    monkeypatch.setattr(me_builder, "_sample_diverse_with_embeddings",
                        lambda **kw: [SimpleNamespace(prompt_id="p1", text="a prompt")])
    _path, info = me_builder.build_me_via_lens_pipeline(dry_run=True)
    assert info.get("dry_run") is True
    assert saved == [], "lens --dry-run overwrote topics.json"
    pipeline.stage1_basins()
    assert len(saved) == 1, "a real build must still write the topology"


def test_a_council_writes_no_record_nothing_reads(patch_trinity_home, monkeypatch):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle
    from trinity_local.providers import BaseProvider, ProviderResult
    chair = ('Agreed claims\n- one\n\n```json\n{"winner":"claude","confidence":"medium",'
             '"task_type":"coding"}\n```\n')

    def fake(self, command, cwd, *, timeout=None):
        if any("synthesizer" in str(a).lower() for a in command):
            return ProviderResult(provider=self.config.name, stdout=chair, stderr="", returncode=0)
        return ProviderResult(provider=self.config.name, stdout="an answer", stderr="", returncode=0)
    monkeypatch.setattr(BaseProvider, "_run_command", fake)
    out = run_council(config=load_config(), bundle=create_prompt_bundle(task_cluster_id="c", task_text="q", goal="g"),
                      member_providers=["claude", "codex"], primary_provider="claude", cwd=Path(patch_trinity_home))
    assert out.outcome.council_run_id
    home = Path(patch_trinity_home)
    assert list(home.glob("council_outcomes/*.json")), "the canonical record is still written"
    for dead in ("council_runs.jsonl", "launch_events.jsonl", "task_sync"):
        assert not list(home.rglob(dead)), f"a council wrote {dead}, which nothing reads"


def test_nothing_writes_council_results_into_the_users_prompts():
    """Founder lock #1: the lens learns from transcripts only. PromptNode's council fields are
    read by legacy ranking but must never be written; the writer that did had no caller."""
    import trinity_local.memory as memory
    assert not hasattr(memory, "record_council_outcome")
    writes = re.compile(r"\.(council_run_ids|last_replayed_at)\s*=(?!=)")
    bad = [f"{p.relative_to(SRC)}:{i}" for p in SRC.rglob("*.py")
           for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if writes.search(line)]
    assert not bad, f"code writes council results onto PromptNodes: {bad}"
