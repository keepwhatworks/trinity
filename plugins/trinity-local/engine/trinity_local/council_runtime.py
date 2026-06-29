from __future__ import annotations

import json
from pathlib import Path
import re

from .council_schema import (
    CouncilChainStep,
    CouncilMemberResult,
    CouncilOutcome,
    CouncilRoutingLabel,
    LaunchEvent,
    PromptBundle,
)
from .state_paths import (
    council_outcomes_dir,
    council_runs_path,
    launch_events_path,
    prompt_bundles_dir,
)
from .utils import now_iso, stable_id


def create_prompt_bundle(
    *,
    task_cluster_id: str,
    task_text: str,
    context_excerpt: str = "",
    goal: str = "",
    comparison_instructions: str = "",
    origin_session_id: str | None = None,
    origin_provider: str | None = None,
    metadata: dict | None = None,
) -> PromptBundle:
    bundle_id = stable_id(
        "bundle",
        task_cluster_id,
        task_text[:400],
        goal[:200],
        origin_session_id or "",
    )
    return PromptBundle(
        bundle_id=bundle_id,
        task_cluster_id=task_cluster_id,
        origin_session_id=origin_session_id,
        origin_provider=origin_provider,
        task_text=task_text.strip(),
        context_excerpt=context_excerpt.strip(),
        goal=goal.strip(),
        comparison_instructions=comparison_instructions.strip(),
        created_at=now_iso(),
        metadata=metadata or {},
    )


def save_prompt_bundle(bundle: PromptBundle) -> Path:
    from .utils import atomic_write_text
    path = prompt_bundles_dir() / f"{bundle.bundle_id}.json"
    atomic_write_text(path, json.dumps(bundle.to_dict(), indent=2))
    return path


def load_prompt_bundle(path_or_bundle_id: str) -> PromptBundle:
    from .council_schema import normalize_provider_slug

    path = Path(path_or_bundle_id)
    if not path.exists():
        path = prompt_bundles_dir() / f"{path_or_bundle_id}.json"
    if not path.exists():
        # Clean, catchable error (no leaked absolute path) — CLI handlers route
        # through `load_prompt_bundle_or_exit`. Sibling of load_council_outcome /
        # load_task_record.
        raise FileNotFoundError(
            f"No prompt bundle found for id {str(path_or_bundle_id)!r}."
        )
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"prompt bundle at {path} is not a JSON object")
    # Normalize the bundle's origin_provider at the load boundary so
    # task_runtime, council_runner.source_provider, and the launch-arc
    # handoff source_providers display all see canonical slugs only.
    # Same pattern as load_council_outcome (tick 97) and
    # CouncilRoutingLabel.from_dict (tick 96).
    if "origin_provider" in raw:
        raw["origin_provider"] = normalize_provider_slug(raw["origin_provider"])
    return PromptBundle(**raw)


