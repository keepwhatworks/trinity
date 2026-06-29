"""Stage 0 — turn-pair gap extraction (the spec's load-bearing piece).

Council `council_6892781d06ac3fa8` ratified Stage 0 as the highest-leverage
import from taste-terminal because turn-pair gaps capture implicit
behavioral preference that user-turn-only extraction misses entirely.

Council `council_e7560934cb1f1d72` ratified Option A (one batch chairman
call) over per-pair (B) and two-pass triage (C). The required mitigation
is **deterministic post-validators** that drop chairman-emitted labels
when they fail simple structural checks. Without those validators, A is
just chairman skimming with nice JSON — net negative.

Four implicit rejection signal types — adapted from the external
taste-terminal spec, ratified into Trinity's pipeline by
`council_6892781d06ac3fa8` (Stage 0 as the highest-leverage import)
and `council_e7560934cb1f1d72` (Option A with deterministic
post-validators):

- REFRAME: human accepted facts, rejected frame; substituted frame holds 2+ turns
- COMPRESSION: model gave N words, human responded with ≤N/10 — what survived is wanted
- REDIRECT: multi-part answer, human follows exactly one thread, ignores the rest
- SHARPENING: human repeats model's conclusion with harder/sharper language

Output: the model_miss subset of `~/.trinity/me/preference_acts.jsonl` (the
unified preference ledger; legacy `rejections.jsonl` migrates into it). Stage 2
reads it as additional high-signal source material alongside regular sampled turns.
"""

from __future__ import annotations

import collections
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from ..ingest import is_user_facing_text
from ..memory.store import iter_prompt_nodes
from .basins import Basin, basin_for_prompt


VALID_SIGNAL_TYPES = {"REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING"}

# Stop words that crowd out distinctive overlap when checking SHARPENING.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were",
    "be", "been", "being", "of", "to", "in", "on", "at", "for", "with",
    "by", "from", "as", "this", "that", "these", "those", "it", "its",
    "do", "does", "did", "have", "has", "had", "will", "would", "should",
    "could", "can", "may", "might", "must", "not", "no", "yes", "so",
    "more", "less", "than", "then", "there", "what", "when", "where",
    "who", "how", "why", "all", "any", "some", "into", "onto", "off",
}


# ── De-weight by BATCH-PROVENANCE, not raw repetition ────────────────────────
#
# Founder direction (2026-06-03): "de weight ... so the lens reflects you, not
# your loops" — BUT "de-weight by batch-provenance, not raw repetition.
# Deliberately asking the same question of Claude, GPT, and Gemini is also
# repetition — and it's the cross-provider signal that is your entire edge, the
# thing [the engine] runs on. Raw-dedup would delete the asset. The provenance
# flag is the discriminator: collapse batch-dispatched repeats to unit weight;
# keep human cross-provider repeats at full weight. Step 1 must not eat what
# the engine eats."
#
# The discriminator on prompt nodes: a repeated prompt-key is the edge (exempt,
# kept at full weight) only if it is a *deliberate cross-provider ask* — seen
# under ≥2 LABS (Claude / OpenAI / Google) AND substantive (the founder's words:
# "deliberately asking the same QUESTION of Claude, GPT, and Gemini"). Anything
# else that repeats at machine frequency (≥`_BATCH_REPEAT_FLOOR`) is collapsed to
# `_BATCH_UNIT_WEIGHT`:
#   • single-lab loops — the autonomous /loop firing floor-plan critique 704×, an
#     md-consistency sweep 376× (all under one lab);
#   • cross-lab DRIVERS — "continue"/"status"/"yes"/"ok" appear under all 3 labs
#     not because they're a deliberate ask but because the founder types them in
#     every harness. Real cross-provider deliberations ("what's the go-to-market
#     for this app?", "launch it as a desktop app for non-coders") run ≥40 chars;
#     every driver is shorter (measured 2026-06-03: the gap sits between "commit
#     and push" (15c) and "i want to test my app by running the browser" (44c)).
# Legit dev work, do-NOT-drop: one representative survives to carry the shape.
#
# This is the clean *substrate* the cross-provider engine will later run on:
# de-weight the loops AND the drivers, keep the deliberate cross-provider asks.
_BATCH_REPEAT_FLOOR = 10        # ≥ this many repeats ⇒ machine frequency, not a human re-ask
_BATCH_UNIT_WEIGHT = 1          # collapse a batch-dispatched prompt to one representative ("unit weight")
_DELIBERATE_ASK_MIN_CHARS = 40  # a cross-lab repeat shorter than this is a driver, not a question

