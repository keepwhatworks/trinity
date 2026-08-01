"""Ingest must not starve the sources at the end of the list.

WHY THIS EXISTS. `ingest_recent` walks `sources` in order under a SHARED deadline (60s
when fired from stale_pass) and breaks when it expires. With a fixed order, the sources
at the front drain on every pass and the ones at the back never run.

Measured 2026-07-26 on the real corpus:

    claude / codex / antigravity cursors    0 days behind
    browser_claude / chatgpt / gemini      13 days behind, all frozen at the SAME minute

That same minute was the last MANUAL ingest, not any gated pass. The usage gate
(`maybe_kick_stale_pass`, fired on council launch and every MCP tool call) had been
working correctly the entire time. Its budget simply never reached the tail of
DEFAULT_SOURCES, where all three browser_* sources live. Thirteen days of claude.ai,
chatgpt and gemini sessions sat captured on disk and outside the corpus.

The fix orders sources most-stale-first, so the budget follows the need. These guards
pin the ORDERING PROPERTY rather than the sort expression, so they survive a rewrite of
how staleness is computed but red the moment a fixed order comes back.
"""
from __future__ import annotations

import time


from trinity_local import incremental_ingest as II


def _order(monkeypatch, tmp_path, cursors, sources=None):
    """Run ingest_recent far enough to capture the source order it chose.

    TRINITY_HOME is isolated because `_save_cursors` is NOT stubbed: this runs
    the real end of ingest_recent, so without isolation these fixtures would
    overwrite the developer's own ~/.trinity/prompts/cursors.json with fake
    sources and fake timestamps."""
    seen: list[str] = []
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setattr(II, "_load_cursors", lambda: dict(cursors))
    monkeypatch.setattr(II, "_load_drained", lambda: {})
    monkeypatch.setattr(II, "_existing_prompt_node_ids", lambda: set())

    def fake_iter(source, last_mtime):
        seen.append(source)
        return iter(())

    import trinity_local.watch_runtime as W
    monkeypatch.setattr(W, "_iter_recent_paths", fake_iter, raising=False)
    II.ingest_recent(sources=list(sources) if sources else None, deadline_s=30.0)
    return seen


class TestMostStaleFirst:
    def test_a_starved_source_is_visited_before_a_current_one(self, monkeypatch, tmp_path):
        """The exact production shape: browser_* stale by days, CLI current."""
        now = time.time()
        cursors = {
            "claude": now - 60,          # current
            "codex": now - 60,
            "antigravity": now - 60,
            "browser_claude": now - 13 * 86400,   # starved
            "browser_chatgpt": now - 13 * 86400,
            "browser_gemini": now - 13 * 86400,
        }
        order = _order(monkeypatch, tmp_path, cursors, sources=list(cursors))
        first_browser = min(order.index(s) for s in order if s.startswith("browser_"))
        first_cli = min(order.index(s) for s in ("claude", "codex", "antigravity")
                        if s in order)
        assert first_browser < first_cli, (
            f"starved browser sources must precede current CLI sources; got {order}"
        )

    def test_a_never_seen_source_sorts_first(self, monkeypatch, tmp_path):
        """No cursor means everything to gain. It must not queue behind a source
        that is already up to date."""
        now = time.time()
        cursors = {"claude": now - 10}          # 'newcomer' absent entirely
        order = _order(monkeypatch, tmp_path, cursors, sources=["claude", "newcomer"])
        assert order[0] == "newcomer", order

    def test_ordering_is_deterministic_for_equal_staleness(self, monkeypatch, tmp_path):
        """Equal cursors must not produce a random walk — tests and cursor
        bookkeeping both depend on a stable order."""
        now = time.time()
        cursors = {s: now - 100 for s in ("b_two", "a_one", "c_three")}
        runs = [_order(monkeypatch, tmp_path, cursors, sources=["c_three", "a_one", "b_two"])
                for _ in range(3)]
        assert runs[0] == runs[1] == runs[2], runs
        assert runs[0] == sorted(runs[0]), f"expected name tie-break, got {runs[0]}"

    def test_the_default_source_list_is_not_walked_in_declaration_order(self, monkeypatch, tmp_path):
        """Mutation-sensitive: delete the sort and this reds, because DEFAULT_SOURCES
        declares the browser sources last and this fixture makes them the stalest."""
        now = time.time()
        cursors = {s: now - 5 for s in II.DEFAULT_SOURCES}
        cursors["browser_gemini"] = now - 30 * 86400
        order = _order(monkeypatch, tmp_path, cursors)
        assert order[0] == "browser_gemini", (
            f"the stalest source must lead regardless of declaration order; got {order[:3]}"
        )
        assert list(II.DEFAULT_SOURCES).index("browser_gemini") == len(II.DEFAULT_SOURCES) - 1, (
            "fixture assumes browser_gemini is declared LAST; if that changed, this "
            "test no longer discriminates and must be re-fixtured"
        )


class TestStarvationIsActuallyPossible:
    def test_the_deadline_break_still_exists(self):
        """The reordering only matters because the loop CAN exit early. If the break
        were removed, this guard should be reconsidered rather than silently kept."""
        import inspect

        src = inspect.getsource(II.ingest_recent)
        assert "deadline_hit = True" in src and "break" in src, (
            "ingest_recent no longer exits early on the deadline; the starvation "
            "premise for most-stale-first ordering needs re-checking"
        )
