"""Machine-checked EVIDENCE claims — the canonical-placeholder mechanism applied
to the numbers the product actually rests on.

``scripts/render_docs.py`` has always machine-checked the INVENTORY counts (test
count, CLI subcommand count, py-file count, version…). Those are trivia: a stale
"59 CLI subcommands" embarrasses, it does not mislead. The numbers that carry the
product — "you side with Opus 4.8 on 68% of the disagreements your later work
settled", "chairman picks your branch 66.1%" — were pure prose, guarded by
nothing. A stale one of those is a false measured claim, which is the one thing
CLAUDE.md's own copy rule forbids ("Never requote a per-model number taken before
that fix").

This module closes that asymmetry: every ledger-derived percentage in a doc
surface becomes a ``<!-- canonical:NAME -->VALUE<!-- /canonical -->`` placeholder
whose VALUE is recomputed from ``~/.trinity/disagreement_ledger/summary.json``.
Drift is then a RED test, not a prose review.

THE DESIGN CONSTRAINT THAT MATTERS — three states, never two
-----------------------------------------------------------
The summary.json artifact lives in the USER's ``~/.trinity``. It does not exist
in CI, on a fresh clone, or in any isolated-``TRINITY_HOME`` test run. A guard
that quietly returns "nothing to check" there is the exact bug this repo keeps
shipping: a green over degenerate data. So ``evidence_status()`` never returns a
boolean. It returns one of THREE states, and the caller must handle each:

* ``verified``  — artifact present AND the gate passed. Values are planted; the
  live test compares them against the doc and REDS on drift.
* ``refused``   — artifact present but degenerate (tally not trustworthy, a cell
  thinner than the engine's own ``MIN_TALLY_N``, a missing record key, OR a file
  that exists but is truncated / unreadable / not a JSON object). NO values are
  planted, and the live test FAILS: the doc is asserting numbers that the live
  ledger cannot currently back.
* ``absent``    — artifact NOT ON DISK. NO values are planted, and the live test
  SKIPS with a reason naming the missing path. This is the only state that skips,
  and the ONLY thing that reaches it is a nonexistent file. A corrupt file is
  REFUSED, not absent: it is degenerate data, not a missing input. The first cut
  of this module got that wrong — it returned ``None`` for both and so skipped on
  a half-written ``trust --build`` — which is the same skip-reads-as-a-pass
  failure the module exists to prevent, reproduced inside the prevention.
  Mutation testing caught it (2026-07-31).

The skip is prevented from reading as a pass by a second, unconditional layer:
``tests/test_evidence_claim_guards.py`` exercises every extractor and the whole
refusal path against a COMMITTED SYNTHETIC FIXTURE
(``tests/fixtures/disagreement_ledger_summary.sample.json``). That layer runs in
CI with no ``~/.trinity`` at all. So a rotted registry, a broken extractor, or an
unwrapped placeholder is RED everywhere; only the live-corpus AGREEMENT check is
skippable, and it says so out loud.

THE GATE (pre-registered — reused, not invented)
------------------------------------------------
Both floors are the disagreement-ledger ENGINE's own pre-registered constants,
not numbers picked after looking at the current corpus:

* ``summary["tally_trustworthy"]`` — the K3-band + K4-discrimination + >=60
  resolved gate that already decides whether ``trust`` may SHOW a per-model
  verdict at all (``disagreement_ledger.aggregate_tally``). If the tally is not
  fit to show a user, it is not fit to plant in a doc.
* ``disagreement_ledger.MIN_TALLY_N`` (10) — the per-cell floor the engine
  already uses for ``ci_excludes_half`` and for surfacing an effort sub-cell.
  A cell thinner than that yields no claim.

A claim whose source key is missing yields ``None`` and forces ``refused`` — a
renamed model key must not silently drop a guarded number back to unguarded prose.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# The three states. Exported so callers can't typo a string literal.
VERIFIED = "verified"
REFUSED = "refused"
ABSENT = "absent"


def ledger_summary_path(home: str | Path | None = None) -> Path:
    """``~/.trinity/disagreement_ledger/summary.json`` — the artifact these
    claims are recomputed from. Resolved through ``trinity_home()`` so
    ``$TRINITY_HOME`` isolation works (a test home simply has no such file →
    ``absent``)."""
    from .state_paths import trinity_home

    base = Path(home) if home is not None else Path(trinity_home())
    return base / "disagreement_ledger" / "summary.json"


def load_ledger_summary(home: str | Path | None = None) -> dict[str, Any] | None:
    """Parse the ledger summary, or ``None`` when it is missing/unreadable/
    wrong-shape. ``None`` means "no usable summary", never "empty but fine".

    Callers that need the three states must NOT infer them from this return
    value — ``None`` conflates "no file" with "corrupt file", and those map to
    different states (see ``read_ledger_summary``)."""
    return read_ledger_summary(home)[1]


def read_ledger_summary(
    home: str | Path | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(exists_on_disk, parsed_or_None)``.

    The two facts must travel separately. A summary.json that is truncated,
    half-written by an interrupted ``trust --build``, or a JSON array instead of
    an object is PRESENT AND DEGENERATE — the module's own definition of
    REFUSED. Folding it into the same ``None`` as "no file here" would send it
    down the ABSENT path, and ABSENT is the one state that SKIPS. A corrupt
    ledger silently skipping the agreement check, while CLAUDE.md keeps
    asserting numbers nothing verified, is precisely the failure this module was
    written against. Found 2026-07-31 by mutation testing the first cut."""
    path = ledger_summary_path(home)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        # Unreadable for filesystem reasons. `exists()` separates "not there"
        # (ABSENT) from "there but we cannot read it" (a permissions/IO fault,
        # which is degenerate, not missing).
        try:
            return path.exists(), None
        except OSError:
            return False, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return True, None
    return True, (data if isinstance(data, dict) else None)


