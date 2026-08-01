"""Guards for `scripts/refilter_prompt_store.py` — the only script in this repo
that can delete rows from the user's prompt corpus.

WHY IT EXISTS. The script closes a real defect (amd_0047: the ingest purity
filter is apply-once with no backfill, leaving 6,718 of 53,427 live lines that
today's filter would reject). But the fix is destructive, and the failure mode
that matters is not "it deleted too little" — it is a filter regression causing
it to delete most of a corpus, or a test fixture being mistaken for the real
store. Both refusals below exist for that, so both are tested here, in the
direction that proves the refusal RATHER than the happy path.

Every test asserts the STORE ON DISK, not the exit message. A refusal that
prints the right words while still writing is the exact producer-asserted /
consumer-unverified shape this repo keeps finding.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "refilter_prompt_store.py"


def _write_store(home: Path, rows: list[dict]) -> Path:
    d = home / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "prompt_nodes.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def _run(home: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(REPO),
        env={"PATH": "/usr/bin:/bin", "TRINITY_HOME": str(home),
             "PYTHONPATH": str(REPO / "src")},
    )


def _human(i: int) -> dict:
    return {"id": f"h{i}", "text": f"a genuine question about the widget numbered {i}"}


def _machine(i: int) -> dict:
    return {"id": f"m{i}", "text": f"<system-reminder>injected block {i}</system-reminder>"}


def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path):
    rows = [_human(i) for i in range(1200)] + [_machine(i) for i in range(200)]
    store = _write_store(tmp_path, rows)
    before = store.read_bytes()
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "DRY RUN" in r.stdout
    assert store.read_bytes() == before, "default run modified the store — it must not"
    assert not list(store.parent.glob("*.bak-*")), "dry run created a backup"


def test_refuses_a_store_too_small_to_be_real(tmp_path: Path):
    """A 50-row store is a fixture, not the corpus. Pruning it is never right."""
    store = _write_store(tmp_path, [_human(i) for i in range(50)])
    before = store.read_bytes()
    r = _run(tmp_path, "--apply")
    assert r.returncode == 2
    assert "MIN_STORE_LINES" in r.stdout
    assert store.read_bytes() == before, "refused but wrote anyway"


def test_refuses_when_the_filter_would_eat_the_corpus(tmp_path: Path):
    """The regression guard: if the filter suddenly rejects most rows, that is a
    broken filter, not a dirty corpus. Deleting on that signal is the disaster
    this refusal exists to prevent."""
    rows = [_machine(i) for i in range(1800)] + [_human(i) for i in range(400)]
    store = _write_store(tmp_path, rows)
    before = store.read_bytes()
    r = _run(tmp_path, "--apply")
    assert r.returncode == 2
    assert "MAX_REJECT_FRACTION" in r.stdout
    assert store.read_bytes() == before, "refused but wrote anyway"
    assert not list(store.parent.glob("*.bak-*"))


def test_apply_removes_only_machine_rows_and_backs_up_first(tmp_path: Path):
    """The happy path, asserted on CONTENT rather than counts: every surviving
    row must be one the filter accepts, and the backup must hold the original."""
    rows = [_human(i) for i in range(1200)] + [_machine(i) for i in range(100)]
    store = _write_store(tmp_path, rows)
    r = _run(tmp_path, "--apply")
    assert r.returncode == 0, r.stderr

    kept = [json.loads(x) for x in store.read_text().splitlines() if x.strip()]
    assert len(kept) == 1200
    assert all(k["id"].startswith("h") for k in kept), "a machine row survived"

    backups = list(store.parent.glob("*.bak-*"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"
    restored = [json.loads(x) for x in backups[0].read_text().splitlines() if x.strip()]
    assert len(restored) == 1300, "backup does not hold the pre-prune store"


def test_preserves_embeddings_on_kept_rows(tmp_path: Path):
    """Re-embedding 46k rows would be expensive and would silently change the
    vector space every downstream instrument reads. Kept rows must come through
    byte-identical."""
    rows = [dict(_human(i), embedding=[0.5, -0.25, float(i)]) for i in range(1200)]
    rows += [_machine(i) for i in range(50)]
    store = _write_store(tmp_path, rows)
    assert _run(tmp_path, "--apply").returncode == 0
    kept = [json.loads(x) for x in store.read_text().splitlines() if x.strip()]
    assert len(kept) == 1200
    assert kept[7]["embedding"] == [0.5, -0.25, 7.0]
