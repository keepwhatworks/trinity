"""MCP server exposing Trinity's canonical 4 tools + v1.5 trio + provider-loop tool.

Public tools, in lifecycle order:
  - route(task, harness, available_models, budget, latency)
      "Which model should I use?" — heuristic + k-NN, no model calls.
  - run_council(task, members, mode, sequence, responses)
      "Run the task across multiple models." — N+1 model calls.
      When `responses` is provided, skips member dispatch and goes straight
      to chairman synthesis (one model call). This is the structured
      verdict path: agreed_claims, disagreed_claims, winner, routing_lesson.
  - get_persona()
      "Return the user's /me document." — chairman context for any harness.
  - get_council_status(council_run_id)
      "Poll an in-flight or completed council." — for harnesses without fs access.

v1.5 pair: ask / get_picks.
In-protocol provider loop: import_provider_memory.

Plus MCP Resources (v2 substrate, 2026-05-26) for read-only context:
~/.trinity/memories/*, ~/.trinity/scoreboard/* exposed as
trinity:// URIs that the harness lists at session handshake.

Note: record_outcome retired 2026-05-21. CLI council-rate followed
on 2026-05-22 (task #134 — full rating retirement). Chairman's pick
(routing_label.winner) is the entire supervision signal now;
refinement prompts on the council page carry the "what user wanted
differently" signal inline.

handoff MCP tool retired 2026-05-26 alongside the CLI verb (0 usage
events in 163 launch_events on the dogfooder's machine; deprioritized
as primary demo earlier in the same session; spec v2 makes the lens-via-
MCP-Resources path the primary cross-provider continuity surface).

Internal helpers (get_status, get_elo, get_recent_councils, watch_once)
remain importable for the launchpad but are not exposed via MCP.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import ErrorData, Resource, Tool
from pydantic import AnyUrl

from .config import load_config
from .ranker import RoutingContext, build_default_ranker, predict_strongest_chairman, chairman_pick_reason
from .telemetry import APP_VERSION

# Pass Trinity's OWN version to the MCP Server. Without `version=`, the mcp SDK
# defaults serverInfo.version to the mcp *library's* version (e.g. 1.27.1), so a
# harness's MCP-server list would display "trinity-local v<mcp-lib-version>" —
# misidentifying Trinity as the transport library's version. APP_VERSION is the
# single resolver shared with telemetry (importlib.metadata.version of the
# installed package; correct for pip-installed users, the editable dev install's
# baked metadata otherwise).
server = Server("trinity-local", version=APP_VERSION)


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="ask",
            description=(
                "WHEN TO CALL THIS: before asking the user to pick between options that aren't "
                "user-personal preferences. Default to `ask` for product/architectural questions "
                "where the answer is 'which option is RIGHT' (Trinity routes to the model whose "
                "past wins on similar questions are strongest, applies the user's lens). Reserve "
                "AskUserQuestion / interactive prompts for genuinely personal choices (taste, "
                "priorities, scope).\n\n"
                "WHAT IT DOES: Trinity routes via kNN over the user's past prompts (which model "
                "has historically won for similar questions), dispatches one call to the best "
                "provider, and returns a concise structured answer.\n\n"
                "Returns: {answer, routed_to, trust_score (0..1), latency_ms, optional runner_up, "
                "optional escalate_hint='run_council' when trust is low and you should consider "
                "calling `run_council` for parallel perspectives instead}.\n\n"
                "Cost: ~$0.01–0.05 typical for one model call. Latency 3–30s dominated by "
                "the dispatched provider's response time (Trinity overhead is <1s). Single "
                "dispatched call, no flagship planning, no multi-model fan-out. If you genuinely "
                "need disagreement-vs-agreement structure, use `run_council` instead.\n\n"
                "MODE: default `mode='answer'` dispatches one call and returns the answer. "
                "`mode='route'` returns ONLY the routing decision "
                "{mode, primary, challenger, confidence, reason, fallback} with NO model call. "
                "Read it as an ESCALATION decision, not merely 'which provider': mode='council' "
                "is the signal the question is hard enough that one model isn't enough — reach "
                "for run_council. (Subsumes the standalone `route` tool, removed in the "
                "loop-primitive surface cut — `ask(mode='route')` is the one routing entrypoint.)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The user's question or task"},
                    "mode": {
                        "type": "string",
                        "enum": ["answer", "route"],
                        "default": "answer",
                        "description": "`answer` (default): dispatch one call, return the answer. `route`: return the routing decision only, no model call.",
                    },
                    "available_providers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Provider names allowed to route to (default: all enabled in config)",
                    },
                    "top_k": {"type": "integer", "default": 5, "description": "How many past prompts to retrieve for the vote (answer mode)"},
                    "budget": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal", "description": "route mode: cost preference"},
                    "latency": {"type": "string", "enum": ["fast", "normal", "patient"], "default": "normal", "description": "route mode: latency preference"},
                    "current_provider": {"type": "string", "description": "route mode: provider currently in use (optional)"},
                    "harness": {"type": "string", "description": "route mode: calling harness name (optional)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="run_council",
            description=(
                "WHEN TO REACH FOR THIS (the reflex): you're about to commit to a hard, "
                "contested, or hard-to-reverse decision and you are NOT fully confident. You "
                "alone are ONE model's opinion; this turns that into a panel of independent "
                "models + a synthesis conditioned on THIS user's taste — strictly more than you "
                "can do solo, and the move to make instead of quietly guessing. Concrete "
                "triggers: an architecture / public-API / data-schema call two senior engineers "
                "could disagree on; the user asks for a second opinion; ask() returned "
                "mode='council'; or you'd otherwise hand the user a multi-option choice that's "
                "product/architectural (NOT personal taste — that stays the user's). Skip "
                "trivial bugs, syntax, mechanical refactors, retrieval — one right answer "
                "wastes it.\n\n"
                "WHAT IT DOES: launches a multi-provider comparison (claude / codex / antigravity "
                "by default). Supports parallel mode (default; members run simultaneously) and "
                "chain mode (sequential refinement, each model refines the prior). The chairman "
                "synthesizes via the user's lens.md and returns agreed_claims, disagreed_claims "
                "with why_matters, winner, runner_up, routing_lesson. Returns the council_run_id "
                "and the path to the live review page; the council runs asynchronously.\n\n"
                "HORIZON WEIGHTING (#139): the chairman receives a query-horizon classification "
                "(tactical / strategic / philosophical) computed from the task text, and weights "
                "lens cards tagged with the matching horizon heavier in synthesis. This means a "
                "strategic question pulls strategic-tagged lens tensions to the front of the "
                "winner-picking reasoning. The routing_lesson Trinity returns reflects which "
                "lens tags were load-bearing in the verdict.\n\n"
                "When `responses` is provided (pre-supplied member outputs), skips member "
                "dispatch and goes straight to chairman synthesis — one model call instead of "
                "N+1. Use when you ALREADY HAVE multiple model outputs and just want the "
                "structured verdict.\n\n"
                "Cost: 3 member calls + 1 chairman call (~30s-2min). Anthropic's advisor-tool "
                "pattern is intra-provider (Sonnet→Opus, all Claude); `run_council` is "
                "cross-provider (claude/codex/antigravity) — different value prop, both can "
                "coexist."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "goal": {"type": "string", "default": "Find the strongest answer."},
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Provider names (e.g. ['claude', 'antigravity', 'codex']). Omit to use the default 3-member lineup.",
                    },
                    "mode": {"type": "string", "enum": ["parallel", "chain"], "default": "parallel"},
                    "sequence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "For mode='chain': the ordered provider sequence. Defaults to members.",
                    },
                    "primary_provider": {"type": "string", "description": "Chairman/synthesizer. Auto-selected if omitted."},
                    "responses": {
                        "type": "array",
                        "description": (
                            "Pre-supplied member outputs. When present, skips member dispatch "
                            "and runs chairman synthesis only (structured verdict)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "provider": {"type": "string"},
                                "content": {"type": "string"},
                                "model": {"type": "string"},
                            },
                            "required": ["provider", "content"],
                        },
                    },
                    "host_responses": {
                        "type": "array",
                        "description": (
                            "PROVIDER-SIDE LOOP (flag-gated by TRINITY_HOST_CLAUDE_MEMBER). "
                            "Member answers YOU (the host agent) already produced in-session — "
                            "typically the Claude voice on the user's full plan, so the Claude "
                            "member rides the session instead of `claude -p`/MCP sampling (the "
                            "deprecated path). Trinity dispatches ONLY the members you did NOT "
                            "supply (e.g. codex, antigravity) and synthesizes over the full set. "
                            "To use: answer the task yourself, then call run_council with "
                            "host_responses=[{provider:'claude', content:<your answer>}]."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "provider": {"type": "string"},
                                "content": {"type": "string"},
                                "model": {"type": "string"},
                            },
                            "required": ["provider", "content"],
                        },
                    },
                    "host_synthesis": {
                        "type": "string",
                        "description": (
                            "PROVIDER-SIDE LOOP, chairman-on-host (flag-gated by "
                            "TRINITY_HOST_CLAUDE_MEMBER). The Routing-JSON verdict YOU synthesized "
                            "in-session over the members (full plan, no `claude -p`). Pass with "
                            "responses=[all members]; Trinity records the outcome with ZERO chairman "
                            "model calls. Get the members first via host_responses + dispatch_only."
                        ),
                    },
                    "dispatch_only": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "With host_responses (flag-gated): dispatch only the non-host members "
                            "(codex/antigravity) and RETURN their answers + the synthesis prompt for "
                            "YOU to synthesize, instead of running a chairman. Pair with a follow-up "
                            "run_council(responses=..., host_synthesis=...) call."
                        ),
                    },
                    "wait_seconds": {
                        "type": "number",
                        "default": 0,
                        "description": (
                            "If > 0, block up to this many seconds waiting for the council to "
                            "finish; if it completes in time, the outcome (winner, agreed/disagreed "
                            "claims, routing_lesson) is returned inline. Otherwise returns the "
                            "council_run_id immediately and the caller polls get_council_status. "
                            "Useful when the council is likely cached or fast — saves a round trip. "
                            "Ignored when `responses` is provided (synthesis is always inline)."
                        ),
                    },
                },
                "required": ["task"],
            },
        ),
        # record_outcome retired 2026-05-21 per user direction "we are
        # sunsetting user ratings. Full retirement including MCP." The
        # chairman's pick (routing_label.winner) is the supervision
        # signal that feeds compute_personal_routing_table() now (commit
        # bb817b6). Refinement prompts on the council page carry the
        # "what user wanted differently" signal. CLI `council-rate`
        # was retired one day later on 2026-05-22 (task #134) — full
        # rating retirement, no power-user override remained.
        # Registry entry: src/trinity_local/retired_names.py.
        Tool(
            name="get_persona",
            description=(
                "Return the user's lens — paired tensions distilled by a chairman call over "
                "the user's prompt history across providers (lives at `~/.trinity/memories/lens.md`). "
                "Pull this once at session start and use it as latent context to tailor responses, "
                "terseness, vocabulary, and standing decisions to THIS user. Empty string when not "
                "built — run `trinity-local lens-build` to (re)build, or `trinity-local dream` for "
                "the full memory-rebuild pass.\n\n"
                "AMBIENT ALTERNATIVE (zero call): the user can run `trinity-local lens-skill` to "
                "write this lens as a `SKILL.md` their harness auto-loads (e.g. into "
                "`~/.claude/skills/`) — then their taste is already in your context and you don't "
                "need to call this at all.\n\n"
                "Abstract-lens cards may carry a horizon suffix tag `[tactical]` / `[strategic]` / "
                "`[philosophical]` (task #139). Tactical = response-shape preference (format, length, "
                "what to include); strategic = quarter-scale trajectory choices; philosophical = "
                "year-scale identity / framing. When the user's query reads as a particular horizon, "
                "weight matching lens cards heavier than non-matching ones — that's the lens "
                "prioritization the local chairman does too. Untagged cards default to tactical."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_picks",
            description=(
                "Return the user's lens-derived routing picks from "
                "`~/.trinity/scoreboard/picks.json` — the per-lens-basin "
                "chairman-winner tally across past councils. Each basin: "
                "`{winner, count, margin, n_episodes, evidence}` — the provider "
                "that wins for that kind of question, how many real-contest "
                "councils it was tallied from, the margin over the runner-up "
                "(the confidence proxy), and `routes` — whether ask() actually "
                "routes on this basin (margin >= `winner_margin_floor`) or treats "
                "it as a near-tie that falls to kNN. Pull this when planning a "
                "complex task — it tells you which provider this user prefers for "
                "THIS kind of question; act on `routes:true` picks as firm "
                "preferences and `routes:false` picks as weak leans only. Empty "
                "when no consolidation has run yet (`trinity-local consolidate`). "
                "Filter to a specific basin with `basin_id`, or to confident picks "
                "with `min_trust` (a margin floor); omit for the full map."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "basin_id": {
                        "type": "string",
                        "description": "Optional. Return only the pick for this lens basin id (e.g. 'b00').",
                    },
                    "min_trust": {
                        "type": "number",
                        "default": 0.0,
                        "description": "Filter to picks whose margin (confidence over the runner-up) >= this value (0..1).",
                    },
                },
            },
        ),
        Tool(
            name="get_council_status",
            description=(
                "Poll an in-flight or completed council by `council_run_id` (returned from "
                "run_council). Returns: status (running/completed/failed/canceled), per-member "
                "progress, chairman synthesis state, elapsed seconds, and the final outcome "
                "summary (winner, agreed/disagreed claims, routing_lesson) when complete. "
                "Use this to wait on async councils without filesystem access."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "council_run_id": {"type": "string"},
                },
                "required": ["council_run_id"],
            },
        ),
        Tool(
            name="import_provider_memory",
            description=(
                "Pipe lens tensions OR rejection signals you (the agent) "
                "extracted from your conversation history with this user "
                "directly into Trinity's local state — no terminal, no "
                "copy-paste. The agent IS a provider with the user's "
                "history on its side, so this is the same loop as "
                "`trinity-local eval-prompt → eval-import` but closes "
                "inside the harness.\n\n"
                "USE WHEN: the user asks you to 'save my preferences to "
                "Trinity', 'update my lens with what you've learned', or "
                "you (the agent) recognize you've accumulated useful "
                "rejection signals worth persisting.\n\n"
                "SHAPE: pass `kind='eval'` with a `payload` matching the "
                "schema in docs/evals-from-provider.md (rejections: "
                "[{type, model_quote, user_substitute, why_signal, "
                "confidence}, ...]). Pass `kind='lens'` with the schema "
                "in docs/lens-from-provider.md (tensions: [{pole_a, "
                "pole_b, failure_a, failure_b, horizon, evidence, "
                "confidence, why_matters}, ...]).\n\n"
                "Returns a structured summary: count of new vs duplicate "
                "vs malformed items, the on-disk path written. Same "
                "dedup rules as the CLI verbs — same payload twice is "
                "a no-op.\n\n"
                "Cross-provider attribution: when `provider` is set, it "
                "overrides any source_provider in the payload (useful "
                "when you want to attribute to yourself explicitly)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["lens", "eval"],
                        "description": "Which memory artifact to ingest.",
                    },
                    "payload": {
                        "type": "object",
                        "description": (
                            "The JSON payload, matching the schema for "
                            "the chosen kind. See "
                            "docs/lens-from-provider.md or "
                            "docs/evals-from-provider.md."
                        ),
                    },
                    "provider": {
                        "type": "string",
                        "description": (
                            "Override the source_provider attribution "
                            "(optional). When omitted, falls back to "
                            "payload.source_provider or 'unknown'."
                        ),
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Parse + return the merge plan without writing.",
                    },
                },
                "required": ["kind", "payload"],
            },
        ),
        Tool(
            name="lens_generators",
            description=(
                "Run the generators pass (the lens 'lift') and return the user's "
                "cross-domain GENERATING invariants — the deep preferences the task-level "
                "lens tensions are projections of (the same reflex in software AND "
                "materials AND finance AND epistemology). Runs INSIDE this session so it "
                "samples via the user's subscription (no `claude -p`, no transcript "
                "pollution). Selects domain-diverse evidence, abstracts to generators, "
                "then a dual autonomous self-critique (contradiction-split + off-plane "
                "gap). Writes `~/.trinity/memories/generators.md` and returns the "
                "structured generators. A multi-minute call (embedding + two chairman "
                "passes) — trigger on explicit user request, not speculatively."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="run_eval",
            description=(
                "Score a model against the USER's taste, IN-SESSION — the judge "
                "rides this Claude session (#263 MCP sampling) instead of burning "
                "`claude -p` quota (the one cognition surface the CLI `eval-run` "
                "still spent it on). Dispatches `target` over the user's personal "
                "eval set (their own cross-provider rejection signals), then judges "
                "each answer against `lens.md` with a second provider. Returns the "
                "aggregate score + per-axis (REFRAME/REDIRECT/SHARPENING/COMPRESSION) "
                "breakdown + the judge used. USE WHEN: the user asks 'how does "
                "<model> do on MY kind of work?' / 'score the new model'. `target` "
                "is the model to score (claude/codex/gemini or a brand alias); "
                "`judge` defaults to the most-aligned non-target provider (a strong "
                "fixed judge — Claude rides sampling here, so it's free); `limit` "
                "caps items (default 5 — every item is a REAL dispatch, so this is "
                "a multi-minute call). Needs a built eval set "
                "(`trinity-local eval-build`) and >=2 enabled providers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Provider/model to score (claude/codex/gemini or a brand alias like 'gpt').",
                    },
                    "judge": {
                        "type": "string",
                        "description": "Optional judge provider; defaults to the most-aligned non-target (Claude rides sampling — free).",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                        "description": "Max eval items to run (default 5). Each is a real dispatch — keep it small.",
                    },
                    "eval_id": {
                        "type": "string",
                        "description": "Optional eval-set id; defaults to the most recently built set on disk.",
                    },
                },
                "required": ["target"],
            },
        ),
    ]


_lens_kicks_fired = False
_recurring_kicks_last = 0.0
_lens_kicks_lock = threading.Lock()
# Re-check the refresh + stale-ingest gates this often on a LONG-LIVED server.
# The kicks themselves are internally gated (REFRESH_MIN_AGE_H / the 24h stale-pass
# marker / the cross-process lock / the _recently_kicked cooldown), so re-firing is
# a cheap no-op when nothing is due. This fixes the once-per-process freeze
# (confirmed 2026-06-29): a days-long MCP server fired the refresh ONCE at its first
# tool call and never again, so the lens/ingest silently froze until a manual
# `lens --force`. The chairman was never the problem — the trigger was.
_RECURRING_KICK_INTERVAL_S = 1800.0  # 30 min


def _maybe_fire_lens_kicks() -> None:
    """On a tool call: fire the keep-current kicks (lens refresh + stale ingest)
    the first time AND every _RECURRING_KICK_INTERVAL_S after, so a long-lived
    server stays current without a restart; fire the genuinely-once kicks (embedder
    offer, roots discovery) only on the first call. Best-effort: never let an
    auto-kick failure break a tool call."""
    global _lens_kicks_fired, _recurring_kicks_last
    import time as _time

    now = _time.monotonic()
    with _lens_kicks_lock:
        first_time = not _lens_kicks_fired
        _lens_kicks_fired = True
        # Recurring = first call OR the interval has elapsed since the last one.
        due_recurring = first_time or (now - _recurring_kicks_last) >= _RECURRING_KICK_INTERVAL_S
        if due_recurring:
            _recurring_kicks_last = now

    if due_recurring:
        try:
            from .cold_start import maybe_kick_first_lens_build, maybe_kick_lens_refresh

            # First build owns the cold-start case; refresh owns keep-current. Each
            # internally gates (should_build/should_refresh + the shared lock), so
            # calling both is a cheap no-op once a current lens exists.
            maybe_kick_first_lens_build()
            maybe_kick_lens_refresh()
        except Exception:
            pass
        try:
            # #251: MCP usage (not just councils) is the Auto-Dream "usage" trigger
            # for the stale ingest+embed pass — a user who works through ask/get_picks
            # for days without a council still heals. Internally gated (24h marker +
            # lock + TRINITY_AUTOSCAN_DISABLED).
            from .stale_pass import maybe_kick_stale_pass

            maybe_kick_stale_pass(trigger="mcp_tool_call")
        except Exception:
            pass

    if first_time:
        try:
            # Plugin-only installs run on the lexical fallback (the bootstrap venv
            # ships only numpy/mcp/Pillow). Offer the one-time embedding-engine
            # download via MCP elicitation — gated, background, opt-out-able; no-op
            # when the embedder is already live or the client lacks elicitation.
            # ONCE per process — re-offering every interval would re-nag the wizard.
            from .embedder_wizard import maybe_offer_embedder_download

            maybe_offer_embedder_download()
        except Exception:
            pass
        # Roots: ask the client what filesystem roots it exposes (e.g. the open
        # project), and log them. Best-effort — surfaces what transcript dirs a
        # future ingest could auto-discover; no-op when the client lacks roots.
        try:
            from .mcp_features import discover_roots, mcp_log

            roots = discover_roots()
            if roots:
                mcp_log("info", f"Client exposed {len(roots)} root(s): {', '.join(roots[:5])}")
        except Exception:
            pass


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[Any]:
    arguments = arguments or {}
    # Register the active MCP server session so providers running in
    # worker threads can find it (via mcp_sampling.request_claude_sample).
    # When Trinity-MCP is loaded inside Claude Desktop and the client
    # advertised sampling capability, the Claude provider routes through
    # sampling instead of `claude -p` subprocess — sidestepping the
    # post-2026-06-15 Agent SDK credit pool. ContextVar set here
    # propagates to the ThreadPoolExecutor workers in council_runner
    # via copy_context().
    from .mcp_features import clear_request_context, set_request_context
    from .mcp_sampling import clear_active_session, set_active_session
    try:
        ctx = server.request_context
        set_active_session(ctx.session)
        # Capture the request id + client-attached progress token so worker-
        # thread log/progress calls (councils run off-thread) attribute
        # themselves to this request. meta is None when the client sent no
        # _meta; progressToken is None when it didn't ask for progress.
        progress_token = getattr(getattr(ctx, "meta", None), "progressToken", None)
        set_request_context(ctx.request_id, progress_token)
    except (AttributeError, LookupError):
        # Defensive: if the SDK version lacks request_context.session,
        # or this is invoked outside a real MCP request (e.g., tests),
        # skip session registration and let sampling auto-decline.
        pass

    # #263 keystone: fire the LLM-making auto-kicks (first lens build +
    # activity-gated refresh) HERE — on the first tool call, with the session
    # registered above — so their Claude stages route through sampling and
    # ride the user's Claude Code subscription instead of burning `claude -p`
    # quota. copy_context() inside the kick snapshots the active session for
    # the build thread (independent of the finally-clear below). Fire-once.
    _maybe_fire_lens_kicks()

    try:
        if name == "ask":
            return await _ask(arguments)
        if name == "get_picks":
            return await _get_picks(arguments)
        if name == "run_council":
            return await _run_council(arguments)
        # `record_outcome` dispatch removed 2026-05-21 (rating UX sunset).
        if name == "get_persona":
            return await _get_persona(arguments)
        if name == "get_council_status":
            return await _get_council_status(arguments)
        if name == "import_provider_memory":
            return await _import_provider_memory(arguments)
        if name == "lens_generators":
            return await _lens_generators(arguments)
        if name == "run_eval":
            return await _run_eval(arguments)
        return [ErrorData(code=404, message=f"Tool not found: {name}")]
    except Exception as exc:
        return [ErrorData(code=500, message=f"{type(exc).__name__}: {exc}")]
    finally:
        clear_active_session()
        clear_request_context()


# ─── MCP Resources — read-only context surfaces ────────────────────
#
# Per the v2 substrate spec (docs/PREFERENCE_CORPUS_SPEC.md), Trinity
# exposes the four cognitive memories + the operational scoreboards
# as MCP Resources. Resources are listed at session start so any
# MCP-aware harness sees them without a tool round-trip — the agent
# can read trinity://memories/lens.md before the user even types a
# prompt, and condition every response on the lens.
#
# This is the leverage point for Phase A of the v2 arc: tools are
# pull (the agent decides when to call); resources are push (the
# harness lists them at handshake, agent reads on demand). Same
# data, different cost profile.

# Static resource catalog. Each entry maps a trinity:// URI to a
# (label, mime_type, path_func) tuple. path_func is a no-arg callable
# that returns the on-disk Path — lazy so tests can override
# TRINITY_HOME and the resource list reflects the new location.
def _resource_catalog() -> list[tuple[str, str, str, str, Any]]:
    """Return the canonical list of MCP Resources Trinity exposes.

    Each tuple: (uri, name, description, mime_type, path_func).

    The four cognitive memories (core / lens / topics / vocabulary)
    map to the lens hierarchy chairman reads top-down. The two
    scoreboards (picks / routing) are read-only operational data
    surfaced for agents that want to see "what Trinity routes to
    today" without firing the route() tool.

    AGENTS.md was originally on this catalog but dropped 2026-05-26
    after user pushback: AGENTS.md is project-scoped by convention
    (agents read `./AGENTS.md` in the user's repo, not at home), and
    every harness that reads AGENTS.md also reads MCP Resources —
    so generating a user-home AGENTS.md was ceremonial. The lens flows
    via trinity://memories/lens.md instead; users who want a
    project-local AGENTS.md can write one that references that URI.
    """
    from . import state_paths as _sp
    memories = _sp.memories_dir()
    scoreboard = _sp.scoreboard_dir()
    return [
        (
            "trinity://memories/core.md",
            "Trinity Core Memory",
            "Your one-paragraph manifesto — chairman reads this first to ground every council in your voice.",
            "text/markdown",
            # core.md lives at the TOP LEVEL (~/.trinity/core.md via core_path()),
            # NOT under memories/ like lens/topics/vocabulary. The URI keeps the
            # memories/ namespace for back-compat, but the path_func must resolve
            # to the real file or every read falls through to the cold-start stub
            # even on a fully-dreamed install (the chairman-identity resource the
            # harness reads FIRST would silently never deliver). See core_path().
            _sp.core_path,
        ),
        (
            "trinity://memories/lens.md",
            "Trinity Lens",
            "Paired tensions you reject vs accept. The chairman conditions synthesis on these. The load-bearing personalization layer.",
            "text/markdown",
            lambda: memories / "lens.md",
        ),
        (
            "trinity://memories/topics.json",
            "Trinity Topics (Basins)",
            "Subject basins extracted from your corpus — semantic clusters with TF-IDF labels and lens-evidence maps.",
            "application/json",
            lambda: memories / "topics.json",
        ),
        (
            "trinity://memories/vocabulary.md",
            "Trinity Vocabulary",
            "Your anchors, homonyms, and overloaded terms. What 'X' means to you, not to the model's training distribution.",
            "text/markdown",
            lambda: memories / "vocabulary.md",
        ),
        (
            "trinity://scoreboard/picks.json",
            "Trinity Picks (Cortex Routing Rules)",
            "Extracted routing rules per task basin — what model wins on which kind of question, derived from your council history.",
            "application/json",
            lambda: scoreboard / "picks.json",
        ),
        (
            "trinity://scoreboard/routing.json",
            "Trinity Personal Routing Table",
            "Per-task-type provider track record — aggregated from chairman picks across all your councils.",
            "application/json",
            lambda: scoreboard / "routing.json",
        ),
    ]


@server.list_resources()
async def handle_list_resources() -> list[Resource]:
    """Advertise Trinity's readable resources to the harness.

    All resources are exposed unconditionally — even when their files
    don't exist yet (cold install). read_resource() returns a stub
    explaining how to populate them rather than 404'ing, so the
    agent sees "AGENTS.md not yet generated — run trinity-local
    dream" and can offer that action to the user.
    """
    return [
        Resource(
            uri=AnyUrl(uri),
            name=name,
            description=description,
            mimeType=mime,
        )
        for (uri, name, description, mime, _path_func) in _resource_catalog()
    ]


def _is_numeric_vector(value: Any) -> bool:
    """True for an embedding/centroid vector — a list of ≥16 plain numbers.

    bool is a subclass of int, so exclude it explicitly (a list of flags is
    not a vector). The ≥16 floor clears scalar pairs and short lists like
    top_terms while catching every 768-dim centroid we actually carry.
    """
    return (
        isinstance(value, list)
        and len(value) >= 16
        and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in value
        )
    )


# Engine-internal JSON fields that are pure noise to an LLM reading a
# resource: opaque ID/membership lists the agent can't resolve. Embedding
# vectors are caught structurally by _is_numeric_vector (no allow-list needed).
_ENGINE_INTERNAL_KEYS = frozenset({"prompt_ids"})


def _project_json_for_agent(obj: Any) -> Any:
    """Strip engine internals from a JSON resource before serving it.

    Trinity's basin/scoreboard files co-locate runtime state (768-dim
    centroids, prompt-membership indices) with the human-meaningful fields
    (labels, representatives, track records). The engine reads the raw file
    from disk via state_paths; an agent reading the MCP *resource* only wants
    the meaningful fields. Serving the raw file dumps megabytes of float
    vectors into the harness context — wasteful AND it buries the signal.

    This drops (a) any numeric vector (_is_numeric_vector) and (b) the known
    opaque-ID keys, recursively. Markdown resources are never touched — they're
    already agent-readable and the byte-for-byte contract still holds for them.
    """
    if isinstance(obj, dict):
        return {
            k: _project_json_for_agent(v)
            for k, v in obj.items()
            if k not in _ENGINE_INTERNAL_KEYS and not _is_numeric_vector(v)
        }
    if isinstance(obj, list):
        return [_project_json_for_agent(v) for v in obj]
    return obj


def _canonicalize_resource_routing_slugs(obj: Any) -> Any:
    """Fold web-era capture slugs (chatgpt/claude_ai/gemini) in a scoreboard
    resource's ROUTING-DECISION fields to the dispatchable CLI slug.

    The `get_picks` / `ask` TOOLS canonicalize on read (cortex.load_routing_patterns,
    v1.7.166), but the `trinity://scoreboard/picks.json` RESOURCE reads the raw file
    — which keeps the historical web-era slugs until the next `consolidate`. Without
    this, the resource hands an agent `primary: "chatgpt"` (not a provider it can
    dispatch to) while the tool returns `"codex"` — inconsistent and unactionable.
    The routing_rule decision fields AND the provider-KEYED scoreboard dicts
    (winner_distribution / successful_prompts / failure_modes) are folded — both
    exactly as the tool now does (cortex._pattern_from_dict, v1.7.x); only the
    free-text `reason` prose keeps its provenance. Walks the projected (small)
    structure, so topics.json's centroids are already stripped."""
    from .council_schema import normalize_provider_slug

    # Scoreboard fields keyed BY provider name — fold their keys, not their values.
    _PROVIDER_KEYED = {"winner_distribution", "successful_prompts", "failure_modes"}

    def _merge(a: Any, b: Any) -> Any:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a + b
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        if isinstance(a, str) and isinstance(b, str):
            return f"{a}; {b}"
        return a  # incompatible types collide → keep the first

    def _fold_keys(d: dict) -> dict:
        out: dict = {}
        for pk, pv in d.items():
            ck = normalize_provider_slug(pk) if isinstance(pk, str) else pk
            out[ck] = _merge(out[ck], pv) if ck in out else pv
        return out

    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if k == "routing_rule" and isinstance(v, dict):
                rr = dict(v)
                if isinstance(rr.get("primary"), str):
                    rr["primary"] = normalize_provider_slug(rr["primary"])
                if isinstance(rr.get("challenger"), str):
                    rr["challenger"] = normalize_provider_slug(rr["challenger"])
                subs = rr.get("subroutes")
                if isinstance(subs, list):
                    rr["subroutes"] = [
                        {**s, "prefer": normalize_provider_slug(s["prefer"])}
                        if isinstance(s, dict) and isinstance(s.get("prefer"), str)
                        else s
                        for s in subs
                    ]
                out[k] = rr
            elif k in _PROVIDER_KEYED and isinstance(v, dict):
                out[k] = _fold_keys(v)
            else:
                out[k] = _canonicalize_resource_routing_slugs(v)
        return out
    if isinstance(obj, list):
        return [_canonicalize_resource_routing_slugs(v) for v in obj]
    return obj


