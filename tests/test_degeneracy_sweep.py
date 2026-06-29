"""The real-data degeneracy sweep (trinity_local.degeneracy) + its two
status surfaces (_check_lens_freshness, _check_data_degeneracy).

Founder 2026-06-01: "check the last few issues and see what tests we need to run
to find more like them." Most recurring bugs are 'green-while-degenerate' — the
producer emits degenerate data while every unit test (synthetic seed) stays
green. This sweep runs the producers on real data; these tests pin its detectors
+ the status wiring so the guard itself can't silently rot."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    (patch_trinity_home / "memories").mkdir(parents=True, exist_ok=True)
    (patch_trinity_home / "council_outcomes").mkdir(parents=True, exist_ok=True)
    return patch_trinity_home


def test_sweep_flags_code_identifier_in_vocabulary(home: Path):
    """Class B (v1.7.152): a multi-underscore code identifier in vocabulary.md is
    a homonym leak from the user's dev sessions, not a word."""
    (home / "memories" / "vocabulary.md").write_text(
        "# Vocab\ntoken | bimodality | uses\ntest_doc_count_consistency | 0.98 | 211\n",
        encoding="utf-8",
    )
    from trinity_local.degeneracy import run_degeneracy_sweep

    findings = run_degeneracy_sweep()
    assert any("B/vocab" in f and "test_doc_count_consistency" in f for f in findings)


def test_sweep_clean_when_vocab_has_only_words(home: Path):
    (home / "memories" / "vocabulary.md").write_text(
        "# Vocab\noutbuilding | 0.9 | 67\nstaircase | 0.93 | 295\n", encoding="utf-8"
    )
    from trinity_local.degeneracy import run_degeneracy_sweep

    # No code identifiers, no eval ledger (cold start) → no findings.
    assert run_degeneracy_sweep() == []


def test_sweep_no_eval_ledger_is_clean_not_error(home: Path):
    """A fresh install has no rejection ledger — that's cold-start, not a
    degeneracy. The eval check must return clean, never an error string."""
    from trinity_local.degeneracy import _check_eval

    assert _check_eval() == []


def test_lens_freshness_flags_stale_vocab(home: Path):
    """vocab/topics older than the newest council (by >1 day) → soft warning with
    a `dream` fix so status surfaces it (founder 2026-06-01 stale-vocab finding)."""
    from trinity_local.health_checks import _check_lens_freshness

    vocab = home / "memories" / "vocabulary.md"
    topics = home / "memories" / "topics.json"
    vocab.write_text("v", encoding="utf-8")
    topics.write_text("{}", encoding="utf-8")
    council = home / "council_outcomes" / "council_x.json"
    council.write_text("{}", encoding="utf-8")
    now = time.time()
    old = now - 3 * 86400  # lens built 3 days ago
    os.utime(vocab, (old, old))
    os.utime(topics, (old, old))
    os.utime(council, (now, now))  # a council landed since

    r = _check_lens_freshness()
    assert r.ok is True  # soft — stale, not broken
    assert r.fix == "trinity-local dream"
    assert "predate" in r.detail


def test_lens_freshness_current_within_grace(home: Path):
    """A council an hour after a rebuild must NOT nag (1-day grace)."""
    from trinity_local.health_checks import _check_lens_freshness

    for name, body in (("vocabulary.md", "v"), ("topics.json", "{}")):
        (home / "memories" / name).write_text(body, encoding="utf-8")
    (home / "council_outcomes" / "council_x.json").write_text("{}", encoding="utf-8")
    # all default mtimes ≈ now → within grace → current, no fix
    r = _check_lens_freshness()
    assert r.ok is True and not r.fix


def test_basins_check_degrades_clean_on_wrong_shape_basin_entry(home: Path):
    """Class C duplicate-parser guard: `degeneracy._check_basins` and the
    canonical `lens_routing.load_topics_basins` parse the SAME topics.json. The
    old `_check_basins` open-coded `for b in d.get("basins") or []` with NO
    `isinstance(b, dict)` filter, so a single non-dict basin entry (a valid-JSON-
    but-wrong-shape topics.json) threw inside the loop — "'str' object has no
    attribute 'get'" — and the user-facing `trinity-local status` degeneracy line
    read "C/basins: sweep error" instead of the real template-concentration
    verdict. The fix routes BOTH through the one shape-guarded reader.

    Founder symptom: one wrong-shape basin entry must NOT turn the basin sweep
    into a generic error; it must SKIP the bad entry and report on the rest.
    """
    import json

    from trinity_local import lens_routing
    from trinity_local.degeneracy import _check_basins

    topics = home / "memories" / "topics.json"
    # A non-dict basin entry ("not-a-dict") alongside clean dict basins — the
    # discriminating shape the two parsers used to disagree on.
    topics.write_text(
        json.dumps({"basins": [
            {"id": "b1", "size": 10, "representatives": ["a", "b", "c"]},
            {"id": "b2", "size": 8, "representatives": ["x", "y", "z"]},
            {"size": 5},               # dict basin MISSING its id
            "not-a-dict",              # non-dict entry — the crash trigger
        ]}),
        encoding="utf-8",
    )
    lens_routing._TOPICS_BASINS_CACHE = None

    findings = _check_basins()
    # Must NOT crash into a generic sweep error — the founder symptom.
    assert not any("sweep error" in f for f in findings), findings
    # No real concentration in this seed → genuinely clean.
    assert findings == [], findings
    # The two parsers now AGREE: both skip the non-dict, count the 3 dict basins
    # (id-agnostic), so they read the SAME population from the same bytes.
    assert len(lens_routing.load_topics_basins()) == 3


