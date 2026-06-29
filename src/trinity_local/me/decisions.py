"""Stage 2 — decision extraction (one chairman call).

NOTE: the `trinity-local decision-log` CLI was retired 2026-05-27 (0
organic usage); the historical references to it below describe the
data shape that the loader still reads from any user-authored
`~/.trinity/me/decision_log.jsonl` file. See retired_names.py.

Walk MMR-sampled chunks, ask the chairman to surface every
decision-shaped utterance with structured tags. Output is
`~/.trinity/me/decisions.jsonl` — one JSON line per decision.

Schema:
{
  "id": "d_001",
  "privileged": "<what got optimized for>",
  "sacrificed": "<what got traded away>",
  "valence": "satisfaction|regret|unresolved|correction|cost",
  "basin": "<basin id from stage 1>",
  "verbatim": "<≤25 word excerpt from user turn>",
  "prompt_id": "<originating PromptNode id>"
}

Why valence has 5 values, not 3: literal regret quotes are rare; real
lens evidence often shows up as a correction ("ok that's wrong, do X
instead") or a stated cost ("this approach gives up Y"). Council
council_c63fa273bdc2ed21 ratified expanding the enum so the three
tests don't reject real lenses for lacking explicit regret.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .basins import Basin, basin_for_prompt, me_dir


VALID_VALENCES = {"satisfaction", "regret", "unresolved", "correction", "cost"}
VALID_HORIZONS = {"tactical", "strategic", "philosophical"}


@dataclass
class Decision:
    id: str
    privileged: str
    sacrificed: str
    valence: str
    basin: str | None
    verbatim: str
    prompt_id: str | None = None
    # Plan iter 1 (2026-05-23): post-launch lens-build extensions.
    # `would_flip_if`: counterfactual reasoning attached to the decision —
    # "what evidence would have flipped this trade-off?" Originally
    # captured live via the (retired) decision-log CLI (HIGH-QUALITY
    # signal, source='user_logged'); now only backfilled retroactively
    # from transcripts via Stage 2 prompt (LOWER-QUALITY — chairman may
    # rationalize; prompt explicit "leave blank if unclear, do not
    # rationalize"). The pattern: capture the highest-quality signal
    # where it lives (at the decision), not where it's cheapest to
    # extract (in the transcript).
    would_flip_if: str = ""
    # `source`: provenance of this decision. Weight hierarchy reflects
    # how directly the user asserted the signal:
    #   "transcript"   = Stage 2 chairman extracted from prompt history (weight 1.0, default)
    #   "user_logged"  = sourced from ~/.trinity/me/decision_log.jsonl — historically populated by the retired `decision-log` CLI (weight 2.0)
    #   "lens_edit"    = derived from a user edit to lens.md — strongest signal,
    #                    user is directly asserting taste rather than reacting
    #                    to council output (weight 3.0; #140 slice 2)
    source: str = "transcript"
    # `logged_at`: ISO8601 timestamp for user_logged / lens_edit decisions;
    # blank for transcript-extracted (the prompt_id already provides chronology).
    logged_at: str = ""
    # `weight`: pair-mining evidence weight. Defaults to 1.0; user-logged
    # decisions get 2.0 so the pair-miner treats them as load-bearing
    # evidence over transcript-derived decisions.
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "privileged": self.privileged,
            "sacrificed": self.sacrificed,
            "valence": self.valence,
            "basin": self.basin,
            "verbatim": self.verbatim,
            "prompt_id": self.prompt_id,
        }
        # Only emit non-default fields to keep transcript-extracted lines compact.
        # User-logged + lens-edit decisions always emit the full shape.
        if self.would_flip_if:
            out["would_flip_if"] = self.would_flip_if
        if self.source != "transcript":
            out["source"] = self.source
        if self.logged_at:
            out["logged_at"] = self.logged_at
        if self.weight != 1.0:
            out["weight"] = self.weight
        return out


def decision_log_path() -> Path:
    """Append-only JSONL of live-captured strategic decisions.

    Historically populated by the `decision-log` CLI (retired 2026-05-27,
    0 organic usage); users wanting to keep the high-quality at-decision-
    time signal can append JSONL to this path by hand — the pair-miner
    still reads it first at 2x weight. Pair-mining backfills the long
    tail from transcripts at default 1x weight.
    """
    return me_dir() / "decision_log.jsonl"


def render_extraction_prompt(samples: list[dict], basins: list[Basin]) -> str:
    """Render the chairman prompt for stage 2.

    `samples` is a list of `{prompt_id, text, basin}` dicts (basin tag is
    a hint for the chairman to surface basin-id in its output, but the
    actual ground-truth basin is re-attached deterministically after
    parsing — chairman output is not trusted for the post-filter step).
    """
    basin_summary = "\n".join(
        f"  {b.id}: {', '.join(b.top_terms) or '(no distinctive terms)'} ({b.size} prompts)"
        for b in basins[:20]
    )
    chunks = []
    for i, s in enumerate(samples):
        prompt_id = s.get("prompt_id") or f"sample_{i}"
        basin = s.get("basin") or "?"
        text = (s.get("text") or "").strip().replace("\n", " ")
        if len(text) > 600:
            text = text[:600] + "…"
        chunks.append(f"[{prompt_id} · basin={basin}] {text}")
    chunk_block = "\n\n".join(chunks)

    return f"""You are extracting decision-shaped utterances from a user's prompt history.