def _winner_margin_floor() -> float:
    """The margin ask() routes on (lens_routing.WINNER_MARGIN_FLOOR). Below it a
    basin's winner is a near-tie and ask falls to kNN. Local import + literal
    fallback mirrors launchpad_data.py / memory_viewer.py."""
    try:
        from .lens_routing import WINNER_MARGIN_FLOOR
        return float(WINNER_MARGIN_FLOOR)
    except Exception:
        return 0.15


def _annotate_picks_routes(obj: Any, floor: float) -> Any:
    """Add `routes` (margin >= floor) to each per-basin pick in the picks RESOURCE,
    so the handshake-time push matches the get_picks TOOL: the agent can tell a firm
    route from a sub-floor near-tie. Picks-resource only — gated on the resource name
    by the caller, since routing.json/topics.json have different shapes. Picks.json
    is flat `{basin_id: {winner, count, margin, ...}}`; a value with a `winner` + a
    `margin` is a live pick to annotate (a legacy/odd entry is left untouched)."""
    if not isinstance(obj, dict):
        return obj
    out: dict = {}
    for k, v in obj.items():
        if isinstance(v, dict) and isinstance(v.get("winner"), str) and "margin" in v:
            try:
                m = float(v.get("margin") or 0.0)
            except (TypeError, ValueError):
                m = 0.0
            out[k] = {**v, "routes": m >= floor}
        else:
            out[k] = v
    return out


