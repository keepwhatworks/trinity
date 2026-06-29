"""Load public human-preference datasets into the judge-alignment harness.

Pillar #2 of `docs/trust-and-validation.md`: the SAME alignment harness that
measures a judge against YOUR private corrections also runs against *published*
human-preference datasets (RewardBench, Chatbot Arena, Anthropic HH-RLHF, ...),
so a skeptic can reproduce the **mechanism** on public ground truth without ever
seeing your data. The argument the doc makes — "if the judge agrees with KNOWN
human preference at strong-judge rates, the same judge on your private pairs is
trustworthy for the same reason" — needed an actual loader; this is it.

The harness only ever consumes a `list[PreferencePair]`. `build_preference_pairs`
produces them from the user's ledger; this module produces the identical type
from a downloaded dataset file, so `validate_judge` / `select_aligned_judge` /
the length split / the shuffle-null control all apply unchanged.

**Offline by design.** Trinity pins `HF_HUB_OFFLINE=1` and ships no `datasets`
dependency, so we never touch the network here: the user downloads the dataset
file themselves (`huggingface-cli download ...`, a browser, `git lfs`) and points
this loader at the local path. We parse the on-disk JSONL/JSON only.

Supported on-disk shapes (auto-detected per record):

  1. **chosen / rejected** — RewardBench, HH-RLHF, most preference sets. `chosen`
     is the human-preferred completion. Each may be a plain string, a chat-message
     list (`[{"role","content"}, ...]` → last message's content), or an
     HH-RLHF-style transcript (`"...\n\nAssistant: <final>"` → text after the last
     `Assistant:`).
  2. **response_a / response_b + winner** — Chatbot-Arena shape. `winner` ∈
     {A, B, model_a, model_b, tie}; ties are dropped (no preference signal).
  3. **option_a / option_b / human_side** — the harness's own pair shape, passed
     straight through (the literal "any list of (option_a, option_b, human_side)"
     the trust doc promised).

Position-balancing mirrors `build_preference_pairs` exactly: the human-preferred
side alternates A/B by index, so a judge with a constant-answer position bias
scores ~50%, not a fake-high number. Degenerate records (preferred == rejected,
or either side empty) are skipped — same floor-guard as the user-corrections
builder.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .judge_alignment import PreferencePair


from ._textnorm import norm_for_compare as _norm  # shared eval text-compare primitive


_ASSISTANT_TURN = re.compile(r"(?:^|\n)\s*assistant\s*:\s*", re.I)


def _extract_text(value) -> str:
    """Coerce a chosen/rejected/response field into the response text.

    Handles the three forms public preference sets use for a "side":
      - a plain string (RewardBench completions) → as-is;
      - a chat-message list → the last message's `content` (the model's answer);
      - an HH-RLHF transcript string with `Assistant:` turns → the text after the
        LAST `Assistant:` marker (the diverging final turn the label is about).
    Returns "" for anything it can't turn into text, so the degenerate-skip in the
    loader drops it rather than feeding the judge an empty side.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        # Chat-message list: take the last element's content (last turn = the answer).
        for msg in reversed(value):
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            elif isinstance(msg, str) and msg.strip():
                return msg.strip()
        return ""
    if isinstance(value, dict):
        content = value.get("content")
        return content.strip() if isinstance(content, str) else ""
    if isinstance(value, str):
        text = value.strip()
        # HH-RLHF-style transcript: keep only the final Assistant turn so the two
        # sides aren't ~identical dialogue blobs that differ only at the very end.
        matches = list(_ASSISTANT_TURN.finditer(text))
        if matches:
            return text[matches[-1].end():].strip()
        return text
    return ""


def _winner_to_side(winner) -> str | None:
    """Map an Arena-style `winner` field to the slot ('A'/'B') of the preferred
    response, or None for a tie / unrecognised value (no preference signal)."""
    w = _norm(str(winner))
    if w in ("a", "model_a", "response_a", "left"):
        return "A"
    if w in ("b", "model_b", "response_b", "right"):
        return "B"
    return None  # tie / both_bad / unknown → no usable label


