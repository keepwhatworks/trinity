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
    prune_store_to_cap,
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
    prune_store_to_cap(prompt_bundles_dir())  # opt-in retention (#7); no-op by default
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


# The optional sections render_member_prompt may add, in render order. The
# framing label is built from THIS tuple and the same conditionals the renderer
# uses, so a section added to one without the other is caught by
# tests/test_council_records_framing.py rather than silently mislabelling a run.
_FRAMING_SECTIONS = ("goal", "context_excerpt", "comparison_instructions")


def member_prompt_framing(bundle: PromptBundle) -> str:
    """Which optional sections this bundle's member prompt actually carries.

    Registered in the compression-turn plan (§2, "framing in the ledger key")
    and unbuilt until now: RECORDING framing is instrumentation and safe;
    SELECTING framing from outcomes is a policy and stays behind the airgap.

    Measured 2026-09-03 across 10,375 bundles on disk: three distinct framings
    at 74.3% / 20.6% / 5.0%. Unlike effort -- whose rotation was never switched
    on, so no model ever had a second level -- framing already varies. The
    contrast was there the whole time and simply never recorded, so a council
    could never be joined to the shape of the prompt its members answered.

    Returns a stable "+"-joined label, or "task_only" when the bundle carries
    nothing but the task.
    """
    present = [name for name in _FRAMING_SECTIONS if getattr(bundle, name, None)]
    return "+".join(present) if present else "task_only"


def render_member_prompt(bundle: PromptBundle) -> str:
    """Build the council-member prompt (shared across providers) — now
    lens-conditioned at GENERATION.

    The 2026-05-16 "digital-twin" DESIGN HOLE that used to live here was
    closed 2026-07-05 by council_7e031d6e431bcceb (unanimous): the lens's
    own axes (REFRAME/REDIRECT/SHARPENING/COMPRESSION) are generation
    acts, and the causal ablation showed the selection-side read moves
    only 1/12 chairman picks — a generation signal was wired into a
    selection slot. So the tensions now condition the MEMBER prompt:
    every answer arrives taste-shaped before synthesis, and the chairman's
    two-stage rule still guarantees correctness outranks taste.

    Read-side only: this reads lens.md; it never writes anything —
    no council→lens edge (the founder lock).

    MEASURED NULL → default OFF (2026-07-05): the pre-registered generation
    ablation came back at chance — combined n=30 paired contests, the
    lens-conditioned answer aligned better on the validated lens direction
    only 16/30 (one-sided p=0.43; the first batch's 7/10 was small-n noise
    the n=30 extension corrected). Per the council's own kill condition the
    block is dormant: TRINITY_LENS_MEMBERS=1 opts in (the experiment lever);
    default renders no lens block. The selection-side two-stage rule stays —
    its 1/12 tie-break effect is real (0/6 noise floor) and correct-by-design.
    """
    sections = [
        "You are one member of a multi-model council.",
        f"Task:\n{bundle.task_text}",
    ]
    lens_block = _member_lens_constraints(bundle.task_text)
    if lens_block:
        sections.append(lens_block)
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


def _member_lens_constraints(query: str = "") -> str:
    """The user's named tensions as GENERATION constraints (compact, ≤6,
    never the 25KB lens.md — same budget as the chairman's block).

    Framing per the council: shape the answer's approach and form toward
    the user's leans; never sacrifice correctness or completeness for
    them — the chairman's quality gate would (rightly) punish that.
    pole_a is canonically the user's optimized-for pole (pair-mining
    schema: "the optimized-for axis"; lens.md renders privileged > sacrificed).
    """
    import os

    if os.environ.get("TRINITY_LENS_MEMBERS", "0").strip().lower() not in ("1", "true"):
        return ""  # dormant by measurement — see render_member_prompt docstring
    try:
        from .me.pipeline import _TENSION_HEADING
        from .state_paths import lens_path

        lp = lens_path()
        lens_md = lp.read_text(encoding="utf-8") if lp.exists() else ""
        from .lens_routing import scope_for_query

        tensions = (scope_for_query(query, 6)
                    or _TENSION_HEADING.findall(lens_md)[:6])
        if not tensions:
            return ""
        lines = "\n".join(
            f"  {i}. prefer {a} over {b}" for i, (a, b) in enumerate(tensions, 1)
        )
        return (
            "Shape your answer for THIS user (tensions mined from their real "
            "decisions — the FIRST pole is the one they consistently choose):\n"
            f"{lines}\n"
            "Honor these in form and approach. Never sacrifice correctness or "
            "completeness to satisfy them."
        )
    except Exception:
        return ""