def _canonicalize_member_slugs(seq: list, *, dedupe: bool = True) -> list:
    """Canonicalize agent-supplied provider slugs at the council-LAUNCH boundary.

    An agent can reach `run_council` with a web-era brand slug — a stale
    `get_picks` `primary='chatgpt'` (long-lived --mcp server serving pre-fix
    code), a provider-imported pick, or copy-paste from a council page — while
    `config.providers` only knows the CLI slugs (claude/codex/antigravity).
    Without folding here, dispatch does `config.providers.get('chatgpt')` → None
    and SILENTLY DROPS that member (or loses the chairman), so a 3-member council
    quietly runs with 2. `normalize_provider_slug` maps the known brand names
    (chatgpt→codex, claude_ai→claude, gemini→antigravity) and passes canonical
    slugs AND arbitrary labels ('answer_a', 'external') through untouched. The
    symmetric boundary to council_runtime's outcome-LOAD canonicalization (which
    only sees stored outcomes, never the launch-time member list).

    `dedupe=True` (parallel members): collapse `['chatgpt','codex']` → `['codex']`
    so the same provider isn't dispatched twice concurrently. `dedupe=False`
    (chain `sequence`): a chain legitimately REVISITS a provider across rounds —
    `['claude','codex','claude']` must stay 3 steps — so fold each element in
    place without collapsing repeats."""
    from .council_schema import normalize_provider_slug

    out: list = []
    for s in seq:
        c = normalize_provider_slug(s) if isinstance(s, str) else s
        if not c:
            continue
        if dedupe and c in out:
            continue
        out.append(c)
    return out


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
    """Return the contents of a trinity:// resource.

    Returns ReadResourceContents carrying the resource's DECLARED mimeType
    (from the same catalog list_resources advertises) — a JSON resource
    (picks.json / routing.json / topics.json) reads back as
    `application/json`, markdown as `text/markdown`. Returning a bare `str`
    would let the SDK default every read to `text/plain`, so a JSON resource
    advertised as application/json would read as text/plain — a list/read
    mimeType mismatch a spec-conformant harness can trip on.

    Cold-install behavior: when the underlying file doesn't exist, return a
    markdown stub explaining how to populate it (actionable feedback over a
    dead-resource 404). The stub is markdown regardless of the resource's
    declared type, so it reads as text/markdown, not e.g. application/json.

    JSON resources (topics / picks / routing) are projected for the agent —
    embedding centroids and opaque ID/membership lists are stripped before
    serving (see _project_json_for_agent). Without this, topics.json alone is
    ~2.2 MB of 768-dim float vectors per read. Markdown resources (core / lens
    / vocabulary) are served byte-for-byte — they're already agent-readable.
    """
    uri_str = str(uri)
    catalog = {entry[0]: entry for entry in _resource_catalog()}
    if uri_str not in catalog:
        raise ValueError(f"Unknown Trinity resource: {uri_str}")
    _, name, _, mime, path_func = catalog[uri_str]
    path = path_func()
    if not path.exists():
        # Stub response per cold-install path: tell the agent what
        # the resource WILL contain + how to populate it. Strictly
        # better than 404 — the agent surfaces a concrete next-step
        # to the user instead of a vague error.
        stub = (
            f"# {name}\n\n"
            f"_(empty — this file does not exist yet)_\n\n"
            f"Trinity hasn't generated this resource yet. To populate it:\n\n"
            f"```bash\n"
            f"trinity-local dream\n"
            f"```\n\n"
            f"Dream reads your existing corpus and writes the four cognitive "
            f"memories (`core.md`, `lens.md`, `topics.json`, `vocabulary.md`) "
            f"plus the AGENTS.md lens-derived guidance. After dream finishes, "
            f"re-read this resource and it will be populated.\n\n"
            f"Resource URI: `{uri_str}`\n"
            f"On-disk path: `{path}`\n"
        )
        return [ReadResourceContents(content=stub, mime_type="text/markdown")]
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Non-UTF8 bytes on disk (corruption / bad encoding). Serve the readable
        # parts with U+FFFD replacements rather than raise an McpError that
        # breaks the agent's resource read — the SAME "serve it, don't 500"
        # philosophy as the malformed-JSON branch below (the agent sees the
        # bytes and can surface the corruption to the user).
        raw = path.read_text(encoding="utf-8", errors="replace")
    if mime == "application/json":
        try:
            projected = _canonicalize_resource_routing_slugs(
                _project_json_for_agent(json.loads(raw))
            )
            # Picks RESOURCE matches the get_picks TOOL: annotate each pick with
            # `routes` so the handshake-time push doesn't present a sub-floor
            # near-tie as a firm route (gated to the picks URI — other scoreboards
            # have different shapes; the catalog `name` is a human title, not the
            # filename, so gate on the stable URI).
            if uri_str == "trinity://scoreboard/picks.json":
                projected = _annotate_picks_routes(projected, _winner_margin_floor())
            raw = json.dumps(projected, ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            # Malformed JSON on disk — serve it raw rather than 500. The agent
            # sees the actual bytes (and can surface the corruption) instead of
            # a dead read; the projection is an optimization, not a gate.
            pass
    return [ReadResourceContents(content=raw, mime_type=mime)]


# ─── MCP Prompts — Trinity's verbs as native slash-prompts ──────────
#
# Registering these handlers makes the host advertise the `prompts`
# capability, so `/council`, `/lens`, `/ask` show up in the harness's
# slash menu. Each prompt expands to a user-role message that tells the
# agent which Trinity tool to call — discovery for users who'd never read
# a tool docstring. The arg becomes the task/query text.

_PROMPT_CATALOG = [
    {
        "name": "council",
        "description": "Compare a hard question across Claude, GPT, and Gemini "
        "and synthesize the answer your taste would pick.",
        "arg": ("task", "The question or task to run the council on.", True),
        "tool": "run_council",
        "render": lambda task: (
            f"Run a Trinity council on this, then report the chairman's verdict "
            f"with the agreed_claims and disagreed_claims:\n\n{task}\n\n"
            f"Use the `mcp__trinity-local__run_council` tool."
        ),
    },
    {
        "name": "ask",
        "description": "Route one question to the single best provider for it "
        "(cheap — one call, chairman-blessed).",
        "arg": ("query", "The question to route.", True),
        "tool": "ask",
        "render": lambda query: (
            f"Route this question to the best provider for it and answer:\n\n{query}\n\n"
            f"Use the `mcp__trinity-local__ask` tool."
        ),
    },
    {
        "name": "lens",
        "description": "Read the user's taste lens so you can tailor this "
        "session's answers to how they decide.",
        "arg": ("focus", "Optional: what to focus the lens read on.", False),
        "tool": "get_persona",
        "render": lambda focus: (
            "Read the user's Trinity taste lens via the "
            "`mcp__trinity-local__get_persona` tool and condition your answers "
            "on it for the rest of this session."
            + (f" Focus especially on: {focus}." if focus else "")
        ),
    },
]


@server.list_prompts()
async def handle_list_prompts() -> list[Any]:
    """Advertise Trinity's verbs as MCP prompts (slash-menu entries)."""
    from mcp.types import Prompt, PromptArgument

    prompts = []
    for spec in _PROMPT_CATALOG:
        arg_name, arg_desc, arg_required = spec["arg"]
        prompts.append(
            Prompt(
                name=spec["name"],
                description=spec["description"],
                arguments=[
                    PromptArgument(
                        name=arg_name, description=arg_desc, required=arg_required
                    )
                ],
            )
        )
    return prompts


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: dict | None) -> Any:
    """Expand a Trinity prompt into a user-role message that routes the agent
    to the matching Trinity tool."""
    from mcp.types import GetPromptResult, PromptMessage, TextContent

    arguments = arguments or {}
    spec = next((s for s in _PROMPT_CATALOG if s["name"] == name), None)
    if spec is None:
        raise ValueError(f"Unknown Trinity prompt: {name}")
    arg_name = spec["arg"][0]
    text = spec["render"](arguments.get(arg_name, "") or "")
    return GetPromptResult(
        description=spec["description"],
        messages=[
            PromptMessage(role="user", content=TextContent(type="text", text=text))
        ],
    )


