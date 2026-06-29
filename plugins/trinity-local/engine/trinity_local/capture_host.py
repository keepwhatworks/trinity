"""Trinity Local — native messaging host (v1.6 browser capture).

Chrome's stdio wire format is length-prefixed JSON:
    [4-byte little-endian length][UTF-8 JSON]

The browser extension's service worker connects via
``chrome.runtime.connectNative("local.trinity.capture")``. Chrome spawns
this process as a child on first connect, reads stdin until the
connection drops, and reaps the process. No listening port; no daemon.

Capture-host writes the full conversation snapshot to
``~/.trinity/conversations/<provider>/<conv_id>.json`` per payload.
Idempotency is by overwrite — each capture is the canonical state of
that conversation, so subsequent turns overwrite cleanly.

INVARIANT: NO NETWORK. The host imports no networking module. The
"your data, your machine" claim depends on it. Pinned by the regression
guard in ``tests/test_capture_host_no_network.py``.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

from .registry import CAPTURE_PROVIDERS
from .state_paths import conversations_dir


def _conv_dir() -> Path:
    return conversations_dir()


def iter_capture_files() -> list[Path]:
    """Canonical capture files across all providers under conversations_dir().

    The single source of truth for "which files count as a captured conversation",
    applying the provider-conditional filter ONCE:
      - skip ``stream-<urlhash>.json`` — raw-fallback orphans (no conv_id), written
        when no adapter exists for the domain; not user-facing conversations.
      - skip ``_``-prefixed sentinels (``_sidebar.json``) — per-provider metadata
        (the recent-conversations snapshot the sync diff reads), written by a
        ``sidebar_list`` capture independent of any real thread capture. Counting it
        as a conversation inflated the total AND masked the never-synced state
        (a scraped-but-uncaptured provider read as "1 captured · synced").
      - skip ``<conv_id>.stream.json`` EXCEPT under gemini/ — for claude.ai /
        chatgpt.com it's a sidecar accumulator alongside the canonical
        ``<conv_id>.json`` (skip to avoid double-counting); for gemini it IS the
        canonical output (Google's batchexecute is reply-only — gemini.js writes
        only the .stream.json). Shipped 2026-05-22 (441bc28).

    Shared by the CLI doctor (`health_checks._check_browser_capture`) and the
    launchpad cockpit (`launchpad_data._browser_capture`) so the capture COUNT and
    the >24h staleness flag are computed from ONE filter and can't drift apart.
    Best-effort: a missing conversations dir yields []. Callers stat the returned
    paths themselves (each keeps its own stat-error handling).
    """
    root = conversations_dir()
    files: list[Path] = []
    if not root.is_dir():
        return files
    for provider_dir in root.iterdir():
        if not provider_dir.is_dir():
            continue
        if provider_dir.name == "gemini":
            # Gemini writes ONE `<conv_id>__<ts>.stream.json` per SSE FRAME (Google's
            # batchexecute is reply-only streaming), so a single conversation explodes
            # into dozens of frame-files — measured 47 conversations -> 2465 files.
            # Counting raw frames inflated the capture total ~17× (a degenerate green:
            # the launchpad showed 2553 "captures" for ~143 real conversations). Collapse
            # to ONE representative per conversation (conv_id = the name prefix before
            # "__"), keeping the LATEST frame so the callers' mtime / 24h / last-capture
            # stay correct. Files without the "__<ts>" shape (e.g. `_sidebar.json`) aren't
            # conversations and are skipped.
            latest_per_conv: dict[str, Path] = {}
            for f in provider_dir.glob("*.json"):
                if f.name.startswith("stream-") or f.name.startswith("_"):
                    continue  # raw-fallback orphan / `_`-prefixed sentinel metadata (e.g. _sidebar.json), not a conversation
                # conv_id = the name with its `.stream.json`/`.json` extension stripped,
                # then truncated at the per-frame "__<ts>" suffix. So all SSE frames of
                # one gemini conversation (`<conv_id>__<ts>.stream.json`) AND a plain
                # `<conv_id>.json` collapse to a single conv_id.
                name = f.name
                for ext in (".stream.json", ".json"):
                    if name.endswith(ext):
                        name = name[: -len(ext)]
                        break
                conv_id = name.partition("__")[0]
                if not conv_id:
                    continue
                prev = latest_per_conv.get(conv_id)
                try:
                    if prev is None or f.stat().st_mtime > prev.stat().st_mtime:
                        latest_per_conv[conv_id] = f
                except OSError:
                    latest_per_conv.setdefault(conv_id, f)
            files.extend(latest_per_conv.values())
            continue
        for f in provider_dir.glob("*.json"):
            if f.name.startswith("stream-"):
                continue
            # `_`-prefixed files are per-provider sentinels/metadata, NOT
            # conversations — `_sidebar.json` is the recent-conversations snapshot
            # the sync diff reads (written by a `sidebar_list` capture, independent
            # of any real thread capture). The gemini branch above and
            # `_query_sync_status` already exclude it; this branch did NOT, so for
            # claude/chatgpt a `_sidebar.json` got counted as "1 captured
            # conversation" — inflating total_captured AND making a provider whose
            # sidebar was scraped but whose threads were NEVER captured read as
            # "1 captured · synced" instead of the truth ("0 captured · N unsynced").
            # Match the sentinel convention (`_` prefix), not just the one name.
            if f.name.startswith("_"):
                continue
            # `<conv_id>.stream.json` for claude.ai / chatgpt.com is a sidecar
            # accumulator alongside the canonical `<conv_id>.json` — skip to avoid
            # double-counting (gemini, handled above, is the one provider where the
            # .stream.json IS the canonical output).
            if f.name.endswith(".stream.json"):
                continue
            files.append(f)
    return files


# Cap the framed-message size before reading the body. Chrome permits
# extension→host Native-Messaging messages up to 4 GB (only host→extension is
# capped at 1 MB), so a compromised/buggy extension — or a page abusing the
# extension's confused-deputy surface — could send a 4-byte length prefix of
# ~4 GB and make `read(length)` accumulate gigabytes into memory (OOM/DoS).
# Real captures are well under 1 MB; 64 MB is a generous ceiling that still
# bounds the worst case. We can't safely resync the stream past an unread
# oversized body, so on violation we raise — main() acks the error and exits,
# and Chrome respawns the host on the next message.
_MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# Chrome caps a host→extension Native-Messaging frame at 1 MB and SILENTLY drops
# anything larger. The launchpad_data query returns the full page-data dict
# (~285 KB on a 313-council corpus), so we budget 900 KB and fail SOFT above it —
# a typed error the page can act on (fall back to the file:// launchpad) beats a
# dropped frame the page waits on forever.
_MAX_LAUNCHPAD_BYTES = 900 * 1024


def _read_message() -> dict[str, Any] | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        return None
    length = struct.unpack("<I", raw_length)[0]
    if length > _MAX_MESSAGE_BYTES:
        raise ValueError(
            f"message length {length} exceeds {_MAX_MESSAGE_BYTES}-byte cap"
        )
    body = sys.stdin.buffer.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _extract_target(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """Pull (provider, conv_id, conversation_state) out of a capture payload.

    The extension sends two payload kinds:

    * ``kind="stream"`` — raw streamed body text. Day-1 scaffold logs it
      as-is under a synthetic id keyed by url. Once the claude.js / chatgpt
      .js adapters land (Day 5+ of the v1.6 ship plan) they normalize the
      stream into a conversation tree and the kind is "canonical".
    * ``kind="canonical"`` — full conversation JSON from the provider's
      canonical-state endpoint (preferred — avoids reconstructing from
      streamed deltas).
    """
    raw = payload.get("payload") or payload
    provider = raw.get("provider")
    kind = raw.get("kind")
    if not provider:
        return None
    if kind == "canonical":
        conv = raw.get("conversation") or {}
        conv_id = conv.get("uuid") or conv.get("id") or conv.get("conversation_id")
        if not conv_id:
            return None
        return provider, str(conv_id), conv
    if kind == "sidebar_list":
        # Recent-conversations list snapshot. Used by the auto-sync diff
        # pipeline to compute "what's in the sidebar that we don't have
        # on disk yet?" — sentinel filename `_sidebar.json` per provider
        # so it doesn't collide with per-thread captures. Overwrites on
        # each fetch — only the latest snapshot is needed for the diff.
        return provider, "_sidebar", dict(raw)
    if kind == "adapter_stream":
        # Per-provider adapter (e.g. adapters/claude.js) has normalized
        # the streamed SSE body. The adapter provides conv_id directly.
        # The whole adapter result is saved — assistant_text, message_uuid,
        # events_count etc. — so consumers can join with the canonical
        # fetch (which arrives later under the same conv_id).
        conv_id = raw.get("conv_id")
        if not conv_id:
            return None
        # `file_stem`, when present, is what the adapter wants on disk
        # — typically a per-call discriminator like `<conv_id>__<msg_id>`
        # so multiple RPCs per turn (gemini fires several) don't
        # overwrite each other. conv_id stays the semantic field for
        # ingest-side grouping. Falls back to conv_id when absent
        # (claude/chatgpt one-stream-per-turn doesn't need it).
        stem = raw.get("file_stem") or conv_id
        return provider, f"{stem}.stream", dict(raw)
    if kind == "stream":
        # Raw (un-adapted) stream — fallback when no adapter is loaded
        # for this provider. Key by URL hash so distinct streams don't
        # overwrite each other.
        from hashlib import sha1
        url = raw.get("url") or ""
        conv_id = "stream-" + sha1(url.encode("utf-8")).hexdigest()[:16]
        return provider, conv_id, {"_raw_stream_body": raw.get("body_text", ""), "_url": url}
    return None


# 100-persona audit D6 fix (security blocker): conv_id + provider arrive
# unsanitized via Chrome Native Messaging. A compromised/malicious extension
# OR adversarial JSON in a captured response (conv.uuid is server-controlled)
# could send provider="../../.." or conv_id="../../../../.ssh/authorized_keys"
# and the host would happily write attacker-controlled JSON anywhere the
# user can write. Strict allowlist on both fields blocks this primitive.
_SAFE_ID_RX = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")


def _sanitize_id(value: str, label: str) -> str:
    """Return value if it's filename-safe; else raise.

    Allows ASCII alnum + `.` `_` `-`, capped at 80 chars (UUIDs +
    `.stream` suffix fit). Rejects:
      - non-string types (a malicious payload could send an int/dict)
      - traversal sequences (`..`)
      - leading dots (would hide files / disrupt globs)
      - path separators (`/` `\\` already excluded by the allowlist)
    """
    if not isinstance(value, str) or not _SAFE_ID_RX.match(value):
        raise ValueError(
            f"unsafe {label}: must match [a-zA-Z0-9._-]{{1,80}}, got {value!r}"
        )
    if ".." in value or value.startswith("."):
        raise ValueError(
            f"unsafe {label}: contains traversal sequence or leading dot, got {value!r}"
        )
    return value


# Standing constraint #43102d25 — every `error` string this host hands back to
# the extension is painted VERBATIM into a user-facing surface: the popup
# ("Failed: <error>"), the launchpad dispatch ribbon (launchError = error), and
# the side-panel. The dispatch path historically surfaced the *last line of the
# CLI's stderr* (capture_host._run_action) and the query/capture paths
# interpolated a raw `{exc}`/`{e}`. When a `trinity-local` subcommand crashes
# with an uncaught exception that last line is the bare traceback exception line
# — e.g. `FileNotFoundError: [Errno 2] No such file or directory:
# '/Users/<name>/.trinity/memories/topics.json'` — leaking a Python type name,
# an `[Errno N]`, and an ABSOLUTE filesystem path straight into the popup. That
# is dishonest UX (the user can't act on a traceback frame) and an internals
# leak. `_safe_error` delegates to the shared `utils.safe_error_message` choke
# point (the SAME redaction the council member-failure path uses) so the two
# user-painted error surfaces can't drift apart.
def _safe_error(raw: object, *, fallback: str = "the command failed") -> str:
    from .utils import safe_error_message
    return safe_error_message(raw, fallback=fallback)


def _count_sidebar_items(payload: dict[str, Any]) -> int:
    """Number of conversations in a sidebar_list payload, across the
    per-provider shapes (chatgpt `items`, claude `data`, gemini DOM list)."""
    sb = payload.get("sidebar") or {}
    if isinstance(sb, list):
        return len(sb)
    if isinstance(sb, dict):
        for key in ("items", "data", "chat_conversations"):
            v = sb.get(key)
            if isinstance(v, list):
                return len(v)
    return 0


def _write_capture(provider: str, conv_id: str, conversation: dict[str, Any]) -> Path:
    from .utils import atomic_write_text
    provider = _sanitize_id(provider, "provider")
    conv_id = _sanitize_id(conv_id, "conv_id")
    target = _conv_dir() / provider / f"{conv_id}.json"
    # Clobber guard for the sidebar sentinel: claude.ai fires a filtered
    # `?starred=true` v2 list that returns [] for an account with no stars;
    # letting that empty snapshot overwrite a populated recent-conversations
    # sidebar zeroed the sync-pill count (found 2026-05-30). Refuse to replace
    # a non-empty sidebar with an empty one. (page-hook.js now also excludes
    # the starred variant at the source — this is belt-and-suspenders.)
    if conv_id == "_sidebar" and target.exists() and _count_sidebar_items(conversation) == 0:
        try:
            existing = json.loads(target.read_text())
            if _count_sidebar_items(existing) > 0:
                return target  # keep the good snapshot
        except (json.JSONDecodeError, OSError):
            pass
    atomic_write_text(target, json.dumps(conversation, indent=2, ensure_ascii=False))
    return target


# Phase 1 (Chrome extension transition): action-dispatch messages.
#
# The browser extension sends two message classes:
#   1. `kind` in {"canonical", "adapter_stream", "stream"} → conversation
#       captures (handled by _extract_target above, v1.6 flow)
#   2. `kind` in ACTION_ALLOWLIST → CLI-invocation requests (new in this
#       release — replaces the macOS Shortcuts dispatch path)
#
# The action allowlist is defense-in-depth: even if the extension is
# compromised, the host will only run pre-approved CLI surfaces. New
# action kinds require an explicit ALLOWLIST entry — adding one is a
# security review.

# Each entry can be one of two shapes:
#   2-tuple: (cli_subcommand, [(arg_name, json_field, required), ...])
#   3-tuple: above + a list of *constant* CLI flags appended unconditionally
#            (e.g. always pass `--open` when the launchpad's "Render lens
#            card" button fires render-me-card — the dispatcher path can't
#            shell-chain `open <path>`, so the CLI does it).
# Args are passed as `--<arg_name> <value>`; missing required → reject.
# The allowlist intentionally lists only the buttons the launchpad UI
# exposes today — not the full CLI surface.
# Action kinds in this set are fire-and-forget: capture_host spawns the
# CLI via Popen with stdio redirected to /dev/null and a fresh session
# (so it survives Chrome closing the Native Messaging pipe) and returns
# immediately with `{ok: true, detached: true, pid, status_token?}`.
# The caller is expected to poll via `get-council-status` using the
# status_token it generated.
#
# Without this, council-launch blocks the popup for the full council
# duration (30-90s) and times out at 120s with "Failed: unknown error".
#
# `council-iterate` (Refine / Continue / Auto-chain on the live council page)
# is here for the SAME reason: it runs a full council round (30-90s), but the
# live page's __TRINITY_DISPATCH__ gives up after ACTION_TIMEOUT_MS (8s) and
# reports "Couldn't reach the Trinity extension." Detaching makes the host ack
# instantly with the status_token; the page polls run-state for progress, just
# like launch-council. run_consensus_round writes its run-state early (an
# init_council_run_state before the slow model calls), so the page's
# MAX_MISSING_POLLS window sees "running" well before it gives up. Founder
# report 2026-06-12 (chain dispatch from the live council page).
_DETACHED_ACTIONS = {"launch-council", "lens-build", "council-iterate"}

# Action kinds handled in-process (no subprocess). The popup uses
# `get-council-status` to poll a council's status JSON without the
# ~150ms-per-call subprocess startup cost — capture_host has direct
# filesystem access so it just reads the JSON.
#
# `open-launchpad` is in-process when launchpad.html already exists —
# the old path (`trinity-local portal-html --open-browser`) ran a full
# refresh_launchpad rebuild on every click (~3-10s on real corpora).
# The static file is kept fresh by council/ingest callbacks, so a
# bare-open is correct in the steady state. Falls through to the
# subprocess regen path only when the file is missing (first install).
_INPROCESS_ACTIONS = {"get-council-status", "open-launchpad", "open-council-page"}

ACTION_ALLOWLIST: dict[str, tuple | None] = {
    "launch-council": (
        "council-launch",
        [
            ("task", "task", True),
            ("goal", "goal", False),
            ("primary-provider", "primary_provider", False),
            # status-token threads through to the council runner so the
            # status JSON at ~/.trinity/portal_pages/status/<token>.json
            # is written under a token the caller chose, not the bundle_id.
            # The popup uses this for its incremental status display.
            ("status-token", "status_token", False),
        ],
    ),
    # In-process: reads ~/.trinity/portal_pages/status/<token>.json
    # directly via council_status.load_council_status. No CLI subcommand.
    "get-council-status": None,
    # In-process when live_council.html exists; falls back to portal-html
    # regen via the open-launchpad entry below (which writes both pages
    # as a side effect) on first install.
    "open-council-page": None,
    "ingest-recent": (
        "ingest-recent",
        [],
    ),
    # #242(a) — the 'Building your lens' card's Stop / Restart buttons.
    # lens-stop is a no-arg cancel (drops the flag the build checks between
    # stages). lens-build re-kicks `lens --force` detached (a multi-minute
    # build mustn't block the popup — same reason as launch-council).
    "lens-stop": (
        "lens-stop",
        [],
    ),
    "lens-build": (
        "lens",
        [],
        ["--force"],
    ),
    # Memory Health "Refresh memory" button (council_1f9cbecd7104f90f #3).
    # The user's intent is "don't make me open a terminal" — not "auto-run
    # LLM calls without my knowledge." Dream is expensive and surprising
    # (10+ flagship calls, several minutes). A single button labeled
    # "Refresh memory" that the user clicks explicitly satisfies the
    # intent. No args from the launchpad — the defaults (full pipeline
    # incl. vocabulary, consolidate, lens-build, distill) are what
    # "refresh memory" means for someone whose lens has drifted.
    "dream": (
        "dream",
        [],
    ),
    # Phase 4b (council_bf1ab3f4dd70f75e residual-drift fix): stop-council
    # lets the launchpad's "Stop" button work cross-platform. Previously
    # the button fired a `shortcuts://run_command` payload that no-op'd
    # silently off macOS. Narrow allowlist entry — only --status-token,
    # no shell command — preserves the "no run_command" verdict from
    # the council.
    "stop-council": (
        "council-stop",
        [
            ("status-token", "status_token", True),
        ],
    ),
    # Refine / iterate / auto-chain / continue all dispatch to
    # `trinity-local council-iterate`. The legacy alias names
    # (council_refine / council_continue / council_auto_chain) map
    # to council_iterate per dispatch_registry.py L145, so a single
    # extension allowlist entry covers all four buttons on the
    # council-review page. council_review.py L519 was firing
    # shortcuts:// for this until tick 140 — the macOS Shortcut
    # dispatcher was retired pre-launch (claude.md L578), so the
    # supervision loop's only signal path was silently dead. This
    # entry restores it via the Chrome extension dispatch tier.
    "council-iterate": (
        "council-iterate",
        [
            ("council", "council", True),
            ("prompt", "prompt", False),
            ("rounds", "rounds", False),
            ("status-token", "status_token", False),
        ],
    ),
    # Phase 4b (council_bf1ab3f4dd70f75e residual-drift cleanup): the seven
    # settings toggles. Each is a no-arg CLI subcommand — the narrowest
    # possible allowlist surface, satisfying the council's "do NOT add
    # run_command" verdict. Enum-by-kind so spoofed payloads can't trigger
    # arbitrary shell commands.
    "telemetry-enable":   ("telemetry-enable",   []),
    "telemetry-disable":  ("telemetry-disable",  []),
    "telemetry-reset-id": ("telemetry-reset-id", []),
    # render-me-card closes the last residual-drift gap from
    # council_bf1ab3f4dd70f75e. The CLI grew an `--open` flag (Phase 4b
    # follow-up) so the host doesn't need to shell-chain `open <path>`.
    # `open` is a no-arg boolean — payload may include `{"open": true}`
    # to fire the cross-platform open after writing the PNG.
    "render-me-card": (
        "me-card",
        [],
        ["--open"],  # always opens the PNG after writing — that's what the
                     # launchpad button means by "render". The CLI honors
                     # --open via notifications.open_path (cross-platform).
    ),
    # open-launchpad regenerates ~/.trinity/portal_pages/launchpad.html
    # and opens it in the user's default browser. The extension popup
    # uses this as the single "Open Trinity launchpad" entry point —
    # the extension's own launchpad.html duplicate was removed in
    # favor of one canonical file:// surface.
    "open-launchpad": (
        "portal-html",
        [],
        ["--open-browser"],
    ),
    # #147 self-healing UI surface: the launchpad's "Repair extension"
    # button fires this. CLI runs `extension repair --auto --json` —
    # the --json output goes back to the launchpad which renders the
    # detected patterns + chairman's proposed patch (if any code-patch
    # pattern triggered the dispatch). No HAR required.
    #
    # No dynamic args: --auto is a fixed flag; the diagnose() walk
    # reads from ~/.trinity/conversations/ which the host already knows
    # the location of. Anything the user might want to override would
    # go through the CLI directly, not the launchpad surface.
    "extension-repair-auto": (
        "extension",
        [],
        ["repair", "--auto", "--json"],
    ),
    # #148 bulk Takeout import: the launchpad's "Import export" file-
    # picker fires this. CLI runs `import-export --path <PATH> --dry-run`
    # first (detection-only — no embedding cost) so the user can confirm
    # before the actual ingest. A second button click without --dry-run
    # runs the full ingest. Both buttons map to this same allowlist
    # entry; --dry-run flag in payload toggles the behavior.
    "import-export": (
        "import-export",
        [
            ("path", "path", True),
            ("source", "source", False),
            ("limit", "limit", False),
        ],
    ),
    # Variant for dry-run mode — separate allowlist entry so payload
    # can't escalate from probe-only to full ingest by manipulating
    # JSON. Same CLI under the hood; --dry-run is a constant flag
    # (host-controlled, not payload-influenced).
    "import-export-dry-run": (
        "import-export",
        [
            ("path", "path", True),
            ("source", "source", False),
        ],
        ["--dry-run"],
    ),
}


def _trinity_local_bin() -> str:
    """Locate the ``trinity-local`` CLI.

    Chrome launches Native Messaging hosts with a minimal PATH that
    typically excludes ``~/.local/bin`` / user-installed pip script
    dirs. Bare ``subprocess.run(["trinity-local", ...])`` PATH lookup
    fails under that env. But ``trinity-local`` and
    ``trinity-local-capture-host`` are installed by pip as siblings in
    the same ``bin/`` directory — we can resolve the CLI relative to
    THIS process's own binary path and skip PATH lookup entirely.

    Falls back to the bare name for the PATH-lookup path if the
    sibling isn't found (e.g., editable installs with unusual
    layouts) — that branch then surfaces the FileNotFoundError →
    "CLI not on PATH" error to the user, which is still informative.
    """
    try:
        host_bin = Path(sys.argv[0]).resolve()
        sibling = host_bin.parent / "trinity-local"
        if sibling.exists():
            return str(sibling)
    except (OSError, ValueError):
        pass
    return "trinity-local"


def _open_council_page(payload: dict[str, Any]) -> dict[str, Any]:
    """In-process handler for `open-council-page`.

    Opens the live council review page for a specific status_token —
    not the launchpad. URL shape mirrors the launchpad's
    liveCouncilUrl computed property: live_council.html with
    status_token + task + members as query params.

    The popup uses this both for the "Open council page" button and
    for the auto-open-on-completion handoff (so the user lands on
    the specific council that just finished, not the launchpad).
    """
    import webbrowser
    from .state_paths import review_pages_dir

    token = payload.get("status_token") or payload.get("status-token")
    if not isinstance(token, str) or not _SAFE_ID_RX.match(token):
        return {"ok": False, "error": "invalid status_token"}

    live = review_pages_dir() / "live_council.html"
    if not live.exists():
        # First-install fallback — fall through to portal-html regen so
        # live_council.html gets written, then re-open.
        return {"ok": False, "needs_regen": True}

    task = payload.get("task")
    task = task.strip() if isinstance(task, str) and task.strip() else ""
    members = payload.get("members")
    members = [str(m) for m in members] if isinstance(members, list) and members else []

    # macOS strips the query string (AND fragment) from file:// URLs opened via
    # `open`/`open location` — which is what webbrowser.open uses. So the
    # `?status_token=…` below survives ONLY over http://localhost (served), NOT
    # the file:// path the popup actually opens → the page loads bare and renders
    # nothing (founder report 2026-06-12, Image #10; verified: Chrome receives
    # `file:///…/live_council.html` with no query). Write a sidecar pointer the
    # page reads when it has no URL params — the file://-safe channel, same shape
    # as the status .js the page already script-injects.
    import json as _json
    sidecar = review_pages_dir() / "_active_council.js"
    try:
        pointer = {"status_token": token, "task": task, "members": members}
        sidecar.write_text(
            "window.__TRINITY_ACTIVE_COUNCIL__ = "
            + _json.dumps(pointer, separators=(",", ":"), ensure_ascii=True)
            + ";\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # sidecar is best-effort; the served-path query still works

    params: list[tuple[str, str]] = [("status_token", token)]
    if task:
        params.append(("task", task))
    if members:
        params.append(("members", ",".join(members)))

    # Inline %-encoder: the no-network regression guard bans `urllib`
    # at the namespace level (whole-stdlib safety net — see
    # tests/test_capture_host_no_network.py). The CGI rules for query
    # values are simple enough to do here without pulling in urllib.parse.
    _SAFE = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~"
    )
    def _q(s: str) -> str:
        out = []
        for ch in s:
            if ch in _SAFE:
                out.append(ch)
            else:
                for byte in ch.encode("utf-8"):
                    out.append(f"%{byte:02X}")
        return "".join(out)
    query = "&".join(f"{_q(k)}={_q(v)}" for k, v in params)
    url = live.as_uri() + "?" + query
    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        return {
            "ok": False,
            "error": "open_failed: " + _safe_error(
                exc, fallback="couldn't open the council page"
            ),
        }
    return {
        "ok": bool(opened),
        "action": "open-council-page",
        "url": url,
        "opened": bool(opened),
    }


def _open_launchpad(payload: dict[str, Any]) -> dict[str, Any]:
    """In-process handler for `open-launchpad`.

    Fast path: if ~/.trinity/portal_pages/launchpad.html already exists,
    just open it. The launchpad is regenerated as a side effect of every
    council/ingest run via refresh_launchpad, so the static file is
    fresh in the steady state.

    Slow path: file missing → fall back to running `trinity-local
    portal-html --open-browser` which writes + opens. Returns a sentinel
    {"ok": False, "needs_regen": True} so _run_action's caller layer
    re-dispatches via the subprocess branch.
    """
    from .notifications import open_path
    from .state_paths import trinity_home
    launchpad_path = trinity_home() / "portal_pages" / "launchpad.html"
    if not launchpad_path.exists():
        return {"ok": False, "needs_regen": True}
    opened = open_path(str(launchpad_path))
    return {
        "ok": bool(opened),
        "action": "open-launchpad",
        "path": str(launchpad_path),
        "opened": bool(opened),
    }


def _read_council_status(payload: dict[str, Any]) -> dict[str, Any]:
    """In-process handler for `get-council-status` polling.

    Reads ~/.trinity/portal_pages/status/<token>.json directly rather
    than shelling out — saves ~150ms per poll, which matters when the
    popup polls every 1.5s.
    """
    token = payload.get("status_token") or payload.get("status-token")
    if not isinstance(token, str) or not _SAFE_ID_RX.match(token):
        return {"ok": False, "error": "invalid status_token"}
    try:
        from .council_status import load_council_status
        status = load_council_status(token)
    except Exception as exc:
        return {
            "ok": False,
            "error": "status_read_failed: " + _safe_error(
                exc, fallback="couldn't read this council's status"
            ),
        }
    # status is None until the runner writes the first record. That's
    # the normal early-poll case — return ok:true with status:null so
    # the popup can keep rotating its loading copy without flashing
    # an error.
    return {"ok": True, "status": status, "status_token": token}


def _run_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an action message.

    Three paths:

    * In-process (``_INPROCESS_ACTIONS``) — handled by a dedicated
      function. Used for tight-loop polling like `get-council-status`
      where a 150ms subprocess startup per call is wasteful.
    * Detached (``_DETACHED_ACTIONS``) — subprocess.Popen with stdio
      redirected and a new session, returns immediately. Used for
      `launch-council` so the popup doesn't block 30-90s on a council.
    * Default — ``subprocess.run`` with capture_output, blocks until
      the CLI exits, returns stdout/stderr/returncode.
    """
    import shlex
    import subprocess

    kind = payload.get("kind")
    if kind not in ACTION_ALLOWLIST:
        return {"ok": False, "error": f"action {kind!r} not in allowlist"}

    # In-process fast paths. Each returns early on success; on a
    # `needs_regen` sentinel, we fall through to the subprocess regen
    # path below by re-binding `kind` to `open-launchpad` (which the
    # allowlist maps to `portal-html --open-browser` — that subcommand
    # writes BOTH launchpad.html and live_council.html so the next click
    # gets the fast path).
    if kind == "get-council-status":
        return _read_council_status(payload)
    if kind == "open-council-page":
        result = _open_council_page(payload)
        if result.get("needs_regen") is not True:
            return result
        kind = "open-launchpad"  # fall through to regen
    if kind == "open-launchpad":
        result = _open_launchpad(payload)
        if result.get("needs_regen") is not True:
            return result
        # First-install: portal-html regen writes the file then opens.

    entry = ACTION_ALLOWLIST[kind]
    if entry is None:
        return {"ok": False, "error": f"action {kind!r} has no CLI binding"}
    if len(entry) == 2:
        cli_subcommand, arg_spec = entry
        constant_flags: list[str] = []
    else:
        cli_subcommand, arg_spec, constant_flags = entry

    argv: list[str] = [_trinity_local_bin(), cli_subcommand]
    for arg_name, json_field, required in arg_spec:
        # Accept BOTH the underscore json_field ('status_token') and the hyphen
        # CLI-flag spelling ('status-token'). The launchpad sends underscore; a
        # dispatch payload that used the hyphen (the live council page did until
        # 2026-06-12) must not silently drop the value — that made council-iterate
        # run without its --status-token, so the page polled a token no run was
        # written under ("council never started"). The in-process handlers
        # (_open_council_page / _read_council_status) already tolerate both; this
        # closes the same gap on the CLI-dispatch path.
        value = payload.get(json_field)
        if value is None and arg_name != json_field:
            value = payload.get(arg_name)
        if value is None or value == "":
            if required:
                return {
                    "ok": False,
                    "error": f"missing required field {json_field!r} for action {kind!r}",
                }
            continue
        if not isinstance(value, (str, int, float)):
            return {
                "ok": False,
                "error": f"field {json_field!r} must be primitive, got {type(value).__name__}",
            }
        argv.extend([f"--{arg_name}", str(value)])

    # Append any constant flags (e.g. always `--open` for render-me-card).
    # These are defined in the allowlist, not in the payload — caller
    # cannot influence them, so they're safe to append unconditionally.
    argv.extend(constant_flags)

    # Detached path — fire-and-forget. Child inherits NOTHING from the
    # host's stdio (Chrome owns those FDs as the Native Messaging wire),
    # and runs in a new session so SIGHUP to the host doesn't take it
    # down. Caller polls status via `get-council-status`.
    #
    # Pass build_runtime_env() so the child can find provider binaries
    # (claude, codex, agy) — Chrome's spawn env strips ~/.local/bin
    # and Homebrew dirs, which is where those CLIs live. Without this,
    # every council launched from the popup fails with "Provider
    # binary not found: claude" within ~10s.
    from .runtime_env import build_runtime_env
    if kind in _DETACHED_ACTIONS:
        try:
            child = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=build_runtime_env(),
            )
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "trinity-local CLI not on PATH; re-run the Trinity installer (the curl-bash one-liner) or add its bin dir to PATH",
            }
        response: dict[str, Any] = {
            "ok": True,
            "action": kind,
            "detached": True,
            "pid": child.pid,
        }
        # Echo the status_token back so the popup doesn't have to remember
        # what it sent (and so a misroute is visible). Tolerate either spelling
        # for the same reason the arg loop above does.
        token = payload.get("status_token") or payload.get("status-token")
        if token:
            response["status_token"] = token
        return response

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            # Same PATH augmentation rationale as the detached branch
            # above — every CLI we dispatch may itself shell out to
            # claude / codex / agy binaries, which Chrome's minimal
            # PATH doesn't see.
            env=build_runtime_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "timeout after 120s",
            "argv": " ".join(shlex.quote(a) for a in argv),
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "trinity-local CLI not on PATH; install via "
                     "`curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash` "
                     "(no PyPI package), then ensure ~/.local/bin is on PATH",
        }
    # Real error message when the CLI exited non-zero. Previously the
    # popup got `ok: false` with no `error` field and rendered "Failed:
    # unknown error". Surface returncode + the last useful line of stderr
    # so the popup can show something diagnosable.
    ok = result.returncode == 0
    response = {
        "ok": ok,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "action": kind,
    }
    if not ok:
        last_stderr = (result.stderr or "").strip().splitlines()
        raw = last_stderr[-1] if last_stderr else f"exit code {result.returncode}"
        # Sanitize before it leaves the host: the popup paints this verbatim as
        # "Failed: <error>", so a crashed CLI's last traceback line would
        # otherwise leak an absolute path / [Errno N] / a Python type name
        # (#43102d25). Keep it actionable — point at the recovery verb.
        response["error"] = (
            _safe_error(raw, fallback=f"exit code {result.returncode}")
            + " — re-run `trinity-local status` to diagnose, then retry."
        )
    return response


