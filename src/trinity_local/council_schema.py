from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# Legacy provider-slug aliases. The harness rename of 2026-05-20 changed
# the canonical Google-harness slug from "gemini" → "antigravity", but
# historical council_outcomes/*.json files on disk still carry the old
# slug in `winner`, `runner_up`, and `provider_scores` keys. Normalize at
# the from_dict boundary so personal_routing.aggregate_routing_table,
# chairman picker, launchpad rendering, and every other downstream
# consumer sees the canonical slug only. Delete this mapping once
# historical outcomes are far enough in the past to stop caring (or
# after a one-time batch-migration pass over council_outcomes/).
# Cortex.py:373,484 already does the same shape for failure_modes keys.
_LEGACY_PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "antigravity",
    # Web-capture councils (Chrome extension: claude.ai / chatgpt.com /
    # gemini.google.com) recorded BRAND names on disk while CLI councils
    # recorded slugs — so the same lab fragmented across two names
    # (chatgpt vs codex, claude_ai vs claude). Canonicalize the brand names
    # to the trio slug at the load boundary so routing tables, scoreboards,
    # and the winner-distribution stat aggregate per-lab, not per-entry-
    # surface. Raw files keep the brand name as provenance; normalization
    # happens at read.
    "chatgpt": "codex",
    "openai": "codex",
    "gpt": "codex",
    "claude_ai": "claude",
    "claude.ai": "claude",
    "anthropic": "claude",
    "google": "antigravity",
    "bard": "antigravity",
}


def normalize_provider_slug(slug: Any) -> Any:
    """Canonicalize a provider slug at the JSON-on-disk → Python boundary.

    Non-str values pass through unchanged (preserves None and any future
    type). Unknown slugs pass through unchanged (only known legacy
    aliases get rewritten). String-shaped str-likes also pass through.
    """
    if not isinstance(slug, str):
        return slug
    return _LEGACY_PROVIDER_ALIASES.get(slug, slug)


# User-facing model/brand names → internal Trinity slug. The internal slugs
# (claude / codex / antigravity) are a leaky abstraction at the CLI: a user who
# wants to score the new Gemini types `eval-run --target gemini` and it fails
# because the config key is `antigravity`. This map accepts the names people
# actually type — brand, lab, and underlying-model aliases — and resolves them
# to the slug. Superset of the legacy disk-normalization map above.
_PROVIDER_NAME_ALIASES: dict[str, str] = {
    "gemini": "antigravity",
    "google": "antigravity",
    "bard": "antigravity",
    "gpt": "codex",
    "chatgpt": "codex",
    "openai": "codex",
    "anthropic": "claude",
    "opus": "claude",
    "sonnet": "claude",
}


def same_provider(a: Any, b: Any) -> bool:
    """True iff two provider identifiers name the SAME lab, comparing canonically.

    USE THIS FOR EVERY PROVIDER COMPARISON. A raw `a == b` is wrong whenever one
    side came from disk and the other from a normalizing loader, because the same
    lab is recorded under different names by different entry surfaces: web-capture
    councils store `chatgpt` / `claude_ai` / `gemini` while CLI councils and
    `CouncilRoutingLabel.from_dict` store `codex` / `claude` / `antigravity`. So
    `"chatgpt" != "codex"` is True and *means nothing* — it is the same lab.

    This has now bitten three times (2026-07-24/25): the combine census scored a
    spurious novelty of 1.00 on ~46% of councils, and the re-chair drift counters
    reported a 54% "model effect" that was 38% pure aliasing — a wrong finding
    reported to the founder before the log line `(was chatgpt) -> codex` gave it
    away. Both bugs came from reading a raw JSON field and comparing it against a
    normalized one. The standing rule is canonicalize at EVERY boundary, not just
    display; this function is that rule made callable so the next comparison is
    correct by construction instead of by remembering.

    Empty/None on either side compares False — "no winner" is not a match.
    """
    an, bn = normalize_provider_slug(a), normalize_provider_slug(b)
    if not isinstance(an, str) or not isinstance(bn, str):
        return False
    an, bn = an.strip().lower(), bn.strip().lower()
    if not an or not bn:
        return False
    # normalize_provider_slug is case-sensitive on its alias keys, so fold first
    # and re-normalize: "ChatGPT" -> "chatgpt" -> "codex".
    return str(normalize_provider_slug(an)).lower() == str(normalize_provider_slug(bn)).lower()