# ───────────────────────────────────────────────────────────────────────
# Extraction primitives
# ───────────────────────────────────────────────────────────────────────

def _pct(numerator: int, denominator: int, places: int = 0) -> str:
    """Half-UP percent. ``round()`` is banker's rounding — 48.5 would land on
    48, so a win rate sitting exactly on a half would render one point low and
    the guard would then enforce the wrong number forever."""
    from decimal import Decimal, ROUND_HALF_UP

    value = Decimal(numerator) * 100 / Decimal(denominator)
    quantum = Decimal(1).scaleb(-places)
    return f"{value.quantize(quantum, rounding=ROUND_HALF_UP)}%"


def _record(summary: dict, key: str) -> dict | None:
    """A ``records[<model x version>]`` cell that clears the engine's per-cell
    floor, else None. Recomputes from the raw ``w``/``l`` counts rather than the
    stored (3-dp rounded) ``win_rate`` so the rendered percent is exact."""
    from .disagreement_ledger import MIN_TALLY_N

    records = summary.get("records")
    if not isinstance(records, dict):
        return None
    cell = records.get(key)
    if not isinstance(cell, dict):
        return None
    try:
        w, l = int(cell["w"]), int(cell["l"])
    except (KeyError, TypeError, ValueError):
        return None
    if w < 0 or l < 0 or (w + l) < MIN_TALLY_N:
        return None
    return {"w": w, "l": l, "n": w + l}


def _effort_cell(summary: dict, key: str, effort: str) -> dict | None:
    """An ``effort_breakdown[<model x version>][<effort>]`` sub-cell. The engine
    only writes sub-cells that already cleared ``MIN_TALLY_N``; re-check anyway
    so a hand-edited summary can't smuggle a thin cell in."""
    from .disagreement_ledger import MIN_TALLY_N

    breakdown = summary.get("effort_breakdown")
    if not isinstance(breakdown, dict):
        return None
    per_model = breakdown.get(key)
    if not isinstance(per_model, dict):
        return None
    cell = per_model.get(effort)
    if not isinstance(cell, dict):
        return None
    try:
        w, l = int(cell["w"]), int(cell["l"])
    except (KeyError, TypeError, ValueError):
        return None
    if w < 0 or l < 0 or (w + l) < MIN_TALLY_N:
        return None
    return {"w": w, "l": l, "n": w + l}


