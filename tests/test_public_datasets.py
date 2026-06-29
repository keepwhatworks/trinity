"""Public human-preference dataset loader — trust pillar #2 (reproducible mechanism).

The SAME judge-alignment harness that measures a judge against the user's private
corrections also runs against published datasets (RewardBench / Arena / HH-RLHF),
so a skeptic can reproduce the mechanism on public ground truth without touching
private data. These tests pin the loader's parsing across the on-disk shapes those
datasets ship in, and that `eval-judge-check --dataset` keeps the public run
SEPARATE from the corrections-based judge pick.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from trinity_local.evals import judge_alignment as ja
from trinity_local.evals.public_datasets import (
    _extract_text,
    _winner_to_side,
    load_public_pairs,
)


def _write(path, records, *, jsonl=True):
    if jsonl:
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    else:
        path.write_text(json.dumps(records), encoding="utf-8")
    return path


# --- shape parsing -------------------------------------------------------------

def test_reward_bench_chosen_rejected_jsonl(tmp_path):
    recs = [
        {"chosen": "Short, direct.", "rejected": "A rambling hedge that goes on.", "subset": "chat"},
        {"chosen": "Use a hashmap.", "rejected": "Use nested loops.", "subset": "reasoning"},
        {"chosen": "Decline; unsafe.", "rejected": "Sure, here's how.", "subset": "safety"},
    ]
    p = _write(tmp_path / "rb.jsonl", recs)
    pairs = load_public_pairs(p)
    assert len(pairs) == 3
    # axis carried from `subset`
    assert [pr.axis for pr in pairs] == ["chat", "reasoning", "safety"]
    # the human-preferred (chosen) side is recoverable from human_side
    for pr in pairs:
        preferred = pr.option_a if pr.human_side == "A" else pr.option_b
        assert preferred in ("Short, direct.", "Use a hashmap.", "Decline; unsafe.")


def test_position_balanced_sides_alternate(tmp_path):
    # 4 identical-shape records → human side must alternate A/B/A/B (cancels position bias).
    recs = [{"chosen": f"good {i}", "rejected": f"bad {i}"} for i in range(4)]
    p = _write(tmp_path / "rb.jsonl", recs)
    pairs = load_public_pairs(p)
    assert [pr.human_side for pr in pairs] == ["A", "B", "A", "B"]


def test_json_array_and_wrapper_forms(tmp_path):
    recs = [{"chosen": "g1", "rejected": "b1"}, {"chosen": "g2", "rejected": "b2"}]
    arr = _write(tmp_path / "arr.json", recs, jsonl=False)
    assert len(load_public_pairs(arr)) == 2
    wrap = tmp_path / "wrap.json"
    wrap.write_text(json.dumps({"data": recs}), encoding="utf-8")
    assert len(load_public_pairs(wrap)) == 2


def test_arena_winner_shape_drops_ties(tmp_path):
    recs = [
        {"response_a": "aaa wins", "response_b": "bbb loses", "winner": "model_a", "category": "arena"},
        {"response_a": "ccc loses", "response_b": "ddd wins", "winner": "B"},
        {"response_a": "x", "response_b": "y", "winner": "tie"},        # no signal → dropped
        {"response_a": "p", "response_b": "q", "winner": "both_bad"},   # no signal → dropped
    ]
    p = _write(tmp_path / "arena.jsonl", recs)
    pairs = load_public_pairs(p)
    assert len(pairs) == 2
    # first record: winner model_a → preferred text is "aaa wins"
    first = pairs[0]
    preferred = first.option_a if first.human_side == "A" else first.option_b
    assert preferred == "aaa wins"


def test_passthrough_option_human_side_shape(tmp_path):
    recs = [{"option_a": "foo", "option_b": "bar", "human_side": "B"}]
    p = _write(tmp_path / "pt.jsonl", recs)
    pairs = load_public_pairs(p)
    assert len(pairs) == 1
    pr = pairs[0]
    preferred = pr.option_a if pr.human_side == "A" else pr.option_b
    assert preferred == "bar"  # human_side B preserved as the preferred side


def test_chat_message_list_extraction(tmp_path):
    recs = [{
        "chosen": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "THE GOOD ANSWER"}],
        "rejected": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "the bad answer"}],
    }]
    p = _write(tmp_path / "chat.jsonl", recs)
    pairs = load_public_pairs(p)
    preferred = pairs[0].option_a if pairs[0].human_side == "A" else pairs[0].option_b
    assert preferred == "THE GOOD ANSWER"


def test_hh_rlhf_transcript_takes_final_assistant_turn(tmp_path):
    # HH-RLHF ships full dialogues; the diverging answer is the LAST Assistant turn.
    recs = [{
        "chosen": "Human: hi\n\nAssistant: filler\n\nHuman: more\n\nAssistant: GOOD final reply",
        "rejected": "Human: hi\n\nAssistant: filler\n\nHuman: more\n\nAssistant: bad final reply",
    }]
    p = _write(tmp_path / "hh.jsonl", recs)
    pairs = load_public_pairs(p)
    pr = pairs[0]
    preferred = pr.option_a if pr.human_side == "A" else pr.option_b
    assert preferred == "GOOD final reply"  # only the final turn, not the whole transcript


# --- guards: degenerate, limit, errors ----------------------------------------

def test_degenerate_records_are_skipped(tmp_path):
    recs = [
        {"chosen": "same text", "rejected": "same text"},  # no preference signal
        {"chosen": "", "rejected": "nonempty"},            # empty side
        {"chosen": "real winner", "rejected": "real loser"},
    ]
    p = _write(tmp_path / "deg.jsonl", recs)
    pairs = load_public_pairs(p)
    assert len(pairs) == 1  # only the genuine pair survives


def test_limit_caps_pairs(tmp_path):
    recs = [{"chosen": f"g{i}", "rejected": f"b{i}"} for i in range(50)]
    p = _write(tmp_path / "rb.jsonl", recs)
    assert len(load_public_pairs(p, limit=10)) == 10


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_public_pairs(tmp_path / "nope.jsonl")


def test_unusable_file_raises_value_error(tmp_path):
    # Valid JSON, but no recognised preference fields → ValueError (not a silent empty).
    p = _write(tmp_path / "junk.jsonl", [{"foo": "bar"}, {"baz": 1}])
    with pytest.raises(ValueError):
        load_public_pairs(p)


def test_malformed_jsonl_line_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        json.dumps({"chosen": "g1", "rejected": "b1"}) + "\n"
        + "{ this is not valid json\n"
        + json.dumps({"chosen": "g2", "rejected": "b2"}) + "\n",
        encoding="utf-8",
    )
    pairs = load_public_pairs(p)
    assert len(pairs) == 2  # the broken middle line is skipped, the two good ones survive


def test_extract_text_and_winner_helpers():
    assert _extract_text("plain") == "plain"
    assert _extract_text([{"role": "assistant", "content": "last"}]) == "last"
    assert _extract_text({"content": "dict form"}) == "dict form"
    assert _extract_text(None) == ""
    assert _winner_to_side("model_a") == "A"
    assert _winner_to_side("B") == "B"
    assert _winner_to_side("tie") is None


# --- the public run uses GENERIC preference, never the user's lens -------------

class _PromptCapturingJudge:
    """Records every prompt it's asked, and picks whichever side carries GOOD."""
    seen: list[str] = []

    def run(self, prompt: str, cwd=None):
        type(self).seen.append(prompt)
        a_start = prompt.find("Response A:")
        b_start = prompt.find("Response B:")
        a_block = prompt[a_start:b_start]
        out = "A" if "GOOD" in a_block else "B"
        return SimpleNamespace(stdout=out, returncode=0, stderr="")


