"""The published test counts must come from a MEASURED run — or refuse.

THE BUG (found + fixed 2026-07-31). `scripts/render_docs.py` published
the headline "N tests passing + M skipped" that CLAUDE.md,
LAUNCH_CHECKLIST.md and launch-package.md all carry. It computed them
like this::

    result = pytest --collect-only -q
    total   = int(re.search(r"(\\d+) tests collected", result.stdout))
    skipped = int(re.search(r"(\\d+) skipped", ...)) if match else 4
    return total - skipped

``--collect-only`` never emits a skip summary — a skip is a RUNTIME
outcome, and collect-only runs nothing — so the second regex could never
match, the hardcoded ``4`` always fired, and the docs published
``collected - 4`` as an observed pass count. On the day of the fix the
docs said **4389 passing + 4 skipped**; a real ``pytest -q`` read
**3877 passed, 516 skipped** (exit 0). Both numbers were fiction, and
113 doc-consistency guards were green on them — because those guards
only ever compare doc-to-generator-or-doc-to-doc, never
generator-to-reality.

THE FIX. ``tests/conftest.py`` writes ``test-run-snapshot.json`` from the
terminal summary of every whole-suite run; the extractors read it and
RAISE (``UnmeasuredCountError``) when it is missing, red, degenerate, or
stale. There is no fallback constant anywhere in that path.

These tests are the green-gate pair for that claim (principle #35 /
docs/green-gate-checklist.md): the count FIRES on a real measurement and
is REFUSED on every degenerate input.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def render_docs_module():
    """Import scripts/render_docs.py without making scripts/ a package."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import render_docs  # type: ignore[import-not-found]

        return render_docs
    finally:
        sys.path.pop(0)


def _healthy_snapshot(collected: int) -> dict:
    return {
        "collected": collected,
        "selected": collected,
        "deselected": 0,
        "passed": collected - 7,
        "skipped": 7,
        "failed": 0,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
        "exit_status": 0,
        "trinity_slow": False,
        "invocation": "pytest -q (default shard)",
        "measured_at": "2026-07-31T00:00:00+00:00",
    }


def _write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "test-run-snapshot.json"
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return path


class TestRefusedOnDegenerateInput:
    """Every one of these used to produce a confident wrong number."""

    def test_missing_snapshot_refuses(self, render_docs_module, tmp_path):
        with pytest.raises(render_docs_module.UnmeasuredCountError) as exc:
            render_docs_module.load_run_snapshot(tmp_path / "nope.json")
        assert "missing" in str(exc.value)

    def test_unparseable_snapshot_refuses(self, render_docs_module, tmp_path):
        path = _write(tmp_path, "{not json")
        with pytest.raises(render_docs_module.UnmeasuredCountError):
            render_docs_module.load_run_snapshot(path)

    def test_snapshot_without_measured_fields_refuses(
        self, render_docs_module, tmp_path
    ):
        """The exact old failure mode: a source that carries no observed
        skip count must not yield one."""
        path = _write(tmp_path, {"collected": 4393})
        with pytest.raises(render_docs_module.UnmeasuredCountError) as exc:
            render_docs_module.load_run_snapshot(path)
        assert "skipped" in str(exc.value)

    def test_red_run_refuses(self, render_docs_module, tmp_path, monkeypatch):
        monkeypatch.setattr(
            render_docs_module, "_live_collected_count", lambda: 4393
        )
        snap = _healthy_snapshot(4393)
        snap.update({"failed": 3, "passed": snap["passed"] - 3, "exit_status": 1})
        path = _write(tmp_path, snap)
        with pytest.raises(render_docs_module.UnmeasuredCountError) as exc:
            render_docs_module.load_run_snapshot(path)
        assert "RED" in str(exc.value)

    def test_zero_passed_refuses(self, render_docs_module, tmp_path, monkeypatch):
        monkeypatch.setattr(
            render_docs_module, "_live_collected_count", lambda: 0
        )
        snap = _healthy_snapshot(7)
        snap.update({"collected": 0, "selected": 0, "passed": 0, "skipped": 0})
        path = _write(tmp_path, snap)
        with pytest.raises(render_docs_module.UnmeasuredCountError) as exc:
            render_docs_module.load_run_snapshot(path)
        assert "degenerate" in str(exc.value)

    def test_stale_snapshot_refuses(self, render_docs_module, tmp_path, monkeypatch):
        """A measurement taken before tests were added/removed no longer
        describes this tree, so it must not be published."""
        monkeypatch.setattr(
            render_docs_module, "_live_collected_count", lambda: 5000
        )
        path = _write(tmp_path, _healthy_snapshot(4393))
        with pytest.raises(render_docs_module.UnmeasuredCountError) as exc:
            render_docs_module.load_run_snapshot(path)
        assert "Stale" in str(exc.value)


