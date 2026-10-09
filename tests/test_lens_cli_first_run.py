"""`trinity-local lens` on a fresh install (found in a sandboxed run of the one-line installer,
2026-10-08): it built only from what the MCP server had already ingested, so a user with years
of history got "no prompts"; and on an empty corpus it still ran the post-build chain, spending a
chairman call to distill an empty lens and filing a dated copy of the placeholder.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.commands import me as me_cmd


def _args(**kw):
    return SimpleNamespace(dry_run=False, force=False, sample_size=10, k_basins=None, deep=False, legacy=False, **kw)


@pytest.fixture
def empty_build(monkeypatch):
    monkeypatch.setattr("trinity_local.embeddings.require_real_embedder", lambda: None)
    monkeypatch.setattr("trinity_local.me_builder.build_me_via_lens_pipeline",
                        lambda **k: ("/x/lens.md", {"skipped": True, "reason": "no_prompts"}))


def test_an_empty_corpus_runs_no_refresh_chain(empty_build, monkeypatch, capsys, patch_trinity_home):
    def boom(_dry):
        raise AssertionError("the refresh chain (and its chairman call) ran on an empty corpus")
    monkeypatch.setattr(me_cmd, "_post_build_hooks", boom)
    me_cmd.handle_me_build(_args())
    out = capsys.readouterr()
    assert '"reason": "no_prompts"' in out.out
    assert "No prompts to build from yet" in out.err and "degenerate" not in out.err


def test_lens_reads_new_transcripts_before_building(empty_build, monkeypatch, patch_trinity_home):
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "0")
    calls = []
    monkeypatch.setattr("trinity_local.stale_pass.run_stale_pass",
                        lambda trigger: calls.append(trigger) or {"ingest": {"added": 3}})
    monkeypatch.setattr(me_cmd, "_post_build_hooks", lambda _d: {})
    me_cmd.handle_me_build(_args())
    assert calls == ["lens"]


def test_tests_and_ci_never_ingest(empty_build, monkeypatch, patch_trinity_home):
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setattr("trinity_local.stale_pass.run_stale_pass",
                        lambda trigger: (_ for _ in ()).throw(AssertionError("ingested under the CI switch")))
    monkeypatch.setattr(me_cmd, "_post_build_hooks", lambda _d: {})
    me_cmd.handle_me_build(_args())


def test_a_running_pass_is_waited_for_then_lens_runs_its_own(empty_build, monkeypatch, patch_trinity_home):
    """verify (codex, 2026-10-08): with a background pass holding the lock, lens used to build at
    once, so transcripts written since were not guaranteed to be read."""
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "0")
    claims = iter([False, False, True])
    monkeypatch.setattr("trinity_local.stale_pass._try_claim_lock", lambda: next(claims))
    monkeypatch.setattr("trinity_local.stale_pass._release_lock", lambda: None)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []
    monkeypatch.setattr("trinity_local.stale_pass.run_stale_pass",
                        lambda trigger: calls.append(trigger) or {"ingest": {"added": 1}})
    monkeypatch.setattr(me_cmd, "_post_build_hooks", lambda _d: {})
    me_cmd.handle_me_build(_args())
    assert calls == ["lens"]