@server.set_logging_level()
async def handle_set_logging_level(level: Any) -> None:
    """Honor the client's logging/setLevel request — filters mcp_log emissions
    to the requested minimum level for the rest of the connection."""
    from .mcp_features import set_min_log_level

    set_min_log_level(str(level))


def _text(payload: dict | str) -> dict:
    """Wrap a JSON-serializable result as an MCP text response.

    Two optional hints get injected into dict payloads so agents can
    surface them inline:

    * ``cold_start`` — "Trinity is ingesting your CLI history…" when
      the first-run auto-scan is running or just finished.
    * ``extension_status`` — "Chrome extension not configured — install
      it for browser capture + auto-update." when the user installed
      via curl|bash and never wired the extension. Closes the cross-
      bootstrap loop from the agent side (the launchpad and install.sh
      already surface this; agents calling MCP tools see it too).

    Strings pass through unchanged — hints only attach to structured
    responses where the agent can pluck a field.
    """
    if isinstance(payload, dict):
        if "cold_start" not in payload:
            try:
                from .cold_start import cold_start_hint

                hint = cold_start_hint()
                if hint is not None:
                    payload = dict(payload)
                    payload["cold_start"] = hint
            except Exception:
                pass
        # #212 cold-start aha: once the lens has signal, surface ONE surprising
        # true tension so the agent can open with "here's one thing I've
        # learned about how you decide" — the differentiated wow, before the
        # user has learned a verb. Self-omits on a cold install (None).
        if "lens_cold_open" not in payload:
            try:
                from .cold_start import cold_open_tension

                co = cold_open_tension()
                if co:
                    payload = dict(payload)
                    payload["lens_cold_open"] = co
            except Exception:
                pass
        if "extension_status" not in payload:
            try:
                hint = _extension_status_hint()
                if hint is not None:
                    payload = dict(payload)
                    payload["extension_status"] = hint
            except Exception:
                pass
        # Staged onboarding: the next funnel rung (create-lens → add-web-history →
        # view-lens → lens-health), one per response, gated on live state. Self-
        # omits once the user adopts the step (the prereq flips). Fusion-first: a
        # no-lens user is nudged toward the lens add-on without ever being blocked.
        if "tip" not in payload:
            try:
                from .tips import next_tip

                t = next_tip()
                if t:
                    payload = dict(payload)
                    payload["tip"] = t
            except Exception:
                pass
    body = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
    return {"type": "text", "text": body}


# Cached at module level so we don't hit the filesystem on every MCP
# response. The extension config is set by `install-extension` and
# doesn't change mid-process — a process-lifetime cache is correct.
# `_NOT_COMPUTED` is the sentinel for "we haven't asked yet." Critical
# detail: `is not object()` would create a FRESH sentinel each call
# and always read as "not computed" — the cache would never hit. Use
# a stable module-level singleton.
_NOT_COMPUTED = object()
_EXTENSION_HINT_CACHED: dict | None | object = _NOT_COMPUTED


def _extension_status_hint() -> dict | None:
    """Return an extension-status hint dict when the Chrome extension
    isn't wired, else None. Cached for the process lifetime.

    Hint shape:
      {
        "configured": False,
        "message": str,           # human-readable, agent surfaces inline
        "install_doc": str,       # URL to the install instructions
      }

    When the extension IS configured, returns None so agents don't
    see a "your extension is fine, by the way" hint on every call.
    """
    global _EXTENSION_HINT_CACHED
    if _EXTENSION_HINT_CACHED is not _NOT_COMPUTED:
        return _EXTENSION_HINT_CACHED  # type: ignore[return-value]

    try:
        from .launchpad_data import dispatch_readiness

        readiness = dispatch_readiness()
    except Exception:
        _EXTENSION_HINT_CACHED = None
        return None

    if readiness.get("extension_configured"):
        _EXTENSION_HINT_CACHED = None
        return None

    _EXTENSION_HINT_CACHED = {
        "configured": False,
        "message": (
            "Chrome extension not wired — install it for browser "
            "capture (claude.ai / chatgpt.com / gemini.google.com) + "
            "Web Store auto-update."
        ),
        "install_doc": "https://github.com/keepwhatworks/trinity/blob/main/docs/INSTALL-extension.md",
    }
    return _EXTENSION_HINT_CACHED


def _dispatch_via_config(provider_name: str, prompt: str) -> str:
    """Production dispatch shim used by `ask`. Looks up the named provider in
    config first; if not found, falls through to detected Ollama models
    (provider_name="ollama:<model>"). Errors raise; the MCP handler catches
    them and returns an error response so Claude in the harness knows the
    route failed and can retry / replan.
    """
    from pathlib import Path

    from .config import load_config
    from .providers import make_provider, ProviderError

    config = load_config()
    # config.providers is a dict keyed by name; iterate values for ProviderConfig
    # objects with .enabled and .name attributes.
    for p in config.providers.values():
        if p.name == provider_name and p.enabled:
            provider = make_provider(p)
            result = provider.run(prompt, Path.cwd())
            if result.returncode != 0:
                raise ProviderError(f"{provider_name} exit {result.returncode}: {result.stderr[:200]}")
            return result.stdout

    # Fall through: maybe this is a detected local model, e.g. "ollama:qwen3:32b".
    if provider_name.startswith("ollama:"):
        return _dispatch_to_ollama_model(provider_name, prompt)

    raise ProviderError(f"Provider not configured or not enabled: {provider_name}")


def _dispatch_to_ollama_model(provider_name: str, prompt: str) -> str:
    """Build an ephemeral ProviderConfig for a detected Ollama model and run.
    `provider_name` shape is `ollama:<model_name>` (the LocalModel.provider_name
    stable identifier from local_models.py)."""
    from pathlib import Path
    from .config import ProviderConfig
    from .providers import OllamaProvider, ProviderError

    model = provider_name[len("ollama:"):]
    cfg = ProviderConfig(
        name=provider_name,
        type="ollama",
        enabled=True,
        label=f"Ollama {model}",
        command=["ollama"],
        args=[],
        task_types=set(),
        model=model,
    )
    provider = OllamaProvider(cfg)
    result = provider.run(prompt, Path.cwd())
    if result.returncode != 0:
        raise ProviderError(f"{provider_name} exit {result.returncode}: {result.stderr[:200]}")
    return result.stdout


def _trigger_incremental_ingest() -> None:
    """Fire-and-forget: scan transcripts newer than the per-source memory
    cursor and append fresh ``PromptNode``s. Runs at the start of ``ask``
    so MCP-driven flows pick up new conversations without a manual
    ``import-export`` rerun (or its retired predecessor seed-from-taste-
    terminal). Bounded at 1s so it cannot dominate user-facing latency;
    errors are swallowed so a parser breakage cannot take down the tool
    surface.
    """
    try:
        from .incremental_ingest import ingest_recent

        ingest_recent(deadline_s=1.0)
    except Exception:
        return


def _full_provider_pool() -> list[str]:
    """Build the available-provider pool: enabled config providers + detected
    local Ollama models, with currently-unhealthy providers demoted to the
    end. The Conductor / ask uses this when the caller doesn't pass an
    explicit `available_providers` list.

    Demotion (not exclusion) preserves the option for routing to fall back to
    an unhealthy provider when nothing else fits, while keeping it from being
    the first choice. Unhealthy = recent rate-limit / billing / auth failure
    within the decay window — see dispatch_health.py.
    """
    from .config import load_config
    from .dispatch_health import unhealthy_providers
    from .local_models import detect_local_models

    pool: list[str] = []
    try:
        config = load_config()
        for p in config.providers.values():
            if p.enabled:
                pool.append(p.name)
    except Exception:
        pass
    try:
        for m in detect_local_models():
            pool.append(m.provider_name)
    except Exception:
        pass

    # Demote unhealthy providers to the end (preserve relative order otherwise).
    try:
        unhealthy = unhealthy_providers()
    except Exception:
        unhealthy = set()
    if unhealthy:
        healthy = [p for p in pool if p not in unhealthy]
        sick = [p for p in pool if p in unhealthy]
        pool = healthy + sick

    return pool