def load_prompt_bundle_or_exit(bundle_id: str) -> PromptBundle:
    """CLI helper: load a prompt bundle or exit cleanly (one-line error, no
    traceback) when the id is unknown — a typo'd / deleted --bundle id passed to
    council-start (or a review-link whose outcome outlived its bundle) shouldn't
    dump a stack trace. Sibling of load_council_outcome_or_exit /
    load_task_record_or_exit."""
    try:
        return load_prompt_bundle(bundle_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")


def create_launch_event(
    *,
    bundle: PromptBundle,
    mode: str,
    source_provider: str | None,
    target_provider: str | None,
    target_model: str | None = None,
    handoff_reason: str | None = None,
    source_session_id: str | None = None,
    target_session_id: str | None = None,
    metadata: dict | None = None,
) -> LaunchEvent:
    launch_id = stable_id(
        "launch",
        bundle.bundle_id,
        mode,
        source_provider or "",
        target_provider or "",
        target_model or "",
        now_iso(),
    )
    return LaunchEvent(
        launch_id=launch_id,
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        mode=mode,
        source_provider=source_provider,
        target_provider=target_provider,
        target_model=target_model,
        launched_at=now_iso(),
        handoff_reason=handoff_reason,
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        metadata=metadata or {},
    )


def append_launch_event(event: LaunchEvent) -> None:
    with launch_events_path().open("a") as handle:
        handle.write(json.dumps(event.to_dict()) + "\n")


def render_member_prompt(bundle: PromptBundle) -> str:
    """Build the council-member prompt (currently identical across providers).

    DESIGN HOLE (digital-twin vision, 2026-05-16): this function returns
    the SAME prompt for every member. The chairman is lens-conditioned
    (reads core.md before synthesis) but the dispatch is not — each
    model receives the raw user prompt with no taste-derived twist.

    The vision (per the user's persona-twin framing): each member's
    prompt should be twisted the way the user would twist it, derived
    from the lens + scoreboards. Example shape when adopted:

        render_member_prompt(bundle, provider_name="claude", lens=...)
        → prepends "User has historically rejected over-engineered
           answers (COMPRESSION rejection signal n=8, mean 0.50 on
           your corpus). Prefer the tightest answer that lands."
        render_member_prompt(bundle, provider_name="antigravity", lens=...)
        → prepends "User's REFRAME rejection rate on your corpus is
           low — don't pivot the question, answer it."

    Inputs already on disk: ~/.trinity/me/preference_acts.jsonl (per-axis
    rejection signal — the unified preference ledger), ~/.trinity/memories/lens.md
    (paired tensions — cognitive-lens artifact the chairman reads),
    ~/.trinity/scoreboard/picks.json (per-task_type winner rules).

    Not implemented yet. When implemented, callers in council_runner.py
    must pass provider_name + lens; today they call this with bundle
    alone and get a shared prompt.
    """
    sections = [
        "You are one member of a multi-model council.",
        f"Task:\n{bundle.task_text}",
    ]
    if bundle.goal:
        sections.append(f"Goal:\n{bundle.goal}")
    if bundle.context_excerpt:
        sections.append(f"Context:\n{bundle.context_excerpt}")
    if bundle.comparison_instructions:
        sections.append(f"Instructions:\n{bundle.comparison_instructions}")
    sections.append(
        "Respond directly to the task. Do not mention the council. Be concise but complete."
    )
    return "\n\n".join(sections)


def chairman_says_converged(routing_label: CouncilRoutingLabel | None) -> bool:
    """Convergence rule (consensus-iteration chain mode).

    The chairman has effectively said "the models agree" when:
      - confidence is "high"  (chairman is sure)
      - AND there are no disagreed_claims (or only trivial ones)
      - AND there is at least one agreed_claim (otherwise it's a vacuous agreement)

    This drives auto-chain stop. Tunable; if the loop runs too long in practice,
    relax to confidence != "low" + disagreed_claims <= 1.
    """
    if routing_label is None:
        return False
    if (routing_label.confidence or "").lower() != "high":
        return False
    if routing_label.disagreed_claims:
        return False
    if not routing_label.agreed_claims:
        return False
    return True


def render_consensus_round_prompt(
    bundle: PromptBundle,
    *,
    round_index: int,
    own_provider: str,
    own_prior_output: str,
    other_outputs: list[tuple[str, str]],  # list[(provider, output)]
    user_refinement: str | None = None,
) -> str:
    """Render the prompt for one member in a consensus-iteration round.

    Round 1 is the normal member prompt. Round 2+ each member sees its OWN
    prior answer + the OTHER members' prior answers, and is asked to refine.
    Optionally, the user can inject a `user_refinement` prompt that overrides
    the "refine" instruction with a new directive.
    """
    sections: list[str] = []
    sections.append(
        f"You are {own_provider} in a multi-model council, round {round_index + 1}. "
        "Earlier rounds produced these outputs. Your job: read the other models' "
        "answers, keep what's right, fix what's wrong, and produce a stronger "
        "answer than yours from the prior round. Do NOT just summarize what "
        "others said — commit to a concrete answer."
    )
    sections.append(f"Original task:\n{bundle.task_text}")
    if bundle.goal:
        sections.append(f"Goal:\n{bundle.goal}")
    if bundle.context_excerpt:
        sections.append(f"Context:\n{bundle.context_excerpt}")
    sections.append(f"Your prior-round answer:\n{own_prior_output.strip() or '(no prior answer)'}")
    if other_outputs:
        other_blocks = []
        for provider, output in other_outputs:
            body = output.strip() or "(no output)"
            other_blocks.append(f"[{provider}] said:\n{body}")
        sections.append("Other models' prior-round answers:\n\n" + "\n\n".join(other_blocks))
    if user_refinement:
        sections.append(
            "ADDITIONAL USER DIRECTIVE for this round (treat as the most important "
            f"signal — override your prior answer if it conflicts):\n{user_refinement}"
        )
    sections.append(
        "Produce your refined answer below. Be concrete and decisive. "
        "Do not mention the council. Do not summarize the other models. "
        "Just give your strongest current answer."
    )
    return "\n\n".join(sections)


def render_chain_step_prompt(
    bundle: PromptBundle,
    *,
    step_index: int,
    prior_steps: list,  # list[CouncilChainStep]
    is_final: bool = False,
) -> str:
    """Render the prompt for one step of a chain-mode council.

    The first step sees only the user task. Each subsequent step sees the
    original task plus all prior steps' outputs, and is asked to refine.
    """
    sections: list[str] = []
    if step_index == 0:
        sections.append(
            "You are the first model in a chain of refinement. "
            "Other models will see your output and improve on it."
        )
        sections.append(f"Task:\n{bundle.task_text}")
        if bundle.goal:
            sections.append(f"Goal:\n{bundle.goal}")
        if bundle.context_excerpt:
            sections.append(f"Context:\n{bundle.context_excerpt}")
        sections.append(
            "Give your best concrete answer. Be specific. The next model "
            "will critique and improve, so don't hedge — commit to an answer."
        )
        return "\n\n".join(sections)

    sections.append(
        f"You are step {step_index + 1} of a chain of refinement. "
        "Earlier models have answered the same task. Read their outputs, "
        "keep what's right, fix what's wrong, and produce a stronger answer."
    )
    sections.append(f"Original task:\n{bundle.task_text}")
    if bundle.goal:
        sections.append(f"Goal:\n{bundle.goal}")
    if bundle.context_excerpt:
        sections.append(f"Context:\n{bundle.context_excerpt}")
    prior_blocks: list[str] = []
    for step in prior_steps:
        provider = getattr(step, "model_provider", "unknown")
        text = getattr(step, "output_text", "").strip() or "(no output)"
        prior_blocks.append(f"[Step {step.step_index + 1}] from {provider}:\n{text}")
    sections.append("Prior chain outputs:\n" + "\n\n".join(prior_blocks))
    if is_final:
        sections.append(
            "You are the FINAL step. Produce the converged answer. Do NOT "
            "summarize or critique the prior steps — produce the cleanest "
            "best answer that incorporates what they got right."
        )
    else:
        sections.append(
            "Produce the next refinement. Be concrete and decisive. "
            "Subsequent models may iterate further."
        )
    return "\n\n".join(sections)


def render_primary_council_prompt(
    bundle: PromptBundle,
    members: list[CouncilMemberResult],
) -> str:
    member_sections = []
    for index, member in enumerate(members, start=1):
        member_sections.append(
            "\n".join(
                [
                    f"[Member {index}] provider={member.provider} model={member.model or 'unknown'}",
                    member.output_text.strip() or "(no output)",
                ]
            )
        )
    sections = [
        "You are the primary council synthesizer for a SPECIFIC user. Your job",
        "is to pick the answer that best fits THIS user — not the world. "
        "Members generate broad; you condense through the user's taste.",
    ]
    # User profile — chairman reads `core.md` FIRST (one paragraph, the
    # distillation of the lens hierarchy: lens.md tensions, topics.json
    # basins, vocabulary.md anchors) and falls through to the full
    # `lens.md` only when core is absent. This keeps each council
    # cheap on a populated install (just one paragraph in context) while
    # cold-start installs still get the full lens.
    try:
        from .state_paths import core_path
        from .me_builder import load_me

        core = ""
        cpath = core_path()
        if cpath.exists():
            try:
                core = cpath.read_text(encoding="utf-8").strip()
            except OSError:
                core = ""
        if core:
            sections.append(
                "User profile (from ~/.trinity/core.md — distilled paragraph "
                "subsuming the lens hierarchy: lens.md tensions, topics.json "
                "basins, vocabulary.md anchors).\n"
                "Use this to score 'which answer fits THIS user'. Do not echo "
                "it back; use it as latent context.\n\n"
                f"{core}"
            )
        else:
            me_doc = load_me()
            if me_doc:
                sections.append(
                    "User profile (from ~/.trinity/memories/lens.md — paired "
                    "tensions extracted from prior transcripts; core.md not "
                    "yet distilled).\n"
                    "Use this to score 'which answer fits THIS user'. Do not "
                    "echo it back; use it as latent context.\n\n"
                    f"{me_doc}"
                )
    except Exception:
        pass
    # Horizon hint (#139): classify the query and tell chairman which
    # lens-card resolution to weight. lens.md emits `[tactical]` /
    # `[strategic]` / `[philosophical]` tags on abstract lenses; without
    # a hint, chairman has no signal about which to prioritize for THIS
    # query. The hint is one line — cheap, transparent, easy to ignore
    # if the lens.md isn't horizon-tagged yet (pre-#139 lens still works,
    # everything defaults to tactical).
    try:
        from .task_types import guess_horizon

        horizon = guess_horizon(bundle.task_text)
        if horizon != "tactical":
            sections.append(
                f"Query horizon: {horizon}. When the user profile contains "
                f"lens cards tagged `[{horizon}]`, weight those heavier than "
                f"local-shape (tactical) lenses — they encode the user's "
                f"trajectory-level preferences which is what this query "
                f"reads as. Tactical lenses still apply for response shape."
            )
    except Exception:
        pass
    sections.append(f"Original task:\n{bundle.task_text}")
    if bundle.goal:
        sections.append(f"Goal:\n{bundle.goal}")
    if bundle.context_excerpt:
        sections.append(f"Context:\n{bundle.context_excerpt}")
    if bundle.comparison_instructions:
        sections.append(f"Comparison instructions:\n{bundle.comparison_instructions}")
    sections.append("Council member outputs:\n" + "\n\n".join(member_sections))
    sections.append(
        "Treat this like a live competition between named models. Use provider names directly, not response letters.\n\n"
        "Your job is to help the user decide quickly, not to write a long essay.\n\n"
        "Return TWO parts in this exact order:\n\n"
        "PART 1 — concise decision memo in markdown. Stay under 120 words total.\n"
        "Prefer short bullets. Skip weak sections rather than padding.\n\n"
        "Use these sections:\n\n"
        "## Winner\n"
        "- Choose exactly one best response for this task.\n"
        "- Name the winning provider directly, like: Gemini.\n"
        "- Add one short reason.\n\n"
        "## Why They Win\n"
        "- One short bullet per provider.\n"
        "- Focus on what that model actually contributes.\n"
        "- If a response is unusable, say so briefly.\n\n"
        "## Key Tradeoffs\n"
        "- 2 bullets max.\n"
        "- Name the real decision criteria for this task.\n\n"
        "## Recommendation\n"
        "- 2 bullets max.\n"
        "- Use the format: If you value X → choose Provider.\n"
        "- Be specific about what matters here.\n\n"
        "Do not restate the full task. Do not summarize every paragraph. Be decisive and sharp.\n\n"
        "PART 2 — a fenced code block containing strict JSON, on its own line, exactly like:\n\n"
        "```routing-json\n"
        "{\n"
        '  "winner": "<provider_name>",\n'
        '  "runner_up": "<provider_name_or_null>",\n'
        '  "confidence": "high|medium|low",\n'
        '  "task_type": "<short_snake_case>",\n'
        '  "task_domain": "<short_snake_case>",\n'
        '  "user_likely_values": ["<value_1>", "<value_2>"],\n'
        '  "provider_scores": {\n'
        '    "<provider>": {"overall": 0, "planning": 0, "execution": 0, "evaluation": 0, "specificity": 0, "user_fit": 0, "risk": 0, "conciseness": 0}\n'
        "  },\n"
        '  "major_failure_mode": "<short sentence or null>",\n'
        '  "routing_lesson": "For <task_type>, prefer <provider> because <observed reason>.",\n'
        '  "eval_seed": "A future answer should pass: <one concrete check>",\n'
        '  "agreed_claims": ["<claim all responses agree on>", "..."],\n'
        '  "disagreed_claims": [\n'
        '    {"claim": "<the disputed claim>",\n'
        '     "providers_for": ["<provider>"],\n'
        '     "providers_against": ["<provider>"],\n'
        '     "why_matters": "<one short sentence on why this disagreement matters>"}\n'
        '  ]\n'
        "}\n"
        "```\n\n"
        "Rules for the JSON:\n"
        "- ALL provider identifiers in structured fields (winner, runner_up, providers_for, providers_against, provider_scores keys) MUST be lowercase. Use 'codex', not 'Codex'. Use 'claude', not 'Claude'. Capitalised names are ONLY allowed in the human-readable PART 1 markdown.\n"
        "- Provider scores are integers 0..10. 'overall' is required for every provider you scored.\n"
        "- task_type and task_domain stay short and lowercase, e.g. 'code_refactor', 'web_research'.\n"
        "- routing_lesson is one short sentence in the form: For <task_type>, prefer <provider> because <observed reason>.\n"
        "- eval_seed is one short sentence describing a check a future answer should satisfy.\n"
        "- agreed_claims: short factual statements ALL responses make. 3-7 items. Empty list if none.\n"
        "- disagreed_claims: each entry names ONE specific disagreement, with which providers landed on which side, and one sentence on why it matters. 0-5 items.\n"
        "- Output ONLY this JSON inside the routing-json fence. No commentary inside the fence.\n"
        "- The JSON block is required. If a field is unknown, use null. Never omit a required field."
    )
    return "\n\n".join(sections)


def create_council_outcome(
    *,
    bundle: PromptBundle,
    primary_provider: str,
    member_results: list[CouncilMemberResult],
    primary_model: str | None = None,
    primary_session_id: str | None = None,
    agreement_score: float | None = None,
    winner_provider: str | None = None,
    winner_model: str | None = None,
    needs_followup: bool | None = None,
    differences: list[str] | None = None,
    synthesis_output: str | None = None,
    synthesis_prompt: str | None = None,
    routing_label: CouncilRoutingLabel | None = None,
    mode: str = "parallel",
    chain_steps: list[CouncilChainStep] | None = None,
    metadata: dict | None = None,
) -> CouncilOutcome:
    if synthesis_prompt is None:
        synthesis_prompt = render_primary_council_prompt(bundle, member_results)
    council_run_id = stable_id(
        "council",
        bundle.bundle_id,
        primary_provider,
        primary_model or "",
        now_iso(),
    )
    # Embed the task_text directly on the outcome metadata so the post-hoc
    # council review page (loaded by ?council_id=... only) has the prompt
    # without needing a separate fetch of the bundle JSON. Truncate to 5000
    # chars to keep the outcome JSON bounded — the original is always still
    # available in the bundle for full-text retrieval.
    final_metadata = dict(metadata or {})
    if "task_text" not in final_metadata and bundle.task_text:
        text = bundle.task_text
        final_metadata["task_text"] = text if len(text) <= 5000 else text[:5000] + "\n[…truncated; full text in bundle]"

    return CouncilOutcome(
        council_run_id=council_run_id,
        bundle_id=bundle.bundle_id,
        task_cluster_id=bundle.task_cluster_id,
        primary_provider=primary_provider,
        primary_model=primary_model,
        primary_session_id=primary_session_id,
        agreement_score=agreement_score,
        winner_provider=winner_provider,
        winner_model=winner_model,
        needs_followup=needs_followup,
        differences=differences or [],
        member_results=member_results,
        synthesis_prompt=synthesis_prompt,
        synthesis_output=synthesis_output,
        routing_label=routing_label,
        mode=mode,
        chain_steps=chain_steps or [],
        created_at=now_iso(),
        metadata=final_metadata,
    )


def append_council_outcome(outcome: CouncilOutcome) -> None:
    with council_runs_path().open("a") as handle:
        handle.write(json.dumps(outcome.to_dict()) + "\n")


def save_council_outcome(outcome: CouncilOutcome) -> Path:
    from .markdown_utils import render_markdown
    from .utils import atomic_write_text

    # Contract: council_outcome.schema.json declares synthesis_output +
    # routing_label as required. The dataclass allows both to be None
    # (it's the same shape during async council execution before
    # chairman synthesis lands), so the strict save-time contract has
    # to live here — every callsite in council_runner.py passes
    # populated values, but a future code path that accidentally writes
    # a partial outcome would silently break downstream readers that
    # validate against schema. Fail fast at the boundary.
    if outcome.synthesis_output is None:
        raise ValueError(
            f"save_council_outcome refused: synthesis_output is None "
            f"for council {outcome.council_run_id!r}. The schema "
            f"declares this field required. Live progress files belong "
            f"in council_status_dir(); council_outcomes/ is for completed "
            f"councils only."
        )
    if outcome.routing_label is None:
        raise ValueError(
            f"save_council_outcome refused: routing_label is None for "
            f"council {outcome.council_run_id!r}. The schema declares "
            f"this field required. Chairman synthesis emits the "
            f"routing_label inline; outcomes without it indicate a "
            f"parse failure that should be surfaced loudly, not "
            f"silently written."
        )

    payload = outcome.to_dict()
    path = council_outcomes_dir() / f"{outcome.council_run_id}.json"
    atomic_write_text(path, json.dumps(payload, indent=2))

    # JSONP wrapper for the unified review page (file:// can't fetch JSON
    # cross-origin; a script tag works). Pre-render markdown so the page
    # doesn't ship a JS markdown renderer.
    jsonp_payload = dict(payload)
    rendered_members = []
    for member in payload.get("member_results", []):
        m = dict(member)
        text = m.get("output_text") or ""
        m["output_html"] = render_markdown(text) if text else ""
        rendered_members.append(m)
    jsonp_payload["member_results"] = rendered_members
    synthesis_text = payload.get("synthesis_output") or ""
    synthesis_clean = re.sub(
        r"```routing-json\s*\n.*?\n```\s*$", "", synthesis_text, flags=re.DOTALL,
    ).rstrip()
    jsonp_payload["synthesis_output_clean"] = synthesis_clean
    jsonp_payload["synthesis_html"] = render_markdown(synthesis_clean) if synthesis_clean else ""

    jsonp_path = council_outcomes_dir() / f"{outcome.council_run_id}.js"
    atomic_write_text(
        jsonp_path,
        "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
        f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(outcome.council_run_id)}] = "
        f"{json.dumps(jsonp_payload)};\n",
    )
    append_council_outcome(outcome)
    update_thread_manifest(outcome)
    return path


