"""User-facing lens-trustworthiness self-test — "run the tests we run".

Trinity's #1 recurring bug shape is a green check while the GENERATED data is
degenerate (data_sampling_principle): a lens built on the SHA-1 TF-IDF fallback,
collapsed into one mega-basin, or polluted by a single agent-loop template LOOKS
built — but the chairman reading it stands in for a caricature, not you. The
maintainers catch these by hand (`degeneracy.run_degeneracy_sweep` + the
`health_checks` quality probes). This module PRODUCTIZES that discipline: it runs
the same checks and returns an honest per-dimension verdict that ABSTAINS rather
than greens a degenerate lens, so a user can trust (or distrust) their own lens.

It's also the credibility substrate for the fusion benchmark — you cannot publish
a "your lens beats X" number on a lens this surface would refuse to bless.

Read-only: never mutates ~/.trinity. Never raises — every dimension self-reports
(an environment blow-up degrades to an `abstain`, not a crash; CLAUDE.md
"analytics never crash").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── status vocabulary ────────────────────────────────────────────────────────
OK = "ok"             # the dimension is healthy
WEAK = "weak"         # real but thin/concentrated — usable, not yet strong
DEGRADED = "degraded" # actively untrustworthy — a known degeneracy is present
ABSTAIN = "abstain"   # not measurable here (e.g. semantic check under TF-IDF)
ABSENT = "absent"     # nothing built yet to check

_GLYPH = {OK: "✓", WEAK: "~", DEGRADED: "✗", ABSTAIN: "–", ABSENT: "·"}

# ── pre-registered floors (green-gate discipline: declared, not inline magic) ──
CORPUS_WEAK_FLOOR = 200          # < this many text-bearing prompts → too thin to stand in for you
COVERAGE_FLOOR = 0.70            # mirror health_checks._EMBED_COVERAGE_FLOOR
COVERAGE_MIN_NODES = 500         # below this, ingest is still ramping — don't flag coverage
BASIN_COLLAPSE_FLOOR = 0.40      # one basin holding ≥40% of turn-mass → collapsed topology
BASIN_CONCENTRATION_WARN = 0.25  # ≥25% in one basin → concentration warning (weak)
TEMPLATE_POLLUTION_DEGRADE_FRAC = 0.15  # ≥15% of basins template-concentrated → widespread (blocks trust)
NOISE_FRACTION_FLOOR = 0.35      # >35% of the corpus near the noise prototype → learning from junk

# Severity model: DEGRADED/ABSENT BLOCK trust (the lens fundamentally can't stand in
# for you — TF-IDF fallback, collapsed topology, broken/empty tensions, no corpus).
# WEAK is a surfaced CAUTION that does NOT flip the verdict (a localized blemish: a
# few polluted basins, a concentrated-but-separated topology, a producer-view slug
# leak). The point is to not cry wolf — an otherwise-excellent lens with one polluted
# basin is trustworthy-with-a-caution, not "untrustworthy".


@dataclass
class LensCheck:
    key: str
    label: str
    status: str
    summary: str
    metric: dict[str, Any] = field(default_factory=dict)
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "label": self.label, "status": self.status, "summary": self.summary}
        if self.metric:
            d["metric"] = self.metric
        if self.fix:
            d["fix"] = self.fix
        return d


@dataclass
class LensHealthReport:
    checks: list[LensCheck] = field(default_factory=list)
    trustworthy: bool = False
    verdict: str = ""

    @property
    def blocking(self) -> list[LensCheck]:
        """Dimensions that make the lens untrustworthy (the green is REFUSED)."""
        return [c for c in self.checks if c.status in (DEGRADED, ABSENT)]

    @property
    def weak(self) -> list[LensCheck]:
        return [c for c in self.checks if c.status == WEAK]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trustworthy": self.trustworthy,
            "verdict": self.verdict,
            "checks": [c.to_dict() for c in self.checks],
            "blocking": [c.key for c in self.blocking],
        }


# ── individual dimensions ────────────────────────────────────────────────────


def _corpus_and_coverage(cov: dict) -> tuple[LensCheck, LensCheck]:
    """Two dimensions off one corpus pass: depth (enough prompts to stand in for
    you) and embedding coverage (what FRACTION carries a real vector — #235 stalled
    at 66% unembedded, silently running the lens on a third of the data)."""
    text_bearing = int(cov.get("text_bearing", 0) or 0)
    embedded = int(cov.get("embedded", 0) or 0)

    if text_bearing == 0:
        corpus = LensCheck(
            "corpus", "Corpus depth", ABSENT,
            "No prompts ingested yet — there's nothing to build a lens from.",
            metric={"text_bearing": 0},
            fix="Use your CLIs (Claude Code / Codex / Antigravity), then `trinity-local lens`.",
        )
    elif text_bearing < CORPUS_WEAK_FLOOR:
        corpus = LensCheck(
            "corpus", "Corpus depth", WEAK,
            f"Only {text_bearing} prompts — thin. The lens is real but generalizes weakly "
            f"below ~{CORPUS_WEAK_FLOOR}.",
            metric={"text_bearing": text_bearing},
            fix="Keep using your CLIs; the lens sharpens as the corpus grows.",
        )
    else:
        corpus = LensCheck(
            "corpus", "Corpus depth", OK,
            f"{text_bearing:,} prompts — enough signal to stand in for you.",
            metric={"text_bearing": text_bearing},
        )

    pct = (embedded / text_bearing) if text_bearing else 0.0
    if text_bearing == 0:
        coverage = LensCheck(
            "coverage", "Embedding coverage", ABSENT,
            "No corpus to embed yet.", metric={"fraction": 0.0},
        )
    elif text_bearing < COVERAGE_MIN_NODES or pct >= COVERAGE_FLOOR:
        coverage = LensCheck(
            "coverage", "Embedding coverage", OK,
            f"{pct*100:.0f}% of the corpus is embedded ({embedded:,}/{text_bearing:,}).",
            metric={"fraction": round(pct, 4), "embedded": embedded, "text_bearing": text_bearing},
        )
    else:
        coverage = LensCheck(
            "coverage", "Embedding coverage", DEGRADED,
            f"Only {pct*100:.0f}% of the corpus is embedded "
            f"({text_bearing - embedded:,} nodes have empty vectors) — basins and the lens "
            "are silently running on the embedded fraction only.",
            metric={"fraction": round(pct, 4), "embedded": embedded, "text_bearing": text_bearing},
            fix="The MLX embedder backfills on your next active session; if it stays low the "
                "backfill has stalled (#235) — run `trinity-local lens --force`.",
        )
    return corpus, coverage


def _embedding_backend() -> LensCheck:
    """The single most load-bearing dimension: is the REAL semantic embedder live,
    or is Trinity silently on the SHA-1 TF-IDF lexical fallback? On the fallback,
    every semantic flow (basin geometry, the lens tensions, the noise filter) is
    keyword-shaped, not meaning-shaped — the lens looks built but isn't yours."""
    from . import embeddings
    try:
        live = embeddings.mlx_actually_loaded()
    except Exception as exc:  # noqa: BLE001 — never crash the report
        return LensCheck(
            "embeddings", "Semantic embeddings", ABSTAIN,
            f"Couldn't probe the embedding backend this run — {type(exc).__name__}. "
            "Re-run after `pip install 'trinity-local[mlx]'` + `trinity-local download-embedder`.",
        )
    if live:
        return LensCheck(
            "embeddings", "Semantic embeddings", OK,
            "Real 768d semantic embeddings are live (MLX) — tensions are meaning-shaped.",
        )
    return LensCheck(
        "embeddings", "Semantic embeddings", DEGRADED,
        "Running on the SHA-1 TF-IDF fallback — the lens builds on lexical (keyword) "
        "vectors, not semantic ones, so its tensions are caricatures of your taste, not it.",
        fix="Install the embedder: `pip install 'trinity-local[mlx]'` then "
            "`HF_HUB_OFFLINE=0 trinity-local download-embedder` and `trinity-local lens --force`.",
    )


