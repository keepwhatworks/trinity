"""Generators pass (the lens "lift"): abstract task-level lens tensions into the
cross-domain GENERATING invariants they project from.

Validated 2026-06-04 (real corpus). The lens pipeline mines TASK-LOCAL tensions
(executable-artifact ↔ explanatory-description, verification-gate ↔ momentum).
A user's deep preferences are the cross-domain GENERATORS those tensions are
projections of — the SAME reflex showing up in software AND materials AND
finance AND epistemology. Today's pipeline surfaces zero generators; fed
domain-diverse evidence + asked to abstract, the chairman recovers them.

Pipeline (all autonomous — NO human ratification):

    domain-diverse evidence   (geometry: balance across basins, neutral signal)
      → abstract to generators (pass 1)
      → dual self-critique:    (a) contradiction-split — two task-tensions that
                                   rank an option oppositely cannot share a
                                   generator ⇒ one is missing
                               (b) off-plane gap — a preference the cross-domain
                                   evidence shows but NO task-tension expresses
                                   ⇒ a generator the (software-mined) projection
                                   plane could not see
      → orthogonal generator tier (the task-tensions demote to evidence)

Flag-gated (``TRINITY_LENS_GENERATORS``, default OFF) and STANDALONE: nothing
here is wired into ``build_me_via_lens_pipeline`` yet — the founder reviews the
generators first. Dispatch PREFERS MCP-sampling (#263): a ``claude -p``
subprocess writes a ``~/.claude`` transcript that re-ingests into the lens
corpus (the generator-over-generated pollution path), so sampling is the
correct route; the subprocess is a quiet CLI-only fallback.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Callable

# Geometry knobs (validated: domain-balanced 48 beat recency-200 by 4-8x on
# cross-domain invariant coverage, using a NEUTRAL length signal — never
# invariant-richness, which would be circular).
_GENERATORS_FLAG = "TRINITY_LENS_GENERATORS"
_EVIDENCE_N = 48          # turns fed to the chairman
_POOL_SIZE = 600          # most-substantive turns to assign + round-robin over
_MIN_TURN_CHARS = 20


def generators_enabled() -> bool:
    """The generators pass only runs when the flag is explicitly set. Default
    OFF — the stage is standalone and unreviewed."""
    return os.environ.get(_GENERATORS_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


# ── evidence selection (pure geometry — no LLM, no quota) ──────────────────────


def _uturn(pair) -> str:
    """The user turn from a turn-pair (tuples from iter_turn_pairs, or dicts)."""
    if isinstance(pair, (list, tuple)):
        for x in pair:
            if isinstance(x, str) and len(x.strip()) > _MIN_TURN_CHARS:
                return x.strip()
        return " ".join(str(x) for x in pair)
    if isinstance(pair, dict):
        for k in ("prompt", "user", "text", "user_turn"):
            v = pair.get(k)
            if isinstance(v, str) and len(v.strip()) > _MIN_TURN_CHARS:
                return v.strip()
    return str(pair)


def _load_basin_centroids() -> list[list[float]]:
    """768d basin centroids from the live topics.json (the domain map)."""
    import json

    from ..state_paths import trinity_home

    path = trinity_home() / "memories" / "topics.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
    except Exception:
        return []
    basins = data.get("basins") or data.get("topics") or []
    out = []
    for b in basins:
        if isinstance(b, dict):
            cen = b.get("centroid") or b.get("vector") or b.get("embedding")
            if isinstance(cen, list) and cen:
                out.append(cen)
    return out


def select_domain_diverse_evidence(
    n: int = _EVIDENCE_N,
    pool_size: int = _POOL_SIZE,
    *,
    pairs: list | None = None,
    centroids: list[list[float]] | None = None,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[str]:
    """Round-robin the most-substantive turns across the basin map so the
    chairman sees EVERY domain (materials/finance/epistemology), not just the
    software-dominant cluster the default recency sample over-weights.

    The signal is turn LENGTH (substance) — deliberately NEUTRAL, never
    invariant-richness, so the selection can't trivially inflate the coverage
    it's meant to demonstrate. All inputs are injectable for testing.
    """
    import numpy as np

    if pairs is None:
        from .turn_pairs import iter_turn_pairs

        pairs = list(iter_turn_pairs(limit=None))
    if centroids is None:
        centroids = _load_basin_centroids()
    if not pairs or not centroids:
        return []
    if embed_fn is None:
        from ..embeddings import embed as embed_fn

    C = np.asarray(centroids, dtype=float)
    C = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)

    pool = sorted(pairs, key=lambda p: -len(_uturn(p)))[:pool_size]
    by_basin: dict[int, list[str]] = defaultdict(list)
    for p in pool:
        t = _uturn(p)
        v = np.asarray(embed_fn(t), dtype=float)
        v = v / (np.linalg.norm(v) + 1e-9)
        by_basin[int((C @ v).argmax())].append(t)

    order = sorted(by_basin, key=lambda b: -len(by_basin[b]))
    idx: dict[int, int] = defaultdict(int)
    out: list[str] = []
    while len(out) < n:
        moved = False
        for b in order:
            if idx[b] < len(by_basin[b]):
                out.append(by_basin[b][idx[b]])
                idx[b] += 1
                moved = True
                if len(out) >= n:
                    break
        if not moved:
            break
    return out


# ── prompts (the lift + the dual autonomous critique) ─────────────────────────


def build_generate_prompt(tensions: list[str], evidence: list[str]) -> str:
    """Pass 1: lift the task-tensions to cross-domain generators."""
    ev = "\n".join(f"- {t[:340]}" for t in evidence)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(tensions))
    return (
        "You are analyzing a user's preference corpus to find the DEEP GENERATING "
        "preferences behind their surface choices.\n\n"
        f"A lens pipeline extracted these {len(tensions)} task-level preference tensions "
        "from the user's software work (PROJECTIONS):\n"
        f"{numbered}\n\n"
        "HYPOTHESIS: a smaller set of 5-8 GENERATING invariants — abstract preferences "
        "that recur ACROSS the user's domains, not just software — produce all of these. "
        "A generating invariant is the SAME reflex showing up in software AND materials "
        "AND finance AND epistemology AND aesthetics.\n\n"
        f"EVIDENCE — {len(evidence)} turns sampled across the user's FULL range of domains:\n"
        f"{ev}\n\n"
        "TASK: find the generating invariants. For EACH: name (2-5 words); a terse "
        "IMPERATIVE in the user's own two-beat voice — 'X, don't Y' — that compresses the "
        "reflex into a memorable command (like Pull, don't push; Compare, don't score; "
        "Inspect, don't trust); tension X over Y; projection into >=3 DISTINCT domains with "
        "one concrete example each from the evidence; which task-tensions it explains.\n\n"
        "Reason it through, then END your reply with a fenced JSON block and nothing "
        "after it:\n"
        "```json\n"
        '{"generators": [{"name": "<2-5 words>", "imperative": "<terse two-beat command, X not Y>", "tension": "<X over Y>", '
        '"projections": {"<domain>": "<one concrete example from the evidence>"}, '
        '"task_tensions": [<int>]}]}\n'
        "```\n"
        "Each generator needs >=3 distinct domains in projections; task_tensions are the "
        "1-based numbers of the task-tensions above that it explains."
    )


def build_critique_prompt(generators_text: str, tensions: list[str], evidence: list[str]) -> str:
    """Pass 2: the dual AUTONOMOUS self-critique. Both signals are mechanical —
    no human pointing at any domain, so the loop needs no ratification."""
    ev = "\n".join(f"- {t[:300]}" for t in evidence)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(tensions))
    return (
        "You previously produced these generating invariants:\n"
        f"{generators_text}\n\n"
        f"The {len(tensions)} task-tensions they were lifted from:\n{numbered}\n\n"
        f"The cross-domain evidence:\n{ev}\n\n"
        "CRITIQUE PASS — audit for THREE general failure modes. Use no outside hints; "
        "derive everything from the tensions and evidence above:\n"
        "1. CONTRADICTION-SPLIT: if two task-tensions would rank the SAME option "
        "oppositely, they cannot share a generator — a generator is missing or one is "
        "doing double duty. Split it.\n"
        "2. OFF-PLANE GAP: the task-tensions were mined from ONE domain (software). "
        "Re-read the cross-domain evidence: is there a recurring preference the evidence "
        "shows that NONE of the task-tensions express? If so it is a generator the "
        "projection plane could not see — name it.\n"
        "3. METHOD vs CONTENT: a full lens has CONTENT generators (what is preferred) AND "
        "METHOD generators (how the user judges/ranks). Ensure both are present.\n"
        "Output the full ORTHOGONAL set (two generators are distinct iff they would rank "
        "some option differently). For each generator: tension + >=3-domain projection + "
        "which task-tensions it explains.\n\n"
        "END with the full set as a fenced JSON block and nothing after it (same schema):\n"
        "```json\n"
        '{"generators": [{"name": "<2-5 words>", "imperative": "<terse two-beat command, X not Y>", "tension": "<X over Y>", '
        '"projections": {"<domain>": "<concrete example>"}, "task_tensions": [<int>]}]}\n'
        "```"
    )


# ── dispatch (sampling-preferred, pollution-safe) ─────────────────────────────


def _default_dispatch(prompt: str) -> str:
    """Prefer MCP-sampling (#263) so the pass leaves no ~/.claude transcript to
    re-ingest; fall back to the chairman subprocess only when no sampling
    session is active (CLI use)."""
    try:
        from ..mcp_sampling import request_claude_sample

        # The abstraction + critique are big reasoning passes — give them headroom
        # over the 60s default so sampling doesn't time out into the claude -p path.
        out = request_claude_sample(prompt, max_tokens=4096, timeout_seconds=300)
        if out:
            return out
    except Exception:
        pass
    # Subprocess fallback — mirrors build_me_via_lens_pipeline's chairman pick.
    try:
        from ..config import load_config
        from ..providers import make_provider
        from ..ranker.chairman_picker import predict_strongest_chairman

        config = load_config()
        available = [
            name
            for name, p in (config.providers if config else {}).items()
            if p.enabled and p.type in ("cli", "codex")
        ]
        chairman = predict_strongest_chairman(
            "Lift task-level preference tensions to cross-domain generators.",
            available_providers=available or ["claude"],
        )
        cfg = (config.providers.get(chairman) if config else None)
        if cfg is None or not cfg.enabled:
            cfg = config.providers.get(available[0]) if (config and available) else None
        if cfg is None:
            return ""
        import pathlib

        res = make_provider(cfg).run(prompt, cwd=pathlib.Path.cwd())
        return (getattr(res, "stdout", "") or "").strip()
    except Exception:
        return ""


def _current_tensions() -> list[str]:
    """The active lens tensions (the projections to lift).

    Counts ONLY well-formed `### N. pole_a ↔ pole_b` headings via the shared
    `iter_lens_tensions` predicate — a numbered heading WITHOUT the `↔`
    separator (a malformed hand edit, a non-tension line) is NOT a tension and
    is excluded, so this "lift" count matches degeneracy/lens-health's count of
    the SAME lens.md (previously this parsed `### N. <anything>` and over-counted
    a malformed heading the degeneracy `↔` parser skipped — a cross-surface
    miscount that also polluted the generators prompt with a non-tension line).
    """
    from ..state_paths import trinity_home
    from .pipeline import iter_lens_tensions

    path = trinity_home() / "memories" / "lens.md"
    if not path.exists():
        return []
    md = path.read_text(encoding="utf-8")
    return [f"{a} ↔ {b}" for a, b in iter_lens_tensions(md)]


# ── structured parse + render (the top lens tier as cards) ────────────────────


def parse_generators(text: str) -> list[dict] | None:
    """Extract the chairman's fenced JSON block into validated generator dicts.
    Tolerant: prefers the last ```json fence, falls back to the outermost {...};
    returns None if nothing parses (the caller degrades to the raw text)."""
    import json
    import re as _re

    if not text:
        return None
    blob = None
    fences = list(_re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.S))
    if fences:
        blob = fences[-1].group(1)
    else:
        s, e = text.find("{"), text.rfind("}")
        if 0 <= s < e:
            blob = text[s : e + 1]
    if not blob:
        return None
    data = None
    for candidate in (blob, blob[blob.find("{") : blob.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
            break
        except Exception:
            continue
    if data is None:
        return None
    gens = (
        data.get("generators")
        if isinstance(data, dict)
        else (data if isinstance(data, list) else None)
    )
    if not isinstance(gens, list):
        return None
    out: list[dict] = []
    for g in gens:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name", "")).strip()
        imperative = str(g.get("imperative", "")).strip()
        tension = str(g.get("tension", "")).strip()
        proj = g.get("projections")
        proj = {str(k): str(v) for k, v in proj.items()} if isinstance(proj, dict) else {}
        tt = g.get("task_tensions")
        tt = (
            [int(t) for t in tt if isinstance(t, (int, float)) and not isinstance(t, bool)]
            if isinstance(tt, list)
            else []
        )
        if name and tension and proj:
            out.append({"name": name, "imperative": imperative, "tension": tension,
                        "projections": proj, "task_tensions": tt})
    return out or None


def render_generators_cards(generators: list[dict]) -> str:
    """Structured generators -> the top lens tier (markdown cards). The task
    tensions demote to evidence under each generator (the ``task_tensions`` ids)."""
    lines = [
        "## Generators (cross-domain invariants)",
        "",
        "*The reflex under the surface — the same preference across your domains. "
        "The task tensions are its projections.*",
        "",
    ]
    for i, g in enumerate(generators, 1):
        # Lead with the user-voice imperative (the load-bearing compression); the
        # geometry's name + tension becomes the explanatory subtitle underneath.
        headline = (g.get("imperative") or "").strip() or g["name"]
        lines.append(f"### {i}. {headline}")
        subtitle = g["tension"] if headline == g["name"] else f"{g['name']} — {g['tension']}"
        lines.append(f"*{subtitle}*")
        lines.append("")
        for dom, ex in g["projections"].items():
            lines.append(f"- **{dom}** — {ex}")
        if g.get("task_tensions"):
            ids = ", ".join(str(t) for t in g["task_tensions"])
            lines.append("")
            lines.append(f"*Projects task-tensions: {ids}*")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_generators(
    *,
    n_evidence: int = _EVIDENCE_N,
    dispatch_fn: Callable[[str], str] | None = None,
    tensions: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict:
    """Run the full lift: select domain-diverse evidence, abstract to
    generators, then the dual autonomous self-critique. Returns the result;
    persists NOTHING (the founder reviews before any wiring).

    ``dispatch_fn`` is injectable (text -> text) for testing; defaults to the
    sampling-preferred dispatch.
    """
    dispatch = dispatch_fn or _default_dispatch
    if tensions is None:
        tensions = _current_tensions()
    if evidence is None:
        evidence = select_domain_diverse_evidence(n=n_evidence)
    if not tensions or not evidence:
        return {
            "ok": False,
            "reason": "no tensions" if not tensions else "no evidence",
            "tensions": len(tensions),
            "evidence": len(evidence),
        }

    pass1 = dispatch(build_generate_prompt(tensions, evidence)).strip()
    if not pass1:
        return {"ok": False, "reason": "pass-1 dispatch empty", "evidence": len(evidence)}
    final_raw = dispatch(build_critique_prompt(pass1, tensions, evidence)).strip() or pass1
    # The post-critique set is the answer; fall back to pass-1 if the critique's
    # JSON didn't parse.
    parsed = parse_generators(final_raw) or parse_generators(pass1)

    return {
        "ok": bool(parsed),
        "reason": None if parsed else "no parseable generators JSON in either pass",
        "evidence_turns": len(evidence),
        "task_tensions": len(tensions),
        "generators": parsed or [],
        "cards": render_generators_cards(parsed) if parsed else "",
        "raw": final_raw,
    }
