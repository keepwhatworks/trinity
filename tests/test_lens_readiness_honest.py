"""Nothing says the lens is ready when it is not (lens batch L2b, coherence review 2026-10-15).

- `lens-setup` ran its own ingest (no embeddings, a short deadline), ignored the build's result
  and printed "Your lens is ready" after a refused build; `lens` itself exited 0 after printing
  "Lens NOT rebuilt".
- The two background builds skipped require_real_embedder, and the first-lens build recorded
  "done" whatever the build returned.
- get_backend() said "mlx" whenever the module imported, model or not.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _lens_build(monkeypatch, summary):
    from trinity_local.commands import me
    monkeypatch.setattr("trinity_local.embeddings.require_real_embedder", lambda: None)
    monkeypatch.setattr(me, "build_me_via_lens_pipeline", lambda **kw: ("/tmp/lens.md", dict(summary)))
    monkeypatch.setattr(me, "_post_build_hooks", lambda dry_run: {})
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    return me


@pytest.mark.parametrize("summary,rc", [
    ({"tensions": 3}, 0),
    ({"skipped": True, "reason": "no_corpus_change"}, 0),
    ({"preserved_existing": True}, 1),
    ({"validation_failed": True}, 1),
    ({"skipped": True, "reason": "no_prompts"}, 1),
])
def test_lens_exit_code_says_whether_the_lens_landed(monkeypatch, summary, rc):
    me = _lens_build(monkeypatch, summary)
    assert me.handle_me_build(SimpleNamespace(deep=False, only_distill=False, dry_run=False, legacy=False,
                                              sample_size=80, k_basins=None, force=False)) == rc


def test_lens_setup_does_not_say_ready_after_a_refused_build(monkeypatch, capsys, patch_trinity_home):
    me = _lens_build(monkeypatch, {"preserved_existing": True})
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)
    assert me.handle_lens_setup(SimpleNamespace()) == 1
    out = capsys.readouterr()
    assert "your lens is ready" not in out.out.lower()


def test_lens_setup_says_ready_when_the_lens_landed(monkeypatch, capsys, patch_trinity_home):
    me = _lens_build(monkeypatch, {"tensions": 3})
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)
    assert me.handle_lens_setup(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "Your lens is ready" in out and "picks the winner" not in out


def _sync_kick(monkeypatch, cs, should):
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
    monkeypatch.setattr(cs, "_autoscan_disabled", lambda: False)
    monkeypatch.setattr(cs, should, lambda: (True, "test"))
    monkeypatch.setattr(cs, "_recently_kicked", lambda: False)
    monkeypatch.setattr(cs, "_try_claim_refresh_lock", lambda: True)
    monkeypatch.setattr(cs, "_release_refresh_lock", lambda: None)
    markers = []
    monkeypatch.setattr(cs, "_write_refresh_marker", lambda m: markers.append(dict(m)))

    class _Now:
        def __init__(self, target=None, **kw):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(cs.threading, "Thread", _Now)
    return markers


@pytest.mark.parametrize("kick,should", [("maybe_kick_lens_refresh", "should_refresh_lens"),
                                         ("maybe_kick_first_lens_build", "should_build_first_lens")])
def test_a_background_build_refuses_without_a_real_embedder(monkeypatch, patch_trinity_home, kick, should):
    import trinity_local.cold_start as cs
    from trinity_local.embeddings import EmbedderNotReadyError
    markers = _sync_kick(monkeypatch, cs, should)

    def not_ready():
        raise EmbedderNotReadyError("no model")
    monkeypatch.setattr("trinity_local.embeddings.require_real_embedder", not_ready)
    monkeypatch.setattr("trinity_local.me_builder.build_me_via_lens_pipeline",
                        lambda **kw: pytest.fail("built a lens without a real embedder"))
    getattr(cs, kick)()
    assert markers[-1]["status"] == "failed" and "download-embedder" in markers[-1]["error"]


def test_the_first_lens_build_does_not_record_done_for_a_refused_build(monkeypatch, patch_trinity_home):
    import trinity_local.cold_start as cs
    markers = _sync_kick(monkeypatch, cs, "should_build_first_lens")
    monkeypatch.setattr("trinity_local.embeddings.require_real_embedder", lambda: None)
    monkeypatch.setattr("trinity_local.me_builder.build_me_via_lens_pipeline",
                        lambda **kw: ("/tmp/lens.md", {"preserved_existing": True}))
    cs.maybe_kick_first_lens_build()
    assert markers[-1]["status"] != "done"


@pytest.mark.parametrize("loaded,expect_v3", [(False, False), (True, True)])
def test_takeout_grouping_uses_embeddings_only_when_real_vectors_come_out(monkeypatch, tmp_path, loaded, expect_v3):
    """ingest grouped Gemini Takeout cells by embedding whenever get_backend() said "mlx",
    which it says on import alone; with no model the vectors were TF-IDF hashes."""
    import trinity_local.embeddings as E
    import trinity_local.ingest as ingest
    from tests.test_ingest_parsers import GEMINI_FIXTURE_HTML
    used = []
    monkeypatch.setattr(ingest, "_group_cells_by_embedding",
                        lambda cells: used.append("v3") or [[c] for c in cells])
    monkeypatch.setattr(E, "_backend_name", "mlx")
    monkeypatch.setattr(E, "mlx_actually_loaded", lambda: loaded)
    path = tmp_path / "MyActivity.html"
    path.write_text(GEMINI_FIXTURE_HTML, encoding="utf-8")
    sessions = list(ingest.parse_gemini_takeout_html(path))
    assert sessions, "the fixture must parse, or this test proves nothing"
    assert (used == ["v3"]) is expect_v3
