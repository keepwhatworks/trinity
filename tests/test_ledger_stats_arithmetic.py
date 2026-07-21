"""Guard: the public ledger numbers must be internally arithmetic-consistent and
agree across surfaces.

This catches the exact drift the validation workflow found on 2026-07-19: a hero
stat showing '77%' next to '(37-11)' when the two don't match, a GPT-5.5 stat that
read 51% in the block and 48% in the note, and the blog/site disagreeing on the
same model. These are static snapshots (they do NOT track the live tally — a user's
number accrues from their own councils), so the guard checks self-consistency and
cross-surface agreement, NOT equality to ~/.trinity.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "index.html"
BLOG = ROOT / "docs" / "blog" / "i-benchmarked-the-models-on-my-own-corrections.md"


def _rate(w: int, l: int) -> int:
    n = w + l
    return round(100 * w / n) if n else 0


def _site_stats() -> list[tuple[str, int, int, int]]:
    """(label, shown_pct, w, l) for every site ledger-stat that carries a W-L record."""
    html = INDEX.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(
        r'stat-n">(\d+)%</span><span class="stat-l">([^<(]+)\((\d+)&ndash;(\d+)\)', html
    ):
        pct, label, w, l = int(m.group(1)), m.group(2).strip(), int(m.group(3)), int(m.group(4))
        out.append((label, pct, w, l))
    return out


def _blog_rows() -> list[tuple[str, int, int, int]]:
    """(model, shown_pct, w, l) for every blog table row."""
    out = []
    for m in re.finditer(
        r"\|\s*([^|]+?)\s*\|\s*(\d+)\s*wins?,\s*(\d+)\s*loss(?:es)?\s*\|\s*\*{0,2}(\d+)%",
        BLOG.read_text(encoding="utf-8"),
    ):
        model, w, l, pct = m.group(1).strip(), int(m.group(2)), int(m.group(3)), int(m.group(4))
        out.append((model, pct, w, l))
    return out


def test_site_ledger_stats_are_arithmetic_consistent():
    stats = _site_stats()
    if not stats:
        pytest.skip("site ledger-stats block removed by design (stale personal-numbers table)")
    for label, pct, w, l in stats:
        assert abs(_rate(w, l) - pct) <= 1, (
            f"docs/index.html: '{label}' shows {pct}% but {w}-{l} = {_rate(w, l)}%"
        )


def test_blog_table_is_arithmetic_consistent():
    rows = _blog_rows()
    assert len(rows) >= 4, "blog ledger table did not parse — shape changed"
    for model, pct, w, l in rows:
        assert abs(_rate(w, l) - pct) <= 1, (
            f"blog: '{model}' shows {pct}% but {w}-{l} = {_rate(w, l)}%"
        )


def test_blog_and_site_agree_on_shared_models():
    """A model that appears on BOTH public surfaces must show the same snapshot."""
    site = {label: (pct, w, l) for label, pct, w, l in _site_stats()}
    if not site:
        pytest.skip("site ledger-stats block removed by design — nothing to cross-check")
    blog = {model: (pct, w, l) for model, pct, w, l in _blog_rows()}
    shared = 0
    for model, (bpct, bw, bl) in blog.items():
        for slabel, (spct, sw, sl) in site.items():
            if slabel in model or model in slabel:  # 'Claude Opus 4.8' vs 'Claude Opus 4.8'
                shared += 1
                assert (bw, bl) == (sw, sl), (
                    f"'{model}': blog {bw}-{bl} vs site {sw}-{sl} — the two public "
                    "surfaces show different snapshots of the same model"
                )
    assert shared >= 1, "expected at least one model shared between blog and site to cross-check"
