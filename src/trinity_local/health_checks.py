"""Pre-flight cold-install checks for Trinity.

Per `council_35b2ae198a65b349`: the audit-missed launch blocker is a fresh
user running `lens-build` without provider auth, hanging silently, and
blaming Trinity. The eval seed: *"name a specific cold-install failure
mode AND the exact CLI command that detects it before the user hits a
live council."*

`trinity-local status` runs these checks (the standalone `doctor`
command was collapsed into `status` pre-launch). The module was
named `doctor.py` until 2026-05-27 when it was renamed to match its
actual job (the `doctor` name was a misleading-name parasitism flag).
Each check returns:
- ok: bool
- name: short human label
- detail: what it found
- fix: one-line command the user runs to resolve

Doctor never makes network calls and never invokes a chairman; it's pure
filesystem + subprocess version probes. <1s on a working install.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state_paths import embedder_install_command, state_dir


# Provider-specific auth indicators. We don't probe a live API call (that
# would require user input on auth prompts and add latency). We check for
# the indicator files each CLI writes after the user authenticates once.
_AUTH_INDICATORS = {
    "claude": [
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".claude" / "config.json",
        Path.home() / ".claude.json",  # Claude Code's project config
    ],
    "codex": [
        Path.home() / ".codex" / "auth.json",
        Path.home() / ".codex" / "config.toml",
    ],
    "antigravity": [
        Path.home() / ".gemini" / ".credentials" / "credentials.json",
        Path.home() / ".gemini" / "settings.json",
    ],
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def ready_for_council(self) -> bool:
        """Minimum bar: ≥1 provider ready + Trinity dir writeable."""
        provider_checks = [c for c in self.checks if c.name.startswith("provider:")]
        ready_providers = sum(1 for c in provider_checks if c.ok)
        trinity_ok = next((c.ok for c in self.checks if c.name == "trinity_home_writeable"), False)
        return ready_providers >= 1 and trinity_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "all_ok": self.all_ok,
            "ready_for_council": self.ready_for_council,
        }


def _check_trinity_home() -> CheckResult:
    """state_dir() itself can raise on a read-only parent — wrap the whole
    thing in one try block so the doctor surfaces the failure as a check
    result instead of bubbling up as an exception."""
    try:
        home = state_dir()
        probe = home / ".doctor_write_probe"
        probe.write_text("ok")
        probe.unlink()
        return CheckResult(
            name="trinity_home_writeable",
            ok=True,
            detail=f"{home} writeable",
        )
    except OSError as exc:
        # state_dir() failed before we got home, or write probe failed.
        # Read the env var directly for the user-facing error so they know
        # which path they need to fix.
        import os
        target = os.environ.get("TRINITY_HOME") or str(Path.home() / ".trinity")
        return CheckResult(
            name="trinity_home_writeable",
            ok=False,
            # Show the path the user configured + the error TYPE only — the raw
            # OSError str can carry a second errno/path; the fix already names
            # the path to chmod, so the raw payload adds no actionable value.
            detail=f"{target} not writeable ({type(exc).__name__})",
            fix=f"chmod u+w {target} OR set TRINITY_HOME=/path/to/writeable/dir",
        )


def _check_provider(provider: str, cli_name: str) -> CheckResult:
    """Three sub-checks merged into one CheckResult: installed → auth indicator
    present → recently used (transcript file modified < 90 days ago).

    Why one merged check: from the user's perspective, "claude is ready" is
    one bit. The detail string surfaces which sub-check failed for the fix.
    """
    # Resolve against the SAME enriched PATH the council runner dispatches with
    # (which_on_runtime_path), so `status` AGREES with the launchpad tier card +
    # the dispatch gate (test_phase8_integration::...doctor_launchpad_agree). A
    # bare shutil.which would report a Homebrew provider as "not on PATH" under a
    # GUI-launched IDE even though the runner can dispatch to it.
    from .runtime_env import which_on_runtime_path

    installed = which_on_runtime_path(cli_name) is not None
    if not installed:
        return CheckResult(
            name=f"provider:{provider}",
            ok=False,
            detail=f"{cli_name} CLI not on PATH",
            fix=_install_command_for(provider),
        )

    indicators = _AUTH_INDICATORS.get(provider, [])
    auth_seen = any(p.exists() for p in indicators)
    if not auth_seen:
        return CheckResult(
            name=f"provider:{provider}",
            ok=False,
            detail=f"{cli_name} installed but no auth indicator file found",
            fix=f"{cli_name} login   # or run any one-shot {cli_name} command interactively",
        )

    return CheckResult(
        name=f"provider:{provider}",
        ok=True,
        detail=f"{cli_name} installed and authenticated",
    )


def _install_command_for(provider: str) -> str:
    """Single-line install hints — same canonical strings as
    launchpad_data._TIER_INSTALL_HELP + _provider_install_help() (the
    user-facing setup-card surface). Iter #40 harmonized these after
    iter #39 caught the launchpad's internal divergence; the
    test_install_commands_match_across_surfaces guard pins all three
    surfaces to the same per-provider command so a fix hint from
    `status` matches what the launchpad teaches."""
    return {
        "claude": "npm install -g @anthropic-ai/claude-code",
        "codex": "npm install -g @openai/codex && codex --login",
        "antigravity": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
    }.get(provider, f"install the {provider} CLI")


def _check_config() -> CheckResult:
    """Config loadable + at least one provider enabled."""
    try:
        from .config import load_config
        cfg = load_config(required=False)
        if cfg is None:
            return CheckResult(
                name="config_loadable",
                ok=False,
                detail="config.json not found and no defaults available",
                fix="trinity-local install-mcp   # creates a default config",
            )
        enabled = [n for n, p in cfg.providers.items() if p.enabled and p.type in ("cli", "codex")]
        if not enabled:
            return CheckResult(
                name="config_loadable",
                ok=False,
                detail="config.json has no enabled CLI providers",
                fix="edit config.json — enable at least one of {claude, antigravity, codex}",
            )
        return CheckResult(
            name="config_loadable",
            ok=True,
            detail=f"config OK · enabled providers: {', '.join(enabled)}",
        )
    except Exception as exc:
        return CheckResult(
            name="config_loadable",
            ok=False,
            detail=f"config load failed ({type(exc).__name__})",
            fix="trinity-local install-mcp   # rewrites a default config",
        )


def _check_mcp_available() -> CheckResult:
    """MCP server module importable (the `mcp` extras dependency installed)."""
    try:
        import mcp  # noqa: F401
        return CheckResult(
            name="mcp_available",
            ok=True,
            detail="mcp package importable",
        )
    except ImportError:
        return CheckResult(
            name="mcp_available",
            ok=False,
            detail="mcp package not installed (Claude Code MCP integration disabled)",
            fix="python3 -m pip install --user 'mcp>=1.0' 'Pillow>=10' 'numpy>=1.26'",
        )


def _check_skill_freshness() -> CheckResult:
    """Auto-CHECK leg of automatic updates: report whether the cloned
    skill repo is behind origin/main.

    No network call by default — uses git's cached refs which were
    last updated by `git fetch`. Users who want fresher staleness
    info can run `trinity-local update --check` (which does a real
    fetch). This keeps `status` fast (<200ms) while still surfacing
    the "you should update" signal in the common case where the
    fetch happened recently (last update, last install, etc.).
    (The `doctor` CLI was absorbed into `status` 2026-05-18 per
    retired_names.py; this function remains the underlying check.)

    Trust positioning: this surfaces "you're behind" — never auto-
    pulls. The user runs `trinity-local update` to apply.
    """
    import os
    import subprocess
    from pathlib import Path

    skill_dir = Path(os.environ.get(
        "TRINITY_SKILL_DIR", Path.home() / ".claude" / "skills" / "trinity"
    ))
    if not (skill_dir / ".git").exists():
        # Not a git checkout (or skill not installed via install.sh) —
        # nothing to compare against. This is fine (some users may run
        # straight from a repo clone for dev).
        return CheckResult(
            name="skill_freshness",
            ok=True,
            detail=(
                f"skill at {skill_dir} is not a git checkout; "
                "freshness check skipped"
            ),
        )

    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=skill_dir, capture_output=True, text=True, check=False,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return CheckResult(
            name="skill_freshness", ok=True,
            detail="git not available; freshness check skipped",
        )
    if result.returncode != 0:
        return CheckResult(
            name="skill_freshness", ok=True,
            detail="no origin/main ref cached; "
                   "run `trinity-local update --check` to fetch",
        )
    behind = int(result.stdout.strip() or "0")
    if behind == 0:
        return CheckResult(
            name="skill_freshness", ok=True,
            detail="skill is up to date with origin/main (cached refs)",
        )
    return CheckResult(
        name="skill_freshness", ok=False,
        detail=(
            f"skill is {behind} commit(s) behind origin/main "
            "(per cached refs; run --check for a fresh fetch)"
        ),
        fix="trinity-local update   # pulls + refreshes MCP + verifies",
    )


def _check_dispatch_ready() -> CheckResult:
    """Extension dispatch readiness — the Chrome extension's Native
    Messaging host must be reachable for the launchpad to do anything.

    The macOS Shortcut tier was retired 2026-05-17 (Chrome extension is
    now the cross-platform launchpad host); this check covers macOS,
    Linux, and Windows with the same shape.

    Surfaces the same `recommended_action` hint the launchpad shows in
    its inline banner so the doctor and the launchpad agree.
    """
    try:
        from .launchpad_data import dispatch_readiness
    except Exception as exc:
        return CheckResult(
            name="dispatch_ready",
            ok=False,
            detail=f"could not import dispatch_readiness ({type(exc).__name__})",
        )
    readiness = dispatch_readiness()
    if readiness["ready"]:
        return CheckResult(
            name="dispatch_ready",
            ok=True,
            detail="launchpad dispatch ready via Chrome extension",
        )
    fix_command = (
        "trinity-local install-extension --extension-id <ID>   "
        "# 1. Load browser-extension/ in chrome://extensions first."
    )
    return CheckResult(
        name="dispatch_ready",
        ok=False,
        detail=readiness["recommended_action"] or "no dispatch path wired",
        fix=fix_command,
    )


def _check_prompts_seeded() -> CheckResult:
    """Soft check: do we have any prompt history? Doctor passes either way
    (a fresh install is legitimately empty), but surfaces a hint.

    Reads from ~/.trinity/prompts/prompt_nodes.jsonl (renamed from
    `memory/` per the brand axis: prompts are raw, memories is the
    consolidated output).
    """
    # state_dir() / "memory" was the v1.0 path; the renamed location is
    # state_dir() / "prompts". `prompts_dir()` resolves either via the
    # in-place migration helper.
    from .state_paths import prompts_dir
    nodes = prompts_dir() / "prompt_nodes.jsonl"
    if not nodes.exists() or nodes.stat().st_size == 0:
        return CheckResult(
            name="prompts_seeded",
            ok=True,  # not blocking — first-time users have empty memory
            detail="no transcripts seeded yet (you can still run councils)",
            fix="trinity-local import-export <path-to-export>   # ChatGPT / Claude.ai / Gemini Takeout ingest",
        )
    # Approximate count from line count
    line_count = sum(1 for _ in nodes.open())
    return CheckResult(
        name="prompts_seeded",
        ok=True,
        detail=f"{line_count} prompt nodes indexed at ~/.trinity/prompts/",
    )


# Pre-registered floor: catches the #235 disaster (66% unembedded) while passing a
# healthy corpus whose only gap is a recent-ingest frontier awaiting incremental
# embedding (measured 2026-06-03: 92.7% coverage, the 7.3% gap being purely the last
# two days' nodes). The semantic layer (basins, cortex centroids, lens Stage-4) can
# only place EMBEDDED nodes, so a silent backfill stall degrades everything downstream
# while _check_embedding_backend (backend live?) and _check_prompts_seeded (prompts
# exist?) both stay green — the exact gap that let #235 hide. 0.70 is the registered
# green-gate floor on the invariant "most of the corpus is embedded."
_EMBED_COVERAGE_FLOOR = 0.70
_EMBED_COVERAGE_MIN_NODES = 500  # below this, ingestion is still ramping — don't flag


def _check_embedding_coverage() -> CheckResult:
    """Soft check: what FRACTION of the corpus is actually embedded? #235 (backfill
    stalled ~May 12 -> 66% unembedded) was dangerous precisely because nothing
    surfaced it: the backend was live and prompts existed, but two-thirds of the
    corpus had empty embeddings, so semantic search / basins / the lens silently ran
    on a third of the data. This pins the coverage floor (data_sampling_principle:
    measure coverage, not presence). Streaming substring scan (~0.6s on a 568MB
    corpus — no JSON/float parse), so it fits the deliberate `status` action.
    Unembedded nodes carry an empty array (``"embedding": []``, the cheap-write
    ingest path); everything else is a populated 768d vector."""
    from .state_paths import prompts_dir

    nodes = prompts_dir() / "prompt_nodes.jsonl"
    if not nodes.exists() or nodes.stat().st_size == 0:
        return CheckResult(name="embedding_coverage", ok=True,
                           detail="no corpus yet (nothing to embed)")
    total = empty = 0
    try:
        with nodes.open("rb") as fh:
            for line in fh:
                total += 1
                if b'"embedding": []' in line or b'"embedding":[]' in line:
                    empty += 1
    except OSError as exc:
        return CheckResult(name="embedding_coverage", ok=True,
                           detail=f"could not read corpus: {exc.__class__.__name__}")
    if total == 0:
        return CheckResult(name="embedding_coverage", ok=True, detail="empty corpus")
    embedded = total - empty
    pct = embedded / total * 100.0
    if total < _EMBED_COVERAGE_MIN_NODES or pct / 100.0 >= _EMBED_COVERAGE_FLOOR:
        return CheckResult(
            name="embedding_coverage", ok=True,
            detail=f"{pct:.1f}% embedded ({embedded}/{total}); {empty} pending",
        )
    # Below the floor: not a broken install (it self-heals on the next embed pass),
    # but the semantic layer is degraded NOW — surface it (ok=True + fix per #273).
    return CheckResult(
        name="embedding_coverage", ok=True,
        detail=f"only {pct:.1f}% of the corpus is embedded ({empty}/{total} nodes have empty vectors)",
        fix="the MLX embedder backfills incrementally on your next active session "
            "(any MCP tool call, or `trinity-local lens`); if it stays low the "
            "backfill has stalled (#235) and semantic search + the lens are running "
            "on the embedded fraction only.",
    )


def _check_lens_built() -> CheckResult:
    """Soft check: has the lens been built? Doctor passes either way.

    Reads from ~/.trinity/memories/lens.md (renamed from `me.md` per
    the 5-memories restructure)."""
    from .state_paths import lens_path
    lens = lens_path()
    if not lens.exists():
        return CheckResult(
            name="lens_built",
            ok=True,  # not blocking
            detail="lens not built yet",
            fix="trinity-local lens   # builds your taste lenses (after running a few councils)",
        )
    size = lens.stat().st_size
    return CheckResult(
        name="lens_built",
        ok=True,
        detail=f"~/.trinity/memories/lens.md present ({size} bytes)",
    )


def _check_core_distilled() -> CheckResult:
    """Soft check: has the singular core.md distillation been built?

    core.md is what the chairman reads FIRST on every council — when
    missing, chairman falls through to the full lens.md (more context,
    longer prompts). Surface the upgrade path."""
    from .state_paths import core_path
    core = core_path()
    if not core.exists():
        return CheckResult(
            name="core_distilled",
            ok=True,
            detail="core.md not distilled yet (chairman falls through to full lens)",
            fix="trinity-local dream   # rebuild memories; Phase 5 writes ~/.trinity/core.md (distill CLI was hidden 2026-05-17 — dream is the live path)",
        )
    size = core.stat().st_size
    return CheckResult(
        name="core_distilled",
        ok=True,
        detail=f"~/.trinity/core.md present ({size} bytes)",
    )


def _check_vendor_published() -> CheckResult:
    """Soft check: are all VENDORED_FILES present under portal_pages/vendor/?

    vendor.py publishes 12 bundled JS files (petite-vue, chart.js, marked,
    9 d3-* modules) into `~/.trinity/portal_pages/vendor/` on every
    `refresh_launchpad`. The privacy claim ("never leaves your machine")
    is structural — every render references `./vendor/<file>.js`. If the
    publish silently failed at install (perms / disk full / etc.),
    `vendor.py`'s stderr warning will tell whoever ran install-mcp, but
    a user who clicks the launchpad days later sees broken `./vendor/*`
    404s with no surface that explains it. This check closes that loop.

    Soft (ok=True regardless of result). When some vendor files are
    missing, the detail names how many + suggests the one-liner that
    re-publishes them. Re-running `trinity-local portal-html` (or any
    `refresh_launchpad`-touching command) re-publishes.
    """
    try:
        from .state_paths import portal_pages_dir
        from .vendor import VENDORED_FILES
    except Exception as exc:
        return CheckResult(
            name="vendor_published",
            ok=True,
            detail=f"could not check vendor files ({type(exc).__name__})",
        )
    vendor_dir = portal_pages_dir() / "vendor"
    if not vendor_dir.exists():
        return CheckResult(
            name="vendor_published",
            ok=True,
            detail=(
                "vendor/ not yet populated — run "
                "`trinity-local portal-html` to publish "
                f"{len(VENDORED_FILES)} vendored assets (JS + fonts) into "
                "~/.trinity/portal_pages/vendor/"
            ),
        )
    missing = [n for n in VENDORED_FILES if not (vendor_dir / n).exists()]
    if missing:
        sample = ", ".join(missing[:3])
        suffix = "" if len(missing) <= 3 else f" (+{len(missing) - 3} more)"
        return CheckResult(
            name="vendor_published",
            ok=True,
            detail=(
                f"{len(missing)} of {len(VENDORED_FILES)} vendored assets "
                f"missing ({sample}{suffix}) — launchpad will 404 "
                f"on those scripts. Re-run `trinity-local portal-html` "
                f"to republish. If it fails again, check perms on "
                f"{vendor_dir}."
            ),
        )
    return CheckResult(
        name="vendor_published",
        ok=True,
        detail=f"all {len(VENDORED_FILES)} vendored assets present",
    )


# Cortex-staleness primitives moved to cortex.py (v1.7.299) so the CLI doctor and
# the launchpad cockpit compute "councils newer than the last consolidate" from ONE
# implementation and can't drift. Re-exported here under the old private names so
# existing references + tests keep working.
from .cortex import (  # noqa: E402
    count_councils_newer_than as _count_councils_newer_than,
    freshest_consolidated_at as _freshest_consolidated_at,
)


def _check_cortex_freshness() -> CheckResult:
    """Soft check: are cortex picks current relative to recent councils?

    `picks.json` carries `consolidated_at` per task_type. If any council
    outcome on disk is newer than the freshest `consolidated_at`, the
    cortex layer's routing rules don't yet reflect the new training
    data — `ask()` will route based on stale signal until the user
    re-runs `consolidate`. Tick #96 noticed this concretely: real
    corpus had 19 outcomes but picks.json was based on 2.

    Soft check: ok stays True (stale picks aren't broken, just dated).
    Detail surfaces the count so the user can decide whether to
    re-consolidate. Pre-rated user (no chairman verdicts yet) gets
    a different message than rated-but-stale.
    """
    from .state_paths import picks_path
    picks = picks_path()
    if not picks.exists():
        return CheckResult(
            name="cortex_freshness",
            ok=True,
            detail="picks.json not built yet — run `trinity-local consolidate` once you have ≥10 rated councils",
        )
    try:
        picks_data = json.loads(picks.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CheckResult(
            name="cortex_freshness",
            ok=True,
            detail="picks.json unreadable — re-run `trinity-local consolidate`",
        )
    freshest_picks = _freshest_consolidated_at(picks_data)
    if freshest_picks is None:
        return CheckResult(
            name="cortex_freshness",
            ok=True,
            detail="picks.json has no task_types yet — re-run `trinity-local consolidate`",
        )
    newer, total = _count_councils_newer_than(freshest_picks)
    if newer == 0:
        return CheckResult(
            name="cortex_freshness",
            ok=True,
            detail=f"picks.json current ({total} outcomes, all consolidated)",
        )
    return CheckResult(
        name="cortex_freshness",
        ok=True,  # soft — not a failure, just outdated
        detail=(
            f"{newer} of {total} councils are newer than the last consolidate "
            f"— ask routes on stale rules"
        ),
        # `fix` is load-bearing here, not decoration: status' soft-warning loop
        # (status.py: `if c.ok and c.fix`) keys on `fix`, so WITHOUT it this
        # stale-cortex result counts toward "all green" and its detail is never
        # printed. Live 2026-05-31: a 268-of-559-stale cortex was invisible in
        # `status` (the install-recommended verify command) while the launchpad
        # surfaced it prominently. Setting fix surfaces it in both, consistently.
        fix="trinity-local consolidate",
    )


def _check_cortex_basin_density() -> CheckResult:
    """Soft check: are the cortex basins DENSE enough to route confidently?

    The margin gate (now `lens_routing.MARGIN_FLOOR`, applied by `place_query`)
    routes only when a query is clearly closest to ONE basin (top1−top2 ≥ floor),
    abstaining on near-ties. Its
    strength depends on basin density: sparse basins (few episodes) give
    under-determined centroids, so real queries align WEAKLY with all of them
    (~0.38 sim to each) and the margins compress below the gate — routing
    correctly abstains, but on MORE queries than a dense corpus would. Measured
    2026-06-02 on the founder's corpus: 9 routing basins, median n_episodes=3,
    inter-centroid cosine ~0.43 (SEPARATED, not overlapping) — the limit is
    sparsity, not overlap. The fix is more rated councils per basin (densify),
    NOT a lower margin floor. This surfaces the lever so a user whose routing
    "feels conservative" has the reason. Soft (ok=True + fix); best-effort.
    """
    from .state_paths import picks_path

    picks = picks_path()
    if not picks.exists():
        return CheckResult(name="cortex_basin_density", ok=True,
                           detail="no cortex basins yet (picks.json not built)")
    try:
        data = json.loads(picks.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CheckResult(name="cortex_basin_density", ok=True, detail="picks.json unreadable")
    if not isinstance(data, dict):
        return CheckResult(name="cortex_basin_density", ok=True, detail="picks.json wrong shape")
    # POST-COLLAPSE (#298): a pick is routing-capable when it carries a `winner`
    # tally with an episode count — placement reads the lens centroids
    # (topics.json), so picks no longer carry their own centroid. A legacy/
    # malformed entry (missing `winner`) is skipped.
    # `isinstance(..., str)` shape-guards the STRING field: picks.json is a hand-
    # editable state file, so a corrupt `winner` (a NUMBER from a half-migrated /
    # mangled entry) would hit `.strip()` on an int and CRASH this status check
    # (the launchpad-render sibling fixed alongside, Iter 257). Treat non-string as
    # absent → the basin is skipped, the check still runs.
    eps = [
        int(v["n_episodes"])
        for v in data.values()
        if isinstance(v, dict) and isinstance(v.get("winner"), str) and v["winner"].strip()
        and isinstance(v.get("n_episodes"), int)
    ]
    if not eps:
        return CheckResult(name="cortex_basin_density", ok=True,
                           detail="no routing-capable basins (none carry a winner tally)")
    import statistics

    median_eps = statistics.median(eps)
    # Pre-registered: stable centroids want ~10+ episodes; below 8 = sparse enough
    # that query alignment is weak and the margin gate abstains broadly.
    DENSE_EPISODE_FLOOR = 8
    if median_eps >= DENSE_EPISODE_FLOOR:
        return CheckResult(
            name="cortex_basin_density", ok=True,
            detail=f"{len(eps)} routing basins, median n_episodes={median_eps:.0f} — dense enough to route confidently",
        )
    # Sparse — but WHY? Two causes with OPPOSITE fixes:
    #   (a) STALE consolidation: councils exist on disk that aren't folded into any
    #       basin yet → re-consolidate NOW (the corpus is rich, the picks are dated).
    #   (b) genuinely small corpus: ~everything is already consolidated → density
    #       only rises as MORE rated councils accumulate.
    # Measured 2026-06-02 on the founder's corpus this is (a): median n=3 but 271 of
    # 562 councils were un-consolidated — the basins held only ~36 of the 562. Emitting
    # the (b) "run more / wait" remedy there is a wrong-fix green (a user reads "corpus
    # too small" and does nothing, when one `consolidate` would densify ~15×). So we
    # disambiguate from the same signal the freshness check uses.
    freshest = _freshest_consolidated_at(data)
    newer, _ = _count_councils_newer_than(freshest) if freshest else (0, 0)
    if newer >= DENSE_EPISODE_FLOOR:
        # (a) stale: re-consolidating would materially densify. This is the strong fix.
        return CheckResult(
            name="cortex_basin_density",
            ok=True,
            detail=(
                f"{len(eps)} routing basins are SPARSE (median n_episodes={median_eps:.0f}, "
                f"recommend ~{DENSE_EPISODE_FLOOR}+), but {newer} councils on disk aren't "
                "consolidated into basins yet — the sparsity is a STALE consolidation, not a "
                "small corpus. Re-consolidate to fold them in and densify; the margin gate "
                "sharpens as density rises — NOT a lower margin floor."
            ),
            fix="trinity-local consolidate",
        )
    # (b) genuinely small corpus.
    return CheckResult(
        name="cortex_basin_density",
        ok=True,  # soft — routing still works, just precision-limited
        detail=(
            f"{len(eps)} routing basins are SPARSE (median n_episodes={median_eps:.0f}, "
            f"recommend ~{DENSE_EPISODE_FLOOR}+). The margin gate abstains on near-ties "
            "until centroids densify, so cortex routing fires on fewer queries than a "
            "fuller corpus would — the fix is more rated councils per basin, NOT a lower "
            "margin floor."
        ),
        fix="trinity-local consolidate   # after more rated councils accumulate, to densify basins",
    )


def _check_lens_freshness() -> CheckResult:
    """Soft check: are the lens artifacts (vocabulary.md / topics.json) current?

    lens.md auto-refreshes via the activity-gated MCP lens-build, but
    vocabulary.md + topics.json only rebuild on a full `dream` — so they drift
    stale as new prompts/councils arrive AND as code fixes land. Live 2026-06-01:
    the served vocabulary.md still carried the test-file homonym that v1.7.152
    drops, because the file predated the fix. Soft (ok=True) with
    fix='trinity-local dream' so status' soft-warning loop surfaces it.
    """
    from .state_paths import memories_dir, council_outcomes_dir

    mem = memories_dir()
    existing = [a for a in (mem / "vocabulary.md", mem / "topics.json") if a.exists()]
    if not existing:
        return CheckResult(
            name="lens_freshness",
            ok=True,
            detail="lens vocab + topics not built yet — run `trinity-local dream`",
            fix="trinity-local dream",
        )
    newest = 0.0
    outcomes = council_outcomes_dir()
    if outcomes.is_dir():
        for p in outcomes.glob("council_*.json"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
    try:
        mtimes = {a: a.stat().st_mtime for a in existing}
    except OSError:
        return CheckResult(name="lens_freshness", ok=True, detail="lens artifacts present")
    # 1-day grace so a single fresh council an hour after a rebuild doesn't nag —
    # only flag a MEANINGFULLY stale lens. (lens.md auto-refreshes; this is for
    # vocab/topics which only a full `dream` rebuilds.) Name only the artifacts
    # that are ACTUALLY stale — rebuilding just the vocab shouldn't make the
    # message keep blaming vocabulary.md (verified live 2026-06-01).
    stale = [a for a in existing if (newest - mtimes[a]) >= 86400]
    if not stale:
        return CheckResult(name="lens_freshness", ok=True, detail="lens vocab + topics current")
    days = (newest - min(mtimes[a] for a in stale)) / 86400.0
    names = " + ".join(a.name for a in stale)
    verb = "predate" if len(stale) > 1 else "predates"
    return CheckResult(
        name="lens_freshness",
        ok=True,  # soft — stale, not broken
        detail=(
            f"{names} {verb} your newest council by ~{days:.0f}d "
            f"— re-run dream to refresh the lens"
        ),
        fix="trinity-local dream",
    )


def _check_data_degeneracy() -> CheckResult:
    """Soft check: run the real-data degeneracy sweep (trinity_local.degeneracy)
    — eval prompt==gold, vocab code-identifiers, basin scaffolding, Elo web-era
    leak, routing legacy slugs. The recurring 'green-while-degenerate' class;
    surfaced here so `status` (the install-verify surface) catches them. Soft: a
    finding is dated/degenerate data, not a broken install.
    """
    try:
        from .degeneracy import run_degeneracy_sweep

        findings = run_degeneracy_sweep()
    except Exception as e:  # noqa: BLE001
        return CheckResult(name="data_degeneracy", ok=True, detail=f"sweep skipped ({type(e).__name__})")
    if not findings:
        return CheckResult(
            name="data_degeneracy",
            ok=True,
            detail="no degenerate data on the known producer surfaces",
        )
    return CheckResult(
        name="data_degeneracy",
        ok=True,  # soft
        detail=f"{len(findings)} degenerate-data finding(s): {'; '.join(findings)[:240]}",
        fix="python scripts/degeneracy_sweep.py   # see all findings + the fix per class",
    )


def _check_embedding_backend() -> CheckResult:
    """Degraded-honesty (#238): is the real MLX embedder live, or is Trinity
    silently on the SHA-1 TF-IDF fallback?

    Why this is load-bearing: without the `[mlx]` extras (or with the extras
    but no downloaded nomic model), every embedding silently falls back to a
    stable-but-lexical TF-IDF projection. Lens-build's Stage-4 semantic
    filtering then runs on lexical vectors — exactly the #185/#186 latent
    degradation where the lens looks built but the tension geometry is
    keyword-shaped, not meaning-shaped. The fallback is intentional graceful
    degradation; the SIN is doing it silently. This check tells the user the
    truth so a thin lens has an explanation.

    Soft (ok=True) — Trinity still runs on TF-IDF; this is a quality signal,
    not a blocker. Best-effort: an import blow-up must not break `status`.
    """
    try:
        from .embeddings import get_backend, is_available, mlx_actually_loaded
        from .embeddings.backend_mlx import MODEL_ID
        _model_name = MODEL_ID.split("/")[-1]
    except Exception as exc:
        return CheckResult(
            name="embedding_backend",
            ok=True,
            detail=f"could not probe embedding backend ({type(exc).__name__})",
        )

    try:
        backend = get_backend()
        imported = is_available()
        # mlx_actually_loaded() does a real probe embed — distinguishes
        # "mlx module imported" from "mlx can actually produce vectors".
        live = mlx_actually_loaded()
    except Exception as exc:
        return CheckResult(
            name="embedding_backend",
            ok=True,
            detail=f"embedding backend probe failed ({type(exc).__name__})",
        )

    if live:
        return CheckResult(
            name="embedding_backend",
            ok=True,
            detail=f"MLX embeddings live ({_model_name}, 768d semantic vectors)",
        )

    # On the TF-IDF fallback. The standard message says "future lens quality is
    # reduced". But if a REAL nomic lens was already built under a prior MLX
    # session (768d basins on disk), the runtime falling back to TF-IDF doesn't
    # just degrade future builds — it makes the EXISTING lens DORMANT (the
    # semantic flows abstain on the space mismatch, the cortex cross-space guard
    # rejects the centroids). That's a louder signal: you're not missing quality,
    # you're losing an investment you already have. Found 2026-06-02 — the founder
    # ran TF-IDF with a dormant 48-basin nomic lens + 172-correction taste signal.
    dormant = _dormant_nomic_lens_basins()
    dormant_note = (
        f" CRITICAL: you ALSO have a real {_model_name} lens already built "
        f"({dormant} 768d basins) sitting DORMANT — the runtime fell back to "
        "TF-IDF so the semantic flows abstain and the cortex can't use it. "
        "Installing the extras REACTIVATES that existing lens (not just future "
        "builds); you're currently losing taste signal you already paid for."
        if dormant else ""
    )

    # is_available()/backend=="mlx" is TRUE even WITHOUT the extras: MlxEmbedder()
    # instantiates lazily (torch is imported only at the first embed), so a
    # default install WITHOUT the [mlx] extras reports backend "mlx" +
    # is_available() True while torch/mlx-embeddings are absent. Probing find_spec separates the
    # two distinct degradations — "extras present, model not downloaded" vs
    # "extras not installed at all" — instead of collapsing them into a false
    # "MLX extras installed, just download the model" that sent the most common
    # install (no [mlx]) to fetch a model it has no deps to load (verified in a
    # clean venv 2026-06-06). find_spec checks installability without the heavy
    # import.
    def _mlx_extras_present() -> bool:
        import importlib.util as _ilu
        for mod in ("mlx_embeddings", "torch"):
            try:
                if _ilu.find_spec(mod) is not None:
                    return True
            except Exception:
                continue
        return False

    if imported and backend == "mlx" and _mlx_extras_present():
        # Extras installed but the model never produced a vector — almost
        # always the weights aren't downloaded yet.
        return CheckResult(
            name="embedding_backend",
            ok=True,  # soft — TF-IDF still works
            detail=(
                "MLX extras installed but the embedder can't produce vectors "
                f"({_model_name} not downloaded) — Trinity is on the lexical TF-IDF "
                "fallback, so lens tensions are keyword-shaped, not meaning-shaped." + dormant_note
            ),
            fix="HF_HUB_OFFLINE=0 trinity-local download-embedder   # then `trinity-local lens --force`",
        )

    return CheckResult(
        name="embedding_backend",
        ok=True,  # soft — TF-IDF is a deliberate fallback
        detail=(
            "running on the SHA-1 TF-IDF embedding fallback (MLX extras not "
            "installed) — the lens still builds, but on lexical (keyword) "
            "vectors rather than semantic ones, so tension quality is reduced." + dormant_note
        ),
        fix=f"{embedder_install_command()} && HF_HUB_OFFLINE=0 trinity-local download-embedder",
    )


def _dormant_nomic_lens_basins() -> int:
    """Count nomic-space (768d) basins already on disk — a lens built under a
    prior MLX session that the CURRENT TF-IDF runtime can't use (DORMANT, not
    stale). Returns 0 when no such lens exists. Best-effort; never raises."""
    try:
        import json

        from .state_paths import trinity_home
        topics = trinity_home() / "memories" / "topics.json"
        if not topics.exists():
            return 0
        data = json.loads(topics.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0
        return sum(
            1
            for b in data.get("basins", [])
            if isinstance(b, dict)
            and isinstance(b.get("centroid"), list)
            and len(b["centroid"]) == 768
        )
    except Exception:
        return 0


def _check_council_breadth() -> CheckResult:
    """Degraded-honesty (#238): a council needs ≥2 authed providers to be a
    *council* — one voice plus a chairman is just a single-model answer with
    extra latency.

    `ready_for_council` (the launch bar) only requires ONE provider, which is
    correct: Trinity must still run with one CLI authed. But running with one
    is a REDUCED mode, and the prior surfaces never said so out loud — the
    user could authenticate only Claude, run "a council", and get a
    single-member result silently dressed up as cross-provider deliberation.
    The asymmetric value (cross-provider disagreement no single lab can see)
    is exactly what's missing at n=1. This check names it.

    Counts the same provider-auth indicators `_check_provider` uses, so the
    breadth verdict matches the per-provider rows.

    ok=True when ≥2 are ready (real council possible). ok=False (soft gap,
    not a hard blocker) when exactly 1 is ready — honest "reduced" signal.
    Returns a distinct detail for the 0-ready case so the message isn't a
    lie about a council that can't run at all.
    """
    ready: list[str] = []
    for provider, cli in (("claude", "claude"), ("codex", "codex"), ("antigravity", "agy")):
        if _check_provider(provider, cli).ok:
            ready.append(provider)

    if len(ready) >= 2:
        return CheckResult(
            name="council_breadth",
            ok=True,
            detail=f"{len(ready)} providers authed ({', '.join(ready)}) — full cross-provider council available",
        )
    if len(ready) == 1:
        return CheckResult(
            name="council_breadth",
            ok=False,  # soft gap — councils run, but in reduced single-voice mode
            detail=(
                f"only 1 provider authed ({ready[0]}) — councils run in REDUCED mode "
                "(one voice + chairman, no cross-provider disagreement). Trinity's "
                "asymmetric edge needs ≥2 providers."
            ),
            fix="auth a second CLI (e.g. `codex --login` or run any one-shot `agy`/`claude` command)",
        )
    return CheckResult(
        name="council_breadth",
        ok=False,
        detail="no providers authed — councils cannot run yet",
        fix="install + authenticate at least one CLI: claude, codex, or agy",
    )


def _check_browser_capture() -> CheckResult:
    """v1.6 browser-capture preflight.

    Three install stages; first failure wins. All SOFT (ok=True) — the
    extension is optional. The user might be CLI-only and never want
    browser capture.

    Stage 1 — host binary on PATH. ``trinity-local install-extension``
    refuses to write the Native Messaging manifest until this exists.
    If missing, the wheel installed without the v1.6 console script
    (pre-v1.6 install of the package).

    Stage 2 — Chrome Native Messaging manifest written. Chrome only
    knows to spawn the host if its per-user
    ``NativeMessagingHosts/local.trinity.capture.json`` exists with
    Trinity's extension ID in ``allowed_origins``.

    Stage 3 — at least one capture exists. If both above pass but
    ``~/.trinity/conversations/`` is empty after the user has
    presumably installed the extension, the host has never been
    spawned — extension not loaded in Chrome, or its ID doesn't match
    the manifest's ``allowed_origins``. Surface what to check.

    Stage 4 — last capture freshness. Same threshold as Surface 33's
    ``stale`` flag (24h): if at least one capture exists but the most
    recent is > 24h old, surface as "investigate" (could be a provider
    refactor, extension disabled, or just genuine no-use).
    """
    import sys
    import time

    # Enriched runtime PATH (venv bin / ~/.local/bin / homebrew), where the
    # capture-host console_script installs — bare shutil.which would falsely say
    # "not installed" under a GUI-launched stripped PATH (sibling of the council
    # gate fix; the host runs via the Native-Messaging manifest's absolute path).
    from .runtime_env import which_on_runtime_path

    host_path = which_on_runtime_path("trinity-local-capture-host")
    if not host_path:
        return CheckResult(
            name="browser_capture",
            ok=True,  # soft
            detail=(
                "browser capture host not installed — `trinity-local-capture-host` "
                "not on PATH. Reinstall the wheel (`pip install -e .` or "
                "`pip install -U trinity-local`) so the v1.6 console script lands. "
                "Skip if you don't use claude.ai / chatgpt.com / gemini.google.com chat UIs."
            ),
        )

    # Stage 2 — Native Messaging manifest written?
    if sys.platform == "darwin":
        manifest_path = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts" / "local.trinity.capture.json"
    elif sys.platform.startswith("linux"):
        manifest_path = Path.home() / ".config" / "google-chrome" / "NativeMessagingHosts" / "local.trinity.capture.json"
    else:
        # Windows path unverified; surface as soft skip.
        return CheckResult(
            name="browser_capture",
            ok=True,
            detail=f"v1.6 browser capture is macOS/Linux-first; platform {sys.platform!r} not yet supported.",
        )

    if not manifest_path.exists():
        return CheckResult(
            name="browser_capture",
            ok=True,
            detail=(
                "Native Messaging manifest not written — Chrome doesn't know how to "
                "spawn the capture host. Load `browser-extension/` in chrome://extensions, "
                "copy the extension ID, then `trinity-local install-extension "
                "--extension-id <ID>`. Skip if you don't use chat-UI captures."
            ),
        )

    # Stage 3 — any captures yet? Shared enumerator (capture_host.iter_capture_files)
    # applies the stream-/gemini filter ONCE so this count and the launchpad cockpit's
    # capture count can't drift (v1.7.300 — same de-dup as cortex staleness).
    from .capture_host import iter_capture_files
    capture_files = iter_capture_files()

    if not capture_files:
        # The manifest existing is NOT proof capture works. install.sh
        # pre-wires the host for registry.CANONICAL_EXTENSION_ID before the
        # user installs anything — so a green "manifest written" can sit on
        # top of a dead pipe. A SIDELOADED (Load-unpacked) build gets a
        # different per-machine id, and the host only accepts connections
        # whose origin is in allowed_origins. When the manifest still points
        # at the canonical id but nothing has been captured, name that as the
        # prime suspect rather than a generic "check the ID" nudge.
        provisional = False
        try:
            from .registry import CANONICAL_EXTENSION_ID
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest_data, dict):
                manifest_data = {}
            origins = manifest_data.get("allowed_origins") or []
            provisional = any(CANONICAL_EXTENSION_ID in str(o) for o in origins)
        except (OSError, ValueError, json.JSONDecodeError):
            provisional = False
        if provisional:
            detail = (
                "Manifest written but no captures yet — install.sh pre-wires the host "
                "for Trinity's canonical extension id before any extension is "
                "installed, so a written manifest is not proof the pipe is live. Since "
                "0.2.22 the id is KEY-PINNED (every sideload and store install shares "
                "it), so the likely causes are: the extension isn't installed/enabled "
                "yet, or you're running a pre-0.2.22 sideload whose per-machine "
                "path-derived id predates the pin — reload the unpacked extension from "
                "the current browser-extension/ build, or copy the id from "
                "chrome://extensions and run `trinity-local install-extension "
                "--extension-id <that id>`. Skip if you don't use chat-UI captures."
            )
        else:
            detail = (
                "Manifest installed but no captures yet. Check: extension loaded in "
                "chrome://extensions, extension ID in the manifest matches Chrome's "
                "assigned ID, then send a message on claude.ai, chatgpt.com, or gemini.google.com. "
                "Debug steps in `browser-extension/README.md`."
            )
        return CheckResult(
            name="browser_capture",
            ok=True,
            detail=detail,
        )

    # Stage 4 — freshness.
    try:
        latest_mtime = max(f.stat().st_mtime for f in capture_files)
    except OSError:
        latest_mtime = 0
    # Clamp to 0: a FUTURE mtime (clock skew, a restored file with a preserved
    # future timestamp) must not render "newest -179m ago." in the health detail —
    # a future capture reads as "just now" (0m), matching the launchpad's
    # `_humanize_ago` future->now convention.
    age_hours = max(0.0, time.time() - latest_mtime) / 3600 if latest_mtime else None
    if age_hours is not None and age_hours > 24:
        return CheckResult(
            name="browser_capture",
            ok=True,
            detail=(
                f"{len(capture_files)} captures total but newest is "
                f"{int(age_hours)}h old. Provider may have refactored their API, "
                "extension may be disabled, or you may genuinely not have chatted "
                "lately. chrome://extensions → service worker console for diagnosis."
            ),
        )

    age_label = f"{int(age_hours * 60)}m" if age_hours and age_hours < 1 else f"{int(age_hours or 0)}h"
    return CheckResult(
        name="browser_capture",
        ok=True,
        detail=f"{len(capture_files)} captures across providers; newest {age_label} ago.",
    )


def format_one_line(report: DoctorReport) -> str:
    """Terse one-line health verdict for the `status` command header.

    `ready_for_council` is the launch bar (≥1 provider + writable home);
    everything else is optional / informational.
    """
    total = len(report.checks)
    failed = [c for c in report.checks if not c.ok]
    # "green" must mean nothing-to-do. A soft-degraded check is ok=True but
    # carries a `fix` — and status.py prints a ⚠ nudge for every such check
    # right below this header (the #273 "don't let degradation be silent"
    # contract). Counting those as green made the header contradict the
    # warnings shown beneath it: the founder's home read "green — 20/20 checks
    # pass" with a ⚠ underneath, and a cold home read "(1 optional gap)" while
    # displaying 4. So a fully-green check is ok AND fix-free; every other
    # check (soft-degraded or failed) is a surfaced gap.
    fully_green = sum(1 for c in report.checks if c.ok and not c.fix)
    gaps = total - fully_green
    if not report.ready_for_council:
        # No usable provider OR home not writable — the user can't run
        # a council until this is fixed.
        return f"red — not ready for council ({len(failed)} checks failing); run `trinity-local status --json` for detail"
    if gaps:
        return f"yellow — ready for council, {fully_green}/{total} green ({gaps} optional gap{'s' if gaps != 1 else ''})"
    return f"green — {fully_green}/{total} checks pass"


def _check_retired_dirs_reclaimable() -> CheckResult:
    """Surface disk + FILE COUNT held by directories no live code reads.

    Two shapes of cruft accumulate from agent-driven development:

    1. **Retired-and-stopped** — Trinity stopped writing these when the
       feature retired, but pre-retirement installs keep the files:
       `cache/` (embedding cache retired 2026-05-17), `models/` (retired
       2026-05-20 — model lives in the HF cache). Real install: 786 MB +
       2.1 GB = 2.9 GB dead disk. A one-time `rm -rf` reclaims it.
    2. **Write-only dead state** — a feature whose CONSUMER retired but
       whose WRITER is still live, so it keeps GROWING: `task_sync/` (a
       `TaskSyncRecord` written on every council, but the only reader —
       the `task-sync` CLI — was retired; verified 0 readers across
       src/scripts/extension). Plus orphan dirs from retired features
       (`moves/` #184, `shortcut_setup/`). For the write-only one, `rm`
       only reclaims today's files — the dir regrows until the writer is
       retired (a code change, flagged in the detail).

    Counts FILES, not just bytes — the recurring "too many empty files
    with inconsistent data" smell is a file-count problem (task_sync was
    a 9255-file mirror of todos/). Soft check (ok=True + fix); suggests
    `rm -rf` rather than executing it so the user keeps the choice.
    """
    from .state_paths import trinity_home

    home = trinity_home()
    # (label, path, reason, live_writer) — live_writer=True means the dir
    # REGROWS after rm until the writing code path is retired.
    candidates = [
        ("cache/", home / "cache",
         "embedding cache retired 2026-05-17", False),
        ("models/", home / "models",
         "models dir retired 2026-05-20; nomic lives in ~/.cache/huggingface/", False),
        ("task_sync/", home / "task_sync",
         "write-only — its reader (the task-sync CLI) retired; 0 readers in code", True),
        ("moves/", home / "moves",
         "v2 moves substrate retired to dormant code (#184)", False),
        ("shortcut_setup/", home / "shortcut_setup",
         "macOS-Shortcut dispatch tier retired 2026-05-26", False),
    ]
    reclaimable = []
    for label, path, reason, live in candidates:
        if not path.exists():
            continue
        try:
            files = [p for p in path.rglob("*") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
        except OSError:
            continue
        if files:  # present and non-empty
            reclaimable.append((label, len(files), total, reason, live, path))

    if not reclaimable:
        return CheckResult(
            name="retired_dirs_reclaimable",
            ok=True,
            detail="no retired-feature directories holding disk",
        )

    def _fmt(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}TB"

    parts = [
        f"{label} {count} files {_fmt(size)} ({reason})"
        + (" — REGROWS until the writer is retired" if live else "")
        for label, count, size, reason, live, _ in reclaimable
    ]
    cmd_list = " ".join(f'"{path}"' for *_, path in reclaimable)
    return CheckResult(
        name="retired_dirs_reclaimable",
        ok=True,  # soft — not blocking, just informational
        detail=f"reclaimable: {'; '.join(parts)}",
        fix=f"rm -rf {cmd_list}",
    )


def run_doctor() -> DoctorReport:
    """Sequential checks — fast (<1s), no network, no chairman calls."""
    report = DoctorReport()
    report.checks.append(_check_trinity_home())
    report.checks.append(_check_config())
    report.checks.append(_check_mcp_available())
    report.checks.append(_check_skill_freshness())
    report.checks.append(_check_dispatch_ready())
    report.checks.append(_check_provider("claude", "claude"))
    report.checks.append(_check_provider("codex", "codex"))
    report.checks.append(_check_provider("antigravity", "agy"))
    report.checks.append(_check_council_breadth())
    report.checks.append(_check_embedding_backend())
    report.checks.append(_check_prompts_seeded())
    report.checks.append(_check_embedding_coverage())
    report.checks.append(_check_lens_built())
    report.checks.append(_check_core_distilled())
    report.checks.append(_check_cortex_freshness())
    report.checks.append(_check_cortex_basin_density())
    report.checks.append(_check_lens_freshness())
    report.checks.append(_check_data_degeneracy())
    report.checks.append(_check_browser_capture())
    report.checks.append(_check_vendor_published())
    report.checks.append(_check_retired_dirs_reclaimable())
    return report


def _next_step_hint(report: DoctorReport) -> str | None:
    """Return a single 'try this next' line based on what's healthy.

    Pillar 4 + #115 first-run-wow: after a green doctor run, the user
    doesn't know what to DO next. Surface the next concrete action
    given current state.

    Tiered by what's already healthy (fusion-first: the council is the free,
    zero-setup win — the lens is the opt-in add-on, never a prerequisite):
      - <2 providers green: nothing to try yet (need two for a council)
      - ≥2 providers green, no lens: run a council NOW, then offer the lens
      - ≥2 providers green AND lens built: run a council in your voice
    """
    provider_checks = [c for c in report.checks if c.name.startswith("provider:")]
    green_providers = [c for c in provider_checks if c.ok]
    if len(green_providers) < 2:
        return None

    prompts_check = next((c for c in report.checks if c.name == "prompts_seeded"), None)
    if prompts_check is None or not prompts_check.ok:
        return (
            "Try this next: run a council now — it's free and needs zero setup. "
            "From inside Claude Code / Codex CLI / Cursor, ask the agent to "
            "'run a Trinity council on …', or from the CLI: "
            "`trinity-local council-launch --task 'your hard question'`. "
            "Want the answer in your voice? Add the lens: `trinity-local lens-setup`."
        )
    return (
        "Try this next: from inside Claude Code / Codex CLI / Cursor, ask "
        "the agent to 'run a Trinity council on …' — the MCP tools surface "
        "inline and the chairman synthesizes in your voice via the lens."
    )


def format_human(report: DoctorReport) -> str:
    """Pretty-print the report for the CLI."""
    lines: list[str] = []
    for c in report.checks:
        mark = "✓" if c.ok else "✗"
        lines.append(f"  {mark}  {c.name:30s}  {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"        → fix: {c.fix}")
    lines.append("")
    if report.all_ok:
        lines.append("All checks passed. Trinity is ready.")
    elif report.ready_for_council:
        lines.append("Ready for your first council. Some optional checks failed (see above).")
    else:
        lines.append("Trinity is NOT ready. Fix the ✗ items above, then re-run `trinity-local status`.")
    hint = _next_step_hint(report)
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)
