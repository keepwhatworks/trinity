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
        # CouncilOutcome's field is `member_results`, NOT `responses` — the old
        # `getattr(outcome, "responses", [])` silently defaulted to [] on EVERY
        # call, so the council_complete GA4 event always logged member_count=0
        # (a DISCLOSED_EVENT_PARAM, so the analytics stream was corrupted to a
        # constant). Bind to the real field the rest of the codebase reads.
        member_count = len(getattr(outcome, "member_results", []) or [])
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
    create_council_outcome,
    create_launch_event,
    parse_routing_label,
    parse_synthesis_sections,
    render_member_prompt,
    render_primary_council_prompt,
    save_council_outcome,
)
from .council_schema import (
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
        # Identity-triple stamping (#239 extended to the behavioral stream,
        # 2026-07-14): the disagreement ledger joins claims to member models,
        # and effort was the missing leg — every unstamped council is
        # behavioral data that can never gain effort fidelity later.
        from .providers import _effective_effort
        member = CouncilMemberResult(
            provider=provider_name,
            model=_provider_model(execution.provider_config, member_model_overrides.get(provider_name)),
            session_id=None,
            output_text=execution.output_text,
            metadata={
                "returncode": execution.returncode,
                "stderr": execution.stderr,
                "stdout": execution.stdout,
                "effort": _effective_effort(execution.provider_config),
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
