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

When you add a NEW staleness warning with a `fix=` command, add its closure
test here.
"""
from __future__ import annotations

import inspect
import json
import os
import time


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
