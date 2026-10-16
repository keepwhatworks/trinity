"""A core the gate can never replace is "kept", not "stale", and costs no call (lens batch L2,
coherence review 2026-10-15).

While the core gate's ruler is length-confounded and the stance admitter is off, every draft
after the first core.md is refused. Each `lens` still paid a flagship call to write that draft,
and `status` / the launchpad called the core stale and advised `lens --only-distill`, which
could not clear it. The no-corpus refusal was also archived under the label "admitted".
"""
from __future__ import annotations

import os
import time

import pytest


@pytest.fixture
def stale_core(patch_trinity_home):
    from trinity_local.state_paths import core_path, lens_path
    core_path().parent.mkdir(parents=True, exist_ok=True)
    core_path().write_text("You ship leverage over polish.\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(core_path(), (old, old))
    lens_path().parent.mkdir(parents=True, exist_ok=True)
    lens_path().write_text("# Lens\n\n## A tension\n", encoding="utf-8")
    return core_path()


def _no_call(monkeypatch):
    import trinity_local.providers as P
    monkeypatch.setattr(P, "make_provider", lambda *a, **k: pytest.fail("a flagship call was made"))


def test_a_frozen_core_spends_no_call(stale_core, monkeypatch):
    from trinity_local.distill import core_freshness, distill_via_chairman
    monkeypatch.delenv("TRINITY_STANCE_ADMITTER", raising=False)
    _no_call(monkeypatch)
    assert core_freshness()["state"] == "frozen"
    out = distill_via_chairman()
    assert out["skipped"] and out["frozen"]
    assert stale_core.read_text(encoding="utf-8") == "You ship leverage over polish.\n"


def test_with_the_stance_admitter_armed_the_core_is_stale_again(stale_core, monkeypatch):
    from trinity_local.distill import core_freshness
    monkeypatch.setenv("TRINITY_STANCE_ADMITTER", "1")
    assert core_freshness()["state"] == "stale"


def test_status_says_kept_and_its_advice_clears(stale_core, monkeypatch):
    """Advice closure: status names a fix (delete core.md, run `lens`) that works -- the next
    distill writes a first core, which the gate admits. The old advice could not clear it."""
    from trinity_local.commands.status import _core_md_lines
    from trinity_local.core_gate import propose_core
    from trinity_local.distill import core_freshness
    monkeypatch.delenv("TRINITY_STANCE_ADMITTER", raising=False)
    out = "\n".join(_core_md_lines())
    assert "kept" in out and "delete ~/.trinity/core.md" in out
    assert "stale" not in out and "--only-distill" not in out
    stale_core.unlink()
    assert core_freshness()["state"] == "missing"
    assert propose_core("A new first core.").admitted is True


def test_a_no_corpus_refusal_is_not_archived_as_admitted(patch_trinity_home):
    from trinity_local.core_gate import propose_core
    from trinity_local.state_paths import core_path
    core_path().parent.mkdir(parents=True, exist_ok=True)
    core_path().write_text("The incumbent core.\n", encoding="utf-8")
    v = propose_core("A candidate.", heldout=[])
    assert v.admitted is False
    assert v.archived and "admitted" not in os.path.basename(v.archived)
