"""Read-only onboarding check for running Claude Code, Codex and agy together.

`trinity-local install --check` answers, for each of the three CLIs: is it
installed, is there a sign-in file, is Trinity registered in its MCP config,
how many of its transcripts does Trinity read, which model does its council
member run, and (agy only, the one CLI that lists its models) does it offer a
newer model family than the member. It writes nothing. Every fix it prints is
the command the rest of Trinity already teaches.

Chosen by council_fcb48fe49b232639 (amd_0280) as the one thing to ship for
people using all three CLIs. Two measured decisions shape it:
  * A newer model is reported, never switched to (amd_0282): a silent swap
    would merge two models' results in one trust-ledger cell.
  * The proof step is a tests-only verify. hq_122 (res_151) found that the
    cross-lab readers' flags did not pick out later-fixed commits (lift 1.07
    on 103 agent-written commits), so the readers are a second read, not proof.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# (config slug, CLI binary, name people know it by)
MEMBERS = (
    ("claude", "claude", "Claude Code"),
    ("codex", "codex", "Codex CLI"),
    ("antigravity", "agy", "Antigravity (agy)"),
)

# The two moments, in the words a user types to their agent. One copy: the
# README and the site teach the same two asks.
NEXT_ASKS = (
    "Before you build: \"Run a Trinity council on this plan.\"",
    "Before you call it done: \"Verify this change with our tests.\"",
)


@dataclass
class MemberCheck:
    provider: str
    label: str
    installed: bool
    version: str | None
    signed_in: bool
    registered: bool | None          # None: config exists but could not be read
    transcripts: int
    member_model: str | None
    member_enabled: bool
    newer_models: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.installed and self.signed_in and bool(self.registered) and self.member_enabled


def mcp_config_path(provider: str) -> Path:
    """Where `install-mcp` registers Trinity for this CLI (user scope)."""
    home = Path.home()
    return {
        "claude": home / ".claude.json",
        "codex": home / ".codex" / "config.toml",
        "antigravity": home / ".gemini" / "settings.json",
    }[provider]


def is_registered(provider: str) -> bool | None:
    """True/False from the CLI's own MCP config; None when it exists but is unreadable."""
    path = mcp_config_path(provider)
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        if provider == "codex":
            from .commands.install import _CODEX_INLINE_TRINITY_RE, _CODEX_MCP_BLOCK_RE
            return bool(_CODEX_MCP_BLOCK_RE.search("\n" + text) or _CODEX_INLINE_TRINITY_RE.search(text))
        servers = json.loads(text).get("mcpServers") or {}
        return "trinity-local" in servers
    except (OSError, ValueError, AttributeError):
        return None


def unreadable_config_fix(provider: str) -> str:
    """A command that shows WHERE the config is broken. Not a rewrite: these
    files hold the user's other settings, so the user repairs them by hand."""
    path = mcp_config_path(provider)
    if provider == "codex":
        show = "codex mcp list"            # codex reports its own config.toml parse error
    else:
        show = f"python3 -m json.tool {path} > /dev/null"
    return f"{show}   # prints the parse error; repair it, then: trinity-local install-mcp"


_FAMILY = re.compile(r"gemini[-\s](\d+)", re.I)


def _family(model: str | None) -> int | None:
    m = _FAMILY.search(model or "")
    return int(m.group(1)) if m else None


def agy_listed_models() -> list[str]:
    """Model ids from `agy models` (first column), or [] when it cannot be run."""
    from .runtime_env import run_with_runtime_env
    try:
        r = run_with_runtime_env(["agy", "models"], capture_output=True, text=True, timeout=30)
    except (OSError, ValueError):
        return []
    except Exception:            # subprocess.TimeoutExpired and friends: no listing, not a crash
        return []
    if r.returncode != 0:
        return []
    return [line.split()[0] for line in (r.stdout or "").splitlines()
            if line.strip() and not line.lower().startswith("fetching")]


def newer_families(member_model: str | None, listed: list[str]) -> list[str]:
    """Listed Gemini models from a newer major family than the member (e.g. 4.x over 3.x)."""
    current = _family(member_model)
    if current is None:
        return []
    return [m for m in listed if (_family(m) or 0) > current]


_GEMINI_ID = re.compile(r"^gemini-(\d+)(?:\.(\d+))?-([a-z]+)(?:-([a-z]+))?$")
_TIER = {"pro": 2, "flash": 1}
_EFFORT = {"high": 3, "medium": 2, "low": 1}


def best_gemini(listed: list[str]) -> str | None:
    """The model a NEW user's Gemini member starts on: newest version first, then
    Pro over Flash at the same version, then the highest effort. With Gemini 4 Pro
    listed that is gemini-4-pro-high; today it is the newest 3.x. Existing users are
    never switched (amd_0282); this only seeds a config that does not exist yet."""
    best, best_key = None, None
    for m in listed:
        g = _GEMINI_ID.match(m)
        if not g:
            continue
        key = (int(g.group(1)), int(g.group(2) or 0), _TIER.get(g.group(3), 0), _EFFORT.get(g.group(4) or "", 0))
        if best_key is None or key > best_key:
            best, best_key = m, key
    return best


