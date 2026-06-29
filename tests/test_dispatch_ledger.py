"""Dispatch ledger — Trinity's own dispatches must not re-enter the corpus.

Verified live 2026-06-09: one 42-item eval run put 40 VERBATIM replicas of the
founder's original prompts into the corpus (eval items are founder prompts
replayed; every generated-shape filter correctly passes them because they ARE
founder voice). Councils have the same exposure (task text → 3 member
transcripts). The fix records a normalized hash at the provider-dispatch
chokepoint and drops matching role=user turns at the ingest gate.

Pins: record/lookup round-trip, the short-prompt precision guard, TTL expiry,
hash-only privacy, corrupt-ledger resilience, the ingest-gate wire-in, and the
provider-dispatch wire-in (both mutation-sensitive: remove either hook -> reds).
"""
from __future__ import annotations

import json

import pytest


LONG = "Refactor the floor-plan engine so wall joins resolve before fixture placement begins."


@pytest.fixture()
def ledger_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    # The module cache must not leak across tests/homes.
    import trinity_local.dispatch_ledger as dl

    monkeypatch.setattr(dl, "_CACHE_KEY", None)
    monkeypatch.setattr(dl, "_CACHE_SET", frozenset())
    return tmp_path


class TestReadPathSkipsLedger:
    """The ledger is INGEST-only dedup. The READ-time re-filter (is_user_facing_text,
    used by lens-build Stage 0 + basin clustering + search) must NOT apply it: at read
    time it can't tell the user's ORIGINAL from Trinity's replay, so applying it DELETES
    the user's own authored prompts (measured 2026-06-15: 383 legit cross-domain prompts
    eaten — running your own question through a council puts it in the ledger)."""

    def test_read_keeps_original_that_ingest_drops_as_replica(self, ledger_home):
        from trinity_local.dispatch_ledger import record_dispatched_prompt
        from trinity_local.ingest import (
            SessionMessage,
            _is_user_facing_prompt,
            is_user_facing_text,
        )

        record_dispatched_prompt(LONG)
        # INGEST still drops the replay (default apply_dispatch_ledger=True):
        assert _is_user_facing_prompt(SessionMessage(role="user", text=LONG)) is False
        # READ keeps the user's original. Re-apply the ledger in is_user_facing_text
        # (revert the fix) and this goes red.
        assert is_user_facing_text(LONG) is True

    def test_read_still_drops_real_scaffolding(self, ledger_home):
        """Dropping the ledger from the read path must not weaken the genuine
        scaffolding guards — a real 'You are ...' system prompt is still rejected."""
        from trinity_local.ingest import is_user_facing_text

        assert is_user_facing_text("You are a helpful assistant. Summarize the following text.") is False


