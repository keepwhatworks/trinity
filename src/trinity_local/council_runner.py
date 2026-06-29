from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .council_status import (
    finalize_council_run_state,
    init_council_run_state,
    load_council_status,
    start_member_progress,
    update_member_failure,
    update_member_progress,
    update_synthesis_progress,
)
from .council_review import write_unified_council_page


def _emit_council_telemetry(outcome) -> None:
    """Fire a GA4 categorical event for the council completion.

    NEVER includes prompt text, lens text, member output bodies, or any
    free-form strings beyond the chairman's discrete labels (task_type +
    winner). Per CLAUDE.md "Architectural commitments" #2.

    Best-effort: any exception is swallowed; telemetry must never crash
    a successful council.
    """
    try:
        from .telemetry import record_event
        rl = getattr(outcome, "routing_label", None)
        task_type = (getattr(rl, "task_type", None) or "unknown") if rl else "unknown"
        winner = (getattr(rl, "winner", None) or "unknown") if rl else "unknown"
        member_count = len(getattr(outcome, "responses", []) or [])
        mode = getattr(outcome, "mode", "parallel") or "parallel"
        record_event(
            "council_complete",
            task_type=str(task_type)[:40],
            winner=str(winner)[:40],
            member_count=int(member_count),
            mode=str(mode)[:40],
        )
    except Exception:
        pass