def ensure_new_user_config(listed: list[str] | None = None) -> str | None:
    """Write a starter config.json only when none exists; return what was done."""
    from .config import config_path
    from .state_paths import trinity_home
    if config_path().exists():
        return None
    try:
        from importlib import resources
        raw = json.loads(resources.files("trinity_local").joinpath("data/config.example.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("providers"), dict):
        return None
    pick = best_gemini(listed if listed is not None else agy_listed_models())
    if pick and "antigravity" in raw.get("providers", {}):
        raw["providers"]["antigravity"]["model"] = pick
    target = trinity_home() / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return f"Wrote {target}" + (f"; your Gemini member starts on {pick}, the newest agy serves." if pick else ".")


def check_member(provider: str, cli: str, label: str, *, adapter=None, config=None,
               listed: list[str] | None = None) -> MemberCheck:
    from .health_checks import _check_provider, _install_command_for

    installed = bool(adapter and adapter.installed)
    auth = _check_provider(provider, cli)
    signed_in = auth.ok
    registered = is_registered(provider)
    pconf = (config.providers.get(provider) if config is not None else None)
    member_model = getattr(pconf, "model", None)
    member_enabled = bool(pconf is not None and getattr(pconf, "enabled", False))
    c = MemberCheck(provider=provider, label=label, installed=installed,
                  version=getattr(adapter, "version", None), signed_in=signed_in,
                  registered=registered, transcripts=int(getattr(adapter, "transcript_count", 0) or 0),
                  member_model=member_model, member_enabled=member_enabled)
    if not installed:
        c.fixes.append(_install_command_for(provider))
    elif not signed_in:
        c.fixes.append(auth.fix or f"{cli} login")
    if registered is False:
        c.fixes.append("trinity-local install-mcp")
    elif registered is None:
        c.fixes.append(unreadable_config_fix(provider))
    if pconf is not None and not member_enabled:
        c.fixes.append(f"trinity-local config --set providers.{provider}.enabled=true")
        c.notes.append(f"the {provider} member is disabled in config.json, so councils skip it")
    if provider == "antigravity" and installed:
        c.newer_models = newer_families(member_model, listed if listed is not None else agy_listed_models())
        if c.newer_models:
            c.notes.append(
                f"agy offers a newer model family ({', '.join(c.newer_models)}) than this member "
                f"({member_model}). Trinity does not switch on its own: a new model starts its own "
                "trust-ledger cell. To use it, set providers.antigravity.model in config.json.")
    return c


def run_check() -> dict[str, Any]:
    from .adapters import check_all_adapters
    from .config import config_path, load_config

    adapters = {a.provider: a for a in check_all_adapters()}
    try:
        config = load_config(required=False)
    except Exception:
        config = None
    members = [check_member(p, cli, label, adapter=adapters.get(p), config=config) for p, cli, label in MEMBERS]
    ready = [s.provider for s in members if s.ready]
    # Member models come from whichever config dispatch will load. When there is
    # no config.json, that is the bundled example, and the report says so
    # rather than presenting example models as the user's choice.
    cfg_path = config_path()
    config_source = str(cfg_path) if cfg_path.exists() else f"bundled defaults (no config.json at {cfg_path})"
    return {
        "config_source": config_source,
        "members": [dict(asdict(s), ready=s.ready) for s in members],
        "ready": ready,
        "council_ready": len(ready) >= 2,
        "transcripts_total": sum(s.transcripts for s in members),
        "next": {
            "asks": list(NEXT_ASKS),
            "why_tests_only": ("Your tests are the proof. The other labs' read is a second opinion: "
                               "on 103 agent-written commits its flags did not pick out the ones "
                               "later fixed (hq_122)."),
        },
        "writes_nothing": True,
    }


def format_check(report: dict[str, Any]) -> str:
    out = ["Trinity install check (read-only: nothing was changed)", ""]
    for s in report["members"]:
        mark = "✅" if s["ready"] else "⚠️ "
        reg = {True: "registered", False: "NOT registered", None: "config unreadable"}[s["registered"]]
        ver = f" {s['version']}" if s["version"] else ""
        out.append(f"  {mark} {s['label']}{ver}")
        out.append(f"       installed: {'yes' if s['installed'] else 'no'} · sign-in file: "
                   f"{'found' if s['signed_in'] else 'missing'} · Trinity: {reg} · "
                   f"transcripts read: {s['transcripts']:,}")
        if s["member_model"]:
            out.append(f"       council member model: {s['member_model']}" + ("" if s["member_enabled"] else " (disabled)"))
        for f in s["fixes"]:
            out.append(f"       fix: {f}")
        for n in s["notes"]:
            out.append(f"       note: {n}")
    out.append(f"  Member models from: {report['config_source']}")
    out.append("")
    n = len(report["ready"])
    out.append(f"  {n} of 3 ready. " + ("Councils can compare labs." if report["council_ready"]
                                        else "A council needs at least two ready CLIs."))
    out.append(f"  Transcripts Trinity reads across all three: {report['transcripts_total']:,} (they stay on this machine).")
    out.append("")
    out.append("  Next, in Claude Code, Codex or agy (restart them first so they load Trinity):")
    for ask in report["next"]["asks"]:
        out.append(f"    {ask}")
    out.append(f"  {report['next']['why_tests_only']}")
    return "\n".join(out)
