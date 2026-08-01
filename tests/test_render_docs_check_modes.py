"""`render_docs --check` must OBSERVE drift, not repair it — and must not
deadlock the suite that produces its measurement.

Two defects found 2026-07-31 by running the (then brand-new) measured-count
renderer rather than reading it:

1. **`--check` wrote the files it was checking.** `main()` called
   `render_file(path, values)` with the write unconditional, so the
   "read-only" verification step re-rendered every drifted doc and *then*
   reported the drift it had just erased. A second `--check` passed. A
   verifier that repairs what it measures can only ever fail once.

2. **Red was an ABSORBING state.** The measured counts read
   `test-run-snapshot.json`, which `tests/conftest.py` writes from the
   terminal summary of the *currently executing* run. Plain `--check`
   refuses on a red snapshot — correct at the publishing boundary. But
   `TestCanonicalPlaceholdersAreRendered::test_render_docs_check_exits_clean`
   invokes it from INSIDE the suite, so: run N is red → run N writes a red
   snapshot → run N+1's renderer refuses → that test fails → run N+1 is red
   → forever. Observed live: the suite could not return to green without
   hand-deleting the snapshot.

The fix keeps the strict gate where publishing happens (`--check`, step 5
of `launch-check.sh`) and gives the in-suite caller `--allow-unmeasured`,
which reports the counts as UNMEASURED and checks only what the tree can
compute on its own. These tests pin both halves, because the loosening is
only safe if the tolerant mode is still a real gate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def render_docs_module():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import render_docs  # type: ignore[import-not-found]

        return render_docs
    finally:
        sys.path.pop(0)


def _red_snapshot(tmp_path: Path, collected: int) -> Path:
    """A snapshot from a run that FAILED — exactly what an in-suite caller
    reads on the run after any red run."""
    path = tmp_path / "test-run-snapshot.json"
    path.write_text(
        json.dumps(
            {
                "collected": collected,
                "selected": collected,
                "deselected": 0,
                "passed": collected - 10,
                "skipped": 5,
                "failed": 5,
                "errors": 0,
                "xfailed": 0,
                "xpassed": 0,
                "exit_status": 1,
                "trinity_slow": False,
                "invocation": "pytest -q (default shard)",
                "measured_at": "2026-07-31T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def isolated_render(render_docs_module, tmp_path, monkeypatch):
    """Run main() against a red snapshot and ONE throwaway doc.

    Never touches the repo's real docs, the real snapshot, or ~/.trinity.
    """
    mod = render_docs_module
    COLLECTED = 4321
    snap = _red_snapshot(tmp_path, COLLECTED)
    monkeypatch.setattr(mod, "RUN_SNAPSHOT", snap)
    monkeypatch.setattr(mod, "_live_collected_count", lambda: COLLECTED)
    # Evidence claims read ~/.trinity; stub them out so this test measures
    # the count path only.
    monkeypatch.setattr(mod, "evidence_state", lambda: ("verified", {}, "stubbed"))
    # A minimal canonical set: one extractor that REFUSES (no green run) and
    # one that always computes from the tree.
    monkeypatch.setattr(
        mod,
        "CANONICAL",
        {"test_count": mod.canonical_test_count, "version": mod.canonical_version},
    )
    doc = tmp_path / "doc.md"
    monkeypatch.setattr(mod, "docs_with_placeholders", lambda: [doc])
    mod._run_snapshot_cached.cache_clear()
    yield mod, doc, mod.canonical_version()
    mod._run_snapshot_cached.cache_clear()


def _run(mod, monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["render_docs.py", *argv])
    return mod.main()


class TestCheckIsReadOnly:
    """Defect 1: the verifier must not repair what it measures."""

    def test_render_file_write_false_reports_drift_without_writing(
        self, render_docs_module, tmp_path
    ):
        doc = tmp_path / "doc.md"
        original = "v <!-- canonical:version -->0.0.0-stale<!-- /canonical -->\n"
        doc.write_text(original, encoding="utf-8")

        changed, count = render_docs_module.render_file(
            doc, {"version": "9.9.9"}, write=False
        )

        assert changed is True, "drift must still be REPORTED in read-only mode"
        assert count == 1
        assert doc.read_text(encoding="utf-8") == original, (
            "--check rewrote the file it was checking; the next --check would "
            "pass and the drift would never be reported again"
        )

    def test_render_file_write_true_still_writes(
        self, render_docs_module, tmp_path
    ):
        """The read-only mode must be opt-in, not a silent no-op renderer."""
        doc = tmp_path / "doc.md"
        doc.write_text(
            "v <!-- canonical:version -->0.0.0-stale<!-- /canonical -->\n",
            encoding="utf-8",
        )

        changed, _ = render_docs_module.render_file(doc, {"version": "9.9.9"})

        assert changed is True
        assert "9.9.9" in doc.read_text(encoding="utf-8")

    def test_check_mode_leaves_a_drifted_doc_untouched(
        self, isolated_render, monkeypatch
    ):
        """End-to-end through main(): --check on a drifted doc must report
        exit 1 AND leave the bytes alone."""
        mod, doc, _real_version = isolated_render
        original = "v <!-- canonical:version -->0.0.0-stale<!-- /canonical -->\n"
        doc.write_text(original, encoding="utf-8")

        rc = _run(mod, monkeypatch, "--check", "--allow-unmeasured")

        assert rc == 1, "drift in a computable placeholder must fail --check"
        assert doc.read_text(encoding="utf-8") == original


class TestUnmeasuredIsNotDrift:
    """Defect 2: strict at the publishing boundary, non-absorbing in-suite."""

    def test_strict_check_refuses_when_counts_are_unmeasured(
        self, isolated_render, monkeypatch, capsys
    ):
        """The publishing gate (launch-check.sh step 5) passes no flag: a
        red/absent measurement must NOT be publishable."""
        mod, doc, real_version = isolated_render
        doc.write_text(
            f"v <!-- canonical:version -->{real_version}<!-- /canonical -->\n",
            encoding="utf-8",
        )

        rc = _run(mod, monkeypatch, "--check")

        assert rc == 1, (
            "a red measurement must block publishing even when no doc drifted"
        )
        out = capsys.readouterr()
        assert "UNMEASURED" in out.out
        assert "test_count" in out.out

    def test_allow_unmeasured_lets_a_red_run_return_to_green(
        self, isolated_render, monkeypatch
    ):
        """The anti-deadlock property, stated as a test: with a RED snapshot
        on disk and no doc drift, the in-suite invocation exits 0. If this
        ever exits 1 again, one red run permanently pins the suite red."""
        mod, doc, real_version = isolated_render
        doc.write_text(
            f"v <!-- canonical:version -->{real_version}<!-- /canonical -->\n",
            encoding="utf-8",
        )

        rc = _run(mod, monkeypatch, "--check", "--allow-unmeasured")

        assert rc == 0, (
            "red snapshot + clean docs must not fail the in-suite check — that "
            "makes red an absorbing state the suite cannot leave"
        )

    def test_unmeasured_counts_are_named_not_silently_skipped(
        self, isolated_render, monkeypatch, capsys
    ):
        """Tolerance must be LOUD. An unrendered count must never read as a
        rendered-and-agreeing one."""
        mod, doc, real_version = isolated_render
        doc.write_text(
            f"v <!-- canonical:version -->{real_version}<!-- /canonical -->\n",
            encoding="utf-8",
        )

        _run(mod, monkeypatch, "--check", "--allow-unmeasured")

        out = capsys.readouterr().out
        assert "UNMEASURED" in out
        assert "test_count" in out
        assert "keep whatever value is already on disk" in out

    def test_unmeasured_placeholder_keeps_its_on_disk_value(
        self, isolated_render, monkeypatch
    ):
        """An unmeasured count must not be planted from anywhere — the
        placeholder stays byte-identical (same contract as a REFUSED
        evidence claim)."""
        mod, doc, real_version = isolated_render
        original = (
            f"v <!-- canonical:version -->{real_version}<!-- /canonical -->\n"
            "t <!-- canonical:test_count -->4389<!-- /canonical -->\n"
        )
        doc.write_text(original, encoding="utf-8")

        rc = _run(mod, monkeypatch, "--allow-unmeasured")

        assert rc == 0
        assert doc.read_text(encoding="utf-8") == original, (
            "the unmeasured count was rewritten; only a real measurement may "
            "set it"
        )


class TestSlowShardSnapshotIsRefusedWithTheRealReason:
    """A `TRINITY_SLOW=1` snapshot must be refused BY NAME, not misreported as
    staleness.

    Found 2026-08-01 running the slow shard for the first time. It collects a
    different set (4582 vs the default 4570), and the canonical claim in
    CLAUDE.md is explicitly `pytest -q` — the DEFAULT shard. The staleness check
    compares against `_live_collected_count()`, which collects WITHOUT
    TRINITY_SLOW, so a slow snapshot always mismatched it and the reader was
    told "tests were added or removed since the last run". That is false, and it
    sends someone hunting a diff that does not exist. A refusal that names the
    wrong cause is only marginally better than no refusal — the whole point of
    this file's gate is that a person can act on what it says.
    """

    def _snapshot(self, tmp_path, **over):
        import json

        base = {
            "collected": 4582, "selected": 4582, "deselected": 0,
            "passed": 4560, "failed": 0, "errors": 0, "skipped": 22,
            "xfailed": 0, "xpassed": 0, "exit_status": 0,
            "measured_at": "2026-08-01T00:00:00+00:00",
            "invocation": "TRINITY_SLOW=1 pytest -q (full shard)",
            "trinity_slow": True,
        }
        base.update(over)
        p = tmp_path / "snap.json"
        p.write_text(json.dumps(base), encoding="utf-8")
        return p

    def test_slow_snapshot_is_refused_and_says_why(self, tmp_path):
        import sys

        sys.path.insert(0, "scripts")
        import render_docs as mod

        with pytest.raises(mod.UnmeasuredCountError) as exc:
            mod.load_run_snapshot(self._snapshot(tmp_path))
        msg = str(exc.value)
        assert "SLOW shard" in msg, "the refusal must name the shard as the cause"
        assert "TRINITY_SLOW=1" in msg, "it must quote the invocation it found"
        assert "pytest -q" in msg, "it must prescribe the default-shard re-run"
        assert "added or removed" not in msg, (
            "the slow shard must NOT be misreported as tests being added/removed "
            "— that is the misdiagnosis this guard exists to prevent"
        )

    def test_a_default_shard_snapshot_still_loads(self, tmp_path):
        """The refusal must key on the shard, not on any full-suite run — a
        normal green measurement has to keep working."""
        import sys

        sys.path.insert(0, "scripts")
        import render_docs as mod

        snap = self._snapshot(
            tmp_path, trinity_slow=False,
            invocation="pytest -q (default shard)",
            collected=mod._live_collected_count(),
        )
        data = mod.load_run_snapshot(snap)
        assert data["exit_status"] == 0 and data["passed"] > 0