def _is_action_message(msg: dict[str, Any]) -> bool:
    """An action message has `kind` in the action allowlist. Capture
    messages have `kind` in {canonical, adapter_stream, stream}."""
    return msg.get("kind") in ACTION_ALLOWLIST


# Read-only query handlers — no side effects, just compute + return.
# Distinct from actions (which dispatch CLI subprocesses) and captures
# (which write files). Used by the in-provider sync pill to ask
# "how many threads are in the sidebar that aren't captured locally?"
QUERY_KINDS = {
    "ping",
    "sync_status",
    "launchpad_data",
    "council_status",
    "council_outcome",
    "thread_manifest",
    "active_council",
}


def _query_ping(payload: dict[str, Any]) -> dict[str, Any]:
    """Cheap host-reachability check for the side-panel shell. Returns instantly,
    WITHOUT building the launchpad payload. The shell only needs to know the host
    is up to choose launchpad-vs-standalone; the launchpad iframe builds the real
    payload ONCE. (Using launchpad_data here made the host build the full payload
    TWICE per open — once for the shell's reachability check, once for the iframe —
    lingering the loading spinner on a large corpus. Founder-caught 2026-06-16.)"""
    return {"ok": True, "host": "trinity-local"}


def _query_sync_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Diff sidebar conv_ids vs on-disk capture conv_ids for one provider.

    Returns {provider, sidebar_count, on_disk_count, missing_count,
    missing_ids[:50]}. Used by the in-provider sync pill to decide
    whether to show + what count to display.
    """
    provider = str(payload.get("provider") or "").strip()
    if provider not in CAPTURE_PROVIDERS:
        return {"ok": False, "error": "invalid_provider"}
    try:
        provider = _sanitize_id(provider, "provider")
    except ValueError:
        return {"ok": False, "error": "invalid_provider"}

    provider_dir = _conv_dir() / provider
    sidebar_ids: set[str] = set()
    if (provider_dir / "_sidebar.json").exists():
        try:
            data = json.loads((provider_dir / "_sidebar.json").read_text())
            if not isinstance(data, dict):
                data = {}
            sidebar_obj = data.get("sidebar") or {}
            # Per-provider sidebar shape: chatgpt {items: [{id, ...}]},
            # gemini {items: [{conv_id, title}]} (DOM scrape),
            # claude {data: [{uuid, ...}]} or similar.
            items = []
            if isinstance(sidebar_obj, dict):
                items = (
                    sidebar_obj.get("items")
                    or sidebar_obj.get("data")
                    or sidebar_obj.get("chat_conversations")
                    or []
                )
            elif isinstance(sidebar_obj, list):
                items = sidebar_obj
            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = item.get("conv_id") or item.get("id") or item.get("uuid")
                if cid:
                    sidebar_ids.add(str(cid))
        except (json.JSONDecodeError, OSError):
            pass  # Treat unreadable sidebar as empty — pill stays hidden

    on_disk_ids: set[str] = set()
    if provider_dir.is_dir():
        for path in provider_dir.iterdir():
            name = path.name
            if name.startswith("_"):
                continue  # _sidebar.json sentinel
            if not name.endswith(".json"):
                continue
            stem = name[:-5]  # strip .json
            if stem.endswith(".stream"):
                stem = stem[:-7]
            stem = stem.split("__", 1)[0]  # strip gemini's __<msg_id> suffix
            if stem:
                on_disk_ids.add(stem)

    # claude.ai canonical URLs need the org_id (which isn't in conv_ids).
    # The _sidebar.json's stored URL has it — extract once here so the
    # pill can construct canonical URLs without a second roundtrip.
    org_id: str | None = None
    if provider == "claude" and (provider_dir / "_sidebar.json").exists():
        try:
            sidebar_url = json.loads((provider_dir / "_sidebar.json").read_text()).get("url", "")
            m = re.search(r"/api/organizations/([a-f0-9-]{16,})/", sidebar_url)
            if m:
                org_id = m.group(1)
        except (json.JSONDecodeError, OSError):
            pass

    missing = sorted(sidebar_ids - on_disk_ids)
    result = {
        "ok": True,
        "provider": provider,
        "sidebar_count": len(sidebar_ids),
        "on_disk_count": len(on_disk_ids),
        "missing_count": len(missing),
        "missing_ids": missing[:50],  # cap so payload stays small
    }
    if org_id:
        result["org_id"] = org_id
    return result


def _query_launchpad_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the launchpad page-data dict so the in-extension launchpad can
    render LIVE over Native Messaging instead of reading a generated
    `launchpad.html` file (which a plugin-only / no-`serve` user never gets).

    Read-only: assembles the SAME data `write_portal_html` bakes into the page,
    from ~/.trinity only — no subprocess, no model call, no Hub. Passes through
    the host's existing query path (the read-only tier; no allowlist subprocess,
    so no new attack surface beyond reading state this host already reads).

    The result is JSON-serialized straight to the extension, so it must stay
    under Chrome's 1 MB host→extension cap. `build_launchpad_payload`'s docstring
    + the capture-host test pin a 900 KB budget; if a pathological corpus ever
    breaches it, fail SOFT with a typed error the page can surface (open the
    file:// launchpad) rather than letting Chrome silently drop an oversized
    frame.
    """
    from .launchpad_page import build_launchpad_payload_cached

    # mtime-gated cache: a repeated panel open is a file read, not a ~6.5s full
    # analytics rebuild over the whole corpus (founder-caught side-panel latency,
    # 2026-06-17). Auto-invalidates when a council runs / the lens rebuilds.
    data = build_launchpad_payload_cached()
    encoded = json.dumps(data, default=str)
    if len(encoded.encode("utf-8")) > _MAX_LAUNCHPAD_BYTES:
        return {
            "ok": False,
            "error": "launchpad_payload_too_large",
            "bytes": len(encoded.encode("utf-8")),
        }
    return {"ok": True, **data}


