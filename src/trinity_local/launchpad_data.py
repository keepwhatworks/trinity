from __future__ import annotations

import html
import json
import math
from pathlib import Path

from .adapters import check_all_adapters
from .categories import (
    DEFAULT_CATEGORY_FOR_UNKNOWN_TASK_TYPE,
    category_keys as _category_keys,
    category_labels as _category_labels,
    task_type_to_category as _task_type_to_category,
)
from .config import load_config
from .council_runtime import load_prompt_bundle
from .council_schema import normalize_provider_slug, provider_model_brand
from .council_status import load_council_status
from .dispatch_registry import make_dispatch_action
from .global_benchmarks import get_global_benchmarks, get_reference_evals_meta
from .memory.store import iter_prompt_nodes
# The macOS-Shortcut dispatch tier retired 2026-05-17 (Native Messaging
# via the Chrome extension's capture_host took over). These constants
# stay as harmless data-attr defaults the JS dispatch knows to skip
# when the URL is empty. See retired_names.py: `shortcuts_integration`.
DEFAULT_SHORTCUT_NAME = "Trinity Dispatch"
_EMPTY_SHORTCUT_URL = ""
from .state_paths import (
    council_outcomes_dir,
    council_status_dir,
    embedder_install_command,
    review_pages_dir,
    trinity_home,
)
from .telemetry import build_elo_snapshot, launchpad_telemetry_state
from .utils import finite_float_or_none, now_iso

COUNCIL_LOADING_MESSAGES = [
    # Keep "Reticulating splines..." first — it's the signature line (and a
    # frontend test pins it). The rest rotate; trio + lens-aware lines nod to
    # what's actually happening (three models, then the chairman, then your
    # taste) without breaking the dry register.
    "Reticulating splines...",
    "Generating witty dialog...",
    "Tokenizing real life...",
    "Convincing AI not to turn evil...",
    "Computing chance of success...",
    "Optimizing the optimizer...",
    "Keeping all the 1's and removing all the 0's...",
    "Pushing pixels...",
    "Three models walk into a council...",
    "Asking the other two to disagree...",
    "Letting the models argue it out...",
    "Polling the hive mind...",
    "Weighing three opinions against your taste...",
    "Checking this against how you'd answer...",
    "Summoning the chairman...",
    "Counting the votes that matter...",
]


# Centroid-similarity threshold for the picks↔topology bridge. A pick
# is considered to map onto a topology basin only when their nomic
# centroids share ≥ this cosine similarity. Empirical: 0.5 was too
# lax (oranges mapped to apples), 0.8 too strict (real matches dropped).
# Single source of truth — the JS helper in memory_viewer.py reads
# this same value via render-time injection, so the launchpad-side
# Python match and the in-viewer JS match can't drift.
BASIN_SIM_THRESHOLD = 0.65


def _esc(value: str | None) -> str:
    return html.escape(value or "")


def _strip_thread_context(text: str) -> str:
    """When the prompt was built by the launchpad's JS thread-context wrapper
    (launchpad_template.py applySuggestion), the actual user question lives
    after a "Current user message:\n" marker. The preceding block is
    prior-assistant context that humans don't want as the card title. Strip
    it for display.
    """
    marker = "Current user message:\n"
    idx = text.find(marker)
    if idx >= 0:
        return text[idx + len(marker):].strip()
    return text


def _truncate(text: str, length: int = 88) -> str:
    """Truncate at the nearest word boundary so titles don't end mid-word
    like "or p…" or "the output a…". Falls back to hard cut only if the
    text contains no spaces in the budget window (rare, single long token).
    """
    text = _strip_thread_context(text)
    if len(text) <= length:
        return text
    cut = text[:length]
    last_space = cut.rfind(" ")
    if last_space >= length // 2:
        cut = cut[:last_space]
    return cut.rstrip(" ,.;:!?-—") + "…"


def _load_recent_councils(limit: int = 10) -> list[dict[str, str | None]]:
    """Group council outcomes into threads (one card per chain_root_id).

    A "thread" here is the sequence of refine/continue/auto-chain rounds
    rooted at one initial question. The card title comes from the root
    round's prompt, the meta line shows the LATEST round's winner and
    timestamp, and the link points at `live_council.html?thread_id=<root>`
    so opening the card reveals every round on one scrollable page.
    """
    threads: dict[str, dict] = {}
    for path in council_outcomes_dir().glob("council_*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue  # corrupted-but-parseable (wrong shape) — skip, don't crash
        council_id = raw.get("council_run_id") or path.stem
        # Shape-guard the NESTED metadata too (guard_shape_not_just_parse / #304):
        # the root-isinstance check above doesn't cover a truthy-but-wrong-type
        # `metadata` (a list/str from a hand-mangled or half-migrated outcome).
        # `raw.get("metadata") or {}` keeps a truthy list, then `.get` below raises
        # AttributeError — which bubbles out of build_page_data and 500s the WHOLE
        # launchpad render (the rail builder is on the hot page-data path). Coerce
        # a non-dict metadata to {} so the council still surfaces (sans chain/round
        # detail) instead of nuking the page.
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        bundle_id = raw.get("bundle_id")
        # bundle_id is the canonical chain root identifier — it's allocated
        # at launch time and stays stable across all rounds in a chain. The
        # thread manifest, pending registration, and ?thread_id= URLs all
        # key off it. Falling back to council_id only matters for very old
        # outcomes that predate the manifest writer.
        chain_root_id = metadata.get("chain_root_id") or bundle_id or council_id
        # round_number feeds the (created_at, round_number) sort keys that pick
        # the chain root + latest round. A hand-edited council_outcomes/<id>.json
        # can carry it as a non-numeric string ("first"), a list, or null —
        # a bare `int(...)` raised ValueError that bubbled out of this reader,
        # through `_assemble_page_data`, and BLANKED THE WHOLE launchpad render
        # (portal-html exit 1, the Iter-257 corrupt-field-crash class on a
        # different file). Coerce via the shared `_safe_number` shape-guard so a
        # garbled round_number degrades to round 1 instead of nuking the page.
        round_number = int(_safe_number(metadata.get("round_number"), 1.0))
        created_at = str(raw.get("created_at") or "")
        bundle_id = raw.get("bundle_id")

        thread = threads.setdefault(
            chain_root_id,
            {
                "chain_root_id": chain_root_id,
                "segment_count": 0,
                "root_bundle_id": None,
                "root_title": None,
                "latest_winner": None,
                # Count of DISTINCT provider slugs in the LATEST round — drives
                # the solo guard in build_recent_sidebar_html. A 1-responder
                # ("solo") council has a chairman-emitted winner (the chairman
                # runs regardless of member count), but showing that winner
                # brand in the rail implies a contest that never happened — the
                # exact overclaim the share card (Iter 57) + the live page
                # (Iter 74) suppress. A council whose members are all the SAME
                # provider (claude·claude·claude) is the SAME overclaim: 3
                # responders but ONE voice, so the winner is its own runner-up.
                # Gate on DISTINCT provider count so the rail agrees with the
                # share card + review page's distinct-voice `_solo` definition
                # (Iter 111), not the raw member count.
                "latest_member_count": None,
                "latest_created_at": "",
                # (created_at, round_number) of the LATEST round seen so far —
                # the symmetric mirror of root_sort_key (which picks the MIN /
                # earliest = chain root). created_at is second-resolution
                # (now_iso() pins microsecond=0), so two rounds of a fast chain
                # can share an identical created_at. Comparing created_at ALONE
                # (the prior `>=`) let the LAST-globbed round win that tie — and
                # glob order is filesystem-nondeterministic, so the rail's
                # winner brand AND its Solo verdict (member_count) flipped run to
                # run on a same-second chain. round_number breaks the tie the
                # same way it does for the root, making the latest-round pick
                # deterministic. None until the first round is seen.
                "latest_sort_key": None,
                # task_type pulled from the latest round's routing_label.
                # Used to render cross-links from each recent card to the
                # matching picks.json / routing.json viewer entries.
                "task_type": None,
                # The root round's metadata.task_text — the canonical fallback
                # for the card title when the prompt_bundles/ store can't
                # resolve the bundle_id (imported/legacy outcomes, or a council
                # whose bundle was never written). The outcome ledger records
                # task_text on ~100% of councils, so this rescues the recent
                # sidebar from a useless "[unavailable]" placeholder.
                "root_task_text": None,
                # (created_at, round_number) of the EARLIEST round seen so far —
                # identifies the chain root for the title. See the root-selection
                # block below for why round_number alone is unreliable.
                "root_sort_key": None,
            },
        )
        thread["segment_count"] += 1
        # Keep the latest task_type — if a chain mid-rounds shifts type
        # (rare), the most recent round's label is most relevant.
        routing_label = raw.get("routing_label") or {}
        if isinstance(routing_label, dict) and routing_label.get("task_type"):
            thread["task_type"] = routing_label["task_type"]
        # (Phase 3d 2026-05-22: the per-thread "any_rated" flag was
        # stripped here when the launchpad rating UI was retired. The
        # chairman's pick — recorded in routing_label.winner above — is
        # now the supervision signal and never needs a user click.)
        # The root round carries the original prompt for the card title.
        # round_number is UNRELIABLE: real councils omit it, so
        # update_thread_manifest defaults every segment to 1 — making
        # `round_number == 1` true for ALL rounds of a chain, so the old logic
        # let the LAST-globbed round overwrite the title (non-deterministic;
        # verified — a 3-round chain's card showed a mid-round refinement, not
        # the original question). created_at is authoritative: the EARLIEST
        # round IS the chain root. round_number only breaks a created_at tie
        # (sub-second rounds) when it's actually present.
        sort_key = (created_at or "", round_number)
        if thread["root_sort_key"] is None or sort_key < thread["root_sort_key"]:
            thread["root_sort_key"] = sort_key
            thread["root_bundle_id"] = bundle_id
            thread["root_council_id"] = council_id
            thread["root_task_text"] = metadata.get("task_text")
        # Latest round drives meta line. Tie-break on round_number (max) the
        # SAME way root_sort_key tie-breaks (min): created_at is second-
        # resolution, so a same-second chain would otherwise let the last-globbed
        # (filesystem-nondeterministic) round set the winner + member_count,
        # flipping the rail's winner brand and its Solo verdict between renders.
        latest_sort_key = (created_at or "", round_number)
        if thread["latest_sort_key"] is None or latest_sort_key > thread["latest_sort_key"]:
            thread["latest_sort_key"] = latest_sort_key
            thread["latest_created_at"] = created_at
            thread["latest_winner"] = raw.get("winner_provider")
            # Count the latest round's DISTINCT provider slugs so the rail can
            # suppress the winner brand on a solo (1-responder) OR an
            # all-same-provider council (claude·claude·claude — 3 responders,
            # one voice). Shape-guard: a corrupt outcome can carry
            # member_results as a non-list (valid JSON, wrong shape), or an
            # individual member as a non-dict; coerce defensively so a bad
            # shape never inflates the distinct count into a fake contest.
            member_results = raw.get("member_results")
            if isinstance(member_results, list):
                _distinct = {
                    m.get("provider")
                    for m in member_results
                    if isinstance(m, dict) and m.get("provider")
                }
                thread["latest_member_count"] = len(_distinct)
            else:
                thread["latest_member_count"] = None

    items: list[dict[str, str | None]] = []
    for thread in threads.values():
        # Title source priority: the prompt_bundles/ store (richest), then the
        # outcome's own metadata.task_text (canonical fallback when the bundle
        # can't be resolved), then a last-resort placeholder. The middle tier
        # rescues imported/legacy councils whose bundle was never written.
        prompt: str | None = None
        if thread["root_bundle_id"]:
            try:
                bundle = load_prompt_bundle(thread["root_bundle_id"])
                prompt = bundle.task_text.strip() or None
            except Exception:
                pass
        if not prompt:
            prompt = (thread.get("root_task_text") or "").strip() or None
        if not prompt:
            prompt = "[Council prompt unavailable]"
        items.append(
            {
                "council_id": thread.get("root_council_id") or thread["chain_root_id"],
                "chain_root_id": thread["chain_root_id"],
                "bundle_id": thread["root_bundle_id"],
                "title": _truncate(prompt),
                "winner_provider": thread["latest_winner"],
                "created_at": thread["latest_created_at"],
                "segment_count": thread["segment_count"],
                # Latest-round DISTINCT-provider count — the rail uses it to
                # render an honest "Solo" marker (not a fake winner brand) on a
                # 1-responder OR all-same-provider council. See
                # build_recent_sidebar_html.
                "member_count": thread["latest_member_count"],
                "task_type": thread.get("task_type"),
                # The per-thread "rated" flag was retired Phase 3d
                # (2026-05-22) along with the launchpad rating UI;
                # chairman's winner_provider is the supervision signal.
                "review_page_path": str(
                    (review_pages_dir() / "live_council.html").resolve()
                ),
            }
        )
    # Newest-first, tie-broken on chain_root_id so the rail is a TOTAL order
    # even when two DIFFERENT chains share a second-resolution created_at
    # (now_iso() pins microsecond=0, so two councils launched in the same
    # second tie). Without the id tie-break the two rows keep the upstream
    # `threads.values()` order (glob-derived, unsorted) — so the rail order,
    # and crucially WHICH chain survives the `items[:limit]` cut, flips on
    # filesystem order. `reverse=True` over the tuple makes the id tie-break
    # descending too; the direction is immaterial, only that it's stable.
    items.sort(key=lambda item: (item.get("created_at") or "", item.get("chain_root_id") or ""), reverse=True)
    return items[:limit]


def _provider_install_help(provider: str) -> tuple[str, str]:
    # Same install strings as _TIER_INSTALL_HELP below — they're two
    # surfaces of the same fact (the canonical install command per
    # provider). Iter-#39 caught divergent strings (Antigravity here
    # had `&& agy` appended, which auto-launches the CLI after
    # install — surprising in a copy-paste install one-liner). New
    # invariant: these two functions agree byte-for-byte on the
    # install command field. The doc-consistency guard
    # test_launchpad_install_commands_match enforces it.
    if provider == "claude":
        return ("Claude Code", "npm install -g @anthropic-ai/claude-code")
    if provider == "codex":
        return ("Codex CLI", "npm install -g @openai/codex && codex --login")
    if provider == "antigravity":
        return ("Antigravity", "curl -fsSL https://antigravity.google/cli/install.sh | bash")
    if provider == "cowork":
        return ("Cowork / Claude Desktop", "Install Claude Desktop, then open Local Agent Mode once.")
    pretty = provider.replace("_", " ").title()
    return (pretty, f"Install {pretty} and rerun Trinity.")


def _provider_health_data() -> dict[str, object]:
    statuses = check_all_adapters()
    providers: list[dict[str, object]] = []
    missing_count = 0
    for status in statuses:
        if status.installed:
            continue
        label, install_command = _provider_install_help(status.provider)
        detail_parts: list[str] = []
        if status.error and not status.installed:
            detail_parts.append(status.error)
        providers.append(
            {
                "provider": status.provider,
                "label": label,
                "installed": status.installed,
                "detail": " · ".join(detail_parts),
                "installCommand": install_command,
            }
        )
        if not status.installed:
            missing_count += 1
    return {
        "providers": providers,
        "missingCount": missing_count,
        "hasMissing": missing_count > 0,
        "footerNote": "After installing, open a new terminal and run `trinity-local status`. Trinity will pick up newly installed providers automatically.",
    }


# Tier-card display order for the launchpad — visually distinct from
# config.CANONICAL_COUNCIL_PROVIDERS / registry.CANONICAL_COUNCIL_PROVIDERS
# (which use a chairman-preference order). The launchpad's order here is
# UI-led, not load-bearing for routing. Membership is the same; if either
# the launchpad order OR the canonical order changes, both call sites
# must be checked.
_TIER_PROVIDERS: tuple[str, ...] = ("claude", "codex", "antigravity")

# Provider slug → on-PATH binary name. Most providers use the same string,
# but Antigravity's slug is "antigravity" while its CLI binary is `agy`.
_TIER_PROVIDER_BINARY: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "antigravity": "agy",
}

# Per-provider install commands. Canonical-source here; the historical
# iter-#39 fix harmonized this map with setup_guidance.py + doctor.py,
# both of which have since been retired (see retired_names.py). The
# canonical bind is now: this map → tests/test_install_commands.py
# guards the install URLs match.
_TIER_INSTALL_HELP: dict[str, tuple[str, str, str]] = {
    # provider -> (display name, install command, value proposition)
    "claude": (
        "Claude Code",
        "npm install -g @anthropic-ai/claude-code",
        "Anchor voice — drives the chairman synthesis by default.",
    ),
    "codex": (
        "Codex CLI",
        "npm install -g @openai/codex && codex --login",
        "Adversarial second voice — surfaces real disagreement.",
    ),
    "antigravity": (
        "Antigravity",
        "curl -fsSL https://antigravity.google/cli/install.sh | bash",
        "Long-context third voice — completes the canonical council.",
    ),
}


