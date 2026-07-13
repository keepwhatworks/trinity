"""Pairwise eval statistics + judge prompt — pure leaf functions. Written by gpt-5.6-luna (xhigh) under host review; tests are host-owned."""

from __future__ import annotations

import json
import math


def wilson_ci(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    try:
        if n <= 0:
            return (0.0, 1.0)

        n_float = float(n)
        wins_float = float(wins)
        z_float = abs(float(z))

        if not all(
            math.isfinite(value) for value in (n_float, wins_float, z_float)
        ):
            return (0.0, 1.0)

        proportion = min(1.0, max(0.0, wins_float / n_float))
        z_squared = z_float * z_float
        denominator = 1.0 + z_squared / n_float
        center = (proportion + z_squared / (2.0 * n_float)) / denominator
        variance = (
            proportion * (1.0 - proportion) / n_float
            + z_squared / (4.0 * n_float * n_float)
        )
        half_width = (
            z_float * math.sqrt(max(0.0, variance)) / denominator
        )

        lo = max(0.0, min(1.0, center - half_width))
        hi = max(0.0, min(1.0, center + half_width))
        return (lo, hi)
    except Exception:
        return (0.0, 1.0)


def kendall_tau(rank_a: list[str], rank_b: list[str]) -> float:
    try:
        labels_a = set(rank_a)
        labels_b = set(rank_b)
    except Exception:
        return 0.0

    if labels_a != labels_b:
        raise ValueError("rankings must contain the same labels")

    try:
        if len(rank_a) != len(rank_b) or len(labels_a) != len(rank_a):
            return 0.0

        n = len(rank_a)
        if n < 2:
            return 0.0

        positions_b = {label: index for index, label in enumerate(rank_b)}
        concordant = 0
        discordant = 0

        for i in range(n - 1):
            position_i = positions_b[rank_a[i]]
            for j in range(i + 1, n):
                if position_i < positions_b[rank_a[j]]:
                    concordant += 1
                else:
                    discordant += 1

        pairs = concordant + discordant
        return (concordant - discordant) / pairs if pairs else 0.0
    except Exception:
        return 0.0


def win_rate(wins: int, ties: int, losses: int) -> float:
    try:
        denominator = wins + ties + losses
        if denominator <= 0:
            return 0.0
        result = (wins + 0.5 * ties) / denominator
        return float(result) if math.isfinite(float(result)) else 0.0
    except Exception:
        return 0.0


PAIRWISE_JUDGE_PROMPT = """User prompt:
{prompt}

What the user said they wanted (their own words, possibly fragmentary):
{context_fragment}

Answer 1:
{answer_1}

Answer 2:
{answer_2}

Judge ONLY which answer better honors what this user wanted. Ignore length unless the user asked for brevity. Return exactly one JSON object:
{{"winner": "1"|"2"|"tie", "reason": "<one sentence>"}}
Return NOTHING else."""


def _braced_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escaped = False

    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0:
                blocks.append(text[start : index + 1])

    return blocks


def _normalize_winner(value: object) -> str | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        if value == 1:
            return "1"
        if value == 2:
            return "2"
        return None

    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if normalized in {"1", "answer 1", "answer1"}:
        return "1"
    if normalized in {"2", "answer 2", "answer2"}:
        return "2"
    if normalized in {"tie", "draw", "equal"}:
        return "tie"
    return None


def parse_pairwise_verdict(raw: str) -> tuple[str, str]:
    try:
        raw_text = raw if isinstance(raw, str) else str(raw)
    except Exception:
        raw_text = ""

    fallback = ("tie", f"unparseable: {raw_text[:80]}")

    try:
        blocks = _braced_blocks(raw_text)
        if not blocks:
            return fallback

        verdict = json.loads(blocks[-1])
        if not isinstance(verdict, dict):
            return fallback

        winner = _normalize_winner(verdict.get("winner"))
        reason = verdict.get("reason")
        if winner is None or not isinstance(reason, str):
            return fallback

        return (winner, reason)
    except Exception:
        return fallback
