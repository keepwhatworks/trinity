from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ProviderConfig
from .runtime_env import run_with_runtime_env, which_on_runtime_path


# Defense-in-depth ceiling for any single provider invocation. The real bug
# we hit was inherited-stdin causing codex to block forever; that's fixed by
# `input=""` in `_run_command`. This timeout catches anything else that goes
# wrong (network, model server hang, etc.) without holding up the council.
# 8 minutes is comfortable headroom for codex on xhigh on a hard prompt; a
# single member taking longer is almost certainly stuck.
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 8 * 60


@dataclass
class ProviderResult:
    provider: str
    stdout: str
    stderr: str
    returncode: int
    elapsed_seconds: float = 0.0


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def run(self, prompt: str, cwd: Path) -> ProviderResult:
        raise NotImplementedError

    def _ensure_binary(self) -> None:
        binary = self.config.command[0]
        # Resolve against the SAME enriched PATH the dispatch executes with
        # (which_on_runtime_path → build_runtime_env: venv bin + ~/.local/bin +
        # /opt/homebrew/bin + /usr/local/bin, then the inherited PATH). A bare
        # shutil.which here used to REJECT a provider that IS installed and
        # runnable — e.g. a Homebrew CLI under a GUI-launched IDE whose MCP server
        # inherits a stripped PATH lacking /opt/homebrew/bin — defeating the very
        # injection that lets _run_command find it. Gate must match execution.
        if which_on_runtime_path(binary) is None:
            raise ProviderError(f"Provider binary not found: {binary}")

    def _run_command(
        self,
        command: list[str],
        cwd: Path,
        *,
        timeout: float | None = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    ) -> ProviderResult:
        self._ensure_binary()
        t0 = time.monotonic()
        try:
            # Provider CLIs are one-shot non-interactive invocations. Codex on
            # `xhigh` reasoning blocks for 30+ minutes reading from inherited
            # stdin (it treats non-TTY stdin as "additional prompt input").
            # Pass empty stdin so codex sees stdin closed immediately. Claude
            # and Gemini are unaffected — both ignore stdin in -p mode — but
            # closing stdin is correct universally for "run this CLI, capture
            # output" semantics.
            completed = run_with_runtime_env(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                input="",
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - t0
            return ProviderResult(
                provider=self.config.name,
                stdout=(exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                stderr=f"Timed out after {elapsed:.1f}s",
                returncode=-1,
                elapsed_seconds=elapsed,
            )
        elapsed = time.monotonic() - t0
        return ProviderResult(
            provider=self.config.name,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
            returncode=completed.returncode,
            elapsed_seconds=elapsed,
        )


def _effective_model(config: ProviderConfig) -> str | None:
    """Return the configured model for this provider. Use CLI aliases
    like Claude's `'opus'` in config.json to track latest."""
    return config.model


def _effective_effort(config: ProviderConfig) -> str | None:
    """Return the configured reasoning/thinking effort level.

    Per-provider valid values (we pass through verbatim — the CLI itself
    validates):
      - claude:       low / medium / high / xhigh / max (CLI flag --effort)
      - codex:        none / low / medium / high / xhigh
                      (-c model_reasoning_effort=<level>; xhigh added with
                      GPT-5.5 in Codex CLI 0.124+; "minimal" deprecated in
                      favor of "none"/"low" depending on the CLI version)
      - antigravity:  NONE at CLI invocation time — agy CLI exposes no flag.
                      The user picks via agy's `/model` slash command, which
                      persists to ~/.gemini/antigravity-cli/settings.json and
                      sticks across sessions. Trinity reads that file to
                      display the active model+effort, but can't set it
                      programmatically per-council (no CLI flag).

    Trinity stores this on ProviderConfig.effort. agy users set it via
    `/model` inside agy; the live council card reads back the persisted
    selection so the chip shows the actual model+effort, not a guess.
    """
    return config.effort


def read_agy_active_model_raw() -> str | None:
    """The raw model SKU agy will ACTUALLY dispatch (e.g. ``"Gemini 3.5 Flash
    (High)"``), read from agy's own ``~/.gemini/antigravity-cli/settings.json``.

    This is the single honest source for antigravity's model: agy has no
    ``--model`` flag, so ``config.model`` is *ignored* by the CLI — the user's
    ``/model`` slash-command selection in that file is what runs. Returns
    ``None`` on any miss (file absent, unknown key, parse error) so callers
    degrade quietly to ``config.model``. The schema isn't public, so we probe a
    few likely keys.
    """
    try:
        import json as _json

        path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
        if not path.exists():
            return None
        settings = _json.loads(path.read_text())
        model = (
            settings.get("defaultReasoningModel")
            or settings.get("reasoningModel")
            or settings.get("model")
        )
        return model if isinstance(model, str) and model else None
    except Exception:
        return None


def dispatched_model(config: ProviderConfig) -> str | None:
    """The model that will ACTUALLY run for this provider — what eval/council
    must RECORD (the recorded == dispatched invariant). For antigravity this is
    agy's settings.json selection (``config.model`` is a dead value the CLI
    ignores); for every other provider it's ``config.model``.
    """
    if config.name == "antigravity":
        agy_model = read_agy_active_model_raw()
        if agy_model:
            return agy_model
    return config.model


# Provider-specific flags that turn an agentic CLI invocation into a plain
# one-shot COMPLETION — no MCP servers, no tools, no agent loop. The eval
# runner sets `clean_completion=True` so an eval item is *answered*, not
# *executed*: without these, `claude -p <item>` inherits the user's full
# ~/.claude.json (6 MCP hosts + tools) and an agentic eval prompt ("look at the
# live app and trace…") makes the model try to USE the browser and hang (#270).
_CLEAN_COMPLETION_FLAGS = {
    "claude": ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--disallowedTools", "*"],
    # codex exec / agy -p are already non-interactive and didn't hang; add their
    # no-tool flags here if a target ever does.
}


class CLIProvider(BaseProvider):
    clean_completion: bool = False

    def run(self, prompt: str, cwd: Path) -> ProviderResult:
        # MCP host sampling — preferred path for the Claude voice when
        # Trinity-MCP is loaded inside a chat client (Claude Desktop)
        # that advertised the `sampling` capability. Counts against
        # the user's regular Claude plan, NOT the post-2026-06-15 Agent
        # SDK credit pool.
        #
        # Other CLI-shaped providers (agy/Antigravity, etc.) don't go through
        # sampling — they don't have the billing problem `claude -p`
        # has, and the host can't promise to route to a non-Claude
        # model anyway. Gate on the provider name.
        if self.config.name == "claude":
            sampled = self._try_sampling(prompt)
            if sampled is not None:
                return sampled

        # This subprocess writes a transcript the ingest pipeline will later
        # scan as role=user text. Record the dispatch hash so the ingest gate
        # can tell Trinity's own echo from the founder typing (corpus
        # self-pollution fix, 2026-06-09 — eval items are founder prompts
        # replayed VERBATIM, invisible to every shape filter).
        from .dispatch_ledger import record_dispatched_prompt

        record_dispatched_prompt(prompt)
        command = [*self.config.command]
        # Inject --model BEFORE the prompt-consuming flag (e.g. -p, --prompt).
        # If the last token of `command` is a flag, --model goes in front of it
        # so the prompt sits immediately after -p. Putting --model between -p
        # and the prompt makes Gemini fail with "Not enough arguments following: p".
        model = _effective_model(self.config)
        effort = _effective_effort(self.config)
        # Inject claude's --effort flag too (low/medium/high/xhigh/max).
        # Only Claude's CLI supports this — agy CLI has no equivalent
        # (Antigravity bakes the level into the model SKU and only
        # exposes selection via the IDE dropdown). Skip for non-claude
        # CLIProvider instances.
        inject_effort = (
            effort
            and self.config.name == "claude"
            and "--effort" not in command
            and "--effort" not in self.config.args
        )
        # Compute the tail-flag once so both --model and --effort can
        # land BEFORE it. Walk-through:
        #   command = ["claude", "-p"]  →  tail = "-p"
        #   command = ["claude"]        →  tail = None
        tail: str | None = None
        if len(command) > 1 and command[-1].startswith("-"):
            tail = command.pop()
        # --model is claude-only among CLIProvider configs (claude + agy).
        # agy has no --model flag — model is its `/model` slash-command — and
        # exits 2 if given one. Allowlist claude, mirroring the --effort gate.
        inject_model = (
            model
            and self.config.name == "claude"
            and "--model" not in command
            and "--model" not in self.config.args
        )
        if inject_model:
            command.extend(["--model", model])
        if inject_effort:
            command.extend(["--effort", effort])
        # #270: clean-completion mode (eval dispatch) — strip MCP + tools so the
        # item is answered, not executed agentically. Flags land before the
        # prompt-consuming tail flag, like --model/--effort.
        if self.clean_completion:
            command.extend(_CLEAN_COMPLETION_FLAGS.get(self.config.name, []))
        if tail is not None:
            command.append(tail)
        command.append(prompt)
        command.extend(self.config.args)
        return self._run_command(command, cwd)

    def _try_sampling(self, prompt: str) -> ProviderResult | None:
        """Attempt MCP host sampling for this Claude invocation.

        Returns a ProviderResult wrapping the sampled text on success,
        or None to signal the caller to fall through to the
        subprocess path. Never raises — the contract is "quietly
        degrade if sampling isn't available."
        """
        try:
            from .mcp_sampling import request_claude_sample
        except ImportError:
            return None
        t0 = time.monotonic()
        text = request_claude_sample(prompt)
        if text is None:
            return None
        return ProviderResult(
            provider=self.config.name,
            stdout=text.strip(),
            stderr="",
            returncode=0,
            elapsed_seconds=time.monotonic() - t0,
        )


class CodexProvider(BaseProvider):
    def run(self, prompt: str, cwd: Path) -> ProviderResult:
        # Same corpus self-pollution guard as CLIProvider.run — codex exec
        # writes ~/.codex session transcripts the ingest pipeline scans.
        from .dispatch_ledger import record_dispatched_prompt

        record_dispatched_prompt(prompt)
        command = [*self.config.command]
        args = list(self.config.args)
        if "--skip-git-repo-check" not in args:
            args.append("--skip-git-repo-check")
        model = _effective_model(self.config)
        if model and "--model" not in args:
            args.extend(["--model", model])
        # Codex reasoning effort takes a TOML config override:
        #   codex exec -c model_reasoning_effort=xhigh
        # Valid values per Codex CLI 0.124+ docs:
        #   none / low / medium (default) / high / xhigh.
        # "xhigh" landed with GPT-5.5 in April 2026 — use it on hard
        # tasks where latency matters less ("the hardest asynchronous
        # agentic tasks or evals that test the bounds of model
        # intelligence" per OpenAI's release notes). Skip if the user
        # already passed -c model_reasoning_effort=... explicitly in
        # args; we don't want to layer two overrides.
        effort = _effective_effort(self.config)
        if effort:
            already_set = any(
                a == "-c" and i + 1 < len(args)
                and "model_reasoning_effort" in args[i + 1]
                for i, a in enumerate(args)
            )
            if not already_set:
                args.extend(["-c", f"model_reasoning_effort={effort}"])
        command.extend(args)
        command.append(prompt)
        return self._run_command(command, cwd)


class MLXProvider(BaseProvider):
    def run(self, prompt: str, cwd: Path) -> ProviderResult:
        if not self.config.model:
            raise ProviderError("MLX provider requires a model name in config.")
        command = [
            *self.config.command,
            "--model",
            self.config.model,
            "--prompt",
            prompt,
            *self.config.args,
        ]
        return self._run_command(command, cwd)


# Ollama's `run` streams a reasoning model's full chain-of-thought followed by
# a "...done thinking." sentinel and then the actual answer, and it emits ANSI
# spinner/cursor escape sequences even when stdout is redirected to a pipe.
# Neither belongs in a council member's captured response — the thinking trace
# balloons the output 6-9x and the escape bytes render as terminal garble in
# the chairman's context. `--hidethinking`/`--think false` would suppress it at
# the source but return EMPTY on Qwen3.6-class models (whose whole reply lives
# in the thinking stream), so we strip in post instead.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[=>PX^_].*?(?:\x1b\\|\x07)|\x1b[=>]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_DONE_THINKING = "done thinking."


def clean_ollama_output(text: str) -> str:
    """Strip ANSI/control bytes and the chain-of-thought trace from raw
    `ollama run` stdout, leaving only the model's final answer."""
    if not text:
        return text
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    text = _THINK_TAG_RE.sub("", text)
    # Reasoning models close their thinking with "...done thinking." right
    # before the answer. Keep only what follows the LAST such sentinel.
    idx = text.lower().rfind(_DONE_THINKING)
    if idx != -1:
        text = text[idx + len(_DONE_THINKING):]
    return text.strip()


# Provider-CLI banner/noise lines that carry no failure information — codex prints
# a 10-line startup banner (model/sandbox/session id/…) to stderr BEFORE the real
# error, so a raw `result.stderr` failure detail is mostly noise. We extract the
# meaningful cause instead.
_FAILURE_BANNER_PREFIXES = (
    "reading additional input", "openai codex", "workdir:", "model:", "provider:",
    "approval:", "sandbox:", "reasoning", "session id:", "--------", "user", "tokens used",
)


def describe_provider_failure(
    stdout: str | None, stderr: str | None, returncode: int | None, *, provider: str | None = None
) -> str:
    """Turn a failed dispatch (rc!=0 / empty stdout) into ONE clean human reason.

    A quota-exhausted CLI exits non-zero with the real cause on stderr buried under
    a startup banner — e.g. codex prints its model/sandbox/session banner then
    ``ERROR: You've hit your usage limit … try again at Jun 12th, 2026 11:25 PM.``
    Surfacing the raw multi-line blob as a council member's failure detail (or an
    eval judge's score reason) is noise; this names the cause so a degraded run says
    WHY — 'usage limit reached — resets Jun 12th…' beats 'provider failed'.

    Recognizes usage/rate-limit and auth failures across claude / codex / agy
    (all three print a 'usage limit'/'rate limit'/login phrase); otherwise returns
    the most informative non-banner line, else 'exited with code N'."""
    text = f"{stderr or ''}\n{stdout or ''}".strip()
    low = text.lower()
    label = f"{provider} " if provider else ""

    if "usage limit" in low or "rate limit" in low or "quota" in low or "out of credits" in low:
        m = re.search(r"try again (?:at|on|after)\s+([^.\n]+)", text, re.IGNORECASE)
        when = f" — resets {m.group(1).strip()}" if m else ""
        return f"{label}usage limit reached{when}".strip()
    if any(k in low for k in ("not logged in", "unauthorized", "not authenticated",
                              "please log in", "please login", "run `claude login",
                              "authentication", "401")):
        return f"{label}not authenticated — log in to the provider CLI".strip()

    # Most informative line: prefer an explicit ERROR line; skip banner noise.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.upper().startswith("ERROR"):
            return ln.lstrip("ERROR:").strip()[:200] or ln[:200]
    for ln in reversed(lines):
        if not any(ln.lower().startswith(p) for p in _FAILURE_BANNER_PREFIXES):
            return ln[:200]
    return f"exited with code {returncode}" if returncode is not None else "dispatch failed"


class OllamaProvider(BaseProvider):
    """Local model dispatch via Ollama. Each model the user has pulled (via
    `ollama pull <name>`) is a candidate worker; the chosen model goes through
    `ollama run <model> "<prompt>"` and returns stdout.

    Cost: $0 (runs on user hardware). Latency depends on the model + hardware.
    Per spec-v1.5.md: when this works reliably at ship, the "and local models"
    line stays in the pitch; if dispatch is wobbly, cut it from the pitch.
    """

    def run(self, prompt: str, cwd: Path) -> ProviderResult:
        if not self.config.model:
            raise ProviderError("Ollama provider requires a model name in config.")
        # Ollama CLI: `ollama run <model> "<prompt>"` reads prompt as argv,
        # outputs to stdout. We post-process to drop the thinking trace +
        # ANSI escape bytes (see clean_ollama_output) rather than rely on
        # --hidethinking, which returns empty on Qwen3.6-class reasoners.
        command = [
            *self.config.command,
            "run",
            self.config.model,
            prompt,
            *self.config.args,
        ]
        result = self._run_command(command, cwd)
        result.stdout = clean_ollama_output(result.stdout)
        return result


def make_provider(config: ProviderConfig) -> BaseProvider:
    if config.type == "cli":
        return CLIProvider(config)
    if config.type == "codex":
        return CodexProvider(config)
    if config.type == "mlx":
        return MLXProvider(config)
    if config.type == "ollama":
        return OllamaProvider(config)
    raise ProviderError(f"Unsupported provider type: {config.type}")


# ─── Chairman fallback (token exhaustion / rate-limit resilience) ────────
#
# The chairman is a single provider (default Claude). When its tokens are
# exhausted or it rate-limits, its CLI returns non-zero / empty stdout (or the
# call raises) — and a council/lens build that depends on it would simply fail.
# These helpers let the caller fall back through the other enabled providers so
# a dead primary degrades to "synthesized by GPT/Gemini instead" rather than a
# hard failure.

# Provider types that can chair a synthesis (run a real LLM). Local stubs
# (mlx/ollama) are excluded — they're council members, not synthesis chairs.
_CHAIR_TYPES = {"cli", "codex"}


def chairman_fallback_order(config, primary_provider: str) -> list[str]:
    """Ranked chair providers for a synthesis: the requested primary first,
    then the OTHER enabled chair-capable providers (the fallback chain). Order
    among the rest follows config insertion order — claude / codex / antigravity
    as declared."""
    enabled = [
        name for name, cfg in config.providers.items()
        if getattr(cfg, "enabled", False) and getattr(cfg, "type", None) in _CHAIR_TYPES
    ]
    order: list[str] = []
    if primary_provider in enabled:
        order.append(primary_provider)
    for name in enabled:
        if name not in order:
            order.append(name)
    return order


def _result_is_usable(res: "ProviderResult") -> bool:
    """A synthesis result is usable when the call succeeded AND produced real
    stdout. A rate-limited / token-exhausted CLI returns non-zero or empty —
    the signal to fall through to the next chair.

    NOTE: this is the CONSERVATIVE chairman-fallback gate (any nonzero → try the
    next chair). It is deliberately NOT `not result_hard_failed()`: member +
    judge dispatch are LENIENT (a nonzero exit that still printed real stdout is
    used, not discarded). Those two resilience models stay separate by design."""
    return bool(res) and res.returncode == 0 and bool((res.stdout or "").strip())


def result_hard_failed(res: "ProviderResult") -> bool:
    """True when a provider call HARD-failed: non-zero exit AND no usable stdout
    (a rate-limited / token-exhausted CLI). The single predicate the member-
    dispatch (council_runner) and judge-dispatch (evals.scorer) paths share —
    each previously open-coded `returncode != 0 and not (stdout or "").strip()`
    inline before pairing it with `describe_provider_failure`. LENIENT by design:
    a nonzero exit that still printed real stdout is NOT a hard failure (the
    caller uses the stdout) — distinct from the conservative `_result_is_usable`
    chairman gate above.

    Uses getattr defaults so a lightweight test double (a SimpleNamespace judge
    without ``returncode``) reads as success (rc=0 → not a hard failure), matching
    the eval scorer's original inline guard."""
    return getattr(res, "returncode", 0) != 0 and not (getattr(res, "stdout", "") or "").strip()


def run_with_chairman_fallback(
    prompt: str,
    config,
    primary_provider: str,
    cwd,
    *,
    on_fallback=None,
    provider_factory=None,
) -> tuple["ProviderResult | None", str | None, str | None]:
    """Run `prompt` through the chairman, falling back through the other enabled
    providers when the primary raises OR returns an unusable (empty/non-zero)
    result — e.g. Claude's tokens are exhausted.

    Returns ``(result, provider_used, error)``: on success the winning result +
    which provider actually chaired; on total failure ``(None, None, error)``
    with the last error. ``on_fallback(failed_provider, reason)`` is called each
    time it advances, so the caller can record/telemetry the substitution.
    """
    factory = provider_factory or make_provider
    order = chairman_fallback_order(config, primary_provider)
    last_error: str | None = None
    for name in order:
        cfg = config.providers.get(name)
        if cfg is None or not getattr(cfg, "enabled", False):
            continue
        try:
            res = factory(cfg).run(prompt, cwd)
        except Exception as exc:  # noqa: BLE001 — any failure → try next chair
            last_error = f"{name}: {type(exc).__name__}: {exc}"
            if on_fallback:
                on_fallback(name, last_error)
            continue
        if _result_is_usable(res):
            return res, name, None
        last_error = (
            f"{name}: unusable result (rc={res.returncode}, "
            f"empty_stdout={not (res.stdout or '').strip()})"
        )
        if on_fallback:
            on_fallback(name, last_error)
    return None, None, last_error