def _embedder_status() -> dict[str, object]:
    """Surface the deeper-memory opt-in state on the launchpad.

    The modernbert-embed-base weights are ~600 MB. They aren't bundled
    with Trinity — first lens-build / dream / vocabulary call triggers
    a HuggingFace Hub download. The CLAUDE.md status block describes
    this; the user encounters it as a RuntimeError the first time they
    run lens-build, which is jarring.

    Better: surface the state on the launchpad ON FIRST PAINT, with a
    clear "Build deeper memory" CTA showing the exact download
    command. The card is gated on a real signal — only show when the
    user has prompts indexed (so they'd actually benefit). Cold
    install with no prompts shows nothing; user has bigger things to
    do first.

    Returns:
      modelDownloaded:    True if HF cache contains the weights
      promptsIndexed:     True if prompt_nodes.jsonl has content
      mlxAvailable:       True if sentence-transformers is importable
      downloadCommand:    shell command to fetch the model
      show:               True only when prompts are indexed AND model
                          isn't downloaded; everything else hides the
                          card (cold install → nothing to embed yet;
                          everything wired → nothing to do)
    """
    # Model weights live in HuggingFace cache, NOT in ~/.trinity/models/.
    # sentence-transformers writes to the HF cache; backend_mlx.py used
    # to expose a `model_path()` helper that pointed at ~/.trinity/models/
    # but nothing read it, and the helper was retired 2026-05-20 (tick 28).
    # We read the real cache directly here.
    # Resolve from the live MODEL_ID (modernbert-embed-base post-#244,
    # env-overridable) — a hardcoded nomic-v1.5 dir probed the wrong model
    # and showed the "Build deeper memory" card even after the real model
    # was cached.
    from .embeddings.backend_mlx import MODEL_ID, hf_cache_model_path

    model_cache_dir = hf_cache_model_path()
    model_downloaded = False
    if model_cache_dir.exists():
        # snapshots/<commit-hash>/ holds the weight files. Require the actual
        # WEIGHTS file (*.safetensors / *.bin), not "any file": an interrupted
        # `huggingface-cli download` completes the small files (config, tokenizer)
        # first and leaves the ~596MB model.safetensors absent — HF only creates
        # the snapshot symlink on completion. The old "any iterdir" check reported
        # a config-only partial download as "downloaded" → card hidden → SILENT
        # TF-IDF fallback (the partial-download sibling of #273). `.exists()`
        # follows the symlink, so a dangling symlink (incomplete blob) is rejected
        # too. Cheap — globs filenames, never loads the model.
        snapshots = model_cache_dir / "snapshots"
        if snapshots.exists():
            for snapshot in snapshots.iterdir():
                if not snapshot.is_dir():
                    continue
                weights = list(snapshot.glob("*.safetensors")) + list(snapshot.glob("*.bin"))
                if any(w.exists() for w in weights):
                    model_downloaded = True
                    break

    # Prompts indexed = user has data that would benefit from embeddings.
    # Empty install → no upsell.
    prompt_nodes_file = trinity_home() / "prompts" / "prompt_nodes.jsonl"
    prompts_indexed = (
        prompt_nodes_file.exists()
        and prompt_nodes_file.stat().st_size > 100  # 100 bytes = at least one record
    )

    # mlxAvailable tracks whether the LIBS (sentence-transformers + torch)
    # are importable. Without these, even running the download command
    # won't help — the user needs the embedder deps installed first
    # (embedder_install_command() resolves the [mlx] extra from the local
    # source; the trinity-local[mlx] PyPI form 404s pre-publish).
    try:
        from . import embeddings
        mlx_available = embeddings.is_available()
    except Exception:
        mlx_available = False

    # Two genuinely-different remediations hide behind one "show" gate:
    #   (a) model MISSING       → a real ~600 MB download is needed.
    #   (b) model PRESENT, libs broken (model_downloaded AND not mlx_available —
    #       a torch/sentence-transformers break or a venv switch, the #273
    #       trigger) → the weights are ALREADY on disk; only the Python libs
    #       need reinstalling. Telling THIS user to `huggingface-cli download`
    #       re-fetches ~600 MB they already have, and the "~600 MB download /
    #       One-time download" framing is plain wrong — the fix is just the
    #       pip install. Emit a `mode` so the card shows the honest cure for
    #       each state instead of one over-broad download pitch.
    if not model_downloaded:
        mode = "download"
        download_command = (
            f"huggingface-cli download {MODEL_ID}"
            if mlx_available
            else f"{embedder_install_command()} && huggingface-cli download {MODEL_ID}"
        )
    else:
        # model_downloaded is True and (since show is gated below) mlx is broken.
        mode = "reinstall-libs"
        download_command = embedder_install_command()

    return {
        "modelDownloaded": model_downloaded,
        "promptsIndexed": prompts_indexed,
        "mlxAvailable": mlx_available,
        "downloadCommand": download_command,
        # mode distinguishes the two remediations so the card copy can be
        # honest: "download" = the ~600 MB fetch; "reinstall-libs" = the model
        # is cached, only the libs need reinstalling (no re-download).
        "mode": mode,
        # Show the card when we have signal (prompts indexed) AND embeddings
        # aren't FULLY functional — i.e. the model is missing OR the MLX libs
        # aren't importable. The old gate (`not model_downloaded`) ignored
        # mlx_available, so a user whose model files are cached but whose
        # sentence-transformers/torch broke (or a venv switch) ran SILENT
        # TF-IDF fallback with the card hidden — "all green while embeddings
        # silently degraded" (#273). Embeddings need BOTH the weights and the
        # libs; surface the card unless both are present. Cold install (no
        # prompts) still shows nothing; fully-wired still hides.
        "show": prompts_indexed and not (model_downloaded and mlx_available),
    }


def _lens_trust() -> dict[str, object]:
    """Embedder-degraded honesty for the launchpad's embedding-derived cards.

    The SAME #35 green-while-degraded probe the memory viewer's trust banner
    uses (`lens_health._embedding_backend()`) — one source of truth so the
    launchpad's taste card, cortex/picks card, and topology basins can't paint
    a confident lens while the CLI `lens-health` verb says DEGRADED.

    On the SHA-1 TF-IDF fallback (a fresh install WITHOUT the [mlx] extras —
    NORMAL operation per CLAUDE.md), a lens still BUILDS, but its tensions are
    "caricatures of your taste, not it" (lens_health). The taste card displays
    those tensions as confident truth ("The patterns in how you think"); it
    needs the same degraded-trust banner the viewer carries. The `embedderStatus`
    download-mode card frames the missing embedder as an OPTIONAL upsell ("get
    sharper basins") and lives on /stats only — so on the simple HOME view the
    user gets ZERO honesty about the lens being a caricature. This closes that.

    Returns {embedder_degraded: bool, summary?, fix?}. Cheap (the mlx backend is
    None on the fallback path, so no real embed runs) and read-only. Never let a
    probe failure blank the launchpad — degrade to "no banner" (the cards still
    render; this only ADDS honesty, never removes content).
    """
    try:
        from . import lens_health as _lh

        backend = _lh._embedding_backend()
        if backend.status == _lh.DEGRADED:
            return {
                "embedder_degraded": True,
                "summary": backend.summary,
                "fix": backend.fix,
            }
    except Exception:
        return {"embedder_degraded": False}
    return {"embedder_degraded": False}


def _council_tier_status() -> dict[str, object]:
    """The audience-expansion tier card data.

    Pillar of the "works with 1, sells the other two" pitch: shows the
    user where they are on the 1 → 2 → 3 ladder and what the next
    free-tier add unlocks.

    Returned shape:
      tier:           1 | 2 | 3  — number of canonical providers on PATH
      installed:      [provider names that have a binary on PATH]
      missing:        [{provider, label, installCommand, value} for missing]
      headline:       short status line for the card header
      nextStep:       single next provider to pitch, None when tier == 3

    Tier card UI:
      tier 0 → "Install a Claude-compatible CLI to start" (rare; cold install)
      tier 1 → "You have <X>. Add <Y> for a 2nd voice."
      tier 2 → "You have <X> + <Y>. Add <Z> for the full council."
      tier 3 → card hidden (all installed).
    """
    from .runtime_env import which_on_runtime_path

    installed: list[str] = []
    missing: list[dict[str, str]] = []
    for provider in _TIER_PROVIDERS:
        # Most provider slugs match their on-PATH binary name 1:1, but
        # Antigravity is "antigravity" with binary `agy`. `_TIER_PROVIDER_BINARY`
        # is the canonical map; `which_on_runtime_path` is the SAME resolver the
        # council runner's dispatch gate (providers._ensure_binary) uses — the
        # enriched PATH (venv bin + ~/.local/bin + /opt/homebrew/bin +
        # /usr/local/bin), so the tier card doesn't falsely pitch "install X" to a
        # GUI-launched user whose Homebrew provider is off the bare PATH but still
        # dispatchable. Same source of truth as the runner, not a bare which.
        binary = _TIER_PROVIDER_BINARY.get(provider, provider)
        if which_on_runtime_path(binary) is not None:
            installed.append(provider)
        else:
            label, cmd, value = _TIER_INSTALL_HELP[provider]
            missing.append({
                "provider": provider,
                "label": label,
                "installCommand": cmd,
                "value": value,
            })

    tier = len(installed)
    next_step = missing[0] if missing else None

    installed_labels = [_TIER_INSTALL_HELP[p][0] for p in installed]
    if tier == 0:
        headline = "Install a council provider to get started."
    elif tier == 1:
        headline = f"You have {installed_labels[0]}. Add one more for cross-model disagreement."
    elif tier == 2:
        joined = " + ".join(installed_labels)
        headline = f"You have {joined}. One more provider completes the canonical council."
    else:
        headline = "Full canonical council — all three providers installed."

    return {
        "tier": tier,
        "installed": installed,
        "missing": missing,
        "headline": headline,
        "nextStep": next_step,
        # The card renders only when 0 < tier < 3. Tier 0 is rare
        # (truly cold install) — surfacing the message is still
        # useful but uses a different visual treatment. Tier 3
        # hides the card entirely.
        "show": 0 <= tier < 3,
    }


def _active_launchpad_operation() -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for path in council_status_dir().glob("council_status_*.json"):
        status_token = path.stem.replace("council_status_", "", 1)
        raw = load_council_status(status_token)
        if raw is None:
            continue
        if raw.get("status") != "running":
            continue
        metadata = dict(raw.get("metadata") or {})
        kind = metadata.get("kind") or "council"
        candidates.append(
            {
                "statusToken": raw.get("status_token") or status_token,
                "kind": kind,
                "status": raw.get("status") or "running",
                "label": raw.get("task_text") or ("Scan recent transcripts once" if kind == "ingest" else "Council"),
                "memberOrder": list(metadata.get("members") or list((raw.get("members") or {}).keys()) or ["claude", "antigravity", "codex"]),
                "members": dict(raw.get("members") or {}),
                "activeProvider": raw.get("active_provider"),
                "activeProviders": list(raw.get("active_providers") or []),
                "synthesis": dict(raw.get("synthesis") or {}),
                "reviewPath": raw.get("review_path") or "",
                "error": raw.get("error") or "",
                "updatedAt": raw.get("updated_at") or "",
            }
        )
    if not candidates:
        return None
    # Most-recently-updated wins, tie-broken on the statusToken so the choice
    # is a TOTAL order: two running operations sharing a second-resolution
    # updated_at would otherwise keep the unsorted glob order, so WHICH one
    # renders as "the active operation" flips on filesystem order. statusToken
    # is the unique per-operation id.
    candidates.sort(
        key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("statusToken") or "")),
        reverse=True,
    )
    return candidates[0]


# A provider needs at least this many councils before its Elo is a signal
# rather than noise. A 1-game entity's rating is still pinned at the 1500
# default (one K=24 step barely moves it), so plotting it on a "which model
# wins" chart is misleading. Live 2026-05-31: the founder's chart carried
# three local-model experiments (gemma4local / qwen27 / qwen35) at exactly
# 1495 with 1 game + 0 wins each — pure clutter next to providers with
# 50-250 games. 2 keeps cold-start healthy (a new user's providers reach 2
# games fast) while dropping single-game noise. NOTE: this does NOT address
# the deeper slug-fragmentation (chatgpt vs codex, gemini vs antigravity are
# the same models under legacy vs current slugs) — that canonicalization is a
# founder product-data decision, flagged separately.
MIN_GAMES_FOR_ELO_CHART = 2

# An Elo chart is a RELATIVE ranking — its whole meaning is "which model rates
# above which". With a single bar plotted there is nothing to rank against, so
# the chart is degenerate (not merely thin). This is normal-operation reachable:
# a 2-council user whose default played two DIFFERENT challengers clears the
# 2-game floor alone while each partner stays at 1 game, so they drop below the
# floor and the chart renders ONE lonely bar floating above the 1500 base — the
# opponents it actually beat are silently hidden, and the bar reads as a settled
# verdict. Mirror the eval per-axis-leader contest gate ("a leader needs a
# CONTEST … the data layer must not EMIT a leader at 1 contender — any other
# consumer would inherit the overclaim") and the routing cheat-sheet's ghost-
# table suppression: refuse the ranking at <2 plotted entrants and let the
# template paint an honest single-entrant explanation instead.
MIN_PROVIDERS_FOR_ELO_RANKING = 2

# Below this many games per provider the Elo chart is a thin-data signal: a
# single 2-0 coin-flip lands Claude at 1523 / GPT at 1477 — a 46-point gap that,
# rendered as absolute bars on a 1400-floored axis, paints a confident
# screenshot-worthy "Claude crushes GPT" ranking off two coin flips. The chart
# DEMOTES (not hides) under this floor: it still plots, but adds a "Thin sample"
# caveat + per-bar n=N disclosure (the cheat-sheet sibling already shows n=2).
# Floor = 10, matching the eval-leaderboard hero's HERO_LOW_CONFIDENCE_ITEMS and
# the council-value home card's 10-real-contest bar — a "public claim / the
# screenshot" surface uses the larger 10 floor, not the smaller per-axis 3.
# (Trinity council council_21a5b74fb1df3fda, winner claude, codex chairman,
# UNANIMOUS: "do not hide at 2 games; disclose game counts; thin caveat below 10;
# the 1400 axis floor exaggerates near-base differences — combine disclosure with
# axis correction; render Elo as 1500-centered deltas, not magnified absolute
# bars".)
ELO_CHART_THIN_GAMES = 10
# The Elo seed every provider starts at; bars render as signed deviation from it
# (centered axis) so a near-base coin-flip is a small bar near the midline rather
# than a tower floored at 1400. Anchoring the axis at min:1500 instead would CLIP
# below-base ratings (the council's lone disagreed_claim + major_failure_mode) —
# a delta-from-base bar shows -23 honestly where a 1500-floor would hide it.
ELO_BASE_RATING = 1500


def _elo_chart_data(snapshot: dict) -> dict:
    from .council_schema import provider_model_brand

    providers = snapshot.get("providers", {})
    plotted = [
        (name, data)
        for name, data in providers.items()
        if data.get("total_games", 0) >= MIN_GAMES_FOR_ELO_CHART
    ]
    # Label with the recognizable brand, not the raw slug: build_elo_snapshot
    # already canonicalized legacy slugs to one identity per lab (#275), so this
    # maps claude→Claude, codex→GPT, antigravity→Gemini — the marketing trio,
    # no fragmented "Chatgpt / Claude_Ai / Antigravity" bars (founder report
    # 2026-06-01). provider_model_brand also folds any residual legacy slug.
    labels = [provider_model_brand(name) for name, _ in plotted]
    elos = [int(data.get("elo", ELO_BASE_RATING)) for _, data in plotted]
    games = [int(data.get("total_games", 0)) for _, data in plotted]
    # Bars are signed deviation from the 1500 base (the chart renderer centers the
    # axis on 0): a coin-flip near base is a small bar near the midline, not a
    # tower. `elos` is kept so the tooltip can restore the real rating.
    deltas = [e - ELO_BASE_RATING for e in elos]
    min_games = min(games) if games else 0
    # Thin when the WEAKEST plotted provider is under the floor — the chart's
    # ranking is only as trustworthy as its least-played bar (weakest-link).
    thin = bool(plotted) and min_games < ELO_CHART_THIN_GAMES
    # Degenerate when fewer than 2 entrants clear the games floor: an Elo chart
    # with one bar is not a "thin ranking", it's NOT A RANKING — there is nothing
    # to rate it against. Emit the flag at the data layer (not just hidden in the
    # template) so every consumer inherits the honest gate, exactly like the eval
    # per-axis-leader contest gate. `soloLabel` names the lone plotted model so
    # the template's single-entrant message can be specific.
    degenerate = bool(plotted) and len(plotted) < MIN_PROVIDERS_FOR_ELO_RANKING
    solo_label = labels[0] if (degenerate and labels) else None
    return {
        "labels": labels,
        "elos": elos,
        "games": games,
        "base": ELO_BASE_RATING,
        "thin": thin,
        "degenerate": degenerate,
        "soloLabel": solo_label,
        "gamesFloor": MIN_GAMES_FOR_ELO_CHART,
        "minGames": min_games,
        "thinFloor": ELO_CHART_THIN_GAMES,
        "datasets": [
            {
                "label": "Rating vs 1500 base",
                "data": deltas,
                "backgroundColor": "rgba(79, 144, 149, 0.18)",
                "borderColor": "#4f9095",
                "borderWidth": 2,
                "borderRadius": 10,
            }
        ],
    }