COMBINE_ENV_VAR = "TRINITY_COMBINE_SURVIVORS"


def combine_enabled() -> bool:
    """Dormant by default. See _combine_survivors_block for the pre-registered
    falsifier this ships behind."""
    import os

    return os.environ.get(COMBINE_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def _combine_json_fields() -> str:
    """The combine output as STRUCTURED fields, not just PART 1 prose.

    Prose in the decision memo is unreadable to an agent, the MCP surface, or any
    eval — if the merged answer is the deliverable, it has to be in the JSON. Four
    layers must agree or the field silently vanishes: this template, the parse
    normalizer's whitelist, the CouncilRoutingLabel dataclass fields, and the
    outcome JSON schema. `resolution` shipped with only the first and lost the
    other two, and no test caught it because the test asserted the PROMPT.
    """
    return (
        '  "combined_answer": "<the merged answer: the winner as the spine with the '
        'surviving claims grafted in. Markdown is fine. Omit entirely if the winner '
        'already subsumes every survivor>",\n'
        '  "grafts": [\n'
        '    {"claim": "<the specific claim you grafted in>",\n'
        '     "from": "<lowercase provider it came from>",\n'
        '     "basis": "evidence|lens",\n'
        '     "tension": "<which named tension decided it — ONLY when basis is '
        'lens>"}\n'
        '  ]\n'
    )


def _combine_survivors_block() -> str:
    """Ask the chairman to MERGE the claims that survived cross-provider
    prosecution, instead of only crowning one member's answer.

    WHY OVER SURVIVORS AND NOT OVER THE LENS (founder call 2026-07-24). Combining
    "based on the lens" is the thing measured NULL twice here: lens-conditioned
    generation aligned better on the validated lens direction only 16/30
    (one-sided p=0.43) and chairman-transmission came back at chance. Those two
    nulls are why the product is selection-only ("selection, not a reproduced
    answer"). Merging what SURVIVED the prosecution round is a different claim —
    synthesis over evidence, not reproduction of a voice — and it has never been
    measured either way, so it is a live question rather than a dead one.

    DORMANT until it earns its place (TRINITY_COMBINE_SURVIVORS=1 opts in).
    PRE-REGISTERED FALSIFIER, in two stages so no user attention is spent on a
    feature that might be a no-op:
      1. ADDRESSABILITY (free, no judge, no user time): on real councils, how
         often does the combined answer differ MATERIALLY from the winner's own
         answer? If under ~20% of contests, the merge is cosmetic — KILL without
         ever running a preference test. This is the echo-council census shape
         (1.9% addressable → killed).
      2. PREFERENCE (only if 1 clears): blind A/B, combined vs the selected
         winner, unlabelled, ~15 decided pairs, the FOUNDER picks. There is no
         valid model judge for this — the judged tier measured 57% against its
         own 70% floor — so the user is the instrument. Bar: combined wins and
         the CI excludes 50%. Adaptive stopping.
    """
    if not combine_enabled():
        return ""
    return (
        "## Combined\n"
        "- Only when the Contested section left at least one SURVIVING claim that "
        "the winning answer does not already make.\n"
        "- Merge those survivors into the winner's answer: keep the winner as the "
        "spine, graft in the specific claims that withstood cross-examination.\n"
        "- Cite which member each grafted claim came from.\n"
        "- MERGE RULE — two stages, same order as the WINNER RULE:\n"
        "  Stage 1 (reason first): decide what to keep on the EVIDENCE — which "
        "claim is better supported, more complete, more correct for this task. "
        "Reasoning decides the merge whenever it can.\n"
        "  Stage 2 (taste only breaks ties): when two surviving claims are "
        "evidence-equivalent and reasoning genuinely cannot separate them, choose "
        "the one that fits the user's named tensions above, and say which tension "
        "decided it. Taste never overrides better evidence.\n"
        "- Do NOT rewrite in anyone's voice and do NOT smooth the answers into a "
        "consensus mush. Only carry over claims that SURVIVED on the evidence.\n"
        "- Skip this section entirely when the winner already subsumes every "
        "survivor — an empty merge is worse than no merge.\n\n"
    )


def render_primary_council_prompt(
    bundle: PromptBundle,
    members: list[CouncilMemberResult],
    extra_context: str = "",
) -> str:
    # extra_context (default ""): an optional block inserted after the member
    # outputs and before the decision instructions. Slice (c) uses it to feed the
    # chairman the CROSS-EXAMINATION verdicts on the disputed claims during
    # re-synthesis, so a claim that was broken does not survive. Empty for every
    # normal council — no behaviour change for existing callers.
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
        "You are a PROSECUTOR, not a summarizer. The members answered "
        "independently and never saw each other's work. Where they disagree, "
        "do not just report the split — force each disputed claim against the "
        "OTHER members' evidence and name which side SURVIVES. Keep what "
        "withstands scrutiny; kill what does not. A disagreement you genuinely "
        "cannot resolve on the evidence is itself a verdict — say 'unresolved' "
        "and why, rather than papering over it.",
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
        # Named tensions + the two-stage winner rule
    # (council_33f3f375c82f03b1, 2026-07-05). The ablation proved the
    # latent core.md read alone moves only 1/12 picks; all three council
    # members agreed on explicit tension-scoring inside a quality gate —
    # taste decides only among quality-equivalent answers, evidence-
    # weighted, with citations — and rejected blended scoring (taste
    # leaks into quality) and category-counting (drops evidence
    # weights). Compact block (~600 chars), never the 25KB lens.md.
    try:
        from .me.pipeline import _TENSION_HEADING  # canonical predicate
        from .state_paths import lens_path

        lens_md = lens_path().read_text(encoding="utf-8") if lens_path().exists() else ""
        # Scoped read first, global slice as the fallback. scope_for_query
        # returns [] for every degradation (flag off by default), so this is
        # exactly today's behaviour until TRINITY_DAG_SCOPED_LENS is set.
        from .lens_routing import scope_for_query

        tensions = (scope_for_query(bundle.task_text, 6)
                    or _TENSION_HEADING.findall(lens_md)[:6])
        if tensions:
            tension_lines = "\n".join(
                f"  {i}. {a} ↔ {b}" for i, (a, b) in enumerate(tensions, 1)
            )
            sections.append(
                "The user's named taste tensions (mined from their real "
                "decisions; each ranks one pole above the other for THIS "
                "user):\n"
                f"{tension_lines}\n\n"
                "WINNER RULE — two stages, in order:\n"
                "Stage 1 (quality gate): if one answer is CLEARLY stronger "
                "on correctness and completeness, it wins — taste never "
                "overrides a clearly better answer.\n"
                "Stage 2 (taste decides close calls): when two or more "
                "answers are quality-equivalent, score EACH of them "
                "against EACH numbered tension above with a one-line "
                "citation from the answer's own text, and pick the winner "
                "by lens fit. When Stage 2 decided the winner, add a "
                "'## Lens fit' section to PART 1 showing those citations."
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
    if extra_context.strip():
        sections.append(extra_context.strip())
    sections.append(
        "Treat this like a live competition between named models. Use provider names directly, not response letters.\n\n"
        "Your job is to help the user decide quickly, not to write a long essay.\n\n"
        "Return TWO parts in this exact order:\n\n"
        "PART 1 — concise decision memo in markdown. Stay under 160 words total.\n"
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
        "## Contested\n"
        "- Only if members genuinely disagreed. Skip entirely if they converged.\n"
        "- One bullet per real disagreement: which side SURVIVES when weighed "
        "against the other members' evidence, and the ground for it.\n"
        "- If undecidable on the evidence, say 'unresolved' and why.\n"
        "- This is the priority section. Never drop it to save words; drop Key "
        "Tradeoffs first.\n\n"
        + _combine_survivors_block() +
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
        '     "resolution": "<which side survives the other members\' evidence and the ground for it, or \'unresolved\' if undecidable>",\n'
        '     "why_matters": "<one short sentence on why this disagreement matters>"}\n'
        '  ],\n'
        '  "facets": [\n'
        '    {"name": "<name the dimension that ACTUALLY separated these answers on '
        'this task, in your own words — not a generic axis>",\n'
        '     "winner": "<provider that won that dimension>",\n'
        '     "basis": "<one line of evidence>"}\n'
        '  ]' + (",\n" + _combine_json_fields() if combine_enabled() else "\n") +
        "}\n"
        "```\n\n"
        "Rules for the JSON:\n"
        "- ALL provider identifiers in structured fields (winner, runner_up, providers_for, providers_against, provider_scores keys) MUST be lowercase. Use 'codex', not 'Codex'. Use 'claude', not 'Claude'. Capitalised names are ONLY allowed in the human-readable PART 1 markdown.\n"
        "- Provider scores are integers 0..10. 'overall' is required for every provider you scored.\n"
        "- task_type and task_domain stay short and lowercase, e.g. 'code_refactor', 'web_research'.\n"
        "- routing_lesson is one short sentence in the form: For <task_type>, prefer <provider> because <observed reason>.\n"
        "- eval_seed is one short sentence describing a check a future answer should satisfy.\n"
        "- agreed_claims: short factual statements ALL responses make. 3-7 items. Empty list if none.\n"
        "- facets: 1-3 entries. Name the dimensions that actually DISCRIMINATED between these answers for THIS task (e.g. 'invalidation semantics', 'cost realism', 'migration risk') and who won each. Do NOT reuse the generic score axes; if a dimension separated nobody, leave it out. Empty list when the answers did not differ along any nameable dimension.\n"
        "- disagreed_claims: each entry names ONE specific disagreement, with which providers landed on which side, the RESOLUTION (which side survives the other members' evidence, or 'unresolved'), and one sentence on why it matters. 0-5 items.\n"
        + ("- combined_answer: the SAME merge you wrote in '## Combined', as one string. It must be usable on its own by someone who never reads the memo. Omit the field when there was nothing to merge.\n"
           "- grafts: one entry per claim you grafted in, naming its source provider and whether EVIDENCE or the LENS decided to keep it. Set basis='lens' ONLY for a genuine evidence-tie broken by taste, and then name the tension. An empty list is correct when nothing was grafted.\n"
           if combine_enabled() else "")
        + "- Output ONLY this JSON inside the routing-json fence. No commentary inside the fence.\n"
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
        # QUARANTINE BEFORE REFUSING (res_080). Refusing is right — an outcome
        # without a routing_label must never reach the ledger. But the raw
        # chairman text was DISCARDED with it, and a `lens --deep` run on
        # 2026-08-24 lost 49 of 433 syntheses (11.3%) this way: three hours of
        # quota spent, zero examples kept, so the failure could not be
        # diagnosed and a rerun would void identically. The archive costs one
        # file and makes the next occurrence explicable.
        try:
            from .state_paths import trinity_home

            qdir = trinity_home() / "council_quarantine"
            qdir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                qdir / f"{outcome.council_run_id}.json",
                json.dumps({
                    "council_run_id": outcome.council_run_id,
                    "reason": "routing_label is None — chairman emitted no parseable routing JSON",
                    "quarantined_at": now_iso(),
                    "synthesis_output": outcome.synthesis_output,
                    "primary_provider": getattr(outcome, "primary_provider", None),
                    "primary_model": getattr(outcome, "primary_model", None),
                }, indent=2))
        except Exception:
            pass  # quarantine is diagnostic; it must never mask the refusal below
        raise ValueError(
            f"save_council_outcome refused: routing_label is None for "
            f"council {outcome.council_run_id!r}. The schema declares "
            f"this field required. Chairman synthesis emits the "
            f"routing_label inline; outcomes without it indicate a "
            f"parse failure that should be surfaced loudly, not "
            f"silently written. Raw synthesis quarantined under "
            f"council_quarantine/ for diagnosis."
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
        # so `trinity-local council-share <id>` on a
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
    to a documented command (review-link, council-share) shouldn't
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
            # The prosecutorial chairman's verdict on the split: which side
            # SURVIVES the other members' evidence (or "unresolved"). This
            # normalizer REBUILDS each claim from an explicit key list, so a new
            # field that isn't named here is silently dropped — which is exactly
            # what happened to `resolution` between shipping the prompt
            # (2026-07-22) and the first real council that used it (2026-07-24):
            # the chairman emitted it, the markdown rendered it, and the stored
            # outcome lost it. Round-trip guard: test_resolution_survives_parse.
            resolution = entry.get("resolution")
            if isinstance(resolution, str) and resolution.strip():
                sub["resolution"] = resolution.strip()
            cleaned_disagreed.append(sub)
        if cleaned_disagreed:
            out["disagreed_claims"] = cleaned_disagreed
    # Machine-parsable combine. Same rebuild-from-whitelist rule as above: a field
    # not named here is dropped even when the chairman emitted it correctly.
    combined = data.get("combined_answer")
    if isinstance(combined, str) and combined.strip():
        out["combined_answer"] = combined.strip()
    grafts = data.get("grafts")
    if isinstance(grafts, list):
        from .council_schema import normalize_provider_slug

        cleaned_grafts: list[dict[str, object]] = []
        for g in grafts:
            if not isinstance(g, dict):
                continue
            claim = g.get("claim")
            if not isinstance(claim, str) or not claim.strip():
                continue
            entry: dict[str, object] = {"claim": claim.strip()}
            src = g.get("from")
            if isinstance(src, str) and src.strip():
                entry["from"] = normalize_provider_slug(src.strip())
            basis = g.get("basis")
            if isinstance(basis, str) and basis.strip().lower() in ("evidence", "lens"):
                entry["basis"] = basis.strip().lower()
            tension = g.get("tension")
            if isinstance(tension, str) and tension.strip():
                entry["tension"] = tension.strip()
            cleaned_grafts.append(entry)
        if cleaned_grafts:
            out["grafts"] = cleaned_grafts
    # Free-form facets. Kept UNCONDITIONALLY (not behind the combine flag): naming
    # what actually separated the answers is valuable on every council, and it is
    # the raw material the facet clustering embeds.
    facets = data.get("facets")
    if isinstance(facets, list):
        from .council_schema import normalize_provider_slug as _nps

        cleaned_facets: list[dict[str, object]] = []
        for fc in facets:
            if not isinstance(fc, dict):
                continue
            name = fc.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            entry: dict[str, object] = {"name": name.strip()[:120]}
            win = fc.get("winner")
            if isinstance(win, str) and win.strip():
                entry["winner"] = _nps(win.strip())
            basis = fc.get("basis")
            if isinstance(basis, str) and basis.strip():
                entry["basis"] = basis.strip()
            cleaned_facets.append(entry)
        if cleaned_facets:
            out["facets"] = cleaned_facets
    return out


