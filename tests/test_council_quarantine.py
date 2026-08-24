"""Unparseable chairman output is quarantined before the refusal, not lost with it.

A `lens --deep` run on 2026-08-24 lost 49 of 433 syntheses (11.3%) to
`routing_label is None`. Refusing was correct — an outcome with no routing label
must never reach the ledger — but the raw chairman text went into the exception
with it. Three hours of quota spent, zero examples kept, the failure
undiagnosable, and a rerun guaranteed to void the same way (res_080).

The refusal is unchanged. Only the evidence survives it now.
"""
from __future__ import annotations

import json

import pytest

from trinity_local import council_runtime
from trinity_local.state_paths import trinity_home


def _outcome(**kw):
    o = type("O", (), {})()
    o.council_run_id = kw.get("cid", "council_test123")
    o.synthesis_output = kw.get("synth", "PART 1\nthe chairman said things but emitted no fence")
    o.routing_label = kw.get("label")
    o.primary_provider = "claude"
    o.primary_model = "claude-opus-5"
    return o


class TestQuarantine:
    def test_the_refusal_still_happens(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        with pytest.raises(ValueError, match="routing_label is None"):
            council_runtime.save_council_outcome(_outcome())

    def test_the_raw_synthesis_survives_the_refusal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        with pytest.raises(ValueError):
            council_runtime.save_council_outcome(_outcome(cid="council_abc"))
        q = trinity_home() / "council_quarantine" / "council_abc.json"
        assert q.exists(), "the chairman text was discarded — the failure stays undiagnosable"
        rec = json.loads(q.read_text())
        assert "emitted no fence" in rec["synthesis_output"]
        assert rec["primary_model"] == "claude-opus-5", "need the model to attribute the failure"

    def test_quarantine_failure_never_masks_the_refusal(self, tmp_path, monkeypatch):
        """Diagnostics must not become a new failure mode."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # atomic_write_text is imported INSIDE the function, so patch it at
        # the source module — patching council_runtime would silently no-op and
        # the test would pass without exercising anything.
        import trinity_local.utils as _u
        monkeypatch.setattr(_u, "atomic_write_text",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(ValueError, match="routing_label is None"):
            council_runtime.save_council_outcome(_outcome())

    def test_a_valid_outcome_is_not_quarantined(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        try:
            council_runtime.save_council_outcome(_outcome(label={"winner": "claude"}))
        except Exception:
            pass  # the object is a stub; only the quarantine dir matters here
        assert not (trinity_home() / "council_quarantine").exists()