# ── Council-page data queries (the side-panel council page) ──────────────────
# The sandboxed side-panel council page (chrome-extension://, opaque origin) can't
# read the ~/.trinity .js files its file:// twin loads via <script> injection. So
# it asks the host for the SAME data over the bridge. To guarantee the bytes match
# what the <script>-injection path would have set, these read the EXISTING .js
# files the runner already writes and extract their JSON object verbatim — no
# re-derivation, so no shape drift between the two transports.
import re as _re

_JS_ASSIGN_RX = _re.compile(r"window\.__TRINITY_[A-Z_]+__(?:\[[^\]]*\])?\s*=\s*\{")


def _extract_js_object(text: str) -> dict[str, Any] | None:
    """Pull the last `window.__TRINITY_X__[...] = { ... };` object out of a Trinity
    data .js file (string-aware brace matching, so braces inside string values
    don't fool it)."""
    matches = list(_JS_ASSIGN_RX.finditer(text))
    if not matches:
        return None
    start = text.index("{", matches[-1].start())
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    return None


def _read_js_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": True, "result": None}
    obj = _extract_js_object(path.read_text(encoding="utf-8"))
    return {"ok": True, "result": obj}


def _query_council_status(payload: dict[str, Any]) -> dict[str, Any]:
    token = payload.get("status_token") or payload.get("status-token")
    if not isinstance(token, str) or not _SAFE_ID_RX.match(token):
        return {"ok": False, "error": "invalid status_token"}
    # Route through load_council_status — NOT a raw .js read — so a DEAD+STALE
    # "running" council (runner crashed / killed / pid reused) is coerced to
    # "failed" before it reaches the SIDE PANEL's live-council poller. The raw
    # read (_read_js_object on the .js) returns the on-disk "running" verbatim,
    # so the side panel painted "Council running" with an infinite spinner
    # FOREVER on a council whose runner had exited — the host-RPC sibling of the
    # already-coerced ACTION path (_read_council_status) + the launchpad_data
    # scan. _coerce_stale_running_status (pid-liveness AND status-freshness) is
    # the one place that catches it; bypassing it on the panel's read-path is
    # why the panel never showed the terminal "Council failed — runner exited"
    # card. load_council_status reads the sibling .json (written together with
    # the .js by _write_status) and applies the coercion + shape guards.
    try:
        from .council_status import load_council_status

        status = load_council_status(token)
    except Exception as exc:  # noqa: BLE001 — host queries must never crash the host
        return {
            "ok": False,
            "error": "query_failed: "
            + _safe_error(exc, fallback="couldn't read this council's status"),
        }
    return {"ok": True, "result": status}