def test_generic_prompt_does_not_leak_the_lens(monkeypatch):
    """A public-dataset validation asks generic preference — the user's lens (a
    PRIVATE secret) must NOT appear in the prompt the judge sees."""
    _PromptCapturingJudge.seen = []
    monkeypatch.setattr("trinity_local.providers.make_provider", lambda cfg: _PromptCapturingJudge())
    pairs = [
        ja.PreferencePair(pair_id="p0", axis="chat", option_a="GOOD a", option_b="bad b",
                           human_side="A", source_id="s0"),
        ja.PreferencePair(pair_id="p1", axis="chat", option_a="bad a", option_b="GOOD b",
                           human_side="B", source_id="s1"),
    ]
    SECRET = "MY-PRIVATE-LENS-SECRET-XYZ"
    res = ja.validate_judge(
        "claude", pairs, {"claude": SimpleNamespace(enabled=True)},
        lens_text=f"The user loves {SECRET}",
        prompt_template=ja.GENERIC_PREFERENCE_PROMPT,
    )
    assert res.agreement == 1.0  # the aligned judge agrees with the public label
    assert _PromptCapturingJudge.seen, "judge was never called"
    for prompt in _PromptCapturingJudge.seen:
        assert SECRET not in prompt, "the private lens leaked into the public-dataset prompt"
        assert "this user's taste" not in prompt.lower()  # generic, not taste-anchored