def _basins(sweep_basin_findings: list[str]) -> LensCheck:
    """Topology health: is the lens spread across distinct subject basins, or has it
    COLLAPSED into one mega-blob (a single basin holding most of the turn-mass means
    the geometry can't separate your domains)? Also folds in the degeneracy sweep's
    per-basin template/scaffolding concentration (class C)."""
    import json
    from .state_paths import memories_dir

    p = memories_dir() / "topics.json"
    if not p.exists():
        return LensCheck(
            "basins", "Topic basins", ABSENT,
            "No basins built yet — the lens hasn't clustered your work into subjects.",
            fix="trinity-local lens",
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return LensCheck("basins", "Topic basins", ABSTAIN,
                         f"topics.json unreadable ({exc.__class__.__name__}).")
    if not isinstance(data, dict):
        return LensCheck("basins", "Topic basins", ABSTAIN, "topics.json is the wrong shape.")
    basins = [b for b in (data.get("basins") or []) if isinstance(b, dict)]
    sizes = [int(b.get("size", 0) or 0) for b in basins]
    total = sum(sizes)
    n = len(basins)
    if n == 0 or total == 0:
        return LensCheck(
            "basins", "Topic basins", ABSENT,
            "Basins file exists but holds no clustered turns.", fix="trinity-local lens",
        )
    largest = max(sizes) / total
    n_polluted = len(sweep_basin_findings)
    metric = {"n_basins": n, "largest_basin_share": round(largest, 4),
              "template_concentrated": n_polluted}

    if largest >= BASIN_COLLAPSE_FLOOR:
        return LensCheck(
            "basins", "Topic basins", DEGRADED,
            f"Topology collapsed: one basin holds {largest*100:.0f}% of all turns across "
            f"{n} basins — the geometry can't separate your domains.",
            metric=metric,
            fix="Re-cluster with mega-basin splitting (TRINITY_SPLIT_MEGA_BASINS=1 "
                "trinity-local dream), or feed a more diverse corpus.",
        )
    if n_polluted and n_polluted / n >= TEMPLATE_POLLUTION_DEGRADE_FRAC:
        return LensCheck(
            "basins", "Topic basins", DEGRADED,
            f"{n_polluted} of {n} basins are template-concentrated — a repeated agent-loop / "
            "scaffolding shape pollutes the topology widely, so it encodes the tool's output, "
            "not your taste.",
            metric=metric,
            fix="Tighten the ingest filter and re-run `trinity-local dream` (#248).",
        )
    if n_polluted:
        return LensCheck(
            "basins", "Topic basins", WEAK,
            f"{n_polluted} of {n} basins are template-concentrated (a repeated agent-loop shape) "
            "— localized; the rest of the topology is clean.",
            metric=metric,
            fix="Tighten the ingest filter and re-run `trinity-local dream` (#248).",
        )
    if largest >= BASIN_CONCENTRATION_WARN:
        return LensCheck(
            "basins", "Topic basins", WEAK,
            f"{n} basins, but one holds {largest*100:.0f}% of the turn-mass — concentrated. "
            "Usable; a more diverse corpus would spread it out.",
            metric=metric,
        )
    return LensCheck(
        "basins", "Topic basins", OK,
        f"{n} distinct subject basins, well spread (largest holds {largest*100:.0f}%).",
        metric=metric,
    )


def _semantic_noise(backend_ok: bool) -> LensCheck:
    """How much of the corpus sits near the 'noise' prototype (boilerplate, tool
    chatter, low-signal turns) rather than the taste centroids? Only meaningful in
    real semantic space — ABSTAINS honestly under the TF-IDF fallback."""
    if not backend_ok:
        return LensCheck(
            "noise", "Signal vs noise", ABSTAIN,
            "Not measurable on the TF-IDF fallback — needs real embeddings.",
        )
    try:
        from .me.semantic_filter import semantic_noise_report
        rep = semantic_noise_report()
    except Exception as exc:  # noqa: BLE001
        return LensCheck(
            "noise", "Signal vs noise", ABSTAIN,
            f"Couldn't measure the noise fraction this run — {type(exc).__name__}. "
            "Re-run after `trinity-local lens` rebuilds the topology.",
        )
    if not rep.get("ready"):
        return LensCheck("noise", "Signal vs noise", ABSTAIN,
                         f"Not measurable yet ({rep.get('reason', 'no basins')}).")
    frac = float(rep.get("fraction", 0.0) or 0.0)
    metric = {"fraction": round(frac, 4), "flagged": rep.get("flagged"), "total": rep.get("total")}
    if frac > NOISE_FRACTION_FLOOR:
        return LensCheck(
            "noise", "Signal vs noise", DEGRADED,
            f"{frac*100:.0f}% of the corpus reads as noise (boilerplate / tool chatter) — the "
            "lens is learning from low-signal turns.",
            metric=metric,
            fix="Re-run `trinity-local dream`; the semantic filter drops the noise tail at build.",
        )
    return LensCheck(
        "noise", "Signal vs noise", OK,
        f"{frac*100:.0f}% of the corpus reads as noise — the taste signal dominates.",
        metric=metric,
    )


def _preference_collapse(backend_ok: bool) -> LensCheck:
    """Self-preference / preference-collapse meter (RQGM 2606.26294, transposed to taste).
    Fits the lens's taste DIRECTION on a TRAIN split of your corrections and checks whether it
    still ranks HELD-OUT corrections the right way. If it ranks the rejected output above your
    own substitute on the held-out subset, the single-direction lens is collapsing — rewarding
    what already looks your shape and going blind where your taste reverses (the failure the
    regression gate can't see). Meter only: a WEAK caution, never trust-blocking — human review
    + the regression gate are the actual guardrails; this just surfaces the blind spots (the
    de-biasing adversarial samples for the next build). ABSTAINS under TF-IDF / thin data."""
    if not backend_ok:
        return LensCheck("preference_collapse", "Preference collapse", ABSTAIN,
                         "Not measurable on the TF-IDF fallback — needs real embeddings.")
    try:
        from .me.preference_collapse import lens_collapse_signal
        sig = lens_collapse_signal()
    except Exception as exc:  # noqa: BLE001
        return LensCheck("preference_collapse", "Preference collapse", ABSTAIN,
                         f"Couldn't measure this run — {type(exc).__name__}.")
    if not sig.get("ready"):
        return LensCheck("preference_collapse", "Preference collapse", ABSTAIN,
                         f"Not measurable yet ({sig.get('reason', 'thin corrections')}).")
    rate = float(sig.get("false_accept_rate", 0.0) or 0.0)
    metric = {"false_accept_rate": rate, "val_n": sig.get("val_n"), "p": sig.get("p")}
    if sig.get("verdict") == "collapse":
        return LensCheck(
            "preference_collapse", "Preference collapse", WEAK,
            f"the lens direction doesn't reliably rank held-out corrections "
            f"({rate*100:.0f}% false-accepts) — possible collapse to surface features; the "
            "divergent corrections are blind spots the next build should weight up.",
            metric=metric,
            fix="Re-run `trinity-local dream`; if it persists the lens is over-fit to one axis "
                "and the blind-spot corrections need their own tension.",
        )
    return LensCheck(
        "preference_collapse", "Preference collapse", OK,
        f"the lens direction generalizes to held-out corrections ({rate*100:.0f}% false-accepts).",
        metric=metric,
    )


def _lens_structure(sweep_lens_findings: list[str]) -> LensCheck:
    """The lens.md the chairman reads every council. Degenerate shapes: a tension
    whose poles are identical (no real tension), a tension with 0 supporting
    decisions (no evidence), an empty tensions section. Sourced from the degeneracy
    sweep's class F so this surface and the maintainers' sweep agree."""
    from .state_paths import memories_dir

    if not (memories_dir() / "lens.md").exists():
        return LensCheck(
            "lens", "Lens structure", ABSENT,
            "lens.md not built yet — no paired tensions for the chairman to read.",
            fix="trinity-local lens",
        )
    if sweep_lens_findings:
        return LensCheck(
            "lens", "Lens structure", DEGRADED,
            "; ".join(f.split(": ", 1)[-1] for f in sweep_lens_findings),
            metric={"findings": len(sweep_lens_findings)},
            fix="Re-run `trinity-local dream` to rebuild the tensions from evidence.",
        )
    return LensCheck("lens", "Lens structure", OK, "Paired tensions are well-formed and evidenced.")


def _known_issues(other_findings: list[str]) -> LensCheck:
    """Catch-all for the remaining maintainer degeneracy classes (eval prompt==gold,
    vocab code-identifier leak, Elo/routing/cortex web-era slug leaks). These are
    producer-surface bugs that corrupt the lens's downstream views."""
    if not other_findings:
        return LensCheck("known_issues", "Known degeneracies", OK,
                         "No known degenerate-data patterns on the producer surfaces.")
    # WEAK (caution), not trust-blocking: these are localized producer-view blemishes
    # (a vocab code-identifier leak, a web-era slug in a routing view) that the user
    # clears with `dream`/`consolidate` — they don't make the chairman's reading of the
    # lens geometry a caricature the way a TF-IDF/collapsed lens does.
    return LensCheck(
        "known_issues", "Known degeneracies", WEAK,
        "; ".join(other_findings),
        metric={"findings": len(other_findings)},
        fix="Re-run `trinity-local dream` (rebuilds vocab/basins); slug leaks clear on the "
            "next consolidate.",
    )


# ── sweep partition ──────────────────────────────────────────────────────────


def _partition_sweep(findings: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Route degeneracy.run_degeneracy_sweep() string findings into the dimensions
    that own them: C→basins, F→lens, everything else→known_issues. The sweep prefixes
    every finding with its class (`C/basins: …`, `F/lens: …`)."""
    basin_f, lens_f, other_f = [], [], []
    for f in findings:
        head = f.split(":", 1)[0].strip().upper()
        if head.startswith("C"):
            basin_f.append(f)
        elif head.startswith("F"):
            lens_f.append(f)
        else:
            other_f.append(f)
    return basin_f, lens_f, other_f


# ── orchestration ────────────────────────────────────────────────────────────


def _freshness() -> LensCheck:
    """Is the lens CURRENT, or standing in for an out-of-date you? A structurally
    sound lens can still be stale: the activity-gated auto-refresh stopped LANDING
    (e.g. chairman timeout/quota — the 2026-06-29 freeze: 677 new prompts, 18 days
    behind) while the lens content stayed valid. The chairman then picks the answer
    your taste-as-of-the-last-build would pick, not your recent taste. WEAK — a
    caution that does NOT flip the verdict (the lens is sound, just behind), with
    the cause + escape hatch."""
    try:
        from .cold_start import lens_freshness_status

        state, reason = lens_freshness_status()
    except Exception:  # noqa: BLE001 - a freshness read must never break the self-test
        return LensCheck("freshness", "Lens freshness", ABSTAIN, "freshness not measurable.")
    if state == "stale":
        return LensCheck(
            "freshness", "Lens freshness", WEAK,
            f"the lens isn't current — {reason}; the chairman is picking for an "
            "out-of-date you.",
            fix="run `trinity-local lens --force` to rebuild from your latest "
                "transcripts (it also surfaces the chairman error if the "
                "auto-refresh is silently failing).",
        )
    if state == "absent":
        # No lens to age — corpus/lens ABSENT checks already own the cold-start case.
        return LensCheck("freshness", "Lens freshness", ABSTAIN, "no lens built yet.")
    return LensCheck("freshness", "Lens freshness", OK,
                     "the lens reflects your latest transcripts.")


def run_lens_health() -> LensHealthReport:
    """Run every dimension against the live ~/.trinity and return an honest verdict.
    Read-only; never raises."""
    from . import embeddings, degeneracy

    try:
        cov = embeddings.prompt_node_embedding_coverage()
    except Exception:  # noqa: BLE001
        cov = {"text_bearing": 0, "embedded": 0}
    try:
        findings = degeneracy.run_degeneracy_sweep()
    except Exception:  # noqa: BLE001
        findings = []
    basin_f, lens_f, other_f = _partition_sweep(findings)

    backend = _embedding_backend()
    corpus, coverage = _corpus_and_coverage(cov)
    checks = [
        corpus,
        backend,
        coverage,
        _basins(basin_f),
        _semantic_noise(backend.status == OK),
        _preference_collapse(backend.status == OK),
        _lens_structure(lens_f),
        _freshness(),
        _known_issues(other_f),
    ]

    report = LensHealthReport(checks=checks)
    report.trustworthy = not report.blocking
    report.verdict = _verdict(report)
    return report


def _verdict(report: LensHealthReport) -> str:
    blocking = report.blocking
    if any(c.key in ("corpus", "lens", "basins") and c.status == ABSENT for c in blocking):
        return ("No lens to check yet — Trinity hasn't built one from your transcripts. "
                "Run `trinity-local lens` once you've used your CLIs.")
    if blocking:
        names = ", ".join(c.label.lower() for c in blocking)
        return (f"Not trustworthy — {len(blocking)} degeneracy(ies) would make the chairman stand "
                f"in for a caricature, not you ({names}). Fixes are listed per row.")
    if report.weak:
        names = ", ".join(c.label.lower() for c in report.weak)
        return (f"Trustworthy, with {len(report.weak)} minor caution(s) ({names}) — the lens is "
                "fundamentally sound; the flagged items are localized and listed with fixes.")
    return "Trustworthy — your lens reflects your real taste, clean across every dimension checked."


# ── human formatter ──────────────────────────────────────────────────────────


def format_human(report: LensHealthReport) -> str:
    lines = ["Lens health — can you trust the answer Trinity picks for you?", ""]
    for c in report.checks:
        glyph = _GLYPH.get(c.status, "?")
        lines.append(f"  {glyph} {c.label}: {c.summary}")
        if c.fix and c.status in (DEGRADED, WEAK, ABSENT):
            lines.append(f"      → {c.fix}")
    lines.append("")
    if not report.trustworthy:
        headline = "NOT TRUSTWORTHY"
    elif report.weak:
        headline = "TRUSTWORTHY (with cautions)"
    else:
        headline = "TRUSTWORTHY"
    lines.append(f"  {headline}. {report.verdict}")
    return "\n".join(lines)
