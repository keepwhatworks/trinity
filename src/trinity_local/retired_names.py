"""Registry of retired CLI names, MCP tools, modules, and concepts.

Retirement is narrated everywhere
(claude.md, MIGRATION.md, simplification_log.md, inline comments) but
declared nowhere. Iter #68/#69 caught MIGRATION.md internally
contradicting itself ("keep `shortcut-install`" vs "the CLI no longer
exists") 20 lines apart. The narration doesn't talk to itself.

This module declares retirements as structured data so:
- Tests can grep for present-tense references to retired names
  (sweep pattern #27 as automated guard)
- ``trinity-local <retired-cli>`` can show a friendly migration message
  rather than ``unknown command``
- Docs can reference the registry at render time instead of restating

Schema:
    RetirementRecord(retired_at, commit, replacement, reason, kind,
                     artifact_persists)

When adding a retirement: add the entry here in the same commit as
the deletion. Don't narrate the retirement in 5 places + forget the
registry — the registry IS the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RetirementKind = Literal["cli", "mcp_tool", "module", "file", "config_field", "concept"]


@dataclass(frozen=True)
class RetirementRecord:
    """A single retirement event captured as structured data.

    Fields:
        name: The retired name as it appeared in code or invocation
            (e.g., ``"shortcut-install"``, ``"search_prompts"``,
            ``"me-build"``, ``"Trinity.app"``).
        retired_at: ISO date the retirement landed on main (YYYY-MM-DD).
        commit: Short or full hash of the retirement commit (if known).
        replacement: The current substitute, or empty string if there
            is no direct replacement (the feature was killed without
            a successor).
        reason: One-line human-readable reason.
        kind: Category — cli / mcp_tool / module / file / config_field /
            concept. Lets tests filter by kind.
        artifact_persists: True if the retired artifact (e.g., a macOS
            Shortcut on disk) keeps working for users who already had
            it, even though the bootstrap path is gone.
    """

    name: str
    retired_at: str
    commit: str
    replacement: str
    reason: str
    kind: RetirementKind = "cli"
    artifact_persists: bool = False


# Canonical registry. Keys = retired names; values = records.
# Add entries in the SAME commit as the deletion. Sorted by retirement
# date (most recent first) for ease of audit.
RETIRED: dict[str, RetirementRecord] = {
    # ── 2026-06-05 Stage 4b conflicts retirement (pipeline simplification) ──
    "src/trinity_local/me/conflicts.py": RetirementRecord(
        name="src/trinity_local/me/conflicts.py",
        retired_at="2026-06-05",
        commit="",
        replacement="the generators pass's semantic contradiction-split self-critique (me/generators.py)",
        reason="Stage 4b (#141) detected contradictions between lens pairs by LITERAL pole-swapping (same axis, opposite direction) — surface-form logic on a semantic problem, the same anti-pattern that sank the moves substrate (#184). It produced an EMPTY conflicts.json on the real corpus (literal swaps almost never occur) and never built its planned V2 (embedding-similarity). The generators pass IS that semantic V2 AND resolves the contradiction (names the missing cross-domain generator), so the cross-topic layer subsumes it. Removed: the Stage 4b call in me_builder, stage4b_surface_conflicts + the lens.md 'Tensions in tension' render in me/pipeline, the launchpad + status conflict surfaces, me/conflicts.py, and tests/test_me_conflicts.py.",
        kind="module",
    ),
    # ── 2026-05-27 post-moves dead-code cleanup (#187) ──
    "src/trinity_local/me/depth.py": RetirementRecord(
        name="src/trinity_local/me/depth.py",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — depth_score never wired into a live consumer)",
        reason="Task #139 (multi-resolution horizon tagging) shipped depth_score (371 LOC) but nothing in production reads its output. Surfaced by the orphan finder built during #184/#187 — module passed unit tests but had zero non-test consumers. The TestDirectAgentsViaDepthSignal class in tests/test_real_corpus_invariants.py wraps its imports in try/except + pytest.skip, so it degrades to skipped rather than red. Future re-introduction should wire into chairman/cortex consumption before re-landing.",
        kind="module",
    ),
    "src/trinity_local/setup_guidance.py": RetirementRecord(
        name="src/trinity_local/setup_guidance.py",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — health_checks + status absorbed its surface)",
        reason="60 LOC of cold-install guidance helper with zero production import statements (only mentioned in stale comments inside main.py, adapters.py, launchpad_data.py). The CLI surface it powered was absorbed into health_checks.py + the status verb prior to this commit. Test file (33 LOC) deleted alongside.",
        kind="module",
    ),
    # ── 2026-05-27 moves substrate teardown (#184) ──
    "trinity-local moves-build": RetirementRecord(
        name="trinity-local moves-build",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — substrate retired; lens IS the source of truth)",
        reason="Moves substrate was a redundant projection of the lens. The chairman LLM bridges declarative→procedural at inference time when it reads lens tensions during synthesis — pre-computing procedural moves is a JIT-cache for a free operation. Real-data dream cycle proved the 4-tier gate filtered 100% of candidates due to T1 surface-form mismatch (#181 + this commit's investigation). Net deletion: -4400 LOC across substrate + tests + schemas.",
        kind="cli",
    ),
    "trinity-local moves-show": RetirementRecord(
        name="trinity-local moves-show",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — substrate retired)",
        reason="See trinity-local moves-build retirement.",
        kind="cli",
    ),
    "trinity-local moves-export": RetirementRecord(
        name="trinity-local moves-export",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — substrate retired)",
        reason="See trinity-local moves-build retirement.",
        kind="cli",
    ),
    "src/trinity_local/moves/": RetirementRecord(
        name="src/trinity_local/moves/",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — substrate retired; lens primitives live in me/)",
        reason="The procedural-memory layer Trinity attempted (gate.py 4-tier Bayesian gate, dream.py Phase 6 promotion/demotion, schemas.py Move dataclass, store.py persistence, frontmatter.py SKILL.md YAML, ~2300 LOC total). Empirically dormant: real-data dream cycle showed T1 rejecting 100% of candidates due to surface-form-vs-semantic-space mismatch. The chairman LLM is the right primitive for that bridge at inference time. The orphan gate_lens_tensions primitive from #181 Change #3 went with it; T2 cosine validation will land at Stage 4 of lens-build per #186.",
        kind="module",
    ),
    "src/trinity_local/commands/moves.py": RetirementRecord(
        name="src/trinity_local/commands/moves.py",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — substrate retired)",
        reason="CLI surface for moves-build / moves-show / moves-export. 375 LOC. See src/trinity_local/moves/ retirement.",
        kind="module",
    ),
    "schemas/move.schema.json": RetirementRecord(
        name="schemas/move.schema.json",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — Move dataclass retired)",
        reason="JSON Schema for the Move dataclass. Substrate retired.",
        kind="file",
    ),
    "schemas/dream_rejection.schema.json": RetirementRecord(
        name="schemas/dream_rejection.schema.json",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — moves promotion path retired)",
        reason="JSON Schema for dream_rejections.jsonl events emitted by the moves promotion pass. Substrate retired.",
        kind="file",
    ),
    "schemas/dream_demotion.schema.json": RetirementRecord(
        name="schemas/dream_demotion.schema.json",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — moves demotion path retired)",
        reason="JSON Schema for dream_demotions.jsonl events emitted by the moves demotion pass. Substrate retired.",
        kind="file",
    ),
    "schemas/dream_calibration.schema.json": RetirementRecord(
        name="schemas/dream_calibration.schema.json",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — calibration loop retired; future tension-level calibration will design its own schema)",
        reason="JSON Schema for ~/.trinity/dream_calibration.json — the T3↔T4 calibration state we shipped in #181 Change #2. Retired one day later when the moves substrate it calibrated was demoted to dormant projection.",
        kind="file",
    ),
    "trinity-local dream --skip-moves": RetirementRecord(
        name="trinity-local dream --skip-moves",
        retired_at="2026-05-27",
        commit="",
        replacement="(none — Phase 6 retired entirely)",
        reason="CLI flag for skipping moves phase of dream. Phase itself retired.",
        kind="config_field",
    ),
    # ── 2026-05-26 substrate-arc cut pass (handoff + memory-compare) ──
    "trinity-local handoff": RetirementRecord(
        name="trinity-local handoff",
        retired_at="2026-05-26",
        commit="",
        replacement="MCP Resources (trinity://memories/lens.md) — every MCP-aware harness reads the lens at handshake; the agent can suggest 'try this in <other provider>' without an explicit handoff dispatch.",
        reason="0 handoff events in 163 launch_events on the dogfooder's production machine. User deprioritized as primary demo 2026-05-26. Heavy surface (~350 LOC across handoff.py + commands/handoff.py + 15 shared-file refs) for a feature with no measurable usage. The cross-provider continuity claim survives via MCP Resources.",
        kind="cli",
    ),
    "mcp_tool:handoff": RetirementRecord(
        name="mcp_tool:handoff",
        retired_at="2026-05-26",
        commit="",
        replacement="MCP Resources (trinity://memories/lens.md, trinity://memories/core.md, etc.) — lens flows to the agent at session handshake without an extra tool round-trip.",
        reason="Same as trinity-local handoff CLI — 0 usage events in production. Tool surface drops from 9 → 8.",
        kind="mcp_tool",
    ),
    "mcp_tool:route": RetirementRecord(
        name="mcp_tool:route",
        retired_at="2026-06-08",
        commit="",
        replacement="ask(mode='route') — the identical routing decision through the one routing entrypoint. The _route() helper survives internally (ask delegates to it); only the standalone tool registration was removed.",
        reason="Loop-primitive surface cut (council_2eaa453fa20b69f6, all 3 labs agreed): a standalone route tool reinvented what ask already owns. Deprecated since #213 (Q4 surface-collapse) when ask(mode='route') subsumed it; removed now to fit the standardized primitives. Tool surface drops from 8 → 7.",
        kind="mcp_tool",
    ),
    "trinity-local memory-compare": RetirementRecord(
        name="trinity-local memory-compare",
        retired_at="2026-05-26",
        commit="",
        replacement="(none — was marketing-positioning artifact; the Trinity-vs-Auto-Dream comparison narrative now lives only in CLAUDE.md prose)",
        reason="199 LOC of CLI surface that compared Trinity's lens against Claude Auto-Dream lexically. Not referenced in any launch-day doc. Marketing artifact that shipped its purpose pre-launch; v2 substrate work doesn't need it.",
        kind="cli",
    ),
    "src/trinity_local/handoff.py": RetirementRecord(
        name="src/trinity_local/handoff.py",
        retired_at="2026-05-26",
        commit="",
        replacement="(none — feature retired, not relocated)",
        reason="Handoff feature's core module — 263 LOC packaging (user, assistant) turn-window assembly + dispatch to a different provider.",
        kind="module",
    ),
    "src/trinity_local/memory_compare/": RetirementRecord(
        name="src/trinity_local/memory_compare/",
        retired_at="2026-05-26",
        commit="",
        replacement="(none — feature retired, not relocated)",
        reason="Memory-compare feature's module directory — metrics + parse_lens + parse_claude_memory.",
        kind="module",
    ),
    # ── 2026-05-21 launchpad backend dead-code cleanup ──
    "_rate_limit_saves": RetirementRecord(
        name="_rate_limit_saves",
        retired_at="2026-05-21",
        commit="f9dafc2",
        replacement="(none — function was orphan; no Vue consumer)",
        reason="launchpad_data._rate_limit_saves() computed a 30-day rate-limit-save count and shipped it into pageData['rateLimitSaves'], but the Vue template never read it. Pre-launch the user explicitly said 'remove this' for the rate-limit-saves card; the UI was deleted but the backend compute survived as orphan. Removed both the function and the pageData injection in the same commit.",
        kind="module",
    ),
    "pageData.verdictStats": RetirementRecord(
        name="pageData.verdictStats",
        retired_at="2026-05-21",
        commit="f9dafc2",
        replacement="(none — rating-rate signal is moot post-2026-05-22 full rating retirement; doctor._check_verdict_rate also retired)",
        reason="The launchpad pageData field stopped being read by Vue when the rating UX was sunset (commit 96b03c7). doctor._check_verdict_rate kept consuming _verdict_stats() until the full rating-surface retirement on 2026-05-22 (cleanup pass decision #2). With ratings gone, the verdict-capture rate is structurally 0%; surfacing it would be misleading noise. _verdict_stats() compute function may still exist for back-compat but has no live consumers.",
        kind="concept",
    ),
    "pageData.rateLimitSaves": RetirementRecord(
        name="pageData.rateLimitSaves",
        retired_at="2026-05-21",
        commit="f9dafc2",
        replacement="(none — see _rate_limit_saves)",
        reason="Companion entry: the pageData field never had a Vue consumer; removed alongside the _rate_limit_saves() function.",
        kind="concept",
    ),
    # ── 2026-05-21 rate-action mechanism retirement (companion to record_outcome) ──
    "rate_action": RetirementRecord(
        name="rate_action",
        retired_at="2026-05-21",
        commit="e8c9c4f",
        replacement="(none — chairman pick IS the supervision signal, fed automatically)",
        reason="The `rate_action` field that route/ask/run_council/get_council_status injected into MCP responses pointed agents at the retired record_outcome tool. With record_outcome retired and chairman pick auto-flowing into the personal routing table (commit 44eb934), the nudge had no destination. Per user direction 'Retire the whole mechanism' — agents don't need a hint to capture a verdict that's already captured. Pillar 4 funnel-widener deferred until a different shape proves out (current default: refinement prompts on the council page surface 'what should the chairman have picked instead' without an agent-side tax).",
        kind="concept",
    ),
    "pending_ratings": RetirementRecord(
        name="pending_ratings",
        retired_at="2026-05-21",
        commit="e8c9c4f",
        replacement="(none — see rate_action)",
        reason="`pending_ratings` was the SECONDARY funnel-widener — surfaced older unrated councils in route/ask responses. Same mechanism, same retirement (the agent-side capture pressure is gone with record_outcome). Launchpad surfaces unrated councils via the existing `unrated` CLI; pending_ratings as an MCP nudge is sunset alongside the rate-action mechanism.",
        kind="concept",
    ),
    # ── 2026-05-21 rating-UX MCP retirement ──
    "record_outcome": RetirementRecord(
        name="record_outcome",
        retired_at="2026-05-21",
        commit="809e617",
        replacement="routing_label.winner (chairman pick) as supervision signal; refinement prompts as 'what user wanted differently'",
        reason="Per user direction 'we are sunsetting user ratings. Full retirement including MCP.' The MCP rating tool pressured agents to interrupt conversations to surface rate prompts. The chairman's pick IS the verdict (per the 2026-05-21 prime directive 'picks the answer YOU would have picked'); compute_personal_routing_table now aggregates from routing_label.winner (commit 44eb934) instead of blending with user_winner. Refinement prompts on each council carry the post-pivot 'what should it have been instead' signal — embedded in the user's natural flow, not a tax. CLI council-rate followed this retirement one day later on 2026-05-22 (see council-rate entry; full rating retirement, no power-user override remained).",
        kind="mcp_tool",
    ),
    # ── 2026-05-21 consistency-sweep retirement ──
    "commands.tasks": RetirementRecord(
        name="commands.tasks",
        retired_at="2026-05-21",
        commit="064e7ba",
        replacement="(none — task/bundle/launch flows supplanted by the live council architecture)",
        reason="`commands/tasks.py` (90 LOC) held the handle_bundle_create / handle_task_create / handle_launch_create / handle_task_show / handle_task_sync handlers for retired CLIs. The module's own docstring claimed 'Tests still import handle_* for handler-level coverage' but tick 85 audit found ZERO callers across src/ + tests/ — the false-claim docstring outlived the test coverage by months. The actual live substrate (LaunchEvent dataclass, append_launch_event, create_launch_event) survives in council_runtime.py and is exercised by the council pipeline. Pattern #4 + #20: when a CLI retires, the handler module's docstring is the last surface to update; sometimes it never does. Sunset confirmed via AskUserQuestion in tick 85.",
        kind="module",
    ),
    "commands.depth": RetirementRecord(
        name="commands.depth",
        retired_at="2026-05-21",
        commit="064e7ba",
        replacement="(none — depth-signal geometry lives in me/depth.py; no caller wants a CLI wrapper)",
        reason="`commands/depth.py` (123 LOC) held the handle_depth_show CLI handler for a CLI that was retired pre-launch. The docstring claimed 'Tests still import handle_depth_show for coverage' but tick 85 audit found ZERO callers in src/ + tests/. The actual geometry primitives (depth_score, corpus_distance, inter_turn_distance, LID) live in `me/depth.py` and ARE actively used by basins.py + lens pipeline. Sunset confirmed via AskUserQuestion in tick 85; the geometry stays.",
        kind="module",
    ),
    "unrated": RetirementRecord(
        name="unrated",
        retired_at="2026-05-22",
        commit="998e8c1",
        replacement="(none — the unrated funnel widened toward a rating UX that itself is retired; nothing replaces it. compute_personal_routing_table walks council_outcomes/ directly.)",
        reason="`unrated` subcommand listed councils without `user_verdict` so the user could clear the rating backlog. Whole CLI was Pillar 4 'verdict-capture funnel widening' from the forward arc. With ratings retired (decision #2 of 2026-05-22 cleanup pass), there's no backlog to widen.",
        kind="cli",
    ),
    "_verdict_stats": RetirementRecord(
        name="_verdict_stats",
        retired_at="2026-05-22",
        commit="9da7149",
        replacement="(none — last consumer doctor._check_verdict_rate was retired in commit c316727; load_council_outcome wipes user_verdict on read so the function would have always returned rated=0 anyway)",
        reason="Walked council_outcomes/*.json counting how many carried metadata.user_verdict.user_winner. Powered the launchpad verdictStats card (retired 2026-05-21) + doctor._check_verdict_rate health-check (retired 2026-05-22). With ratings fully retired and load_council_outcome stripping user_verdict on every read, the function had zero callers AND would always return rated=0/rate=0.0 on fresh data. 55 LOC dead code + 511 LOC dead tests (tests/test_verdict_stats.py).",
        kind="function",
    ),
    "doctor._check_verdict_rate": RetirementRecord(
        name="doctor._check_verdict_rate",
        retired_at="2026-05-22",
        commit="c316727",
        replacement="(none — doctor no longer reports a verdict-capture rate; with ratings retired the metric is always 0% which would be noise rather than signal)",
        reason="Soft health check that walked _verdict_stats() and reported what fraction of councils had user_verdict.user_winner set. Pillar-4 'verdict-capture funnel' was the framing. With the full rating retirement on 2026-05-22 (decisions #2 + #7 of cleanup pass: CLI council-rate gone, UI removed, schema clean, wipe-on-read), the metric is structurally 0% on all fresh installs. Tests in tests/test_doctor.py::TestVerdictRateCheck deleted in the same commit.",
        kind="function",
    ),
    "council-rate": RetirementRecord(
        name="council-rate",
        retired_at="2026-05-22",
        commit="998e8c1",
        replacement="(none — the chairman's routing_label.winner IS the supervision signal; compute_personal_routing_table aggregates from it directly. Refinement prompts on the council page carry 'what should it have been instead' signal inline.)",
        reason="Per user directive 2026-05-22: 'user doesn't have to provide ratings. that's another task for them. use the lens governed council selections.' The whole rating loop violated the thesis (lens > ratings; evaluation easier than generation through lens). MCP `record_outcome` was retired 2026-05-21 for the same reason; CLI `council-rate` was kept as 'power-user override' but the user now confirms full retirement is the right call. Cleanup pass decision #2 in plan-mode session.",
        kind="cli",
    ),
    "commands.unrated": RetirementRecord(
        name="commands.unrated",
        retired_at="2026-05-22",
        commit="998e8c1",
        replacement="(none — the whole module was purpose-built for the rating-loop funnel widening (Pillar 4 from forward arc). With ratings retired, the unrated backlog has no actionable next step. Chairman pick is the supervision signal.)",
        reason="`commands/unrated.py` listed councils without `user_verdict` so the user could see their rating backlog. Module docstring: 'Closes Pillar 4 (verdict-capture funnel widening)' + 'The 16%-rate problem isn't a UX-flaw-per-click; it's that the user doesn't realize how many councils they haven't rated.' Both motivations are moot once ratings are retired. Cleanup pass decision #2 cascaded retirement.",
        kind="module",
    ),
    "commands.trust": RetirementRecord(
        name="commands.trust",
        retired_at="2026-05-22",
        commit="8465dd6",
        replacement="(none — trust + audit library lives in `trinity_local.trust`; CLI rebuilt from scratch in v1.1)",
        reason="`commands/trust.py` (69 LOC) held handle_audit_show / handle_trust_init / handle_trust_show for CLIs already retired 2026-05-20 (audit-show / trust-init / trust-show, commit 1d84366). Docstring claimed 'handlers stay reachable by tests' but iter #115 audit found ZERO callers in tests/ — `test_trust.py` exercises only the library (load_trust_config / resolve_trust / read_audit_log / write_default_trust_toml), not the CLI handlers. Exact same false-claim-docstring shape as `commands.tasks` (tick 85) and `commands.depth` (tick 85). Same fix: delete the orphan module. Library trinity_local.trust + 16 library tests stay; v1.1 will rebuild the CLI surface fresh when needed. Sunset confirmed via AskUserQuestion in iter #115.",
        kind="module",
    ),
    "implementation-notes.html": RetirementRecord(
        name="implementation-notes.html",
        retired_at="2026-05-22",
        commit="(see git log — orphan HTML sunset in iter #124 of post-launch sweep)",
        replacement="(none — the historical iter #15-#72 consistency-sweep write-up lives in git history; no live surface needs it)",
        reason="implementation-notes.html lived at repo root, titled 'Implementation Notes — Consistency Sweep (iters #15-#72)'. Carried canonical-placeholder blocks so the test-count and doc-consistency-guard numbers stayed fresh — but the file was referenced from nowhere. The static site at keepwhatworks.com is rooted at docs/, not repo root, so this HTML was structurally orphaned: it auto-rendered on every test-count change (cache-busting churn) without any navigation surface. Sunset confirmed via AskUserQuestion in iter #124 — user picked 'Sunset — delete the file'. Net cleanup: -17 KB tracked HTML + stops auto-render churn from rippling 1 surface on every test-count shift.",
        kind="file",
    ),
    "trust.schema.json": RetirementRecord(
        name="trust.schema.json",
        retired_at="2026-05-22",
        commit="(see git log — same iter as TRUST-MODE.md sunset follow-on)",
        replacement="(none — v1.1 trust gating will design its own schema fresh; v1.0 shape preserved in docs/historical/trust-mode.md text)",
        reason="The trust.toml JSON Schema (schemas/trust.schema.json + skills/trinity/schemas/trust.schema.json byte-identical mirror) defined the v1.0 trust.toml gating-config shape. With the trinity_local.trust library retired 2026-05-22 (iter #117) and ~/.trinity/trust.toml having zero consumers, the schemas became orphan reference material. TRUST-MODE.md was moved to docs/historical/ in iter #119 (commit dc2bf48); iter #121 finished the substrate cleanup by deleting the schema files. v1.1 trust-mode rebuild was already explicitly framed as 'from scratch' in iter #120 (commit 21259d0, three-tier-architecture.md), so preserving the v1.0 schema as forward reference offered no value. The original shape is shown verbatim in the historical doc text if v1.1 contributor wants reference. Sunset confirmed via AskUserQuestion in iter #121 — user picked 'Sunset — delete both files'.",
        kind="file",
    ),
    "trinity_local.trust": RetirementRecord(
        name="trinity_local.trust",
        retired_at="2026-05-22",
        commit="(see git log — same change as the file deletion)",
        replacement="scripts/_runtime.py::audit_log() — the active trust+audit substrate that scripts/embed.py, scripts/cluster.py, scripts/anchor.py, scripts/descriptor.py write through. Independent implementation; never went through trinity_local.trust.",
        reason="src/trinity_local/trust.py (302 LOC) was a planned-but-deferred Phase 6 'trust mode + audit log' library. The commands.trust retirement on 2026-05-22 (8465dd6) kept the library on the assumption v1.1 would rebuild a CLI on top of it. Iter #117 audit found ZERO production imports — only tests/test_trust.py (270 LOC) exercises the library. Meanwhile the actual ~/.trinity/audit.log writes happen via scripts/_runtime.audit_log(), a separate stdlib-only implementation that the scripts cluster has used all along. The library was duplicate scaffolding, not active substrate. Sunset confirmed via AskUserQuestion in iter #117 of the post-launch consistency sweep — user picked 'Sunset — delete + register retired'. Net delete: 572 LOC (302 library + 270 tests).",
        kind="module",
    ),
    "thread_context": RetirementRecord(
        name="thread_context",
        retired_at="2026-05-21",
        commit="55499b8",
        replacement="(none — JS-side inline implementation in launchpad_template.py:2548-2574)",
        reason="Docstring claimed it was the canonical formatter \"used by commands/replay.py + launchpad_template.py\" but tick 81 audit found ZERO Python callers. replay.py uses its own `_build_hidden_context()` with a different format; launchpad_template.py must reimplement in JS because the file:// architecture (claude.md \"File:// is the substrate\") blocks JS from importing Python. The `_strip_thread_context()` reader in launchpad_data.py only PARSES the format, doesn't import the producer. Pure orphan; deleted cleanly with the JS-side comment updated to no longer point at the removed file. Pattern #4: when fixing a bug, audit for its shape — same orphan-module shape as the `feature_extractors` + `training_schema` sunset in tick 57.",
        kind="module",
    ),
    # ── 2026-05-20 consistency-sweep retirement ──
    "models_dir": RetirementRecord(
        name="models_dir",
        retired_at="2026-05-20",
        commit="0b82c83",
        replacement="hf_cache_model_path() in backend_mlx (reports HF cache)",
        reason="state_paths helper built ~/.trinity/models/<name>/ but nothing "
               "read the resulting path; the actual model lives in HF cache. "
               "Tick 28 dropped the dead helper + the misleading model_path() "
               "wrapper in backend_mlx; MlxEmbedder.model_path now points at "
               "the real ~/.cache/huggingface/hub/ location.",
        kind="module",
        artifact_persists=True,  # empty ~/.trinity/models/ may exist on older installs
    ),
    "~/.trinity/models/": RetirementRecord(
        name="~/.trinity/models/",
        retired_at="2026-05-20",
        commit="0b82c83",
        replacement="~/.cache/huggingface/hub/models--nomic-ai--nomic-embed-text-v1.5",
        reason="Directory created as side-effect of the unused models_dir() "
               "helper. Actual nomic weights cached by sentence-transformers "
               "in HF cache, not here. Safe to delete on user systems.",
        kind="file",
        artifact_persists=True,
    ),
    # ── 2026-05-20 dead-register strip (CLIs that lost their surface earlier) ──
    # Commit 1d84366 stripped fully-elaborated register() functions for CLIs
    # that main.py had already dropped from CORE_COMMAND_MODULES. Each had
    # been silently un-callable for some time before the dead code was
    # removed. retired_at = strip date; "deferred" replacements are explicit.
    "task-create": RetirementRecord(
        name="task-create",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="(none — task/bundle/launch flows supplanted by the live council architecture)",
        reason="Dead register() in commands/tasks.py stripped; user-facing surface had already been orphan after the live-council architecture took over (see commands.tasks module retirement in this registry).",
        kind="cli",
    ),
    "task-show": RetirementRecord(
        name="task-show",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="(none — see task-create)",
        reason="Companion to task-create; same dead-register strip.",
        kind="cli",
    ),
    "task-sync": RetirementRecord(
        name="task-sync",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="(none — see task-create)",
        reason="Companion to task-create; same dead-register strip.",
        kind="cli",
    ),
    "bundle-create": RetirementRecord(
        name="bundle-create",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="(none — bundles are now internal to council_runtime)",
        reason="Dead register() stripped; PromptBundle creation flows through council-start instead of a standalone CLI verb.",
        kind="cli",
    ),
    "launch-create": RetirementRecord(
        name="launch-create",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="(none — LaunchEvent is internal to council_runtime)",
        reason="Dead register() stripped; launch events were never a user-facing concept post live-council.",
        kind="cli",
    ),
    "features": RetirementRecord(
        name="features",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="ingest-recent",
        reason="Orphan handler for the retired `ingest` CLI; tool-triggered incremental ingest replaced the manual feature-extraction surface.",
        kind="cli",
    ),
    "core-show": RetirementRecord(
        name="core-show",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="cat ~/.trinity/core.md",
        reason="Was: print core.md verbatim (symmetry with lens-show). Inlined — users can read the file directly; CLI surface didn't pay its keep.",
        kind="cli",
    ),
    "depth-show": RetirementRecord(
        name="depth-show",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="(none — depth-signal geometry lives in me/depth.py; no caller wanted a CLI wrapper)",
        reason="Internal vectorized LID metric inspection. No user-facing surface needed it.",
        kind="cli",
    ),
    "trust-init": RetirementRecord(
        name="trust-init",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="manual ~/.trinity/trust.toml authoring (see docs/historical/trust-mode.md)",
        reason="Trust-CLI surface deferred to v1.1 per the Phase 6 council (council_c18f739a0234aa58). The substrate ITSELF ships in v1.0 — trust.toml schema, audit.log writer, --dangerously-trust-all env-var gate — only the friendly CLI surface is deferred.",
        kind="cli",
    ),
    "trust-show": RetirementRecord(
        name="trust-show",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="trinity_local.trust.resolve_trust() Python API (see docs/historical/trust-mode.md)",
        reason="Trust-CLI surface deferred to v1.1; library inspection available programmatically until then.",
        kind="cli",
    ),
    "audit-show": RetirementRecord(
        name="audit-show",
        retired_at="2026-05-20",
        commit="1d84366",
        replacement="tail ~/.trinity/audit.log | jq (manual until v1.1)",
        reason="Trust-CLI surface deferred to v1.1; audit.log is plain JSONL readable directly. v1.1 picks up `--tier`/`--operation`/`--outcome` filter flags.",
        kind="cli",
    ),
    # ── 2026-05-18 simplification pass ──
    "stats": RetirementRecord(
        name="stats",
        retired_at="2026-05-18",
        commit="f1d46f6",
        replacement="(none — launchpad surfaces the same numbers)",
        reason="Off-the-live-product-path marketing-summary CLI; killed in the simplification pass alongside `metric` and the research/ CLIs.",
        kind="cli",
    ),
    "metric": RetirementRecord(
        name="metric",
        retired_at="2026-05-18",
        commit="f1d46f6",
        replacement="dispatch_health.compute_provider_health() (Python API)",
        reason="The rate-limit-saves CTA on the launchpad retired with the rest of the rating UX; the jsonl is still written by ask.py and read by dispatch_health.py — the CLI was the user-facing surface, now gone.",
        kind="cli",
    ),
    "council-last": RetirementRecord(
        name="council-last",
        retired_at="2026-05-18",
        commit="65b45a8",
        replacement="(none — launchpad filter chips surface recent councils)",
        reason="P0 onboarding shortcut absorbed by launchpad's per-source/per-week filters; users no longer need a CLI to find their last council.",
        kind="cli",
    ),
    "polish-auto-enable": RetirementRecord(
        name="polish-auto-enable",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="(none — polish-shape auto-iterate setting removed)",
        reason="Implicit auto-iteration on polish-shape tasks didn't pay for itself; users prefer explicit `council-iterate --rounds N`.",
        kind="cli",
    ),
    "polish-auto-disable": RetirementRecord(
        name="polish-auto-disable",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="(none — see polish-auto-enable)",
        reason="Companion to polish-auto-enable; both retired together.",
        kind="cli",
    ),
    "auto-chain-enable": RetirementRecord(
        name="auto-chain-enable",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="council-iterate --rounds N",
        reason="Implicit chaining replaced with explicit per-invocation `--rounds` flag; the setting was global state hiding a per-call decision.",
        kind="cli",
    ),
    "auto-chain-disable": RetirementRecord(
        name="auto-chain-disable",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="(none — see auto-chain-enable)",
        reason="Companion to auto-chain-enable; both retired together.",
        kind="cli",
    ),
    "auto-open-enable": RetirementRecord(
        name="auto-open-enable",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="(none — review-page handoff via launchpad)",
        reason="Auto-shell-`open` on council completion was noisy in dev workflows; launchpad's review-link affordance replaced it.",
        kind="cli",
    ),
    "auto-open-disable": RetirementRecord(
        name="auto-open-disable",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="(none — see auto-open-enable)",
        reason="Companion to auto-open-enable; both retired together.",
        kind="cli",
    ),
    "cache-stats": RetirementRecord(
        name="cache-stats",
        retired_at="2026-05-18",
        commit="ac8515b",
        replacement="(none — persistent embedding cache retired)",
        reason="Cache retired alongside the persistent-embeddings simplification; offline rebuild passes re-encode per run (~2 min on 50k prompts).",
        kind="cli",
    ),
    "cache-clear": RetirementRecord(
        name="cache-clear",
        retired_at="2026-05-18",
        commit="ac8515b",
        replacement="(none — see cache-stats)",
        reason="Companion to cache-stats; cache no longer exists to clear.",
        kind="cli",
    ),
    "doctor": RetirementRecord(
        name="doctor",
        retired_at="2026-05-18",
        commit="702da53",
        replacement="status",
        reason="`status` absorbed the role; one canonical health-check entry",
        kind="cli",
    ),
    "watch-once": RetirementRecord(
        name="watch-once",
        retired_at="2026-05-18",
        commit="64c85fc",
        replacement="ingest-recent",
        reason="Tool-triggered ingest (Chrome ext + MCP ask) replaces watcher daemon model",
        kind="cli",
    ),
    "watch-loop": RetirementRecord(
        name="watch-loop",
        retired_at="2026-05-18",
        commit="64c85fc",
        replacement="ingest-recent",
        reason="See watch-once; same daemon-subsystem retirement",
        kind="cli",
    ),
    "distill": RetirementRecord(
        name="distill",
        retired_at="2026-05-18",
        commit="3a1734b",
        replacement="dream",
        reason="dream Phase 5 refreshes core.md; standalone distill CLI hidden",
        kind="cli",
    ),
    "bootstrap-pairs": RetirementRecord(
        name="bootstrap-pairs",
        retired_at="2026-05-18",
        commit="ac75b51",
        replacement="dream",
        reason="dream Phase 2.5 subsumes pair-mining",
        kind="cli",
    ),
    "me-build": RetirementRecord(
        name="me-build",
        retired_at="2026-05-12",
        commit="a3bd491",
        replacement="lens-build",
        reason="Tier 1 #2 rename: me/persona → lens",
        kind="cli",
    ),
    "merges-show": RetirementRecord(
        name="merges-show",
        retired_at="2026-05-18",
        commit="03860a6",
        replacement="",
        reason="Internal CLI with no skill/launchpad callers; killed in simplification iter 7",
        kind="cli",
    ),
    "get_eval_summary": RetirementRecord(
        name="get_eval_summary",
        retired_at="2026-05-18",
        commit="6a7f176",
        replacement="ask + get_picks",
        reason="Agents ground 'which model is best for me at X' via the v1.5 trio (ask + picks); the eval-summary surface remains on the launchpad card and `eval-show` CLI for direct user inspection. Bundled into the auto-chain settings retirement (commit 6a7f176).",
        kind="mcp_tool",
    ),
    # ── 2026-05-17 macOS Shortcut + Trinity.app retirement ──
    "shortcut-install": RetirementRecord(
        name="shortcut-install",
        retired_at="2026-05-17",
        commit="ab36d86",
        replacement="install-extension",
        reason="Chrome extension is the cross-platform dispatch path",
        kind="cli",
        artifact_persists=True,  # macOS Shortcut on existing installs still works
    ),
    "install-app": RetirementRecord(
        name="install-app",
        retired_at="2026-05-17",
        commit="84c98e5",
        replacement="install-extension",
        reason="Trinity.app wrapper retired; Chrome extension is cross-platform launchpad host",
        kind="cli",
    ),
    "Trinity.app": RetirementRecord(
        name="Trinity.app",
        retired_at="2026-05-17",
        commit="84c98e5",
        replacement="install-extension",
        reason="osacompile .app wrapper retired pre-launch",
        kind="file",
    ),
    "shortcut_setup": RetirementRecord(
        name="shortcut_setup",
        retired_at="2026-05-17",
        commit="ab36d86",
        replacement="(none — Chrome extension Native Messaging host is the cross-platform dispatch path)",
        reason="`src/trinity_local/shortcut_setup.py` (325 LOC) held the macOS Shortcut bootstrap logic — `~/.trinity/bin/trinity-dispatch` wrapper writer + .shortcut bundle installer. Companion to the retired `shortcut-install` CLI. The Chrome extension's `capture_host.py` Native Messaging host is the cross-platform replacement; the macOS-only Shortcut dispatcher had no Linux/Windows counterpart. Sunset confirmed via the same Pass-A council that retired Trinity.app. Caught missing from the registry by the post-launch consistency loop 2026-05-23 (iter #29) — claude.md L132 narrates the retirement; the registry should too.",
        kind="module",
    ),
    "dispatch_runner": RetirementRecord(
        name="dispatch_runner",
        retired_at="2026-05-17",
        commit="ab36d86",
        replacement="(none — superseded by capture_host.py Native Messaging dispatch)",
        reason="`src/trinity_local/dispatch_runner.py` (60 LOC) was the runtime executor for the macOS Shortcut dispatcher — read the action manifest, spawn the CLI subprocess, write back the result. Same retirement scope as `shortcut_setup`: cross-platform replacement is the Chrome extension's `capture_host.py` action handler.",
        kind="module",
    ),
    "doctor.py": RetirementRecord(
        name="doctor.py",
        retired_at="2026-05-27",
        commit="(pending — landing now with parasitism-audit cuts)",
        replacement="src/trinity_local/health_checks.py (same engine, accurate name)",
        reason="The `doctor` CLI verb was collapsed into `status` pre-launch (commit 702da53); the engine module survived under its old name `doctor.py` (881 LOC). The 2026-05-27 parasitism audit flagged this as a misleading-name parasitism: any reader seeing `doctor.py` reasonably expected retired code, but it's actually the live library `status` calls into. Renamed `src/trinity_local/doctor.py` → `src/trinity_local/health_checks.py` 2026-05-27. Test files renamed in lockstep: `test_doctor.py` → `test_health_checks.py`, `test_doctor_browser_capture.py` → `test_health_checks_browser_capture.py`. All import sites updated. This entry uses the `.py` suffix to disambiguate from the `doctor` CLI-verb entry above (which retired 2026-05-18).",
        kind="file",
    ),
    "commands.seed": RetirementRecord(
        name="commands.seed",
        retired_at="2026-05-27",
        commit="(pending — landing now alongside the ingest_helpers consolidation)",
        replacement="trinity-local import-export (auto-detects ChatGPT / claude.ai / Gemini Takeout at any path)",
        reason="`src/trinity_local/commands/seed.py` (339 LOC) shipped the `seed-from-taste-terminal` CLI which required a personal-rig directory layout `~/projects/taste-terminal/data/exports/{claude_ai,chatgpt-*,gemini_takeout}/` that no end user has. The replacement `import-export` (task #148, shipped) auto-detects export type from any path. Confusingly, `import-export` was importing 3 helpers (`_existing_prompt_node_ids`, `_stage_session`, `_flush_chunk`) FROM the dead seed module — meaning the live CLI parasitically depended on the retired one. This retirement consolidates the helpers into `src/trinity_local/ingest_helpers.py` (public names: `existing_prompt_node_ids`, `stage_session`, `flush_chunk`) using the OPTIMIZED `iter_prompt_nodes_no_embedding` variant that `incremental_ingest.py` had been using locally. Both call sites now import from the shared module. Net: -339 LOC + slow-variant-fixed in import-export's hot path.",
        kind="module",
    ),
    "seed-from-taste-terminal": RetirementRecord(
        name="seed-from-taste-terminal",
        retired_at="2026-05-27",
        commit="(pending — landing now alongside the ingest_helpers consolidation)",
        replacement="trinity-local import-export <path>",
        reason="The `trinity-local seed-from-taste-terminal` CLI verb retired alongside its handler module. End-user-facing replacement is `import-export` which auto-detects export type at any path.",
        kind="cli",
    ),
    "commands.replay": RetirementRecord(
        name="commands.replay",
        retired_at="2026-05-27",
        commit="b641f55",
        replacement="(none — the personal routing table populates from normal council usage via compute_personal_routing_table())",
        reason="`src/trinity_local/commands/replay.py` (298 LOC, 1 test) registered the `replay-history` CLI verb. Cut as a MEDIUM-confidence parasitism: a power-user verb to replay outcomes against current routing, whose signal (would the new router agree with old verdicts?) isn't surfaced anywhere user-facing. Removed from launchpad UI cold-start CTA (now points at council-launch) + memory-viewer empty state. The natural way to populate the routing table is to use Trinity normally; council outcomes accumulate on disk and aggregate via personal_routing.compute_personal_routing_table().",
        kind="module",
    ),
    "replay-history": RetirementRecord(
        name="replay-history",
        retired_at="2026-05-27",
        commit="b641f55",
        replacement="trinity-local council-launch (run real councils; routing table builds organically)",
        reason="The `trinity-local replay-history` CLI verb retired alongside its handler. The launchpad cold-start CTA now points users at running real councils instead of backfilling from history.",
        kind="cli",
    ),
    "commands.decision_log": RetirementRecord(
        name="commands.decision_log",
        retired_at="2026-05-27",
        commit="d5cdb8c",
        replacement="(none — decision_log.jsonl loader survives in me/decisions.py for back-compat)",
        reason="`src/trinity_local/commands/decision_log.py` (214 LOC, 1 test) registered the `decision-log` CLI verb shipped 2026-05-23 (task #137). Cut as a low-uptake parasitism: recently shipped but it requires the user to interactively log decisions (friction), and 3 days post-ship there was 0 evidence of organic use — the lens pipeline works without it. The `me/decisions.py` loader survives so existing `~/.trinity/me/decision_log.jsonl` files (if any user wrote some) still feed lens-build Stage 2 at weight=2.0.",
        kind="module",
    ),
    "decision-log": RetirementRecord(
        name="decision-log",
        retired_at="2026-05-27",
        commit="d5cdb8c",
        replacement="(none — see retired_names commands.decision_log)",
        reason="The `trinity-local decision-log` CLI verb retired alongside its handler module. Users wanting to log decisions can write JSONL directly to ~/.trinity/me/decision_log.jsonl by hand — the loader still reads it.",
        kind="cli",
    ),
    "commands.adapters": RetirementRecord(
        name="commands.adapters",
        retired_at="2026-05-27",
        commit="0fa5ce1",
        replacement="trinity-local status",
        reason="`src/trinity_local/commands/adapters.py` (35 LOC, 0 tests) registered the `adapters` CLI verb which printed provider-adapter discovery status. `trinity-local status` already covers the same surface and is the canonical health-check entry point. HIGH-confidence cut: the verb duplicates `status` with zero unique value. Library module `src/trinity_local/adapters.py` survives — used by status / setup_guidance / launchpad_data.",
        kind="module",
    ),
    "adapters": RetirementRecord(
        name="adapters",
        retired_at="2026-05-27",
        commit="0fa5ce1",
        replacement="trinity-local status",
        reason="The `trinity-local adapters` CLI verb retired alongside its handler module. `status` shows the same provider-adapter table.",
        kind="cli",
    ),
    "shortcuts_integration": RetirementRecord(
        name="shortcuts_integration",
        retired_at="2026-05-26",
        commit="c99044f",
        replacement="(none — call sites inline the empty-URL pattern directly; JS dispatch already skips tier-2-shortcut when URL is empty)",
        reason="`src/trinity_local/shortcuts_integration.py` (47 LOC) was the inert shim left behind when the macOS-Shortcut dispatch tier was retired 2026-05-17 (commit ab36d86). It returned empty URLs so the launchpad JS dispatch (`launchpad_runtime.js`) would skip tier-2 and route everything through the Chrome extension. The shim was kept so 6 import sites in `council_review.py` + `launchpad_data.py` didn't break. HIGH-confidence cut: the inline 'DEFAULT_SHORTCUT_NAME = \"Trinity Dispatch\"' constant + empty-string URL placeholder is two lines per call site, which is cheaper than maintaining the shim. Module deleted in this commit; call sites updated to inline the constants.",
        kind="module",
    ),
    "commands.shortcuts": RetirementRecord(
        name="commands.shortcuts",
        retired_at="2026-05-17",
        commit="ab36d86",
        replacement="(none — CLI surface retired alongside `shortcut-install`)",
        reason="`src/trinity_local/commands/shortcuts.py` (46 LOC) held the `handle_shortcut_install` / `handle_shortcut_status` CLI handlers — registered the `shortcut-install` + `shortcut-status` CLIs that the same Pass A retirement removed. Module went when its CLI surface did. Same shape as later commands/tasks + commands/trust retirements (handler-only orphan modules whose surface was already gone).",
        kind="module",
    ),
    "search_prompts": RetirementRecord(
        name="search_prompts",
        retired_at="2026-05-17",
        commit="7cc950b",
        replacement="substring + recency + replay-value heuristics",
        reason="Embedding-free hot path; ranker/heuristic.py replaces it",
        kind="mcp_tool",
    ),
    "parse_peer_review_sections": RetirementRecord(
        name="parse_peer_review_sections",
        retired_at="2026-05-06",
        commit="e5b78eb",
        replacement="parse_synthesis_sections (council_runtime.py)",
        reason="Council pipeline simplification — the v1.0 peer-review subsystem (`render_peer_review_prompt` + `parse_peer_review_sections` + `parse_ranking_labels` + `aggregate_peer_rankings`) was an extra round before synthesis, retired in the v1.1 4-iteration council audit (commit e5b78eb). The surviving `parse_synthesis_sections` (hardened to case-insensitive + numbered variants + `raw` fallback) is the live parser. Until now it wasn't formally in the registry. Caught by the post-launch consistency loop 2026-05-23 (iter #30) — same doc-vs-registry cross-reference probe that caught iter #29's `shortcut_setup`/`dispatch_runner`/`commands.shortcuts` gap.",
        kind="function",
    ),
    "watcher_dir": RetirementRecord(
        name="watcher_dir",
        retired_at="2026-05-18",
        commit="db5cc78",
        replacement="(none — watcher subsystem killed; MCP `ask` triggers ingestion passively via cursor-based incremental ingest in `watch_runtime.py`)",
        reason="`watcher_dir()` returned `~/.trinity/watcher/` — the cursor directory for the v1.0 `watch-once` / `watch-loop` CLIs (which were also retired in the same commit). Removed in db5cc78 as part of the state_paths lean (sweep iters Q+R+S+U). state_paths.py L217 still narrates the retirement in a comment but the registry never recorded it. Caught by the source-code-comment-vs-registry probe (iter #31) — same drift class as iters #29 + #30 (registry coverage gap behind retirement-narrating prose).",
        kind="function",
    ),
    "trinity-dispatch": RetirementRecord(
        name="trinity-dispatch",
        retired_at="2026-05-17",
        commit="ab36d86",
        replacement="trinity-local-capture-host",
        reason="Chrome extension Native Messaging replaces the macOS Shortcut dispatcher",
        kind="file",
    ),
    "guess_task_kind": RetirementRecord(
        name="guess_task_kind",
        retired_at="2026-05-20",
        commit="0fdceef",
        replacement="guess_task_type",
        reason="Back-compat alias from task #92 (Tier 1 #3 task_kind → task_type rename). Tick 48 audit found ZERO callers in src/ or tests/ — the comment claiming 'external callers (and a handful of tests) still import' was already untrue at the time of writing. Trinity has no external SDK consumers; internal callers all use guess_task_type directly. Pure dead aliasing.",
        kind="module",
    ),
    "default_task_kind": RetirementRecord(
        name="default_task_kind",
        retired_at="2026-05-20",
        commit="a0b054b",
        replacement="(removed, never consumed downstream)",
        reason="`task_kind` was renamed to `task_type` in task #92 (Tier 1 #3), but the `default_task_kind` config field survived as a parsed-but-never-read remnant in AppConfig. No code path read `config.default_task_kind` after the rename — pure dead state. Tick 47 swept it out of the dataclass, config.json, config.example.json, and 5 tests.",
        kind="config_field",
    ),
    "commands.ingest": RetirementRecord(
        name="commands.ingest",
        retired_at="2026-05-20",
        commit="bfe939e",
        replacement="(none — orphan handler for retired `features` CLI)",
        reason="`commands/ingest.py` was the retired-CLI shim for `trinity-local features` (kept importable but unregistered in main.py per pre-launch simplification). Imported from `..feature_extractors`, which tick 57 deleted alongside the v2 trained-coordinator sunset. Tick 58 found the broken import via `python -c 'from trinity_local.commands import ingest'`. Pattern #4 audit-for-shape: tick 57's relative-import grep used 1-dot patterns and missed the 2-dot `from ..feature_extractors` import. The whole file was dead even before the broken import — main.py registered no handler, no tests, no smoke. Deleted cleanly.",
        kind="module",
    ),
    "training_schema": RetirementRecord(
        name="training_schema",
        retired_at="2026-05-20",
        commit="77c14d3",
        replacement="(none — v2 trained-coordinator path sunset 2026-05-11)",
        reason="262-LOC module defining the v2 trained-coordinator data schema (TranscriptWindow, RoutingExample, etc). The v2 path was sunset 2026-05-11 per claude.md ('reopens only if v1.5 hits a quality ceiling'). Tick 57 audit: ZERO callers across src/ and tests/ — feature_extractors.py was the only importer, and it itself had zero importers. Same shape as the Loop Constitution substrate that was deleted in pre-launch simplification. Architecture preserved in git history + docs/spec-v2.md sunset header.",
        kind="module",
    ),
    "feature_extractors": RetirementRecord(
        name="feature_extractors",
        retired_at="2026-05-20",
        commit="77c14d3",
        replacement="(none — v2 trained-coordinator path sunset)",
        reason="261-LOC module wrapping the training_schema dataclasses for feature extraction. Zero importers across src/ and tests/. Deleted alongside training_schema.py — same v2-substrate-after-sunset shape.",
        kind="module",
    ),
    "hard_examples_dir": RetirementRecord(
        name="hard_examples_dir",
        retired_at="2026-05-20",
        commit="ce37976",
        replacement="(inlined as `research_dir() / 'hard_examples'`)",
        reason="Same Pattern #4 shape as cortex_dir / models_dir — mkdir'd `~/.trinity/research/hard_examples/` on every call but zero callers. The single consumer (knn_advisor.py L60-61) constructs the path inline from `research_dir()`. Tick 52 deleted the helper.",
        kind="module",
    ),
    "replay_examples_dir": RetirementRecord(
        name="replay_examples_dir",
        retired_at="2026-05-20",
        commit="ce37976",
        replacement="(inlined as `research_dir() / 'examples'`)",
        reason="Misleading name: returned `research_dir() / 'examples'` (NOT 'replay_examples'). Zero callers; knn_advisor.py L60-61 inlines the path string. Tick 52 deleted alongside hard_examples_dir.",
        kind="module",
    ),
    "cortex_dir": RetirementRecord(
        name="cortex_dir",
        retired_at="2026-05-20",
        commit="7312dab",
        replacement="(none — picks.json carries failure_modes + successful_prompts inline)",
        reason="Spec-v1.5 originally described a `~/.trinity/cortex/` subdirectory with separate failure_modes.json + successful_prompts.json files. The shipped architecture embeds those fields INSIDE each RoutingPattern entry in scoreboard/picks.json — no separate cortex/ directory was ever needed. `cortex_dir()` survived as a state_paths helper that mkdir'd an empty `~/.trinity/cortex/` on every call (same ghost-dir shape as the retired `models_dir()` from tick 28). Zero callers in src/ or tests/. Tick 51 deleted the helper.",
        kind="module",
    ),
    "~/.trinity/cortex/": RetirementRecord(
        name="~/.trinity/cortex/",
        retired_at="2026-05-20",
        commit="7312dab",
        replacement="~/.trinity/scoreboard/picks.json (inline failure_modes / successful_prompts)",
        reason="Spec-v1.5 § L133-134 described `~/.trinity/cortex/failure_modes.json` + `~/.trinity/cortex/successful_prompts.json` as v1.5 NEW files. The shipped architecture put both fields inline in scoreboard/picks.json's RoutingPattern records. The cortex/ subdirectory was never written by any code path beyond the (zero-caller) `cortex_dir()` helper. Tick 51 sunset both.",
        kind="file",
    ),
    "~/.trinity/analytics/watch_errors.jsonl": RetirementRecord(
        name="~/.trinity/analytics/watch_errors.jsonl",
        retired_at="2026-05-20",
        commit="753c74b",
        replacement="(none)",
        reason="Watcher subsystem retired 2026-05-17; no writer remains. Tick 44 swept the dead `_watch_error_summary` reader + status CLI surface that read the never-written file.",
        kind="file",
    ),
    # ── Earlier renames (Tier 1/2 simplification) ──
    "task_kind": RetirementRecord(
        name="task_kind",
        retired_at="2026-05-12",
        commit="e7d3238",
        replacement="task_type",
        reason="Tier 1 #3 rename to disambiguate from category (LMArena grouping)",
        kind="concept",
    ),
    "persona.md": RetirementRecord(
        name="persona.md",
        retired_at="2026-05-12",
        commit="a3bd491",
        replacement="lens.md",
        reason="Tier 1 #2 rename: me/persona → lens",
        kind="file",
    ),
    "TranscriptNode": RetirementRecord(
        name="TranscriptNode",
        retired_at="2026-05-12",
        commit="e5b78eb",
        replacement="PromptNode + TurnWindow",
        reason="Tier 2 #5: memory tier collapsed to 2 from 3",
        kind="concept",
    ),
    "judge": RetirementRecord(
        name="judge",
        retired_at="2026-05-12",
        commit="e5b78eb",
        replacement="run_council(responses=[...])",
        reason="Tier 1 #2: pre-supplied member outputs go straight to chairman synthesis",
        kind="mcp_tool",
    ),
    "personal_routing_table.json": RetirementRecord(
        name="personal_routing_table.json",
        retired_at="2026-05-12",
        commit="e5b78eb",
        replacement="compute_personal_routing_table() on demand",
        reason="Tier 1 #3: computed from council_outcomes/ at read time, no durable state",
        kind="file",
    ),
    # ── 2026-05-28 EXTRACT-unification Stage 4b: legacy split retired (#209) ──
    # The two legacy stores collapsed into the unified preference_acts.jsonl
    # ledger. RejectionSignal / Decision dataclasses survive (Stage 0/2 produce
    # them in memory; pair_mining/post-filter consume them) — only the disk
    # persistence retired. The ledger is now the sole store.
    "save_rejections": RetirementRecord(
        name="save_rejections",
        retired_at="2026-05-28",
        commit="",
        replacement="save_preference_acts (me/preference_acts.py)",
        reason="EXTRACT Stage 4b (#209): rejections.jsonl retired. Stage 0 rejections flow in-memory into the unified ledger; the #194 clobber guard now lives on save_preference_acts.",
        kind="function",
    ),
    "load_rejections": RetirementRecord(
        name="load_rejections",
        retired_at="2026-05-28",
        commit="",
        replacement="iter_preference_acts() (model_miss subset)",
        reason="EXTRACT Stage 4b (#209): the ledger is the sole store; iter_preference_acts reads it.",
        kind="function",
    ),
    "rejections_path": RetirementRecord(
        name="rejections_path",
        retired_at="2026-05-28",
        commit="",
        replacement="preference_acts_path()",
        reason="EXTRACT Stage 4b (#209): rejections.jsonl retired in favor of preference_acts.jsonl.",
        kind="function",
    ),
    "save_decisions": RetirementRecord(
        name="save_decisions",
        retired_at="2026-05-28",
        commit="",
        replacement="save_preference_acts (me/preference_acts.py)",
        reason="EXTRACT Stage 4b (#209): decisions.jsonl retired. Stage 2 decisions flow in-memory into the unified ledger.",
        kind="function",
    ),
    "load_decisions": RetirementRecord(
        name="load_decisions",
        retired_at="2026-05-28",
        commit="",
        replacement="iter_preference_acts() (self_expressed subset)",
        reason="EXTRACT Stage 4b (#209): the ledger is the sole store.",
        kind="function",
    ),
    "decisions_path": RetirementRecord(
        name="decisions_path",
        retired_at="2026-05-28",
        commit="",
        replacement="preference_acts_path()",
        reason="EXTRACT Stage 4b (#209): decisions.jsonl retired in favor of preference_acts.jsonl. (decision_log.jsonl + decision_log_path survive — separate live-capture log.)",
        kind="function",
    ),
    # ── 2026-06-05 user-pick layer removal ──
    # Per user direction "let's go with the chairman pick and remove anything
    # that refers to my picks — I don't want users to get another task where
    # they need to pick the model they like. They just chat without changing
    # any of their behavior." The whole user-veto/verdict/acceptance layer was
    # removed so the chairman's pick stands alone. get_picks / picks.json /
    # personal_routing survive — those ARE the chairman's accumulated picks.
    "mark_pick_wrong": RetirementRecord(
        name="mark_pick_wrong",
        retired_at="2026-06-05",
        commit="",
        replacement="(none — the chairman's pick is the supervision signal; there is no user-veto. get_picks introspects the chairman-derived routing rules.)",
        reason="MCP tool that let the user veto a cortex routing rule, incrementing override_count and halving effective_trust per click. Removed with the rest of the user-pick layer: asking the user to mark a pick wrong is exactly the 'one more task on them' the founder removed. The chairman's pick IS the verdict; consolidate re-derives the rules from council winners. Tool count 9→8.",
        kind="mcp_tool",
    ),
    "cortex-override": RetirementRecord(
        name="cortex-override",
        retired_at="2026-06-05",
        commit="",
        replacement="(none — re-run `consolidate` to re-derive routing rules from council outcomes; there is no manual override.)",
        reason="CLI mirror of mark_pick_wrong — flagged a routing rule wrong and halved its effective trust per invocation, persisting override_count across consolidations. Removed with the user-pick layer (override_count, effective_trust, route_outcome.py, council_feedback all went 2026-06-05). The user never picks/vetoes a model; the chairman decides. CLI count 50→49.",
        kind="cli",
    ),
    "consolidate-flags-audit-centroids": RetirementRecord(
        name="consolidate-flags-audit-centroids",
        retired_at="2026-06-06",
        commit="",
        replacement="`trinity-local consolidate` (now LLM-free, no flags but `--dry-run`).",
        reason="The cortex collapse (#298, council_0dd6ee69698d620b) replaced the v1.5 cortex ENGINE — an LLM flagship rule-extractor + 6-component TrustScore + a SEPARATE per-basin cortex centroid (#277 stale-space bug) — with a deterministic per-lens-basin recency-weighted chairman-winner tally (lens_routing.compute_basin_routing). The `consolidate` CLI flags that only made sense for that engine were removed: `--audit` / `--audit-provider` (the independent-chairman audit pass that fed the audit_score trust component), `--centroids-only` (recompute the separate cortex centroids in the live embedding space — the cheap #277 fix, now structurally unnecessary since routing reads the lens's always-live centroids), `--min-basin-size` / `--basin` / `--provider` (per-basin LLM-extraction gating). The retired cortex symbols (RoutingPattern, TrustScore, RoutingRule, FailureModes, compute_trust_score, consolidate_basin, consolidate_all, make_flagship_extractor, make_rule_auditor, refresh_centroids, AUDIT_SCORE_MAP, TRUST_USE_RULE/TRUST_KNN_FALLBACK) and the MCP picks-store schema (trust_score / routing_rule / basin_centroid / winner_distribution / failure_modes / audit_status / centroid_embedder) went with them — picks.json is now the flat `{basin_id: {winner, count, margin, n_episodes, evidence}}`.",
        kind="concept",
    ),
}


def get(name: str) -> RetirementRecord | None:
    """Look up a retirement record by name (returns None if not retired)."""
    return RETIRED.get(name)


def names_by_kind(kind: RetirementKind) -> list[str]:
    """All retired names of a given kind, sorted alphabetically."""
    return sorted(n for n, r in RETIRED.items() if r.kind == kind)


def all_names() -> list[str]:
    """All retired names regardless of kind."""
    return sorted(RETIRED.keys())


def format_migration_hint(name: str) -> str:
    """Format a friendly migration hint for ``trinity-local <retired-cli>``.

    Example::

        >>> format_migration_hint("shortcut-install")
        '`shortcut-install` was retired 2026-05-17 — use `install-extension`. ...'
    """
    record = RETIRED.get(name)
    if record is None:
        return f"`{name}` is not a known retired CLI."
    msg = f"`{record.name}` was retired {record.retired_at}"
    if record.replacement:
        msg += f" — use `{record.replacement}` instead"
    msg += f". ({record.reason}.)"
    if record.artifact_persists:
        msg += " Existing installs of the retired artifact may still work."
    return msg