def _settings_links() -> dict[str, dict[str, str]]:
    # Register dispatch actions so the extension's Native Messaging path
    # has a known kind; shortcutUrl is empty post-shortcut-retirement
    # (2026-05-17) so the JS dispatch skips tier-2-shortcut and goes
    # straight to the extension.
    make_dispatch_action(
        "run_command",
        args={"command": "trinity-local telemetry-enable"},
        metadata={"kind": "telemetry_enable"},
    )
    make_dispatch_action(
        "run_command",
        args={"command": "trinity-local telemetry-disable"},
        metadata={"kind": "telemetry_disable"},
    )
    make_dispatch_action(
        "run_command",
        args={"command": "trinity-local telemetry-reset-id"},
        metadata={"kind": "telemetry_reset"},
    )
    # cliCommand is the no-extension fallback: the launchpad dispatches via the
    # Chrome extension, but a user who declines the extension (e.g. for privacy)
    # must STILL be able to apply a privacy setting the modal promises ("toggle
    # it off anytime"). Without the dispatcher the JS hands them this command to
    # run instead of silently no-opping the toggle.
    # feedbackKey routes the no-extension CLI-copy confirmation to a line rendered
    # NEXT TO the control the user clicked (not a single shared line pinned at the
    # bottom near the sharing toggle, which read as confirming the WRONG action and
    # told a Reset-clicker "the toggle stays as-is" — there's no toggle in Reset).
    return {
        "enable": {"shortcutUrl": _EMPTY_SHORTCUT_URL, "extensionKind": "telemetry-enable",
                   "cliCommand": "trinity-local telemetry-enable", "feedbackKey": "settings-cli"},
        "disable": {"shortcutUrl": _EMPTY_SHORTCUT_URL, "extensionKind": "telemetry-disable",
                    "cliCommand": "trinity-local telemetry-disable", "feedbackKey": "settings-cli"},
        "reset": {"shortcutUrl": _EMPTY_SHORTCUT_URL, "extensionKind": "telemetry-reset-id",
                  "cliCommand": "trinity-local telemetry-reset-id", "feedbackKey": "settings-reset-cli"},
    }


def _load_personal_routing_table() -> dict | None:
    """Compute the personal routing table on demand from council_outcomes/.

    Returns None when no councils have been rated yet so the launchpad shows
    the empty-state CTA. The single source of truth is the council_outcomes
    directory; aggregation is cached in-process by directory mtime.

    Augments the table with a ``cold_start`` block per task_type so the
    launchpad can render "X% personalized" badges that match the chairman
    picker's actual sigmoid weighting (task #40).
    """
    from .personal_routing import compute_personal_routing_table
    from .ranker.chairman_picker import sigmoid_alpha

    try:
        table = compute_personal_routing_table()
    except Exception:
        return None
    by_task = table.get("by_task_type") or {}
    if not by_task:
        return None

    cold_start: dict[str, dict] = {}
    for task_type, providers in by_task.items():
        n_personal = 0
        for sub in providers.values():
            if isinstance(sub, dict):
                n_personal = max(n_personal, int(sub.get("n", 0) or 0))
        alpha = sigmoid_alpha(n_personal)
        cold_start[task_type] = {
            "n_personal": n_personal,
            "alpha": round(alpha, 3),
            "personalization_pct": int(round(alpha * 100)),
        }
    table = dict(table)
    # Strip the per-(task_type, provider) `wins` field from the page-data embed:
    # the launchpad client never reads it (the cheat-sheet's "picked X of Y" uses
    # the SEPARATE top-level `wins_per_task_type`; the ELO chart uses `.overall`;
    # cold_start uses `.n`). On the founder's home that's ~10KB of dead weight in
    # every launchpad.html, and it scales with task-type count. Build a FRESH
    # by_task_type so the mtime-cached table (a shared dict) isn't mutated; the
    # on-disk routing.json (memory-viewer "Raw JSON") keeps the full per-entry
    # data — only the embed slims.
    table["by_task_type"] = {
        task_type: {
            provider: {k: v for k, v in (entry or {}).items() if k != "wins"}
            for provider, entry in (providers or {}).items()
        }
        for task_type, providers in by_task.items()
    }
    table["cold_start"] = cold_start
    return table


def build_page_data(
    *,
    live_review_path: Path,
    recent_councils: list[dict[str, str | None]],
) -> dict:
    telemetry = launchpad_telemetry_state()
    elo_snapshot = build_elo_snapshot()
    chart_data = _elo_chart_data(elo_snapshot)
    settings_links = _settings_links()
    global_benchmarks = get_global_benchmarks()
    provider_health = _provider_health_data()
    council_tier = _council_tier_status()
    embedder_status = _embedder_status()
    active_operation = _active_launchpad_operation()
    personal_routing = _load_personal_routing_table()
    benchmark_providers = list(next(iter(global_benchmarks.values()))["models"].keys()) if global_benchmarks else []
    # Map provider name -> configured model id, for header annotations on the
    # ratings/benchmarks card. Reads from config.json so it tracks whatever
    # the user is actually running.
    provider_models: dict[str, str] = {}
    # Map provider name -> configured reasoning/thinking effort. Standardized
    # vocabulary: low / medium / high (each CLI accepts its own dialect on
    # top — claude also takes xhigh/max; codex also takes minimal). Agy CLI
    # has no flag; effort there is baked into the model SKU ("Gemini 3.1
    # Pro (high)" vs "(low)") and selected via agy's `/model` slash command
    # which persists to ~/.gemini/antigravity-cli/settings.json. The live
    # council card surfaces this alongside the model name so the leaderboard
    # claims are defensible: "claude scored 0.79 on Opus 4.7 with high
    # reasoning" not just "claude scored 0.79".
    provider_efforts: dict[str, str] = {}
    try:
        cfg = load_config(required=False)
        for name, provider in cfg.providers.items():
            if provider.model:
                provider_models[name] = provider.model
            if provider.effort:
                provider_efforts[name] = provider.effort
    except Exception:
        provider_models = {}
        provider_efforts = {}

    # Antigravity-specific: agy's CLI has no --model flag. Selection
    # happens via the `/model` slash command inside agy and persists to
    # ~/.gemini/antigravity-cli/settings.json. Read that file to surface
    # the real active model on the launchpad — config.json model values
    # would be ignored by agy anyway, so reading the agy-side file is
    # the only honest source. Model SKUs encode effort in parentheses
    # ("Gemini 3.1 Pro (high)"), so split that into model + effort for
    # the chip.
    # Read agy's active model via the shared single source of truth
    # (providers.read_agy_active_model_raw — the same read the eval recorder
    # uses, so the chip and the recorded target_model can't diverge). SKUs
    # encode effort in parentheses ("Gemini 3.1 Pro (high)"); split for the chip.
    from .providers import read_agy_active_model_raw

    agy_model = read_agy_active_model_raw()
    if agy_model:
        import re as _re
        m = _re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", agy_model)
        if m:
            provider_models["antigravity"] = m.group(1).strip()
            provider_efforts["antigravity"] = m.group(2).strip().lower()
        else:
            provider_models["antigravity"] = agy_model
    # Computed once so the lens-build progress card's "ready" claim stays
    # consistent with the empty-state card — both key off the SAME signal,
    # so they can never contradict ("✓ Your lens is ready" next to "no lens
    # yet, build one" on a completed-but-empty build).
    taste_lenses = _load_taste_lenses()
    return {
        "shortcutName": DEFAULT_SHORTCUT_NAME,
        "defaultGoal": "Find the strongest answer.",
        "defaultMembers": __import__("trinity_local.config", fromlist=["default_council_members"]).default_council_members(),
        "defaultPrimaryProvider": None,
        "telemetry": telemetry,
        "settingsLinks": settings_links,
        "providerHealth": provider_health,
        "councilTier": council_tier,
        "embedderStatus": embedder_status,
        "eloChart": chart_data,
        "globalBenchmarks": global_benchmarks,
        "benchmarkProviders": benchmark_providers,
        # Server-injected canonical map so the launchpad's per-category bar
        # chart aggregates ALL personal routing entries (not just the six
        # task_types an out-of-sync hardcoded JS map happened to know about).
        "taskTypeToCategory": _task_type_to_category(),
        "defaultCategoryForUnknownTaskType": DEFAULT_CATEGORY_FOR_UNKNOWN_TASK_TYPE,
        # The personal chart's X-axis uses the LMArena-aligned CATEGORY_REGISTRY
        # keys (overall / coding / hard_prompts / ...). Reference evals use a
        # different category scheme (intelligence/coding/agentic from
        # ArtificialAnalysis); aligning the two sides one day is v1.1+ work.
        "personalChartCategoryKeys": _category_keys(),
        "personalChartCategoryLabels": _category_labels(),
        "providerModels": provider_models,
        "providerEfforts": provider_efforts,
        "referenceEvalsMeta": get_reference_evals_meta(),
        # Relative URLs so the launchpad works under both file:// (double-click
        # the HTML) and http://localhost:PORT (when serving ~/.trinity via
        # `python -m http.server`). The launchpad lives at
        # /portal_pages/launchpad.html; live_council is at /review_pages/...
        "liveReviewUrl": "../review_pages/live_council.html",
        "activeOperation": active_operation,
        "statusScriptBaseUrl": "./status",
        "councilLoadingMessages": COUNCIL_LOADING_MESSAGES,
        "personalRoutingTable": personal_routing,
        "cortexRules": _load_cortex_rules(),
        "tasteLenses": taste_lenses,
        # Embedder-degraded honesty for the embedding-derived cards (taste /
        # cortex-picks / topology). One source of truth with the CLI
        # `lens-health` verb (lens_health._embedding_backend) and the memory
        # viewer's .viewer-trust-banner, so the surfaces can't disagree: when
        # the lens is a TF-IDF caricature, the taste card SAYS SO instead of
        # painting "The patterns in how you think" as settled truth.
        "lensTrust": _lens_trust(),
        # Merge corpus summary (tick #48) — small counts dict so future
        # launchpad surfaces can show "Trinity has captured N tacit-
        # record acts" without re-walking the log. Computed-view-on-
        # demand same as personal_routing_table — no separate state file.
        "mergeLog": _safe_merge_summary(),
        # Map basin_id → "top_term1 · top_term2 · top_term3" — used by
        # the launchpad chip tooltips that deep-link into topology so
        # the user sees what a basin is *about* without having to click.
        # Resolved at page-build time from topics.json; empty {} when
        # no consolidation has run.
        "topologyBasinLabels": _topology_basin_labels(),
        "coreStatus": _core_status(),
        # Aggregate "what's stale, what should I do" — only surfaces when
        # one of the five signals (core staleness / picks overrides /
        # audit disagreement / pre-thread-aware topology / picks cortex-
        # stale) fires. See `_memory_health()` docstring for the
        # canonical list.
        "memoryHealth": _memory_health(),
        # Just the count — the council list itself is server-rendered into
        # the left rail via build_recent_sidebar_html. The hero h1 no longer
        # branches on this (promise wins the H1 in idle state); kept exposed
        # in case other sections want a first-run greeting affordance.
        "recentCouncilsCount": len(recent_councils),
        # verdictStats removed from pageData 2026-05-21 with the
        # rating UX sunset (commit 8f1fd95). The compute function
        # _verdict_stats() was retired 2026-05-22 alongside its
        # sole remaining consumer doctor._check_verdict_rate.
        # Retired 2026-05-17. The macOS Shortcut dispatcher is gone;
        # _shortcut_status() now always reports applicable=False so the
        # legacy banner stays hidden. Kept on the payload for template
        # backward compat — the JS dispatch reads it and short-circuits.
        "shortcutStatus": _shortcut_status(),
        # Empirical benchmark summary — most-recent eval-run result
        # surfaced on the launchpad so the user sees their personal
        # benchmark numbers without cat'ing JSON. Empty state (CTA)
        # when no runs have completed yet.
        "evalSummary": _eval_summary(),
        # #212 cold-start aha: ONE surprising true tension about how the user
        # decides, shown as the hero cold-open the instant the lens has signal.
        # None on a cold install (hero shows the council promise instead).
        "coldOpen": _cold_open_for_launchpad(),
        # #218 — new-model celebration banner: providers whose current model
        # the user hasn't scored against their taste yet. The launchpad half
        # of the detect→notify loop (status carries the CLI half). Empty list
        # when every current model has been scored, so the banner self-hides.
        "newModels": _new_models_for_launchpad(),
        # #236 council value proof: how often the chairman picked a DIFFERENT
        # model than the user's default (the council-first painkiller, in one
        # stat) + the per-lab win split. Computed from council_outcomes/, no
        # model calls. None below the headline threshold so the card self-hides
        # on a thin ledger.
        "councilValue": _council_value_for_launchpad(),
        # #252 'Your timeline' — the user's life-chapters (datable topic surges)
        # as a chronological history. Empty list on a thin/dev-only corpus so
        # the card self-hides.
        "timeline": _timeline_for_launchpad(),
        # #242(a) live lens-build progress — drives the 'Building your lens'
        # card (stage + bar + stop/restart). None when no build is running or
        # recently finished, so the card self-hides.
        "lensBuild": _lens_build_for_launchpad(lens_populated=taste_lenses is not None),
        # rateLimitSaves removed from pageData 2026-05-21 alongside
        # the rate-action / pending-ratings mechanism retirement. The
        # launchpad never rendered a card for it (the user explicitly
        # asked "remove this" pre-launch); function was orphan.
        # v1.6 Surface 33 — browser-capture activity. Empty state has a
        # CTA (install the extension); populated state shows per-provider
        # counts + last-capture timestamp. Stale (> 24h since last
        # capture, when at least one exists) flips a warning border —
        # the same silent-breakage signal verdict_rate / handoff_ready
        # use elsewhere.
        "browserCapture": _browser_capture(),
        # Phase 4: Chrome extension dispatch ID. Populated when the user has
        # run `trinity-local install-extension --extension-id <ID>` (Phase 2).
        # Read by window.__TRINITY_DISPATCH__ to call chrome.runtime.sendMessage
        # against the right extension. None when not configured — dispatch
        # falls back to the macOS Shortcut path on Mac, or shows the install
        # banner elsewhere.
        "browserExtension": _browser_extension(),
        # Timestamp baked at render time — shown in the footer so cache
        # staleness is diagnosable at a glance. If the user sees an old
        # stamp after pip upgrade or fix-deploy, they need to hard-reload.
        "regeneratedAt": now_iso(),
    }


def _cold_open_for_launchpad() -> str | None:
    """The #212 cold-open tension for the launchpad hero. Best-effort."""
    try:
        from .cold_start import cold_open_tension
        return cold_open_tension()
    except Exception:
        return None


def _lens_build_for_launchpad(*, lens_populated: bool = False) -> dict | None:
    """#242(a) — live lens-build progress for the launchpad 'Building your lens'
    card. Returns {building, stage, label, pct, status, lensPopulated} while a
    build is running OR recently finished (so the user sees the completion /
    failure / cancel), else None so the card self-hides. Best-effort, cheap
    (one JSON read).

    ``lens_populated`` is the SAME signal the empty-state card keys off
    (``tasteLenses is not None``), threaded in so the "complete" header can be
    honest: a build that finished but produced no tensions (cold start, or the
    #295 preserved-degenerate path) must NOT claim "✓ Your lens is ready" while
    the empty-state CTA says "no lens yet, build one" — that contradiction is
    the launchpad's worst first-run moment. The template branches on this."""
    try:
        from .lens_progress import read_progress
        p = read_progress()
        if p is None or not p.status:
            return None
        # Surface while running, OR for a short window after a terminal state so
        # the user sees "done / canceled / failed" rather than the card just
        # vanishing mid-glance.
        if p.status == "running":
            show = True
        else:
            from .cold_start import _hours_since
            age_h = _hours_since(p.updated_at)
            show = age_h is not None and age_h < 0.17  # ~10 min
        if not show:
            return None
        return {
            "building": p.status == "running",
            "stage": p.stage,
            "label": p.label,
            "pct": p.pct,
            "status": p.status,
            "error": p.error,
            "lensPopulated": bool(lens_populated),
        }
    except Exception:
        return None


# Top-terms that mark a chapter as Trinity-dev / agent-ops noise rather than a
# real-life arc (#252 / #73) — these chapters are the tool building itself, not
# the user's history, so they don't belong on a "your timeline" surface.
_TIMELINE_DEV_TERMS = {"loop", "run", "commit", "mcp", "agent", "prompt", "council"}


def _timeline_for_launchpad(max_chapters: int = 6, min_prompts: int = 80) -> list[dict]:
    """#252 'Your timeline' — the user's life-chapters as datable topic surges
    (from `detect_chapters`), filtered to substantive non-dev arcs, returned
    most-substantial-first then ordered chronologically for the card.

    Read-only, best-effort ([] on any failure). Labels are the chapter's raw
    top-terms (title-cased) — auto-derived, recognizable-not-polished."""
    try:
        from .me.chapters import detect_chapters
        chapters = detect_chapters() or []
    except Exception:
        return []
    rows: list[dict] = []
    for c in chapters:
        label = getattr(c, "label", "") or ""
        terms = [t.strip().lower() for t in label.split(",") if t.strip()]
        if not terms or any(t in _TIMELINE_DEV_TERMS for t in terms):
            continue
        if getattr(c, "total_prompts", 0) < min_prompts:
            continue
        start, end = c.start_month, c.end_month
        rows.append({
            "label": ", ".join(t.strip().title() for t in label.split(",") if t.strip()),
            "range": start if start == end else f"{start} → {end}",
            "start": start,
            "prompts": int(getattr(c, "total_prompts", 0)),
        })
    # Select the top max_chapters by prompt volume, tie-broken on (start, label)
    # so the cut is a TOTAL order: two chapters with the same prompt count
    # straddling the max_chapters boundary would otherwise keep the upstream
    # `chapters` order, so WHICH chapter survives the cut flips on that order.
    # (start, label) is a stable per-chapter identity.
    rows.sort(key=lambda r: (-r["prompts"], r["start"], r["label"]))
    rows = rows[:max_chapters]
    rows.sort(key=lambda r: (r["start"], r["label"]))
    return rows