def test_public_report_path_is_separate_from_corrections(monkeypatch, patch_trinity_home):
    """eval-judge-check --dataset writes the PUBLIC report, never the corrections
    report eval-run reads to pick its judge (so a public run can't change the pick)."""
    from argparse import Namespace

    from trinity_local.commands import eval as eval_cmd

    monkeypatch.setattr(
        "trinity_local.providers.make_provider",
        lambda cfg: _PromptCapturingJudge(),
    )
    monkeypatch.setattr(
        "trinity_local.config.load_config",
        lambda *a, **k: SimpleNamespace(providers={"claude": SimpleNamespace(enabled=True)}),
    )
    recs = [{"chosen": f"GOOD {i}", "rejected": f"bad {i}", "subset": "chat"} for i in range(16)]
    ds = patch_trinity_home / "rb.jsonl"
    _write(ds, recs)

    rc = eval_cmd.handle_eval_judge_check(Namespace(dataset=str(ds), limit=16, config=None))
    assert rc == 0
    assert eval_cmd._public_alignment_report_path().exists()
    assert not eval_cmd._alignment_report_path().exists(), \
        "a public run must NOT write the corrections report (the judge-selection signal)"
    # report is numbers-only — no raw dataset text (privacy invariant)
    blob = eval_cmd._public_alignment_report_path().read_text(encoding="utf-8")
    assert "GOOD" not in blob and "bad " not in blob


def test_per_axis_breakdown_is_displayed(monkeypatch, patch_trinity_home, capsys):
    """A public benchmark's value is the per-CATEGORY breakdown (chat / reasoning /
    safety — the standard RewardBench split). `eval-judge-check` must print it, not
    just the aggregate (counts only — privacy-safe)."""
    from argparse import Namespace

    from trinity_local.commands import eval as eval_cmd

    monkeypatch.setattr(
        "trinity_local.providers.make_provider", lambda cfg: _PromptCapturingJudge()
    )
    monkeypatch.setattr(
        "trinity_local.config.load_config",
        lambda *a, **k: SimpleNamespace(providers={"claude": SimpleNamespace(enabled=True)}),
    )
    recs = []
    for cat, n in (("chat", 6), ("reasoning", 4), ("safety", 2)):
        recs += [{"chosen": f"GOOD {cat}{i}", "rejected": f"bad {cat}{i}", "subset": cat}
                 for i in range(n)]
    ds = patch_trinity_home / "rb.jsonl"
    _write(ds, recs)

    eval_cmd.handle_eval_judge_check(Namespace(dataset=str(ds), limit=12, config=None))
    out = capsys.readouterr().out
    assert "per-axis:" in out, "the per-category breakdown was not displayed"
    # each category surfaces with its own count (counts only — no raw text leak)
    assert "chat" in out and "reasoning" in out and "safety" in out
    assert "(6)" in out and "(4)" in out and "(2)" in out