def _effort_cells(summary: dict) -> list[tuple[str, str]] | None:
    """Every ``effort_breakdown[<model x version>][<effort>]`` sub-cell that
    clears the engine's per-cell floor, as ``(model x version, effort)`` pairs.

    WHY THIS COUNTS INSTEAD OF LOOKING UP A KEY (2026-07-31). CLAUDE.md, both
    `mcp_server.py` comment blocks and a guard docstring all asserted that the
    clean tally surfaces "exactly ONE" effort sub-cell — GPT-5.5 xhigh. That
    shape was inferred from reading the one key the sentence was about, and the
    committed fixture happened to have exactly one cell too, so nothing
    contradicted it. The live artifact carries THREE (Fable high 15W-7L,
    Gemini 3.1 high 30W-57L, GPT-5.5 xhigh 17W-12L). The load-bearing fact was
    never the count anyway — it is that NO model x version has a second level,
    so no cell has a sibling to be contrasted against. A claim about how many
    cells exist has to count them.

    Returns ``None`` — never an empty list — when the breakdown is missing,
    wrong-shape, or holds no cell above the floor: a corpus with no effort
    evidence cannot back a sentence ABOUT the effort evidence, so it must
    refuse rather than publish a confident "0".
    """
    from .disagreement_ledger import MIN_TALLY_N

    breakdown = summary.get("effort_breakdown")
    if not isinstance(breakdown, dict):
        return None
    cells: list[tuple[str, str]] = []
    for model_version, levels in breakdown.items():
        if not isinstance(levels, dict):
            return None
        for effort, cell in levels.items():
            if not isinstance(cell, dict):
                return None
            # int() throws on a non-numeric count; evidence_status turns a
            # throwing extractor into REFUSED, which is the correct outcome.
            w, l = int(cell["w"]), int(cell["l"])
            if w < 0 or l < 0:
                return None
            # Re-apply the floor even though the engine already filters on it,
            # so a hand-edited summary cannot smuggle a thin cell into a count.
            if (w + l) >= MIN_TALLY_N:
                cells.append((str(model_version), str(effort)))
    return cells or None


def _effort_cells_n(summary: dict) -> str | None:
    """How many effort sub-cells clear the floor at all."""
    cells = _effort_cells(summary)
    return None if cells is None else str(len(cells))


def _effort_max_levels_per_model(summary: dict) -> str | None:
    """The most effort levels any SINGLE model x version has above the floor.

    This is the number the "effort has produced no result" copy actually rests
    on. While it reads 1, every sub-cell is its model's only recorded level, so
    there is no within-model contrast for effort to have revealed. The day it
    reads 2, that sentence is due for a re-measure — and the doc goes red.
    """
    cells = _effort_cells(summary)
    if cells is None:
        return None
    per_model: dict[str, int] = {}
    for model_version, _effort in cells:
        per_model[model_version] = per_model.get(model_version, 0) + 1
    return str(max(per_model.values()))


def _win_pct(key: str, places: int = 0) -> Callable[[dict], str | None]:
    def extract(summary: dict) -> str | None:
        cell = _record(summary, key)
        return None if cell is None else _pct(cell["w"], cell["n"], places)
    return extract


def _effort_win_pct(key: str, effort: str, places: int = 0) -> Callable[[dict], str | None]:
    def extract(summary: dict) -> str | None:
        cell = _effort_cell(summary, key, effort)
        return None if cell is None else _pct(cell["w"], cell["n"], places)
    return extract


def _record_wl(key: str, *, verbose: bool = False) -> Callable[[dict], str | None]:
    """Win-loss record. Two renderings because the docs use two: the terse
    ``28-13`` and the explicit ``17W-12L``. Same source, so a doc can quote
    either and both stay guarded."""
    def extract(summary: dict) -> str | None:
        cell = _record(summary, key)
        if cell is None:
            return None
        return f"{cell['w']}W-{cell['l']}L" if verbose else f"{cell['w']}-{cell['l']}"
    return extract


def _effort_record_wl(key: str, effort: str, *, verbose: bool = False) -> Callable[[dict], str | None]:
    def extract(summary: dict) -> str | None:
        cell = _effort_cell(summary, key, effort)
        if cell is None:
            return None
        return f"{cell['w']}W-{cell['l']}L" if verbose else f"{cell['w']}-{cell['l']}"
    return extract


