from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    enabled: bool
    label: str
    command: list[str]
    args: list[str]
    task_types: set[str]
    model: str | None = None
    # Reasoning/thinking level — standardized across providers. The CLIs
    # each spell it differently (claude `--effort low|medium|high|xhigh|max`;
    # codex `-c model_reasoning_effort=minimal|low|medium|high`;
    # antigravity bakes it into the model name e.g. "Gemini 3.1 Pro (high)"
    # and exposes no CLI knob — agy users set it in the IDE dropdown).
    # Trinity normalizes to a small vocabulary and translates per-provider
    # in providers.py. Value: low / medium / high / None. agy gets None
    # by design (its CLI has no flag for it as of 2026-05-26).
    effort: str | None = None


@dataclass(frozen=True)
class AppConfig:
    max_turns: int
    notifications: bool
    providers: dict[str, ProviderConfig]
    task_preferences: dict[str, list[str]]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def trinity_home() -> Path:
    """Return the Trinity state directory.

    Defaults to ``~/.trinity/``. Override with ``TRINITY_HOME`` env var.
    The directory is created on first access.
    """
    env = os.environ.get("TRINITY_HOME")
    if env:
        path = Path(env).expanduser().resolve()
    else:
        path = Path.home() / ".trinity"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return project_root() / "config.json"


def _reconcile_model_arg(
    command: list[str], args: list[str], model: str | None
) -> tuple[str | None, list[str], list[str]]:
    """Lift an inline `--model X` (or `--model=X`) out of command/args into the
    authoritative `model` field and strip it from the token lists.

    The CLI dispatches whatever `--model` is on the command line, so when one
    is present it — not the JSON `model` field — is the truth. We make
    `config.model` equal that dispatched value and remove the inline flag, so
    the recording path (which reads `config.model`) can never disagree with
    what actually ran. Returns (model, cleaned_command, cleaned_args)."""

    def _extract(tokens: list[str]) -> tuple[str | None, list[str]]:
        out: list[str] = []
        found: str | None = None
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--model" and i + 1 < len(tokens):
                found = tokens[i + 1]
                i += 2
                continue
            if tok.startswith("--model="):
                found = tok[len("--model="):]
                i += 1
                continue
            out.append(tok)
            i += 1
        return found, out

    cmd_model, command = _extract(command)
    arg_model, args = _extract(args)
    # Dispatched value wins over the JSON field; args override command if both.
    effective = arg_model or cmd_model or model
    return effective, command, args