def test_basins_check_still_fires_on_real_concentration_with_wrong_shape(home: Path):
    """The shape-guard must not blind the detector: a genuinely template-
    concentrated basin still fires even when a non-dict entry sits beside it
    (the bad entry is skipped, the real one is still judged)."""
    import json

    from trinity_local import lens_routing
    from trinity_local.degeneracy import _check_basins

    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": [
            {"id": "polluted", "size": 20, "representatives": ["SAME"] * 5},
            {"id": "clean", "size": 8, "representatives": ["p", "q", "r"]},
            "wrong-shape",            # non-dict entry beside the polluted basin
        ]}),
        encoding="utf-8",
    )
    lens_routing._TOPICS_BASINS_CACHE = None

    findings = _check_basins()
    assert any("C/basins" in f and "polluted" in f for f in findings), findings
    assert not any("sweep error" in f for f in findings), findings


def test_cortex_picks_check_flags_web_era_routing_slug():
    """Class E2: a lens-derived pick whose `winner` is a web-era slug
    (chatgpt/claude_ai/gemini) points at a non-dispatchable provider — ask()
    drops the route. Post-collapse (#298) the pick schema is the flat lens-basin
    tally `{winner, count, margin, ...}`, so `winner` is the single dispatch
    target the guard checks. (Injected picks bypass load-time canonicalization.)"""
    from trinity_local.degeneracy import _check_cortex_picks

    bad = {
        "b00": {"winner": "chatgpt", "count": 4, "margin": 0.5, "n_episodes": 4},
    }
    findings = _check_cortex_picks(bad)
    assert any("E2/cortex" in f and "chatgpt" in f for f in findings)

    bad_gemini = {
        "b01": {"winner": "gemini", "count": 3, "margin": 0.3, "n_episodes": 3},
    }
    assert any("gemini" in f for f in _check_cortex_picks(bad_gemini))


def test_cortex_picks_check_clean_on_canonical_slugs():
    from trinity_local.degeneracy import _check_cortex_picks

    ok = {
        "b00": {"winner": "codex", "count": 5, "margin": 0.6, "n_episodes": 5},
        "b01": {"winner": "antigravity", "count": 3, "margin": 0.2, "n_episodes": 3},
    }
    assert _check_cortex_picks(ok) == []


def test_sweep_flags_degenerate_lens(home: Path):
    """Class F: the chairman reads lens.md every council. A tension with identical
    poles (no real tension) or 0 supporting decisions (no evidence) is degenerate
    — the sweep must catch it (a future bad lens-build can't silently ship)."""
    (home / "memories" / "lens.md").write_text(
        "# Lens\n## Lenses (paired tensions)\n"
        "### 1. speed ↔ speed\n- Supported by 0 decisions · stable since 2026-05-01\n",
        encoding="utf-8",
    )
    from trinity_local.degeneracy import _check_lens

    findings = _check_lens()
    assert any("identical poles" in f for f in findings)
    assert any("0 decisions" in f for f in findings)


def test_sweep_clean_on_well_formed_lens(home: Path):
    (home / "memories" / "lens.md").write_text(
        "# Lens\n## Lenses (paired tensions)\n"
        "### 1. executable artifact ↔ explanatory description\n"
        "- Pure-executable artifact fails as: **cargo-cult**\n"
        "- Tension evidence spans basins: b00, b03\n"
        "- Supported by 17 decisions · stable since 2026-05-28\n",
        encoding="utf-8",
    )
    from trinity_local.degeneracy import _check_lens

    assert _check_lens() == []


def test_data_degeneracy_check_surfaces_findings(home: Path):
    """_check_data_degeneracy must be a SOFT check (ok=True) that surfaces sweep
    findings + a fix, so status' soft-warning loop prints them."""
    (home / "memories" / "vocabulary.md").write_text(
        "run_in_background | 0.9 | 5\n", encoding="utf-8"
    )
    from trinity_local.health_checks import _check_data_degeneracy

    r = _check_data_degeneracy()
    assert r.ok is True  # degenerate data is dated, not a broken install
    assert r.fix and "degeneracy_sweep" in r.fix
    assert "run_in_background" in r.detail