# Lab canon — maps every provider slug to its LAB (kept local so the me/
# layer stays decoupled). cross-LAB spread (not cross-slug) is what marks the
# deliberate cross-provider ask. cowork is Anthropic, so it folds to claude —
# claude+cowork is NOT cross-provider.
_LAB_CANON = {
    "claude_ai": "claude", "claude": "claude", "cowork": "claude",
    "chatgpt": "codex", "gpt": "codex", "openai": "codex", "codex": "codex",
    "gemini": "antigravity", "gemini_google": "antigravity", "antigravity": "antigravity",
}


def _dedup_key(text: str) -> str:
    """Normalized near-duplicate key: lowercase, whitespace-collapsed, first 80
    chars. Collapses the md-consistency variants (which differ only in
    whitespace/truncation) onto one key while keeping genuinely distinct prompts
    apart — two real prompts rarely share a verbatim 80-char prefix, but a
    dispatched loop emits the same prefix every time."""
    return re.sub(r"\s+", " ", text.lower().strip())[:80]


def classify_batch_keys(
    key_labs: dict[str, set[str]],
    key_counts: dict[str, int],
    *,
    floor: int = _BATCH_REPEAT_FLOOR,
    substance_min: int = _DELIBERATE_ASK_MIN_CHARS,
) -> set[str]:
    """The provenance discriminator (pure). Given each dedup-key's set of LABS
    and its total occurrence count, return the keys that are BATCH-DISPATCHED and
    should collapse to unit weight.

    A key repeating ≥ `floor` times is batch UNLESS it is the edge — a deliberate
    cross-provider ask, meaning it spans ≥2 labs AND is substantive
    (`len(key) >= substance_min`). That excludes two things from the edge that a
    naive ≥2-lab rule would wrongly protect:
      • single-lab loops (one lab, machine frequency) — never the edge;
      • cross-lab DRIVERS ("continue"/"ok"/"status") — they span labs only
        because the founder types them in every harness; too short to be a
        question. The substance gate is the founder's "asking the same QUESTION."
    A key under the floor is a genuine occasional re-ask and stays at full weight
    regardless of length or lab-spread."""
    batch: set[str] = set()
    for k, labs in key_labs.items():
        if key_counts.get(k, 0) < floor:
            continue  # genuine occasional turn — keep at full weight
        is_moat = len(labs) >= 2 and len(k) >= substance_min
        if not is_moat:
            batch.add(k)
    return batch


def cap_repeated_prompts(
    pairs: Iterable[tuple[str, str, Any, str]],
    *,
    batch_keys: set[str],
    unit_weight: int = _BATCH_UNIT_WEIGHT,
) -> Iterator[tuple[str, str, Any, str]]:
    """Collapse batch-dispatched prompts to `unit_weight`; pass everything else
    through at full weight.

    `pairs` yields (assistant_text, user_turn, prompt_id, next_user_text); the
    user turn (index 1) is keyed. Only keys in `batch_keys` (single-lab machine
    loops) are capped — cross-provider asks and genuine occasional repeats are
    never in `batch_keys`, so the edge is preserved. Pure + streaming +
    order-preserving (the first `unit_weight` occurrences survive)."""
    counts: collections.Counter[str] = collections.Counter()
    for pair in pairs:
        key = _dedup_key(pair[1])
        if key not in batch_keys:
            yield pair
            continue
        counts[key] += 1
        if counts[key] > unit_weight:
            continue
        yield pair


def _corpus_batch_keys(floor: int = _BATCH_REPEAT_FLOOR) -> set[str]:
    """Scan the prompt-node corpus (no embeddings) and return the batch-dispatched
    dedup-keys: per key, the set of LABS it appears under and its total count,
    over user-facing turns only, fed to `classify_batch_keys`."""
    from ..memory.store import iter_prompt_nodes_no_embedding

    key_labs: dict[str, set[str]] = {}
    key_counts: collections.Counter[str] = collections.Counter()
    for node in iter_prompt_nodes_no_embedding(limit=None):
        text = (getattr(node, "text", "") or "").strip()
        if not text or not is_user_facing_text(text):
            continue
        key = _dedup_key(text)
        lab = _LAB_CANON.get(getattr(node, "provider", "") or "", getattr(node, "provider", "") or "?")
        key_labs.setdefault(key, set()).add(lab)
        key_counts[key] += 1
    return classify_batch_keys(key_labs, dict(key_counts), floor=floor)