def load_config(explicit: str | None = None, *, required: bool = True) -> AppConfig:
    """Load configuration from disk.

    Args:
        explicit: explicit path to config.json; uses project_root()/config.json if None.
        required: if True (default), raises FileNotFoundError when the config
            file is missing.  When False, returns a minimal empty config instead
            — useful for read-only CLI commands that only inspect state.
    """
    path = config_path(explicit)
    raw_text: str | None = None
    if path.exists():
        raw_text = path.read_text()
    else:
        # 100-persona audit D1 fix: a fresh wheel install has no
        # `config.json` next to project_root(), so the README's first
        # command (`council-launch --task "hello"`) used to crash with a
        # FileNotFoundError pointing at a site-packages path the user
        # neither owns nor knows about — the tweet-screenshot failure
        # mode. Fall through to the bundled `data/config.example.json`
        # so the hero command works on a clean pip install.
        try:
            from importlib import resources
            raw_text = (
                resources.files("trinity_local")
                .joinpath("data/config.example.json")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            raw_text = None
        if raw_text is None:
            if not required:
                return _empty_config()
            raise FileNotFoundError(
                f"Missing config file at {path} and no bundled fallback found. "
                "Run `trinity-local install-mcp` to recreate config.json."
            )

    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise ValueError(f"config at {path} is not a JSON object")
    providers: dict[str, ProviderConfig] = {}
    for name, provider in raw["providers"].items():
        command = list(provider["command"])
        args = list(provider.get("args", []))
        # Make `model` authoritative. A `--model X` baked into command/args
        # is what the CLI actually dispatches, but the recording path reads
        # `config.model` — so if they disagree (the shipped config does this:
        # model "gpt-5.5" with args carrying "--model gpt-5.3-codex"), councils
        # dispatch one model and record another, poisoning the routing table
        # and every "Model X scored Y on your taste" claim. Lift the inline
        # --model into config.model (the dispatched value wins) and strip it,
        # so there is exactly one source of truth and dispatch is unchanged.
        model, command, args = _reconcile_model_arg(
            command, args, provider.get("model")
        )
        providers[name] = ProviderConfig(
            name=name,
            type=provider["type"],
            enabled=provider.get("enabled", True),
            label=provider.get("label", name),
            command=command,
            args=args,
            task_types=set(provider.get("task_types", [])),
            model=model,
            effort=provider.get("effort"),
        )

    return AppConfig(
        max_turns=int(raw.get("max_turns", 4)),
        notifications=bool(raw.get("notifications", True)),
        providers=providers,
        task_preferences={
            key: list(value) for key, value in raw.get("task_preferences", {}).items()
        },
    )


def _empty_config() -> AppConfig:
    """Return a minimal config with no providers — used when config is optional."""
    return AppConfig(
        max_turns=4,
        notifications=False,
        providers={},
        task_preferences={},
    )


# Canonical 3-provider lineup for parallel councils, in display order.
# Codex-only / claude-only / antigravity-only users (persona audit P89)
# had broken 3-column councils because 9 sites hardcoded this list as a
# default; the helper below picks the enabled subset so a single-
# provider user gets a clean single-call instead of failed 3-of-3.
# (Renamed "gemini" → "antigravity" 2026-05-20 alongside the provider
# slug flip in config.example.json; the legacy `gemini` slug no longer
# exists in any shipped config.)
CANONICAL_COUNCIL_PROVIDERS: tuple[str, ...] = ("claude", "antigravity", "codex")


def installed_council_providers(explicit: str | None = None) -> list[str]:
    """Return the canonical lineup filtered to providers whose CLI binary
    is actually on PATH.

    The audience-expansion claim is "Trinity works with one provider —
    add the other two for richer disagreement signal." Today's default
    lineup ignores binary availability, so a user with only Claude
    installed gets a 3-member council that fails 2 of 3. Pre-filter to
    only the providers whose CLI we can actually invoke; the launchpad
    tip card surfaces the missing ones as a free-tier upsell.

    Fallback: if NO canonical providers have binaries on PATH, return
    the full canonical list. Caller's existing "Provider binary not
    found" error path becomes the diagnostic (the council fails
    clearly with a list of what to install, instead of silently
    rendering an empty council page).
    """
    try:
        config = load_config(explicit, required=False)
    except Exception:
        config = None

    enabled: set[str] = set(CANONICAL_COUNCIL_PROVIDERS)
    if config is not None:
        enabled = {
            name for name, p in config.providers.items()
            if getattr(p, "enabled", True) and name in CANONICAL_COUNCIL_PROVIDERS
        }
        if not enabled:
            enabled = set(CANONICAL_COUNCIL_PROVIDERS)

    # Resolve against the SAME enriched PATH the council runner dispatches with
    # (which_on_runtime_path → providers._ensure_binary gate + run_with_runtime_env
    # execution). A bare shutil.which would drop a provider that IS installed and
    # dispatchable — a Homebrew CLI under a GUI-launched IDE on a stripped PATH —
    # silently shrinking the lineup below what the user actually has.
    from .runtime_env import which_on_runtime_path

    available: list[str] = []
    for name in CANONICAL_COUNCIL_PROVIDERS:
        if name not in enabled:
            continue
        provider_config = (
            config.providers.get(name) if config is not None else None
        )
        binary = (
            provider_config.command[0]
            if provider_config and provider_config.command
            else name
        )
        if which_on_runtime_path(binary) is not None:
            available.append(name)
    if not available:
        return list(CANONICAL_COUNCIL_PROVIDERS)
    return available


def default_council_members(explicit: str | None = None) -> list[str]:
    """Return the canonical lineup filtered by enable+install status.

    Alias for ``installed_council_providers`` — kept as the public
    name every CLI / MCP / launchpad caller already uses. The PATH
    filter (added 2026-05-19) makes the 1-member council case the
    natural default for users who haven't installed all three CLIs,
    rather than a 3-member council that fails 2 of 3.
    """
    return installed_council_providers(explicit)
