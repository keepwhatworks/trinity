"""Tests for the eval-share PNG renderer + CLI command.

Shape mirrors test_me_card_smoke (the existing PNG-share-card smoke):
we check that the renderer produces valid PNG bytes of the expected
shape, and that the CLI handler writes the file + prints a parseable
JSON summary. We don't pixel-diff — Pillow renders differ slightly
across font availability — but we DO check that:

  - The card embeds the install CTA + the GitHub Pages URL
  - The card claims the right target provider + score
  - The fallback (no eval results) path is graceful
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest

from trinity_local.eval_card import (
    CARD_HEIGHT,
    CARD_WIDTH,
    CTA_HEADLINE,
    CTA_LANDING_URL,
    EvalCardData,
    collect_card_data_from_result,
    render_eval_card,
)


@dataclass
class _FakeRunResult:
    """Minimal stand-in for evals.runner.RunResult — only the fields
    collect_card_data_from_result reads from. Lets the renderer test
    run without touching disk."""
    target_provider: str
    target_model: str | None
    aggregate_score: float | None
    items_total: int
    items_completed: int
    by_rejection_type: dict


# ── unit tests: data shaping ───────────────────────────────────────


def test_collect_card_data_extracts_axes_sorted():
    """by_axis must be alphabetically sorted so the rendered bars are
    in a stable order across runs (REFRAME/COMPRESSION/REDIRECT/
    SHARPENING). Without stable sort the same eval would render
    different bar orders between runs — bad for diff-friendly
    screenshot regression."""
    result = _FakeRunResult(
        target_provider="claude",
        target_model="claude-opus-4-7",
        aggregate_score=0.661,
        items_total=20,
        items_completed=20,
        by_rejection_type={
            "REFRAME": {"count": 9, "mean_score": 0.74},
            "COMPRESSION": {"count": 8, "mean_score": 0.50},
            "REDIRECT": {"count": 2, "mean_score": 0.80},
            "SHARPENING": {"count": 1, "mean_score": 0.93},
        },
    )
    data = collect_card_data_from_result(result)
    assert [a for a, _, _ in data.by_axis] == [
        "COMPRESSION", "REDIRECT", "REFRAME", "SHARPENING",
    ]
    assert data.target_provider == "claude"
    assert data.aggregate_score == 0.661
    assert data.items_completed == 20


def test_collect_card_data_handles_empty_by_rejection_type():
    """When eval-run produced no per-axis stats, by_axis should be an
    empty list, not crash. The renderer then falls through to the
    empty-state card."""
    result = _FakeRunResult(
        target_provider="antigravity",
        target_model=None,
        aggregate_score=None,
        items_total=0,
        items_completed=0,
        by_rejection_type={},
    )
    data = collect_card_data_from_result(result)
    assert data.by_axis == []
    assert data.aggregate_score is None


# ── renderer tests: PNG shape + content invariants ─────────────────


def _assert_valid_png(png_bytes: bytes) -> None:
    """Pillow round-trip the bytes and assert the 1200×630 shape."""
    PIL_Image = pytest.importorskip("PIL.Image")
    img = PIL_Image.open(io.BytesIO(png_bytes))
    assert img.size == (CARD_WIDTH, CARD_HEIGHT), (
        f"expected {CARD_WIDTH}×{CARD_HEIGHT} OG shape; got {img.size}"
    )
    assert img.format == "PNG"


def test_render_eval_card_produces_valid_png_with_data():
    pytest.importorskip("PIL")
    data = EvalCardData(
        target_provider="claude",
        target_model="claude-opus-4-7",
        aggregate_score=0.66,
        items_total=20,
        items_completed=20,
        by_axis=[
            ("COMPRESSION", 0.50, 8),
            ("REDIRECT", 0.80, 2),
            ("REFRAME", 0.74, 9),
            ("SHARPENING", 0.93, 1),
        ],
    )
    png = render_eval_card(data)
    _assert_valid_png(png)


def test_render_eval_card_produces_valid_png_empty_state():
    """Empty data (no aggregate, no axes) should still render — the
    card falls through to the 'Run trinity-local eval-run' fallback.
    Caller can't crash a launchpad share-button by having no data."""
    pytest.importorskip("PIL")
    data = EvalCardData(target_provider="claude")
    png = render_eval_card(data)
    _assert_valid_png(png)


def test_render_eval_card_suppresses_a_degraded_score():
    """#246: a degraded judge must NOT headline a score on the public share card,
    even if aggregate_score happens to be non-None. The card checks the
    `scoring_degraded` flag directly — not just the scorer's aggregate=None
    side-effect — so a result written without that nulling can't ship a degraded
    "scored X.XX" to HN/Twitter. render_eval_card is deterministic, so a degraded
    card must render byte-identical to the empty-state and differ from a healthy
    scored card with the same numbers."""
    pytest.importorskip("PIL")
    axes = [("REFRAME", 0.50, 10)]

    def _card(*, aggregate_score, scoring_degraded):
        return render_eval_card(
            EvalCardData(
                target_provider="claude",
                target_model="m",
                items_total=10,
                items_completed=10,
                by_axis=axes,
                aggregate_score=aggregate_score,
                scoring_degraded=scoring_degraded,
            )
        )

    degraded = _card(aggregate_score=0.50, scoring_degraded=True)
    healthy = _card(aggregate_score=0.50, scoring_degraded=False)
    empty = _card(aggregate_score=None, scoring_degraded=False)
    assert degraded != healthy, "degraded eval must not render the same as a real scored card"
    assert degraded == empty, "degraded eval must render the empty-state (like aggregate=None)"


