"""Handler for `trinity-local extension repair` — Trinity heals itself.

When a frontier provider (chatgpt.com / claude.ai / gemini.google.com)
silently moves a streaming endpoint (e.g., ``/backend-api/conversation``
→ ``/backend-api/f/conversation`` as observed 2026-05), the Chrome
extension's ``page-hook.js`` keeps installing fine — but
``classifyRequest()`` returns ``null`` and captures stop. This command
is the diagnose + dispatch-a-council loop that:

1. **Diagnoses** zero-capture intervals: per provider, counts files in
   ``~/.trinity/conversations/<slug>/`` and reports most-recent mtime.
2. **Investigates** when given a ``--har`` file: parses standard HAR
   1.2 JSON, extracts POSTs to known chat domains.
3. **Repairs** by dispatching a council (Claude + Codex + Antigravity)
   with the current ``page-hook.js`` source + the observed POSTs, asking
   each member to propose a patch. Chairman synthesizes one diff.

The structural pitch: only Trinity has the council + the local code +
the cross-provider signal — so only Trinity can heal itself in this
shape. No hosted service does this; the labs themselves are
commercially prevented from recommending each other.

CLI surface (intentionally narrow):
  trinity-local extension repair                  # diagnose only
  trinity-local extension repair --har <path>     # diagnose + repair

The ``--apply`` flag is NOT wired in MVP — the patch is printed and
the user applies + reloads the extension manually. Wiring auto-apply
needs a signing/trust step we haven't designed yet.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from ..registry import CAPTURE_PROVIDERS
from ..state_paths import conversations_dir
from ..utils import now_iso, stable_id

# Domains the page-hook claims to capture from. Used for HAR filtering.
_CHAT_DOMAINS = {
    "claude": ("claude.ai",),
    "chatgpt": ("chatgpt.com", "chat.openai.com"),
    "gemini": ("gemini.google.com",),
}

# Path to the canonical page-hook source. Repair command reads this
# verbatim into the council prompt so members see the actual current
# state, not a paraphrase.
_PAGE_HOOK_PATH = Path(__file__).resolve().parents[3] / "browser-extension" / "page-hook.js"


def register(subparsers):
    sp = subparsers.add_parser(
        "extension",
        help="Inspect or repair the Chrome extension (capture pipeline)",
    )
    extension_sub = sp.add_subparsers(dest="extension_command", required=True)

    repair_sp = extension_sub.add_parser(
        "repair",
        help="Diagnose capture failures and (with --har) dispatch a council to propose a page-hook.js patch",
    )
    repair_sp.add_argument(
        "--har",
        type=Path,
        default=None,
        help="Path to a HAR 1.2 JSON export from Chrome DevTools. With this, dispatch a council to propose a patch.",
    )
    repair_sp.add_argument(
        "--provider",
        choices=list(CAPTURE_PROVIDERS),
        default=None,
        help="Restrict HAR analysis to a single provider's domains (default: all three).",
    )
    repair_sp.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Output the diagnostic + council result as JSON instead of human-readable.",
    )
    repair_sp.add_argument(
        "--auto", dest="auto", action="store_true",
        help=("Self-healing mode (#147): when detect_failure_patterns finds "
              "a code-patch pattern, dispatch the repair council with the "
              "diagnostic + page-hook.js source (no HAR needed). User-action "
              "patterns (e.g. stale-auth-cookie) print the hint and skip "
              "council dispatch — those fixes are on the user's side."),
    )
    repair_sp.set_defaults(handler=handle_repair)


def handle_repair(args):
    diag = diagnose()

    # Self-healing path (#147): when --auto is set, look for code-patch
    # patterns and dispatch the council without requiring HAR. Falls
    # through to the diagnose-only print if no actionable patterns are
    # found OR only user-action patterns surface.
    if getattr(args, "auto", False) and args.har is None:
        patterns = detect_failure_patterns(diag)
        code_patches = [p for p in patterns if p.get("fix_kind") == "code-patch"]
        if not code_patches:
            user_actions = [p for p in patterns if p.get("fix_kind") == "user-action"]
            _print_diagnose(diag, as_json=getattr(args, "as_json", False))
            if user_actions:
                print()
                print(
                    "→ --auto found no code-patch patterns. The "
                    f"{len(user_actions)} user-action pattern(s) above need "
                    "manual fixes (refresh auth, restart browser, etc.) "
                    "before Trinity-side changes would help."
                )
            else:
                print()
                print(
                    "→ --auto found no actionable patterns. Capture pipeline "
                    "looks healthy. If you're seeing a specific failure, "
                    "re-run with `--har <file>` to dispatch the council "
                    "with concrete network data."
                )
            return

        page_hook_source = _PAGE_HOOK_PATH.read_text()
        bundle = build_auto_repair_bundle(
            diag=diag, patterns=patterns, page_hook_source=page_hook_source,
        )

        if getattr(args, "as_json", False):
            print(json.dumps({
                "diagnosis": diag,
                "patterns": patterns,
                "council_prompt_preview": (
                    bundle.task_text[:500] + ("..." if len(bundle.task_text) > 500 else "")
                ),
                "bundle_id": bundle.bundle_id,
            }, indent=2))
            return

        _print_diagnose(diag, as_json=False)
        print()
        print(
            f"→ {len(code_patches)} code-patch pattern(s) detected. "
            "Dispatching self-healing council (no HAR required)…"
        )
        print(f"  bundle_id: {bundle.bundle_id}")
        result = dispatch_repair_council(bundle)
        if result is None:
            print(
                "✗ Council dispatch skipped — no providers wired in this install. "
                "Run `trinity-local install-mcp` first.",
                file=sys.stderr,
            )
            raise SystemExit(2)

        print()
        print("=" * 78)
        print("Chairman's proposed patch (auto-repair, no HAR)")
        print("=" * 78)
        print(result)
        return

    if args.har is None:
        _print_diagnose(diag, as_json=getattr(args, "as_json", False))
        return

    if not args.har.exists():
        print(f"✗ HAR file not found: {args.har}", file=sys.stderr)
        raise SystemExit(2)

    try:
        har_data = json.loads(args.har.read_text())
    except json.JSONDecodeError as exc:
        print(f"✗ HAR is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2)

    posts = extract_chat_posts(har_data, provider=args.provider)
    if not posts:
        print(
            "✗ No chat-domain POSTs found in the HAR. "
            "Open the target chat tab, reproduce the broken flow with DevTools open, "
            "then right-click in Network → Save all as HAR with content.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    page_hook_source = _PAGE_HOOK_PATH.read_text()
    bundle = build_repair_bundle(diag=diag, har_posts=posts, page_hook_source=page_hook_source)

    if getattr(args, "as_json", False):
        # MVP: dump the bundle (which carries the prompt). Real council
        # dispatch is the next step — gated behind interactive confirm.
        print(json.dumps({
            "diagnosis": diag,
            "har_posts": posts,
            "council_prompt_preview": bundle.task_text[:500] + ("..." if len(bundle.task_text) > 500 else ""),
            "bundle_id": bundle.bundle_id,
        }, indent=2))
        return

    _print_diagnose(diag, as_json=False)
    print()
    print(f"→ {len(posts)} chat-domain POSTs found in HAR")
    for p in posts[:10]:
        print(f"  · {p['method']} {p['url']}  (status {p['status']})")
    if len(posts) > 10:
        print(f"  · ... and {len(posts) - 10} more")
    print()
    print("Dispatching council (Claude + Codex + Antigravity) with the HAR + page-hook.js source…")
    print(f"  bundle_id: {bundle.bundle_id}")

    result = dispatch_repair_council(bundle)
    if result is None:
        print(
            "✗ Council dispatch skipped — no providers wired in this install. "
            "Run `trinity-local install-mcp` first.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    print()
    print("=" * 78)
    print("Chairman's proposed patch")
    print("=" * 78)
    print(result)


def diagnose() -> dict[str, Any]:
    """Per-provider capture summary: file count, most-recent mtime,
    hours-since-last-capture. The diagnostic signal the repair flow
    needs: if hours_since_last is large but the user reports active
    use, the page-hook's classifyRequest() probably stopped matching.
    """
    home = conversations_dir()
    summary: dict[str, Any] = {"generated_at": now_iso(), "providers": {}}
    now = time.time()
    # Count canonical conversations via the SHARED enumerator so this
    # "Captures: N" surface (CLI `status`) agrees with the launchpad cockpit
    # card AND the CLI doctor — all three read ONE filter. iter_capture_files()
    # is the single source of truth for "which files count as a captured
    # conversation": it dedups the `<conv_id>.stream.json` sidecar against the
    # canonical `<conv_id>.json` for claude/chatgpt AND collapses gemini's
    # per-SSE-frame `<conv_id>__<ts>.stream.json` explosion (one real gemini
    # conversation writes dozens of frame-files) down to ONE per conversation.
    # An open-coded per-file glob here (skip `.`/`_`/`stream-` only) used to
    # MISS both, so `status` over-reported captures ~17× on a gemini-heavy
    # install (raw frames) and double-counted every claude/chatgpt thread
    # (.json + .stream.json) — while the launchpad card showed the deduped
    # truth from the same dir. Sourcing the count + the latest mtime from
    # iter_capture_files() (which keeps the LATEST gemini frame per conv, so
    # hours_since_last keeps its resolution) ends the drift at the root.
    from ..capture_host import iter_capture_files

    per_provider: dict[str, list] = {}
    for f in iter_capture_files():
        per_provider.setdefault(f.parent.name, []).append(f)

    for slug in CAPTURE_PROVIDERS:
        provider_dir = home / slug
        if not provider_dir.exists():
            summary["providers"][slug] = {
                "exists": False,
                "captures": 0,
                "last_capture": None,
                "hours_since_last": None,
            }
            continue
        files = per_provider.get(slug, [])
        if not files:
            summary["providers"][slug] = {
                "exists": True,
                "captures": 0,
                "last_capture": None,
                "hours_since_last": None,
            }
            continue
        try:
            latest_mtime = max(p.stat().st_mtime for p in files)
        except OSError:
            summary["providers"][slug] = {
                "exists": True,
                "captures": len(files),
                "last_capture": None,
                "hours_since_last": None,
            }
            continue
        summary["providers"][slug] = {
            "exists": True,
            "captures": len(files),
            "last_capture": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(latest_mtime)),
            # Clamp to 0: a FUTURE mtime (clock skew, a restored/rsync'd file with a
            # preserved future timestamp) must not render "-3.0h ago" in the status
            # CLI capture line — a future capture reads as "just captured" (0h), the
            # same future->now convention `_humanize_ago` enforces on the launchpad.
            "hours_since_last": round(max(0.0, now - latest_mtime) / 3600.0, 1),
        }
    return summary


def extract_chat_posts(har_data: dict[str, Any], *, provider: str | None = None) -> list[dict[str, Any]]:
    """Pull POST entries to chat-provider domains out of a HAR 1.2 file.
    Returns a list of ``{provider, method, url, status, content_type, has_request_body}``
    dicts. POSTs to known telemetry endpoints (``/ces/``, ``/sentinel/``)
    are filtered out — they aren't the streaming conversation calls and
    just add noise to the council prompt.
    """
    entries = har_data.get("log", {}).get("entries", [])
    out: list[dict[str, Any]] = []
    if provider:
        domains = set(_CHAT_DOMAINS.get(provider, ()))
    else:
        domains = {d for v in _CHAT_DOMAINS.values() for d in v}

    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        method = request.get("method", "")
        url = request.get("url", "")
        if method != "POST":
            continue
        # Match by host. URLs come from HAR as fully qualified.
        if not any(("://" + d) in url or ("//" + d) in url for d in domains):
            continue
        # Drop noise: telemetry, sentinel, anti-bot pings, statsc.
        if any(noise in url for noise in ("/ces/", "/sentinel/", "/statsc/", "/lat/")):
            continue
        content_type = ""
        for h in response.get("headers", []) or []:
            if (h.get("name") or "").lower() == "content-type":
                content_type = h.get("value", "")
                break
        prov_slug = next(
            (slug for slug, doms in _CHAT_DOMAINS.items() if any(d in url for d in doms)),
            "unknown",
        )
        out.append({
            "provider": prov_slug,
            "method": method,
            "url": url,
            "status": response.get("status", 0),
            "content_type": content_type,
            "has_request_body": bool((request.get("postData") or {}).get("text")),
        })
    return out


def build_repair_bundle(*, diag: dict[str, Any], har_posts: list[dict[str, Any]], page_hook_source: str):
    """Construct the PromptBundle the council reads. Kept in a separate
    function so tests can assert on the prompt shape without dispatching
    real LLM calls.
    """
    from ..council_schema import PromptBundle

    bundle_id = stable_id("extrepair", now_iso(), str(len(har_posts)))
    task_text = (
        "Trinity's Chrome extension page-hook.js intercepts window.fetch on chat sites "
        "(claude.ai, chatgpt.com, gemini.google.com) and classifies streaming requests "
        "via the PROVIDER_PATTERNS list. Captures recently stopped for one or more "
        "providers — see DIAGNOSIS below. A HAR export from DevTools shows the actual "
        "POSTs the page made — see HAR_POSTS. Your task: identify which PROVIDER_PATTERNS "
        "entries no longer match real traffic and propose a minimal patch (added "
        "entries, regex relaxation, or content-type gating). Return the patch as a "
        "unified diff against page-hook.js. Keep legacy entries — older accounts may "
        "still be on prior rollouts. Do NOT propose architectural changes (e.g., "
        "MutationObserver-based capture) in this patch — keep the fix narrow.\n\n"
        f"DIAGNOSIS:\n{json.dumps(diag, indent=2)}\n\n"
        f"HAR_POSTS ({len(har_posts)} entries, filtered to chat domains, telemetry stripped):\n"
        f"{json.dumps(har_posts[:50], indent=2)}\n\n"
        "CURRENT page-hook.js:\n```js\n"
        f"{page_hook_source}\n"
        "```"
    )
    return PromptBundle(
        bundle_id=bundle_id,
        task_cluster_id=stable_id("extrepair_cluster"),
        task_text=task_text,
        goal="Propose a minimal page-hook.js patch (unified diff) that restores capture for any provider whose streamPath no longer matches real traffic. Preserve legacy patterns. No architectural changes.",
        context_excerpt="",
        created_at=now_iso(),
        metadata={"kind": "extension_repair", "providers_diagnosed": list(diag.get("providers", {}).keys())},
    )


def build_auto_repair_bundle(
    *,
    diag: dict[str, Any],
    patterns: list[dict[str, str]],
    page_hook_source: str,
):
    """Self-healing bundle (#147) — same structure as build_repair_bundle
    but uses detected drift patterns in lieu of HAR data.

    Caller filters ``patterns`` to fix_kind=="code-patch" before this
    runs (user-action patterns shouldn't dispatch the council). The
    council reads page-hook.js + the diagnosis + the pattern hints
    and proposes a minimal patch.
    """
    from ..council_schema import PromptBundle

    code_patterns = [p for p in patterns if p.get("fix_kind") == "code-patch"]
    bundle_id = stable_id("extautorepair", now_iso(), str(len(code_patterns)))
    task_text = (
        "Trinity's Chrome extension page-hook.js intercepts window.fetch on chat sites "
        "(claude.ai, chatgpt.com, gemini.google.com) and classifies streaming requests "
        "via the PROVIDER_PATTERNS list. Captures have stopped for one or more providers "
        "AND the silence is past the stale-auth-cookie band — see DIAGNOSIS + "
        "DETECTED_PATTERNS below. NO HAR was captured this run; you're working from "
        "the diagnostic signal + page-hook source alone (#147 self-healing path). "
        "Identify which PROVIDER_PATTERNS entries likely no longer match real traffic "
        "based on the pattern hints, and propose a minimal patch (added entries, regex "
        "relaxation, or content-type gating). Return the patch as a unified diff "
        "against page-hook.js. Keep legacy entries — older accounts may still be on "
        "prior rollouts. Do NOT propose architectural changes (e.g., "
        "MutationObserver-based capture) in this patch — keep the fix narrow. If the "
        "diagnostic alone doesn't give enough signal to propose a confident patch, "
        "say so and recommend the user re-run with --har <file>.\n\n"
        f"DIAGNOSIS:\n{json.dumps(diag, indent=2)}\n\n"
        f"DETECTED_PATTERNS ({len(code_patterns)} code-patch pattern(s)):\n"
        f"{json.dumps(code_patterns, indent=2)}\n\n"
        "CURRENT page-hook.js:\n```js\n"
        f"{page_hook_source}\n"
        "```"
    )
    return PromptBundle(
        bundle_id=bundle_id,
        task_cluster_id=stable_id("extautorepair_cluster"),
        task_text=task_text,
        goal=(
            "Propose a minimal page-hook.js patch (unified diff) that restores capture "
            "for any provider whose PROVIDER_PATTERNS entry no longer matches. Preserve "
            "legacy patterns. No architectural changes. If signal is too thin, say so."
        ),
        context_excerpt="",
        created_at=now_iso(),
        metadata={
            "kind": "extension_repair_auto",
            "providers_diagnosed": list(diag.get("providers", {}).keys()),
            "patterns_count": len(code_patterns),
        },
    )


def dispatch_repair_council(bundle) -> str | None:
    """Run the council and return the chairman's synthesized patch text.
    Returns None if no providers are wired (caller surfaces an error).

    Uses Trinity's own installed_council_providers() so the lineup
    matches whatever the user has set up via install-mcp.
    """
    from ..config import installed_council_providers, load_config
    from ..council_runner import run_council

    config = load_config(None, required=True)
    members = installed_council_providers(None)
    if not members:
        return None
    # Primary defaults to the first installed provider (typically claude).
    primary = members[0]
    result = run_council(
        config=config,
        bundle=bundle,
        member_providers=members,
        primary_provider=primary,
        cwd=Path.cwd(),
    )
    return result.outcome.synthesis_output or "(chairman produced no synthesis text — check council_outcomes/)"


# Threshold for "this provider was working but suddenly stopped." Below
# 24h we still consider captures fresh. Above 168h (7 days) we can't
# distinguish stale-auth-cookie from "user simply hasn't used this
# provider in a while" — so we only flag the recoverable pattern in the
# middle band.
STALE_RECOVERABLE_LOW_HOURS = 24
STALE_RECOVERABLE_HIGH_HOURS = 168


def detect_failure_patterns(diag: dict[str, Any]) -> list[dict[str, str]]:
    """Surface known-recoverable failure patterns from a diagnose() dict.

    Each pattern returns ``{provider, pattern, fix_kind, hint,
    fix_command}``. ``fix_kind`` is one of:
    - ``"user-action"``: user runs a manual step (no Trinity code change
      will help). E.g., refresh auth cookies.
    - ``"code-patch"``: Trinity's own code likely drifted out of sync
      with provider site changes. ``extension repair --auto`` will
      dispatch a council to propose a patch automatically.

    Tagging fix_kind so ``--auto`` only dispatches the council on
    code-patch patterns; user-action patterns don't benefit from
    council deliberation (the fix is on the user's side).

    Currently detected:
    - **stale-auth-cookie** (#150): user-action. Provider has prior
      captures but hasn't fired in 24h–168h. Auth cookie likely
      expired — page loads but authed fetches fail before page-hook
      sees them. Recovery: log out + log back in.
    - **provider-extended-silence** (#147): code-patch. Provider has
      prior captures but hasn't fired in 168h+ AND the prior captures
      were within recent memory (>5 entries — proves the provider was
      regularly used). Beyond the stale-cookie band, the dominant
      cause is page-hook PROVIDER_PATTERNS drift — provider changed
      its streaming-endpoint shape and our regex no longer matches.
      ``--auto`` dispatches the repair council with the pattern hint
      (no HAR needed); the council reads page-hook.js + the diagnosis
      and proposes a minimal patch.
    """
    patterns: list[dict[str, str]] = []
    for slug, info in diag.get("providers", {}).items():
        if not info.get("exists") or info.get("captures", 0) == 0:
            continue
        h = info.get("hours_since_last")
        if h is None:
            continue
        captures = info.get("captures", 0)
        if STALE_RECOVERABLE_LOW_HOURS <= h <= STALE_RECOVERABLE_HIGH_HOURS:
            patterns.append({
                "provider": slug,
                # Display-ready login domain (claude.ai / chatgpt.com /
                # gemini.google.com). Capture slugs are web-era; surfacing the
                # raw slug in a user-facing hint leaks an internal token AND is
                # less actionable than the exact site to re-login to — the
                # fix_command below already uses this. Consumed by the launchpad
                # memory-health card (launchpad_data._memory_health).
                "provider_url": _provider_url(slug),
                "pattern": "stale-auth-cookie",
                "fix_kind": "user-action",
                "hint": (
                    f"{slug} hasn't captured in {h}h despite having "
                    f"{captures} prior captures. The dominant cause "
                    f"is an expired auth cookie — the provider page loads "
                    f"but its bundle's authed fetches fail before reaching "
                    f"page-hook's interceptor."
                ),
                "fix_command": (
                    f"Log out of {_provider_url(slug)}, log back in, refresh "
                    f"the page, send a test message. If captures still don't "
                    f"resume, fall through to `trinity-local extension repair "
                    f"--har <file>`."
                ),
            })
        elif h > STALE_RECOVERABLE_HIGH_HOURS and captures >= 5:
            # Beyond the stale-cookie band AND there's enough history to
            # rule out "user just doesn't use this provider." The
            # dominant cause is code-side drift in page-hook patterns —
            # the council can propose a fix without HAR by reading
            # page-hook.js + the diagnosis.
            patterns.append({
                "provider": slug,
                "provider_url": _provider_url(slug),  # display-ready domain
                "pattern": "provider-extended-silence",
                "fix_kind": "code-patch",
                "hint": (
                    f"{slug} has {captures} prior captures but hasn't "
                    f"fired in {h}h (>{STALE_RECOVERABLE_HIGH_HOURS}h). "
                    f"Beyond the stale-cookie band; likely a "
                    f"PROVIDER_PATTERNS regex no longer matches the "
                    f"provider's current streaming endpoint."
                ),
                "fix_command": (
                    "Run `trinity-local extension repair --auto` to "
                    "dispatch a council that proposes a page-hook.js "
                    "patch from the diagnosis + source (no HAR needed)."
                ),
            })
    return patterns


def _provider_url(slug: str) -> str:
    return {
        "claude": "claude.ai",
        "chatgpt": "chatgpt.com",
        "gemini": "gemini.google.com",
    }.get(slug, slug)


def _print_diagnose(diag: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        out = dict(diag)
        out["recoverable_patterns"] = detect_failure_patterns(diag)
        print(json.dumps(out, indent=2))
        return
    print("Chrome extension capture diagnosis")
    print("-" * 40)
    for slug, info in diag["providers"].items():
        if not info["exists"]:
            print(f"  {slug:10s}  (no directory — no captures ever)")
            continue
        if info["captures"] == 0:
            print(f"  {slug:10s}  0 captures (directory exists but empty)")
            continue
        h = info["hours_since_last"]
        warn = " ⚠ stale" if h is not None and h > 24 else ""
        print(f"  {slug:10s}  {info['captures']} captures, last {info['last_capture']} ({h}h ago){warn}")
    print()

    # #150: surface recoverable patterns BEFORE the HAR ask. Don't make
    # users export HARs for issues that an auth-refresh fixes.
    patterns = detect_failure_patterns(diag)
    if patterns:
        print("Likely-recoverable patterns detected:")
        print("-" * 40)
        for p in patterns:
            print(f"  [{p['pattern']}] {p['provider']}")
            print(f"    {p['hint']}")
            print(f"    Try first: {p['fix_command']}")
            print()

    print("Repair: open the affected chat in Chrome, DevTools → Network, reproduce the broken flow,")
    print("        right-click → 'Save all as HAR with content', then re-run:")
    print("          trinity-local extension repair --har <path>")
