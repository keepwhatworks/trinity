"""The `ask` orchestration — single-call routing for v1.5.

`ask` is the cheap default tool Claude Code reaches for. Flow:

  1. Embed-or-substring-search the user query against the hippocampus
     (`memory.search_prompt_nodes`) — top-K similar past prompts of yours.
  2. Vote on provider from the hits using two signals (in priority):
     - council winners that came out of this prompt (chairman_winner)
     - which provider the user originally asked this prompt (PromptNode.provider)
  3. Compute trust_score from agreement strength + sample size + recency proxy.
  4. Dispatch (callback) to the chosen provider; concise structured return.

Week-1 scope per docs/spec-v1.5.md. Cortex-layer routing rules land in Week 2;
this is the kNN-only hippocampus path. Cortex rules will plug in here as a
*prior* over the vote, not a replacement.

`dispatch_fn` is intentionally an injected callable so tests can run end-to-end
without spawning real provider CLIs. Production wires `providers.make_provider(...)`
through.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .memory import search_prompt_nodes


# trust_score weights — sum to 1.0. Tunable in Week 2 after the human-calibration gate.
_W_AGREEMENT = 0.55
_W_SAMPLE = 0.30
_W_RECENCY = 0.15

# Below this trust score the response should include an escalate_hint=run_council
# so Claude in the harness can choose to call `run_council` instead of trusting
# the ask. (The hint string matches the actual MCP tool name; "compare" was the
# spec-v1.5.md proposed name but the shipped tool is `run_council`.)
ESCALATE_HINT_THRESHOLD = 0.55

# The lens-basin router was removed 2026-08-11 (council_8817ca0c57a2e4ff,
# amd_0165-67). hq_062 replayed the real consumer against its real fallback on
# 653 councils under a group-disjoint split: it FIRED on 58.6% and led the
# leader-constant 42.9% to 37.0% — and still failed its pre-registered bar at
# McNemar p=0.2649. A directional lead removes when the bar was set first and
# the burden of proof sits on the complexity. `ask` is now kNN plus a
# heuristic, with no routing table.

# Token-economy budget for `ask` returns. The answer goes straight into the
# calling agent's context window — long returns are expensive in tokens AND in
# attention. Roughly 4 chars per token, so 2000 chars ≈ 500 tokens. Truncated
# with a one-line marker so the agent knows the output was capped (and can
# call `run_council` or fetch the full council if it needs more).
ASK_ANSWER_CHAR_BUDGET = 2000
_TRUNCATION_MARKER = "\n\n[…truncated by Trinity for context economy — call `run_council` or read the council outcome for full output]"


@dataclass
class AskDecision:
    """Routing decision plus the evidence that produced it. Pre-dispatch."""

    routed_to: str
    trust_score: float
    runner_up: str | None
    vote_counts: dict[str, int]
    evidence_prompt_ids: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        out = {
            "routed_to": self.routed_to,
            "trust_score": round(self.trust_score, 3),
            "vote_counts": self.vote_counts,
            "evidence_prompt_ids": self.evidence_prompt_ids[:5],
        }
        if self.runner_up:
            out["runner_up"] = self.runner_up
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class AskResult:
    """Final tool return — what Claude in the harness gets back."""

    answer: str
    routed_to: str
    trust_score: float
    runner_up: str | None
    escalate_hint: str | None  # e.g. "run_council" when trust is low
    latency_ms: int
    decision: AskDecision

    def to_dict(self) -> dict:
        # Token-economy: keep this compact. The agent's context window is the
        # cost; verbose returns burn tokens AND attention. Truncate long
        # answers with a marker so the agent knows what was cut and can fetch
        # full output via `run_council` if needed.
        answer = self.answer
        if len(answer) > ASK_ANSWER_CHAR_BUDGET:
            answer = answer[: ASK_ANSWER_CHAR_BUDGET - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
        out = {
            "answer": answer,
            "routed_to": self.routed_to,
            "trust_score": round(self.trust_score, 3),
            "latency_ms": self.latency_ms,
        }
        if self.runner_up:
            out["runner_up"] = self.runner_up
        if self.escalate_hint:
            out["escalate_hint"] = self.escalate_hint
        return out


def decide_route(
    query: str,
    *,
    top_k: int = 5,
    available_providers: list[str] | None = None,
) -> AskDecision:
    """Pure decision logic — no dispatch. Useful for dry-run / inspection.

    kNN over the prompt index, then a heuristic. The lens-basin router that ran
    FIRST here was removed 2026-08-11 — see the module header for the numbers
    that licensed it. This is the fifth independent kill of per-context routing
    in this repo, and the widest reading is the one that settles it: on a 3-way
    choice where chance is ~33%, the BEST arm reached 42.9%. Out-of-sample the
    chairman's winner is barely predictable by anything.
    """
    hits = search_prompt_nodes(query, top_k=top_k)
    return _decide_from_hits(hits, available_providers=available_providers)






def _log_exploration_route(basin_id: str, sampled: str, rule: dict) -> None:
    """One PII-free line per Thompson route (analytics never crash) — the
    exploration is itself an instrument and must be measurable."""
    try:
        import json as _json
        from .state_paths import trinity_home
        from .utils import now_iso
        d = trinity_home() / "analytics"
        d.mkdir(parents=True, exist_ok=True)
        # Identity triple on every exploration row (2026-07-14): the sampled
        # SLUG alone can't answer "which codex won the exploration" a month
        # later — stamp what that provider currently dispatches.
        model = effort = None
        try:
            from .config import load_config
            from .providers import _effective_effort, dispatched_model
            cfg = load_config(None, required=False)
            pc = (cfg.providers or {}).get(sampled) if cfg else None
            if pc is not None:
                model = dispatched_model(pc)
                effort = _effective_effort(pc)
        except Exception:
            pass
        with (d / "exploration_routes.jsonl").open("a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "at": now_iso(), "basin": basin_id, "sampled": sampled,
                "model": model, "effort": effort,
                "margin": rule.get("margin"), "effective_n": rule.get("effective_n"),
            }) + "\n")
    except Exception:
        pass


def _decide_from_hits(
    hits: list,
    *,
    available_providers: list[str] | None,
) -> AskDecision:
    if not hits:
        return AskDecision(
            routed_to=(available_providers or ["claude"])[0],
            trust_score=0.0,
            runner_up=None,
            vote_counts={},
            evidence_prompt_ids=[],
            reason="no_history",
        )

    # Pass 1: council-derived signal — the chairman's pick on each prompt.
    # That IS the supervision signal (per the 2026-05-21 prime directive
    # "picks the answer YOU would have picked"); it's what
    # `compute_personal_routing_table()` aggregates from
    # `~/.trinity/council_outcomes/<id>.json`. The user-verdict path was
    # retired with the rest of the user-pick layer — the user just chats,
    # the chairman decides.
    # `votes` is WEIGHTED (council=1.0, transcript-origin=0.5) — it drives the
    # ranking + trust. `prompt_counts` is the HONEST per-provider tally of how many
    # past prompts actually voted, surfaced as `vote_counts`. They differ on the
    # transcript path: 5 prompts at 0.5 weight is 2.5 of weighted vote but FIVE
    # prompts — surfacing `int(2.5)=2` as "vote_counts" understated the evidence and
    # contradicted the reason ("voted from 5 …"). Keep the weighting in trust (where
    # the cold-start cap lives), report a true count to the agent.
    votes: dict[str, float] = {}
    prompt_counts: dict[str, int] = {}
    evidence: list[str] = []
    for hit in hits:
        provider = hit.chairman_winner
        if provider:
            votes[provider] = votes.get(provider, 0.0) + 1.0
            prompt_counts[provider] = prompt_counts.get(provider, 0) + 1
            if hit.prompt_id not in evidence:
                evidence.append(hit.prompt_id)

    # Pass 2 (cold-start fallback): if no council signal exists, fall back to
    # the prompt's origin provider — which CLI the user actually reached for.
    # Weak signal (0.5 weight) because "what they reached for" ≠ "what was
    # best", but better than no signal. This is what makes ask useful from
    # day-1 of install, before any councils have run. Skipped entirely when
    # any council signal is present — explicit evaluation dominates revealed
    # preference.
    reason: str
    if votes:
        # `len(evidence)` = prompts that actually carried a council signal, NOT
        # `len(hits)` (the raw neighbor count). A query whose 4 nearest prompts
        # include only 1 with a chairman pick "voted from 1", not "from 4".
        n = len(evidence)
        reason = f"voted from {n} similar past prompt{'s' if n != 1 else ''} (council signals)"
    else:
        for hit in hits:
            if getattr(hit, "provider", ""):
                votes[hit.provider] = votes.get(hit.provider, 0.0) + 0.5
                prompt_counts[hit.provider] = prompt_counts.get(hit.provider, 0) + 1
                if hit.prompt_id not in evidence:
                    evidence.append(hit.prompt_id)
        n = len(evidence)
        reason = (
            f"voted from {n} similar past prompt{'s' if n != 1 else ''} "
            "(transcript origin only — no councils yet)"
        )

    if available_providers:
        votes = {p: v for p, v in votes.items() if p in available_providers}
        prompt_counts = {p: c for p, c in prompt_counts.items() if p in available_providers}

    if not votes:
        return AskDecision(
            routed_to=(available_providers or ["claude"])[0],
            trust_score=0.0,
            runner_up=None,
            vote_counts={},
            evidence_prompt_ids=[h.prompt_id for h in hits[:5]],
            reason="hits_found_but_no_winner_signal",
        )

    # Most votes wins the route, tie-broken on the provider slug so the routed
    # model is deterministic: two providers with an EQUAL weighted vote would
    # otherwise resolve to whichever the k-NN hits happened to insert first in
    # `votes` (neighbor-order derived) — so `ask()` would route to a DIFFERENT
    # model on the same query depending on hit order. Same canon as the chairman
    # pick + routing chip (b40807ec): max votes, lexically-smallest slug.
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    primary = ranked[0][0]
    runner_up = ranked[1][0] if len(ranked) > 1 else None
    # Cold-start (transcript-origin only) signals are weaker — cap trust
    # accordingly so the escalate_hint fires more eagerly for the agent.
    trust = _compute_trust(
        votes,
        n_hits=len(hits),
        cold_start=("transcript origin only" in reason),
    )

    return AskDecision(
        routed_to=primary,
        trust_score=trust,
        runner_up=runner_up,
        # Honest prompt counts (not the weighted vote), keyed to the providers that
        # survived the availability filter.
        vote_counts={p: prompt_counts[p] for p in votes if p in prompt_counts},
        evidence_prompt_ids=evidence,
        reason=reason,
    )


def _log_dispatch_outcome(
    *,
    query: str,
    primary: str,
    succeeded_on: str | None,
    retries: int,
    failure,  # DispatchFailure | None — type avoided to keep import lazy
) -> None:
    """Append one line to ~/.trinity/analytics/dispatch_outcomes.jsonl. This
    is the canonical record for the rate-limit-saves metric named in
    docs/launch-package.md as the day-1 case-study number.

    Wrapped in try/except so analytics never breaks the dispatch path —
    spec architectural commitment: observability MUST NOT crash callers.
    """
    try:
        import json
        from datetime import datetime, timezone

        from .state_paths import dispatch_outcomes_path

        path = dispatch_outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "query_excerpt": query[:80],
            "primary": primary,
            "succeeded_on": succeeded_on,  # None when all providers failed
            "retries": retries,
            "rate_limit_save": retries > 0 and succeeded_on is not None and succeeded_on != primary,
            "failure_kind": failure.kind.value if failure is not None else None,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Telemetry must never crash the user's flow. Silent skip.
        pass


def _compute_trust(votes: dict[str, float], n_hits: int, *, cold_start: bool = False) -> float:
    """The kNN-vote trust here is the per-query confidence in the voted provider
    given the retrieved neighbours (distinct from the lens-basin routing's
    margin-as-trust). Uses 3 components
    (agreement, sample, recency-proxy=1.0) plus two hard floors:

    - **min-hits floor:** with fewer than 2 hits, trust caps at 0.5 regardless
      of agreement. One data point isn't enough signal to recommend without
      escalation.
    - **cold-start cap:** when the only signal is transcript-origin (the user
      reached for this provider before, but never explicitly evaluated it as
      best), cap trust at 0.55 — just below the escalate threshold — so the
      agent gets `escalate_hint=run_council` and can choose to fan out for an
      explicit comparison. Routes-to-something-reasonable + suggests-run_council
      is the right cold-start behavior.

    Both floors are explicit so the trust score stays interpretable: high
    trust requires either many similar past councils OR many similar past
    prompts with explicit user evaluation. Neither is true on day-1 of an
    install, so day-1 always escalates.
    """
    total = sum(votes.values()) or 1.0
    top = max(votes.values())
    agreement = top / total  # 0..1 — winner's share of the vote

    # Sample size: 5 hits is "fully informed"; fewer hits dilute trust.
    sample_size = min(1.0, n_hits / 5.0)

    # Recency proxy is a placeholder in week 1 — kNN already biases toward
    # recent because search_prompt_nodes weights by recency. Plug full
    # recency-stability metric in week 2 when cortex consolidation ships.
    recency = 1.0

    raw = _W_AGREEMENT * agreement + _W_SAMPLE * sample_size + _W_RECENCY * recency

    if n_hits < 2:
        return min(raw, 0.5)
    if cold_start:
        return min(raw, 0.55)
    return raw


def run_ask(
    query: str,
    *,
    dispatch_fn: Callable[[str, str], str],
    top_k: int = 5,
    available_providers: list[str] | None = None,
    elapsed_ms: int | None = None,
    max_retries: int = 1,
) -> AskResult:
    """End-to-end ask: route → dispatch → return. With auto-retry on
    rate-limit / billing / auth failures: if the primary's dispatch fails
    in a way that classifies as "try a different provider," try the
    runner_up (kNN second place). This is the v1.5
    killer flow — when Claude in the harness hits a rate limit, Trinity
    seamlessly continues on Codex / Gemini / local.

    `dispatch_fn(provider_name, prompt) -> answer_text` is injected so tests
    can run without provider CLIs. Production wires through
    `providers.make_provider(...).run(prompt, cwd).stdout` and raises
    ProviderError with the stderr embedded for our classifier to read.


    `max_retries=1` controls how many provider-fallback attempts to try
    after the primary fails. Each retry uses the next-best provider from
    the decision. Set to 0 to disable auto-retry.
    """
    import time

    from .dispatch_errors import classify_dispatch_failure

    decision = decide_route(
        query,
        top_k=top_k,
        available_providers=available_providers,
    )

    # Build the provider try-order: primary first, then runner_up if any.
    # `available_providers` is the upper bound; never try a provider not
    # in the harness's available pool.
    try_order = [decision.routed_to]
    if decision.runner_up and decision.runner_up != decision.routed_to:
        try_order.append(decision.runner_up)
    if available_providers:
        try_order = [p for p in try_order if p in available_providers]

    t0 = time.monotonic()
    answer: str | None = None
    actually_routed_to = decision.routed_to
    attempts = 0
    last_failure = None

    for provider_name in try_order:
        if attempts > max_retries:
            break
        attempts += 1
        try:
            answer = dispatch_fn(provider_name, query)
            actually_routed_to = provider_name
            break
        except Exception as exc:
            # Pull stderr if the exception carries it (production
            # ProviderError attaches the CLI's stderr to the message).
            stderr_excerpt = str(exc)
            failure = classify_dispatch_failure(
                provider=provider_name,
                returncode=getattr(exc, "returncode", 1),
                stderr=stderr_excerpt,
            )
            last_failure = failure
            if not failure.retry_with_other_provider:
                # Auth-recovery-only / model-not-found / unknown — bail
                # immediately. Caller decides what to do.
                raise
            # Otherwise loop continues to the next provider in try_order.

    if answer is None:
        # Exhausted retries — log the all-failed outcome before raising so
        # the case-study counter still increments (failures matter too).
        _log_dispatch_outcome(
            query=query,
            primary=decision.routed_to,
            succeeded_on=None,
            retries=attempts - 1,
            failure=last_failure,
        )
        if last_failure is not None:
            raise RuntimeError(
                f"All providers failed. Last: {last_failure.provider} "
                f"({last_failure.kind.value}). Excerpt: {last_failure.raw_stderr_excerpt[:200]}"
            )
        raise RuntimeError("dispatch failed with no classifiable error")

    # Successful dispatch — log the outcome for the rate-limit-saves metric.
    _log_dispatch_outcome(
        query=query,
        primary=decision.routed_to,
        succeeded_on=actually_routed_to,
        retries=attempts - 1,
        failure=last_failure,
    )

    dispatch_ms = int((time.monotonic() - t0) * 1000)
    total_ms = elapsed_ms if elapsed_ms is not None else dispatch_ms

    escalate = "run_council" if decision.trust_score < ESCALATE_HINT_THRESHOLD else None

    return AskResult(
        answer=answer,
        routed_to=actually_routed_to,
        trust_score=decision.trust_score,
        runner_up=decision.runner_up if actually_routed_to == decision.routed_to else decision.routed_to,
        escalate_hint=escalate,
        latency_ms=total_ms,
        decision=decision,
    )
