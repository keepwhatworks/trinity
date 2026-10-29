"""Real-data degeneracy sweep — "find more issues like the last fifteen".

Most of Trinity's recurring bugs share ONE shape: a green check while the
GENERATED data is degenerate (data_sampling_principle). Unit tests on synthetic
1-item seeds stay green; the bug only shows when a producer runs on the REAL
corpus. `run_degeneracy_sweep()` exercises each producer against the live
~/.trinity and returns a list of findings (empty == clean).

Importable so BOTH `scripts/degeneracy_sweep.py` (cron/manual) and
`health_checks._check_data_degeneracy` (surfaced in `trinity-local status`) use
the same checks — one source of truth.

The classes mirror the bugs they were born from:
  A  eval set      prompt==gold / non-canonical rejection_type   (#247, v1.7.150)
  B  vocabulary    code-identifier homonyms/anchors              (#250, v1.7.152)
  C  basins        per-basin template/scaffolding concentration  (#248)
  D  Elo snapshot  web-era slug leaks into current-models view   (#275, v1.7.158)
  E  routing       legacy slug in by_task_type                   (#275)
  F  lens          identical poles / 0-decision / empty tensions (the chairman's core)
(E2, web-era slugs in picks.json, was retired 2026-10-26: nothing reads picks.json any
more, so a finding there had no consumer and no command that cleared it.)
"""
from __future__ import annotations

import re
from collections import Counter

_WEB_ERA = {"chatgpt", "claude_ai", "gemini"}


def _s(x: str | None) -> str:
    return (x or "").strip()


def _check_eval() -> list[str]:
    try:
        from .evals.builder import build_eval_set

        es = build_eval_set(source="rejections")
    except FileNotFoundError:
        return []  # no rejection ledger yet — normal cold-start, nothing to check
    except Exception as e:  # noqa: BLE001 — a sweep never crashes; it reports
        return [f"A/eval: sweep error {e!r}"]
    valid = {"REFRAME", "REDIRECT", "SHARPENING", "COMPRESSION"}
    out = []
    if any(_s(i.prompt) and _s(i.prompt) == _s(i.user_substitute) for i in es.items):
        out.append("A/eval: prompt==gold degenerate item leaked into the eval set")
    if any(i.rejection_type not in valid for i in es.items):
        out.append("A/eval: non-canonical rejection_type in eval set")
    return out


def _check_vocab() -> list[str]:
    try:
        from .state_paths import memories_dir

        p = memories_dir() / "vocabulary.md"
        if not p.exists():
            return []
        toks = set(re.findall(r"\b([a-z]+_[a-z_]{3,})\b", p.read_text(encoding="utf-8")))
        multi = sorted(t for t in toks if t.count("_") >= 2)
        if multi:
            return [
                f"B/vocab: {len(multi)} multi-underscore code-identifier token(s) in "
                f"served vocabulary.md ({multi[:4]}) — re-run `trinity-local lens`"
            ]
        return []
    except Exception as e:  # noqa: BLE001
        return [f"B/vocab: sweep error {e!r}"]


def _check_basins() -> list[str]:
    try:
        # Parse topics.json through the ONE shape-guarded reader
        # (`load_topics_basins`, #304) every other basin consumer already shares —
        # milestones._count_basins, lens_health._basins, the launchpad chip map.
        # The old open-coded `for b in d.get("basins") or []` had no
        # `isinstance(b, dict)` filter, so a single non-dict basin entry (a
        # valid-JSON-but-wrong-shape topics.json) threw inside the loop
        # (`'str' object has no attribute 'get'`) and turned this real
        # template-concentration verdict into a generic "sweep error" — the
        # duplicate-parser/drifted-predicate trap the canonical reader exists to
        # close. Returns [] for a missing/unreadable/wrong-shape file, and a list
        # of dict basins otherwise, so the sweep degrades to "clean" not "error".
        from .me.basins import load_topics_basins

        hot = []
        for b in load_topics_basins():
            reps = b.get("representatives") or b.get("reps") or []
            if len(reps) >= 3:
                pref = Counter(str(r)[:40] for r in reps)
                if pref.most_common(1)[0][1] / len(reps) > 0.6:
                    hot.append(b.get("id"))
        if hot:
            return [f"C/basins: {len(hot)} template-concentrated basin(s) {hot[:4]}"]
        return []
    except Exception as e:  # noqa: BLE001
        return [f"C/basins: sweep error {e!r}"]


def _check_elo() -> list[str]:
    try:
        from .telemetry import build_elo_snapshot

        leak = sorted(set(build_elo_snapshot()["providers"]) & _WEB_ERA)
        if leak:
            return [f"D/elo: web-era slug(s) {leak} leaked into current-models snapshot"]
        return []
    except Exception as e:  # noqa: BLE001
        return [f"D/elo: sweep error {e!r}"]


def _check_routing() -> list[str]:
    try:
        from .personal_routing import compute_personal_routing_table

        t = compute_personal_routing_table()
        slugs: set[str] = set()
        for p in (t.get("by_task_type") or {}).values():
            if isinstance(p, dict):
                slugs |= {k for k in p if k not in ("best", "n", "task_type")}
        leak = sorted(slugs & _WEB_ERA)
        if leak:
            return [f"E/routing: legacy slug(s) {leak} in by_task_type"]
        return []
    except Exception as e:  # noqa: BLE001
        return [f"E/routing: sweep error {e!r}"]


def _check_lens() -> list[str]:
    """Class F — the lens itself (the chairman reads it every council). Degenerate
    shapes: a tension whose two poles are identical (no real tension), a tension
    with 0 supporting decisions (no evidence), or a 'tensions' section that parses
    to zero tensions (empty lens with a header). Parses lens.md (get_persona's
    source). Staleness is a separate concern — _check_lens_freshness covers it."""
    try:
        from .state_paths import memories_dir

        p = memories_dir() / "lens.md"
        if not p.exists():
            return []
        md = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return [f"F/lens: sweep error {e!r}"]
    # Tensions render as "### N. pole_a ↔ pole_b". Parsed through the ONE shared
    # predicate (me.pipeline.iter_lens_tensions) so this structure check and the
    # generators "lift" count can never disagree about how many tensions exist.
    from .me.pipeline import iter_lens_tensions

    headers = iter_lens_tensions(md)
    out: list[str] = []
    if "paired tensions" in md.lower() and not headers:
        out.append("F/lens: lens.md has a tensions section but 0 parseable tensions")
    for i, (a, b) in enumerate(headers, 1):
        if a.strip().lower() == b.strip().lower():
            out.append(f"F/lens: tension {i} has identical poles ({a.strip()!r})")
    zero = md.count("Supported by 0 decisions")
    if zero:
        out.append(f"F/lens: {zero} tension(s) supported by 0 decisions (no evidence)")
    return out


def run_degeneracy_sweep(*, include_eval: bool = True) -> list[str]:
    """Run every producer-degeneracy check on the live ~/.trinity. Returns the
    list of findings (empty == clean). Never raises — each check self-reports.

    `include_eval=False` skips the eval check, which rebuilds the eval set from the whole
    prompt store (998 MB on one machine). `status` is documented as fast and runs this on
    every call; `lens-health` and scripts/degeneracy_sweep.py still run it all."""
    findings: list[str] = []
    for fn in (
        *((_check_eval,) if include_eval else ()),
        _check_vocab,
        _check_basins,
        _check_elo,
        _check_routing,
        _check_lens,
    ):
        findings.extend(fn())
    return findings