@dataclass
class RejectionSignal:
    """One classified turn-pair gap.

    Field defaults align with `schemas/rejection_signal.schema.json`:
    only `id`, `type`, `model_quote`, `user_substitute` are required
    by the schema; the rest are optional. The dataclass mirrors that
    contract so an external schema-conformant producer (e.g. a future
    importer that parses minimal records) can construct one without
    supplying every field. `parse_rejections` already passes all
    fields, so live behavior unchanged. Sweep iter #108 caught the
    dataclass-vs-schema asymmetry.
    """
    id: str
    type: str  # REFRAME | COMPRESSION | REDIRECT | SHARPENING
    model_quote: str
    user_substitute: str
    why_signal: str = ""
    prompt_id: str | None = None
    # Inline original prompt for records with no resolvable prompt_id — i.e.
    # provider-imported rejections (the provider supplies it; #280). Empty for
    # lens-build signals, whose prompt_id resolves to a local prompt-node.
    prompt_text: str = ""
    basin: str | None = None
    next_user_turn: str = ""  # used for REFRAME persistence check; empty if unavailable
    # The ORIGINAL question the rejected model answer was responding to — the prior
    # user turn in the transcript. Stage 0 already walks intra-transcript adjacency
    # to build the pair, so it can carry the question for free. Persisting it here
    # lets the eval read `act.question_text` instead of re-walking the whole node
    # index (builder._prior_user_turn_map) — the route-around the accretion audit
    # flagged. Empty when unavailable (first-in-transcript turn / provider import).
    question_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "model_quote": self.model_quote,
            "user_substitute": self.user_substitute,
            "why_signal": self.why_signal,
            "prompt_id": self.prompt_id,
            "prompt_text": self.prompt_text,
            "basin": self.basin,
            "next_user_turn": self.next_user_turn,
            "question_text": self.question_text,
        }


def iter_turn_pairs(limit: int | None = None):
    """Yield (assistant_text, user_turn, prompt_id, next_user_text) tuples,
    with repeated dispatched-automation prompts de-weighted.

    `next_user_text` is the user turn AFTER the current one — used for
    REFRAME persistence validation. Empty string if unavailable.

    The raw stream is routed through `cap_repeated_prompts` so a BATCH-DISPATCHED
    prompt (single lab, machine frequency — the autonomous /loop firing the same
    prompt hundreds of times) collapses to unit weight, while a deliberate
    cross-provider ask (the same question put to ≥2 labs — the edge) is kept at
    full weight (founder direction 2026-06-03: "de-weight by batch-provenance,
    not raw repetition; step 1 must not eat what the engine eats"). `limit`
    counts POST-cap pairs — the de-weighting happens before the cutoff, so the
    cap can never be defeated by an early-loop run flooding the first slots.
    """
    batch_keys = _corpus_batch_keys()
    yielded = 0
    for pair in cap_repeated_prompts(_iter_turn_pairs_raw(), batch_keys=batch_keys):
        yield pair
        yielded += 1
        if limit is not None and yielded >= limit:
            break