async def _ask(args: dict) -> list[Any]:
    """Handle mcp__trinity-local__ask. Routes via kNN + dispatches once.
    See `src/trinity_local/ask.py` for orchestration logic.

    Q4 surface-collapse (#213): `route` merged into `ask` as a mode.
    `mode="route"` returns the routing decision (which model, council-or-not,
    confidence) WITHOUT dispatching a call — the old `route` tool's job. The
    standalone `route` tool was removed 2026-06-08 (loop-primitive surface cut);
    `ask(mode="route")` is the one routing entrypoint.
    """
    from .ask import run_ask

    query = args.get("query")
    if not query or not isinstance(query, str):
        return [ErrorData(code=400, message="`query` is required and must be a string")]

    mode = (args.get("mode") or "answer").lower()
    if mode == "route":
        # Delegate to the routing path: query→task, available_providers→
        # available_models; pass the routing hints through unchanged.
        route_args: dict = {"task": query}
        if "available_providers" in args:
            route_args["available_models"] = args.get("available_providers")
        for k in ("harness", "budget", "latency", "current_provider"):
            if args.get(k) is not None:
                route_args[k] = args[k]
        return await _route(route_args)

    _trigger_incremental_ingest()

    available = args.get("available_providers")
    if available is not None and not isinstance(available, list):
        return [ErrorData(code=400, message="`available_providers` must be a list of provider names")]
    # When caller doesn't specify available_providers, default to the full
    # pool (config providers + detected local models). This is what makes
    # ask aware of Ollama / MLX without each call having to declare them.
    if available is None:
        available = _full_provider_pool()

    top_k = int(args.get("top_k", 5))

    try:
        result = run_ask(
            query,
            dispatch_fn=_dispatch_via_config,
            top_k=top_k,
            available_providers=available,
        )
    except Exception as exc:
        # 100-persona audit D7: structured error shape lets the agent
        # auto-retry around rate limits without parsing a free-form
        # string. Detect the failure kind from the exception message
        # so {error_code, recoverable, retry_with} can drive recovery.
        from .dispatch_errors import classify_dispatch_failure
        exc_text = str(exc)
        try:
            failure = classify_dispatch_failure(
                provider=available[0] if available else "unknown",
                returncode=getattr(exc, "returncode", 1),
                stderr=exc_text,
            )
            failure_kind = failure.kind.value
            recoverable = failure.retry_with_other_provider
        except Exception:
            failure_kind = "unknown"
            recoverable = True
        # Suggest the remaining pool minus the failing provider so the
        # agent can immediately retry around it (the rate-limit-dodge
        # wedge in one hop).
        failing_provider = available[0] if available else None
        retry_pool = [p for p in available if p != failing_provider] if available else []
        return [_text({
            "ok": False,
            "error_code": {
                "rate_limited": "RATE_LIMITED",
                "billing_exceeded": "BILLING_EXCEEDED",
                "auth_failed": "AUTH_FAILED",
                "model_not_found": "MODEL_NOT_FOUND",
                "timeout": "TIMEOUT",
            }.get(failure_kind, "DISPATCH_FAILED"),
            "provider": failing_provider,
            "recoverable": bool(recoverable and retry_pool),
            "retry_with": {"available_providers": retry_pool} if retry_pool else None,
            "user_message": (
                f"{failing_provider} {failure_kind.replace('_', ' ')}; "
                f"try {retry_pool[0]} next" if retry_pool else
                f"All providers failed ({failure_kind})"
            ),
            "detail": exc_text[:240],
        })]

    payload = result.to_dict()
    # Make the success/failure shape symmetric: the failure branch above
    # returns {"ok": False, error_code, ...}; the success branch needs
    # ok=True so an agent doing `if response.get("ok"): proceed` works
    # uniformly. Was asymmetric — happy path returned the raw answer
    # dict with no ok key, agent's natural check treated success as
    # failure.
    payload["ok"] = True
    # pending_ratings hint retired 2026-05-21 alongside record_outcome.
    return [_text(payload)]


async def _get_picks(args: dict) -> list[Any]:
    """Handle mcp__trinity-local__get_picks. Returns the user's lens-derived
    routing picks (the per-lens-basin chairman-winner tally) so the calling agent
    can inspect which provider wins for which kind of question. Post-collapse
    (#298) schema: `{winner, count, margin, n_episodes, evidence}` per basin.
    """
    from .cortex import load_routing_patterns

    # The real routing gate: ask() only ROUTES on a basin whose margin clears this;
    # below it the winner is a near-tie and ask falls to kNN. Surfaced per-pick as
    # `routes` so an agent reading get_picks can tell a confident pick from a
    # coin-flip (the agent-facing analog of the launchpad #299 / memory-viewer
    # demote) instead of treating a margin-0.08 tally as a firm "use X" rule.
    winner_margin_floor = _winner_margin_floor()

    basin_id = args.get("basin_id")
    if basin_id is not None and not isinstance(basin_id, str):
        return [ErrorData(code=400, message="`basin_id` must be a string when provided")]
    try:
        min_trust = float(args.get("min_trust", 0.0))
    except (TypeError, ValueError):
        return [ErrorData(code=400, message="`min_trust` must be numeric")]

    patterns = load_routing_patterns()
    if not patterns:
        return [_text({"rules": {}, "note": "No cortex consolidation yet. Run `trinity-local consolidate`."})]

    # POST-COLLAPSE (#298): each pick is the flat lens-basin tally
    # `{winner, count, margin, n_episodes, evidence}` — no 768-dim centroid, no
    # trust score, no internal geometry to strip. `min_trust` filters on
    # `margin` (the new confidence proxy: how decisively the winner beat the
    # runner-up). A legacy/malformed entry (a dict missing `winner`) is skipped.
    filtered: dict[str, dict] = {}
    for bid, pick in patterns.items():
        if basin_id is not None and bid != basin_id:
            continue
        if not isinstance(pick, dict):
            continue
        # isinstance(..., str) shape-guards the STRING field: a corrupt non-string
        # `winner` (a NUMBER in a hand-edited picks.json) would hit `.strip()` on an
        # int and crash the `get_picks` tool (the launchpad-render sibling, Iter 257).
        winner_raw = pick.get("winner")
        winner = winner_raw.strip() if isinstance(winner_raw, str) else ""
        if not winner:
            continue  # legacy RoutingPattern dict (or junk) — not a live pick
        try:
            margin = float(pick.get("margin", 0.0) or 0.0)
        except (TypeError, ValueError):
            margin = 0.0
        if margin < min_trust:
            continue
        filtered[bid] = {
            "winner": winner,
            "count": int(pick.get("count", 0) or 0),
            "margin": round(margin, 3),
            # Does ask() route on this basin, or is it a near-tie that falls to kNN?
            # `False` means "leans winner, but don't treat it as a firm rule."
            "routes": margin >= winner_margin_floor,
            "n_episodes": int(pick.get("n_episodes", pick.get("count", 0)) or 0),
            "evidence": list(pick.get("evidence") or [])[:20],
        }

    routed = sum(1 for p in filtered.values() if p["routes"])
    return [_text({
        "rules": filtered,
        "total_basins": len(patterns),
        "returned": len(filtered),
        # The gate ask() routes on. Picks with margin < this are advisory near-ties
        # (routes=False); ask falls to kNN for them. Don't act on them as firm rules.
        "winner_margin_floor": round(winner_margin_floor, 3),
        "routed": routed,
    })]


async def _route(args: dict) -> list[Any]:
    task = args["task"]
    # Distinguish absent from explicitly empty. `available_models=[]` is the
    # caller signaling "no providers are available" — we should error rather
    # than silently picking from defaults.
    if "available_models" in args:
        available = args.get("available_models")
        if not isinstance(available, list):
            return [_text({"ok": False, "error": "`available_models` must be a list of provider names"})]
        if not available:
            return [_text({"ok": False, "error": "`available_models` is empty — no providers to route to"})]
    else:
        from .config import default_council_members
        available = default_council_members()
    current_provider = args.get("current_provider") or available[0]
    budget_pref = (args.get("budget") or "normal").lower()
    latency_pref = (args.get("latency") or "normal").lower()

    from .ranker import prompt_calls_for_council
    from .task_types import guess_task_type, is_polish_task

    chairman_pick = chairman_pick_reason(task, available_providers=available)
    task_type = chairman_pick.get("task_type") or guess_task_type(task)
    polish = is_polish_task(task)

    # Cortex-first primary (#277 parity): when a learned basin rule matches
    # this query with sufficient trust, it IS the routing decision — the
    # heuristic chairman-pick becomes the fallback for queries with no basin.
    # Without this, ask(mode="route") — the routing call the MCP docs tell
    # users to PREFER — silently bypassed the learned basins that answer-mode
    # ask already routes through (run_ask → _route_query → _try_cortex_route).
    # _try_cortex_route is fully self-gating: it returns None unless a basin
    # matches (exact or centroid≥floor), trust ≥ fallback floor, the
    # basin isn't bimodal, and the primary is available — so a non-None result
    # is always a confident learned route, and the no-MLX path returns None
    # (centroid fingerprint mismatch) and stays on the heuristic with no regression.
    cortex_primary = None
    cortex_reason = ""
    cortex_challenger = None
    try:
        from .ask import _try_cortex_route
        _cortex_dec = _try_cortex_route(task, available)
        if _cortex_dec is not None:
            cortex_primary = _cortex_dec.routed_to
            cortex_reason = _cortex_dec.reason
            cortex_challenger = _cortex_dec.runner_up
    except Exception:
        cortex_primary = None

    decision = None
    try:
        ranker = build_default_ranker()
        decision = ranker.advise(RoutingContext(
            task_text=task,
            task_type=task_type,
            current_provider=current_provider,
            session_id="mcp_route",
            metadata={"budget": budget_pref, "latency": latency_pref},
        ))
    except Exception:
        decision = None

    # Map RoutingDecision → MCP shape. The ranker exposes `needs_council`
    # (bool) and `top_k` (ordered providers); the spec promises `mode` and
    # `challenger`. Without this mapping, route() would silently return
    # mode="single" for every task — defeating "council on disagreement."
    decision_needs_council = bool(getattr(decision, "needs_council", False))
    decision_top_k = list(getattr(decision, "top_k", []) or [])
    decision_confidence_raw = getattr(decision, "confidence", None)
    decision_evidence = getattr(decision, "evidence", []) or []

    base_mode = "council" if decision_needs_council else "single"
    base_confidence = _confidence_band(decision_confidence_raw)
    if cortex_reason:
        base_reason = cortex_reason
    elif decision_evidence:
        base_reason = "; ".join(decision_evidence[:2])
    else:
        base_reason = f"chairman picked from {chairman_pick.get('source', 'default')}"

    # Latency-aware demotion: when the caller asked for latency='fast', the
    # strongest-on-quality provider (codex+gpt-5.5 xhigh) is the wrong pick —
    # it takes 30s+. Prefer claude/antigravity if either is available.
    fast_demoted = False
    # cortex_primary (the learned basin rule) wins over the heuristic chairman
    # pick; the chairman pick is the fallback when no basin matched. Threading
    # this here (not just the reason/challenger) is what keeps primary and
    # reason consistent — otherwise the reason cites a cortex basin while the
    # primary stays the heuristic pick (a reason/primary mismatch).
    primary_pick = cortex_primary or chairman_pick.get("chairman") or available[0]
    if latency_pref == "fast" and primary_pick == "codex":
        fast_alt = next((p for p in ("claude", "antigravity") if p in available), None)
        if fast_alt:
            primary_pick = fast_alt
            fast_demoted = True

    # Prompt-shape escalation: if the task literally contains "A) ... B) ..."
    # numbered alternatives, "vs.", "which is best", "tradeoffs", etc., the
    # user is asking for a comparison — escalate to mode=council regardless
    # of what task_type says. Single-answer routing on a multi-candidate
    # prompt under-recommends council and starves the personal routing table.
    council_signals: list[str] = []
    escalate, council_signals = prompt_calls_for_council(task)
    if escalate:
        mode = "council"
        confidence = "high"
        reason = f"prompt shape suggests comparison: {', '.join(council_signals)}"
    else:
        mode = base_mode
        confidence = base_confidence
        reason = base_reason

    # Challenger: ranker top_k[1] when distinct from primary AND in the
    # caller-supplied available_models. Pre-fix, the ranker could return
    # `challenger="codex"` even when `available_models=["claude"]` — useless
    # advice. Always filter through `available`.
    if cortex_challenger and cortex_challenger != primary_pick and cortex_challenger in available:
        # The learned basin rule's challenger — prefer it over the kNN runner-up
        # when cortex drove the primary, so the pair stays internally consistent.
        challenger = cortex_challenger
    else:
        challenger = next(
            (p for p in decision_top_k if p != primary_pick and p in available),
            next((p for p in available if p != primary_pick), None),
        )
    # If only one provider is actually available, force mode="single" — there
    # IS no second opinion to be had, so reporting mode="council" is a lie.
    if len(available) < 2:
        mode = "single"
        challenger = None

    if fast_demoted:
        reason = f"{reason}; latency=fast → demoted codex"

    payload = {
        "mode": mode,
        "primary": primary_pick,
        "challenger": challenger,
        "confidence": confidence,
        "reason": reason,
        "task_type": task_type,
        "chairman_source": "cortex" if cortex_primary else chairman_pick.get("source", "default_order"),
        "shape_signals": council_signals,
        "budget": budget_pref,
        "latency": latency_pref,
        "should_auto_council": mode == "council",
        # Polish-shape tasks ("make this better", "tighten this", ≤20 words
        # + "shorter"/"simpler"/etc.) benefit from consensus_round iteration
        # — the first pass catches the obvious; the value is in rounds 2-3
        # where each model refines against the others' outputs. Surfaced
        # here so harnesses + the launchpad can OFFER auto-iterate without
        # changing default behavior.
        "auto_iterate_recommended": polish,
    }
    # pending_ratings hint retired 2026-05-21 alongside record_outcome.
    return [_text(payload)]