class TestLedger:
    def test_record_then_hit_and_unseen_miss(self, ledger_home):
        from trinity_local.dispatch_ledger import is_trinity_dispatched, record_dispatched_prompt

        assert not is_trinity_dispatched(LONG)
        assert record_dispatched_prompt(LONG) is True
        assert is_trinity_dispatched(LONG)
        assert not is_trinity_dispatched(LONG + " but now different")
        # Whitespace reflow (transcript round-trips) must still match.
        assert is_trinity_dispatched("  " + LONG.replace(" ", "  ") + "\n")

    def test_short_prompts_never_recorded(self, ledger_home):
        """A short turn the founder might genuinely retype ('fix this') must
        never be suppressible — the recorder refuses it outright."""
        from trinity_local.dispatch_ledger import is_trinity_dispatched, ledger_path, record_dispatched_prompt

        assert record_dispatched_prompt("fix this") is False
        assert not ledger_path().exists()
        assert not is_trinity_dispatched("fix this")

    def test_ttl_expiry(self, ledger_home):
        from trinity_local.dispatch_ledger import (
            _norm_hash,
            is_trinity_dispatched,
            ledger_path,
        )

        ledger_path().parent.mkdir(parents=True, exist_ok=True)
        ledger_path().write_text(
            json.dumps({"h": _norm_hash(LONG), "ts": "2026-01-01T00:00:00+00:00"}) + "\n"
        )
        assert not is_trinity_dispatched(LONG), "an expired entry must not suppress ingestion"

    def test_ledger_stores_hashes_never_text(self, ledger_home):
        from trinity_local.dispatch_ledger import ledger_path, record_dispatched_prompt

        record_dispatched_prompt(LONG)
        raw = ledger_path().read_text()
        assert "floor-plan" not in raw, "ledger leaked prompt TEXT — it must store hashes only"
        assert '"h"' in raw and '"ts"' in raw

    @pytest.mark.parametrize("junk", ["not json at all", '"a string"', "[1,2]", "null"])
    def test_corrupt_ledger_lines_ignored(self, ledger_home, junk):
        from trinity_local.dispatch_ledger import is_trinity_dispatched, ledger_path, record_dispatched_prompt

        ledger_path().parent.mkdir(parents=True, exist_ok=True)
        ledger_path().write_text(junk + "\n")
        assert not is_trinity_dispatched(LONG)
        # And a valid record APPENDED after junk still works.
        record_dispatched_prompt(LONG)
        assert is_trinity_dispatched(LONG)


class TestIngestGate:
    def test_user_turn_dropped_after_dispatch_recorded(self, ledger_home):
        """The #260 role=user gate must drop a turn whose text Trinity itself
        dispatched (and keep it when nothing was dispatched). Mutation: remove
        the is_trinity_dispatched check from _is_user_facing_prompt -> reds."""
        from trinity_local.dispatch_ledger import record_dispatched_prompt
        from trinity_local.ingest import _is_user_facing_prompt
        from trinity_local.session_schema import SessionMessage

        msg = SessionMessage(role="user", text=LONG)
        assert _is_user_facing_prompt(msg), "baseline: a genuine founder prompt must pass"
        record_dispatched_prompt(LONG)
        assert not _is_user_facing_prompt(msg), (
            "a turn Trinity dispatched itself was ingested as founder voice — "
            "the corpus self-pollution gate is not wired"
        )


class TestProviderWireIn:
    def _patch_dispatch(self, monkeypatch):
        from types import SimpleNamespace

        from trinity_local import providers

        monkeypatch.setattr(
            providers, "run_with_runtime_env",
            lambda command, **kw: SimpleNamespace(stdout="ok", stderr="", returncode=0),
        )
        monkeypatch.setattr(providers, "which_on_runtime_path", lambda *_: "/usr/bin/x")
        return providers

    def test_cli_provider_records_dispatch(self, ledger_home, monkeypatch):
        from pathlib import Path

        from trinity_local.config import ProviderConfig
        from trinity_local.dispatch_ledger import is_trinity_dispatched

        providers = self._patch_dispatch(monkeypatch)
        monkeypatch.setattr(providers.CLIProvider, "_try_sampling", lambda *_: None)
        cfg = ProviderConfig(name="claude", type="cli", enabled=True, label="Claude",
                             command=["claude", "-p"], args=[], task_types=set(),
                             model=None, effort=None)
        providers.CLIProvider(cfg).run(LONG, Path("."))
        assert is_trinity_dispatched(LONG), (
            "CLIProvider.run dispatched without recording — its transcript "
            "will re-enter the corpus as founder voice"
        )

    def test_codex_provider_records_dispatch(self, ledger_home, monkeypatch):
        from pathlib import Path

        from trinity_local.config import ProviderConfig
        from trinity_local.dispatch_ledger import is_trinity_dispatched

        providers = self._patch_dispatch(monkeypatch)
        cfg = ProviderConfig(name="codex", type="codex", enabled=True, label="Codex",
                             command=["codex", "exec"], args=[], task_types=set(),
                             model=None, effort=None)
        providers.CodexProvider(cfg).run(LONG, Path("."))
        assert is_trinity_dispatched(LONG)