def _iter_turn_pairs_raw():
    """Uncapped (assistant, user, prompt_id, next_user) stream — the node logic
    only. `iter_turn_pairs` wraps this with the frequency cap + limit."""
    # Uncapped: Stage 0 turn-pair extraction needs the full corpus, not
    # just the 5000 most-recent (which contain almost no
    # preceding_assistant_text since recent ingest skips that path).
    nodes = list(iter_prompt_nodes(limit=None))
    for i, node in enumerate(nodes):
        user = (node.text or "").strip()
        if not user:
            continue
        # Re-apply the ingest scaffolding filter on READ: the index is
        # append-only, so nodes captured under an older/weaker filter still feed
        # Stage 0. Dropping them here keeps harness scaffolding (council "You
        # are…" prompts, <goal_context>, the /loop driver) out of the chairman's
        # correction-extraction input WITHOUT a re-ingest — so every ingest-filter
        # improvement is retroactively effective. (Tensions were already clean via
        # the chairman LLM; this removes the token waste + spurious-correction risk
        # of feeding it scaffolding.)
        if not is_user_facing_text(node.text):
            continue
        # Per-transcript fallback: claude_code transcripts have ~10%
        # preceding_assistant coverage but 73% following coverage — the
        # ingest path populates `following_assistant_text` on each node
        # but skipped `preceding_assistant_text` on the next-turn node.
        # When the current node lacks preceding, look back at the prior
        # node in the same transcript and use its `following_assistant_text`.
        # Lifts coverage from 37% → ~80% across providers.
        assistant = (node.preceding_assistant_text or "").strip()
        if not assistant and i > 0:
            prev = nodes[i - 1]
            if getattr(prev, "transcript_id", None) == getattr(node, "transcript_id", None):
                assistant = (prev.following_assistant_text or "").strip()
        if not assistant:
            continue
        # Best-effort next-user-turn lookup. Same per-transcript shape.
        next_user = ""
        if i + 1 < len(nodes):
            cand = nodes[i + 1]
            if getattr(cand, "transcript_id", None) == getattr(node, "transcript_id", None):
                cand_pred = (cand.preceding_assistant_text or "").strip()
                following = (node.following_assistant_text or "").strip()
                if cand_pred == following or (following and not cand_pred):
                    next_user = (cand.text or "").strip()
        yield (assistant, user, node.id, next_user)


def render_extraction_prompt(pairs: list[dict], basins: list[Basin]) -> str:
    """Render the single-batch Stage 0 chairman prompt.

    `pairs` items: {prompt_id, assistant_text, user_text, basin}. Output
    is one rejection signal per JSON line — same shape as Stage 2.
    """
    basin_summary = "\n".join(
        f"  {b.id}: {', '.join(b.top_terms) or '(no terms)'} ({b.size})"
        for b in basins[:20]
    )
    chunks = []
    for i, p in enumerate(pairs):
        prompt_id = p.get("prompt_id") or f"pair_{i}"
        basin = p.get("basin") or "?"
        a = (p.get("assistant_text") or "").strip().replace("\n", " ")
        u = (p.get("user_text") or "").strip().replace("\n", " ")
        if len(a) > 600:
            a = a[:600] + "…"
        if len(u) > 400:
            u = u[:400] + "…"
        chunks.append(
            f"[{prompt_id} · basin={basin}]\n"
            f"  MODEL: {a}\n"
            f"  USER: {u}"
        )
    pairs_block = "\n\n".join(chunks)

    return f"""You are mining the four implicit rejection signal types from
turn-pair gaps. Each pair below is a model response followed by the user's
next turn. Look at what the user did NEXT — that's the highest-signal
behavioral data we have, because it's choices made under no obligation.

THE FOUR SIGNAL TYPES

REFRAME: the user accepts the model's facts but pivots to a different
  angle without acknowledging the previous answer. The user's next turn
  introduces a substitute frame.

COMPRESSION: the model gave N words; the user replies with ≤N/10. What
  survived compression is what the user wanted; the rest was implicit
  rejection.

REDIRECT: the model gave a multi-part answer (numbered, bulleted, or
  multi-thread). The user follows exactly one thread and ignores the
  others. Ignored threads are rejections by omission.

SHARPENING: the user repeats the model's conclusion with harder language,
  higher precision, or stronger epistemic posture.
  Example: model says "creates advantages" → user says "structural
  inevitability". User accepted the conclusion, rejected the register.

EXTRACTION RULE: only emit a signal when the user's next turn genuinely
ENGAGES WITH the model's answer — corrects it, picks one option and drops the
others, points out a flaw, commits to a specific choice the model presented, or
sharpens its framing. You must be able to name a concrete delta between what the
model said and what the user steered toward.

EMIT NOTHING (skip the pair) when the user's next turn is ANY of these — they are
NOT taste corrections, and emitting them poisons the signal:
  - a brand-new or unrelated question / topic change
  - a bare acknowledgment ("yes", "ok", "thanks", "sure")
  - a pasted document, quote, or block of reference text
  - a command, a tool/workspace path (e.g. ".../workspace/skills/"), or app chatter
  - a fresh request that doesn't push back on THIS answer
On real corpora ~2/3 of turn-pairs are one of the above; when unsure, SKIP.

For each pair where you find a signal, emit ONE JSON line in this schema:

{{"id": "r_001", "type": "REFRAME|COMPRESSION|REDIRECT|SHARPENING", "model_quote": "<≤25 word excerpt from MODEL>", "user_substitute": "<≤25 word excerpt from USER>", "why_signal": "<one short sentence on the delta>", "prompt_id": "<the [id] from the pair header>"}}

Hard caps:
- Skip pairs with no clear signal. Don't force categorization.
- At most 60 emitted signals total. Quality over quantity.
- Output format: ONE JSON object per line, NO commentary, NO markdown
  fences, NO blank lines.

Basins reference (id : top terms · size):
{basin_summary}

PAIRS:

{pairs_block}
"""