def _query_council_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    cid = payload.get("council_id")
    if not isinstance(cid, str) or not _SAFE_ID_RX.match(cid):
        return {"ok": False, "error": "invalid council_id"}
    from .state_paths import council_outcomes_dir
    return _read_js_object(council_outcomes_dir() / f"{cid}.js")


def _query_thread_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    tid = payload.get("thread_id")
    if not isinstance(tid, str) or not _SAFE_ID_RX.match(tid):
        return {"ok": False, "error": "invalid thread_id"}
    from .state_paths import council_outcomes_dir
    return _read_js_object(council_outcomes_dir() / f"_thread_{tid}.js")


def _query_active_council(payload: dict[str, Any]) -> dict[str, Any]:
    from .state_paths import review_pages_dir
    return _read_js_object(review_pages_dir() / "_active_council.js")


QUERY_HANDLERS: dict[str, Any] = {
    "ping": _query_ping,
    "sync_status": _query_sync_status,
    "launchpad_data": _query_launchpad_data,
    "council_status": _query_council_status,
    "council_outcome": _query_council_outcome,
    "thread_manifest": _query_thread_manifest,
    "active_council": _query_active_council,
}


def _is_query_message(msg: dict[str, Any]) -> bool:
    return msg.get("kind") == "query" and msg.get("query_kind") in QUERY_KINDS