def _read_thread_manifest(path: Path) -> dict:
    """Parse a JSONP thread manifest. The file has two assignments — the
    first is the `... || {}` namespace bootstrap (which is not valid JSON),
    the second is the actual `[id] = {...}` payload. Pull the JSON object
    out of the assignment line, ignoring the bootstrap."""
    text = path.read_text()
    match = re.search(r"\]\s*=\s*(\{.*\})\s*;", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"thread manifest missing payload assignment: {path}")
    return json.loads(match.group(1))


def _write_thread_manifest(chain_root_id: str, segments: list[dict]) -> Path:
    from .utils import atomic_write_text
    manifest_path = council_outcomes_dir() / f"_thread_{chain_root_id}.js"
    segments.sort(key=lambda s: (s.get("round_number") or 1, s.get("started_at") or ""))
    manifest = {"chain_root_id": chain_root_id, "segments": segments}
    atomic_write_text(
        manifest_path,
        "window.__TRINITY_COUNCIL_THREAD__ = window.__TRINITY_COUNCIL_THREAD__ || {};\n"
        f"window.__TRINITY_COUNCIL_THREAD__[{json.dumps(chain_root_id)}] = "
        f"{json.dumps(manifest)};\n",
    )
    return manifest_path


def _read_thread_segments(chain_root_id: str) -> list[dict]:
    manifest_path = council_outcomes_dir() / f"_thread_{chain_root_id}.js"
    if not manifest_path.exists():
        return []
    try:
        payload = _read_thread_manifest(manifest_path)
        return list(payload.get("segments") or [])
    except Exception:
        return []