def parse_rejections(raw: str, basins: list[Basin]) -> list[RejectionSignal]:
    """Parse chairman output. Re-tags basin from prompt_id ground truth.

    The id is a CONTENT hash, never the chairman's own ``id`` field. The
    chairman emits batch-local sequence ids (``r_001``, ``r_002`` per the
    prompt template), so when Stage 0 parses chunked batches separately
    (#195) those ids collide across batches — eight distinct rejections all
    land as ``r_001``. That breaks every id-keyed consumer (the unified
    ledger's identity, eval dedup). Hashing the substantive content instead
    makes ids globally unique AND stable: a genuine duplicate (same
    type/quote/substitute) collapses, distinct rejections never do. Same
    scheme as the provider-import path (`eval_import._provider_dict_to_rejection_signal`)."""
    from ..utils import stable_id

    signals: list[RejectionSignal] = []
    seen_ids: set[str] = set()

    text = raw.strip()
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
        sig_type = (obj.get("type") or "").strip().upper()
        if sig_type not in VALID_SIGNAL_TYPES:
            continue
        model_quote = (obj.get("model_quote") or "").strip()
        user_substitute = (obj.get("user_substitute") or "").strip()
        if not model_quote or not user_substitute:
            continue
        prompt_id = (obj.get("prompt_id") or "").strip() or None
        basin_id = basin_for_prompt(basins, prompt_id) if prompt_id else None
        if basin_id is None:
            basin_id = (obj.get("basin") or "").strip() or None

        # Content hash → globally unique + stable. prompt_id is folded in so
        # the same model/user phrasing against two different prompts stays
        # distinct; genuine duplicates within or across batches collapse.
        r_id = stable_id(
            "r", sig_type, model_quote[:200], user_substitute[:200], prompt_id or ""
        )
        if r_id in seen_ids:
            continue  # true content duplicate — collapse
        seen_ids.add(r_id)

        signals.append(RejectionSignal(
            id=r_id,
            type=sig_type,
            model_quote=model_quote,
            user_substitute=user_substitute,
            why_signal=(obj.get("why_signal") or "").strip(),
            prompt_id=prompt_id,
            basin=basin_id,
        ))
    return signals


# ---- deterministic validators (the load-bearing piece per council_e7560934) ----


def _word_count(text: str) -> int:
    return len(text.split())


def _keyword_set(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z\-_]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _looks_multi_part(text: str) -> bool:
    """Heuristic: is the model's answer structured as multiple threads?

    Looks for any of: numbered list (1. / 1)), bullet markers (- *), or
    ≥3 sentences. Mirrors what the spec means by "multi-part answer".
    """
    if re.search(r"^\s*\d+[.)]\s+", text, flags=re.MULTILINE):
        return True
    if re.search(r"^\s*[-*•]\s+", text, flags=re.MULTILINE):
        return True
    sentence_count = len(re.findall(r"[.!?]\s+[A-Z]", text))
    return sentence_count >= 3


def validate_signals(
    signals: list[RejectionSignal],
    pair_index: dict[str, dict],
) -> tuple[list[RejectionSignal], list[dict]]:
    """Apply deterministic post-validators per signal type.

    Returns (kept, rejected) — rejected entries carry a `reason` field
    so we can audit chairman drift over time.
    """
    kept: list[RejectionSignal] = []
    rejected: list[dict] = []

    for sig in signals:
        pair = pair_index.get(sig.prompt_id or "") or {}
        assistant = pair.get("assistant_text") or ""
        user = pair.get("user_text") or ""
        next_user = pair.get("next_user_text") or ""

        ok, reason = _validate_one(sig, assistant, user, next_user)
        if ok:
            sig.next_user_turn = next_user
            kept.append(sig)
        else:
            rejected.append({"signal": sig.to_dict(), "reason": reason})
    return kept, rejected