def _chairman_agreement_pct(summary: dict) -> str | None:
    from decimal import Decimal, ROUND_HALF_UP

    k3 = summary.get("k3_chairman_agreement")
    if not isinstance(k3, (int, float)) or isinstance(k3, bool):
        return None
    if not (0.0 <= float(k3) <= 1.0):
        return None
    value = Decimal(str(k3)) * 100
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _resolved_n(summary: dict) -> str | None:
    from .disagreement_ledger import K4_MIN_RESOLVED

    n = summary.get("resolved")
    if not isinstance(n, int) or isinstance(n, bool) or n < K4_MIN_RESOLVED:
        return None
    return str(n)


# ───────────────────────────────────────────────────────────────────────
# The registry
# ───────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceClaim:
    """One guarded number. ``name`` is the canonical-placeholder key; ``source``
    names the summary.json path it is recomputed from (so a reader of the doc
    can audit it without reading this file)."""

    name: str
    source: str
    description: str
    extract: Callable[[dict], str | None]


# Model keys use U+00B7 MIDDLE DOT, matching `model_identity.label()`.
_OPUS_48 = "claude · opus · 4.8"
_GEMINI_31 = "google · pro · 3.1"
_GPT_55 = "openai · flagship · 5.5"

CLAIMS: tuple[EvidenceClaim, ...] = (
    EvidenceClaim(
        name="ledger_opus48_win_pct",
        source=f"records[{_OPUS_48!r}].w / (w+l)",
        description="share of resolved disagreements the user's later work settled toward Opus 4.8",
        extract=_win_pct(_OPUS_48),
    ),
    EvidenceClaim(
        name="ledger_opus48_win_pct_1dp",
        source=f"records[{_OPUS_48!r}].w / (w+l)",
        description="same number to one decimal, where the copy contrasts it with a pre-clean figure",
        extract=_win_pct(_OPUS_48, places=1),
    ),
    EvidenceClaim(
        name="ledger_opus48_record",
        source=f"records[{_OPUS_48!r}].w-l",
        description="Opus 4.8 win-loss record at model x version",
        extract=_record_wl(_OPUS_48),
    ),
    EvidenceClaim(
        name="ledger_gemini31_win_pct",
        source=f"records[{_GEMINI_31!r}].w / (w+l)",
        description="same measure for Gemini 3.1 Pro",
        extract=_win_pct(_GEMINI_31),
    ),
    EvidenceClaim(
        name="ledger_gpt55_win_pct",
        source=f"records[{_GPT_55!r}].w / (w+l)",
        description="same measure for GPT-5.5 (indistinguishable from chance)",
        extract=_win_pct(_GPT_55),
    ),
    EvidenceClaim(
        name="ledger_gpt55_win_pct_1dp",
        source=f"records[{_GPT_55!r}].w / (w+l)",
        description="same number to one decimal, where the copy contrasts it with the xhigh sub-cell",
        extract=_win_pct(_GPT_55, places=1),
    ),
    EvidenceClaim(
        name="ledger_gpt55_xhigh_win_pct",
        source=f"effort_breakdown[{_GPT_55!r}]['xhigh'].w / (w+l)",
        description="the effort-split sub-cell that motivated keying effort as a gated secondary",
        extract=_effort_win_pct(_GPT_55, "xhigh"),
    ),
    EvidenceClaim(
        name="ledger_gpt55_xhigh_win_pct_1dp",
        source=f"effort_breakdown[{_GPT_55!r}]['xhigh'].w / (w+l)",
        description="the same sub-cell to one decimal, where the copy contrasts it with the overall rate",
        extract=_effort_win_pct(_GPT_55, "xhigh", places=1),
    ),
    EvidenceClaim(
        name="ledger_gpt55_xhigh_record",
        source=f"effort_breakdown[{_GPT_55!r}]['xhigh'].w-l",
        description="that sub-cell's raw record, so the reader can see how thin it is",
        extract=_effort_record_wl(_GPT_55, "xhigh", verbose=True),
    ),
    EvidenceClaim(
        name="ledger_effort_cells_n",
        source="count of effort_breakdown[*][*] with w+l >= MIN_TALLY_N",
        description=(
            "how many effort sub-cells clear the floor — guarded because the copy "
            "spent a day asserting 'the only one' while the artifact carried three"
        ),
        extract=_effort_cells_n,
    ),
    EvidenceClaim(
        name="ledger_effort_max_levels_per_model",
        source="max over effort_breakdown[*] of cells with w+l >= MIN_TALLY_N",
        description=(
            "most effort levels any one model x version has on file; while this reads 1 "
            "no sub-cell has a sibling, so effort cannot have produced a contrast"
        ),
        extract=_effort_max_levels_per_model,
    ),
    EvidenceClaim(
        name="ledger_chairman_agreement_pct",
        source="k3_chairman_agreement",
        description="how often the chairman picks the branch the user's later work took",
        extract=_chairman_agreement_pct,
    ),
    EvidenceClaim(
        name="ledger_resolved_n",
        source="resolved",
        description="resolved disagreements the whole tally rests on",
        extract=_resolved_n,
    ),
)