def update_thread_manifest(outcome: CouncilOutcome) -> Path:
    """Write/update the JSONP thread manifest for this outcome's chain.

    Each chain (rooted at chain_root_id) gets one
    `_thread_<chain_root_id>.js` file listing its segments in order. The
    live council page reads this when a `?thread_id=` URL is opened so it
    can stack every round of the same conversation on one scrollable page.

    Dedup priority: bundle_id (stable from round-start) > council_id (only
    allocated at finalize time). Lets a pending entry get replaced by the
    final completed entry when the round saves.
    """
    # bundle_id is the canonical chain root: stable from launch time,
    # whereas council_run_id is only allocated when create_council_outcome
    # runs. Using bundle_id lets us register a pending manifest entry at
    # init time (before the outcome exists) and have save_council_outcome
    # update the same file when the round finishes.
    metadata = outcome.metadata or {}
    chain_root_id = metadata.get("chain_root_id") or outcome.bundle_id
    segments = _read_thread_segments(chain_root_id)
    round_number = int(metadata.get("round_number") or 1)

    entry = {
        "council_id": outcome.council_run_id,
        "bundle_id": outcome.bundle_id,
        "round_number": round_number,
        "started_at": metadata.get("started_at") or outcome.created_at,
        "parent_council_id": metadata.get("parent_council_id"),
    }
    # Dedup: only replace the prior entry for THIS round, not every entry
    # sharing this bundle_id. Consensus rounds share bundle_id (deterministic
    # from task_cluster + task_text), so the old dedup-by-bundle_id collapsed
    # every round into one segment. New rule:
    #   - same council_id (finalizing the same finalized round) → replace
    #   - same (bundle_id, round_number) AND pending entry (no council_id) →
    #     replace (pending → finalized handoff)
    # Each round_number gets its own segment.
    segments = [
        s for s in segments
        if not (
            (s.get("council_id") is not None and s.get("council_id") == outcome.council_run_id)
            or (
                s.get("council_id") is None
                and s.get("bundle_id") == outcome.bundle_id
                and int(s.get("round_number") or 1) == round_number
            )
        )
    ]
    segments.append(entry)
    return _write_thread_manifest(chain_root_id, segments)


