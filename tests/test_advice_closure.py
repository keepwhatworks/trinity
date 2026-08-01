"""Advice-closure guards — a warning's recommended fix must be able to CLEAR it.

THE AUDIT CLASS (named 2026-07-04, two live instances found within 24h):
a staleness/health warning recommends a command, but the command's writers
don't touch the signal the checker reads — so the warning survives its own
advice forever, and the user learns to distrust the product's self-reports.

Instance 1 (fixed 2026-07-03): `status` said "corpus stale — run
`trinity-local ingest-recent`", but the checker read ONLY the background
stale-pass marker, which the CLI never writes. Guard:
tests/test_stale_pass.py::test_cli_ingest_cursors_clear_stale_without_marker.

Instance 2 (fixed 2026-07-04): `status` said vocabulary.md was stale and
recommended a refresh, but `trinity-local lens` — the natural refresh verb —
didn't write vocabulary.md (only dream's vocabulary phase did). Fixed by
folding the LLM-free vocabulary scan into the lens post-build hooks; guards
here + tests/test_distill.py::TestVocabularyFoldHooks.

The closure proof for lens_freshness decomposes as:
  (a) the check's fix names `trinity-local lens`          → asserted here
  (b) that handler invokes the writers                    → spy-proven in
      TestVocabularyFoldHooks (vocabulary) + the lens pipeline (topics.json)
  (c) refreshing the files those writers write clears the
      check                                               → asserted here

Instance 3 (pre-empted 2026-07-31): per-source ingest staleness computed from
the cursor's `last_mtime`. That field is a content watermark and the scan
boundary is inclusive, so a source whose newest file is old and fully drained
pins it forever — the live `gemini` source read "86.5 days behind" while being
perfectly current, and `trinity-local ingest-recent` could not move it by a
second. No surface had shipped that number yet; the guards below keep it that
way by pinning the clearable clock (`source_scan_ages`) as the one a freshness
surface may read. Full reproduction: tests/test_ingest_cursor_fixed_point.py.

When you add a NEW staleness warning with a `fix=` command, add its closure
test here.
"""
from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path


def _age(path, days: float) -> None:
    past = time.time() - days * 86400
    os.utime(path, (past, past))


