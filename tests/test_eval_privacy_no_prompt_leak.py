"""Privacy guard: no SHAREABLE eval artifact may carry the user's raw prompts or
responses. The local result file may (it's the user's own audit trail, on their
machine); anything that LEAVES the machine — the share-card PNG data, the served
launchpad summary — must be scores/axes/models/counts only.

Trinity's whole promise is "your transcripts never leave your machine," and the
eval card is built to be screenshotted + posted for press. A regression that
threads prompt / rejected_response / user_substitute / target_response /
score_reason into the card or the served launchpad would be a privacy nightmare
(and there's prior art: a real machine-mined leak shipped once —
launch_readiness_and_corpus_leak). This locks the boundary with the
sentinel-in / assert-not-out discipline: every raw-text field carries a unique
secret; the secret must appear in the LOCAL result file (so the test isn't
vacuous) and must NOT appear in any shareable projection.
"""
from __future__ import annotations

import json

SENTINEL = "SENTINEL_PRIVATE_PROMPT_d3adb33fc0ffee"


def _sentinel_result():
    """An EvalRunResult whose every raw-text field carries the sentinel, plus
    valid scores so the card + summary actually render."""
    from trinity_local.evals.runner import EvalItemRun, EvalRunResult

    items = [
        EvalItemRun(
            eval_item_id=f"ei_{i}",
            rejection_type="REFRAME",
            prompt=f"{SENTINEL}-prompt-{i}",
            rejected_response=f"{SENTINEL}-rejected-{i}",
            user_substitute=f"{SENTINEL}-substitute-{i}",
            rubric_signal=f"{SENTINEL}-rubric-{i}",
            basin_id="b1",
            target_response=f"{SENTINEL}-response-{i}",
            target_error=None,
            elapsed_seconds=1.0,
            score=0.8,
            score_reason=f"{SENTINEL}-judge-reason-{i}",
            judge_provider="claude",
        )
        for i in range(3)
    ]
    return EvalRunResult(
        eval_id="evalSENTINEL",
        target_provider="claude",
        target_model="opus-4-8",
        started_at="2026-06-07T12:00:00",
        completed_at="2026-06-07T12:01:00",
        items_total=3,
        items_completed=3,
        items_failed=0,
        items=items,
        aggregate_score=0.8,
        by_rejection_type={
            "REFRAME": {"count": 3, "mean_score": 0.8, "min_score": 0.7, "max_score": 0.9}
        },
    )


def test_local_result_file_DOES_carry_the_sentinel():
    """Sanity / non-vacuity: the local audit trail genuinely contains the raw text
    — so the 'not in the shareable artifact' assertions below are meaningful."""
    res = _sentinel_result()
    blob = json.dumps(res.to_dict())
    assert SENTINEL in blob, "test fixture must actually carry the raw text"


def test_share_card_data_does_not_leak_raw_text():
    from trinity_local.eval_card import collect_card_data_from_result

    data = collect_card_data_from_result(_sentinel_result())
    to_dict = getattr(data, "to_dict", None)
    blob = json.dumps(to_dict() if callable(to_dict) else data.__dict__, default=str)
    assert SENTINEL not in blob, (
        "the eval SHARE-CARD data carries the user's raw prompt/response text — "
        "a privacy leak in the artifact built to be screenshotted + posted. The "
        "card must be scores/axes/models/counts only."
    )


def test_served_launchpad_eval_summary_does_not_leak_raw_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    res = _sentinel_result()
    rp = res.result_path()  # under TRINITY_HOME/evals/results/
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(res.to_dict()), encoding="utf-8")

    from trinity_local.launchpad_data import _eval_summary

    summary = _eval_summary()
    assert summary.get("has_results") is True, "fixture didn't register as a result run"
    blob = json.dumps(summary, default=str)
    assert SENTINEL not in blob, (
        "the SERVED launchpad eval summary carries the user's raw prompt/response "
        "text — it's embedded verbatim into the launchpad JSON that can be served "
        "+ screenshotted. The summary must be scores/axes/n only."
    )