def _confidence_band(raw) -> str:
    """Normalize a 0..1 ranker confidence (or string) into 'high'/'medium'/'low'.

    The MCP `route()` schema declares confidence as a string enum, but
    `RoutingDecision.confidence` is a 0..1 float. Without this normalizer the
    payload leaked floats like 0.72 into the contract.
    """
    if isinstance(raw, str):
        if raw in ("high", "medium", "low"):
            return raw
        return "medium"
    # bool subclasses int — `float(True) == 1.0` would silently coerce a
    # malformed bool confidence into "high". Reject explicitly.
    if isinstance(raw, bool):
        return "medium"
    try:
        f = float(raw) if raw is not None else 0.5
    except (TypeError, ValueError):
        f = 0.5
    if f >= 0.75:
        return "high"
    if f >= 0.55:
        return "medium"
    return "low"


async def _synthesize_responses(args: dict, responses: list[dict]) -> list[Any]:
    """Chairman-only synthesis over pre-supplied member responses.

    Equivalent to running a council where members already executed; we skip
    the dispatch and feed the chairman directly. One model call (chairman)
    instead of N+1. Returns the structured Routing JSON inline.
    """
    from .council_runtime import (
        create_council_outcome,
        create_prompt_bundle,
        parse_routing_label,
        render_primary_council_prompt,
        save_council_outcome,
        save_prompt_bundle,
    )
    from .council_schema import CouncilMemberResult, normalize_provider_slug
    from .providers import make_provider
    from .utils import stable_id

    task = args["task"]
    # Fold web-era brand labels (chatgpt→codex, claude_ai→claude, gemini→
    # antigravity) on each response so the winner attribution written to the
    # council outcome — which the personal routing table reads — never records a
    # non-dispatchable slug (the #249/#260 routing-poison class). Arbitrary
    # labels ('answer_a', 'external') pass through untouched.
    members = [
        CouncilMemberResult(
            provider=normalize_provider_slug(str(r.get("provider", "unknown"))),
            model=r.get("model"),
            output_text=str(r.get("content", "")),
            metadata={"source": "mcp_synthesis"},
        )
        for r in responses
    ]

    # Pick the chairman from ENABLED LOCAL providers — not from the
    # caller-supplied response provider labels. The labels can be arbitrary
    # ("answer_a", "external", "judge_v2") and don't have to match a Trinity
    # provider config. Use the labels only for display/scoring; chair from
    # the user's actual provider lineup.
    config = load_config()
    bundle = create_prompt_bundle(
        task_cluster_id=stable_id("cluster", "mcp_synthesis", task[:400]),
        task_text=task,
        goal=args.get("goal") or "Synthesize the strongest answer from these responses.",
        origin_provider="mcp_run_council",
    )
    save_prompt_bundle(bundle)

    # Chairman-on-host (the full provider-side loop): the host session — a Claude instance on
    # the user's FULL plan — already produced the verdict, so there is NO chairman model call
    # (no `claude -p`, no deprecated sampling). Record it directly; attribute the synthesis to
    # the host's lab (claude, or a caller-supplied primary_provider) for the routing table.
    host_synthesis = args.get("host_synthesis")
    if host_synthesis:
        _arg = args.get("primary_provider")
        chairman = normalize_provider_slug(_arg) if isinstance(_arg, str) and _arg else "claude"
        chairman_config = config.providers.get(chairman) if config else None
        synthesis_prompt = "(host-synthesized — chairman ran on the host session)"
        synthesis_output = str(host_synthesis)
    else:
        enabled = [
            name for name, p in (config.providers if config else {}).items()
            if p.enabled and p.type in ("cli", "codex")
        ] or ["claude"]
        # Canonicalize a caller-supplied chairman so `primary_provider='chatgpt'` resolves to
        # the dispatchable `codex` config instead of erroring out as "missing or disabled".
        _chairman_arg = args.get("primary_provider")
        if isinstance(_chairman_arg, str) and _chairman_arg:
            _chairman_arg = normalize_provider_slug(_chairman_arg)
        chairman = _chairman_arg or predict_strongest_chairman(
            task, available_providers=enabled
        )
        chairman_config = config.providers.get(chairman) if chairman in (config.providers if config else {}) else None
        if chairman_config is None or not chairman_config.enabled:
            return [_text({
                "ok": False,
                "error": f"Chairman provider '{chairman}' missing or disabled in Trinity config",
            })]
        synthesis_prompt = render_primary_council_prompt(bundle, members)
        primary = make_provider(chairman_config)
        try:
            # cwd is required by the runtime (subprocess.run cwd= can't be None)
            from pathlib import Path
            result = primary.run(synthesis_prompt, cwd=Path.cwd())
        except Exception as exc:
            return [_text({"ok": False, "error": f"Chairman call failed: {exc}"})]
        synthesis_output = result.stdout or result.stderr or ""

    routing_label, parse_error = parse_routing_label(synthesis_output)

    # Surface the chairman's verdict on the outcome itself, not just inside
    # the routing_label. Without `winner_provider`, downstream consumers
    # (personal_routing aggregation) can't tell who won.
    winner_from_label = getattr(routing_label, "winner", None) if routing_label else None
    _mode = "host_synthesis" if host_synthesis else "synthesis_only"
    outcome_metadata: dict = {"mode": _mode}
    if parse_error:
        outcome_metadata["routing_label_error"] = parse_error
    outcome = create_council_outcome(
        bundle=bundle,
        primary_provider=chairman,
        member_results=members,
        primary_model=chairman_config.model if chairman_config else None,
        synthesis_output=synthesis_output,
        synthesis_prompt=synthesis_prompt,
        routing_label=routing_label,
        winner_provider=winner_from_label,
        metadata=outcome_metadata,
    )
    outcome_path = save_council_outcome(outcome)

    payload: dict = {
        "ok": True,
        "council_run_id": outcome.council_run_id,
        "mode": _mode,
        "synthesis_output": synthesis_output,
        "_local_paths": {"outcome_path": str(outcome_path)},
    }
    if routing_label:
        payload["winner"] = routing_label.winner
        payload["runner_up"] = routing_label.runner_up
        payload["confidence"] = routing_label.confidence
        payload["agreed_claims"] = routing_label.agreed_claims
        payload["disagreed_claims"] = routing_label.disagreed_claims
        payload["routing_lesson"] = routing_label.routing_lesson
        payload["eval_seed"] = routing_label.eval_seed
    elif parse_error:
        payload["routing_label_error"] = parse_error
    return [_text(payload)]


def _host_member_council_enabled() -> bool:
    """The provider-side loop is flag-gated OFF by default — the deprecation of MCP sampling
    (in favor of direct provider APIs, which Trinity's no-API-key thesis forbids) is on a 12+
    month window, so this path is shipped behind a flag and proven before it becomes default."""
    import os
    return os.environ.get("TRINITY_HOST_CLAUDE_MEMBER", "").strip().lower() in ("1", "true", "yes", "on")


def _members_to_dispatch(members: list, host_responses: list[dict]) -> list:
    """The members Trinity must dispatch via CLI = the council lineup MINUS the ones the host
    agent already supplied (matched on canonical slug). Order-preserving, slug-deduped."""
    from .council_schema import normalize_provider_slug

    host = {normalize_provider_slug(str(r.get("provider", ""))) for r in host_responses}
    out: list = []
    seen: set[str] = set()
    for m in members:
        slug = normalize_provider_slug(str(m))
        if slug in host or slug in seen:
            continue
        seen.add(slug)
        out.append(m)
    return out


async def _council_with_host_members(args: dict, host_responses: list[dict]) -> list[Any]:
    """Provider-side loop (the MCP-sampling-deprecation-proof path for the Claude voice).

    The host agent already produced some member answers in-session — typically the Claude
    voice on the user's FULL plan, so it never touches `claude -p` (the smaller post-2026-06-15
    Agent-SDK credit pool) or `sampling/createMessage` (deprecated). Trinity dispatches ONLY
    the remaining members via their CLIs (codex/agy — no billing problem) and reuses the
    chairman-synthesis path over the combined set. The Claude member's cognition rides MCP
    *tools* (this call), not server-initiated sampling.
    """
    from pathlib import Path

    from .config import default_council_members, load_config
    from .council_runtime import create_prompt_bundle, render_member_prompt
    from .council_schema import normalize_provider_slug
    from .providers import make_provider
    from .utils import stable_id

    task = args["task"]
    config = load_config()
    members = args.get("members") or default_council_members()
    to_dispatch = _members_to_dispatch(members, host_responses)

    bundle = create_prompt_bundle(
        task_cluster_id=stable_id("cluster", "mcp_host_council", task[:400]),
        task_text=task,
        goal=args.get("goal") or "Find the strongest answer.",
        origin_provider="mcp_host_council",
    )
    member_prompt = render_member_prompt(bundle)
    dispatched: list[dict] = []
    for m in to_dispatch:
        slug = normalize_provider_slug(str(m))
        cfg = (config.providers if config else {}).get(slug)
        if cfg is None or not cfg.enabled:
            continue  # not installed/enabled — degrade, don't fail the council
        try:
            res = make_provider(cfg).run(member_prompt, cwd=Path.cwd())
            dispatched.append({
                "provider": m,
                "model": cfg.model,
                "content": (res.stdout or res.stderr or ""),
            })
        except Exception:
            continue  # a dead member must never break the council (#22 best-effort)

    # Host-supplied first (the Claude voice), then the CLI-dispatched rest.
    all_responses = list(host_responses) + dispatched

    # dispatch_only (chairman-on-host): hand the assembled member answers + the synthesis
    # prompt BACK to the host, which produces the verdict in its own turn (full plan, no
    # `claude -p`) and records it via run_council(responses=<these>, host_synthesis=<verdict>).
    # No chairman model call here.
    if args.get("dispatch_only"):
        from .council_runtime import render_primary_council_prompt
        from .council_schema import CouncilMemberResult

        member_results = [
            CouncilMemberResult(
                provider=normalize_provider_slug(str(r.get("provider", "unknown"))),
                model=r.get("model"),
                output_text=str(r.get("content", "")),
                metadata={"source": "host_council"},
            )
            for r in all_responses
        ]
        synthesis_prompt = render_primary_council_prompt(bundle, member_results)
        return [_text({
            "ok": True,
            "mode": "awaiting_host_synthesis",
            "member_responses": all_responses,
            "synthesis_prompt": synthesis_prompt,
            "next": "Synthesize this prompt yourself (full plan, no `claude -p`), then call "
                    "run_council(task, responses=<member_responses>, host_synthesis=<your Routing "
                    "JSON verdict>) to record the outcome with zero chairman model calls.",
        })]

    # Default: Trinity synthesizes over all members (the chairman MAY be claude → `-p`).
    return await _synthesize_responses(args, all_responses)