def _validate_one(
    sig: RejectionSignal,
    assistant: str,
    user: str,
    next_user: str,
) -> tuple[bool, str]:
    """One signal → (kept, reason). Reason explains rejection when False."""
    t = sig.type
    if t == "COMPRESSION":
        # User text must be ≤10% of model text by word count.
        a_words = _word_count(assistant)
        u_words = _word_count(user)
        if a_words == 0:
            return False, "no model text to compare"
        if u_words * 10 > a_words:
            return False, f"user/model ratio {u_words}/{a_words} > 1/10"
        return True, ""
    if t == "REDIRECT":
        # A redirect is the user steering a DIFFERENT direction than the model
        # offered — it does NOT require the model answer to be multi-part. The old
        # `_looks_multi_part` gate dropped 14/14 of the founder's real single-part
        # redirects ("No, look at customer pictures instead"; "I'll stick with
        # this") — pure signal loss in their non-software domains (sampled
        # 2026-06-06). Trust the chairman's REDIRECT call unless the user turn is
        # too thin to be a genuine steer (a bare "ok"/"yes").
        if _word_count(user) < 3:
            return False, f"redirect user turn too thin ({_word_count(user)} words)"
        return True, ""
    if t == "SHARPENING":
        # User must share ≥2 keywords with model — confirms they're
        # restating the same idea, not pivoting away.
        overlap = _keyword_set(assistant) & _keyword_set(user)
        if len(overlap) < 2:
            return False, f"keyword overlap {len(overlap)} < 2"
        return True, ""
    if t == "REFRAME":
        # Spec: substituted frame must hold ≥2 turns. Approximate by keyword
        # overlap — but with a MARGIN + a min-keyword floor. The bare
        # `return_to_model > sub_persistence` check was noisy on short, domain-
        # specific reframes (the next turn shares a couple of the model's topic
        # keywords just by staying on topic) and dropped 15/15 of the founder's
        # real reframes ("I have a Yamaha receiver, use that"; "these are from
        # Italy, find the one in my view"; sampled 2026-06-06). Only drop on a
        # CLEAR return to the model frame, and skip the test when the next turn is
        # too short to discriminate.
        if not next_user:
            # No next-turn data — be lenient (don't drop), but flag.
            return True, ""
        u_keys = _keyword_set(user)
        next_keys = _keyword_set(next_user)
        a_keys = _keyword_set(assistant)
        if not u_keys:
            return False, "user turn has no keywords"
        sub_persistence = len(u_keys & next_keys)
        return_to_model = len(a_keys & next_keys)
        if len(next_keys) >= 4 and return_to_model >= sub_persistence + 2:
            return False, f"frame did not persist (return_to_model={return_to_model} vs sub={sub_persistence})"
        return True, ""
    return False, f"unknown signal type {t}"


class DegenerateExtractionError(RuntimeError):
    """A populated preference-act corpus was about to be overwritten with a
    cliff-drop result. Almost always a transient chairman-empty run (the
    Stage 0 call returned nothing parseable), NOT a real signal change. Raised
    (by the ledger's save_preference_acts clobber guard, #209) instead of
    silently truncating — the live corpus is preserved and the would-be result
    is written to a `.degenerate` sidecar for inspection.

    Live incident 2026-05-28 (#194): a chairman blip made Stage 0
    extract 0 rejections; lens-build overwrote 49 rejections + a
    3-tension lens with empty results and reported ok:true. Recovery
    relied on a stale 3-day-old .bak that happened to exist. This guard
    removes the luck.
    """


# Clobber-guard thresholds. A new extraction must not wipe the corpus
# when it's a cliff-drop vs what's on disk: empty when ≥MIN_EXISTING
# rows exist, or below MIN_FRACTION of the existing count.
_CLOBBER_MIN_EXISTING = 5
_CLOBBER_MIN_FRACTION = 0.25