def _run_query(msg: dict[str, Any]) -> dict[str, Any]:
    query_kind = msg.get("query_kind")
    handler = QUERY_HANDLERS.get(query_kind)
    if not handler:
        return {"ok": False, "error": f"unknown_query_kind: {query_kind}"}
    try:
        return handler(msg)
    except Exception as e:  # noqa: BLE001 — query errors must never crash the host
        # type name is allowed (TYPE-only honest, #43102d25) but the str(e)
        # tail can carry an absolute path / [Errno N] — sanitize it.
        return {
            "ok": False,
            "error": f"query_failed: {type(e).__name__}: "
                     + _safe_error(e, fallback="couldn't read local state"),
        }


def main() -> int:
    while True:
        try:
            msg = _read_message()
        except Exception as e:
            _write_message({
                "ok": False,
                "error": "read_failed: " + _safe_error(
                    e, fallback="couldn't read the request"
                ),
            })
            return 1
        if msg is None:
            return 0
        # Three message paths: read-only queries (no side effects), action
        # dispatches (CLI subprocesses via ACTION_ALLOWLIST), and captures
        # (write files via _extract_target + _write_capture). One host
        # process, three distinct paths.
        if _is_query_message(msg):
            _write_message(_run_query(msg))
            continue
        if _is_action_message(msg):
            _write_message(_run_action(msg))
            continue
        extracted = _extract_target(msg)
        if extracted is None:
            _write_message({"ok": False, "error": "unrecognized_payload"})
            continue
        provider, conv_id, conversation = extracted
        try:
            target = _write_capture(provider, conv_id, conversation)
            _write_message({"ok": True, "path": str(target), "provider": provider, "conv_id": conv_id})
        except Exception as e:
            _write_message({
                "ok": False,
                "error": "write_failed: " + _safe_error(
                    e, fallback="couldn't save the capture"
                ),
            })


if __name__ == "__main__":
    sys.exit(main())