def _record_to_sides(record: dict) -> tuple[str, str, str] | None:
    """Turn one dataset record into (preferred_text, other_text, axis), or None
    when it carries no usable preference signal. `axis` is a coarse category label
    (subset/category/source) used for the per-axis agreement breakdown."""
    if not isinstance(record, dict):
        return None
    axis = (
        record.get("subset")
        or record.get("category")
        or record.get("source")
        or record.get("dataset")
        or "public"
    )
    axis = str(axis)

    # Shape 3: the harness's own pair form — pass straight through.
    if "human_side" in record and ("option_a" in record or "option_b" in record):
        side = _norm(str(record.get("human_side") or ""))
        a = _extract_text(record.get("option_a"))
        b = _extract_text(record.get("option_b"))
        if side == "a" and a and b:
            return a, b, axis
        if side == "b" and a and b:
            return b, a, axis
        return None

    # Shape 1: chosen / rejected (RewardBench, HH-RLHF, most preference sets).
    if "chosen" in record or "rejected" in record:
        chosen = _extract_text(record.get("chosen"))
        rejected = _extract_text(record.get("rejected"))
        if chosen and rejected:
            return chosen, rejected, axis
        return None

    # Shape 2: response_a / response_b + winner (Chatbot-Arena).
    if "winner" in record and ("response_a" in record or "response_b" in record):
        slot = _winner_to_side(record.get("winner"))
        a = _extract_text(record.get("response_a"))
        b = _extract_text(record.get("response_b"))
        if slot is None or not a or not b:
            return None
        return (a, b, axis) if slot == "A" else (b, a, axis)

    return None


def _iter_records(path: Path):
    """Yield dict records from a JSON file (top-level array, single object, or a
    `{"data": [...]}` / `{"rows": [...]}` wrapper) OR a JSONL file (one object per
    line). The two can't be told apart by first character — a JSONL file's first
    line also starts with `{` — so we try a whole-file JSON parse first and fall
    back to line-by-line only when that fails. A single malformed JSONL line is
    skipped; a wholly-unparseable file yields nothing (the caller raises ValueError)."""
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return
    # Whole-file JSON first: array, wrapper object, or a single record.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        for key in ("data", "rows", "examples", "records"):
            inner = data.get(key)
            if isinstance(inner, list):
                yield from inner
                return
        yield data
        return
    # JSONL fallback: one JSON object per line (the whole-file parse raised above).
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load_public_pairs(path: str | Path, limit: int | None = None) -> list[PreferencePair]:
    """Read a public human-preference dataset file into position-balanced
    `PreferencePair`s for the judge-alignment harness.

    `path` is a LOCAL file (JSONL or JSON) the user downloaded — no network. The
    human-preferred side alternates A/B by index (cancels position bias), and
    degenerate records (preferred == rejected, or empty) are dropped. Raises
    FileNotFoundError for a missing path and ValueError when the file parses but
    yields zero usable pairs (so the caller can tell "no preference signal" from
    "wrong file").
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"dataset file not found: {p}")

    pairs: list[PreferencePair] = []
    idx = 0
    seen_records = 0
    for record in _iter_records(p):
        seen_records += 1
        sides = _record_to_sides(record)
        if sides is None:
            continue
        preferred, other, axis = sides
        if not preferred or not other or _norm(preferred) == _norm(other):
            continue
        # Alternate which slot carries the human-preferred side (position balance).
        if idx % 2 == 0:
            option_a, option_b, human_side = preferred, other, "A"
        else:
            option_a, option_b, human_side = other, preferred, "B"
        pairs.append(PreferencePair(
            pair_id=f"pub_{idx}",
            axis=axis,
            option_a=option_a,
            option_b=option_b,
            human_side=human_side,
            source_id=str(record.get("id") or record.get("prompt_id") or f"rec_{seen_records}"),
        ))
        idx += 1
        if limit is not None and len(pairs) >= limit:
            break

    if not pairs:
        raise ValueError(
            f"parsed {seen_records} record(s) from {p.name} but found no usable "
            "preference pairs — expected `chosen`/`rejected`, `response_a`/"
            "`response_b`+`winner`, or `option_a`/`option_b`/`human_side` fields"
        )
    return pairs
