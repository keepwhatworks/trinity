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
    member_prompt_framing,
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
    """The model this council will ACTUALLY dispatch, not the config label.

    This used to be `return override or config.model`, and that was the founder's
    bug report one level deeper than it looked. `providers.dispatched_model()`
    already exists and its docstring already states the rule -- "what eval/council
    must RECORD (the recorded == dispatched invariant)" -- because for antigravity
    `config.model` is a value the CLI IGNORES: agy has no --model flag, so the
    user's `/model` selection in ~/.gemini/antigravity-cli/settings.json is what
    runs. ask.py called it. evals/runner.py called it twice. This function, which
    feeds the disagreement ledger that the whole which-model-to-trust claim rests
    on, did not.

    Measured 2026-08-18 across the 400 most recent councils: 68 antigravity rows
    recorded 'Gemini 3.1 Pro (high)' and 3 recorded 'Gemini 3.7 Flash', while agy's
    live setting was 'Gemini 3.7 Flash (Low)'. Two symptoms, one cause -- the window
    of Flash results filed under 3.1 Pro, and the missing effort leg, since for agy
    the effort lives INSIDE the model string and config.model drops it.
    """
    if override:
        return override
    if config is None:
        return None
    from .providers import dispatched_model
    return dispatched_model(config) or config.model


def _model_provenance(config, override: str | None) -> str:
    """How trustworthy is the model string this run will record?

    Trinity recorded `providers.<name>.model` as "the model that answered" and
    every ledger row looked equally reliable. For a provider invoked WITHOUT
    --model that string is a static label — a settings alias or a stale hand-edit
    silently falsifies it, and a window of Gemini Flash councils was filed under
    3.1 Pro exactly that way. `pinned` means argv enforces it; `assumed` means
    nobody checked. `echoed` is only knowable after the member runs and is
    attached then.
    """
    if override:
        return "pinned"
    if config is None:
        return "unknown"
    try:
        from .providers import model_provenance
        return model_provenance(config)
    except Exception:
        return "assumed"


def stamp_member_model(config, override: str | None,
                       echo: str | None) -> tuple[str | None, str, str | None]:
    """(recorded_model, source, label) for one member.

    A module-level function rather than inline in run_council for the reason
    `_has_model_flag` gives two functions up: the stamping lives inside a
    subprocess-dispatching loop and so could not be tested there, and an
    untestable guard is how the first version ships wrong. Mine did — the first
    provenance fix wrote to run STATE, which the ledger never reads, and the
    tests I wrote around it reimplemented the logic instead of calling it, so a
    mutation reverting the behaviour left them green.

    CAPTURE INSTEAD OF ASSUME: a CLI that states its model outranks the config
    label, because the label is a static string a settings alias or stale
    hand-edit can silently falsify. The label is kept alongside so drift stays
    visible rather than being overwritten.
    """
    from .providers import model_provenance
    recorded = _provider_model(config, override)
    # The LABEL is deliberately the static config string, not the dispatched
    # model. Setting label = recorded would make drift invisible by definition:
    # the whole point of carrying both is that a reader can see Trinity's label
    # disagree with what actually ran, which is how the Flash-under-3.1-Pro
    # window would have been caught the day it started.
    label = override or (getattr(config, "model", None) if config is not None else None)
    if override:
        return recorded, "pinned", label
    if config is None:
        return (echo or recorded), "unknown", label
    return (echo or recorded), model_provenance(config, echo), label