def register_pending_round(
    *,
    chain_root_id: str,
    bundle_id: str,
    status_token: str,
    round_number: int,
    parent_council_id: str | None = None,
    started_at: str | None = None,
) -> Path:
    """Add a pending segment to the thread manifest before the round finishes.

    Called when a chain round starts so the thread view (which loads the
    manifest) can pick up an in-flight round even when the user navigates
    to the launchpad and clicks the thread tile mid-round.

    The segment is keyed by `bundle_id` (stable from round-start). When
    the round eventually saves via `save_council_outcome`, the matching
    pending entry is replaced with the completed entry that carries the
    real `council_run_id`.
    """
    segments = _read_thread_segments(chain_root_id)
    entry = {
        "council_id": None,
        "bundle_id": bundle_id,
        "status_token": status_token,
        "round_number": int(round_number),
        "started_at": started_at or now_iso(),
        "parent_council_id": parent_council_id,
        "running": True,
    }
    # Same dedup principle as update_thread_manifest: only collapse a prior
    # pending entry for THIS exact (bundle_id, round_number). Consensus rounds
    # share bundle_id, so blanket bundle_id removal would wipe prior rounds.
    rn = int(round_number)
    segments = [
        s for s in segments
        if not (
            s.get("bundle_id") == bundle_id
            and int(s.get("round_number") or 1) == rn
        )
    ]
    segments.append(entry)
    return _write_thread_manifest(chain_root_id, segments)