def resolve_provider_alias(name: Any) -> Any:
    """Resolve a user-facing provider name (CLI input) to its internal slug.

    Case-insensitive on the alias key. Already-canonical slugs and unknown
    names pass through unchanged, so this is safe to apply unconditionally to
    `--target` / `--provider` style arguments. Non-str passes through."""
    if not isinstance(name, str):
        return name
    return _PROVIDER_NAME_ALIASES.get(name.strip().lower(), name)


# Model-brand display names for the canonical trio — the brand a reader
# recognizes (Claude / GPT / Gemini). Use on user-facing MODEL surfaces
# (share cards, the launchpad eval result bar). This is DISTINCT from the
# harness trio (Claude / Codex / Antigravity) the routing/council surfaces
# use — evals name the model that scored, routing names the dispatch lane.
# Single source so the three eval/card surfaces can't drift apart. Per #239
# (model-names-in-UI).
_MODEL_BRAND_DISPLAY: dict[str, str] = {
    "claude": "Claude",
    "codex": "GPT",
    "antigravity": "Gemini",
}


def provider_model_brand(slug: Any) -> str:
    """Model-brand display name for a provider slug (Claude / GPT / Gemini).

    Canonicalizes legacy aliases first (chatgpt→codex→GPT, claude_ai→claude→
    Claude, gemini→antigravity→Gemini) so any historical slug resolves to the
    right brand. Unknown slugs title-case as a safe fallback. Non-str / empty
    returns "" so callers can guard with a simple falsiness check. This is a
    DISPLAY label only — it does not merge routing/Elo history (that's the
    founder-gated #275 decision)."""
    if not isinstance(slug, str) or not slug:
        return ""
    canon = normalize_provider_slug(slug)
    if not isinstance(canon, str):
        canon = slug
    return _MODEL_BRAND_DISPLAY.get(canon, canon.replace("_", " ").title())


@dataclass
class PromptBundle:
    bundle_id: str
    task_cluster_id: str
    origin_session_id: str | None = None
    origin_provider: str | None = None
    task_text: str = ""
    context_excerpt: str = ""
    goal: str = ""
    comparison_instructions: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}


@dataclass
class LaunchEvent:
    launch_id: str
    bundle_id: str
    task_cluster_id: str
    mode: str
    source_provider: str | None = None
    target_provider: str | None = None
    target_model: str | None = None
    launched_at: str = ""
    handoff_reason: str | None = None
    source_session_id: str | None = None
    target_session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}


@dataclass
class CouncilMemberResult:
    provider: str
    model: str | None = None
    session_id: str | None = None
    output_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}


@dataclass
class CouncilChainStep:
    """One step of a chain-mode council (sequential refinement / relay race).

    Each step takes the prior steps' outputs as context and produces a refined
    answer. The last step's output (or the chairman synthesis over the chain)
    is treated as the final answer.
    """
    step_index: int
    model_provider: str
    model_name: str | None = None
    input_text: str = ""
    output_text: str = ""
    latency_seconds: float | None = None
    cost_estimate_usd: float | None = None
    started_at: str = ""
    completed_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step_index": self.step_index,
            "model_provider": self.model_provider,
            "input_text": self.input_text,
            "output_text": self.output_text,
        }
        for key in ("model_name", "started_at", "completed_at"):
            value = getattr(self, key)
            if value not in (None, ""):
                payload[key] = value
        if self.latency_seconds is not None:
            payload["latency_seconds"] = self.latency_seconds
        if self.cost_estimate_usd is not None:
            payload["cost_estimate_usd"] = self.cost_estimate_usd
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CouncilChainStep:
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in raw.items() if k in known}
        # Normalize the chain-step's model_provider at the load boundary —
        # same pattern as CouncilRoutingLabel.from_dict (tick 96) and
        # load_council_outcome (tick 97). Closes the rename-arc gap on
        # chain-mode council steps.
        if "model_provider" in filtered:
            filtered["model_provider"] = normalize_provider_slug(filtered["model_provider"])
        return cls(**filtered)