def _maybe_auto_open(review_path) -> None:
    """Open the review page in the default browser when
    ``settings.auto_open_council`` is True. Off by default; macOS-only;
    failures swallowed (the council write already succeeded — a browser
    hiccup must not pollute the return). The auto-open-enable /
    auto-open-disable CLI was retired 2026-05-17 (commit 1fed7fc);
    flip the setting via `load_telemetry_settings()` + `save_telemetry_settings()`
    if needed.

    Tab discipline (per the user's UX ask): every council opens into a
    single named window via ``window.open(url, "trinity-council")``. The
    browser's named-window mechanism reuses the existing tab — no new
    tab per council, doesn't touch the launchpad's tab. Opened in
    background (`-g`) so it doesn't steal focus.
    """
    try:
        from .telemetry import load_telemetry_settings
        if not load_telemetry_settings().auto_open_council:
            return
        import json
        import subprocess
        import sys
        from .state_paths import portal_pages_dir

        if sys.platform != "darwin":
            return  # macOS-only — Linux/Windows silently skip

        # Stable launcher URL — same path every time, browser is more
        # likely to reuse the launcher tab too. Tiny page; sole job is to
        # call window.open into the named "trinity-council" window.
        launcher = portal_pages_dir() / "_open_council.html"
        council_url = "file://" + str(review_path)
        launcher.write_text(
            "<!DOCTYPE html><html><head>"
            "<title>Opening council…</title>"
            '<meta charset="utf-8">'
            "<script>"
            f"window.open({json.dumps(council_url)}, 'trinity-council');"
            "window.close();"
            "</script></head><body>Opening Trinity council window…</body></html>",
            encoding="utf-8",
        )
        subprocess.Popen(  # noqa: S603 — fixed binary + controlled path
            ["open", "-g", str(launcher)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return
from .council_runtime import (
    append_launch_event,
    chairman_says_converged,
    create_council_outcome,
    create_launch_event,
    create_prompt_bundle,
    load_prompt_bundle,
    parse_routing_label,
    parse_synthesis_sections,
    render_chain_step_prompt,
    render_consensus_round_prompt,
    render_member_prompt,
    render_primary_council_prompt,
    save_council_outcome,
    save_prompt_bundle,
)
from .council_schema import (
    CouncilChainStep,
    CouncilMemberResult,
    CouncilOutcome,
    LaunchEvent,
    PromptBundle,
)
from .providers import (
    ProviderError,
    describe_provider_failure,
    make_provider,
    result_hard_failed,
)
from .task_runtime import save_sync_record, save_task_record, task_from_council
from .utils import now_iso


@dataclass
class CouncilRunResult:
    outcome: CouncilOutcome
    outcome_path: Path
    review_path: Path
    launches: list[LaunchEvent]
    task_path: Path | None = None
    sync_path: Path | None = None


def _log_routing_label_event(
    *,
    bundle_id: str,
    primary_provider: str,
    primary_model: str | None,
    success: bool,
    error: str | None,
    synthesis_error: bool,
) -> None:
    """Append a one-line event so we can track Chairman parse-success rate.

    Phase 8.7 success criterion: ≥85%. If this drops, the Chairman prompt
    needs revision or extraction needs to fall back to a smaller LLM.
    """
    try:
        from .state_paths import analytics_dir
        from .utils import now_iso

        path = analytics_dir() / "routing_label_events.jsonl"
        record = {
            "ts": now_iso(),
            "bundle_id": bundle_id,
            "primary_provider": primary_provider,
            "primary_model": primary_model,
            "success": bool(success),
            "synthesis_error": synthesis_error,
        }
        if error:
            record["error"] = error
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        # Analytics never crash the council
        pass


def _provider_model(config, override: str | None) -> str | None:
    if override:
        return override
    if config is None:
        return None
    return config.model


def _synthesize_with_fallback(
    prompt, config, primary_provider, primary_model, cwd, state_token
):
    """Run the chairman synthesis with provider fallback — when the primary
    chair (default Claude) raises or returns an unusable result (tokens
    exhausted / rate-limited), fall through to the other enabled providers so
    the council still produces a verdict instead of failing.

    Returns ``(output, sections, error, chair_provider, chair_model)``. The
    chair fields reflect whoever ACTUALLY synthesized (reassigned on fallback)
    so the outcome is honest. Always flips the synthesis progress to 'done'.
    """
    from .providers import run_with_chairman_fallback

    output = ""
    sections: dict[str, str] = {}
    error: str | None = None
    chair, model = primary_provider, primary_model
    # Pass council_runner's make_provider so tests patching it (+ the council
    # dispatch path) flow through the fallback unchanged.
    res, used, ferr = run_with_chairman_fallback(
        prompt, config, primary_provider, cwd, provider_factory=make_provider,
    )
    try:
        if res is not None:
            if used and used != primary_provider:
                chair = used
                model = _provider_model(config.providers.get(used), None)
            output = res.stdout or res.stderr or ""
            sections = parse_synthesis_sections(output)
        else:
            error = ferr or "all chairmen unavailable (rate-limited / exhausted?)"
    finally:
        update_synthesis_progress(state_token, "done", output_text=output)
    return output, sections, error, chair, model


def _resolve_winner(
    *,
    routing_label,
    winner_section: str | None = None,
    sequence: list[str],
    label_to_provider: dict[str, str] | None = None,
) -> str | None:
    """Resolve the winning provider from the chairman's Routing JSON.

    Trusts `routing_label.winner` only. The prior implementation had two
    additional fallbacks (first line of the "Winner" prose section, A/B/C
    label mapping) that existed for chairmen which used to write prose. With
    parse-success ≥85% on Routing JSON, those fallbacks now silently mask
    parse failures rather than fix them — better to mark `winner_provider=None`
    and let the user/rater fix it explicitly.

    `winner_section` and `label_to_provider` are kept as accepted arguments
    so call sites compile, but they're ignored.
    """
    if routing_label is None:
        return None
    candidate = (getattr(routing_label, "winner", "") or "").strip().lower()
    if not candidate:
        return None
    sequence_lower = {p.lower(): p for p in sequence}
    if candidate in sequence_lower:
        return sequence_lower[candidate]
    # Substring match for cases where the chairman wrote "claude-opus" instead
    # of "claude". Tightly scoped — no prose scanning.
    for lower, name in sequence_lower.items():
        if lower in candidate or candidate in lower:
            return name
    return None


@dataclass
class MemberExecutionResult:
    provider_name: str
    provider_config: object | None = None
    output_text: str = ""
    returncode: int | None = None
    stderr: str = ""
    stdout: str = ""
    error_payload: dict[str, object] | None = None


def _run_chain(
    *,
    config: AppConfig,
    bundle: PromptBundle,
    sequence: list[str],
    primary_provider: str,
    cwd: Path,
    primary_model_override: str | None = None,
    run_state_token: str | None = None,
) -> CouncilRunResult:
    """Sequential refinement: each model in `sequence` sees prior outputs and refines.

    The final step's output is treated as the converged answer; the chairman
    synthesizes a Routing JSON over the full chain.
    """
    if not sequence:
        raise ProviderError("Chain mode requires a non-empty sequence of providers.")

    try:
        os.setpgrp()
    except OSError:
        pass

    council_id = bundle.bundle_id
    state_token = run_state_token or council_id
    chairman_config = config.providers.get(primary_provider)
    chairman_model = _provider_model(chairman_config, primary_model_override) if chairman_config else None
    if load_council_status(state_token) is None:
        member_models = {
            name: _provider_model(config.providers.get(name), None)
            for name in sequence
            if config.providers.get(name) is not None
        }
        init_council_run_state(
            state_token,
            task_text=bundle.task_text,
            bundle_id=bundle.bundle_id,
            council_id=council_id,
            members=list(sequence),
            runner_pid=os.getpid(),
            runner_pgid=os.getpgid(0),
            member_models=member_models,
            metadata={
                "kind": "council",
                "mode": "chain",
                "sequence": sequence,
                "chairman_provider": primary_provider,
                "chairman_model": chairman_model,
            },
        )
        try:
            from .council_runtime import register_pending_round
            register_pending_round(
                chain_root_id=bundle.bundle_id,
                bundle_id=bundle.bundle_id,
                status_token=state_token,
                round_number=1,
            )
        except Exception:
            pass

    chain_steps: list[CouncilChainStep] = []
    failed_steps: list[dict[str, object]] = []
    launches: list[LaunchEvent] = []

    import time

    for step_index, provider_name in enumerate(sequence):
        is_final = step_index == len(sequence) - 1
        step_prompt = render_chain_step_prompt(
            bundle,
            step_index=step_index,
            prior_steps=chain_steps,
            is_final=is_final,
        )
        provider_config = config.providers.get(provider_name)
        if provider_config is None or not provider_config.enabled:
            update_member_failure(state_token, provider_name, "Provider missing or disabled.")
            failed_steps.append({
                "step_index": step_index,
                "provider": provider_name,
                "stage": "chain_step",
                "reason": "provider_missing_or_disabled",
            })
            break

        provider = make_provider(provider_config)
        start_member_progress(state_token, provider_name)
        started_at = now_iso()
        t0 = time.time()
        try:
            result = provider.run(step_prompt, cwd)
        except Exception as exc:
            elapsed = time.time() - t0
            update_member_failure(state_token, provider_name, str(exc))
            failed_steps.append({
                "step_index": step_index,
                "provider": provider_name,
                "stage": "chain_step",
                "reason": "exception",
                "error": str(exc),
                "latency_seconds": elapsed,
            })
            break

        elapsed = time.time() - t0
        # A rate-limited / token-exhausted CLI exits non-zero with EMPTY stdout
        # (its quota error goes to stderr — codex prints "You've hit your usage
        # limit", claude/agy similarly). The PARALLEL member path (_run_member,
        # ~L666) already treats that as a FAILED member; chain mode must too.
        # Without this, `result.stdout or result.stderr` below feeds the quota
        # ERROR STRING forward as THIS step's answer — poisoning the next chain
        # step, the chairman synthesis, AND the re-ingested corpus. Mirror the
        # parallel gate exactly: fail the step + break (same shape as the
        # exception path above), so the broken chain synthesizes from the real
        # steps it has rather than from an error masquerading as a model reply.
        if result_hard_failed(result):
            why = describe_provider_failure(
                result.stdout, result.stderr, result.returncode, provider=provider_name
            )
            update_member_failure(state_token, provider_name, why)
            failed_steps.append({
                "step_index": step_index,
                "provider": provider_name,
                "stage": "chain_step",
                "reason": "nonzero_returncode_without_stdout",
                "returncode": result.returncode,
                "error": why[:200],
                "latency_seconds": elapsed,
            })
            break

        output_text = result.stdout or result.stderr or ""
        update_member_progress(state_token, provider_name, output_text)

        chain_steps.append(CouncilChainStep(
            step_index=step_index,
            model_provider=provider_name,
            model_name=_provider_model(provider_config, primary_model_override if is_final else None),
            input_text=step_prompt,
            output_text=output_text,
            latency_seconds=elapsed,
            started_at=started_at,
            completed_at=now_iso(),
            metadata={"returncode": result.returncode, "stderr": result.stderr},
        ))
        launches.append(create_launch_event(
            bundle=bundle,
            mode="council_chain",
            source_provider=bundle.origin_provider,
            target_provider=provider_name,
            target_model=chain_steps[-1].model_name,
            handoff_reason=f"chain_step_{step_index + 1}_of_{len(sequence)}",
            metadata={"step_index": step_index, "is_final": is_final},
        ))
        append_launch_event(launches[-1])

    if not chain_steps:
        raise ProviderError(f"All chain steps failed: {failed_steps}")

    # Build a synthetic CouncilMemberResult per chain step so the chairman
    # synthesis prompt format works unchanged.
    member_results = [
        CouncilMemberResult(
            provider=step.model_provider,
            model=step.model_name,
            session_id=None,
            output_text=step.output_text,
            metadata={"chain_step_index": step.step_index, "latency_seconds": step.latency_seconds},
        )
        for step in chain_steps
    ]

    primary_config = config.providers.get(primary_provider)
    if primary_config is None or not primary_config.enabled:
        raise ProviderError(f"Unknown or disabled primary provider: {primary_provider}")
    primary_model = _provider_model(primary_config, primary_model_override)
    synthesis_prompt = render_primary_council_prompt(bundle, member_results)

    update_synthesis_progress(state_token, "running")
    # Chairman fallback (#fallback): if the primary chair is rate-limited /
    # token-exhausted, synthesize with the next enabled provider instead of
    # failing. `primary_provider`/`primary_model` are reassigned to whoever
    # actually chaired so the outcome records the truth.
    synthesis_output, sections, synthesis_error, primary_provider, primary_model = (
        _synthesize_with_fallback(
            synthesis_prompt, config, primary_provider, primary_model, cwd, state_token
        )
    )

    differences = []
    if "differences" in sections:
        differences = [
            line.strip("- ").strip()
            for line in sections["differences"].splitlines()
            if line.strip()
        ]
    routing_label, routing_label_error = parse_routing_label(synthesis_output)
    if routing_label is not None:
        try:
            update_synthesis_progress(state_token, "done", output_text=synthesis_output, routing_label=routing_label.to_dict())
        except Exception:
            pass
    _log_routing_label_event(
        bundle_id=bundle.bundle_id,
        primary_provider=primary_provider,
        primary_model=primary_model,
        success=routing_label is not None,
        error=routing_label_error,
        synthesis_error=bool(synthesis_error),
    )

    # Resolve the winner. Trust the structured Routing JSON FIRST — it's the
    # chairman's explicit verdict. Only fall back to text-scanning the
    # "Winner" section when the routing label is missing, because the
    # narrative often mentions losing providers in passing ("claude argued
    # against codex's pick…") and the first-substring-match heuristic was
    # picking up those mentions instead of the real winner.
    winner_provider = _resolve_winner(
        routing_label=routing_label,
        winner_section=sections.get("winner"),
        sequence=[*sequence, primary_provider],
    )

    final_metadata: dict = {
        "cwd": str(cwd),
        "mode": "chain",
        "sequence": sequence,
        "failed_steps": failed_steps,
    }
    if synthesis_error:
        final_metadata["synthesis_error"] = synthesis_error
    if routing_label_error:
        final_metadata["routing_label_error"] = routing_label_error

    final_outcome = create_council_outcome(
        bundle=bundle,
        primary_provider=primary_provider,
        member_results=member_results,
        primary_model=primary_model,
        winner_provider=winner_provider,
        differences=differences,
        synthesis_output=synthesis_output,
        synthesis_prompt=synthesis_prompt,
        routing_label=routing_label,
        mode="chain",
        chain_steps=chain_steps,
        metadata=final_metadata,
    )
    outcome_path = save_council_outcome(final_outcome)
    review_path = write_unified_council_page(bundle, final_outcome)
    _maybe_auto_open(review_path)
    _emit_council_telemetry(final_outcome)

    task = task_from_council(
        bundle=bundle,
        outcome=final_outcome,
        review_page_path=str(review_path),
        launch_ids=[launch.launch_id for launch in launches],
    )
    task_path = save_task_record(task)
    sync_path = save_sync_record(task)

    finalize_council_run_state(
        state_token,
        status="completed",
        council_id=final_outcome.council_run_id,
        review_path=str(review_path),
    )

    return CouncilRunResult(
        outcome=final_outcome,
        outcome_path=outcome_path,
        review_path=review_path,
        launches=launches,
        task_path=task_path,
        sync_path=sync_path,
    )


def run_council(
    *,
    config: AppConfig,
    bundle: PromptBundle,
    member_providers: list[str],
    primary_provider: str,
    cwd: Path,
    member_model_overrides: dict[str, str] | None = None,
    primary_model_override: str | None = None,
    run_state_token: str | None = None,
    mode: str = "parallel",
    sequence: list[str] | None = None,
) -> CouncilRunResult:
    # #251 (Auto-Dream analog): a council launch is the "usage" trigger for the
    # stale ingest+embed pass — gated on a 24h marker + cross-process lock, run
    # in a daemon thread, so it never blocks the members dispatching below. The
    # not-due cost is one marker read.
    from .stale_pass import maybe_kick_stale_pass

    maybe_kick_stale_pass(trigger="run_council")
    if mode == "chain":
        # `sequence is None` → caller didn't specify, default to members.
        # `sequence == []` → caller passed an empty list explicitly; let
        #   _run_chain's non-empty validation reject it loudly. Collapsing
        #   `[]` to members hides the caller's bug.
        effective_sequence = member_providers if sequence is None else sequence
        return _run_chain(
            config=config,
            bundle=bundle,
            sequence=effective_sequence,
            primary_provider=primary_provider,
            cwd=cwd,
            primary_model_override=primary_model_override,
            run_state_token=run_state_token,
        )
    member_model_overrides = member_model_overrides or {}
    member_results: list[CouncilMemberResult] = []
    launches: list[LaunchEvent] = []

    failed_members: list[str] = []
    member_failures: list[dict[str, object]] = []

    try:
        os.setpgrp()
    except OSError:
        pass

    council_id = bundle.bundle_id
    state_token = run_state_token or council_id
    # Resolve chairman info up front so the live page can render it before
    # synthesis even starts.
    chairman_config = config.providers.get(primary_provider)
    chairman_model = _provider_model(chairman_config, primary_model_override) if chairman_config else None
    if load_council_status(state_token) is None:
        member_models = {
            name: _provider_model(config.providers.get(name), None)
            for name in member_providers
            if config.providers.get(name) is not None
        }
        init_council_run_state(
            state_token,
            task_text=bundle.task_text,
            bundle_id=bundle.bundle_id,
            council_id=council_id,
            members=member_providers,
            runner_pid=os.getpid(),
            runner_pgid=os.getpgid(0),
            member_models=member_models,
            metadata={
                "kind": "council",
                "chairman_provider": primary_provider,
                "chairman_model": chairman_model,
            },
        )
        # Register a pending segment so anyone who opens ?thread_id= for this
        # council mid-run (via launchpad tile or MCP-returned link) sees it
        # streaming live instead of an empty placeholder. Replaced by the
        # completed entry on save_council_outcome.
        try:
            from .council_runtime import register_pending_round
            register_pending_round(
                chain_root_id=bundle.bundle_id,
                bundle_id=bundle.bundle_id,
                status_token=state_token,
                round_number=1,
            )
        except Exception:
            pass  # observability; never block the run
    member_prompt = render_member_prompt(bundle)

    # 100-persona audit P46 fix: classify + log member dispatch failures
    # so dispatch_health.compute_health() demotes rate-limited providers
    # for the NEXT call. Before this, council failures were silent: a
    # rate-limited Codex in a council never demoted, the next ask
    # routed back to it, and the rate-limit-saves metric missed council
    # saves entirely.
    from .dispatch_errors import classify_dispatch_failure
    from .dispatch_health import log_member_failure

    def _log_council_member_failure(provider_name: str, returncode: int, stderr_text: str) -> None:
        try:
            failure = classify_dispatch_failure(
                provider=provider_name,
                returncode=returncode,
                stderr=stderr_text,
            )
            log_member_failure(
                provider=provider_name,
                council_run_id=council_id,
                failure_kind=failure.kind.value,
                stderr_excerpt=stderr_text,
            )
        except Exception:
            # Same contract as the underlying logger — observability
            # MUST NOT crash the dispatch path.
            pass

    def _run_member(provider_name: str) -> MemberExecutionResult:
        provider_config = config.providers.get(provider_name)
        if provider_config is None or not provider_config.enabled:
            update_member_failure(state_token, provider_name, "Provider missing or disabled.")
            return MemberExecutionResult(
                provider_name=provider_name,
                error_payload={
                    "provider": provider_name,
                    "stage": "member",
                    "reason": "provider_missing_or_disabled",
                },
            )

        provider = make_provider(provider_config)
        try:
            start_member_progress(state_token, provider_name)
            result = provider.run(member_prompt, cwd)
        except Exception as exc:
            error_text = str(exc)
            update_member_failure(state_token, provider_name, error_text)
            _log_council_member_failure(
                provider_name,
                returncode=getattr(exc, "returncode", 1),
                stderr_text=error_text,
            )
            return MemberExecutionResult(
                provider_name=provider_name,
                provider_config=provider_config,
                error_payload={
                    "provider": provider_name,
                    "stage": "member",
                    "reason": "exception",
                    "error": error_text,
                },
            )

        output_text = result.stdout or result.stderr or ""
        if result_hard_failed(result):
            why = describe_provider_failure(
                result.stdout, result.stderr, result.returncode, provider=provider_name
            )
            update_member_failure(state_token, provider_name, why)
            _log_council_member_failure(
                provider_name,
                returncode=result.returncode,
                stderr_text=result.stderr or "",
            )
            return MemberExecutionResult(
                provider_name=provider_name,
                provider_config=provider_config,
                returncode=result.returncode,
                stderr=result.stderr,
                stdout=result.stdout,
                error_payload={
                    "provider": provider_name,
                    "stage": "member",
                    "reason": "nonzero_returncode_without_stdout",
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                    "detail": why,
                },
            )

        update_member_progress(state_token, provider_name, output_text)
        return MemberExecutionResult(
            provider_name=provider_name,
            provider_config=provider_config,
            output_text=output_text,
            returncode=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    executions: dict[str, MemberExecutionResult] = {}
    # ContextVar propagation so the MCP active-sampling session set
    # in mcp_server.handle_call_tool reaches the worker threads.
    # Python's ThreadPoolExecutor doesn't propagate ContextVars to
    # workers by default. Each submit needs its OWN fresh context
    # copy — a single Context can only be entered once (ctx.run()
    # raises 'cannot enter context: already entered' if reused).
    import contextvars
    with ThreadPoolExecutor(max_workers=max(1, len(member_providers))) as executor:
        future_map = {
            executor.submit(
                contextvars.copy_context().run, _run_member, provider_name
            ): provider_name
            for provider_name in member_providers
        }
        for future in as_completed(future_map):
            provider_name = future_map[future]
            executions[provider_name] = future.result()

    for provider_name in member_providers:
        execution = executions[provider_name]
        if execution.error_payload is not None:
            failed_members.append(provider_name)
            member_failures.append(execution.error_payload)
            continue
        assert execution.provider_config is not None
        member = CouncilMemberResult(
            provider=provider_name,
            model=_provider_model(execution.provider_config, member_model_overrides.get(provider_name)),
            session_id=None,
            output_text=execution.output_text,
            metadata={
                "returncode": execution.returncode,
                "stderr": execution.stderr,
                "stdout": execution.stdout,
            },
        )
        member_results.append(member)
        event = create_launch_event(
            bundle=bundle,
            mode="council",
            source_provider=bundle.origin_provider,
            target_provider=provider_name,
            target_model=member.model,
            handoff_reason="council_member",
            source_session_id=bundle.origin_session_id,
            metadata={"bundle_role": "member"},
        )
        append_launch_event(event)
        launches.append(event)

    if not member_results:
        raise ProviderError(
            f"All council members failed: {failed_members}. "
            "Cannot proceed with zero successful responses."
        )

    label_to_provider = {
        f"Response {chr(ord('A') + index)}": member.provider
        for index, member in enumerate(member_results)
    }

    primary_config = config.providers.get(primary_provider)
    if primary_config is None or not primary_config.enabled:
        raise ProviderError(f"Unknown or disabled primary provider: {primary_provider}")
    primary_model = _provider_model(primary_config, primary_model_override)
    synthesis_prompt = render_primary_council_prompt(bundle, member_results)
    primary_prompt = synthesis_prompt or (render_member_prompt(bundle) if not member_results else "")

    # --- Primary synthesis with failure handling + chairman fallback ---
    update_synthesis_progress(state_token, "running")
    synthesis_failure: dict[str, object] | None = None
    # Chairman fallback: if the primary chair is rate-limited / exhausted,
    # synthesize with the next enabled provider. primary_provider/_model are
    # reassigned to whoever actually chaired.
    synthesis_output, sections, synthesis_error, primary_provider, primary_model = (
        _synthesize_with_fallback(
            primary_prompt, config, primary_provider, primary_model, cwd, state_token
        )
    )
    if synthesis_error:
        synthesis_failure = {
            "provider": primary_provider,
            "stage": "primary_synthesis",
            "reason": "all_chairmen_failed",
            "error": synthesis_error,
        }

    differences = []
    if "differences" in sections:
        differences = [
            line.strip("- ").strip()
            for line in sections["differences"].splitlines()
            if line.strip()
        ]
    needs_followup = None
    if "followup" in sections:
        follow = sections["followup"].lower()
        if "yes" in follow or "true" in follow:
            needs_followup = True
        elif "no" in follow or "false" in follow:
            needs_followup = False

    routing_label, routing_label_error = parse_routing_label(synthesis_output)
    if routing_label is not None:
        try:
            update_synthesis_progress(state_token, "done", output_text=synthesis_output, routing_label=routing_label.to_dict())
        except Exception:
            pass
    _log_routing_label_event(
        bundle_id=bundle.bundle_id,
        primary_provider=primary_provider,
        primary_model=primary_model,
        success=routing_label is not None,
        error=routing_label_error,
        synthesis_error=bool(synthesis_error),
    )

    # Trust the structured Routing JSON winner FIRST. Text-scanning the
    # narrative "Winner" section was matching losing providers mentioned in
    # passing — see _resolve_winner.
    winner_provider = _resolve_winner(
        routing_label=routing_label,
        winner_section=sections.get("winner"),
        sequence=[*member_providers, primary_provider],
        label_to_provider=label_to_provider,
    )

    final_metadata: dict = {
        "cwd": str(cwd),
        "failed_members": failed_members,
        "member_failures": member_failures,
    }
    if synthesis_error:
        final_metadata["synthesis_error"] = synthesis_error
        final_metadata["synthesis_failure"] = synthesis_failure
    else:
        # The winning chair (possibly a fallback) returned usable output.
        final_metadata["primary_returncode"] = 0
        final_metadata["primary_stderr"] = ""
        final_metadata["parsed_sections"] = sections
        # Record who ACTUALLY chaired (reassigned on fallback) for honesty.
        final_metadata["chairman_provider"] = primary_provider
    if routing_label_error:
        final_metadata["routing_label_error"] = routing_label_error

    final_outcome = create_council_outcome(
        bundle=bundle,
        primary_provider=primary_provider,
        member_results=member_results,
        primary_model=primary_model,
        agreement_score=None,
        winner_provider=winner_provider,
        winner_model=primary_model if winner_provider == primary_provider else None,
        needs_followup=needs_followup,
        differences=differences,
        synthesis_output=synthesis_output,
        synthesis_prompt=synthesis_prompt,
        routing_label=routing_label,
        metadata=final_metadata,
    )
    outcome_path = save_council_outcome(final_outcome)
    review_path = write_unified_council_page(bundle, final_outcome)
    _maybe_auto_open(review_path)

    primary_event = create_launch_event(
        bundle=bundle,
        mode="council",
        source_provider=bundle.origin_provider,
        target_provider=primary_provider,
        target_model=primary_model,
        handoff_reason="council_primary_synthesis",
        source_session_id=bundle.origin_session_id,
        metadata={"bundle_role": "primary"},
    )
    append_launch_event(primary_event)
    launches.append(primary_event)
    task = task_from_council(
        bundle=bundle,
        outcome=final_outcome,
        review_page_path=str(review_path),
        launch_ids=[launch.launch_id for launch in launches],
    )
    task_path = save_task_record(task)
    sync_path = save_sync_record(task)

    finalize_council_run_state(
        state_token,
        status="completed",
        council_id=final_outcome.council_run_id,
        review_path=str(review_path),
    )

    return CouncilRunResult(
        outcome=final_outcome,
        outcome_path=outcome_path,
        review_path=review_path,
        launches=launches,
        task_path=task_path,
        sync_path=sync_path,
    )


# ---------------------------------------------------------------------------
# Consensus-iteration chain mode (multi-round council)
# ---------------------------------------------------------------------------
# Each round = parallel council where every member sees the OTHER members'
# prior-round outputs as context and refines its answer. Chairman judges per
# round. Auto-chain stops when chairman_says_converged() OR max_rounds hit.
#
# Each round is its own CouncilOutcome with metadata.parent_council_id +
# round_number + chain_root_id so the chain is a navigable thread of outcomes.


def run_consensus_round(
    *,
    config: AppConfig,
    parent_outcome: CouncilOutcome,
    user_refinement: str | None = None,
    cwd: Path | None = None,
    primary_provider_override: str | None = None,
    primary_model_override: str | None = None,
    run_state_token: str | None = None,
) -> CouncilRunResult:
    """Run one continuation round on top of an existing council outcome.

    Each member sees the OTHER members' prior-round outputs as context and is
    asked to refine. Optionally `user_refinement` adds a new user directive
    that overrides the "refine" instruction.

    The returned CouncilOutcome has:
      - metadata.parent_council_id = parent_outcome.council_run_id
      - metadata.round_number = (parent.round_number or 1) + 1
      - metadata.chain_root_id = parent.chain_root_id or parent.council_run_id
    """
    # #251: consensus/auto-chain rounds don't pass through run_council, so they
    # carry their own stale-pass usage trigger (same marker+lock gate; no-op
    # when a pass ran within the window or one is already running).
    from .stale_pass import maybe_kick_stale_pass

    maybe_kick_stale_pass(trigger="run_consensus_round")
    parent_bundle = load_prompt_bundle(parent_outcome.bundle_id)
    cwd = cwd or Path(".").resolve()

    # The new round is its own bundle so each round is independently navigable.
    # Inherits task + goal from the parent; context_excerpt is replaced with
    # the prior round's per-provider outputs.
    round_number = int(parent_outcome.metadata.get("round_number") or 1) + 1
    # Chain root MUST be the bundle_id, not the parent's council_run_id.
    # `bundle_id` is deterministic from (task_cluster_id, task_text, goal,
    # origin_session) — so consensus rounds of the same task share it, AND it
    # matches the `?thread_id=` URL the launchpad emits for the originating
    # bundle. Using parent.council_run_id forks the chain into a manifest the
    # bundle URL never opens.
    chain_root_id = (
        parent_outcome.metadata.get("chain_root_id")
        or parent_outcome.bundle_id
    )

    new_bundle = create_prompt_bundle(
        task_cluster_id=parent_bundle.task_cluster_id,
        task_text=parent_bundle.task_text,
        context_excerpt=parent_bundle.context_excerpt,
        goal=parent_bundle.goal,
        comparison_instructions=parent_bundle.comparison_instructions,
        origin_provider=parent_bundle.origin_provider,
        origin_session_id=parent_bundle.origin_session_id,
        metadata={
            **(parent_bundle.metadata or {}),
            "parent_council_id": parent_outcome.council_run_id,
            "chain_root_id": chain_root_id,
            "round_number": round_number,
            "user_refinement": user_refinement,
        },
    )
    save_prompt_bundle(new_bundle)

    member_providers = [m.provider for m in parent_outcome.member_results if m.output_text.strip()]
    if not member_providers:
        raise ProviderError("Cannot start consensus round: parent has no successful member outputs.")
    prior_outputs: dict[str, str] = {
        m.provider: m.output_text for m in parent_outcome.member_results
    }

    state_token = run_state_token or new_bundle.bundle_id
    # For chain continuations, the user's live page is polling on `run_state_token`.
    # Always re-init so the page sees fresh "pending" member rows and the
    # chairman info gets refreshed to this round's chairman.
    chairman_for_round = primary_provider_override or parent_outcome.primary_provider
    chairman_config_for_round = config.providers.get(chairman_for_round)
    chairman_model_for_round = (
        _provider_model(chairman_config_for_round, primary_model_override)
        if chairman_config_for_round
        else None
    )
    member_models_for_round = {
        name: _provider_model(config.providers.get(name), None)
        for name in member_providers
        if config.providers.get(name) is not None
    }
    init_council_run_state(
        state_token,
        task_text=new_bundle.task_text,
        bundle_id=new_bundle.bundle_id,
        council_id=new_bundle.bundle_id,
        members=member_providers,
        runner_pid=os.getpid(),
        runner_pgid=os.getpgid(0),
        member_models=member_models_for_round,
        metadata={
            "kind": "council",
            "mode": "consensus_round",
            "round_number": round_number,
            "parent_council_id": parent_outcome.council_run_id,
            "chain_root_id": chain_root_id,
            "user_refinement": user_refinement,
            "chairman_provider": chairman_for_round,
            "chairman_model": chairman_model_for_round,
        },
    )

    # Register a pending segment in the thread manifest so anyone who opens
    # the launchpad → thread tile mid-round sees this round as a live
    # streaming segment (polling state_token), not just the prior rounds.
    # Replaced by the completed entry when save_council_outcome runs.
    try:
        from .council_runtime import register_pending_round
        register_pending_round(
            chain_root_id=chain_root_id,
            bundle_id=new_bundle.bundle_id,
            status_token=state_token,
            round_number=round_number,
            parent_council_id=parent_outcome.council_run_id,
        )
    except Exception:
        pass  # manifest write is observability; never block the round

    member_results: list[CouncilMemberResult] = []
    failed_members: list[str] = []
    member_failures: list[dict[str, object]] = []
    launches: list[LaunchEvent] = []

    def _run_member(provider_name: str) -> MemberExecutionResult:
        provider_config = config.providers.get(provider_name)
        if provider_config is None or not provider_config.enabled:
            update_member_failure(state_token, provider_name, "Provider missing or disabled.")
            return MemberExecutionResult(
                provider_name=provider_name,
                error_payload={
                    "provider": provider_name,
                    "stage": "consensus_round_member",
                    "reason": "provider_missing_or_disabled",
                },
            )

        own_prior = prior_outputs.get(provider_name, "")
        other_outputs = [
            (other, prior_outputs[other])
            for other in member_providers
            if other != provider_name
        ]
        prompt = render_consensus_round_prompt(
            new_bundle,
            round_index=round_number - 1,
            own_provider=provider_name,
            own_prior_output=own_prior,
            other_outputs=other_outputs,
            user_refinement=user_refinement,
        )

        provider = make_provider(provider_config)
        try:
            start_member_progress(state_token, provider_name)
            result = provider.run(prompt, cwd)
        except Exception as exc:
            error_text = str(exc)
            update_member_failure(state_token, provider_name, error_text)
            return MemberExecutionResult(
                provider_name=provider_name,
                provider_config=provider_config,
                error_payload={
                    "provider": provider_name,
                    "stage": "consensus_round_member",
                    "reason": "exception",
                    "error": error_text,
                },
            )

        output_text = result.stdout or result.stderr or ""
        if result.returncode != 0 and not (result.stdout or "").strip():
            update_member_failure(state_token, provider_name, result.stderr or f"Exited with code {result.returncode}.")
            return MemberExecutionResult(
                provider_name=provider_name,
                provider_config=provider_config,
                returncode=result.returncode,
                stderr=result.stderr,
                stdout=result.stdout,
                error_payload={
                    "provider": provider_name,
                    "stage": "consensus_round_member",
                    "reason": "nonzero_returncode_without_stdout",
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                },
            )

        update_member_progress(state_token, provider_name, output_text)
        return MemberExecutionResult(
            provider_name=provider_name,
            provider_config=provider_config,
            output_text=output_text,
            returncode=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    executions: dict[str, MemberExecutionResult] = {}
    # ContextVar propagation so the MCP active-sampling session set
    # in mcp_server.handle_call_tool reaches the worker threads.
    # Python's ThreadPoolExecutor doesn't propagate ContextVars to
    # workers by default. Each submit needs its OWN fresh context
    # copy — a single Context can only be entered once (ctx.run()
    # raises 'cannot enter context: already entered' if reused).
    import contextvars
    with ThreadPoolExecutor(max_workers=max(1, len(member_providers))) as executor:
        future_map = {
            executor.submit(
                contextvars.copy_context().run, _run_member, provider_name
            ): provider_name
            for provider_name in member_providers
        }
        for future in as_completed(future_map):
            provider_name = future_map[future]
            executions[provider_name] = future.result()

    for provider_name in member_providers:
        execution = executions[provider_name]
        if execution.error_payload is not None:
            failed_members.append(provider_name)
            member_failures.append(execution.error_payload)
            continue
        assert execution.provider_config is not None
        member = CouncilMemberResult(
            provider=provider_name,
            model=_provider_model(execution.provider_config, None),
            session_id=None,
            output_text=execution.output_text,
            metadata={
                "returncode": execution.returncode,
                "stderr": execution.stderr,
                "round_number": round_number,
            },
        )
        member_results.append(member)
        event = create_launch_event(
            bundle=new_bundle,
            mode="council_consensus",
            source_provider=parent_outcome.primary_provider,
            target_provider=provider_name,
            target_model=member.model,
            handoff_reason=f"consensus_round_{round_number}",
            metadata={"round_number": round_number, "parent_council_id": parent_outcome.council_run_id},
        )
        append_launch_event(event)
        launches.append(event)

    if not member_results:
        raise ProviderError(
            f"All consensus-round members failed: {failed_members}. "
            "Cannot proceed."
        )

    # Chairman: re-pick or inherit from parent. Default: inherit (consistency).
    primary_provider = primary_provider_override or parent_outcome.primary_provider
    primary_config = config.providers.get(primary_provider)
    if primary_config is None or not primary_config.enabled:
        # Fall back to first surviving member as chairman
        primary_provider = member_results[0].provider
        primary_config = config.providers.get(primary_provider)
        if primary_config is None or not primary_config.enabled:
            raise ProviderError("No enabled chairman provider available.")

    primary_model = _provider_model(primary_config, primary_model_override)
    synthesis_prompt = render_primary_council_prompt(new_bundle, member_results)

    update_synthesis_progress(state_token, "running")
    # Chairman fallback (#fallback): if the primary chair is rate-limited /
    # token-exhausted, synthesize with the next enabled provider instead of
    # failing. `primary_provider`/`primary_model` are reassigned to whoever
    # actually chaired so the outcome records the truth.
    synthesis_output, sections, synthesis_error, primary_provider, primary_model = (
        _synthesize_with_fallback(
            synthesis_prompt, config, primary_provider, primary_model, cwd, state_token
        )
    )

    differences = []
    if "differences" in sections:
        differences = [
            line.strip("- ").strip()
            for line in sections["differences"].splitlines()
            if line.strip()
        ]

    routing_label, routing_label_error = parse_routing_label(synthesis_output)
    if routing_label is not None:
        try:
            update_synthesis_progress(state_token, "done", output_text=synthesis_output, routing_label=routing_label.to_dict())
        except Exception:
            pass
    _log_routing_label_event(
        bundle_id=new_bundle.bundle_id,
        primary_provider=primary_provider,
        primary_model=primary_model,
        success=routing_label is not None,
        error=routing_label_error,
        synthesis_error=bool(synthesis_error),
    )

    # Use the canonical _resolve_winner so consensus rounds match normal +
    # chain paths. Pre-fix this branch scanned prose first and the routing
    # label only as a fallback — exactly the inverted-priority bug we
    # already removed elsewhere.
    winner_provider = _resolve_winner(
        routing_label=routing_label,
        sequence=[*member_providers, primary_provider],
    )

    final_metadata: dict = {
        "cwd": str(cwd),
        "mode": "consensus_round",
        "round_number": round_number,
        "parent_council_id": parent_outcome.council_run_id,
        "chain_root_id": chain_root_id,
        "user_refinement": user_refinement,
        "failed_members": failed_members,
        "member_failures": member_failures,
    }
    if synthesis_error:
        final_metadata["synthesis_error"] = synthesis_error
    if routing_label_error:
        final_metadata["routing_label_error"] = routing_label_error

    final_outcome = create_council_outcome(
        bundle=new_bundle,
        primary_provider=primary_provider,
        member_results=member_results,
        primary_model=primary_model,
        winner_provider=winner_provider,
        differences=differences,
        synthesis_output=synthesis_output,
        synthesis_prompt=synthesis_prompt,
        routing_label=routing_label,
        mode="consensus_round",
        metadata=final_metadata,
    )
    outcome_path = save_council_outcome(final_outcome)
    review_path = write_unified_council_page(new_bundle, final_outcome)
    _maybe_auto_open(review_path)  # chain-mode follow-up; same gate as fresh councils
    _emit_council_telemetry(final_outcome)

    task = task_from_council(
        bundle=new_bundle,
        outcome=final_outcome,
        review_page_path=str(review_path),
        launch_ids=[launch.launch_id for launch in launches],
    )
    task_path = save_task_record(task)
    sync_path = save_sync_record(task)

    finalize_council_run_state(
        state_token,
        status="completed",
        council_id=final_outcome.council_run_id,
        review_path=str(review_path),
    )

    return CouncilRunResult(
        outcome=final_outcome,
        outcome_path=outcome_path,
        review_path=review_path,
        launches=launches,
        task_path=task_path,
        sync_path=sync_path,
    )


def auto_chain_council(
    *,
    config: AppConfig,
    initial_outcome: CouncilOutcome,
    max_rounds: int = 3,
    cwd: Path | None = None,
    run_state_token: str | None = None,
) -> list[CouncilRunResult]:
    """Repeatedly run consensus rounds until chairman declares convergence
    OR max_rounds is reached. Returns the list of round outcomes (the parent
    is NOT included; just the new rounds this call produced).

    Stops early if any round's chairman fails to emit a routing label.

    `run_state_token` is threaded through to each round's `run_consensus_round`
    so the live launchpad page keeps tracking. Without it, every round wrote
    to a different status file and the launchpad UI broke between rounds.
    """
    if max_rounds < 1:
        return []
    outcomes: list[CouncilRunResult] = []
    current = initial_outcome
    for _ in range(max_rounds):
        if chairman_says_converged(current.routing_label):
            break
        result = run_consensus_round(
            config=config,
            parent_outcome=current,
            cwd=cwd,
            run_state_token=run_state_token,
        )
        outcomes.append(result)
        current = result.outcome
    return outcomes
