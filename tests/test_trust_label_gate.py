"""The trust tally's label gate at every consumer (council_11c91b73c746c1c4, after hq_138).

hq_138 measured the ledger's LLM-resolved label at test-retest kappa 0.47 against a 0.6
floor. Machine consumers (the MCP tool, `trust --json`) must not receive per-model rates
while that holds; people see them only marked directional. The summary.json on disk was
built before the gate and still says trustworthy, so each test starts from that file.
"""
from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

ROW = {"w": 28, "l": 13, "win_rate": 0.683, "ci": [0.53, 0.8], "ci_excludes_half": True}


def _legacy_summary(home) -> None:
    from trinity_local.disagreement_ledger import _ledger_dir

    d = _ledger_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps({
        "resolved": 168, "tally_trustworthy": True, "k3_in_band": True, "k4_discriminates": True,
        "records": {"claude·opus·4.8": ROW}, "effort_breakdown": {"claude·opus·4.8": {"high": ROW}},
        "framing_breakdown": {}}), encoding="utf-8")


def test_mcp_trust_payload_withholds_per_model_rates(tmp_path, monkeypatch):
    """MUTATION: return the raw summary from _load_trust_summary and this reds (the
    pre-fix state: the tool description promised a withheld verdict, the code sent it)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _legacy_summary(tmp_path)
    from trinity_local import mcp_server

    out = mcp_server._load_trust_summary()
    assert out["tally_trustworthy"] is False and out["label_reliable"] is False
    assert "records" not in out and "effort_breakdown" not in out
    assert "withheld" in out["per_model"] and out["caveat"]


def test_trust_json_withholds_per_model_rates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _legacy_summary(tmp_path)
    from trinity_local.commands import trust

    trust.handle_trust(Namespace(build=False, query=None, as_json=True, silver=False,
                                 dissent=False, top_k=5))
    tally = json.loads(capsys.readouterr().out)["tally"]
    assert "records" not in tally and tally["tally_trustworthy"] is False


def test_trust_text_shows_rows_as_directional(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _legacy_summary(tmp_path)
    from trinity_local.commands.trust import _load_summary, _tally_lines

    out = _tally_lines(_load_summary())
    assert "DIRECTIONAL, NOT PROVEN" in out and "kappa 0.47" in out
    assert "claude·opus·4.8" in out and "withheld" not in out


def test_launchpad_card_shows_rows_as_directional(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _legacy_summary(tmp_path)
    import trinity_local.disagreement_ledger as dl
    from trinity_local import launchpad_data

    monkeypatch.setattr(dl, "load_disagreements", lambda *a, **k: [SimpleNamespace(is_cross_provider=True)] * 3)
    data = launchpad_data._load_trust_data()
    assert data["trustworthy"] is False and data["directional"] is True
    assert data["label_kappa"] == 0.47 and data["records"]