async def _run_council(args: dict) -> list[Any]:
    # Chairman-on-host (the recording leg of the provider-side loop): the host already
    # synthesized the verdict in-session, so there are zero chairman model calls. Same flag
    # gate as host_responses — refuse it when the loop is disabled rather than silently
    # recording a host verdict that the rest of the council never opted into.
    if args.get("host_synthesis") and not _host_member_council_enabled():
        return [_text({
            "ok": False,
            "error": "chairman-on-host is flag-gated OFF — set TRINITY_HOST_CLAUDE_MEMBER=1 "
                     "to enable the provider-side loop, or omit host_synthesis for a normal council.",
        })]

    # Provider-side loop: the host agent supplied some member answers (the Claude voice on the
    # user's full plan); Trinity dispatches only the rest + synthesizes. Flag-gated OFF.
    if "host_responses" in args:
        host_responses = args.get("host_responses")
        if not isinstance(host_responses, list) or not host_responses:
            return [_text({"ok": False, "error": "`host_responses` must be a non-empty list of {provider, content} objects"})]
        for i, r in enumerate(host_responses):
            if not isinstance(r, dict) or "content" not in r or "provider" not in r:
                return [_text({"ok": False, "error": f"`host_responses[{i}]` must contain 'provider' and 'content' fields"})]
        if not _host_member_council_enabled():
            return [_text({
                "ok": False,
                "error": "host-member council is flag-gated OFF — set TRINITY_HOST_CLAUDE_MEMBER=1 "
                         "to enable the provider-side loop, or omit host_responses for a normal council.",
            })]
        return await _council_with_host_members(args, host_responses)

    # Pre-supplied responses → chairman synthesis only. One model call instead
    # of N+1. Same outcome shape (structured Routing JSON), persisted as
    # a CouncilOutcome the personal routing table reads from.
    #
    # Distinguish "absent" from "explicitly empty": passing responses=[] is a
    # caller error (they intended to invoke the synthesis path with N candidates
    # and ended up with zero). Reject loudly rather than silently launching a
    # full provider council on an empty list.
    if "responses" in args:
        responses = args.get("responses")
        if not isinstance(responses, list):
            return [_text({"ok": False, "error": "`responses` must be a list of {provider, content} objects"})]
        if not responses:
            return [_text({"ok": False, "error": "`responses` is empty — pass at least one {provider, content} entry"})]
        # Validate each entry has required fields before any dispatch
        for i, r in enumerate(responses):
            if not isinstance(r, dict) or "content" not in r or "provider" not in r:
                return [_text({"ok": False, "error": f"`responses[{i}]` must contain 'provider' and 'content' fields"})]
        return await _synthesize_responses(args, responses)

    from .commands.council import handle_council_launch
    from types import SimpleNamespace
    import asyncio
    import contextlib
    import io
    import time

    task = args["task"]
    goal = args.get("goal", "Find the strongest answer.")
    from .config import default_council_members
    from .council_schema import normalize_provider_slug
    members = args.get("members") or default_council_members()
    mode = args.get("mode", "parallel")
    sequence = args.get("sequence")
    primary_provider = args.get("primary_provider")
    wait_seconds = float(args.get("wait_seconds") or 0)

    # Fold web-era brand slugs to dispatchable CLI slugs at the launch boundary
    # so an agent that read a stale `get_picks primary='chatgpt'` (or any
    # provider-imported / copy-pasted brand name) doesn't silently lose that
    # member / chairman to `config.providers.get('chatgpt') -> None`.
    if isinstance(members, list):
        members = _canonicalize_member_slugs(members)
    if isinstance(sequence, list):
        # dedupe=False: a chain sequence legitimately revisits a provider across
        # rounds (claude→codex→claude), so fold slugs in place without collapsing.
        sequence = _canonicalize_member_slugs(sequence, dedupe=False)
    if isinstance(primary_provider, str) and primary_provider:
        primary_provider = normalize_provider_slug(primary_provider)

    if mode not in ("parallel", "chain"):
        return [_text({"ok": False, "error": f"unknown mode: {mode}"})]

    launch_args = SimpleNamespace(
        config=None,
        task=task,
        goal=goal,
        instructions="Prefer the strongest answer for the user's current task.",
        context_file=None,
        project_hint="",
        members=members if mode == "parallel" else (sequence or members),
        primary_provider=primary_provider,
        # CRITICAL: thread mode + sequence into launch_args so handle_council_launch
        # can propagate them to handle_council_start → run_council. Without these,
        # MCP `run_council(mode="chain")` was reaching the runner as parallel
        # while the response said "mode": "chain" — the silent-dispatch bug
        # the verification council caught.
        mode=mode,
        sequence=sequence,
        cwd=".",
        status_token=None,
        open_browser=False,
    )

    # handle_council_launch prints a JSON record with both the council_run_id
    # (what MCP callers need) and several local filesystem paths (launchpad
    # implementation detail). Capture, parse, and project to a clean shape.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            handle_council_launch(launch_args)
    except SystemExit as exc:
        return [_text({"ok": False, "error": f"council exited: {exc}"})]

    captured = buf.getvalue().strip()
    raw: dict = {}
    if captured:
        try:
            raw = json.loads(captured)
        except json.JSONDecodeError:
            return [_text({"ok": False, "error": "council launch produced unparseable output", "raw": captured})]

    if not isinstance(raw, dict):
        return [_text({"ok": False, "error": "council launch produced a non-object", "raw": captured})]
    council_run_id = raw.get("council_run_id")
    if not council_run_id:
        return [_text({"ok": False, "error": "council launch did not return a council_run_id", "raw": raw})]

    response: dict = {
        "ok": True,
        "council_run_id": council_run_id,
        "mode": mode,
        # Local filesystem artifacts — useful for the CLI / launchpad, opaque
        # to MCP callers. Nested under `_local_paths` so harnesses can ignore.
        "_local_paths": {
            "task_path": raw.get("task_path"),
            "sync_path": raw.get("sync_path"),
            "review_path": raw.get("review_path"),
            "review_action_path": raw.get("review_action_path"),
        },
    }

    # MCP logging + progress (best-effort; no-op without a capable client).
    from .mcp_features import mcp_log, mcp_progress

    mcp_log("info", f"Council {council_run_id} launched ({mode})")
    mcp_progress(0.05, 1.0, message="council dispatched")

    # Optional inline-wait. Polls the status file every 750ms until either
    # the council reports completed/failed/canceled, or the budget expires.
    if wait_seconds > 0:
        from .council_runtime import load_council_outcome
        from .state_paths import council_outcomes_dir

        deadline = time.monotonic() + wait_seconds
        completed_status: dict | None = None
        while time.monotonic() < deadline:
            # Stream progress as a fraction of the wait budget elapsed, so the
            # harness can render a live bar while members deliberate. Monotonic
            # by construction; capped below 1.0 until the council resolves.
            elapsed = wait_seconds - (deadline - time.monotonic())
            mcp_progress(
                min(0.95, 0.05 + 0.9 * (elapsed / wait_seconds)),
                1.0,
                message="council deliberating",
            )
            # Use the same lookup-with-fallback logic as `_get_council_status`:
            # the live status file is keyed by status token (often the
            # bundle_id, not the council_run_id). Without the fallback scan,
            # wait_seconds could time out on a council that already completed.
            status_payload = _lookup_council_status(council_run_id)
            current = (status_payload or {}).get("status")
            if current in ("completed", "failed", "canceled"):
                completed_status = status_payload
                break
            # Belt-and-suspenders: a completed outcome JSON also resolves the
            # wait, even if the live status file lags or never got written.
            outcome_path = council_outcomes_dir() / f"{council_run_id}.json"
            if outcome_path.exists():
                completed_status = status_payload or {"status": "completed"}
                break
            await asyncio.sleep(0.75)

        if completed_status is not None:
            outcome_summary = None
            outcome_path = council_outcomes_dir() / f"{council_run_id}.json"
            if outcome_path.exists():
                try:
                    outcome = load_council_outcome(council_run_id)
                    label = outcome.routing_label
                    outcome_summary = {
                        "winner": outcome.winner_provider,
                        "primary_provider": outcome.primary_provider,
                        "primary_model": outcome.primary_model,
                        "synthesis_output": outcome.synthesis_output,
                        "agreed_claims": list(getattr(label, "agreed_claims", []) or []) if label else [],
                        "disagreed_claims": list(getattr(label, "disagreed_claims", []) or []) if label else [],
                        "routing_lesson": getattr(label, "routing_lesson", "") if label else "",
                        "user_likely_values": list(getattr(label, "user_likely_values", []) or []) if label else [],
                    }
                except Exception:
                    outcome_summary = None
            response["status"] = completed_status.get("status")
            response["outcome"] = outcome_summary
            mcp_progress(1.0, 1.0, message="council complete")
            winner = (outcome_summary or {}).get("winner") if outcome_summary else None
            mcp_log(
                "info",
                f"Council {council_run_id} {completed_status.get('status')}"
                + (f" — winner {winner}" if winner else ""),
            )
            # rate_action injection retired 2026-05-21 alongside record_outcome.
        else:
            response["status"] = "running"
            response["timed_out_after_seconds"] = wait_seconds
            mcp_log("warning", f"Council {council_run_id} still running after {wait_seconds}s")

    return [_text(response)]


# _record_outcome handler removed 2026-05-21 per user direction
# "we are sunsetting user ratings. Full retirement including MCP."
# Chairman's pick is the supervision signal (commit bb817b6).
# Registry entry: src/trinity_local/retired_names.py.


async def _get_persona(args: dict) -> list[Any]:
    from .me_builder import load_me, me_path
    from .config import trinity_home

    text = load_me()
    home = trinity_home()
    path = me_path()
    # Symbolic relative path so consumers don't bake user-specific absolutes
    # into their state. The harness can still use `path` if it has fs access.
    try:
        relative_path = "$TRINITY_HOME/" + str(path.relative_to(home))
    except ValueError:
        relative_path = str(path)
    return [_text({
        "path": str(path),
        "trinity_home_relative": relative_path,
        "size_chars": len(text),
        "text": text,
        "available": bool(text),
    })]


async def _lens_generators(args: dict) -> list[Any]:
    """Run the generators pass in-session (samples via #263 — pollution-free) and
    write generators.md. The sync build runs OFF the event loop via
    ``asyncio.to_thread``, which copies the current contextvars Context so the
    active sampling session (set in handle_call_tool) propagates to the worker —
    request_claude_sample then bridges back to the loop. Returns the structured
    generators."""
    import asyncio

    from .me.generators import build_generators
    from .state_paths import generators_path

    result = await asyncio.to_thread(build_generators)
    if not result.get("ok"):
        return [_text({
            "ok": False,
            "reason": result.get("reason"),
            "evidence_turns": result.get("evidence_turns"),
            "task_tensions": result.get("task_tensions"),
        })]
    out = generators_path()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result["cards"], encoding="utf-8")
    except OSError:
        pass
    return [_text({
        "ok": True,
        "count": len(result["generators"]),
        "generators": result["generators"],
        "evidence_turns": result["evidence_turns"],
        "written": str(out),
    })]


def _blocking_eval(target: str, judge: str | None, limit: int, eval_id: str | None) -> dict:
    """Run + score one eval, returning a structured summary. Mirrors
    commands.eval.handle_eval_run but returns data instead of printing. Runs
    synchronously (the caller hands it to asyncio.to_thread); the judge's Claude
    dispatch rides the active MCP sampling session — that's the whole point: the
    CLI eval-run had no session and so fell back to `claude -p`."""
    from .config import load_config
    from .council_schema import resolve_provider_alias
    from .evals.builder import evals_dir, load_eval_set
    from .evals.runner import run_eval, save_run_result
    from .evals.scorer import score_run
    from .state_paths import lens_path
    from .commands.eval import _default_judge_provider

    target = resolve_provider_alias(target)
    if judge:
        judge = resolve_provider_alias(judge)

    if not eval_id:
        # (-mtime, stem) total order so the run_eval MCP tool picks the SAME
        # "latest" eval set under any glob order when two sets share an
        # st_mtime — mirrors the eval-CLI selectors (commands/eval.py) and the
        # launchpad canon.
        candidates = sorted(
            evals_dir().glob("eval_*.json"), key=lambda p: (-p.stat().st_mtime, p.stem)
        )
        if not candidates:
            return {"ok": False, "reason": "no eval sets on disk — run `trinity-local eval-build` first"}
        eval_id = candidates[0].stem

    eval_set = load_eval_set(eval_id)
    if eval_set is None:
        return {"ok": False, "reason": f"eval set {eval_id!r} not found"}

    config = load_config(None, required=True)
    provider_configs = {name: p for name, p in config.providers.items() if p.enabled}
    if target not in provider_configs:
        return {"ok": False, "reason": f"target {target!r} not enabled; available: {sorted(provider_configs)}"}

    run_result = run_eval(eval_set, target, provider_configs, limit=limit)

    if judge is None:
        judge = _default_judge_provider(target, provider_configs)
    if judge is None:
        return {"ok": False, "reason": "no judge available — need a second enabled provider, or pass `judge`"}

    lens_md = lens_path()
    lens_text = lens_md.read_text(encoding="utf-8") if lens_md.exists() else ""
    score_run(run_result, lens_text, judge, provider_configs)
    save_run_result(run_result)

    d = run_result.to_dict()
    return {
        "ok": True,
        "eval_id": eval_id,
        "target": target,
        "judge": judge,
        "self_judge": d.get("self_judge"),
        "items_total": d.get("items_total"),
        "items_completed": d.get("items_completed"),
        "items_failed": d.get("items_failed"),
        "aggregate_score": d.get("aggregate_score"),
        "by_rejection_type": d.get("by_rejection_type"),
    }


