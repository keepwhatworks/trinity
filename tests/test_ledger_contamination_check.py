"""Ledger-contamination meter (architecture council 2026-07-04, item 4 —
the third wall). Provenance guards fabrication, not reflection: Trinity's own
loop output is a real transcript turn, so self-generated acts pass the
provenance wall untouched. This meter must fire WEAK on a machine-shaped
ledger, stay green on a human one, and abstain when thin."""
from __future__ import annotations

import json


def _write_ledger(home, texts):
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, txt in enumerate(texts):
        rows.append(json.dumps({
            "id": f"a{i}", "trigger": "MODEL_MISS", "kind": "model_miss",
            "question_text": txt, "privileged": "the user rewrite",
            "sacrificed": "the model answer", "prompt_id": f"p{i}",
        }))
    (me / "preference_acts.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


HUMAN = [
    "which sofa fabric holds up with two dogs",
    "compare fixed vs variable mortgage for a 15 year horizon",
    "draft a note to the landlord about the broken heater",
    "best way to layer for alpine hiking in october",
] * 6  # 24 acts

MACHINE = [
    "run the council and consolidate the basins into picks.json",
    "the chairman synthesis failed — rerun eval-run with the claude judge",
    "pytest is green, sync_public and update the CHANGELOG",
    "wire the MCP tool into the launchpad dispatch",
] * 6


def test_clean_human_ledger_reads_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_ledger(tmp_path, HUMAN)
    from trinity_local.lens_health import _ledger_contamination, OK
    c = _ledger_contamination()
    assert c.status == OK, (c.status, c.summary)
    assert c.metric["fraction"] < 0.25


def test_machine_shaped_ledger_fires_weak(tmp_path, monkeypatch):
    """THE degenerate case: a ledger dominated by the loop's own vocabulary
    must NOT read green — that green would attest 'this lens is the user'
    while the mirror reflects the machine."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_ledger(tmp_path, HUMAN[:12] + MACHINE[:12])  # 50% shaped
    from trinity_local.lens_health import _ledger_contamination, WEAK
    c = _ledger_contamination()
    assert c.status == WEAK, (c.status, c.summary)
    assert c.metric["fraction"] >= 0.25
    assert c.fix, "a WEAK must carry its lever"


def test_thin_ledger_abstains(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_ledger(tmp_path, HUMAN[:5])
    from trinity_local.lens_health import _ledger_contamination, ABSTAIN
    assert _ledger_contamination().status == ABSTAIN


def test_missing_ledger_abstains_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.lens_health import _ledger_contamination, ABSTAIN
    assert _ledger_contamination().status == ABSTAIN
