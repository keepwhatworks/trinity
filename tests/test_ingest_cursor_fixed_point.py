"""A drained source must not look stale forever.

THE DEFECT (verified 2026-07-31 against the real ~/.trinity). The per-source
cursor stores `last_mtime`, a WATERMARK over transcript mtimes, and the scan
boundary is inclusive (`>=` — batch-written siblings share an mtime and a strict
`>` silently loses them, see watch_runtime._iter_recent_paths). Put those two
together on a source whose newest file is old and fully drained and you get a
FIXED POINT:

    ~/.gemini/tmp/trinity-local/chats/session-2026-05-06T13-48-1ba7229b.json
    file mtime  1778075695.1382565
    cursor      1778075695.1382565   ← equal to the microsecond

The file is returned on every pass (>= its own mtime), yields nothing, and so
max(cursor, its mtime) cannot move the cursor past it. Ever. Anything reading
`last_mtime` as freshness reported that source as "86.5 days behind" while it
was perfectly current — and no amount of running ingest could change the number.

TWO consequences, both guarded here:
  1. the reported staleness is a lie a user cannot clear (see also
     tests/test_advice_closure.py — this repo has a standing rule that a
     warning must be clearable by its own fix command);
  2. `ingest_recent` orders sources most-stale-first to avoid starving the tail
     of the list, so a permanently-pinned source permanently owned the front of
     that queue no matter how recently it had been walked.

THE FIX IS NOT to advance the watermark past the boundary file: the inclusive
boundary is deliberate, and the drained_path/drained_size record only remembers
ONE file, so it cannot vouch for unseen siblings sharing that mtime. The fix is
to stop asking the watermark a question it cannot answer — `scanned_at` records
when we last WALKED a source, which is what "fresh" actually means.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from trinity_local import incremental_ingest as II
from trinity_local.incremental_ingest import ingest_recent, source_scan_ages
from trinity_local.state_paths import ingest_cursors_path

_AGE_DAYS = 86.5
_AGE_S = _AGE_DAYS * 86400


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """HOME and TRINITY_HOME both isolated — this drives the REAL glob, the
    REAL parser and the REAL cursor file, no mocks."""
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "proj").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRINITY_HOME", str(home / ".trinity"))
    return home


def _drained_session(home) -> "os.PathLike":
    """One real claude-code session, fully ingested, last written 86.5 days ago:
    the exact shape of the pinned `gemini` source on the live machine."""
    sess = home / ".claude" / "projects" / "proj" / "s1.jsonl"
    sess.write_text(
        json.dumps({"type": "user", "sessionId": "s1",
                    "timestamp": "2026-05-06T13:48:00Z",
                    "message": {"role": "user", "content": "split the ingest counter"}}) + "\n"
        + json.dumps({"type": "assistant", "sessionId": "s1",
                      "timestamp": "2026-05-06T13:48:05Z",
                      "message": {"role": "assistant", "model": "claude-opus-4",
                                  "content": [{"type": "text", "text": "ok"}]}}) + "\n",
        encoding="utf-8",
    )
    old = time.time() - _AGE_S
    os.utime(sess, (old, old))
    return sess


def _entry(source: str) -> dict:
    return json.loads(ingest_cursors_path().read_text(encoding="utf-8"))[source]


class TestTheFixedPointItself:
    def test_watermark_is_pinned_but_freshness_is_not(self, isolated_home):
        """Reproduce the fixed point, then assert the honest number survives it.

        The watermark staying put is CORRECT (it is a content watermark and the
        content did not change) — so this test pins both halves: the watermark
        does not move, and the freshness signal is nonetheless current."""
        sess = _drained_session(isolated_home)
        file_mtime = sess.stat().st_mtime

        first = ingest_recent(sources=["claude"], deadline_s=10.0)
        assert first.added == 1, first.to_dict()

        second = ingest_recent(sources=["claude"], deadline_s=10.0)
        assert second.added == 0
        # (1) the fixed point is real: the watermark is exactly the file's mtime
        #     and a second pass cannot push it further.
        assert _entry("claude")["last_mtime"] == pytest.approx(file_mtime, abs=1e-6)
        assert time.time() - _entry("claude")["last_mtime"] > 80 * 86400, (
            "fixture no longer reproduces an old-and-drained source"
        )
        # (2) and yet the source is current, because we just walked it.
        ages = source_scan_ages()
        assert ages["claude"] < 60, (
            f"a source walked seconds ago reads as {ages['claude'] / 86400:.1f} days "
            "stale — the freshness signal is still reading the pinned watermark"
        )

    def test_the_boundary_file_is_still_returned_every_pass(self, isolated_home):
        """Guards the premise. If the inclusive boundary were quietly changed to
        `>`, this whole fix would be solving a problem that no longer exists —
        and the equal-mtime sibling loss it protects against would be back."""
        from trinity_local import watch_runtime

        sess = _drained_session(isolated_home)
        ingest_recent(sources=["claude"], deadline_s=10.0)
        cursor = _entry("claude")["last_mtime"]
        assert sess in list(watch_runtime._iter_recent_paths("claude", cursor)), (
            "the boundary file is no longer re-listed at its own mtime; "
            "re-check whether this fixed point can still occur"
        )

    def test_freshness_is_reported_even_when_there_was_nothing_to_ingest(
        self, isolated_home
    ):
        """The degenerate case that matters most: a source with NO files at all.
        Walking it is still evidence that we looked, and it must not read as
        infinitely stale (the never-seen 0.0 watermark)."""
        result = ingest_recent(sources=["claude"], deadline_s=10.0)
        assert result.scanned == 0, "expected an empty source for this case"
        assert source_scan_ages()["claude"] < 60


class TestQueuePosition:
    """`ingest_recent` walks sources most-stale-first under a shared deadline.
    A source that can never stop looking stale permanently owns the front."""

    def _order(self, monkeypatch, seed: dict) -> list[str]:
        path = ingest_cursors_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seed), encoding="utf-8")
        seen: list[str] = []
        import trinity_local.watch_runtime as W

        def fake_iter(source, last_mtime):
            seen.append(source)
            return iter(())

        monkeypatch.setattr(W, "_iter_recent_paths", fake_iter)
        II.ingest_recent(sources=sorted(seed), deadline_s=30.0)
        return seen

    def test_a_drained_source_walked_just_now_yields_the_front(
        self, isolated_home, monkeypatch
    ):
        now = time.time()
        order = self._order(monkeypatch, {
            # pinned: ancient watermark, but we walked it a second ago
            "pinned": {"last_mtime": now - _AGE_S, "scanned_at": now - 1},
            # genuinely neglected: walked 13 days ago
            "neglected": {"last_mtime": now - 60, "scanned_at": now - 13 * 86400},
        })
        assert order[0] == "neglected", (
            f"the pinned source still owns the front of the queue; got {order}"
        )

    def test_a_source_the_deadline_cut_short_keeps_its_place(
        self, isolated_home, monkeypatch, tmp_path
    ):
        """Only a COMPLETED walk may stamp scanned_at. Otherwise a source with a
        backlog too big for one deadline would be demoted after each partial
        pass — reintroducing the starvation the ordering exists to prevent."""
        import trinity_local.watch_runtime as W

        files = []
        for i in range(20):
            f = tmp_path / f"p{i}.jsonl"
            f.write_text("{}\n", encoding="utf-8")
            files.append(f)

        monkeypatch.setattr(
            W, "_iter_recent_paths",
            lambda source, since: iter(files) if source == "backlog" else iter(()))

        def _slow(source, path):
            time.sleep(0.05)
            return None

        monkeypatch.setattr(W, "_parse_source_path", _slow)

        result = II.ingest_recent(sources=["backlog"], deadline_s=0.2)
        assert result.deadline_hit is True, "fixture failed to blow the deadline"
        assert "scanned_at" not in _entry("backlog"), (
            "a source cut short mid-walk was stamped as fully scanned — it will "
            "be demoted in the queue while it still has unread files"
        )

    def test_legacy_cursors_without_scanned_at_keep_the_old_ordering(
        self, isolated_home, monkeypatch
    ):
        """Backwards compatibility: on a cursors.json written before this field
        existed, ordering must fall back to the watermark rather than treating
        every source as never-scanned (which would flatten to name order)."""
        now = time.time()
        order = self._order(monkeypatch, {
            "aaa_fresh": {"last_mtime": now - 60},
            "zzz_stale": {"last_mtime": now - 13 * 86400},
        })
        assert order[0] == "zzz_stale", order


def test_scan_ages_survives_a_corrupt_cursors_file(isolated_home):
    """guard_shape_not_just_parse, same as _load_cursors/_load_drained: this
    reads on any surface that reports freshness and must degrade, not raise."""
    path = ingest_cursors_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for bad in ("[1,2,3]", "null", '"nope"', "{oops"):
        path.write_text(bad, encoding="utf-8")
        assert source_scan_ages() == {}