async def _run_eval(args: dict) -> list[Any]:
    """Handle mcp__trinity-local__run_eval. Runs the blocking eval OFF the event
    loop via asyncio.to_thread (copies the contextvars Context, so the active
    sampling session set in handle_call_tool propagates to the worker and the
    judge rides the user's subscription instead of `claude -p`)."""
    import asyncio

    target = args.get("target")
    if not isinstance(target, str) or not target.strip():
        return [ErrorData(code=400, message="`target` (provider/model to score) is required")]
    judge = args.get("judge")
    if judge is not None and not isinstance(judge, str):
        return [ErrorData(code=400, message="`judge` must be a string when provided")]
    eval_id = args.get("eval_id")
    if eval_id is not None and not isinstance(eval_id, str):
        return [ErrorData(code=400, message="`eval_id` must be a string when provided")]
    try:
        limit = int(args.get("limit", 5))
    except (TypeError, ValueError):
        return [ErrorData(code=400, message="`limit` must be an integer")]
    if limit < 1:
        return [ErrorData(code=400, message="`limit` must be >= 1")]

    result = await asyncio.to_thread(_blocking_eval, target.strip(), judge, limit, eval_id)
    return [_text(result)]


# _build_rate_action() + _pending_ratings_hint() + _PENDING_HINT_CACHE
# removed 2026-05-21. Per user direction: "Retire the whole mechanism"
# (rate-action hint injection alongside the record_outcome MCP tool).
# The chairman's pick IS the supervision signal — agents don't need
# to be nudged to capture a verdict that's already captured. Pillar 4
# funnel-widener mechanism deferred until a different shape proves out
# (current default: refinement prompts on the council page surface
# "what should the chairman have picked instead" without an agent-side
# tax). Registry: src/trinity_local/retired_names.py.


def _lookup_council_status(council_run_id: str) -> dict | None:
    """Find the live status file for a council, regardless of which token
    keyed it. Status files live at portal_pages/status/council_status_<token>.json
    and the token is often the bundle_id, not the council_run_id, so a direct
    lookup misses. Falls back to a scan that matches on `council_id`.
    """
    from .council_status import load_council_status
    from .state_paths import council_status_dir

    payload: dict | None = load_council_status(council_run_id)
    if payload is not None:
        return payload
    for path in council_status_dir().glob("council_status_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except Exception:
            continue
        if data.get("council_id") == council_run_id:
            return data
    return None


def _maybe_offer_open_council(council_run_id: str, review_path: str | None) -> dict | None:
    """Council finished — ask ONCE whether to open the review page, and open it on
    yes. Runs in a worker thread (via asyncio.to_thread) so the blocking elicit is
    safe. A per-council marker prevents re-asking on subsequent status polls; the
    elicit degrades to a text breadcrumb when the client doesn't support it.
    `TRINITY_OPEN_COUNCIL_PROMPT=0` disables the prompt entirely."""
    import os
    import re

    if os.environ.get("TRINITY_OPEN_COUNCIL_PROMPT") == "0":
        return None
    # Path-safety: council_run_id is joined into a filesystem path below and handed
    # to open_path. Reject anything that isn't a plain id so a crafted id can't
    # traverse out of review_pages_dir() (defense-in-depth — _get_council_status
    # already gates on a similar check, but this helper must stand alone).
    if not isinstance(council_run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", council_run_id):
        return None
    try:
        from . import tips
        from .mcp_features import elicit
        from .notifications import open_path

        key = f"open-council:{council_run_id}"
        if key in tips._seen():
            return None
        if not review_path:
            try:
                from .state_paths import review_pages_dir

                base = review_pages_dir().resolve()
                cand = (base / f"{council_run_id}.html").resolve()
                # realpath containment: the resolved candidate must stay strictly
                # under review_pages_dir (belt-and-suspenders over the id regex).
                if cand.is_relative_to(base) and cand.exists():
                    review_path = str(cand)
            except Exception:
                review_path = None
        if not review_path:
            return None
        tips.mark_tip_seen(key)  # ask at most once per council, regardless of answer
        ans = elicit(
            "Council done — open the page for the full per-model breakdown?",
            {"type": "object",
             "properties": {"open": {"type": "boolean",
                                     "description": "Open the council page in your browser?"}},
             "required": ["open"]},
        )
        if ans is None:
            # No elicitation support (e.g. Codex/Cursor) — leave a breadcrumb the agent can offer.
            return {"key": "open-council", "kind": "text",
                    "message": "Council done — open the page for the full breakdown.",
                    "cta": review_path}
        if ans.get("open"):
            open_path(review_path)
            return {"opened": True, "review_path": review_path}
        return {"opened": False}
    except Exception:
        return None


async def _get_council_status(args: dict) -> list[Any]:
    from .council_runtime import load_council_outcome
    from .state_paths import council_outcomes_dir

    council_run_id = args["council_run_id"]

    # Path-safety: council_run_id is joined into a filesystem path below. Reject
    # path separators / traversal so a crafted or buggy id can't read an
    # arbitrary .json outside council_outcomes_dir() (defense-in-depth — the MCP
    # client is trusted-local, but the id should never escape the directory). An
    # unsafe id matches no real council, so we fall through to the "not found".
    _id_path_safe = (
        isinstance(council_run_id, str)
        and "/" not in council_run_id
        and "\\" not in council_run_id
        and ".." not in council_run_id
    )

    # Two storage locations:
    #   - council_outcomes/<id>.json: written ONCE on completion, durable
    #   - portal_pages/status/council_status_<token>.json: updated live during the run
    # `_lookup_council_status` handles both the direct-key and fallback-scan
    # cases; the wait_seconds polling path uses the same helper.
    status_payload: dict | None = (
        _lookup_council_status(council_run_id) if _id_path_safe else None
    )

    outcome_summary: dict | None = None
    outcome_load_error: str | None = None
    outcome_path = council_outcomes_dir() / f"{council_run_id}.json"
    if _id_path_safe and outcome_path.exists():
        try:
            outcome = load_council_outcome(council_run_id)
            label = outcome.routing_label
            outcome_summary = {
                "winner": outcome.winner_provider,
                "primary_provider": outcome.primary_provider,
                "primary_model": outcome.primary_model,
                "synthesis_output": outcome.synthesis_output,
                "agreed_claims": list(getattr(label, "agreed_claims", []) or []) if label else [],
                "disagreed_claims": list(getattr(label, "disagreed_claims", []) or []) if label else [],
                "routing_lesson": getattr(label, "routing_lesson", "") if label else "",
                "user_likely_values": list(getattr(label, "user_likely_values", []) or []) if label else [],
                "member_count": len(outcome.member_results),
            }
        except Exception as exc:
            # Silent skip would tell the agent "status is completed"
            # but "outcome is null" without explaining why. Most likely
            # cause: outcome JSON half-written by a legacy writer or
            # partially corrupted on disk.
            outcome_summary = None
            outcome_load_error = f"{type(exc).__name__}: {exc}"

    if status_payload is None and outcome_summary is None:
        # The corrupt-outcome-file path needs to carry outcome_load_error
        # too — otherwise the agent sees "unknown" with no signal that
        # an actual file existed but wouldn't parse.
        early_response: dict[str, Any] = {
            "council_run_id": council_run_id,
            "status": "unknown",
            "error": "no live status file or completed outcome found",
        }
        if outcome_load_error is not None:
            early_response["outcome_load_error"] = outcome_load_error
        return [_text(early_response)]

    # Compress live status to a small per-member summary.
    members_summary: dict | None = None
    if status_payload:
        members = status_payload.get("members") or {}
        members_summary = {
            provider: {
                "status": info.get("status"),
                "model": info.get("model"),
                "response_chars": len(info.get("response_text") or ""),
            }
            for provider, info in members.items()
        }

    status_response: dict = {
        "council_run_id": council_run_id,
        "status": (status_payload or {}).get("status") or ("completed" if outcome_summary else "unknown"),
        "task_text": (status_payload or {}).get("task_text"),
        "members": members_summary,
        "synthesis_status": ((status_payload or {}).get("synthesis") or {}).get("status"),
        "review_path": (status_payload or {}).get("review_path"),
        "outcome": outcome_summary,
    }
    if outcome_load_error is not None:
        status_response["outcome_load_error"] = outcome_load_error
    # E1: once the council is done, offer to open the page (the founder's ask).
    # Off-thread so the blocking elicit is safe on the event loop; asked once/council.
    if status_response["status"] == "completed":
        try:
            import asyncio as _asyncio

            rec = await _asyncio.to_thread(
                _maybe_offer_open_council, council_run_id, status_response.get("review_path")
            )
            if rec:
                status_response["open_council"] = rec
        except Exception:
            pass
    return [_text(status_response)]


async def _import_provider_memory(args: dict) -> list[Any]:
    """Pipe lens / eval JSON straight into Trinity's local state.

    The agent reusing this loop INSIDE Claude Code / Codex / Cursor has
    the user's full conversation history on its side — same as the
    provider-side prompt loops (lens-prompt + eval-prompt) but closes
    in-protocol. Dispatch reuses the same dict→signal mapping the
    CLI verbs use, so the dedup / malformed-skip / append-only
    semantics are identical.
    """
    from argparse import Namespace
    import io
    import json as _json
    from contextlib import redirect_stdout

    kind = (args.get("kind") or "").strip().lower()
    if kind not in ("lens", "eval"):
        return [_text({
            "ok": False,
            "error": f"kind must be 'lens' or 'eval' (got {kind!r})",
        })]
    payload = args.get("payload")
    if not isinstance(payload, dict):
        return [_text({
            "ok": False,
            "error": "payload must be an object (matching the lens or eval schema)",
        })]

    # The CLI handlers read raw text from stdin / file. To reuse them
    # without disk I/O, serialize the payload and feed it via the
    # --from-json path. Capture stdout (which carries the JSON summary
    # when as_json=True) into a buffer.
    raw = _json.dumps(payload)
    ns = Namespace(
        path=None,
        from_json=False,
        provider=args.get("provider"),
        dry_run=bool(args.get("dry_run", False)),
        as_json=True,
    )

    if kind == "lens":
        from .commands.lens_import import handle_lens_import as _handler
    else:
        from .commands.eval_import import handle_eval_import as _handler

    # The handlers read sys.stdin when from_json=True. Replace sys.stdin
    # for the duration of the call to feed our serialized payload.
    import sys as _sys
    ns.from_json = True
    saved_stdin = _sys.stdin
    _sys.stdin = io.StringIO(raw)
    buf = io.StringIO()
    rc = None
    try:
        with redirect_stdout(buf):
            rc = _handler(ns)
    finally:
        _sys.stdin = saved_stdin

    out = buf.getvalue().strip()
    try:
        summary = _json.loads(out) if out else {}
    except _json.JSONDecodeError:
        summary = {"ok": rc == 0, "raw_output": out}

    summary["kind"] = kind
    summary["dry_run"] = bool(args.get("dry_run", False))
    if rc not in (None, 0):
        summary["ok"] = False
        summary["exit_code"] = rc
    return [_text(summary)]


async def run_stdio_server():
    # Dev mode: watch source tree for edits and exit on change so the MCP
    # launcher respawns with fresh code. No-op when TRINITY_MCP_WATCH is unset.
    from .mcp_watchdog import start_watchdog_if_enabled

    start_watchdog_if_enabled()

    # First-spawn auto-scan: when corpus is empty AND local CLI transcript
    # dirs (~/.claude, ~/.codex, ~/.gemini, cowork) exist, kick a background
    # ingest so the first council the user fires already has personalization
    # signal. No-op when corpus is populated or no source dirs found.
    from .cold_start import maybe_kick_cold_start

    # The cold-start *scan* is pure ingest (no LLM) — safe to run at startup
    # before any session exists. The two LLM-making kicks (first-build +
    # refresh) are deliberately NOT fired here: they're moved to the first
    # tool call (see `_maybe_fire_lens_kicks` in handle_call_tool) so they run
    # with an active MCP session and route their Claude stages through
    # sampling/createMessage instead of burning `claude -p` quota. Firing them
    # here — before server.run() — means no session is registered yet, so they
    # could never sample (the #263 keystone bug).
    maybe_kick_cold_start()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