def _council_value_for_launchpad() -> dict | None:
    """The #236 council value proof for the launchpad, enriched with
    user-facing brand names. None below the headline threshold (or on any
    read failure) so the card self-hides rather than touting a thin number.
    """
    try:
        from .personal_routing import council_category_wedge, council_value_proof
        vp = council_value_proof()
        if not vp.get("ready"):
            return None
        brand = {"codex": "GPT", "claude": "Claude", "antigravity": "Gemini"}
        wins = [
            {"label": brand.get(p, p), "pct": d["pct"], "count": d["count"]}
            for p, d in vp["win_split"].items()
        ]
        # The asymmetric wedge: which lab wins which KIND of question. Only the
        # confident families (volume + margin floors) come back, so this self-
        # trims to a tight, honest "who wins what" list.
        wedge = [
            {"family": w["family"], "leader": brand.get(w["leader"], w["leader"])}
            for w in council_category_wedge()[:4]
        ]
        return {
            "councils": vp["comparable"],
            "changedPct": vp["changed_pct"],
            "wins": wins,
            "wedge": wedge,
        }
    except Exception:
        return None


def _eval_set_available() -> bool:
    """True when at least one built eval set with >=1 SCOREABLE item exists
    (`~/.trinity/evals/eval_*.json` whose `stats.items` > 0).

    The single 'can the user actually run `eval-run` yet?' check — shared by the
    eval card's cold-CTA (`_eval_summary`) and the new-models card
    (`_new_models_for_launchpad`), so they agree on the prerequisite. Read-only:
    NEVER mkdir's `evals/` (an empty ghost dir would make every cold launchpad
    falsely look eval-ready — same anti-ghost-dir reason as `_eval_summary`).

    The item-count check is the launchpad half of the green-while-degenerate guard
    (the CLI half is `handle_eval_build`): a ledger of only self_expressed acts (or
    all-degenerate model_miss acts) builds a 0-item `eval_*.json` — present on disk,
    but `eval-run` against it dispatches a hollow benchmark with no signal. Counting
    a 0-item set as 'available' would flip the eval card to its "run it" State C and
    un-suppress the new-models `eval-run` chips, both pointing at nothing. A malformed
    /unreadable set is treated as not-available (degrades safe, never crashes).
    """
    import json as _json

    from .state_paths import state_dir
    evals_dir = state_dir() / "evals"
    if not evals_dir.is_dir():
        return False
    for path in evals_dir.glob("eval_*.json"):
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and (data.get("stats") or {}).get("items", 0) > 0:
            return True
    return False


def _rejections_available() -> bool:
    """True when the preference-act ledger exists with >=1 act — the prerequisite
    `eval-build` needs.

    `build_eval_set()` reads `~/.trinity/me/preference_acts.jsonl` and raises
    FileNotFoundError without it, so on a TRULY fresh home (no `lens` run yet) the
    eval card's "build the eval set: eval-build" CTA hard-fails — the exact first-run
    dead-end the 2026-06-07 persona audit flagged. This signal lets the empty state
    lead with `lens` (which MINES the rejections eval-build consumes) instead of
    handing a brand-new user a doomed command. Read-only; never creates the ledger.
    """
    try:
        from .me.preference_acts import preference_acts_path

        p = preference_acts_path()
        if not p.exists():
            return False
        with p.open(encoding="utf-8") as f:
            return any(line.strip() for line in f)
    except Exception:
        return False


def _new_models_for_launchpad() -> list[dict]:
    """New-model events for the launchpad banner (#218). Each carries the
    slug + display name + the ready-to-run eval command. Best-effort: a
    manifest/eval read failure degrades to no banner, never a crash.

    Suppressed until an eval set exists: the card's CTA is `eval-run --target X`,
    which FAILS without a built eval set. On a cold home `detect_new_models()`
    returns every provider (all unscored), so an ungated card would hand a brand-
    new user three commands that all error. The eval-leaderboard card already
    teaches the cold path (`eval-build` + gather rejections); once a set exists,
    this card returns with working commands. (Found 2026-06-02 cold-home browser
    test — the new-models card surfaced premature `eval-run` CTAs.)
    """
    if not _eval_set_available():
        return []
    try:
        from .models import detect_new_models
        from .runtime_env import which_on_runtime_path

        # Only nudge for providers the user can ACTUALLY score. The card's CTA is
        # `eval-run --target X`, which dispatches to X's CLI — so a single-CLI user
        # (e.g. Claude Code only) must NOT be told to score Codex/Gemini they can't
        # run. Without this the nudge is undismissable: the CTA errors, the model
        # never gets scored, so `last_evaluated` stays None and detect_new_models
        # re-surfaces it forever (a stuck "green" nudge for an unactionable item).
        # Same resolver + slug→binary map as the provider-tier card / dispatch gate.
        def _dispatchable(slug: str) -> bool:
            return which_on_runtime_path(_TIER_PROVIDER_BINARY.get(slug, slug)) is not None

        # `rescore` distinguishes a brand-new provider (never benchmarked) from
        # one the user ALREADY scored on a now-superseded model. Both surface the
        # same `eval-run` chip, but the framing must differ: "A number no lab can
        # produce" reads as "you have no number yet" — false for a re-score, where
        # the user has a (now-stale) score the leaderboard still shows under the OLD
        # model name. Without this the card contradicts the leaderboard one screen
        # below ("score Claude" vs "Claude 0.79"). `ev.last_evaluated` (the recorded
        # `target_model`) is the SIGNAL that fires re-score framing — but it's NOT a
        # field the card prints: that recorded value is a raw lowercase-hyphenated
        # model API id (`claude-opus-4-6`, `gpt-5.2-codex`), and rendering it in the
        # parenthetical leaked an opaque code symbol into the celebratory copy while
        # the human display name sat one line up (UX sweep iter 199 — sibling of the
        # #184/#187 opaque-id-as-label leaks). The boolean `rescore` carries the
        # stale-benchmark intent; the model is named by `display` + the leaderboard.
        return [
            {
                "slug": ev.slug,
                "display": ev.display,
                "whatsNew": ev.whats_new,
                "command": f"trinity-local eval-run --target {ev.slug}",
                "rescore": ev.last_evaluated is not None,
            }
            for ev in detect_new_models()
            if _dispatchable(ev.slug)
        ]
    except Exception:
        return []