class TestLensFreshnessAdviceClosure:
    def _seed(self, tmp_path):
        """Old vocabulary.md + topics.json, fresh council outcome → stale."""
        mem = tmp_path / "memories"
        mem.mkdir(parents=True, exist_ok=True)
        vocab = mem / "vocabulary.md"
        topics = mem / "topics.json"
        vocab.write_text("# Vocabulary\n", encoding="utf-8")
        topics.write_text(json.dumps({"basins": []}), encoding="utf-8")
        _age(vocab, 9)
        _age(topics, 9)
        outcomes = tmp_path / "council_outcomes"
        outcomes.mkdir(parents=True, exist_ok=True)
        (outcomes / "council_x.json").write_text("{}", encoding="utf-8")
        return vocab, topics

    def test_stale_fires_and_fix_names_a_command_that_writes_what_it_reads(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.health_checks import _check_lens_freshness
        vocab, topics = self._seed(tmp_path)

        stale = _check_lens_freshness()
        assert "predate" in stale.detail, stale.detail
        assert stale.fix == "trinity-local lens", (
            f"the lens_freshness fix is {stale.fix!r} — it must name the verb "
            "whose post-build hooks write vocabulary.md + topics.json, or the "
            "advice can never clear the warning"
        )
        # (b) source-coupling: the advised handler invokes the vocabulary
        # writer — via the extracted _post_build_hooks (Ousterhout closure,
        # 2026-07-05), so follow the chain: handler → hooks → writer.
        from trinity_local.commands import me as me_cmd
        handler_src = inspect.getsource(me_cmd.handle_me_build)
        assert "_post_build_hooks" in handler_src, (
            "handle_me_build no longer runs the post-build hooks — the "
            "lens_freshness fix advice points at a command that can't clear it"
        )
        hooks_src = inspect.getsource(me_cmd._post_build_hooks)
        assert "distill_vocabulary" in hooks_src, (
            "_post_build_hooks no longer calls distill_vocabulary — the "
            "lens_freshness fix advice points at a command that can't clear it"
        )

        # (c) refreshing the files the advised command's writers write → clears.
        now = time.time()
        os.utime(vocab, (now, now))
        os.utime(topics, (now, now))
        cleared = _check_lens_freshness()
        assert cleared.ok and "current" in cleared.detail, (cleared.ok, cleared.detail)

    def test_partial_refresh_still_warns(self, tmp_path, monkeypatch):
        """Refreshing only ONE of the two read paths must keep warning —
        the closure has to cover the checker's whole read set."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.health_checks import _check_lens_freshness
        vocab, topics = self._seed(tmp_path)
        now = time.time()
        os.utime(topics, (now, now))  # topics fresh, vocab still 9d old
        still = _check_lens_freshness()
        assert "vocabulary.md" in still.detail and "predate" in still.detail, still.detail


class TestIngestSourceFreshnessAdviceClosure:
    """Instance 3. Whichever number a future surface reports for "how far behind
    is this source", running the advised command must move it."""

    def _pinned_home(self, tmp_path, monkeypatch):
        """A source whose only transcript is old and already fully ingested."""
        home = tmp_path / "home"
        (home / ".claude" / "projects" / "proj").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("TRINITY_HOME", str(home / ".trinity"))
        sess = home / ".claude" / "projects" / "proj" / "s1.jsonl"
        sess.write_text(
            json.dumps({"type": "user", "sessionId": "s1",
                        "timestamp": "2026-05-06T13:48:00Z",
                        "message": {"role": "user", "content": "an old prompt"}}) + "\n",
            encoding="utf-8",
        )
        _age(sess, 86.5)
        return home

    def test_running_ingest_recent_clears_the_source_staleness(self, tmp_path, monkeypatch):
        """(a) the number exists, (b) the advised CLI verb — not some internal
        writer — is what moves it, (c) it lands at ~0."""
        from types import SimpleNamespace
        from trinity_local.commands.watch import handle_ingest_recent
        from trinity_local.incremental_ingest import source_scan_ages

        self._pinned_home(tmp_path, monkeypatch)

        # `trinity-local ingest-recent` IS the advice. Drive its handler.
        handle_ingest_recent(SimpleNamespace(sources=["claude"], deadline=10.0))
        first = source_scan_ages()["claude"]
        assert first < 60, f"the advised command left the source at {first}s stale"

        # And it keeps clearing it on a second run, when there is nothing new at
        # all — the case the watermark could never handle.
        handle_ingest_recent(SimpleNamespace(sources=["claude"], deadline=10.0))
        assert source_scan_ages()["claude"] < 60

    def test_the_unclearable_number_is_still_unclearable(self, tmp_path, monkeypatch):
        """The negative half, and the reason the separate clock exists: no
        amount of running the fix moves `last_mtime`, because the content did
        not change. Anyone tempted to report that field as freshness is
        reporting something their own advice cannot fix."""
        from types import SimpleNamespace
        from trinity_local.commands.watch import handle_ingest_recent
        from trinity_local.state_paths import ingest_cursors_path

        self._pinned_home(tmp_path, monkeypatch)
        for _ in range(3):
            handle_ingest_recent(SimpleNamespace(sources=["claude"], deadline=10.0))
        entry = json.loads(ingest_cursors_path().read_text(encoding="utf-8"))["claude"]
        assert time.time() - entry["last_mtime"] > 80 * 86400, (
            "if the watermark now advances past a drained boundary file, re-check "
            "that equal-mtime siblings can still be picked up before relaxing this"
        )

    def test_no_surface_derives_freshness_from_the_watermark(self):
        """Ratchet. `last_mtime` is a content watermark; only the ingest engine
        may touch it. A freshness surface reads source_scan_ages() instead."""
        src = Path(__file__).resolve().parent.parent / "src" / "trinity_local"
        allowed = {"incremental_ingest.py"}
        offenders = sorted(
            str(p.relative_to(src)) for p in src.rglob("*.py")
            if p.name not in allowed
            and "last_mtime" in p.read_text(encoding="utf-8", errors="replace")
        )
        assert not offenders, (
            "these modules read/write the ingest watermark `last_mtime`: "
            f"{offenders}. It cannot move past a fully-drained boundary file, so "
            "a staleness number derived from it can never be cleared by "
            "`trinity-local ingest-recent`. Use "
            "incremental_ingest.source_scan_ages()."
        )
