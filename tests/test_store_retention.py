"""Opt-in per-store retention (#7): TRINITY_STORE_CAP bounds the high-churn
entity stores (todos / task_sync / prompt_bundles) that otherwise grow one
record per council forever. Default OFF must be byte-identical to no retention.

Mutation-proof: delete the `prune_store_to_cap` call in a writer and
`test_cap_bounds_todos_store` reds (the store keeps growing past the cap).
"""
from __future__ import annotations

import time

from trinity_local.state_paths import prune_store_to_cap, tasks_dir


def _write_n(directory, n):
    """Write n dummy *.json records with strictly increasing mtimes so the
    newest-N selection is deterministic. Returns the ids in write order."""
    import os
    directory.mkdir(parents=True, exist_ok=True)
    ids = []
    for i in range(n):
        p = directory / f"rec_{i:04d}.json"
        p.write_text(f'{{"i": {i}}}', encoding="utf-8")
        # Force a monotonic mtime — same-second writes can tie on some FSes.
        ts = 1_000_000 + i
        os.utime(p, (ts, ts))
        ids.append(p.stem)
    return ids


class TestStoreRetention:
    def test_unset_cap_is_a_noop(self, tmp_path, monkeypatch):
        """DEFAULT OFF: with TRINITY_STORE_CAP unset, prune deletes NOTHING —
        byte-identical to today. This is the armed-off invariant."""
        monkeypatch.delenv("TRINITY_STORE_CAP", raising=False)
        d = tmp_path / "store"
        _write_n(d, 50)
        pruned = prune_store_to_cap(d)
        assert pruned == 0
        assert len(list(d.glob("*.json"))) == 50

    def test_invalid_cap_is_a_noop(self, tmp_path, monkeypatch):
        """A non-int / non-positive cap must NOT prune (fail safe toward keep)."""
        d = tmp_path / "store"
        _write_n(d, 20)
        for bad in ("", "  ", "abc", "0", "-5", "3.5"):
            monkeypatch.setenv("TRINITY_STORE_CAP", bad)
            assert prune_store_to_cap(d) == 0
        assert len(list(d.glob("*.json"))) == 20

    def test_cap_keeps_newest_n(self, tmp_path, monkeypatch):
        """With a cap set, only the newest N (by mtime) survive; the older
        records are deleted. rec_0090..rec_0099 are the newest 10."""
        monkeypatch.setenv("TRINITY_STORE_CAP", "10")
        d = tmp_path / "store"
        _write_n(d, 100)
        pruned = prune_store_to_cap(d)
        assert pruned == 90
        survivors = sorted(p.stem for p in d.glob("*.json"))
        assert survivors == [f"rec_{i:04d}" for i in range(90, 100)]

    def test_cap_noop_when_under_limit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_STORE_CAP", "100")
        d = tmp_path / "store"
        _write_n(d, 30)
        assert prune_store_to_cap(d) == 0
        assert len(list(d.glob("*.json"))) == 30

    def test_missing_directory_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_STORE_CAP", "5")
        assert prune_store_to_cap(tmp_path / "does_not_exist") == 0

    def test_cap_bounds_todos_store(self, tmp_path, monkeypatch):
        """END-TO-END through the real writer: with the cap armed, saving more
        task records than the cap keeps the store bounded — and the LATEST write
        always survives (it's the newest). Deleting the prune_store_to_cap call
        in save_task_record reds this (the store grows past the cap)."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_STORE_CAP", "5")
        from trinity_local.council_runtime import create_prompt_bundle
        from trinity_local.task_runtime import create_task_record, save_task_record

        last_id = None
        for i in range(12):
            bundle = create_prompt_bundle(task_cluster_id=f"c{i}", task_text=f"task {i}")
            rec = create_task_record(bundle=bundle)
            save_task_record(rec)
            last_id = rec.task_id
            time.sleep(0.001)  # keep mtimes strictly increasing

        remaining = list(tasks_dir().glob("*.json"))
        assert len(remaining) <= 5, (
            f"store retention not enforced: {len(remaining)} records > cap 5 — "
            "the prune_store_to_cap call in save_task_record is missing"
        )
        # The most recent record must still be readable (never pruned).
        assert (tasks_dir() / f"{last_id}.json").exists()


def test_all_three_capped_stores_wired():
    """Static tripwire: all three high-churn store writers (todos / task_sync /
    prompt_bundles) call prune_store_to_cap. Guards against a future refactor
    silently dropping the retention hook from one of them."""
    import inspect

    from trinity_local import council_runtime, task_runtime

    sources = inspect.getsource(task_runtime) + inspect.getsource(council_runtime)
    assert sources.count("prune_store_to_cap(") >= 3, (
        "expected all three high-churn store writers (todos / task_sync / "
        "prompt_bundles) to call prune_store_to_cap"
    )