def load_council_outcome(path_or_run_id: str) -> CouncilOutcome:
    from .council_schema import normalize_provider_slug

    path = Path(path_or_run_id)
    if not path.exists():
        path = council_outcomes_dir() / f"{path_or_run_id}.json"
    if not path.exists():
        # A clean, catchable FileNotFoundError (no leaked absolute path) — callers
        # that scan a glob already catch this to skip; CLI handlers route through
        # `load_council_outcome_or_exit` so a typo'd/deleted id degrades to a
        # one-line error instead of a traceback.
        raise FileNotFoundError(
            f"No council found for id {str(path_or_run_id)!r}. "
            "Open the launchpad to see your recent councils."
        )
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # A TRUNCATED / 0-byte / non-UTF8 outcome file (a crash or `kill -9`
        # mid-save leaves `{"council_run_id": "...", "metad` with no close; a
        # `touch`ed-but-never-written file is 0 bytes) parses to a raw
        # json.JSONDecodeError. The bare parse here leaked that traceback through
        # `load_council_outcome_or_exit` (which only caught FileNotFoundError) —
        # so `trinity-local council-share <id>` / `council-iterate <id>` on a
        # crash-corrupted council printed a Python stack trace instead of a
        # one-line error. Raise the SAME clean, catchable ValueError the
        # wrong-type guard below already uses, with a TYPE-ONLY honest message
        # (no leaked {exc}/{path} — the #43102d25 raw-exception-leak class). The
        # glob-scanning callers (_scan_outcomes / refresh / lens_routing) already
        # `except Exception` and skip; the CLI helper now catches this to exit
        # cleanly.
        raise ValueError(
            f"Council {path.stem!r} is unreadable (the outcome file is "
            "corrupted or was truncated by a crash). Run a fresh council."
        ) from None
    if not isinstance(raw, dict):
        raise ValueError(f"council outcome {path.stem!r} is not a JSON object")
    # Normalize legacy "gemini" → canonical "antigravity" at the load
    # boundary across every provider-keyed field, so downstream
    # consumers (personal_routing aggregator, chairman picker,
    # launchpad rendering, audit dashboards) see one canonical slug.
    # Tick 96 covered the routing_label fields; tick 97 extends the
    # same fix to the per-outcome provider fields + each member's
    # provider. See _LEGACY_PROVIDER_ALIASES in council_schema.py.
    if "primary_provider" in raw:
        raw["primary_provider"] = normalize_provider_slug(raw["primary_provider"])
    if "winner_provider" in raw:
        raw["winner_provider"] = normalize_provider_slug(raw["winner_provider"])
    # A corrupt on-disk outcome can carry member_results as a wrong-type
    # scalar/dict (valid JSON, wrong shape). Iterating a str yields chars and
    # a dict yields keys (→ `CouncilMemberResult(** "c")` TypeError); an int
    # isn't iterable at all. Coerce a non-list member_results to [] and skip
    # any non-dict / missing-`provider` element so a single garbled council
    # degrades to "no members recorded" instead of a TypeError traceback that
    # strands the unified review / share render (and the launchpad scan, which
    # already swallows the exception but loses the whole council).
    raw_members = raw.get("member_results")
    if not isinstance(raw_members, list):
        raw_members = []
    normalized_members = []
    for member in raw_members:
        if not isinstance(member, dict):
            continue
        if "provider" in member:
            member = dict(member)
            member["provider"] = normalize_provider_slug(member["provider"])
        normalized_members.append(member)
    members = []
    for member in normalized_members:
        try:
            members.append(CouncilMemberResult(**member))
        except TypeError:
            # Unknown/missing keys on a single member (forward/backward drift
            # or a hand-edited outcome) shouldn't sink the whole render.
            continue
    raw["member_results"] = members
    routing = raw.get("routing_label")
    if isinstance(routing, dict):
        raw["routing_label"] = CouncilRoutingLabel.from_dict(routing)
    else:
        # A corrupt on-disk outcome can carry routing_label as a wrong-type
        # scalar/list (valid JSON, wrong shape). The old `elif routing is None`
        # left a non-dict, non-None value RAW on the dataclass, so the unified
        # review render then crashed on `label.winner` (AttributeError: 'str'
        # object has no attribute 'winner') — a 500 on the persistent, shareable
        # council page. Drop ANY non-dict routing_label here (the render treats
        # `routing_label is None` as "no label" and self-hides the section).
        raw.pop("routing_label", None)
    chain_steps_raw = raw.get("chain_steps")
    if isinstance(chain_steps_raw, list):
        steps = []
        for s in chain_steps_raw:
            if not isinstance(s, dict):
                # Non-dict step element (valid JSON, wrong shape) — drop it
                # rather than store a raw scalar that downstream chain readers
                # would choke on.
                continue
            try:
                steps.append(CouncilChainStep.from_dict(s))
            except TypeError:
                # A step dict missing the required step_index/model_provider
                # (a hand-edited / truncated outcome) raised TypeError out of
                # the loader, stranding the unified review render. Skip the
                # malformed step so the rest of the chain still loads.
                continue
        raw["chain_steps"] = steps
    # Rating-surface retirement 2026-05-22 (per "lens-governed council
    # selections" directive): the legacy `metadata.user_verdict` block
    # was sunset alongside the rest of the rating UX. Strip on read so
    # existing on-disk councils naturally lose the field on next save —
    # no separate migration script needed; load+save IS the migration.
    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        if "user_verdict" in metadata:
            metadata = {k: v for k, v in metadata.items() if k != "user_verdict"}
            raw["metadata"] = metadata
    elif "metadata" in raw:
        # A corrupt on-disk outcome can carry metadata as a wrong-type
        # scalar/list (valid JSON, wrong shape). A truthy non-dict (str/int/
        # list) bypasses the render's `(outcome.metadata or {}).get(...)`
        # idiom and raised AttributeError ('str'/'int'/'list' has no attribute
        # 'get') — a 500 on the unified review page. Coerce any non-dict
        # metadata to {} at the load boundary so every consumer (render, share
        # card, refresh, personal_routing) sees the dict contract.
        raw["metadata"] = {}
    # Tolerate forward/backward field drift on load
    known = {f for f in CouncilOutcome.__dataclass_fields__}
    raw = {k: v for k, v in raw.items() if k in known}
    return CouncilOutcome(**raw)