A "decision" is any moment where the user CHOSE, DECLINED, REGRETTED,
RATIONALIZED, or COURSE-CORRECTED. Walk the chunks below and surface every
decision you find. Skip neutral or purely informational prompts.

CRITICAL: When labelling `privileged` and `sacrificed`, abstract one level
ABOVE the literal phrases in the verbatim. The downstream pair-miner needs
poles that recur across domains, not labels tied to one decision.

  Verbatim: "let's just pay 2% to the buyer agent and close fast"
  ❌ literal: privileged="lower buyer-agent fee", sacrificed="agent goodwill"
  ✓ abstract: privileged="momentum to close", sacrificed="relational reciprocity"

  Verbatim: "intelligence is infrastructure, not interface"
  ❌ literal: privileged="infrastructure", sacrificed="interface"
  ✓ abstract: privileged="capability hidden in structure", sacrificed="capability surfaced as features"

  Verbatim: "use the punnett square always, not the ratio"
  ❌ literal: privileged="punnett square", sacrificed="ratio"
  ✓ abstract: privileged="generative mechanism shown", sacrificed="derived shortcut"

If you can't abstract a decision (it's too domain-specific to generalize),
skip it rather than emit a literal label.

For each decision, emit ONE JSON line in this exact schema:

{{"id": "d_001", "privileged": "<what was optimized for>", "sacrificed": "<what was traded away>", "valence": "satisfaction|regret|unresolved|correction|cost", "basin": "<basin id from list below>", "verbatim": "<≤25 word excerpt from the chunk>", "prompt_id": "<the [id] from the chunk header>", "would_flip_if": "<see counterfactual rules>"}}

Valence guide:
- satisfaction: explicit endorsement of the trade
- regret: explicit regret over the trade
- unresolved: ambivalent or still-deliberating
- correction: course-correction ("no, do X instead")
- cost: stated trade-off cost ("this gives up Y")

Use correction or cost when the user shows lens evidence without literal
regret words — both count as valid evidence for pair mining.

COUNTERFACTUAL (`would_flip_if`) — IMPORTANT:
Surface the conditioning variable that gates when this decision would
flip to the opposite pole. Examples:

  privileged="momentum to close", sacrificed="relational reciprocity"
  → would_flip_if: "If we needed the buyer-agent's repeat business
    on the next deal"

  privileged="capability hidden in structure", sacrificed="capability
  surfaced as features"
  → would_flip_if: "If user-facing iteration speed were the validation
    constraint instead of architectural durability"

CRITICAL: Leave `would_flip_if` blank ("") if the chunk doesn't show
clear counterfactual reasoning. Do NOT rationalize. Retroactive
extraction is LOW-QUALITY signal by design — better to emit empty
than to invent a plausible-sounding counterfactual the user never
actually thought. The HIGH-QUALITY signal historically came from the
(retired 2026-05-27) `decision-log` CLI captured at decision-time —
users can still hand-write to decision_log.jsonl.

When in doubt: empty string is correct.

Basins from the user's corpus (id : top terms · size):
{basin_summary}

Hard cap: emit at most 80 decisions. Prefer high-signal ones (clear pole,
explicit trade-off) over weak ones. Skip chunks with no decision shape.

Output format: ONE JSON object per line, NO commentary, NO markdown
fences, NO blank lines. Each line must parse as JSON.

CHUNKS:

