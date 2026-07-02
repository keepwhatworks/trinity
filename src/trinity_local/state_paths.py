from __future__ import annotations

from pathlib import Path

from .config import trinity_home


# v1 schema lock. Bump only when the on-disk layout changes in a way that
# requires a migration. v2 adds new subdirs (videos/, lens/, models/) without
# renaming existing ones — those land at SCHEMA_VERSION 2. See docs/spec-v1.md
# "Folder layout" and docs/spec-v2.md "Foundations laid in v1."
SCHEMA_VERSION = "1"


def state_dir() -> Path:
    home = trinity_home()
    # Lazily anchor the schema version. Written once; future bumps go through
    # an explicit migration script that updates this file under a transaction.
    _ensure_schema_version(home)
    return home


def _ensure_schema_version(home: Path) -> None:
    schema_path = home / "SCHEMA_VERSION"
    if schema_path.exists():
        return
    try:
        home.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(SCHEMA_VERSION + "\n", encoding="utf-8")
    except OSError:
        pass


def prompt_bundles_dir() -> Path:
    path = state_dir() / "prompt_bundles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def council_outcomes_dir() -> Path:
    path = state_dir() / "council_outcomes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def council_runs_path() -> Path:
    return state_dir() / "council_runs.jsonl"


def launch_events_path() -> Path:
    return state_dir() / "launch_events.jsonl"


def review_pages_dir() -> Path:
    path = state_dir() / "review_pages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def portal_pages_dir() -> Path:
    # Directory name kept as portal_pages/ for on-disk back-compat with existing
    # ~/.trinity/ installs; Python module names and function callers have moved
    # to "launchpad_*" but the served path string lives in user filesystems and
    # is regenerable anyway, so we don't migrate it.
    path = state_dir() / "portal_pages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def council_status_dir() -> Path:
    path = portal_pages_dir() / "status"
    path.mkdir(parents=True, exist_ok=True)
    return path


def council_status_json_path(status_token: str) -> Path:
    return council_status_dir() / f"council_status_{status_token}.json"


def council_status_js_path(status_token: str) -> Path:
    return council_status_dir() / f"council_status_{status_token}.js"


def outcomes_log_path() -> Path:
    return state_dir() / "outcomes.jsonl"


def research_dir() -> Path:
    path = state_dir() / "research"
    path.mkdir(parents=True, exist_ok=True)
    return path


# shortcut_setup_dir() / shortcut_bin_dir() / cache_dir() retired
# 2026-05-17. The macOS Shortcut dispatcher (`shortcut_setup/`,
# `bin/trinity-dispatch`) and the persistent embedding cache
# (`cache/embeddings.jsonl`) were killed in the pre-launch
# simplification pass; helper functions had no remaining callers in
# src/ or tests/ after the kill. Their directories may still exist on
# older installs but Trinity no longer reads or writes them.


# `models_dir()` was retired 2026-05-20 (tick 28). It built
# `~/.trinity/models/nomic-embed-text-v1.5` and was stored on
# `MlxEmbedder.model_path` — but the attribute was never read.
# The actual model lives in HF cache (`~/.cache/huggingface/hub/`)
# via `SentenceTransformer(MODEL_ID)`. The dir-create side effect
# spawned an empty `~/.trinity/models/` directory on every embedder
# instantiation. May exist on older installs but Trinity no longer
# writes or reads it.

# `embeddings_cache_path()` was retired 2026-05-17 with the embedding
# cache kill. The persistent cache file at `~/.trinity/cache/embeddings.jsonl`
# may exist on older installs but Trinity no longer reads or writes it.


# --- Paths migrated from individual modules (Phase 0) ---


def _rename_legacy_dir(legacy: Path, new: Path) -> None:
    """Best-effort one-time rename of a legacy state dir to its new name.
    Idempotent (no-op when `new` already exists or `legacy` is absent) and NEVER
    mkdir (callers create the dir themselves), so it is safe to run eagerly at
    startup without surfacing empty ghost dirs."""
    if new.exists() or not legacy.exists():
        return
    try:
        legacy.rename(new)
    except OSError:
        # Cross-device / permission edge — leave legacy in place; the accessor
        # will create the new dir empty so forward writes still land.
        pass