@dataclass
class CouncilRoutingLabel:
    """Machine-parseable verdict from the Chairman synthesis (§8.7).

    This is the supervision signal for the Phase 9 learned controller — every
    valid label is one training example. Schema mirrors the JSON contract
    appended to the chairman prompt.
    """
    winner: str
    confidence: str = "medium"  # "high" | "medium" | "low"
    runner_up: str | None = None
    task_type: str = ""
    task_domain: str = ""
    user_likely_values: list[str] = field(default_factory=list)
    provider_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    routing_lesson: str = ""
    eval_seed: str = ""
    major_failure_mode: str | None = None
    # Verifier-shaped output: the consumer-visible primitive.
    # "models agreed on these claims, disagreed on these, here's why"
    agreed_claims: list[str] = field(default_factory=list)
    disagreed_claims: list[dict[str, object]] = field(default_factory=list)
    # MACHINE-PARSABLE COMBINE (2026-07-24). The chairman's merged answer plus the
    # provenance of every claim grafted into it. Prose in PART 1 is unusable by an
    # agent, the MCP surface, or any eval — the value has to be in the JSON.
    #
    # `from_dict` filters to DECLARED dataclass fields, so a field that is not
    # named here is silently dropped no matter what the prompt asks for or the
    # parse normalizer keeps. That is the third chokepoint on this wire and it is
    # exactly how `resolution` went missing between shipping the prompt and the
    # first real council that used it (both fixed 2026-07-24).
    #
    # Each graft: {claim, from, basis: "evidence"|"lens", tension?}. `basis` is a
    # built-in instrument, not decoration — it makes "how often does taste
    # actually break a tie" countable instead of asserted (the prior estimate was
    # 1/12 chairman picks, from a one-off ablation).
    combined_answer: str = ""
    grafts: list[dict[str, object]] = field(default_factory=list)
    # FREE-FORM FACETS (2026-07-24). The fixed 7-axis rubric in provider_scores
    # (planning / execution / evaluation / specificity / user_fit / risk /
    # conciseness) cannot name the dimension that actually separated the answers on
    # a given task — "invalidation semantics", "cost realism", "migration risk".
    # So the chairman names the discriminating facet in its own words and says who
    # won it. The labels are then EMBEDDED and clustered across councils into an
    # emergent taxonomy, rather than us guessing the axes up front: the same
    # division of labour as the lens (geometry finds the structure, the model names
    # it). Each entry: {name, winner, basis}.
    facets: list[dict[str, object]] = field(default_factory=list)
    # NOTE: `best_stage_models`, `should_be_hard_case`, and `hard_case_reason`
    # were demoted in iter-3 — zero downstream consumers (verified via grep
    # across personal_routing, chairman_picker, council_review, mcp_server,
    # research/). Removing them shrinks the chairman's required JSON shape and
    # the supervision signal Phase 9 trains on. Older outcome JSONs still load
    # cleanly because `from_dict` filters unknown keys.

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "winner": self.winner,
            "confidence": self.confidence,
        }
        for key in (
            "runner_up",
            "task_type",
            "task_domain",
            "routing_lesson",
            "eval_seed",
            "major_failure_mode",
            # The machine-parsable combine. This emit list is the FOURTH chokepoint
            # on that wire — a field can be asked for in the prompt, kept by the
            # parse normalizer, and declared on the dataclass, and STILL never
            # reach disk if it is missing here. Verified by round-tripping all four
            # layers, which is how this omission was caught (2026-07-24).
            # Empty-filtered on purpose: no combined_answer means no merge
            # happened, and an empty key would imply one did.
            "combined_answer",
            "grafts",
            "facets",
        ):
            value = getattr(self, key)
            if value not in (None, "", {}, []):
                payload[key] = value
        if self.user_likely_values:
            payload["user_likely_values"] = self.user_likely_values
        if self.provider_scores:
            payload["provider_scores"] = self.provider_scores
        # agreed_claims + disagreed_claims are marked `required` in
        # council_outcome.schema.json — they MUST be emitted even when
        # empty. "Members reached no consensus" (empty agreed_claims)
        # is semantically distinct from "the writer forgot to record
        # it"; dropping the field collapses that distinction. Prior
        # behaviour filtered them as part of the general empty-list
        # filter and produced one schema-invalid outcome on disk
        # (council_08debe7fdefdfcf8.json — caught by
        # test_real_council_outcomes_validate during the post-launch
        # consistency sweep).
        payload["agreed_claims"] = self.agreed_claims
        payload["disagreed_claims"] = self.disagreed_claims
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CouncilRoutingLabel:
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in raw.items() if k in known}
        # Coerce required fields with sane defaults
        filtered.setdefault("winner", "")
        filtered.setdefault("confidence", "medium")
        # Normalize legacy provider slugs at the load boundary so all
        # downstream consumers (personal_routing aggregator, chairman
        # picker, launchpad) see the canonical slug only. See
        # _LEGACY_PROVIDER_ALIASES at the module top for the mapping.
        filtered["winner"] = normalize_provider_slug(filtered.get("winner", ""))
        if "runner_up" in filtered:
            filtered["runner_up"] = normalize_provider_slug(filtered["runner_up"])
        provider_scores = filtered.get("provider_scores")
        if isinstance(provider_scores, dict):
            normalized_scores: dict[str, Any] = {}
            for provider, sub in provider_scores.items():
                key = normalize_provider_slug(provider)
                # If both legacy + canonical keys exist on disk, prefer
                # the canonical (newest); legacy is silently overwritten.
                # No outcome should carry both, so the conflict is rare.
                if key not in normalized_scores:
                    normalized_scores[key] = sub
            filtered["provider_scores"] = normalized_scores
        elif "provider_scores" in filtered:
            # A corrupt on-disk outcome can carry provider_scores as a wrong-type
            # scalar/list (valid JSON, wrong shape). Left raw, the unified review
            # render crashed on `label.provider_scores.items()` (AttributeError:
            # 'str'/'list' object has no attribute 'items') — a 500 on the
            # persistent council page. Coerce any non-dict to {} at the load
            # boundary (the field's contract is a dict[str, dict[str, float]]).
            filtered["provider_scores"] = {}
        # The claims are list[...] by contract, but a corrupt on-disk outcome can
        # carry them as a wrong-type scalar (valid JSON, wrong shape). The unified
        # review render guards them with isinstance, but the share-card builder
        # (collect_card_data_from_outcome) subscripts `disagreed_claims[0]` and
        # iterates `agreed_claims` unguarded → "'int' object is not subscriptable"
        # crashed council-share. Coerce any non-list to [] here so EVERY consumer
        # sees the list contract.
        for _claim_field in ("agreed_claims", "disagreed_claims", "user_likely_values"):
            if _claim_field in filtered and not isinstance(filtered[_claim_field], list):
                filtered[_claim_field] = []
        return cls(**filtered)