{chunk_block}
"""


def parse_decisions(raw: str, basins: list[Basin]) -> list[Decision]:
    """Parse chairman output into Decision objects.

    Tolerates: trailing/leading text, partial markdown fences, malformed
    lines (skipped not crashed). Re-tags basin from prompt_id ground
    truth so a chairman that ignores basin tags can't poison the
    stage 4 post-filter.
    """
    decisions: list[Decision] = []
    next_auto_id = 1
    seen_ids: set[str] = set()

    text = raw.strip()
    # Strip markdown fences if the model wrapped output despite the prompt.
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
        except json.JSONDecodeError:
            continue
        privileged = (obj.get("privileged") or "").strip()
        sacrificed = (obj.get("sacrificed") or "").strip()
        verbatim = (obj.get("verbatim") or "").strip()
        if not privileged or not sacrificed or not verbatim:
            continue
        valence = (obj.get("valence") or "").strip().lower()
        if valence not in VALID_VALENCES:
            continue
        prompt_id = (obj.get("prompt_id") or "").strip() or None
        # Ground-truth re-tag: don't trust chairman's basin field — look up
        # by prompt_id against the topology. This is what makes basin
        # tags load-bearing despite chairman drift.
        basin_id = basin_for_prompt(basins, prompt_id) if prompt_id else None
        if basin_id is None:
            raw_basin = (obj.get("basin") or "").strip()
            # Sentinel values ("?", "unknown") get nulled so the post-filter
            # treats them correctly as missing topology, not as a basin id.
            if raw_basin and raw_basin.lower() not in {"?", "unknown", "none", "n/a"}:
                basin_id = raw_basin
            else:
                basin_id = None

        d_id = (obj.get("id") or "").strip()
        if not d_id or d_id in seen_ids:
            d_id = f"d_{next_auto_id:03d}"
            while d_id in seen_ids:
                next_auto_id += 1
                d_id = f"d_{next_auto_id:03d}"
        seen_ids.add(d_id)
        next_auto_id += 1

        # Optional new fields (back-compat: missing → defaults).
        would_flip_if = (obj.get("would_flip_if") or "").strip()
        source = (obj.get("source") or "transcript").strip() or "transcript"
        logged_at = (obj.get("logged_at") or "").strip()
        try:
            weight = float(obj.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0

        decisions.append(Decision(
            id=d_id,
            privileged=privileged,
            sacrificed=sacrificed,
            valence=valence,
            basin=basin_id,
            verbatim=verbatim,
            prompt_id=prompt_id,
            would_flip_if=would_flip_if,
            source=source,
            logged_at=logged_at,
            weight=weight,
        ))
    return decisions


def load_decision_log(basins: list[Basin]) -> list[Decision]:
    """Read user-logged decisions from decision_log.jsonl.

    Returns Decision objects with `source='user_logged'` and weight=2.0
    (pair-miner treats these as load-bearing). Re-tags basin via basins
    list when prompt_id_hint resolves; otherwise leaves basin as None
    and the pair-miner uses cross-basin matching as usual.

    Missing or empty file → empty list (cold-start behavior, no crash).
    """
    path = decision_log_path()
    if not path.exists():
        return []
    out: list[Decision] = []
    next_auto_id = 1
    seen_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
        except json.JSONDecodeError:
            continue
        privileged = (obj.get("privileged") or "").strip()
        sacrificed = (obj.get("sacrificed") or "").strip()
        verbatim = (obj.get("verbatim") or "").strip()
        if not privileged or not sacrificed or not verbatim:
            continue
        valence = (obj.get("valence") or "unresolved").strip().lower()
        if valence not in VALID_VALENCES:
            valence = "unresolved"
        prompt_id_hint = (obj.get("prompt_id") or "").strip() or None
        basin_id = basin_for_prompt(basins, prompt_id_hint) if prompt_id_hint else None
        if basin_id is None:
            raw_basin = (obj.get("basin") or "").strip()
            if raw_basin and raw_basin.lower() not in {"?", "unknown", "none", "n/a"}:
                basin_id = raw_basin
        d_id = (obj.get("id") or "").strip()
        if not d_id or d_id in seen_ids:
            d_id = f"u_{next_auto_id:03d}"
            while d_id in seen_ids:
                next_auto_id += 1
                d_id = f"u_{next_auto_id:03d}"
        seen_ids.add(d_id)
        next_auto_id += 1
        out.append(Decision(
            id=d_id,
            privileged=privileged,
            sacrificed=sacrificed,
            valence=valence,
            basin=basin_id,
            verbatim=verbatim,
            prompt_id=prompt_id_hint,
            would_flip_if=(obj.get("would_flip_if") or "").strip(),
            source="user_logged",
            logged_at=(obj.get("logged_at") or "").strip(),
            weight=float(obj.get("weight", 2.0)),
        ))
    return out