def _usable_score(value) -> bool:
    """True only when `value` is a real, finite eval score.

    A None (scorer suppressed it) OR a non-finite NaN/Infinity (from a
    corrupted/partial result file — json.loads accepts the bare `NaN` literal)
    is NOT a score: it must be skipped so it can neither headline the hero nor
    paint a leaderboard row. Centralises the guard the two result readers share.
    """
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _fmt_score(value, digits: int) -> str | None:
    """Format a 0..1 eval score to `digits` decimals using PYTHON rounding so the
    launchpad reads identically to the eval-share PNG and the CLI `eval-run`.

    The cross-language bug this closes: the launchpad template formatted scores
    with JS `Number.toFixed(N)` (round-HALF-UP), while every Python eval surface
    — the public share card (eval_card.py `f"{:.2f}"`) and `eval-run`/`eval-show`
    (`f"{:.3f}"`) — uses Python's `format` (round-half-to-EVEN / banker's). They
    DIVERGE on a `.5`-at-the-cut boundary: a run scoring exactly 0.625 (e.g. 5 of
    8 items) painted "0.63" on the launchpad hero but "0.62" on the founder's
    shareable benchmark card — the SAME run reading two different scores across
    surfaces. Pre-format ONCE here in Python and have the template render the
    string verbatim, so the app and the public artifact can never disagree.
    Returns None when value is None (the template keeps its own "—" fallback).
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # A non-finite score (NaN / Infinity) from a corrupted or partially-written
    # eval result reaches here as a valid float (json.loads accepts the bare
    # `NaN`/`Infinity` literal), and `f"{nan:.3f}"` formats the LITERAL string
    # "nan" — which would paint as the leaderboard/hero SCORE. Treat it as
    # "no score" (the template's "—" fallback), same as None.
    if not math.isfinite(f):
        return None
    return f"{f:.{digits}f}"


def _eval_summary() -> dict:
    """Surface the eval-run value proof on the launchpad.

    Reads ~/.trinity/evals/results/eval_*__model_*.json. The headline is the
    STRONGEST scored run (the value proof — "your best model on YOUR hardest
    questions", #303), not the most recent; the most-recent run is carried
    separately as `latest_run` so the "you just scored a new model" freshness
    signal survives. Returns:
      - {has_results: False, ...empty_state_fields}  when no runs yet
      - {has_results: True, target, model, aggregate_score, axes[],
         total_runs, items_completed, items_total, eval_id, ran_at,
         result_path, latest_run}  when at least one run completed

    Empty state still carries the CTA fields (eval_set_available)
    so the template can render the "you built an eval set, run it
    against gemini" call to action without losing data.

    Per "Analytics never crash": any failure returns the safe
    empty-state shape rather than raising — the launchpad must not
    fall over because an eval result file is malformed.
    """
    from .state_paths import state_dir
    empty = {
        "has_results": False,
        "target": None,
        "target_display": None,
        "model": None,
        "aggregate_score": None,
        "axes": [],
        "total_runs": 0,
        "items_completed": 0,
        "items_total": 0,
        "eval_id": None,
        "ran_at": None,
        "result_path": None,
        # Whether the user has built an eval set — drives whether the
        # empty state CTA points at `eval-build` or `eval-run`.
        "eval_set_available": False,
        # Whether the preference-act ledger has rejections yet — drives whether
        # the empty state leads with `lens` (mine them) or `eval-build` (consume
        # them). False here means eval-build would FileNotFoundError, so the card
        # must NOT lead with it. Set unconditionally below so every return carries it.
        "rejections_available": False,
    }
    empty["rejections_available"] = _rejections_available()
    # NOTE: hardcoded path on purpose — `evals.builder.evals_dir()` mkdir-
    # creates the directory as a side effect, which would surface an empty
    # `~/.trinity/evals/` on every launchpad render for users who haven't
    # run eval-build yet. Same anti-ghost-dir reason as tick 28's
    # models_dir() sunset. Read-only check: just stat for existence.
    evals_dir = state_dir() / "evals"
    if not evals_dir.is_dir():
        return empty
    eval_set_available = _eval_set_available()  # shared with the new-models card
    results_dir = evals_dir / "results"
    if not results_dir.is_dir():
        empty["eval_set_available"] = eval_set_available
        return empty
    # Sort newest-first, but tie-break on the stem so the order is a TOTAL
    # order even when two result files share an st_mtime (a same-second
    # eval-build or a copy). Without the stem key, `sorted(..., reverse=True)`
    # preserves the (unsorted) glob order on an mtime tie — so which run counts
    # as a provider's "latest" (line below), and therefore the leaderboard
    # WINNER + the hero headline score, flipped purely on filesystem glob order
    # (proven: two same-mtime codex runs at 0.40 vs 0.82 + an antigravity run at
    # 0.50 headlined "Gemini 0.500" one glob, "GPT 0.820" the reverse). `-mtime`
    # gives newest-first; `stem` ascending breaks the tie deterministically.
    candidates = sorted(
        results_dir.glob("eval_*__model_*.json"),
        key=lambda p: (-p.stat().st_mtime, p.stem),
    )
    if not candidates:
        empty["eval_set_available"] = eval_set_available
        return empty
    # Headline = the leaderboard WINNER — the value proof ("your best model on
    # YOUR hardest questions"), NOT merely the most recent. #303: a weak latest
    # run (e.g. a new model you just scored at 0.50) must not headline over the
    # 0.82 winner already on disk and bury the proof a journalist screenshots.
    # The freshness ("you just scored X") survives in `latest_run` below + the
    # dedicated newModels card — never in the hero number.
    #
    # The winner is the highest scorer among each provider's MOST-RECENT scored
    # run — the SAME per-provider-latest dedup the `comparison` leaderboard does
    # below — so the headline is exactly `comparison[0]`. A naive global max
    # across ALL historical runs would cherry-pick a stale 1.0 from a tiny old
    # eval set and contradict the leaderboard rendered right under it. A
    # degenerate result (aggregate_score null — an aborted/quota'd run) is
    # skipped so it can't mask real scored runs.
    scored: list[tuple] = []
    per_provider_latest: list[tuple] = []
    seen_targets: set[str] = set()
    for cand in candidates:
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except (OSError, json.JSONDecodeError):
            continue
        if not _usable_score(data.get("aggregate_score")):
            continue  # None OR non-finite NaN/Inf (corrupt file) — not a score
        scored.append((cand, data))
        # Most-recent scored run per provider (candidates is mtime-desc). Mirror
        # the leaderboard's canonicalize-then-dedup so headline == comparison[0].
        tgt = data.get("target_provider")
        tgt = (normalize_provider_slug(tgt) or tgt) if tgt else None
        if tgt and tgt not in seen_targets:
            seen_targets.add(tgt)
            per_provider_latest.append((cand, data))
    latest_scored: tuple | None = scored[0] if scored else None
    if per_provider_latest:
        # Headline = highest aggregate among per-provider-latest runs (the
        # leaderboard winner). `max` returns the FIRST maximal element and the
        # list is mtime-desc, so a tie resolves to the most-recent winner.
        latest, payload = max(per_provider_latest, key=lambda cd: cd[1].get("aggregate_score") or -1.0)
    elif scored:
        latest, payload = scored[0]
    else:
        # No scored run yet — fall back to the most-recent (score-less) file
        # so the card can still report "ran, no score" over the cold CTA.
        latest = candidates[0]
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            empty["eval_set_available"] = eval_set_available
            return empty
    if not isinstance(payload, dict):
        empty["eval_set_available"] = eval_set_available
        return empty
    # Fold a web-era capture slug (gemini / chatgpt / claude_ai) on the result to
    # the dispatchable CLI slug (antigravity / codex / claude). An eval result
    # stored under a web-era target_provider otherwise (a) headlines the
    # single-card `eval-show --target <slug>` command with a slug that won't
    # dispatch, and (b) splits the cross-provider leaderboard below into TWO rows
    # for the same provider, both branding to the same name (e.g. two "Gemini"
    # rows). Canonicalize at this boundary — symmetric to the council outcome /
    # MCP-resource canonicalization (mcp_server_staleness).
    if payload.get("target_provider"):
        payload["target_provider"] = (
            normalize_provider_slug(payload["target_provider"]) or payload["target_provider"]
        )
    # Build the per-axis array for the template, sorted desc by mean.
    # Shape-guard (guard_shape_not_just_parse / #304): a valid-JSON-but-wrong-
    # type `by_rejection_type` (a LIST where the per-axis map {axis: {mean_score,
    # count}} is expected — a schema-drift / hand-edited / half-migrated eval
    # result) is truthy, so the bare `or {}` lets a list through and `.items()`
    # below raised `AttributeError: 'list' object has no attribute 'items'` —
    # bubbling out of `_eval_summary` → `build_page_data` and BLANKING THE WHOLE
    # launchpad render (the same single-corrupt-eval-file blast radius as the
    # bare-NaN headline crash). Coerce any non-dict to {} at the read boundary so
    # one malformed file degrades to "no per-axis breakdown", not a 500.
    by_type_raw = payload.get("by_rejection_type")
    by_type = by_type_raw if isinstance(by_type_raw, dict) else {}
    # Shape-guard EACH per-axis numeric (#304): a hand-edited / half-migrated eval
    # result can carry an axis `count` as a non-numeric string ("abc") or a NaN/Inf,
    # and `mean_score`/`min_score`/`max_score` likewise. A raw `count` flowed into
    # `int(a.get("count"))` below (`int("abc")` / `int(NaN)` → ValueError) and a raw
    # string `mean` became the sort key (`sorted(..., key=a["mean"])` →
    # "'<' not supported between 'str' and 'float'") — either bubbled out of
    # `_eval_summary` → `build_page_data` and BLANKED THE WHOLE launchpad, the same
    # single-corrupt-eval-file blast radius as the bare-NaN headline / wrong-type
    # by_rejection_type fixes. `_safe_number` coerces each to a finite number at the
    # read boundary so a poisoned axis degrades to 0/0.0 and the page still mounts.
    axes = sorted(
        [
            {
                "name": axis,
                "count": int(_safe_number(stats.get("count"), 0.0)),
                "mean": _safe_number(stats.get("mean_score"), 0.0),
                # Pre-formatted (Python 2dp) so the per-axis bar reads the same as
                # the share card / CLI — never JS-rounded. See _fmt_score.
                "mean_str": _fmt_score(stats.get("mean_score", 0.0), 2),
                "min": _safe_number(stats.get("min_score"), 0.0),
                "max": _safe_number(stats.get("max_score"), 0.0),
            }
            for axis, stats in by_type.items()
            if isinstance(stats, dict)
        ],
        key=lambda a: a["mean"],
        reverse=True,
    )
    # Hero low-confidence gate (green-gate #35: a headline value-claim must
    # self-demote when the data doesn't support the claim it makes). The hero
    # number — "Gemini 0.83 · the one signal no vendor can copy" — is the
    # screenshot a journalist takes, so it must carry a thin-sample caveat at
    # low n the SAME way the mixed-eval-set caveat already rides it. Two
    # INDEPENDENT failure modes (Trinity-council-decided 2026-06-17,
    # council_4416b36956074e21, winner claude, unanimous):
    #   1. low sample size — items_completed < HERO_LOW_CONFIDENCE_ITEMS. The
    #      floor is 10 (matching the value-proof home headline's "real claim"
    #      bar, NOT the smaller per-axis MIN_AXIS_LEADER_N=3), because the hero is
    #      a PUBLIC claim, not a per-axis widget — the screenshot-honesty asymmetry
    #      beats internal-threshold consistency.
    #   2. single-axis dominance — one rejection axis is > HERO_MAX_AXIS_SHARE
    #      (0.60, mirroring composition_floor.MAX_AXIS_SHARE) of the scored items,
    #      so "scored on YOUR kind of question" is really "scored on REFRAME only".
    # The score STILL renders below either floor; it's just visibly demoted.
    HERO_LOW_CONFIDENCE_ITEMS = 10
    HERO_MAX_AXIS_SHARE = 0.60
    # Shape-guard items_completed (#304): a corrupt/hand-edited eval result can
    # carry it as a non-numeric string or NaN — a bare `int("abc")` / `int(NaN)`
    # ValueError out of _eval_summary BLANKED THE WHOLE launchpad. _safe_number
    # coerces to a finite number so the hero-confidence gate degrades to 0.
    hero_items_completed = int(_safe_number(payload.get("items_completed"), 0.0))
    hero_low_confidence = (
        payload.get("aggregate_score") is not None
        and 0 < hero_items_completed < HERO_LOW_CONFIDENCE_ITEMS
    )
    # Single-axis dominance: share of the largest axis over the items that
    # actually carry an axis label. Use the by_rejection_type counts (the same
    # source the per-axis bars render from) rather than items_completed, so a
    # run where some items lack an axis doesn't dilute the share.
    _axis_counts = [int(a.get("count") or 0) for a in axes]
    _axis_total = sum(_axis_counts)
    hero_dominant_axis = None
    hero_dominant_axis_share = 0.0
    if payload.get("aggregate_score") is not None and len(axes) >= 2 and _axis_total > 0:
        _top = max(axes, key=lambda a: int(a.get("count") or 0))
        _share = int(_top.get("count") or 0) / _axis_total
        if _share > HERO_MAX_AXIS_SHARE:
            hero_dominant_axis = _top.get("name")
            hero_dominant_axis_share = round(_share, 4)
    # Multi-target comparison view. When Trinity has results for ≥2
    # providers, the leaderboard shows the cross-provider wedge ("Trinity
    # scores models against YOUR rejections") — not just the most recent run.
    # NOTE (corrected 2026-06-02 after a real-browser check): the eval card
    # was DEMOTED into a collapsed <details class="demoted-card-wrapper"> in the
    # 2026-05-21 value-first redesign (lead with the council painkiller), so this
    # leaderboard is a retention/expand-on-click surface, NOT a hero a journalist
    # sees on a default screenshot. The collapsed <summary> teases only the
    # headline (most-recent) run's score; the side-by-side wedge lands only after
    # the user expands. Don't re-justify rendering choices here with "the
    # screenshot must show the wedge" — it doesn't, by design.
    #
    # For each unique target_provider, take the MOST RECENT result
    # (mtime descending). Sort the row order by aggregate score
    # descending so the strongest model is first — that's the natural
    # marketing voice ("here's the leaderboard on YOUR corpus").
    by_target: dict[str, dict] = {}
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Shape-guard (guard_shape_not_just_parse / #304): a valid-JSON-but-wrong-
        # type result file (a list/str/int/null root from a truncated or
        # hand-mangled eval_*__model_*.json) parses fine but `.get` below would
        # raise AttributeError, bubbling out of build_page_data and 500-ing the
        # WHOLE launchpad render. The headline loop above already isinstance-guards
        # its read (line ~1231); this sibling leaderboard loop must too — skip the
        # malformed file, keep the valid results. Mirrors the same skip the first
        # loop does, closing the v1.7.202 reader class on this second reader.
        if not isinstance(data, dict):
            continue
        # Canonicalize the web-era capture slug to the dispatch slug BEFORE the
        # per-target dedup, or gemini (web-era) and antigravity stay separate keys
        # → two indistinguishable "Gemini" rows on the leaderboard.
        target = data.get("target_provider")
        if target:
            target = normalize_provider_slug(target) or target
            data["target_provider"] = target
        if not target or target in by_target:
            continue  # keep the most recent (we walked mtime-desc)
        if not _usable_score(data.get("aggregate_score")):
            continue  # None OR non-finite NaN/Inf (corrupt file) — not a real result
        # Judge provider is stored per-item (each item names its
        # judge). Take the first item's judge as the run's judge —
        # the harness uses one judge per run, so any item works.
        # The stored value is the raw DISPATCH slug (claude / codex /
        # antigravity); brand it for the row the same way `target_display`
        # brands `target`, or the leaderboard renders "judge: codex" /
        # "judge: antigravity" while the row name two columns left already
        # says "GPT" / "Gemini" — the same provider named two ways on one
        # public surface (#275 raw-slug-vs-brand). `judge` stays the slug
        # for any consumer that needs the dispatchable name.
        items = data.get("items") or []
        judge = None
        for item in items:
            if isinstance(item, dict) and item.get("judge_provider"):
                judge = item["judge_provider"]
                break
        judge_display = provider_model_brand(judge) if judge else None
        # Per-axis means for the by-axis matrix view + per-axis leader
        # computation below. Keep nested so consumers paying only for
        # the aggregate-leaderboard view skip the parse.
        # Pairs (mean, count) so the leader-suppression rule can check
        # sample size — claims like "codex wins COMPRESSION 0.77" based
        # on n=2 are noise, not signal.
        per_axis = {}
        per_axis_n = {}
        # Same wrong-type shape-guard as the headline path above: a LIST-shaped
        # `by_rejection_type` (valid JSON, wrong type) would make the bare
        # `or {}` pass a list to `.items()` → AttributeError out of this
        # leaderboard loop → 500 the launchpad. Coerce non-dict to {}.
        _bt_raw = data.get("by_rejection_type")
        for axis_name, stats in (_bt_raw if isinstance(_bt_raw, dict) else {}).items():
            if isinstance(stats, dict) and "mean_score" in stats:
                # Shape-guard the per-axis numerics (#304): the SIBLING reader of
                # the headline axes-loop above — a corrupt `mean_score`/`count` in
                # ONE provider's eval result blanked the WHOLE launchpad here too
                # (`float("abc")` / `int(NaN)` → ValueError out of the comparison
                # loop → build_page_data). Coerce via the shared `_safe_number` so a
                # poisoned axis degrades to 0.0/0 and the leaderboard still renders.
                per_axis[axis_name] = _safe_number(stats.get("mean_score"), 0.0)
                per_axis_n[axis_name] = int(_safe_number(stats.get("count"), 0.0))
        by_target[target] = {
            "target": target,
            "target_display": provider_model_brand(target),
            "model": data.get("target_model"),
            "aggregate_score": data.get("aggregate_score"),
            # Pre-formatted (Python 3dp — the leaderboard precision the CLI
            # eval-show --compare + the eval-share matrix PNG both use). Render
            # this string verbatim so a row can't JS-round to a different last
            # digit than the public card. See _fmt_score.
            "aggregate_score_str": _fmt_score(data.get("aggregate_score"), 3),
            # Coerce the item counts to finite ints (#304): a corrupt result can
            # carry items_completed/total/failed as a non-numeric string or NaN. A
            # NaN passed through here lands in page_data and breaks the strict-JSON
            # build contract (and the client's JSON.parse); the downstream
            # int(items_failed) exclusion gate would ValueError. _safe_number keeps
            # the page NaN-free at the data layer.
            "items_completed": int(_safe_number(data.get("items_completed"), 0.0)),
            # items_total/items_failed feed the exclusion disclosure: the
            # aggregate is a mean over COMPLETED items only (timeouts/dispatch
            # failures are dropped, not scored 0), and providers can fail
            # different items — so the leaderboard must say so, matching the
            # CLI `eval-show --compare` and the single-provider detail view.
            "items_total": int(_safe_number(data.get("items_total"), 0.0)),
            "items_failed": int(_safe_number(data.get("items_failed"), 0.0)),
            "judge": judge,
            # Branded judge label (Claude / GPT / Gemini) for the row's
            # "judge: …" column — the slug `judge` would leak codex/antigravity.
            "judge_display": judge_display,
            # Self-judge transparency (2026-06-09 scorer.self_judge): True when
            # this run's judge slug == its target slug (the model graded its own
            # provider family). The CLI `eval-run` already discloses this
            # ("self-judge — same family as target, measured non-self-preferential"),
            # but the leaderboard hid it AND its footer asserted the OPPOSITE
            # ("Judges are rotated · a model never grades itself") — a public-surface
            # contradiction on a journalist-screenshottable card whenever a self-
            # judged row (e.g. `eval-run --target claude --judge claude`) was the
            # winner ("1. Claude · judge: Claude"). Surface the row flag here so
            # the template can mark the row AND drop the absolute "never grades
            # itself" claim when any displayed row is self-judged. Measured
            # NON-self-preferential, so this is neutral transparency, not a penalty.
            "self_judge": bool(data.get("self_judge")),
            "ran_at": data.get("completed_at") or data.get("started_at"),
            "by_axis": per_axis,
            "by_axis_n": per_axis_n,
            # eval_id surfaces mixed-set drift: when the comparison list
            # contains rows from different eval sets the aggregate
            # scores aren't directly comparable. Template uses the
            # mixed_eval_sets flag below to warn the user.
            "eval_id": data.get("eval_id"),
        }
    comparison = sorted(
        by_target.values(),
        key=lambda r: r.get("aggregate_score") or -1.0,
        reverse=True,
    )
    # Mixed-eval-set drift: each provider's most-recent run may target
    # a different eval set (e.g. user rebuilt then re-scored only 2 of
    # 3 providers). Surface a warning when distinct eval_ids appear in
    # the comparison list — scores from different sets aren't
    # directly comparable. CLI mirror: `eval-show --compare` emits the
    # same warning; this brings it to the launchpad.
    distinct_eval_ids = {
        r["eval_id"] for r in comparison if r.get("eval_id")
    }
    mixed_eval_sets = len(distinct_eval_ids) > 1
    # Per-axis leader: for each axis seen across any provider, who
    # scored highest? Surfaces the wedge claim ("X is best for this
    # kind of question") on the launchpad without requiring the user
    # to leave for `trinity-local eval-show --compare --by-axis`.
    #
    # SUPPRESSED when mixed_eval_sets is True: comparing per-axis
    # scores across different eval sets is exactly the operation the
    # mixed-set warning says is invalid. Rendering "claude leads
    # COMPRESSION (0.12)" next to "codex leads COMPRESSION (0.77)"
    # when those came from DIFFERENT 5-item-vs-45-item sets is a
    # misleading head-to-head claim. The banner already surfaces the
    # remedy; better to hide the chips than make a false comparison.
    per_axis_leader: list[dict] = []
    # Minimum samples per provider before declaring a leader on an axis.
    # n=2 is the live trigger — COMPRESSION on the user's eval set had 2
    # items per provider, but mean differences of 0.7 between providers
    # at n=2 are noise, not signal. In practice users should be at n=10+
    # before a per-axis claim is publishable. ONE source of truth shared
    # with the CLI eval-show/eval-share leader lines and the eval-share PNG
    # matrix de-emphasis — import it so this gate can't drift from theirs
    # (evals.composition_floor.MIN_AXIS_LEADER_N).
    from .evals.composition_floor import (
        MIN_AXIS_LEADER_CONTENDERS,
        MIN_AXIS_LEADER_N as MIN_AXIS_SAMPLES,
        TIE_DP_AXIS,
        distinct_target_count,
        scores_tied,
    )
    # A per-axis "leader" needs a CONTEST: with one provider scored the lone row
    # "leads" every axis only because nobody else ran (the council-card solo-
    # overclaim shape #35). The launchpad TEMPLATE happens to nest these chips
    # inside the comparison>=2 block, but the data layer must not EMIT a leader at
    # 1 contender — any other consumer of per_axis_leader (the eval-share JSON, a
    # future side-panel card) would inherit the overclaim. Mirror the PNG matrix
    # card's `_distinct_target_count(rows) <= 1` gate at the source.
    enough_contenders = distinct_target_count(comparison) >= MIN_AXIS_LEADER_CONTENDERS
    if not mixed_eval_sets and enough_contenders:
        axes_seen: set[str] = set()
        for row in comparison:
            axes_seen.update((row.get("by_axis") or {}).keys())
        for axis in sorted(axes_seen):
            scored = [
                (r["target"], r["by_axis"][axis], (r.get("by_axis_n") or {}).get(axis, 0))
                for r in comparison
                if axis in (r.get("by_axis") or {})
            ]
            if not scored:
                continue
            # Sample-size guard: if ANY contender on this axis is below
            # the floor, suppress the claim — leader-by-noise is worse
            # than no leader.
            if any(n < MIN_AXIS_SAMPLES for _, _, n in scored):
                continue
            # TIE DEMOTION (#35 green-while-degenerate): when the top-two axis
            # scorers round equal at the 2dp the wedge chip shows ("REFRAME:
            # Claude 0.75" while GPT is ALSO 0.75), the chip names a leader of a
            # TIED axis. The slug tie-break below would pick a deterministic but
            # ARBITRARY name — a false "X is best at Y" wedge claim on a public
            # surface. Suppress the chip for that axis; the per-provider scores
            # still render in the leaderboard rows. Same gate as the eval-share
            # PNG matrix + CLI per-axis leader paths (one source of truth in
            # composition_floor) — mirrors the routing cheat-sheet "tied" shape.
            _top2 = sorted((s for _, s, _ in scored), reverse=True)[:2]
            if len(_top2) >= 2 and scores_tied(_top2[0], _top2[1], dp=TIE_DP_AXIS):
                continue
            # Highest axis score wins, tie-broken on the provider slug so the
            # named leader is deterministic: two providers tied on an axis score
            # would otherwise resolve to whichever came first in `comparison`
            # order (itself tie-prone on an aggregate-score tie) — so the wedge
            # chip's "X is best for Y" flipped on scan order. `min` over
            # (-score, slug) = max score, lexically-smallest slug. Same tie-break
            # canon as the chairman pick + routing chip (b40807ec).
            leader_target, leader_score, _ = min(scored, key=lambda kv: (-kv[1], kv[0]))
            per_axis_leader.append({
                "axis": axis,
                "target": leader_target,
                "target_display": provider_model_brand(leader_target),
                "score": leader_score,
                # Pre-formatted (Python 2dp) so the wedge chip matches the share
                # card's per-axis leader chip exactly. See _fmt_score.
                "score_str": _fmt_score(leader_score, 2),
            })

    # #303 — surface the most-recent scored run separately so the "you just
    # scored a new model" freshness signal survives the headline-the-winner
    # flip. None when the latest IS the winner (no redundant second line).
    latest_run = None
    if latest_scored is not None and latest_scored[0] != latest:
        lr_data = latest_scored[1]
        lr_target = lr_data.get("target_provider")
        if lr_target:
            lr_target = normalize_provider_slug(lr_target) or lr_target
        latest_run = {
            "target_display": provider_model_brand(lr_target) if lr_target else None,
            "aggregate_score": lr_data.get("aggregate_score"),
            # Pre-formatted (Python 2dp) so the "most recent run scored X" line
            # matches the hero + share card. See _fmt_score.
            "aggregate_score_str": _fmt_score(lr_data.get("aggregate_score"), 2),
            "ran_at": lr_data.get("completed_at") or lr_data.get("started_at"),
            "model": lr_data.get("target_model"),
        }

    return {
        "has_results": True,
        "target": payload.get("target_provider"),
        # Model-brand display label (Gemini / GPT / Claude) for headlines.
        # `target` stays the raw slug because the template also splices it
        # into a copy-pasteable `eval-show --target <slug>` command — the
        # command needs the slug, the headline needs the brand. Without this
        # the bar rendered "antigravity · scored 0.50" (raw slug leak).
        "target_display": provider_model_brand(payload.get("target_provider")),
        "model": payload.get("target_model"),
        "aggregate_score": payload.get("aggregate_score"),
        # Pre-formatted (Python 2dp — the "tweet-shape" the eval-share PNG uses).
        # The hero headline renders THIS string verbatim instead of JS-rounding
        # the raw float, so the app number == the public share-card number even on
        # a .5-at-2dp boundary (0.625 → "0.62" on BOTH, never "0.63"). _fmt_score.
        "aggregate_score_str": _fmt_score(payload.get("aggregate_score"), 2),
        "axes": axes,
        "total_runs": len(candidates),
        # Finite-coerce the hero item counts (#304) so a corrupt result can't leak
        # a NaN/non-numeric into page_data (breaking the strict-JSON build).
        "items_completed": int(_safe_number(payload.get("items_completed"), 0.0)),
        "items_total": int(_safe_number(payload.get("items_total"), 0.0)),
        "eval_id": payload.get("eval_id"),
        "ran_at": payload.get("completed_at") or payload.get("started_at"),
        "result_path": str(latest.relative_to(state_dir())),
        "eval_set_available": eval_set_available,
        # Multi-target comparison: list of {target, model,
        # aggregate_score, items_completed, judge, ran_at, by_axis},
        # sorted by aggregate desc. Always at least 1 entry (the latest
        # run). Template uses this when len(comparison) >= 2 to render
        # a leaderboard view alongside the per-axis bars.
        "comparison": comparison,
        # Per-axis leader chips. List of {axis, target, score} sorted
        # by axis name. Template renders chips above the leaderboard
        # when len >= 1, surfacing the wedge claim ("X is best at
        # COMPRESSION") without requiring the user to leave for
        # `trinity-local eval-show --compare --by-axis`.
        "per_axis_leader": per_axis_leader,
        # True when the comparison list contains rows from ≥2 distinct
        # eval sets. Template surfaces a warning banner so a user
        # rebuilding-without-rescoring-all-providers sees the drift.
        "mixed_eval_sets": mixed_eval_sets,
        # True when ANY displayed leaderboard row was self-judged (judge slug ==
        # target slug). The footer's absolute "a model never grades itself" claim
        # is FALSE on those rows, so the template must drop it and disclose the
        # self-judge relationship instead (matching the CLI's own disclosure).
        # Without this the public card claimed the opposite of what its own
        # "judge: Claude" row two columns left already showed.
        "any_self_judge": any(r.get("self_judge") for r in comparison),
        # Hero low-confidence demotion (council_4416b36956074e21). Two
        # INDEPENDENT failure modes the template caveats on the headline:
        #   - hero_low_confidence: the headline run scored < 10 items (a smoke
        #     run); the "0.83 · the one signal no vendor can copy" claim is too
        #     thin to publish. Number still renders, visibly demoted.
        #   - hero_dominant_axis / _share: one rejection axis is >60% of the
        #     scored items, so "scored on YOUR kind of question" is really a
        #     single-axis (e.g. REFRAME-only) score. None when balanced.
        "hero_low_confidence": hero_low_confidence,
        "hero_items_completed": hero_items_completed,
        "hero_min_items": HERO_LOW_CONFIDENCE_ITEMS,
        "hero_dominant_axis": hero_dominant_axis,
        "hero_dominant_axis_share": hero_dominant_axis_share,
        # #303 — the most-recent scored run, surfaced as a secondary "you just
        # scored X" line when it ISN'T the headline winner. None when the latest
        # run is itself the winner (the headline already shows it).
        "latest_run": latest_run,
        # Per-provider items dropped from the aggregate (timeouts / dispatch
        # failures — excluded, not scored 0). The aggregate is a mean over
        # COMPLETED items only, so a provider that timed out on its hardest
        # items isn't penalized, and providers can fail different items. The
        # template surfaces this so the leaderboard matches the honesty of the
        # CLI (`eval-show --compare`) and the single-provider detail view —
        # the ranking surface shouldn't read as more apples-to-apples than the
        # data is. Empty list ⇒ every provider scored its whole set.
        # Shape-guard items_failed (#304): a corrupt eval result's items_failed
        # (non-numeric string / NaN) flowed into a bare `int(...)` here — ValueError
        # out of _eval_summary blanked the whole launchpad. _safe_number coerces so
        # the exclusion-disclosure row degrades to 0 (no false "N excluded") instead.
        "excluded_runs": [
            {
                "target_display": r.get("target_display") or r.get("target"),
                "items_failed": int(_safe_number(r.get("items_failed"), 0.0)),
            }
            for r in comparison
            if int(_safe_number(r.get("items_failed"), 0.0)) > 0
        ],
    }


# _rate_limit_saves() retired 2026-05-21 alongside the
# rate-action / pending-ratings mechanism retirement. Function was
# computed every launchpad render and shipped to pageData["rateLimitSaves"]
# but the Vue template never read it (user said "remove this" pre-launch).
# Pure orphan; removed from both call site and definition. Registry:
# src/trinity_local/retired_names.py.


def _humanize_ago(seconds: int | None) -> str:
    """Friendly relative-time string for the launchpad UI."""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _browser_capture() -> dict:
    """Surface 33 — "Browser capture · last 24h" launchpad card.

    Per ``docs/spec-v1.6.md`` line 479-497: makes silent capture breakage
    VISIBLE. Walks ``~/.trinity/conversations/<provider>/*.json`` (the
    paths the v1.6 capture host writes to), counts per-provider, finds
    the most-recent mtime. If the extension stops working, the "Last
    capture" timestamp ages; same shape as the verdict_rate /
    handoff_ready / cortex_freshness checks.

    File-shape filtering (provider-conditional):
      - ``<conv_id>.stream.json`` for claude/chatgpt — adapter
        accumulator sidecars to canonical ``<conv_id>.json`` files;
        skipped to avoid double-counting.
      - ``<conv_id>.stream.json`` for gemini — IS the canonical
        output (Google's batchexecute is reply-only; gemini.js writes
        directly to ``.stream.json``). Counted.
      - ``stream-<urlhash>.json`` (any provider) — fallback no-adapter
        writes when no ``__TRINITY_ADAPTERS.<provider>`` exists. Since
        `gemini.js` shipped (commit 441bc28, task #135), all 3 named
        providers have adapters; this fallback path is dormant unless
        a new untracked provider URL gets visited. Always skipped
        (no conv_id, just an opaque url hash; not user-facing).

    Returns:
      - {has_data: False, install_command} when zero capture files
      - {has_data: True, total_captured, providers[], last_capture_iso,
         last_capture_ago_seconds, stale (when last_capture > 24h ago)}
        once captures exist.

    Per "Analytics never crash": any unexpected failure returns the
    empty shape.
    """
    from .state_paths import conversations_dir
    empty = {
        "has_data": False,
        "total_captured": 0,
        "captured_24h": 0,
        "providers": [],
        "last_capture_iso": None,
        "last_capture_ago_seconds": None,
        "stale": False,
        "install_command": "trinity-local install-extension",
    }
    conv_root = conversations_dir()
    if not conv_root.exists():
        return empty
    try:
        import time as _time
        now = _time.time()
        day_ago = now - 86400
        per_provider: dict[str, dict[str, int]] = {}
        latest_mtime: float = 0.0
        total = 0
        total_24h = 0
        # Shared enumerator (capture_host.iter_capture_files) applies the
        # stream-/gemini filter ONCE so this count + the >24h stale flag agree
        # with the CLI doctor's `_check_browser_capture` — they used to inline
        # independent copies of the filter (v1.7.300 de-dup, like cortex staleness).
        from .capture_host import iter_capture_files
        for f in iter_capture_files():
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            pp = per_provider.setdefault(f.parent.name, {"count": 0, "count_24h": 0})
            pp["count"] += 1
            total += 1
            if mtime > day_ago:
                pp["count_24h"] += 1
                total_24h += 1
            if mtime > latest_mtime:
                latest_mtime = mtime
        if total == 0:
            return empty
        ago_seconds = int(now - latest_mtime) if latest_mtime else None
        last_iso = None
        if latest_mtime:
            from datetime import datetime, timezone
            last_iso = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
        # Sidebar-sync diff per provider: surfaces "you have N unsynced
        # threads" signal the status CLI already shows. Same data source
        # (_query_sync_status) so launchpad + status + in-provider pill
        # all read from one place. Skipped for providers with 0 captures
        # (nothing to diff against — _query_sync_status returns the
        # provider doesn't exist, which is a different state).
        from .capture_host import _query_sync_status
        provider_rows = []
        for p, v in per_provider.items():
            row = {"provider": p, "count": v["count"], "count_24h": v["count_24h"]}
            try:
                sync = _query_sync_status({"provider": p})
                if sync.get("ok"):
                    row["sidebar_count"] = sync.get("sidebar_count", 0)
                    row["missing_count"] = sync.get("missing_count", 0)
            except Exception:
                # Per analytics-never-crash: sidebar lookup failure
                # silently drops the missing-count signal for that
                # provider; the launchpad still renders the row.
                pass
            provider_rows.append(row)
        return {
            "has_data": True,
            "total_captured": total,
            "captured_24h": total_24h,
            "providers": sorted(
                provider_rows,
                key=lambda r: r["count"],
                reverse=True,
            ),
            "last_capture_iso": last_iso,
            "last_capture_ago_seconds": ago_seconds,
            "last_capture_ago_human": _humanize_ago(ago_seconds),
            # > 24h is the silent-breakage signal — capture host should
            # fire at least once a day on any active install. The
            # launchpad shows a warning border when this flips True.
            "stale": ago_seconds is not None and ago_seconds > 86400,
            "install_command": "trinity-local install-extension",
        }
    except Exception:
        return empty


def _browser_extension() -> dict:
    """Read the persisted Chrome extension ID written by install-extension.

    The file:// launchpad calls chrome.runtime.sendMessage(<extensionId>, ...)
    to dispatch button clicks. Without the ID, tier-1 dispatch is dead
    silent — there's no way to discover the ID from JS alone (the user
    has to load the unpacked extension manually, copy the 32-char ID, and
    feed it to `trinity-local install-extension --extension-id <ID>`).

    Returns `{"extensionId": str|None, "configured": bool}`. The launchpad's
    dispatch script gates on `configured`: if False, skip the extension
    probe and go straight to shortcut/install-prompt.
    """
    from .registry import CHROME_WEB_STORE_URL as _ws
    try:
        from . import state_paths as _sp
        settings_path = _sp.telemetry_settings_dir() / "extension.json"
        if not settings_path.exists():
            return {"extensionId": None, "configured": False, "webStoreUrl": _ws}
        import json as _json
        data = _json.loads(settings_path.read_text())
        ext_id = data.get("extension_id")
        if isinstance(ext_id, str) and ext_id:
            return {"extensionId": ext_id, "configured": True, "webStoreUrl": _ws}
        return {"extensionId": None, "configured": False, "webStoreUrl": _ws}
    except Exception:
        return {"extensionId": None, "configured": False, "webStoreUrl": _ws}


def dispatch_readiness() -> dict:
    """Snapshot of whether the Chrome extension dispatch path is wired up.

    Read by `trinity-local portal-html --open-browser` so the CLI can print
    a precise hint when the extension isn't configured. Same data the
    file:// launchpad surfaces in its banner. macOS Shortcut tier retired
    2026-05-17; the legacy fields (`shortcut_applicable`/`shortcut_installed`)
    are kept on the return dict as always-False so callers that read them
    don't crash.

    Returns:
        {
            "extension_configured": bool,
            "host_on_path": bool,
            "shortcut_applicable": False,  # retired; always False
            "shortcut_installed": False,   # retired; always False
            "ready": bool,                 # extension is wired
            "recommended_action": str|None,  # one-line hint, None when ready
        }
    """
    # Resolve on the enriched runtime PATH (venv bin / ~/.local/bin / homebrew),
    # where the `trinity-local-capture-host` console_script is installed — a bare
    # shutil.which would falsely report "not on PATH" under a GUI-launched IDE
    # whose MCP server inherits a stripped PATH, even though the host is installed
    # and the Native-Messaging manifest invokes it by absolute path regardless.
    # Same class as the council-provider gate fix (which_on_runtime_path).
    from .runtime_env import which_on_runtime_path
    ext = _browser_extension()
    host_on_path = bool(which_on_runtime_path("trinity-local-capture-host"))
    ready = ext["configured"] and host_on_path

    recommendation: str | None = None
    if not ready:
        if ext["configured"] and not host_on_path:
            recommendation = (
                "Extension ID is configured but `trinity-local-capture-host` is "
                "not on PATH. Reinstall: `pip install -e .` (or `pip install "
                "trinity-local`) so the console script lands."
            )
        else:
            recommendation = (
                "No dispatch path active. Install the browser extension "
                "(chrome://extensions → Load unpacked → browser-extension/), "
                "then run `trinity-local install-extension --extension-id <ID>`."
            )

    return {
        "extension_configured": ext["configured"],
        "host_on_path": host_on_path,
        "shortcut_applicable": False,
        "shortcut_installed": False,
        "ready": ready,
        "recommended_action": recommendation,
    }


def _shortcut_status() -> dict:
    """Retired 2026-05-17 with the macOS Shortcut dispatcher kill, then
    deeper-cleaned in Pass B (commit 0555a25) when `canUseShortcut()`
    and the JS Tier-2 branch went away. Kept as a stable empty payload
    so `page_data["shortcutStatus"]` doesn't KeyError in any consumer.
    Always reports `applicable: False`; the launchpad banner stays
    hidden, and the JS dispatch path never tries the Shortcut tier
    because that branch no longer exists in launchpad_runtime.js.
    """
    return {"ok": True, "applicable": False}




def _core_status() -> dict:
    """Report the freshness of ~/.trinity/core.md vs the three thinking
    memories it actually distills (lens.md, topics.json, vocabulary.md
    per `distill.py`). picks + routing are scoreboards, NOT inputs to
    core.md — listing them here was a v1.7-collapse leak that fired
    false 'stale' warnings whenever a user rated a council. The
    launchpad surfaces this as a one-line hint so users notice when a
    fresh `distill` would help.

    Returns one of three states:
    - `{"state": "missing"}`  → core.md never built; lens/topics/... exist
    - `{"state": "stale"}`    → one or more source memories are newer
    - `{"state": "fresh"}`    → core.md is the newest of the bunch
    - `{"state": "empty"}`    → no memories present yet (cold install)
    """
    # Single source of truth (v1.7.301): distill.core_freshness() owns the
    # core-vs-sources computation + the canonical source list. The cockpit, the
    # CLI/dream skip-gate (distill.is_core_stale), and `trinity-local status`'s
    # memory marker all derive from it, so they can't drift (this used to inline
    # an independent copy coupled to distill only by a "must match" comment).
    from .distill import core_freshness
    return core_freshness()


def _vocabulary_status() -> dict:
    """Report whether vocabulary.md lags behind the lens it co-builds with.

    `trinity-local lens` rebuilds lens.md + topics.json but NOT
    vocabulary.md — only `dream`'s Phase 2.5 (re)writes vocab. So after a
    bare `lens` run the vocab sits stale: older than the lens, still
    carrying whatever anchors the corpus had at its last build. `dream`
    always writes vocab AFTER lens/topics, so `vocab.mtime < max(lens,
    topics).mtime` is a threshold-free staleness signal that fires ONLY on
    the `lens`-without-`dream` path — never on a normal dream (where vocab
    is the newest of the three).

    Found 2026-05-31: a real install's memory viewer showed template-header
    anchors (AREA / ROOMS / CURRENT FLOOR PLAN) that the #250 filter already
    drops — the on-disk vocab predated the filter because a `lens` run had
    bumped lens.md past it. core.md has _core_status to catch its lag; vocab
    had no equivalent, so the stale pollution surfaced silently.

    States mirror _core_status: empty / missing / stale / fresh.
    """
    from .state_paths import lens_path, topics_path, vocabulary_path

    refs = [p for p in (lens_path(), topics_path()) if p.exists()]
    if not refs:
        return {"state": "empty"}
    vocab = vocabulary_path()
    if not vocab.exists():
        return {"state": "missing"}
    try:
        vocab_mtime = vocab.stat().st_mtime
    except OSError:
        return {"state": "missing"}
    for ref in refs:
        try:
            if ref.stat().st_mtime > vocab_mtime:
                return {"state": "stale", "stale_source": ref.name}
        except OSError:
            continue
    return {"state": "fresh"}


def _memory_health() -> dict:
    """Aggregate the seven staleness signals the launchpad surfaces:
      - core.md staleness (vs the three thinking memories) via _core_status
      - vocabulary.md staleness (older than lens/topics) via _vocabulary_status
        — a bare `lens` run rebuilds lens+topics but not vocab
      - topics.json prompt_ids round-trip integrity (legacy pre-thread-aware)
      - picks.json cortex freshness: councils newer than last consolidate
        (Pillar 3 drift surfacing — `ask` routes on stale rules until
        re-consolidate; doctor.py `_check_cortex_freshness` mirrors this).
        (The picks centroid embedder-drift #277 and chairman-audit-disagreed
        signals were retired 2026-06-06 with the cortex collapse #298 — the new
        lens-basin routing has no separate cortex centroids to drift, no audit.)
      - lens.md pending user edits (#140 slice 3): live diff between
        current lens.md and the post-last-build snapshot. Surfaced so
        the user knows their hand-edits will be picked up by the next
        lens-build (closes the lens-edit-as-signal loop).
      - extension capture-drift (#147): code-patch patterns where a
        provider's streaming endpoint regex no longer matches. Gives
        the "Repair extension" button a trigger so the user knows
        WHEN to click.
      - extension auth-cookie-stale (#150): user-action pattern where
        the provider's auth cookie expired. Hint points at manual
        login refresh (council dispatch wouldn't help — fix is
        browser-side).

    Returns:
      {
        "issues": [{name, status, hint}, ...],   # only non-fresh items
        "ok_count": int,                         # memories with no issue
        "total_count": int,                      # all signals inspected
      }

    The launchpad renders the issues row only when issues is non-empty.
    Fresh state → silent → user isn't told "all good!" every launch.
    """
    # Each issue carries:
    #   name    — which memory file
    #   status  — short status badge
    #   hint    — prose hint (no embedded command — pure context)
    #   command — the CLI command the user should run, broken out so the
    #             launchpad can render a click-to-copy chip. None when
    #             the action is "navigate somewhere" rather than "run a CLI".
    #   href    — in-app navigation target. For a FILE-backed issue (core.md /
    #             lens.md / vocabulary.md / topics.json / picks.json) this is the
    #             memory viewer for that exact file, so the user can INSPECT the
    #             stale/edited memory before deciding to run the rebuild command —
    #             the "Inspect →" affordance the card template already renders
    #             (v-if="issue.href"). Backfilled centrally below so a new issue
    #             type can't forget it; non-file issues (the `extension` repair
    #             signals) stay None — they're actions, not viewable files.
    issues: list[dict[str, str | None]] = []
    total = 7  # was 9; the picks centroid embedder-drift (#277) AND the picks
               # chairman-audit-disagreed signals were retired 2026-06-06 with the
               # cortex collapse (#298) — the new lens-basin routing has no separate
               # cortex centroids (so they can't drift) and no LLM audit pass. Earlier:
               # the lenses.json same-horizon conflicts signal (Stage 4b #141) was
               # retired 2026-06-05 (superseded by the generators contradiction-split),
               # and the picks override_count (user-veto) signal went with the whole
               # user-pick layer 2026-06-05 — the chairman's pick stands alone.
    # 1. core.md freshness
    core = _core_status()
    state = core.get("state")
    if state == "stale":
        src = core.get("stale_source", "a source memory")
        issues.append({
            "name": "core.md",
            "status": "stale",
            "hint": f"{src} is newer than the distillation.",
            # --only-distill is the fast path: ~20s on a real install vs
            # ~5-15min for the full 5-phase dream. core.md is just the
            # distillation of the three upstream memories; if those are
            # current (which they usually are when only core.md is stale),
            # Phase 5 alone fixes it.
            "command": "trinity-local dream --only-distill",
            "href": None,
        })
    elif state == "missing":
        # Missing → no upstream memories may exist yet either. Safer to
        # run the full pipeline so the user gets a complete first-run.
        # --only-distill would write a thin core.md from empty inputs.
        issues.append({
            "name": "core.md",
            "status": "missing",
            "hint": "The singular core memory has not been compiled.",
            "command": "trinity-local dream",
            "href": None,
        })

    # 1b. vocabulary.md freshness vs the lens it co-builds with. A bare
    #     `lens` run rebuilds lens.md + topics.json but skips vocab (only
    #     `dream` Phase 2.5 writes it), so vocab can sit stale — older than
    #     the lens, carrying pre-filter anchors. `trinity-local vocabulary`
    #     is the fast targeted refresh (no full dream needed).
    vocab = _vocabulary_status()
    if vocab.get("state") == "stale":
        src = vocab.get("stale_source", "the lens")
        issues.append({
            "name": "vocabulary.md",
            "status": "stale",
            "hint": f"{src} was rebuilt after your vocabulary — a bare `lens` skips vocab.",
            "command": "trinity-local vocabulary",
            "href": None,
        })

    # (#298 cortex collapse) The picks centroid embedder-drift (#277) signal and
    # the picks chairman-audit-disagreed signal were retired 2026-06-06: the new
    # lens-basin routing has no SEPARATE cortex centroids (placement reads the
    # lens's live centroids, so they can't drift in an orthogonal space) and no
    # LLM audit pass. Routing staleness vs new councils is still surfaced below
    # (the cortex-freshness signal).

    # 4. topics.json — legacy per-turn schema doesn't carry thread_count.
    #    Surfacing as a one-time upgrade prompt; clears on next lens-build.
    try:
        from .state_paths import topics_path
        topics_p = topics_path()
        if topics_p.exists():
            payload = json.loads(topics_p.read_text(encoding="utf-8"))
            basins = payload.get("basins") or []
            has_thread_aware = any(b.get("thread_count", 0) for b in basins)
            if basins and not has_thread_aware:
                issues.append({
                    "name": "topics.json",
                    "status": "pre-thread-aware",
                    "hint": "Topology was computed per-turn (older schema).",
                    "command": "trinity-local lens",
                    "href": None,
                })
    except Exception:
        pass

    # 5. picks.json cortex freshness — councils newer than the last
    #    consolidate mean `ask` is routing on stale rules. Doctor's
    #    _check_cortex_freshness reports the same shape from the CLI
    #    side; this is the launchpad-facing surface so the user sees it
    #    without having to run `doctor`. Pillar 3 (drift surfacing)
    #    + Pillar 4 (supervision signal — stale picks waste the
    #    verdict signal that just came in).
    try:
        from .state_paths import picks_path
        # Shared cortex-staleness primitive (v1.7.299) — the CLI doctor's
        # `_check_cortex_freshness` computes "councils newer than the last
        # consolidate" from these SAME two functions, so the cockpit and `status`
        # can't disagree about staleness (they used to inline independent copies).
        from .cortex import freshest_consolidated_at, count_councils_newer_than
        picks_p = picks_path()
        if picks_p.exists():
            picks_data = json.loads(picks_p.read_text(encoding="utf-8"))
            freshest = freshest_consolidated_at(picks_data)
            if freshest:
                newer, _ = count_councils_newer_than(freshest)
                if newer > 0:
                    issues.append({
                        "name": "picks.json",
                        "status": "cortex-stale",
                        "hint": f"{newer} council(s) newer than the last consolidate — `ask` routes on stale rules.",
                        "command": "trinity-local consolidate",
                        "href": None,
                    })
    except Exception:
        pass  # cortex freshness check must never break launchpad

    # 6. lens.md pending edits — #140 slice 3. Live diff between current
    # lens.md and snapshot baseline. Not a "staleness" issue per se but
    # surfaced through the same channel: action-needed signal that
    # lens-build is the way to commit the user's edits into the corpus.
    try:
        from .me.lens_edits import pending_lens_edits_count

        pending = pending_lens_edits_count()
        if pending > 0:
            issues.append({
                "name": "lens.md",
                "status": "edits-pending",
                "hint": f"{pending} hand-edit(s) will be picked up by the next lens run (weight=3.0, strongest signal).",
                "command": "trinity-local lens",
                "href": None,
            })
    except Exception:
        pass  # capture pipeline must not break launchpad rendering

    # (7. lenses.json same-horizon conflicts — Stage 4b #141 slice 3 — retired
    # 2026-06-05; superseded by the generators pass's semantic contradiction-split.)

    # 8 + 9. Extension-repair patterns — #147/#150. The status CLI
    # already surfaces these; bringing them to the launchpad closes
    # the parity gap. Two signal kinds:
    #   - stale-auth-cookie (user-action): hint points at manual
    #     refresh; no auto-dispatch (login is on the user's side).
    #   - provider-extended-silence (code-patch): hint points at the
    #     auto-repair flow which the "Repair extension" button on this
    #     same card fires. Surfacing this signal gives that button a
    #     trigger — without it, the user doesn't know WHEN to click.
    try:
        from .commands.extension_repair import detect_failure_patterns, diagnose

        patterns = detect_failure_patterns(diagnose())
        code_patches = [p for p in patterns if p.get("fix_kind") == "code-patch"]
        user_actions = [p for p in patterns if p.get("fix_kind") == "user-action"]
        if code_patches:
            # Display the login domain (chatgpt.com), not the raw web-era capture
            # slug (chatgpt) — more actionable + no internal-token leak. Matches
            # the fix_command's `_provider_url(slug)`. Falls back to the slug for
            # any pattern dict that predates the provider_url field.
            providers = ", ".join(p.get("provider_url") or p["provider"] for p in code_patches)
            issues.append({
                "name": "extension",
                "status": "capture-drift",
                "hint": (
                    f"{len(code_patches)} provider(s) with code-patch "
                    f"pattern ({providers}). Click 'Repair extension' "
                    f"above to dispatch the self-healing council "
                    f"(no HAR needed)."
                ),
                "command": "trinity-local extension repair --auto",
                "href": None,
            })
        if user_actions:
            # Login domain (claude.ai, chatgpt.com), not the raw capture slug —
            # the hint says "Log out + log back in", so naming the exact site is
            # strictly more actionable. Consistent with the fix_command.
            providers = ", ".join(p.get("provider_url") or p["provider"] for p in user_actions)
            issues.append({
                "name": "extension",
                "status": "auth-cookie-stale",
                "hint": (
                    f"{len(user_actions)} provider(s) with stale auth "
                    f"({providers}). Log out + log back in to refresh — "
                    f"council dispatch wouldn't help (fix is browser-side)."
                ),
                "command": None,
                "href": None,
            })
    except Exception:
        pass  # extension diagnostic must not break launchpad rendering

    # Backfill the "Inspect →" memory-viewer href for every FILE-backed issue
    # (the card template renders the link v-if="issue.href"). Done in one central
    # pass keyed on the issue name so the append sites can't drift and a future
    # issue type gets the link for free. Only memory files the viewer can render
    # qualify — the `extension` repair signals aren't files, so they stay None.
    # Mirrors the established launchpad cross-link form (../portal_pages/memory.html
    # ?file=<name>) used by the routing/picks/topology cards + the "four files" card.
    _inspectable = {"core.md", "lens.md", "vocabulary.md", "topics.json", "picks.json"}
    for issue in issues:
        name = issue.get("name")
        if name in _inspectable and not issue.get("href"):
            issue["href"] = f"../portal_pages/memory.html?file={name}"

    return {
        "issues": issues,
        "ok_count": max(0, total - len(issues)),
        "total_count": total,
    }


def _safe_number(value: object, default: float) -> float:
    """Coerce a JSON-loaded value to a finite float, falling back to `default`.

    Render-path shape-guard (#304): a state file the user can hand-edit can carry
    a numeric field as a string ("0.4"), a bool, None, a non-numeric string
    ("abc"), or a NaN/Inf (poisoned float). A bare `float(...)`/`int(...)` raised
    ValueError that bubbled up and blanked the whole launchpad; a surviving NaN
    serializes as bare `NaN` and breaks the client's JSON.parse the same way.
    Coerce to a finite float here so the caller renders a real number every time.

    Delegates to the shared `utils.finite_float_or_none` primitive (one source of
    truth across every numeric render-path reader) — the same guard
    `personal_routing.aggregate_routing_table` uses on a council `overall`.
    """
    coerced = finite_float_or_none(value)
    return default if coerced is None else coerced


def _safe_text(value: object) -> str:
    """Coerce a JSON-loaded value to a clean string, "" for non-string/empty.

    The STRING analog of `_safe_number` (#304 / Iter 256's isFiniteNumber). A
    state file the user can hand-edit — picks.json's `winner` is the documented
    case — can carry a string field as a NUMBER (`"winner": 42`), an OBJECT
    (`{"primary": "claude"}`), null, or an array. The old
    `(p.get("winner") or "").strip()` assumed str-or-None: a NUMBER winner raised
    `'int' object has no attribute 'strip'` that bubbled out of build_page_data
    and BLANKED THE WHOLE LAUNCHPAD (the same render-crash class the numeric
    `_safe_number` guard already closed for margin/count). Coerce here so a
    corrupt non-string field degrades to "" (the caller skips it) instead of
    crashing the page or painting "[object Object]"/a bare number.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def _load_cortex_rules() -> dict | None:
    """Surface the lens-derived routing picks for the launchpad (#298 collapse).

    Returns a compact dict the template can render as the "what Trinity has
    learned about you" headline card — the visible artifact of the consolidation
    pass (`trinity-local consolidate`). Each pick is the lens-basin tally
    `{winner, count, margin, n_episodes, evidence}`; sorted by margin desc so the
    highest-confidence picks render first.

    Degrades gracefully on a malformed/legacy entry (a dict missing `winner` is
    skipped). Returns None when no consolidation has run yet — the launchpad
    shows an empty-state CTA pointing at `trinity-local consolidate`.
    """
    try:
        from .cortex import load_routing_patterns
    except Exception:
        return None
    patterns = load_routing_patterns()
    if not patterns:
        return None
    # Compact view for the template — only the fields the launchpad card needs.
    rules = []
    for basin_id, p in patterns.items():
        # POST-COLLAPSE picks are plain dicts; a legacy RoutingPattern dict (or
        # any entry missing `winner`) is skipped so the card never crashes.
        if not isinstance(p, dict):
            continue
        # _safe_text shape-guards the STRING field the same way _safe_number does
        # the numeric ones below: a corrupt non-string winner (a NUMBER / OBJECT
        # from a hand-edited or half-migrated picks.json) used to hit `.strip()`
        # on an int and blank the whole launchpad. Now it degrades to "" → skip.
        winner = _safe_text(p.get("winner"))
        if not winner:
            continue
        # Shape-guard the NUMERIC fields (guard_shape_not_just_parse / #304):
        # picks.json is a hand-editable state file with a documented migration
        # chain (cortex/routing_patterns.json → memories/picks.json →
        # scoreboard/picks.json), so a `margin`/`count`/`n_episodes` can land as
        # a string ("0.4") or other non-numeric value (a half-migrated or
        # hand-mangled entry). `float("abc")` / `int("five")` raised a bare
        # ValueError that bubbled out of build_page_data and BLANKED THE WHOLE
        # launchpad (the same #304 wrong-type-shape class as the by_rejection_type
        # / NaN-eval-score fixes). Coerce defensively — a bad value degrades that
        # one field to 0, the pick still renders, the page still mounts.
        margin = round(_safe_number(p.get("margin"), 0.0), 3)
        rules.append({
            "basin_id": basin_id,
            "winner": winner,
            "margin": margin,
            # Pre-formatted (Python 2dp / banker's round) so the routing card
            # renders the SAME margin string as the CLI `consolidate` line
            # (cortex.py `f"{:.2f}"`) and the memory-viewer picks Reader — never
            # JS `Number.toFixed(2)` (round-half-UP), which paints a 0.625 margin
            # as "0.63" while the CLI prints "0.62" (same picks.json, two numbers
            # across surfaces). The raw float `margin` above stays for the
            # numeric `< winner_margin_floor` gate; only the DISPLAY is the
            # string. Same cross-language fix as _fmt_score for eval scores.
            "margin_str": _fmt_score(margin, 2),
            "count": int(_safe_number(p.get("count"), 0.0)),
            "n_episodes": int(_safe_number(p.get("n_episodes"), _safe_number(p.get("count"), 0.0))),
            # First few council thread-bundle IDs the pick was tallied
            # from (bundle_<hash>, links to the live council page). Deduped
            # order-preserving — a basin can cite the same council more than
            # once (one council can contribute multiple episodes), but the
            # user wants distinct evidence chips, not the same link 3×.
            # Capped at 5 (the full set is in council_outcomes/ — only need
            # a peek). Empty list when the consolidator didn't record IDs.
            # Coerce evidence ids to clean strings (string shape-guard): a corrupt
            # entry (a number/object in the list) would otherwise produce a bad
            # href + an "[object Object]" in the chip title tooltip.
            "evidence": [c for c in dict.fromkeys(_safe_text(e) for e in (p.get("evidence") or [])) if c][:5],
        })
    # Highest margin first — that's what the user wants to see at the top.
    # Tie-broken on basin_id so the routing card is a TOTAL order: two basins
    # at an equal (rounded) margin would otherwise keep the picks.json dict
    # order, so the rows would swap positions on re-read. basin_id is the stable
    # per-basin id; the template renders `cortexRules.rules` in server order
    # (no JS re-sort), so this IS the painted order.
    rules.sort(key=lambda r: (-r["margin"], str(r["basin_id"])))
    # Annotate each pick with the topology basin it bridges to. Post-collapse
    # the routing basins ARE the topology basins (both keyed b00..), so this is
    # an identity map read straight off the picks keys (no centroid match).
    task_to_basin = _task_to_topology_basin()
    for r in rules:
        bid = task_to_basin.get(str(r["basin_id"]))
        if bid:
            r["topology_basin"] = bid
    # Thread the REAL routing gate so the template demotes exactly the basins
    # ask() abstains on — never a hardcoded guess. A basin at margin >= this
    # floor is routed by `ask._try_cortex_route`; below it ask falls to kNN, so
    # the card must dim/label only the sub-floor rows. (Without this single
    # source of truth the card drifts from the router — e.g. a 0.16-margin basin
    # that ask routes but a hardcoded 0.2 demote-threshold mislabels "abstains".)
    try:
        from .lens_routing import WINNER_MARGIN_FLOOR
    except Exception:
        WINNER_MARGIN_FLOOR = 0.15
    return {
        "rules": rules,
        "total_basins": len(rules),
        "winner_margin_floor": WINNER_MARGIN_FLOOR,
    }


def _load_decisions_by_id() -> dict:
    """Map each self-expressed preference act → `{id: decision}` for the
    launchpad lens card's "justified by" backrefs.

    EXTRACT-unification Stage 4a (read-path flip): sources the unified
    ledger (`preference_acts.jsonl`, trigger=self_expressed) instead of the
    legacy `decisions.jsonl`. The template keys (privileged / sacrificed /
    verbatim / basin / valence) are preserved by mapping the PreferenceAct
    fields back: verbatim←context, valence←kind. Returns {} on a cold
    ledger. Resilient — load_preference_acts already skips malformed lines.

    Surfaced on the launchpad lens card so every claim's `tension_decisions`
    IDs render as clickable backrefs to their source pair. Traceability per
    the README's "if it can't show its work, it doesn't get to claim the
    thought."
    """
    try:
        from .me.preference_acts import SELF_EXPRESSED, load_preference_acts
        out: dict = {}
        for a in load_preference_acts():
            if a.trigger != SELF_EXPRESSED or not a.id:
                continue
            out[str(a.id)] = {
                "id": a.id,
                "privileged": a.privileged,
                "sacrificed": a.sacrificed,
                "verbatim": a.context,
                "basin": a.basin,
                "valence": a.kind,
                "prompt_id": a.prompt_id,
            }
        return out
    except Exception:
        return {}


# Top-N caps for the launchpad taste card's per-item lists (the
# unbounded-render class — Iter 251). Every list here GROWS with the
# corpus: a deep multi-domain power-user accumulates many accepted paired
# tensions, many domain-local "preserve_as_ordering" pairs, and (per
# accepted lens) a tension_decisions list that lengthens as the rejection
# corpus deepens. The taste card renders ALL of them IN THE MAIN PAGE FLOW
# (no scroll container) on the minimal HOME view a first-timer sees — so an
# uncapped render walls the whole home into a 16k+px scroll (measured:
# 50 paired × 6 decision-chips + 50 orderings → an 18,244px page, the
# recurring "iterate a long-tailed collection with no floor/cap → a 20k px
# wall" bug-shape). Cap to a top-N here, mirroring the #290 cheat-sheet
# pattern (slice + a "+N more" hidden-count note + an escape to the full
# list), and surface the dropped count so the card stays honest. The escape
# is the card's existing "View full lens →" button → memory.html?file=lens.md,
# which renders the COMPLETE lens (uncapped). _PAIRED keeps every real lens
# (the pipeline expects 4–8 accepted tensions); _DECISIONS_PER_LENS keeps
# enough "show the work" chips to justify a tension without a 6×-per-row
# expander wall (the full justification rides the lens.md viewer).
_TASTE_PAIRED_CAP = 8
_TASTE_ORDERINGS_CAP = 8
_TASTE_DECISIONS_PER_LENS_CAP = 4
_TASTE_LEGACY_LIST_CAP = 12  # rejections / abstract_lenses / vocabulary


def _cap_taste_list(rows: list, cap: int) -> tuple[list, int]:
    """Slice `rows` to the top `cap` and return (capped, hidden_count). The
    caller stamps hidden_count into the payload so the template can render
    an honest "+N more — see full lens" note + escape. Pre-sorted upstream
    (the pipeline emits accepted lenses + orderings strongest-first), so the
    head is the most-supported subset, not an arbitrary cut."""
    if cap < 0 or len(rows) <= cap:
        return rows, 0
    return rows[:cap], len(rows) - cap


def _load_taste_lenses() -> dict | None:
    """Surface taste lenses for the launchpad.

    Prefers the new 5-stage pipeline output (`me/lenses.json`,
    `me/orderings.json`) which carries pole_a / pole_b / failure
    modes / spanned basins. Falls back to the legacy single-virtue
    parse when the pipeline hasn't run yet.

    Returns None when neither source has data — the launchpad shows
    an empty-state CTA pointing at `trinity-local lens-build`.
    """
    from .me.pair_mining import load_lenses, load_orderings
    from .me_lenses import parse_taste_lenses

    paired = [p.to_dict() for p in load_lenses()]
    orderings = [p.to_dict() for p in load_orderings()]

    legacy = None
    try:
        lenses = parse_taste_lenses()
        if not lenses.is_empty:
            legacy = lenses.to_dict()
    except Exception:
        legacy = None

    if not paired and not orderings and not legacy:
        return None

    out: dict = legacy.copy() if legacy else {
        "rejections": [],
        "vocabulary": [],
        "abstract_lenses": [],
        "rejections_share_text": "",
        "vocabulary_share_text": "",
        "abstract_lenses_share_text": "",
        "combined_share_text": "",
    }
    # Enrich each paired lens with its accumulation signal from the
    # tension registry (#197/#198) so the launchpad lens card shows the
    # same support + stability the memory viewer's lens.md does. Matched
    # by (pole_a, pole_b) against the active registry entries. Additive
    # and graceful: if the registry is empty or unavailable, the keys
    # simply aren't set and the card renders without the support chip.
    try:
        from .me.lens_registry import (
            LOW_CONFIDENCE_BELOW,
            load_registry,
            support_index,
        )

        # Index ALL registry entries, not just active ones: the card renders
        # tensions from lenses.json regardless of registry recency, so a
        # tension that's gone inactive (>RECENCY_DAYS since last rebuild)
        # should still show its accumulated support rather than silently
        # losing the chip. Active/inactive visibility is the lens.md render
        # layer's job, not the card's.
        sidx = support_index(load_registry())
        for pl in paired:
            sig = sidx.get((pl.get("pole_a"), pl.get("pole_b")))
            if sig:
                pl["support_count"] = sig["support_count"]
                pl["first_seen"] = sig["first_seen"]
                pl["last_confirmed"] = sig["last_confirmed"]
                pl["low_confidence"] = sig["support_count"] < LOW_CONFIDENCE_BELOW
    except Exception:
        pass
    # ── Cap the per-item lists (unbounded-render class, Iter 251) ──────────
    # "Copy as text" is a deliberate FULL export, so build its share text from
    # the UNCAPPED lists below (snapshot the full sets BEFORE the cap mutates
    # the locals). The CARD renders the capped subset + a "+N more → full lens"
    # note; the clipboard copy stays complete.
    full_paired = list(paired)
    full_orderings = list(orderings)
    # Per-lens decision chips first: a deeply-supported tension can carry
    # dozens of tension_decisions, each rendered as an inline <details>
    # expander — the dominant component of the page wall (50 paired × 6 = 300
    # chips measured). Keep the head N (the strongest justifications) + record
    # how many were hidden so the chip row can say "+N more in lens.md".
    for pl in paired:
        tds = pl.get("tension_decisions")
        if isinstance(tds, list) and len(tds) > _TASTE_DECISIONS_PER_LENS_CAP:
            pl["tension_decisions_hidden"] = len(tds) - _TASTE_DECISIONS_PER_LENS_CAP
            pl["tension_decisions"] = tds[:_TASTE_DECISIONS_PER_LENS_CAP]
    paired, out["paired_lenses_hidden"] = _cap_taste_list(paired, _TASTE_PAIRED_CAP)
    orderings, out["orderings_hidden"] = _cap_taste_list(orderings, _TASTE_ORDERINGS_CAP)
    # Legacy single-virtue lists (only present on the pre-pipeline fallback,
    # but cap them too so the class is closed at the one data source the card
    # renders from). _cap_taste_list returns (rows, hidden); a 0 hidden-count
    # makes the note self-hide.
    for _key, _cap in (
        ("rejections", _TASTE_LEGACY_LIST_CAP),
        ("abstract_lenses", _TASTE_LEGACY_LIST_CAP),
        ("vocabulary", _TASTE_LEGACY_LIST_CAP),
    ):
        _rows = out.get(_key)
        if isinstance(_rows, list):
            out[_key], out[f"{_key}_hidden"] = _cap_taste_list(_rows, _cap)
    out["paired_lenses"] = paired
    out["orderings"] = orderings
    # Traceability: load decisions.jsonl into a {id: {...}} map so the
    # launchpad lens card can render `tension_decisions` IDs as clickable
    # backrefs to the source rejection pairs that justify each lens claim.
    # Principle: "if it can't show its work, it doesn't get to claim the
    # thought." Schema per src/trinity_local/me/decisions.py — each
    # decision has id, privileged, sacrificed, valence, basin, verbatim,
    # prompt_id. Verbatim is the user's actual words from that moment.
    out["decisionsById"] = _load_decisions_by_id()
    if full_paired or full_orderings:
        # Build a combined share text from the new-pipeline form — preferred
        # over the single-virtue legacy text once the pipeline ships.
        # IMPORTANT: build from paired AND orderings. A real Stage-3 output is
        # orderings-only (no full lens passed all 3 tension tests, but some
        # pairs were preserved as directional orderings) — in that state the
        # taste card STILL renders (the "Orderings" block) AND the "Copy as
        # text" button. If this text were gated on `paired` alone, the button
        # would copy "" → copyLens's `if(!text) return` makes it a SILENT
        # NO-OP: the user clicks the only share affordance and nothing happens,
        # no ✓, while the card visibly has shareable content.
        # Use the FULL (uncapped) lists — the clipboard copy is a deliberate
        # complete export, distinct from the card's capped on-screen render
        # (Iter 251 unbounded-render cap): the user copies their WHOLE lens.
        lines: list[str] = []
        if full_paired:
            lines.append("My lenses (paired tensions Trinity surfaced):")
            lines.append("")
            for p in full_paired:
                lines.append(f"→ {p['pole_a']} ↔ {p['pole_b']}")
                if p.get("failure_a") and p.get("failure_b"):
                    lines.append(f"   pure-{p['pole_a']} fails as {p['failure_a']}; pure-{p['pole_b']} fails as {p['failure_b']}")
        if full_orderings:
            if lines:
                lines.append("")
            lines.append("Orderings (preferences without dual evidence):")
            lines.append("")
            for o in full_orderings:
                lines.append(f"→ {o['pole_a']} > {o['pole_b']}")
        lines.append("")
        lines.append("(via trinity-local)")
        out["combined_share_text"] = "\n".join(lines)
    return out


def _format_relative_date(iso: str) -> str:
    # ISO timestamp → friendly relative date for the recent-council cards.
    # "2026-05-08T14:47:53+00:00" -> "May 8" or "Today" or "3 days ago".
    # Falls back to the raw string if parsing fails — better noisy than blank.
    from datetime import datetime, timezone

    if not iso or iso == "unknown":
        return iso or "unknown"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return iso
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
    delta = (now - dt).total_seconds()
    if delta < 0:
        return dt.strftime("%b %-d")
    if delta < 3600:
        return "Just now"
    if delta < 86400:
        h = int(delta // 3600)
        return "1 hour ago" if h == 1 else f"{h} hours ago"
    if delta < 86400 * 7:
        d = int(delta // 86400)
        return "Yesterday" if d == 1 else f"{d} days ago"
    return dt.strftime("%b %-d")


# In-process cache for topics.json — invalidated on (path, mtime) change.
# A single launchpad render calls into the helpers below 4× (cortex card,
# recent-card builder, both topology-helper consumers); without this we
# parse the file 4×. (path, mtime) key — not mtime alone — so test
# fixtures in different isolated_home dirs that happen to share the
# same second-level mtime don't leak cached basins across each other.
def _load_topics_basins() -> list[dict]:
    """Shape-guarded basin list for the launchpad render. Thin alias for the
    single canonical reader `lens_routing.load_topics_basins()` (which owns the
    mtime cache + the dict-root/list/dict-entry guard that #304 hardened here).
    Kept as a local name so `_topology_basin_labels`/`_task_to_topology_basin`
    read through one function and a single launchpad render re-parses topics.json
    at most once."""
    from .lens_routing import load_topics_basins

    return load_topics_basins()


def _topology_basin_labels() -> dict[str, str]:
    """Return {basin_id: "term1 · term2 · term3"} from topics.json.

    Used by launchpad chips that deep-link to topology basins — the
    basin id "b03" alone is opaque, so the hover tooltip surfaces the
    basin's top TF-IDF terms. Same data the topology graph node label
    already shows, just made available to the launchpad's Vue chips.

    Returns {} when topics.json is missing or unparseable so chips
    keep working with their fallback "Open basin <id>" tooltip.
    """
    out: dict[str, str] = {}
    for b in _load_topics_basins():
        if not isinstance(b, dict):
            continue  # defense-in-depth: a wrong-shaped basin entry
        bid = b.get("id")
        if not bid:
            continue
        terms = b.get("top_terms") or []
        if terms:
            # Top-3 is enough for a hover tooltip; the full list lives
            # in the basin detail panel. Plain " · " separator matches
            # the topology view's label style.
            out[str(bid)] = " · ".join(str(t) for t in terms[:3])
    return out


def _safe_merge_summary() -> dict:
    """Return summarize_merges() output, falling back to a zero-filled
    dict if the import fails (cold install / circular dep). Keeps
    page_data shape stable across cold + warm installs."""
    try:
        from .merges import summarize_merges
        return summarize_merges()
    except Exception:
        return {
            "total": 0,
            "by_type": {},
            "by_signal_type": {},
            "first_ts": None,
            "last_ts": None,
        }


def _task_to_topology_basin() -> dict[str, str]:
    """Return {pick_basin_id: topology_basin_id} for each routing pick whose
    basin id exists in topics.json.

    POST-COLLAPSE (#298): the routing basins ARE the topology basins — both keyed
    by the lens basin id (b00..) — so this is an identity map read straight off
    the picks keys, no centroid match. (Pre-collapse this centroid-matched the
    cortex's SEPARATE basin_centroid into topics.json; the new picks carry no
    centroid.) Degrades gracefully: a malformed/legacy pick (missing `winner`) is
    skipped. Returns {} on any error (cold install, missing topics.json) so the
    cards keep rendering.
    """
    try:
        from .cortex import load_routing_patterns
    except Exception:
        return {}
    try:
        patterns = load_routing_patterns()
    except Exception:
        patterns = None
    if not patterns:
        return {}
    basins = _load_topics_basins()
    if not basins:
        return {}
    topology_ids = {str(b.get("id")) for b in basins if b.get("id")}
    result: dict[str, str] = {}
    for basin_id, pick in patterns.items():
        # Skip legacy/malformed picks (a dict missing `winner`); the topology
        # bridge only applies to live lens-basin picks. _safe_text shape-guards
        # the STRING field — a corrupt non-string winner (a NUMBER / OBJECT in a
        # hand-edited picks.json) used to hit `.strip()` on an int and crash the
        # whole launchpad render here too (the sibling of the _load_cortex_rules
        # site). Degrades to "" → skipped.
        if not isinstance(pick, dict) or not _safe_text(pick.get("winner")):
            continue
        bid = str(basin_id)
        if bid in topology_ids:
            result[bid] = bid
    return result


def build_recent_sidebar_html(recent_councils: list[dict[str, str | None]]) -> str:
    """Compact vertical council list for the persistent left rail (the
    ChatGPT/claude.ai pattern — founder request 2026-06-01: councils were
    buried in the single-column scroll). This is the SINGLE home for the
    council list — the old in-page card grid (build_recent_cards_html) was
    removed 2026-06-06 after the rail folded it in.

    Each row links to `live_council.html?thread_id=` — the SINGLE always-written
    review page (set by _load_recent_councils), NOT a per-council `<id>.html`
    stub (those exist for only the runner-completed subset, ~65/562, so linking
    to them would 404 for most councils). Guarded by
    test_launchpad_data.test_recent_council_links_target_the_always_present_live_page."""
    from .council_schema import provider_model_brand

    def _row(item: dict[str, str | None]) -> str:
        thread_id = item.get("chain_root_id") or item.get("council_id")
        review_path = item.get("review_page_path")
        if not review_path or not thread_id:
            return ""
        href = f"../review_pages/{_esc(Path(str(review_path)).name)}?thread_id={_esc(str(thread_id))}"
        title = str(item.get("title") or "Untitled council")
        # Brand the winner (Chatgpt→GPT, Antigravity→Gemini, Claude_Ai→Claude) so
        # the rail matches the Elo chart's recognizable labels — the founder
        # disliked the fragmented harness slugs (#275). Falls back to the raw
        # title-cased slug for anything the brand map doesn't know (local models).
        winner_slug = item.get("winner_provider") or ""
        # SOLO-COUNCIL HONESTY (sibling of the share card's Iter-57 +
        # the live page's Iter-74 suppression): a 1-responder council carries
        # a chairman-emitted winner_provider (the chairman runs regardless of
        # member count), but rendering that winner brand here reads as "this
        # model won a contest" when no contest happened. A solo council can't
        # win against nobody — and neither can an all-same-provider one
        # (claude·claude·claude: 3 responders, one voice, winner == runner-up).
        # When the latest round had <= 1 DISTINCT provider, show an honest
        # "Solo" marker instead of the fake winner brand. member_count carries
        # the DISTINCT-provider count (Iter 111); it is None on legacy/imported
        # outcomes that predate the field — those fall through to the winner
        # brand (we don't know they were solo).
        try:
            member_count = int(item.get("member_count"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            member_count = None  # legacy/missing → can't prove solo
        # ZERO distinct responders is NOT solo — it's a council where every
        # member failed (a hand-edited / legacy / imported outcome with
        # member_results=[]; the runner itself raises before persisting, but the
        # rail renders whatever lands on disk — the #258 hand-editable-state
        # class). Folding member_count==0 into the solo branch painted the
        # SAME "Solo" marker + "Only ONE model answered — no council" tooltip on
        # a council where ZERO models answered: a flat lie, and the unfixed
        # sibling of the share-card / live-page all-failed honesty. Split it out.
        no_responders = member_count == 0
        is_solo = member_count is not None and member_count == 1
        if no_responders:
            winner = "Failed"
        elif is_solo:
            winner = "Solo"
        elif winner_slug:
            winner = provider_model_brand(winner_slug)
        else:
            winner = "—"
        created = _format_relative_date(item.get("created_at") or "")
        # A multi-round chain (refine/continue/auto-chain) collapses to ONE rail
        # card whose thread_id link "reveals every round on one scrollable page"
        # (see _load_recent_councils docstring). segment_count is computed and
        # threaded all the way here — but a 3-round chain rendered IDENTICALLY to
        # a single council, so Trinity's signature iterate-to-convergence feature
        # was invisible in the rail. Surface the round count when >1 so the user
        # can tell a chain from a one-shot before they click in.
        try:
            segments = int(item.get("segment_count") or 1)
        except (TypeError, ValueError):
            segments = 1
        chain_badge = (
            f'<span class="rail-council-rounds" title="This council ran '
            f'{segments} rounds — open it to see every round.">{segments} rounds</span>'
            if segments > 1 else ""
        )
        # Wrap the winner/Solo/Failed token so the degenerate cases carry an
        # explanatory tooltip (a bare label with no context reads as a model
        # name). "Failed" must NOT claim "one model answered" (zero did).
        if no_responders:
            winner_span = (
                f'<span class="rail-council-winner" title="Every model failed to '
                f'respond — no answer, no winner.">{_esc(winner)}</span>'
            )
        elif is_solo:
            winner_span = (
                f'<span class="rail-council-winner" title="Only one model '
                f'answered — no council, so there\'s no winner.">{_esc(winner)}</span>'
            )
        else:
            winner_span = f'<span class="rail-council-winner">{_esc(winner)}</span>'
        return (
            f'<a href="{href}" class="rail-council" data-title="{_esc(title.lower())}" '
            f'title="{_esc(title)}">'
            f'<span class="rail-council-title">{_esc(title)}</span>'
            f'<span class="rail-council-meta">{winner_span} · {_esc(created)}'
            f'{chain_badge}</span>'
            f'</a>'
        )

    rows = "".join(_row(item) for item in recent_councils)
    return rows or '<p class="rail-empty">No councils yet — ask one above.</p>'