def test_collect_card_data_propagates_scoring_degraded():
    from trinity_local.eval_card import collect_card_data_from_result

    @dataclass
    class _R:
        target_provider: str = "claude"
        target_model: str | None = "m"
        target_effort: str | None = None
        aggregate_score: float | None = 0.5
        items_total: int = 10
        items_completed: int = 10
        by_rejection_type: dict | None = None
        scoring_degraded: bool = True

    assert collect_card_data_from_result(_R()).scoring_degraded is True


# ── CTA + URL invariants — the share-workflow guard ───────────────


def test_card_module_pins_github_pages_url():
    """The GH Pages URL is the single source of truth for where the
    eval-card recipient lands. If this string drifts from the URL
    GitHub Pages actually serves (per docs/CNAME + docs/.nojekyll),
    the share workflow ships broken.

    Loud-fail on the obvious wrong shapes:
    - the H1-banned `trinity.local/` vanity domain
    - the bare `trinity-local/install.sh` no-protocol form (also banned by H1)
    """
    assert CTA_LANDING_URL == "keepwhatworks.com", (
        "eval_card.CTA_LANDING_URL drifted. Brand URL flipped 2026-05-17 "
        "from vishigondi.github.io/trinity-local → keepwhatworks.com. "
        "Sweep this in lockstep with docs/CNAME — the keepwhatworks.com "
        "CNAME is the recipient's landing for the eval-share PNG."
    )
    assert "trinity.local/" not in CTA_LANDING_URL, (
        "CTA must not use the unregistered trinity.local vanity domain "
        "(H1 banned this in launch-day copy; same rule applies to share artifacts)."
    )
    assert CTA_HEADLINE.endswith(":"), (
        "CTA headline reads as the lede above the URL line — keep the "
        "colon so the URL below it reads as the answer to the prompt."
    )


# ── CLI smoke: handler writes a file + prints JSON ────────────────


def test_eval_share_handler_writes_png(tmp_path, monkeypatch):
    """Smoke the CLI handler end-to-end. We can't easily fake the full
    RunResult loading without re-importing the runner module, so this
    test seeds a minimal results JSON in TRINITY_HOME and runs the
    handler against it. The handler should produce a PNG file at the
    requested path + print a JSON summary to stdout."""
    pytest.importorskip("PIL")
    import sys
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

    # Build the minimum result JSON the runner can load.
    from trinity_local.evals.builder import results_dir
    results_dir().mkdir(parents=True, exist_ok=True)
    result_json = {
        "eval_id": "eval_test123",
        "target_provider": "claude",
        "target_model": "claude-opus-4-7",
        "started_at": "2026-05-17T00:00:00+00:00",
        "completed_at": "2026-05-17T00:30:00+00:00",
        "items_total": 4,
        "items_completed": 4,
        "items_failed": 0,
        "items": [
            {
                "eval_item_id": f"ei_{i}",
                "rejection_type": axis,
                "prompt": "p",
                "rejected_response": "r",
                "user_substitute": "u",
                "rubric_signal": "s",
                "basin_id": "b00",
                "target_response": "ok",
                "target_error": None,
                "elapsed_seconds": 1.0,
                "score": score,
                "score_reason": "fine",
                "judge_provider": "claude",
            }
            for i, (axis, score) in enumerate([
                ("COMPRESSION", 0.5),
                ("REDIRECT", 0.8),
                ("REFRAME", 0.7),
                ("SHARPENING", 0.9),
            ])
        ],
        "aggregate_score": 0.725,
        "by_rejection_type": {
            "COMPRESSION": {"count": 1, "mean_score": 0.5,
                             "min_score": 0.5, "max_score": 0.5},
            "REDIRECT":   {"count": 1, "mean_score": 0.8,
                             "min_score": 0.8, "max_score": 0.8},
            "REFRAME":    {"count": 1, "mean_score": 0.7,
                             "min_score": 0.7, "max_score": 0.7},
            "SHARPENING": {"count": 1, "mean_score": 0.9,
                             "min_score": 0.9, "max_score": 0.9},
        },
    }
    result_path = results_dir() / "eval_eval_test123__model_claude__20260517T000000.json"
    result_path.write_text(json.dumps(result_json))

    # Invoke the handler directly (skip argparse).
    from trinity_local.commands.eval import handle_eval_share
    from types import SimpleNamespace

    out_path = tmp_path / "card.png"
    args = SimpleNamespace(
        target=None, eval_id=None, out=str(out_path), open_after=False,
    )
    # Capture stdout
    import io
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    handle_eval_share(args)

    summary = json.loads(captured.getvalue())
    assert summary["ok"] is True
    assert summary["path"] == str(out_path)
    assert summary["target_provider"] == "claude"
    assert summary["items_completed"] == 4
    assert summary["axes"] == ["COMPRESSION", "REDIRECT", "REFRAME", "SHARPENING"]
    assert out_path.exists()
    assert out_path.stat().st_size > 5000  # real PNG, not an empty file