class TestCountsComeFromTheMeasurement:
    """The published numbers must be the run's numbers, not constants."""

    def test_skipped_count_reads_the_measurement_not_a_constant(
        self, render_docs_module, tmp_path, monkeypatch
    ):
        """Pins the specific fabrication: the old code returned 4 for
        ANY input. Feed a snapshot saying 777 skipped and demand 777."""
        snap = _healthy_snapshot(4393)
        snap.update({"skipped": 777, "passed": 4393 - 777})
        path = _write(tmp_path, snap)
        monkeypatch.setattr(render_docs_module, "RUN_SNAPSHOT", path)
        monkeypatch.setattr(
            render_docs_module, "_live_collected_count", lambda: 4393
        )
        render_docs_module._run_snapshot_cached.cache_clear()
        try:
            assert render_docs_module.canonical_skipped_count() == 777
            assert render_docs_module.canonical_test_count() == 4393 - 777
            assert render_docs_module.canonical_collected_count() == 4393
        finally:
            render_docs_module._run_snapshot_cached.cache_clear()

    def test_no_fallback_constant_in_the_count_path(self, render_docs_module):
        """Structural anti-regression: the collect-only + `else 4` shape
        must never come back. A defaulted count is the bug."""
        source = (REPO_ROOT / "scripts" / "render_docs.py").read_text(
            encoding="utf-8"
        )
        # Only the comment block documenting the history may mention the
        # old shape; no live code may.
        code_lines = [
            line
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "skipped_match" not in code, (
            "render_docs.py is scraping a skip count out of "
            "`pytest --collect-only` output again. collect-only runs "
            "nothing, so it never reports skips — the fallback constant "
            "always wins and the doc publishes a number nobody observed."
        )


class TestSnapshotIsWrittenAndCoherent:
    """The measurement artifact itself must exist and add up.

    Deliberately NOT a doc-to-snapshot comparison: this guard runs inside
    the same run that WRITES the snapshot, so a doc-sync assertion here
    would deadlock (doc stale → suite red → renderer refuses a red run →
    doc can never be re-rendered). The doc-to-reality comparison lives at
    the publishing boundary instead — `scripts/render_docs.py --check`,
    wired as step 5 of `scripts/launch-check.sh`, which runs AFTER the
    suite has written a fresh snapshot.
    """

    def test_writer_and_reader_agree_on_the_path(self, render_docs_module):
        """Both sides of the contract, per the repo's boundary rule: the
        conftest hook WRITES and render_docs READS. If they ever point at
        different files the renderer silently reads a stale artifact."""
        from tests.conftest import RUN_SNAPSHOT_PATH

        assert RUN_SNAPSHOT_PATH == render_docs_module.RUN_SNAPSHOT

    def test_snapshot_exists(self):
        from tests.conftest import RUN_SNAPSHOT_PATH

        assert RUN_SNAPSHOT_PATH.exists(), (
            f"{RUN_SNAPSHOT_PATH.name} is missing. It is written by "
            f"tests/conftest.py at the end of a whole-suite run and is the "
            f"ONLY source for the published 'N tests passing' claim. Run "
            f"`TRINITY_HOME=$(mktemp -d) PYTHONPATH=src .venv/bin/python -m "
            f"pytest -q` once and it will appear."
        )

    def test_snapshot_counts_add_up(self):
        from tests.conftest import RUN_SNAPSHOT_PATH

        if not RUN_SNAPSHOT_PATH.exists():
            pytest.skip("covered by test_snapshot_exists")
        data = json.loads(RUN_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        outcomes = sum(
            data[k]
            for k in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
        )
        assert outcomes == data["selected"], (
            f"snapshot outcomes sum to {outcomes} but {data['selected']} tests "
            f"were selected — the recorded run does not account for every test, "
            f"so its published counts are not a complete measurement."
        )
        assert data["selected"] + data["deselected"] == data["collected"]
        assert data["passed"] > 0

    def test_snapshot_records_how_it_was_measured(self):
        """A count without its invocation is not a measurement: the same
        tree reads 3877/516 under `pytest -q` and a different split under
        `-m "not slow"`."""
        from tests.conftest import RUN_SNAPSHOT_PATH

        if not RUN_SNAPSHOT_PATH.exists():
            pytest.skip("covered by test_snapshot_exists")
        data = json.loads(RUN_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert data.get("invocation"), "snapshot lost its invocation label"
        assert isinstance(data.get("trinity_slow"), bool)
        assert data.get("measured_at")