def _warn_model_drift(provider_name: str, config, result) -> None:
    """Say so at dispatch time whenever the label disagrees with what will run.

    TWO sources can outrank the label, and this used to watch only the first:

      echo        the CLI states what it used. codex prints `model: gpt-5.6-sol`.
                  Ground truth, because it describes the run that happened.
      settings    the CLI's OWN config file. agy has no --model flag at all, so
                  ~/.gemini/antigravity-cli/settings.json is the only honest
                  source; claude's ~/.claude/settings.json is what a changed
                  default lands in.

    The early `if not echo: return` meant this warned for codex ONLY -- and codex
    is the one provider that was never the problem. The two that motivated the
    whole provenance arc, precisely because they announce nothing, could drift in
    silence. The record was fixed and the warning was left half-wired.

    Stale docstring corrected with it: claude and agy no longer "stay `assumed`",
    they stamp `configured` when their settings can be read.
    """
    import sys as _sys

    if config is None:
        return
    label = getattr(config, "model", None)
    if not label:
        return

    echo = getattr(result, "model_echo", None)
    if echo:
        if str(label).strip().lower() != echo.strip().lower():
            print(f"  [{provider_name}] MODEL DRIFT: config says {label!r} but the CLI "
                  f"reports {echo!r} — the ECHO is ground truth and the label is not.",
                  flush=True, file=_sys.stderr)
        return

    from .providers import (injects_model_flag, read_claude_settings_model,
                            settings_model)

    # A PINNED model is not drift, it is an override -- a different fact deserving a
    # different sentence. Trinity injects `--model` for claude, so a settings value
    # that disagrees is the user's chosen default being overridden, not a mislabelled
    # row. Saying "DRIFT" there would be false, and crying drift on a correct row is
    # how a warning gets trained out of a reader's attention.
    if injects_model_flag(config):
        chosen = read_claude_settings_model()
        if chosen and chosen.strip().lower() not in str(label).strip().lower():
            print(f"  [{provider_name}] OVERRIDING your {provider_name} default: your "
                  f"settings choose {chosen!r}, Trinity dispatches --model {label!r}.",
                  flush=True, file=_sys.stderr)
        return

    declared = settings_model(config)
    if declared and declared.strip().lower() not in str(label).strip().lower():
        print(f"  [{provider_name}] MODEL DRIFT: config says {label!r} but "
              f"{provider_name}'s own settings say {declared!r} — the SETTINGS decide "
              "what runs, and this label does not.", flush=True, file=_sys.stderr)


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
    # What the CLI SAID it ran, when it says anything. Carried this far because
    # the member record is where the ledger reads the model from, and an
    # unstamped council can never gain provenance later — the same argument the
    # effort leg was added under.
    model_echo: str | None = None
    # What the run COST, when the CLI reports it. Carried for the same reason as
    # model_echo: the member record is where the ledger reads, and a council
    # that recorded no cost can never gain it later. None means the CLI said
    # nothing, never zero (plan item 1B, 2026-09-03).
    usage: dict | None = None


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
                # Not every recorded model is worth the same. See
                # _model_provenance: `assumed` rows are labels nobody verified.
                "chairman_model_source": _model_provenance(chairman_config,
                                                           primary_model_override),
                "member_model_sources": {
                    name: _model_provenance(config.providers.get(name), None)
                    for name in member_providers
                    if config.providers.get(name) is not None
                },
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

    def _note_quota_wall(provider_name: str, stderr_text: str, why: str) -> tuple[str, bool]:
        """Classify a member failure and remember a usage wall for this process.

        The classifier had the wrong string until 2026-09-02: it carried
        "usage limit reached", which nothing emits, while codex actually prints
        "You've hit your usage limit ... try again at 4:12 AM". That real banner
        classified as UNKNOWN with retry_with_other_provider=False — the one
        verdict that tells the caller not to try anybody else. Fixed in
        dispatch_errors from a captured sample.

        Returns the message to show (quota walls get a plain-language one) and
        whether this was a wall.
        """
        from .dispatch_errors import DispatchErrorKind, classify_dispatch_failure
        from .provider_quota import mark_exhausted
        try:
            failure = classify_dispatch_failure(
                provider=provider_name, returncode=1, stderr=stderr_text
            )
        except Exception:
            return why, False
        if failure.kind not in (
            DispatchErrorKind.RATE_LIMITED, DispatchErrorKind.BILLING_EXCEEDED
        ):
            return why, False
        entry = mark_exhausted(
            provider_name, kind=failure.kind.value, retry_after=failure.retry_after
        )
        return entry.describe(), True

    def _run_member(provider_name: str) -> MemberExecutionResult:
        provider_config = config.providers.get(provider_name)
        # Effort rotation (default OFF, TRINITY_EFFORT_ROTATION): must happen
        # BEFORE dispatch so the same rotated config flows to both the CLI
        # flags and the identity stamp below — one source, no drift.
        from .providers import rotated_effort_config
        provider_config = rotated_effort_config(provider_config, council_id)
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

        # QUOTA WALL ALREADY OBSERVED THIS PROCESS. Dispatching again buys a
        # second copy of the same banner. The skip is DISCLOSED, never silent:
        # it lands in metadata.failed_members and in the payload below, so a
        # council that consulted two of three models says which one it lost
        # and why (2026-09-02; the same shape that cost res_081/098/112).
        from .provider_quota import exhausted as _exhausted_providers
        _wall = _exhausted_providers().get(provider_name)
        if _wall is not None:
            update_member_failure(state_token, provider_name, _wall.describe())
            return MemberExecutionResult(
                provider_name=provider_name,
                provider_config=provider_config,
                error_payload={
                    "provider": provider_name,
                    "stage": "member",
                    "reason": "quota_exhausted",
                    "detail": _wall.describe(),
                    "retry_after": _wall.retry_after,
                    "dispatched": False,
                },
            )

        provider = make_provider(provider_config)
        try:
            start_member_progress(state_token, provider_name)
            result = provider.run(member_prompt, cwd)
        except Exception as exc:
            error_text = str(exc)
            error_text, _quota = _note_quota_wall(provider_name, error_text, error_text)
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
            why, _quota = _note_quota_wall(provider_name, result.stderr or "", why)
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

        _warn_model_drift(provider_name, provider_config, result)
        update_member_progress(state_token, provider_name, output_text)
        return MemberExecutionResult(
            provider_name=provider_name,
            provider_config=provider_config,
            output_text=output_text,
            returncode=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
            model_echo=getattr(result, "model_echo", None),
            usage=getattr(result, "usage", None),
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
        from .providers import effort_provenance as _effort_source
        _model, _source, _label = stamp_member_model(
            execution.provider_config,
            member_model_overrides.get(provider_name),
            execution.model_echo,
        )
        member = CouncilMemberResult(
            provider=provider_name,
            model=_model,
            session_id=None,
            output_text=execution.output_text,
            metadata={
                "returncode": execution.returncode,
                "stderr": execution.stderr,
                "stdout": execution.stdout,
                "effort": _effective_effort(execution.provider_config),
                "effort_source": _effort_source(execution.provider_config),
                "model_source": _source,
                "model_label": _label,
                # Absent when the CLI reported nothing. to_dict() drops None,
                # so "no cost recorded" and "cost was zero" stay distinguishable.
                "usage": execution.usage,
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
        # §2 of the compression-turn plan. Framing is a property of the COUNCIL
        # (every member answers the same rendered prompt), so it is recorded
        # here rather than per member. DisagreementPattern already carries
        # council_id, so the ledger-key change (§2's schema half, still not
        # built) has the join it needs whenever it is funded.
        "framing": member_prompt_framing(bundle),
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
