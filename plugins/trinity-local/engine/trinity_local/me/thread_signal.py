"""Thread-signal scoring — which conversation threads represent real, high-signal
progress (a tax decision, a design iteration, a trip plan) vs. throwaway tests
("say hi", "make the monkey better", "Reply with exactly: OK") and long
mechanical agent-loop grinds.

Validated empirically (#269, 2026-05-30) over the real 7.7k-thread corpus: pure
turn-depth over-rewards 3000-turn CLI agent loops and buries the user's actual
deliberative threads, so depth is CAPPED and the score leans on substance +
outcome markers, penalized by test-shape and agent-loop shape. A correction-density
term (model_miss acts per thread, weight 0.30) was REMOVED 2026-10-09: no two
labellers agree on which turns are corrections beyond kappa 0.26 (res_169, res_170),
so the term cannot decide what the lens learns from. Weights are not rescaled: on the
real corpus removing it moved 1 of 8,950 threads across the floor, while rescaling
would have admitted 674 new ones.

Used as the lens SEED gate: `collect_turn_pairs` skips pairs whose thread scores
below `LOW_SIGNAL_FLOOR`, so the lens learns from where you did real work and
ignores the monkey.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Throwaway / smoke-test shapes (the monkey lives here).
_TEST_RE = re.compile(
    r"\b(monkey|say hi|hello world|berrel|barrel|blob|reply with exactly|"
    r"make it better|^ok$|^better$|^hi$|^test$)\b",
    re.I,
)
# Resolution / good-outcome markers.
_OUTCOME_RE = re.compile(
    r"\b(works|shipped|done|perfect|exactly|great|fixed|merge|approved|love it|"
    r"nailed|ship it|that.?s it)\b",
    re.I,
)
# Long mechanical CLI agent-loop turns (not deliberative taste).
_AGENT_RE = re.compile(
    r"(request interrupted|uploaded_files|<file_path>|tool use|"
    r"check everything in this dir|update claude\.md|check status on|"
    r"continue the plan|raise_if_canceled)",
    re.I,
)

# A thread below this composite score is throwaway/noise — excluded from the
# lens seed. Tuned so the monkey/"OK"/"say hi" threads (≈0.0) drop while the
# shallowest genuine threads (a 4-turn real question) stay in.
LOW_SIGNAL_FLOOR = 0.08

_DEPTH_CAP = 25      # turns above this don't add depth (kills agent-loop bias)
_SUBSTANCE_CAP = 1500  # chars/turn above this don't add substance


def score_thread(user_texts: list[str]) -> float:
    """Composite 0–1 signal score for one thread from its user turns."""
    from .provenance import typed_substance

    texts = [t.strip() for t in user_texts if t and t.strip()]
    n = len(texts)
    if n == 0:
        return 0.0
    # #262 do-operator: count the user's TYPED substance, not pasted external
    # content (a model's output, an article, a code dump) — a wall of paste
    # isn't the user's authored voice and must not inflate the signal.
    avg = sum(typed_substance(t) for t in texts) / n
    depth = min(n, _DEPTH_CAP) / _DEPTH_CAP
    substance = min(avg, _SUBSTANCE_CAP) / _SUBSTANCE_CAP
    outcome = min(sum(1 for t in texts if _OUTCOME_RE.search(t)) / n * 3, 1.0)
    test_frac = sum(1 for t in texts if _TEST_RE.search(t)) / n
    agent = 1.0 if sum(1 for t in texts if _AGENT_RE.search(t)) / n > 0.1 else 0.0
    base = 0.25 * depth + 0.30 * substance + 0.15 * outcome
    return round(base * (1 - 0.85 * test_frac) * (1 - 0.6 * agent), 4)


def compute_thread_signals() -> dict[str, float]:
    """Score every thread in the corpus → {transcript_id: signal}. Reads user
    prompts (no embeddings needed)."""
    from ..memory.store import iter_prompt_nodes_no_embedding
    from .authorship import lens_only

    by_thread: dict[str, list[str]] = defaultdict(list)
    for node in lens_only(iter_prompt_nodes_no_embedding(limit=None)):
        tid = getattr(node, "transcript_id", "") or ""
        text = (getattr(node, "text", "") or "").strip()
        if tid and text:
            by_thread[tid].append(text)
    return {tid: score_thread(texts) for tid, texts in by_thread.items()}


def rank_threads(top_k: int = 20) -> list[tuple[str, float]]:
    """Highest-signal threads first — for the eval-nomination path + a surface."""
    scores = compute_thread_signals()
    return sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