def test_per_axis_suppressed_for_single_axis(monkeypatch, patch_trinity_home, capsys):
    """One axis is just the overall number again — don't print a redundant per-axis
    line (the unbounded-noise-table discipline: no decorative single-row breakdowns)."""
    from argparse import Namespace

    from trinity_local.commands import eval as eval_cmd

    monkeypatch.setattr(
        "trinity_local.providers.make_provider", lambda cfg: _PromptCapturingJudge()
    )
    monkeypatch.setattr(
        "trinity_local.config.load_config",
        lambda *a, **k: SimpleNamespace(providers={"claude": SimpleNamespace(enabled=True)}),
    )
    # no `subset` field → every pair falls into the single "public" axis
    recs = [{"chosen": f"GOOD {i}", "rejected": f"bad {i}"} for i in range(8)]
    ds = patch_trinity_home / "rb.jsonl"
    _write(ds, recs)

    eval_cmd.handle_eval_judge_check(Namespace(dataset=str(ds), limit=8, config=None))
    out = capsys.readouterr().out
    assert "per-axis:" not in out, "a single-axis run printed a redundant per-axis line"


def test_dry_run_validates_without_dispatching(monkeypatch, patch_trinity_home, capsys):
    """--dry-run loads + reports the dataset's coverage with ZERO judge dispatch
    (no quota) — the measure-before-you-spend discipline. Proven by a make_provider
    that raises: if dry-run dispatched, the run would crash."""
    from argparse import Namespace

    from trinity_local.commands import eval as eval_cmd

    def _boom(cfg):
        raise AssertionError("dry-run dispatched to a judge (must not)")

    monkeypatch.setattr("trinity_local.providers.make_provider", _boom)
    # no providers enabled — dry-run must not need any (it never dispatches)
    monkeypatch.setattr(
        "trinity_local.config.load_config",
        lambda *a, **k: SimpleNamespace(providers={}),
    )
    recs = []
    for cat, n in (("chat", 6), ("reasoning", 4), ("safety", 2)):
        recs += [{"chosen": f"good {cat}{i}", "rejected": f"bad {cat}{i}", "subset": cat}
                 for i in range(n)]
    ds = patch_trinity_home / "rb.jsonl"
    _write(ds, recs)

    rc = eval_cmd.handle_eval_judge_check(
        Namespace(dataset=str(ds), limit=12, dry_run=True, config=None)
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "no judge dispatched" in out
    # per-category coverage + position balance, no raw text
    assert "chat" in out and "reasoning" in out and "safety" in out
    assert "position balance:" in out
    assert "good" not in out and "bad " not in out
    # NO report written — dry-run produces no trust artifact, only a preview
    assert not eval_cmd._public_alignment_report_path().exists()


def test_dry_run_on_missing_dataset_still_errors_cleanly(monkeypatch, patch_trinity_home, capsys):
    """A bad --dataset path under --dry-run exits 2 with the download hint — the
    dry-run branch is reached only after the loader's own error handling."""
    from argparse import Namespace

    from trinity_local.commands import eval as eval_cmd

    monkeypatch.setattr(
        "trinity_local.config.load_config",
        lambda *a, **k: SimpleNamespace(providers={}),
    )
    with pytest.raises(SystemExit) as exc:
        eval_cmd.handle_eval_judge_check(
            Namespace(dataset=str(patch_trinity_home / "nope.jsonl"),
                      limit=12, dry_run=True, config=None)
        )
    assert exc.value.code == 2
    assert "not found" in capsys.readouterr().out