def load_council_outcome_or_exit(council_id: str) -> CouncilOutcome:
    """CLI helper: load a council outcome, or exit cleanly (a one-line error, NOT a
    traceback) when the id is unknown. A user passing a typo'd / deleted council id
    to a documented command (review-link, council-share, council-iterate) shouldn't
    get a Python stack trace — the same first-run-robustness class as the eval-audit
    cold-home fix. Library callers that scan a glob keep using `load_council_outcome`
    directly so they can catch + skip."""
    try:
        return load_council_outcome(council_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")
    except ValueError as exc:
        # A TRUNCATED / 0-byte / wrong-type outcome file (crash mid-save) raises a
        # clean ValueError out of load_council_outcome — exit on a one-line error,
        # NOT the raw json.JSONDecodeError traceback that used to escape here (the
        # founder symptom: `council-share <id>` on a crash-corrupted council
        # printed a Python stack trace). The corruption message is TYPE-ONLY
        # (path-free); the wrong-type branch carries a path in its text, but it's
        # the dev-edited case and still exits one-line rather than tracebacking.
        raise SystemExit(f"error: {exc}")


def _normalize_section_header(line: str) -> str:
    normalized = line.strip()
    normalized = re.sub(r"^#+\s*", "", normalized)
    normalized = re.sub(r"^\*+\s*", "", normalized)
    normalized = re.sub(r"^\d+[\.\)]\s*", "", normalized)
    normalized = re.sub(r"\*+$", "", normalized)
    normalized = normalized.rstrip(":").strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _extract_named_sections(
    text: str,
    section_aliases: list[tuple[str, tuple[str, ...]]],
) -> dict[str, str]:
    alias_lookup: dict[str, str] = {}
    for key, aliases in section_aliases:
        for alias in aliases:
            alias_lookup[_normalize_section_header(alias)] = key

    matches: list[tuple[str, int]] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        normalized = _normalize_section_header(line)
        key = alias_lookup.get(normalized)
        if key:
            matches.append((key, idx))

    if not matches:
        return {}

    extracted: dict[str, str] = {}
    for position, (key, start_idx) in enumerate(matches):
        end_idx = matches[position + 1][1] if position + 1 < len(matches) else len(lines)
        body = "\n".join(lines[start_idx + 1:end_idx]).strip()
        if body and key not in extracted:
            extracted[key] = body
    return extracted


def parse_synthesis_sections(text: str) -> dict[str, str]:
    return _extract_named_sections(
        text,
        [
            ("agreement", ("agreement", "what reviewers found", "reviewer findings")),
            ("differences", ("differences", "key differences", "key tradeoffs", "tradeoffs")),
            ("best_answer", ("best answer", "best overall answer", "strongest answer", "what each response does best")),
            ("winner", ("winner", "decision framework", "recommendation", "recommended answer")),
            ("followup", ("follow-up needed", "followup needed", "follow-up", "followup", "next step", "next steps")),
        ],
    )


_ROUTING_JSON_FENCE_RE = re.compile(
    r"```\s*routing[-_ ]?json\s*\n(.*?)\n\s*```",
    re.IGNORECASE | re.DOTALL,
)
def _bare_json_objects_with_winner(text: str) -> list[str]:
    """Find balanced top-level ``{...}`` objects that mention ``"winner"``.

    The old regex (``\\{[\\s\\S]*?"winner"[\\s\\S]*?\\}``) is non-greedy and stops
    at the FIRST ``}`` after "winner" — so any unfenced routing JSON with nested
    objects (provider_scores, disagreed_claims) gets truncated mid-object and
    fails to parse. That fires in exactly the degraded unfenced case this
    fallback exists to rescue. A brace-depth scan (string/escape aware) returns
    the full balanced object instead — same fix shipped for the Gemini parser
    in v1.7.9, now back-ported. Returns candidates in document order."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj = text[i : j + 1]
                    if '"winner"' in obj:
                        out.append(obj)
                    break
            j += 1
        # Resume scanning AFTER this object (or at the unterminated tail).
        i = j + 1 if j < n else n
    return out


def parse_routing_label(synthesis_text: str | None) -> tuple[CouncilRoutingLabel | None, str | None]:
    """Extract the Chairman Routing JSON from a synthesis output (§8.7).

    Returns (label, error). On success error is None. On failure label is None
    and error is a short reason string suitable for storing in metadata.
    """
    if not synthesis_text:
        return None, "no_synthesis"

    candidates: list[str] = []
    for match in _ROUTING_JSON_FENCE_RE.finditer(synthesis_text):
        candidates.append(match.group(1))

    if not candidates:
        # Fallback: find balanced bare JSON objects that mention "winner"
        # (brace-depth scan — tolerates nested provider_scores/disagreed_claims).
        candidates.extend(_bare_json_objects_with_winner(synthesis_text))

    if not candidates:
        return None, "no_routing_json_block"

    last_error: str = "json_parse_failed"
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = f"json_parse_failed:{exc.msg}"
            continue
        if not isinstance(data, dict):
            last_error = "json_not_object"
            continue
        if not data.get("winner"):
            last_error = "missing_winner"
            continue
        try:
            label = CouncilRoutingLabel.from_dict(_normalize_routing_dict(data))
        except (TypeError, ValueError) as exc:
            last_error = f"schema_error:{exc.__class__.__name__}"
            continue
        return label, None
    return None, last_error


def _normalize_routing_dict(data: dict) -> dict:
    """Coerce field types to expected shapes; drop garbage."""
    out: dict = {}
    for key in (
        "winner",
        "runner_up",
        "confidence",
        "task_type",
        "task_domain",
        "routing_lesson",
        "eval_seed",
        "major_failure_mode",
    ):
        value = data.get(key)
        if isinstance(value, str):
            out[key] = value.strip()
        elif value is None:
            out[key] = None
    values = data.get("user_likely_values")
    if isinstance(values, list):
        out["user_likely_values"] = [str(v) for v in values if v]
    scores = data.get("provider_scores")
    if isinstance(scores, dict):
        clean_scores: dict[str, dict[str, float]] = {}
        for provider, sub in scores.items():
            if not isinstance(sub, dict):
                continue
            cleaned = {}
            for metric, raw in sub.items():
                try:
                    cleaned[metric] = float(raw)
                except (TypeError, ValueError):
                    continue
            if cleaned:
                clean_scores[str(provider)] = cleaned
        if clean_scores:
            out["provider_scores"] = clean_scores
    # `best_stage_models`, `should_be_hard_case`, and `hard_case_reason` were
    # demoted in iter-3 — they had zero downstream consumers. Old outcome
    # JSONs that still carry them load via CouncilRoutingLabel.from_dict's
    # __dataclass_fields__ filter; the normalizer just stops emitting them.
    agreed = data.get("agreed_claims")
    if isinstance(agreed, list):
        cleaned = [str(c).strip() for c in agreed if isinstance(c, str) and c.strip()]
        if cleaned:
            out["agreed_claims"] = cleaned
    disagreed = data.get("disagreed_claims")
    if isinstance(disagreed, list):
        cleaned_disagreed: list[dict[str, object]] = []
        for entry in disagreed:
            if not isinstance(entry, dict):
                continue
            claim = entry.get("claim")
            if not isinstance(claim, str) or not claim.strip():
                continue
            sub: dict[str, object] = {"claim": claim.strip()}
            for key in ("providers_for", "providers_against"):
                items = entry.get(key)
                if isinstance(items, list):
                    sub[key] = [str(p).strip() for p in items if isinstance(p, str) and p.strip()]
                else:
                    sub[key] = []
            why = entry.get("why_matters")
            if isinstance(why, str) and why.strip():
                sub["why_matters"] = why.strip()
            cleaned_disagreed.append(sub)
        if cleaned_disagreed:
            out["disagreed_claims"] = cleaned_disagreed
    return out


