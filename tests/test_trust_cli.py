"""`trust` CLI — the query retrieval + the no-ledger summary path.

Synthetic fixtures only. The retrieval test skips under the TF-IDF stub (the
SHA-1 projection has no semantic paraphrase match); the live gate runs on MLX.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

from trinity_local import embeddings
from trinity_local.commands.trust import handle_trust


def _council(home, cid, claims, winner):
    d = home / "council_outcomes"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps({
        "council_run_id": cid, "created_at": "2026-05-01T12:00:00+00:00",
        "metadata": {"task_text": "router design"},
        "routing_label": {"winner": winner, "disagreed_claims": claims},
    }))


@pytest.mark.skipif(not embeddings.mlx_actually_loaded(),
                    reason="retrieval needs real (non-TF-IDF) embeddings")
def test_trust_query_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _council(tmp_path, "council_1", [{
        "claim": "the router should key on lens basins not task type",
        "why_matters": "task_type is noisy at query time",
        "providers_for": ["claude"], "providers_against": ["gemini"]}], "claude")

    args = SimpleNamespace(build=False, as_json=True, top_k=5,
                           query="should the router key on lens basins rather than task type")
    buf = io.StringIO()
    with redirect_stdout(buf):
        handle_trust(args)
    out = json.loads(buf.getvalue())
    assert out["query"]
    assert out["recurring"], "a paraphrase of the seeded claim should retrieve"
    assert out["recurring"][0]["providers_for"] == ["anthropic"]
    assert out["recurring"][0]["chairman_winner"] == "anthropic"


def test_trust_no_query_prompts_to_build(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _council(tmp_path, "council_1", [{
        "claim": "x", "why_matters": "y",
        "providers_for": ["claude"], "providers_against": ["gemini"]}], "claude")

    args = SimpleNamespace(build=False, as_json=False, top_k=5, query=None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        handle_trust(args)
    text = buf.getvalue()
    assert "1 cross-provider disagreements in your corpus" in text, \
        "the seeded cross-provider council must be counted (not a vacuous 0)"
    assert "trust --build" in text, "with no ledger built, must point the user at --build"


class TestProvenanceDisclosure:
    """`trust` must say how many tally rows have a VERIFIED model identity.

    Founder feedback doc v3 §1 asked for pre-provenance rows to be flagged as
    coarse. The summary has carried model_provenance counts since
    stamp_member_model shipped, but no user surface rendered them — and the live
    split is 3,090 member records unverified against 19 verified. A per-model
    rate owes the reader that ratio.
    """

    def _agg(self, prov, denom):
        return {
            "tally_trustworthy": True, "resolved": denom, "caveat": "",
            "records": {"claude · opus · 4.8": {"w": 28, "l": 13, "win_rate": 0.68,
                                                 "ci": [0.52, 0.81]}},
            "model_provenance": prov, "model_provenance_denominator": denom,
        }

    def test_unverified_majority_is_disclosed(self):
        from trinity_local.commands.trust import _tally_lines

        out = _tally_lines(self._agg({"unknown": 160, "echoed": 5, "pinned": 3}, 168))
        assert "8/168 resolved claims carry a verified model identity" in out
        assert "160 predate verification" in out

    def test_fully_verified_ledger_stays_quiet(self):
        from trinity_local.commands.trust import _tally_lines

        out = _tally_lines(self._agg({"echoed": 10, "pinned": 2}, 12))
        assert "predate verification" not in out, (
            "a disclosure that prints when there is nothing to disclose trains "
            "the reader to skip it")

    def test_a_summary_without_provenance_does_not_crash(self):
        from trinity_local.commands.trust import _tally_lines

        agg = self._agg({}, 0)
        assert "provenance" not in _tally_lines(agg)