@dataclass
class CouncilOutcome:
    council_run_id: str
    bundle_id: str
    task_cluster_id: str
    primary_provider: str
    primary_model: str | None = None
    primary_session_id: str | None = None
    agreement_score: float | None = None
    winner_provider: str | None = None
    winner_model: str | None = None
    needs_followup: bool | None = None
    differences: list[str] = field(default_factory=list)
    member_results: list[CouncilMemberResult] = field(default_factory=list)
    synthesis_prompt: str | None = None
    synthesis_output: str | None = None
    routing_label: CouncilRoutingLabel | None = None
    # Mode of this council. "parallel" = members run simultaneously, chairman synthesizes.
    # "chain" = sequential refinement; chain_steps populated.
    mode: str = "parallel"
    chain_steps: list[CouncilChainStep] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "council_run_id": self.council_run_id,
            "bundle_id": self.bundle_id,
            "task_cluster_id": self.task_cluster_id,
            "primary_provider": self.primary_provider,
            "member_results": [member.to_dict() for member in self.member_results],
            "created_at": self.created_at,
        }
        for key in (
            "primary_model",
            "primary_session_id",
            "agreement_score",
            "winner_provider",
            "winner_model",
            "needs_followup",
            "synthesis_prompt",
            "synthesis_output",
        ):
            value = getattr(self, key)
            if value not in (None, "", {}, []):
                payload[key] = value
        if self.differences:
            payload["differences"] = self.differences
        if self.routing_label is not None:
            payload["routing_label"] = self.routing_label.to_dict()
        if self.mode and self.mode != "parallel":
            payload["mode"] = self.mode
        if self.chain_steps:
            payload["chain_steps"] = [step.to_dict() for step in self.chain_steps]
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload
