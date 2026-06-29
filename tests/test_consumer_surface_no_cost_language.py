"""Guard: the CONSUMER-facing surfaces (launchpad, README, landing) must NOT carry
enterprise COST framing — $/per-token/fleet/spend/Nx-spread/cost-governance.

Founder direction 2026-06-07 (consumer_enterprise_one_app_eval_moat): ONE app for
consumer (FREE) + enterprise (paid SUPPORT). Consumers stay WITHIN flat-rate
subscription plans (~$0 marginal — no per-token bill); enterprises pay PER TOKEN.
So the SAME benefit must read for both — proof of "which model wins YOUR work, on
the times you rewrote the answer" — and the consumer surface must NOT carry
$/per-token/fleet/spend language, which is (a) consumer-irrelevant (they're on
flat plans) and (b) reads as a different product / a second app. The spend /
20-40x-spread story belongs to the ENTERPRISE sales surface ONLY.

This is the green-gate for that constraint: as the enterprise story develops, a
cost line could leak onto the launchpad/README/landing — this catches it. Checks
VISIBLE copy only (HTML comments + <script>/<style> stripped) so dev rationale
comments that mention 'per-token'/'enterprise' don't count.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Enterprise COST framing forbidden on the consumer surface. Specific patterns
# (not bare 'save' / '×') to avoid benign hits: "saved rejections", "Save as PNG",
# "1200×630" PNG dimensions.
_COST_LEAK = re.compile(
    r"per[-\s]token"
    r"|cost[-\s]governance"
    r"|token\s+spend"
    r"|\bfleet\b"
    r"|[0-9]+\s*[x×]\s*(?:cheaper|the\s+price|spread|premium)"
    r"|20[-–]40\s*[x×]"
    r"|\$[0-9][0-9,]*\s*/?\s*(?:mo|month|year)\b",
    re.I,
)


def _strip_noise(html: str) -> str:
    """HTML comments + <script>/<style> → removed, leaving the VISIBLE consumer
    copy (dev rationale comments mentioning per-token/enterprise are not copy)."""
    for pat in (r"<!--.*?-->", r"<script.*?</script>", r"<style.*?</style>"):
        html = re.sub(pat, "", html, flags=re.DOTALL | re.I)
    return html


def _assert_clean(label: str, visible: str) -> None:
    hits = sorted({m.group(0) for m in _COST_LEAK.finditer(visible)})
    assert not hits, (
        f"{label} carries enterprise COST framing on the CONSUMER surface: {hits}. "
        "Per the founder's one-app rule (consumer free / flat plans; enterprise "
        "per-token + paid support), keep $/per-token/fleet/spend language OFF the "
        "consumer surface — lead with the audience-neutral benefit (proof of which "
        "model wins YOUR work). The cost story belongs to the enterprise surface."
    )


def test_launchpad_visible_copy_has_no_cost_framing():
    """Both eval-card states (empty CTA + the promoted has-results moat card) must
    stay audience-neutral — no cost framing in the visible copy."""
    from trinity_local.launchpad_template import render_launchpad_html

    empty = _strip_noise(render_launchpad_html(page_data={}))
    _assert_clean("the launchpad (cold-start)", empty)

    with_eval = _strip_noise(render_launchpad_html(page_data={
        "evalSummary": {
            "has_results": True, "target": "claude", "target_display": "Claude",
            "model": "opus-4-8", "aggregate_score": 0.79, "items_completed": 23,
            "items_total": 23, "total_runs": 2,
            "axes": [{"name": "REFRAME", "count": 12, "mean": 0.71}],
            "comparison": [
                {"target": "claude", "target_display": "Claude", "aggregate_score": 0.79, "items_completed": 23},
                {"target": "codex", "target_display": "GPT", "aggregate_score": 0.70, "items_completed": 20},
            ],
            "per_axis_leader": [], "mixed_eval_sets": False, "latest_run": None,
        },
    }))
    _assert_clean("the launchpad (promoted eval card)", with_eval)


def test_readme_and_landing_have_no_cost_framing():
    for rel in ("README.md", "docs/index.html"):
        text = (REPO / rel).read_text(encoding="utf-8")
        if rel.endswith(".html"):
            text = _strip_noise(text)
        _assert_clean(rel, text)
