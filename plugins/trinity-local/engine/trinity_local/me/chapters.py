"""Life chapters — datable episodes in the user's history (#252).

Deterministic, no LLM. Buckets prompts by month (via the TRUE conversation
`timestamp`, not the ingest `created_at` — the latter collapses a multi-year
corpus into the single month it was indexed), assigns each to its topic basin,
and surfaces contiguous month-runs where a basin's share spikes well above its
all-time baseline. Those runs are the user's "chapters": the smart-home build,
the real-estate deal, the frontend phase that ended.

This is the cross-time TOPIC view — complementary to the dormant within-thread
trajectory lens (#182). It is also the measurement the decay-aware lens needs:
a tension that spans many chapters is a durable trait (keep); one confined to an
old chapter is a phase that passed (decay).
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# A month needs at least this many prompts before we trust its share numbers
# (a 3-prompt month at 100% share is noise, not a chapter).
_MIN_MONTH_PROMPTS = 20
# A basin's run-month needs this many prompts to count as a surge (absolute
# floor on top of the relative multiple).
_MIN_RUN_PROMPTS = 15
# A basin's monthly share must exceed this multiple of its all-time baseline
# share to count as "surging".
_SURGE_MULT = 3.0


def prompt_time(node: Any) -> str:
    """The canonical time for a prompt: the original conversation `timestamp`
    when present, else the ingest `created_at`. Returns an ISO string or "".

    `created_at` is when Trinity INDEXED the prompt — identical across a bulk
    import, so it flattens history. `timestamp` is when the turn actually
    happened. Every time-based surface must read THIS, never `created_at`
    alone. Accepts a PromptNode or a raw dict."""
    if isinstance(node, dict):
        ts = node.get("timestamp") or ""
        ca = node.get("created_at") or ""
    else:
        ts = getattr(node, "timestamp", "") or ""
        ca = getattr(node, "created_at", "") or ""
    ts = (ts or "").strip()
    return ts if len(ts) >= 7 else (ca or "").strip()


@dataclass
class Chapter:
    """A contiguous stretch of months where one topic basin surged."""

    label: str
    start_month: str          # "YYYY-MM"
    end_month: str
    months: int
    peak_month: str
    peak_count: int
    peak_share: float         # 0..1, peak month's share of that month's prompts
    total_prompts: int        # surge-prompts across the run

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start_month": self.start_month,
            "end_month": self.end_month,
            "months": self.months,
            "peak_month": self.peak_month,
            "peak_count": self.peak_count,
            "peak_share": round(self.peak_share, 3),
            "total_prompts": self.total_prompts,
        }


# Calendar / clock tokens that occasionally rank high in a basin's term
# frequencies (a thread that says "...ship by November", timestamps pasted in
# logs) but are NOT topic themes. Live 2026-05-31: the timeline's first chapter
# rendered "Tip, November" — "November" as a chapter theme reads as noise on the
# launchpad. Filter UNAMBIGUOUS temporal words only; "may" / "march" / "am" /
# "pm" are deliberately excluded (common non-calendar words).
_TEMPORAL_LABEL_STOPWORDS = frozenset({
    "january", "february", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "sept", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "gmt", "utc", "pst", "est", "edt", "pdt", "cst", "cet",
    "today", "tomorrow", "yesterday", "tonight",
})


def _basin_label(basin) -> str:
    terms = [
        t for t in (getattr(basin, "top_terms", None) or [])
        if t.strip().lower() not in _TEMPORAL_LABEL_STOPWORDS
    ]
    return ", ".join(terms[:2]) or basin.id


def corpus_month_span() -> int:
    """How many distinct months the corpus actually spans by `timestamp`.
    The data-spread health signal — if this collapses to ~1 on a large corpus,
    a time field regressed to `created_at`. Best-effort; 0 on any failure."""
    try:
        from ..memory.store import iter_prompt_nodes_no_embedding
    except Exception:
        return 0
    months = set()
    for node in iter_prompt_nodes_no_embedding(limit=None):
        t = prompt_time(node)[:7]
        if len(t) == 7 and t >= "2000-00":
            months.add(t)
    return len(months)


def detect_chapters(
    *,
    surge_mult: float = _SURGE_MULT,
    min_month_prompts: int = _MIN_MONTH_PROMPTS,
    min_run_prompts: int = _MIN_RUN_PROMPTS,
) -> list[Chapter]:
    """Detect topic-surge chapters across the corpus timeline. Deterministic."""
    from ..me.basins import load_basins
    from ..memory.store import iter_prompt_nodes_no_embedding

    basins = load_basins()
    if not basins:
        return []
    pid2label: dict[str, str] = {}
    for b in basins:
        label = _basin_label(b)
        for pid in (getattr(b, "prompt_ids", None) or []):
            pid2label[pid] = label

    # month -> basin-label -> count, and month totals
    month_basin: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    month_total: collections.Counter = collections.Counter()
    overall: collections.Counter = collections.Counter()
    for node in iter_prompt_nodes_no_embedding(limit=None):
        m = prompt_time(node)[:7]
        if len(m) != 7 or m < "2000-00":
            continue
        label = pid2label.get(getattr(node, "id", None))
        if not label:
            continue
        month_basin[m][label] += 1
        month_total[m] += 1
        overall[label] += 1

    grand = sum(overall.values())
    if not grand:
        return []
    base_share = {lab: n / grand for lab, n in overall.items()}

    # Per basin, collect the surge-months, then stitch into contiguous runs.
    surge_months: dict[str, list[tuple[str, int, float]]] = collections.defaultdict(list)
    for m in sorted(month_basin):
        if month_total[m] < min_month_prompts:
            continue
        for lab, n in month_basin[m].items():
            share = n / month_total[m]
            if n >= min_run_prompts and share > surge_mult * base_share.get(lab, 1.0):
                surge_months[lab].append((m, n, share))

    chapters: list[Chapter] = []
    for lab, runs in surge_months.items():
        runs.sort()
        total = sum(r[1] for r in runs)
        peak = max(runs, key=lambda r: r[1])
        chapters.append(Chapter(
            label=lab,
            start_month=runs[0][0],
            end_month=runs[-1][0],
            months=len(runs),
            peak_month=peak[0],
            peak_count=peak[1],
            peak_share=peak[2],
            total_prompts=total,
        ))
    # Most-prompts-first: the biggest life chapters lead. Tie-break on the label
    # so two chapters with an equal total_prompts don't swap order across
    # re-runs (the surge_months dict order is content-derived). label is unique
    # per chapter. (peak above is already deterministic: `runs` is month-sorted
    # before the max, so max returns the earliest tied-peak month.)
    chapters.sort(key=lambda c: (-c.total_prompts, c.label))
    return chapters