CLAIM_NAMES: tuple[str, ...] = tuple(c.name for c in CLAIMS)


# ───────────────────────────────────────────────────────────────────────
# The three-state entry point
# ───────────────────────────────────────────────────────────────────────

def evidence_status(
    summary: dict[str, Any] | None = None,
    *,
    home: str | Path | None = None,
    _loaded: bool = False,
) -> tuple[str, dict[str, str], str]:
    """Return ``(state, values, reason)`` where state is VERIFIED / REFUSED /
    ABSENT.

    Values are planted ONLY in the VERIFIED state. REFUSED and ABSENT both
    return ``{}`` — but they are NOT interchangeable and callers must not
    collapse them: ABSENT means "we could not look", REFUSED means "we looked
    and the ledger cannot back these numbers right now". Conflating them is the
    skip-that-reads-as-a-pass failure this module exists to prevent.

    Pass ``summary`` to check an in-memory dict (the fixture path); omit it to
    read the live artifact under ``home`` / ``$TRINITY_HOME``.
    """
    if summary is None and not _loaded:
        exists, summary = read_ledger_summary(home)
        if summary is None:
            if not exists:
                return ABSENT, {}, (
                    f"no ledger summary at {ledger_summary_path(home)} — evidence "
                    "claims UNVERIFIED (not verified-clean; nothing was checked). "
                    "Build it with `trinity-local trust --build`."
                )
            # Present but unreadable/wrong-shape: degenerate, not missing. This
            # must REFUSE (the live guard fails) rather than skip.
            return REFUSED, {}, (
                f"ledger summary at {ledger_summary_path(home)} EXISTS but is "
                "unreadable or is not a JSON object — a corrupt artifact is "
                "degenerate data, not a missing input, so the claims it was "
                "supposed to back are refused rather than skipped. Rebuild it "
                "with `trinity-local trust --build`."
            )
    if not isinstance(summary, dict):
        return REFUSED, {}, "ledger summary is not a JSON object"

    if summary.get("tally_trustworthy") is not True:
        return REFUSED, {}, (
            "ledger tally_trustworthy is not True — the K3-band/K4 gate that "
            "decides whether `trust` may show a per-model verdict at all is "
            "failing, so no per-model number may be planted in a doc either"
        )

    values: dict[str, str] = {}
    missing: list[str] = []
    for claim in CLAIMS:
        try:
            rendered = claim.extract(summary)
        except Exception:  # noqa: BLE001 — a throwing extractor is a refusal, never a pass
            rendered = None
        if rendered is None:
            missing.append(f"{claim.name} (from {claim.source})")
        else:
            values[claim.name] = rendered

    if missing:
        return REFUSED, {}, (
            "these guarded claims could not be recomputed from the live ledger "
            "(missing key, or a cell below the engine's MIN_TALLY_N floor): "
            + "; ".join(missing)
        )
    return VERIFIED, values, (
        f"{len(values)} evidence claim(s) recomputed from "
        f"{ledger_summary_path(home)}"
    )