def migrate_legacy_state_layout() -> None:
    """Fire the directory-rename migrations EAGERLY (tasks→todos, memory→prompts)
    so an upgrading user's task records + prompt index are visible immediately —
    not stuck in the old dirs until something happens to call
    `tasks_dir()`/`prompts_dir()`. The launchpad reads
    ``trinity_home()/"prompts"/prompt_nodes.jsonl`` DIRECTLY (anti-ghost-dir, so
    the empty new dir isn't created on every render), so without an eager
    migration an upgrade followed by `portal-html` shows "0 prompts indexed" for
    a user who actually has a corpus. Idempotent + best-effort; called from
    main's `_run_schema_migrations` on every CLI/MCP dispatch."""
    sd = state_dir()
    _rename_legacy_dir(sd / "tasks", sd / "todos")
    _rename_legacy_dir(sd / "memory", sd / "prompts")


def tasks_dir() -> Path:
    """Durable todo records — one JSON file per pending action (council
    launches, review-ready handoffs). Lives at `~/.trinity/todos/` to
    disambiguate from `task_type` (the classifier label, NOT a stored
    record). Pre-launch directory rename: if a legacy
    `~/.trinity/tasks/` exists from an earlier dev install, move it to
    `todos/` once."""
    path = state_dir() / "todos"
    _rename_legacy_dir(state_dir() / "tasks", path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_sync_dir() -> Path:
    path = state_dir() / "task_sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


# (actions_dir retired 2026-07-02 with the action store, #332 — see
# retired_names.py. The ~/.trinity/actions/ directory is no longer
# created or read; stale installs may still carry one.)


def reviews_dir() -> Path:
    path = state_dir() / "reviews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def telemetry_settings_dir() -> Path:
    path = state_dir() / "settings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def analytics_dir() -> Path:
    path = state_dir() / "analytics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def conversations_dir() -> Path:
    """Captured web-chat conversations under ``~/.trinity/conversations/``.
    The Chrome extension's Native Messaging host (``capture_host.py``)
    writes per-provider subdirs (``claude/``, ``chatgpt/``, etc.); the
    ingest path (``watch_runtime._source_root``) and the launchpad's
    capture-status card (``launchpad_data._capture_card``) read them.

    Was inlined as ``trinity_home() / "conversations"`` in 5 callers
    (capture_host.py, doctor.py, launchpad_data.py, watch_runtime.py ×2)
    before iter #117 promoted it to a helper per principle #17 (three
    inline shapes = missing helper). See ``share_dir`` for the same
    pattern at N=3.

    Unlike most state dirs, this is NOT pre-created — the capture host
    creates it on first message and absence is a meaningful signal
    (doctor surface 33 reports "no captures yet" when missing). Callers
    that need to check existence MUST ``.exists()`` themselves.
    """
    return state_dir() / "conversations"


def conversations_provider_dir(provider: str) -> Path:
    """Per-provider capture subdir, e.g. ``~/.trinity/conversations/claude/``
    or ``~/.trinity/conversations/chatgpt/``. Not pre-created — see
    ``conversations_dir`` for why."""
    return conversations_dir() / provider


def share_dir() -> Path:
    """PNG share-card outputs — `me-card`, `council-share --safe`,
    `eval-share` defaults. Was inlined as `state_dir() / "share"` in
    3 callers before tick 87 promoted it to a helper per principle
    #17 (three inline shapes = missing helper).

    NOTE: the eval suites' `evals_dir()` lives in `evals/builder.py`
    (closer to the EvalSet dataclass that consumes it); state_paths.py
    intentionally does NOT duplicate it — the canonical definition
    has multiple importers (`commands/eval.py`, tests) and adding a
    second declaration would drift per principle #20.
    """
    path = state_dir() / "share"
    path.mkdir(parents=True, exist_ok=True)
    return path


# watcher_dir() retired 2026-05-17 with the watcher subsystem kill
# (watch-once / watch-loop CLIs gone; MCP `ask` triggers ingestion
# passively). The `~/.trinity/watcher/` cursor directory may still
# exist on older installs.


def prompts_dir() -> Path:
    """Raw prompt index — the INPUT to dream. Holds PromptNode / TurnWindow
    JSONL + cursors.

    Migration: if the legacy ~/.trinity/memory/ exists and the new
    ~/.trinity/prompts/ does not, the whole directory is renamed once
    on first access. The function was previously named `prompts_dir()`;
    the brand axis is prompts (raw, yours, the INPUT) vs memories
    (plural, what dream creates, the OUTPUT). The two differed by one
    letter, which was a confusion grenade.
    """
    path = state_dir() / "prompts"
    _rename_legacy_dir(state_dir() / "memory", path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def memories_dir() -> Path:
    """The three *thinking* memories that compose your lens: lens, topics,
    vocabulary. See claude.md Glossary → "core memories".

    picks and routing are model-selection *scoreboards* — operational
    bookkeeping derived from council outcomes, not cognitive shape — and
    live under ``scoreboard_dir()``. They are excluded from distill and
    from the memory viewer's cognitive surface.

    Migration: any pre-existing files at legacy paths
    (`~/.trinity/cortex/routing_patterns.json`, `~/.trinity/me.md`,
    `~/.trinity/me/basins.json`) are moved here on first access. The
    underlying file content schemas are unchanged — only the filenames
    align with the brand axis.
    """
    path = state_dir() / "memories"
    path.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_memory_paths(path)
    return path


def generators_path() -> Path:
    """The OPTIONAL cross-domain generators tier — output of the
    ``lens-generators`` verb (the lens 'lift'). On-demand; absent until the
    verb runs, so the memory viewer shows its tab only when this exists."""
    return memories_dir() / "generators.md"


def scoreboard_dir() -> Path:
    """Operational scoreboards derived from council outcomes —
    ``picks.json`` (extracted model-selection rules) and ``routing.json``
    (per-task-type provider track record). These are computed from
    ``council_outcomes/`` and consumed by the chairman picker, ``ask``,
    and the launchpad routing card; they are NOT cognitive memories and
    are excluded from distill.

    Migration: if pre-existing files live at
    ``~/.trinity/memories/{picks,routing}.json`` (the pre-collapse home),
    they're moved here on first access. Idempotent — see
    ``_migrate_legacy_memory_paths``.
    """
    scoreboard = state_dir() / "scoreboard"
    scoreboard.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_scoreboard_paths(scoreboard)
    return scoreboard


def _migrate_legacy_memory_paths(memories: Path) -> None:
    """One-time best-effort move of legacy memory files into ~/.trinity/memories/.

    Idempotent: if the new path already exists, the legacy file is left alone
    (the legacy file is the on-disk source of truth only until the new path
    is written; after migration the new path wins).
    """
    legacy_map = [
        (state_dir() / "cortex" / "routing_patterns.json", memories / "picks.json"),
        (state_dir() / "me.md", memories / "lens.md"),
        (state_dir() / "me" / "basins.json", memories / "topics.json"),
    ]
    for legacy, new in legacy_map:
        if new.exists() or not legacy.exists():
            continue
        try:
            legacy.rename(new)
        except OSError:
            try:
                new.write_bytes(legacy.read_bytes())
            except OSError:
                pass


def _migrate_legacy_scoreboard_paths(scoreboard: Path) -> None:
    """Move picks.json + routing.json from the pre-collapse memories/ home
    into scoreboard/ on first access. Same shape as
    ``_migrate_legacy_memory_paths`` — best-effort, idempotent."""
    legacy_memories = state_dir() / "memories"
    for filename in ("picks.json", "routing.json"):
        legacy = legacy_memories / filename
        new = scoreboard / filename
        if new.exists() or not legacy.exists():
            continue
        try:
            legacy.rename(new)
        except OSError:
            try:
                new.write_bytes(legacy.read_bytes())
            except OSError:
                pass


def picks_path() -> Path:
    """`picks.json` — extracted model-selection rules per task_type.
    Operational scoreboard, not a cognitive memory. Written by
    `consolidate`; read by `ask` + the chairman picker."""
    return scoreboard_dir() / "picks.json"


def lens_path() -> Path:
    """`lens.md` — paired tensions you'd reject vs accept.
    Value memory. Written by `lens-build` (formerly `me-build`)."""
    return memories_dir() / "lens.md"


def topics_path() -> Path:
    """`topics.json` — k-means clusters of subjects you ask about.
    Semantic memory + evidence map for lens (basins_spanned per pair).
    Written by lens-build Stage 1."""
    return memories_dir() / "topics.json"


def routing_path() -> Path:
    """`routing.json` — per-category provider track record (numbers).
    Operational scoreboard, not a cognitive memory. Computed on demand
    from council_outcomes/ and frozen here for the chairman picker +
    launchpad routing card."""
    return scoreboard_dir() / "routing.json"


def vocabulary_path() -> Path:
    """`vocabulary.md` — anchors + bimodality detection on YOUR terminology.
    Language memory. Written by dream Phase 2.5."""
    return memories_dir() / "vocabulary.md"


def core_path() -> Path:
    """`core.md` — singular paragraph distillation subsuming the three
    *thinking* memories (lens + topics + vocabulary). Identity. Read FIRST
    by the chairman on every council; falls through to specific memory
    files only on demand. Written by dream Phase 5."""
    return state_dir() / "core.md"


def dispatch_outcomes_path() -> Path:
    """JSONL log of `ask` dispatch outcomes — one line per call. Tracks the
    rate-limit-saves metric named in docs/launch-package.md as the day-1
    case-study number. Each line: {ts, query_excerpt, primary, succeeded_on,
    retries, classified_kind}.
    """
    path = analytics_dir() / "dispatch_outcomes.jsonl"
    return path


def cortex_routing_patterns_path() -> Path:
    """Back-compat alias: returns the new picks.json path (now in
    ``~/.trinity/scoreboard/``). Existing callers that import this function
    keep working; new code should call ``picks_path()`` directly. Data
    lineage was ``cortex/routing_patterns.json`` → ``memories/picks.json``
    → ``scoreboard/picks.json``; both migration steps run automatically
    on first call to ``memories_dir()`` and ``scoreboard_dir()``."""
    return picks_path()


def prompt_nodes_path() -> Path:
    return prompts_dir() / "prompt_nodes.jsonl"


def turn_windows_path() -> Path:
    return prompts_dir() / "turn_windows.jsonl"


def ingest_cursors_path() -> Path:
    return prompts_dir() / "cursors.json"



def embedder_install_command() -> str:
    """The HONEST command to install the real-embedder deps for THIS install.

    `pip install 'trinity-local[mlx]'` is a 404 pre-PyPI — the package isn't
    published. The only install path today is the curl|sh script, which clones
    the source to ``~/.trinity/code`` and creates a venv at ``~/.trinity/venv``.
    So resolve the ``[mlx]`` extra from the LOCAL source: pyproject's platform
    markers install ``mlx`` only on Apple Silicon and ``sentence-transformers``/
    ``torch`` everywhere, so the same command is correct on every platform. Falls
    back to the published-extra form for a future pip/uvx install (no local
    source dir present)."""
    home = trinity_home()
    code = home / "code"
    if code.exists():
        venv_pip = home / "venv" / "bin" / "pip"
        pip = str(venv_pip) if venv_pip.exists() else "pip"
        return f"{pip} install '{code}[mlx]'"
    return "pip install 'trinity-local[mlx]'"