def test_latest_result_path_skips_degenerate_null_score(tmp_path, monkeypatch):
    """Live 2026-05-31 (eyeballing the eval-share PNG): the card rendered the
    empty 'Run trinity-local eval-run' CTA while real scores sat on disk.
    _latest_result_path took the newest file by mtime, and a degenerate
    placeholder (aggregate_score=null, 3 items) with a fresher mtime masked
    the real scored run. The picker must skip null-score results — same rule
    as launchpad_data._eval_summary (fixed earlier the same day)."""
    import os
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.evals.builder import results_dir
    from trinity_local.commands.eval import _latest_result_path
    rd = results_dir()
    rd.mkdir(parents=True, exist_ok=True)

    scored = rd / "eval_real__model_claude__20260101T000000.json"
    scored.write_text(json.dumps({
        "eval_id": "real", "target_provider": "claude",
        "aggregate_score": 0.72, "items_completed": 41,
        "by_rejection_type": {"REFRAME": {"count": 5, "mean_score": 0.72}},
        "items": [{"judge_provider": "codex"}],
    }))
    degen = rd / "eval_placeholder__model_claude__20260102T000000.json"
    degen.write_text(json.dumps({
        "eval_id": "placeholder", "target_provider": "claude",
        "aggregate_score": None, "items_completed": 3,
        "by_rejection_type": {}, "items": [],
    }))
    # Make the degenerate run STRICTLY newer so the bug scenario is exact.
    os.utime(scored, (1_000, 1_000))
    os.utime(degen, (2_000, 2_000))

    picked = _latest_result_path(None, None)
    assert picked is not None
    assert picked.name == scored.name, (
        f"picked {picked.name} (the null-score placeholder); must skip it "
        f"and return the most-recent SCORED run."
    )

    # When ONLY a degenerate run exists, fall back to it (don't return None).
    scored.unlink()
    fallback = _latest_result_path(None, None)
    assert fallback is not None and fallback.name == degen.name


def test_compare_leaderboard_skips_degenerate_null_score_per_target(tmp_path, monkeypatch):
    """Companion to the single-card fix: the --compare leaderboard
    (_collect_leaderboard_rows, feeding eval-share --compare AND eval-show
    --compare) dedupes one row per target by newest mtime. A fresh null-score
    placeholder for a target was masking that target's real scored run (live
    2026-05-31: claude's 0.82 showed as '—' on the leaderboard while the
    launchpad showed it correctly). Skip null-score so each target's real run
    wins the slot."""
    import os
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.evals.builder import results_dir
    from trinity_local.commands.eval import _collect_leaderboard_rows
    rd = results_dir()
    rd.mkdir(parents=True, exist_ok=True)

    def write(name, target, score, mtime, by_axis=None):
        p = rd / name
        p.write_text(json.dumps({
            "eval_id": "set_a", "target_provider": target,
            "target_model": f"{target}-m",
            "aggregate_score": score,
            "items_completed": 39 if score is not None else 3,
            "by_rejection_type": by_axis or {},
            "items": [{"judge_provider": "codex"}],
        }))
        os.utime(p, (mtime, mtime))

    # claude: real scored run (older) + degenerate placeholder (newer mtime)
    write("eval_set_a__model_claude__t1.json", "claude", 0.82, 1_000,
          {"REFRAME": {"count": 5, "mean_score": 0.82}})
    write("eval_set_a__model_claude__t2.json", "claude", None, 2_000)
    write("eval_set_a__model_codex__t1.json", "codex", 0.77, 1_500,
          {"REFRAME": {"count": 5, "mean_score": 0.77}})

    rows, _ = _collect_leaderboard_rows(None)
    by_target = {r["target"]: r for r in rows}
    assert by_target["claude"]["aggregate_score"] == 0.82, (
        "claude's real score masked by the newer null-score placeholder"
    )
    assert by_target["codex"]["aggregate_score"] == 0.77
    # Sorted desc, every surfaced row scored.
    assert [r["aggregate_score"] for r in rows] == [0.82, 0.77]


def test_eval_share_handler_errors_when_no_results(tmp_path, monkeypatch):
    """No results on disk should produce a clean error message + exit 1
    (the handler raises SystemExit(1)), not a Python traceback."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.commands.eval import handle_eval_share
    from types import SimpleNamespace

    args = SimpleNamespace(
        target=None, eval_id=None,
        out=str(tmp_path / "card.png"), open_after=False,
    )
    with pytest.raises(SystemExit) as exc_info:
        handle_eval_share(args)
    assert exc_info.value.code == 1
