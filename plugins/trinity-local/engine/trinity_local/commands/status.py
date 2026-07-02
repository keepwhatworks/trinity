"""Handler for the status command — one-shot system summary + health check.

`status` absorbed the role of the (former) `doctor` command pre-launch: it
runs the same provider / MCP-dep / dir-writable pre-flight checks via
`trinity_local.health_checks.run_doctor()` and prints a one-line health verdict
at the top of the human-readable output. JSON callers get the full report
under `"health"`. The standalone `doctor` CLI was retired in favor of this
single "tell me about Trinity" surface.
"""
from __future__ import annotations

import json

from ..adapters import check_all_adapters
from ..health_checks import format_one_line, run_doctor
from ..drift import check_drift
from ..state_paths import state_dir, tasks_dir


def register(subparsers):
    parser = subparsers.add_parser("status", help="Show Trinity system status summary + health check")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=handle_status)


def _count_files(directory, pattern="*.json"):
    """Count files matching a glob in a directory."""
    try:
        return sum(1 for _ in directory.glob(pattern))
    except (OSError, FileNotFoundError):
        return 0


def _topics_summary(path) -> str:
    """Compact summary appended to the topics.json status row:
    "  · 20 basins · 18,184 turns across 12,041 threads".

    Returns empty on any failure — status output never crashes because
    of a malformed memory file (per claude.md "Analytics never crash").
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        basins = payload.get("basins") or []
        if not basins:
            return ""
        total_turns = sum(int(b.get("size") or 0) for b in basins)
        thread_counts = [int(b.get("thread_count") or 0) for b in basins]
        if not any(thread_counts):
            # Legacy per-turn topics.json — emit a hint to refresh.
            return f"  · {len(basins)} basins · pre-thread-aware (run `trinity-local lens`)"
        total_threads = sum(thread_counts)
        return (
            f"  · {len(basins)} basins · {total_turns:,} turns"
            f" across {total_threads:,} threads"
        )
    except Exception:
        # Honor the docstring promise ("never crashes because of a malformed
        # memory file"): a wrong-TYPE-but-parseable topics.json (a list/str)
        # raises AttributeError on `.get`, which the old narrow tuple
        # (OSError/ValueError/KeyError/TypeError) did NOT catch → status crashed.
        return ""


def handle_status(args):
    # Health (absorbed from former `doctor` command).
    health = run_doctor()

    # Adapters
    adapters = check_all_adapters()
    ready_adapters = [a for a in adapters if a.installed]
    total_transcripts = sum(a.transcript_count for a in adapters)

    # Tasks
    task_count = _count_files(tasks_dir())

    # (The action store retired 2026-07-02, #332 — councils stopped writing
    # per-run "review_ready" records whose completion path died 2026-05-18;
    # the 18k-file scan this used to do was the slowest part of `status`.)

    # Drift
    drift_alerts = check_drift()

    # State dir
    home = state_dir()

    # Reviews
    reviews_dir = home / "reviews"
    review_count = _count_files(reviews_dir) if reviews_dir.exists() else 0

    # Council outcomes
    council_dir = home / "council_outcomes"
    council_count = _count_files(council_dir) if council_dir.exists() else 0

    if args.as_json:
        # JSON parity with the human surface — agents/scripts parsing
        # status need the same actionable signals + capture counts that
        # the human format renders. Each block wrapped in try/except so
        # a bug in one helper doesn't poison the whole JSON output.
        signals_payload: list[dict] = []
        try:
            from ..me.lens_edits import pending_lens_edits_count

            n_edits = pending_lens_edits_count()
            if n_edits > 0:
                signals_payload.append({
                    "kind": "lens_edits_pending",
                    "count": n_edits,
                    "fix_command": "trinity-local lens",
                })
        except Exception:
            pass
        try:
            # (lens_contradictions — Stage 4b #141 — retired 2026-06-05; the
            # generators pass surfaces cross-domain contradictions semantically.)
            pass
        except Exception:
            pass
        try:
            from .extension_repair import detect_failure_patterns, diagnose

            patterns = detect_failure_patterns(diagnose())
            code = [p for p in patterns if p.get("fix_kind") == "code-patch"]
            user = [p for p in patterns if p.get("fix_kind") == "user-action"]
            if code:
                signals_payload.append({
                    "kind": "capture_drift",
                    "count": len(code),
                    "fix_command": "trinity-local extension repair --auto",
                })
            if user:
                signals_payload.append({
                    "kind": "auth_cookie_stale",
                    "count": len(user),
                    "fix_command": None,
                    "fix_hint": "refresh login + send a test message",
                })
        except Exception:
            pass

        # Browser-extension captures — parallel to the Adapters: side
        # for JSON parity with the human surface. Each provider entry
        # is augmented with sidebar_sync (sidebar_count / on_disk_count
        # / missing_count) so JSON consumers see the same "unsynced
        # threads" signal the human Captures: section surfaces inline.
        captures_payload: dict | None = None
        try:
            from .extension_repair import diagnose as _diag
            from ..capture_host import _query_sync_status

            cap = _diag()
            providers = cap.get("providers", {})
            if any(info.get("exists") for info in providers.values()):
                # Augment each provider entry with sidebar-sync diff
                # when the provider has captures. Skip the lookup for
                # providers with 0 captures (nothing to diff against).
                augmented = {}
                for slug, info in providers.items():
                    entry = dict(info)
                    if info.get("captures", 0) > 0:
                        sync_state = _query_sync_status({"provider": slug})
                        if sync_state.get("ok"):
                            entry["sidebar_sync"] = {
                                "sidebar_count": sync_state.get("sidebar_count", 0),
                                "on_disk_count": sync_state.get("on_disk_count", 0),
                                "missing_count": sync_state.get("missing_count", 0),
                            }
                    augmented[slug] = entry
                captures_payload = {
                    "total": sum(p.get("captures", 0) for p in providers.values()),
                    "by_provider": augmented,
                }
        except Exception:
            pass

        from ..migrations import SCHEMA_VERSION, current_schema_version
        _recorded_schema = current_schema_version()

        payload: dict = {
            "trinity_home": str(home),
            # #183: schema version of ~/.trinity/. `recorded` is the marker on
            # disk; `current` is what this binary expects. They match after a
            # normal launch (migrations run at startup); a mismatch means a
            # migration failed — visible here for debugging.
            "schema_version": {"recorded": _recorded_schema, "current": SCHEMA_VERSION},
            "health": health.to_dict(),
            "adapters": {
                "ready": len(ready_adapters),
                "total": len(adapters),
                "total_transcripts": total_transcripts,
                "details": [a.to_dict() for a in adapters],
            },
            # Key matches the on-disk directory (~/.trinity/todos/) AND
            # the human "Todos:" display below. Internal Python name
            # `tasks_dir()` retained for back-compat with the v1.7
            # rename; external surfaces use the post-rename "todos".
            "todos": task_count,
            "reviews": review_count,
            "councils": council_count,
            "drift_alerts": len(drift_alerts),
            # Empty list when no signals fire — parallel to human
            # surface staying silent. Always present in payload so
            # scripts can `len(status["signals"])` without branch.
            "signals": signals_payload,
        }
        if captures_payload is not None:
            payload["captures"] = captures_payload
        # Council value proof (#236) — present only when the ledger clears the
        # headline threshold, so scripts can branch on its presence.
        try:
            from ..personal_routing import council_category_wedge, council_value_proof
            vp = council_value_proof()
            if vp.get("ready"):
                vp = dict(vp)
                vp["category_wedge"] = council_category_wedge()
                payload["council_value"] = vp
        except Exception:
            pass
        print(json.dumps(payload, indent=2))
        return

    # Human-readable output
    print("┌─────────────────────────────────────────┐")
    print("│         Trinity Local — Status           │")
    print("└─────────────────────────────────────────┘")
    print()

    # Health (one-line verdict from doctor checks). Full per-check
    # detail surfaces on health failure via `--json` or by calling
    # run_doctor() / format_human() directly.
    print(f"  Health:    {format_one_line(health)}")
    # Soft-degraded checks PASS (ok=True) but carry a fix — the embedding
    # backend on the SHA-1 TF-IDF fallback is the load-bearing one (#273):
    # the one-line verdict says "all green", and format_human only prints
    # fixes for FAILING checks, so without this the degradation is silent —
    # "all green while embeddings silently degraded". Surface them right under
    # the verdict so a thin lens / keyword-shaped tensions have a visible
    # explanation. Empty on a fully-wired install (no soft check has a fix).
    for c in health.checks:
        if c.ok and c.fix:
            print(f"             ⚠ {c.detail}")
            print(f"               └─ run: {c.fix}")
    # #183: schema version line — flags a failed migration (recorded < current).
    from ..migrations import SCHEMA_VERSION, current_schema_version
    _recorded = current_schema_version()
    if _recorded == SCHEMA_VERSION:
        print(f"  Schema:    v{SCHEMA_VERSION}")
    else:
        print(f"  Schema:    v{_recorded} → v{SCHEMA_VERSION} pending "
              f"(migration will run on next launch)")
    print()

    # Adapters
    print(f"  Adapters:  {len(ready_adapters)}/{len(adapters)} ready, {total_transcripts:,} transcripts total")
    for a in adapters:
        icon = "✅" if a.installed else "❌"
        ver = f" ({a.version})" if a.version else ""
        count = f" · {a.transcript_count:,} files" if a.transcript_root else ""
        print(f"    {icon} {a.provider}{ver}{count}")
    print()

    # Browser captures — distinct from CLI adapters: these come from
    # the Chrome extension capturing claude.ai / chatgpt.com /
    # gemini.google.com sessions into ~/.trinity/conversations/. CLI-
    # only users running `status` had no visibility into this side
    # before — they'd see CLI claude (Claude Code) but not web claude
    # (claude.ai). Surfaces only when at least one provider has
    # captures or its directory exists (keeps clean installs terse).
    try:
        from .extension_repair import diagnose
        from ..capture_host import _query_sync_status

        cap_diag = diagnose()
        provider_rows = cap_diag.get("providers", {})
        any_capture_state = any(
            info.get("exists") for info in provider_rows.values()
        )
        if any_capture_state:
            total_captures = sum(
                info.get("captures", 0) for info in provider_rows.values()
            )
            print(f"  Captures:  {total_captures:,} from Chrome extension (browser side)")
            for slug in ("claude", "chatgpt", "gemini"):
                info = provider_rows.get(slug, {})
                if not info.get("exists"):
                    icon = "·"
                    suffix = "not yet captured"
                elif info.get("captures", 0) == 0:
                    icon = "·"
                    suffix = "0 files (extension installed but no captures yet)"
                else:
                    # Sidebar diff: surfaces "you have unsynced threads"
                    # signal that the in-provider auto-sync pill shows in
                    # the browser. Same data source (_query_sync_status)
                    # so CLI + browser surfaces stay in lockstep.
                    sync_state = _query_sync_status({"provider": slug})
                    missing_suffix = ""
                    if sync_state.get("ok"):
                        missing = sync_state.get("missing_count", 0)
                        if missing > 0:
                            missing_suffix = f" · {missing} missing from sidebar"
                    icon = "✅"
                    h = info.get("hours_since_last")
                    h_str = f"{h}h ago" if h is not None else "unknown when"
                    suffix = f"{info['captures']:,} files · last {h_str}{missing_suffix}"
                print(f"    {icon} {slug:10s} {suffix}")
            print()
    except Exception:
        # Same try/except invariant as the Signals section — capture
        # diagnostic must not break the steady-state status command.
        pass

    # Tasks & Actions
    # Display label matches the on-disk directory (~/.trinity/todos/);
    # internal Python name `tasks_dir()` retained for back-compat.
    print(f"  Todos:     {task_count}")
    print(f"  Reviews:   {review_count}")
    print(f"  Councils:  {council_count}")
    # Council value proof (#236) — the council-first painkiller, in one stat:
    # how often the chairman picked a DIFFERENT model than the user's default
    # (i.e. how often a single-provider user would have shipped the worse
    # answer). Computed from council_outcomes/, no model calls. Stays quiet on
    # a thin ledger (n < threshold).
    try:
        from ..personal_routing import council_category_wedge, council_value_proof
        _brand = {"codex": "GPT", "claude": "Claude", "antigravity": "Gemini"}
        vp = council_value_proof()
        if vp.get("ready"):
            print(f"    Council value: picked a different model than your "
                  f"default {vp['changed_pct']}% of the time "
                  f"(across {vp['comparable']} real contests)")
            split = " · ".join(
                f"{_brand.get(p, p)} {d['pct']}%" for p, d in vp["win_split"].items()
            )
            print(f"                   wins: {split}")
            wedge = council_category_wedge()
            if wedge:
                kinds = ", ".join(
                    f"{w['family']}→{_brand.get(w['leader'], w['leader'])}"
                    for w in wedge[:4]
                )
                print(f"                   by kind: {kinds}")
    except Exception:
        pass
    print()

    # Lens hierarchy + scoreboards. Per v1.7 architectural collapse,
    # the three thinking memories (lens / topics / vocabulary) + core.md
    # are what chairman reads as identity context; picks + routing are
    # operational scoreboards, surfaced separately. Reading paths from
    # state_paths so auto-migration kicks in transparently.
    from ..state_paths import (
        core_path, lens_path, picks_path, routing_path,
        topics_path, vocabulary_path,
    )
    print("  Memories:  (lens hierarchy — chairman identity context)")
    for label, path in [
        ("lens.md       ", lens_path()),
        ("topics.json   ", topics_path()),
        ("vocabulary.md ", vocabulary_path()),
    ]:
        if path.exists():
            size = path.stat().st_size
            extra = _topics_summary(path) if label.strip() == "topics.json" else ""
            print(f"    ✅ {label} {size:>8,} bytes{extra}")
        else:
            print(f"    · {label} not built")
    core = core_path()
    if core.exists():
        # Show whether core is fresh vs stale relative to its source memories.
        from ..distill import is_core_stale
        stale = is_core_stale()
        marker = "⚠️ stale" if stale else "✅ fresh"
        print(f"    {marker} core.md       {core.stat().st_size:>8,} bytes — chairman reads this first")
    else:
        print("    · core.md       not distilled — run `trinity-local dream` (Phase 5 distills core.md)")
    # Lens-build freshness (ground truth: built_at vs live corpus fingerprint —
    # NOT the refresh marker, which false-greened "done" while the lens sat 18
    # days / 677 prompts stale on a real install). A persistent "stale" means the
    # background auto-refresh build isn't LANDING — surface it + the manual escape.
    try:
        from ..cold_start import lens_freshness_status
        fresh, why = lens_freshness_status()
        if fresh == "stale":
            print(f"    ⚠️ lens stale     refresh overdue but not landing — {why}; run `trinity-local lens --force`")
        elif fresh == "current":
            print(f"    ✅ lens current   ({why})")
    except Exception:
        pass
    # Corpus-ingest freshness — DISTINCT from lens freshness above: if the
    # background ingest pass stops firing, even search/ask/k-NN read a stale
    # corpus (missing the latest transcripts), not just the lens (the 2026-06-29
    # 13-day stall: councils chairman-failing + the once-per-process MCP kick).
    try:
        from ..stale_pass import ingest_freshness
        ifresh, iwhy = ingest_freshness()
        if ifresh == "stale":
            print(f"    ⚠️ corpus stale   ingest not landing — {iwhy}; run `trinity-local ingest-recent`")
        elif ifresh == "current":
            print(f"    ✅ corpus fresh   ({iwhy})")
    except Exception:
        pass
    # Life chapters (#252) — datable topic episodes across the corpus timeline.
    # Reads the TRUE `timestamp`, so it shows real history span, not ingest day.
    try:
        from ..me.chapters import corpus_month_span, detect_chapters
        chapters = detect_chapters()
        if chapters:
            span = corpus_month_span()
            print(f"    🕮 Chapters     {len(chapters)} across {span} months:")
            for c in chapters[:3]:
                when = c.start_month if c.months == 1 else f"{c.start_month}→{c.end_month}"
                print(f"        · {c.label} ({when}, peak {c.peak_count:,}p)")
    except Exception:
        pass
    print()
    print("  Scoreboards:  (operational model-selection bookkeeping)")
    for label, path in [
        ("picks.json    ", picks_path()),
        ("routing.json  ", routing_path()),
    ]:
        if path.exists():
            print(f"    ✅ {label} {path.stat().st_size:>8,} bytes")
        else:
            print(f"    · {label} not built")
    print()

    # Drift
    if drift_alerts:
        print(f"  ⚠  {len(drift_alerts)} drift alert(s):")
        for alert in drift_alerts:
            print(f"    · {alert.message}")
    else:
        print("  Drift:     no alerts")
    print()

    # Corpus milestone (fire-once) — celebrate a threshold crossing as the
    # lens deepens. Best-effort: a stat-read failure must never break status.
    try:
        from ..milestones import surface_milestone

        _ms = surface_milestone()
        if _ms is not None:
            print(f"  {_ms.message}")
            print()
    except Exception:
        pass

    # Model news (#218) — a current provider model the user hasn't scored
    # against their taste yet. The celebration nudge lives here (and on the
    # launchpad), not behind a new verb — the surface stays collapsed to
    # lens/council. Best-effort: a manifest/eval read failure must never
    # break status.
    try:
        from ..models import detect_new_models

        new_models = detect_new_models()
        if new_models:
            # Cold-home gate (#316 journey-audit): ev.nudge() says "run eval-run
            # --target X", but eval-run → eval-build raises FileNotFoundError
            # without a rejection ledger, so a fresh user is sent into a failing
            # 3-hop chain. Mirror the launchpad's _eval_set_available() gate: only
            # surface the eval-run CTA when there's an eval set to run; otherwise
            # point at the real prerequisite (build the lens first).
            try:
                from ..launchpad_data import _eval_set_available

                have_eval = _eval_set_available()
            except Exception:
                have_eval = True  # never let the gate itself suppress the news
            print("  New models:")
            for ev in new_models:
                if have_eval:
                    print(f"    {ev.nudge()}")
                else:
                    label = getattr(ev, "display", None) or getattr(ev, "slug", "a new model")
                    print(f"    🎉 {label} is out — build your lens first "
                          f"(`trinity-local lens`), then `trinity-local eval-build` "
                          f"to score it against your taste.")
            # #313: a new model may JUDGE differently — re-validate it doesn't
            # inflate its own family (self-preference) before trusting a same-family
            # eval score. Best-effort; never breaks status.
            try:
                from ..evals.self_preference import unvalidated_models

                unval = unvalidated_models([ev.model for ev in new_models if getattr(ev, "model", None)])
                if unval:
                    print(f"    ↳ self-preference unvalidated for {', '.join(unval)} — "
                          f"run `trinity-local eval-selfpref`")
            except Exception:
                pass
            print()
    except Exception:
        pass

    # #212 cold-start aha: ONE surprising true tension once the lens has
    # signal. Best-effort, self-hides on a cold install.
    try:
        from ..cold_start import cold_open_tension

        co = cold_open_tension()
        if co:
            print(f"  Lens insight: 🪞 {co}")
            print()
    except Exception:
        pass

    # Actionable signals — surfaces the same per-feature counts the
    # launchpad shows so CLI-only users see "you have N edits queued"
    # without opening the file:// surface. Each line silently hidden
    # when its count is 0; the section header shows only when ≥1
    # signal fires so the steady-green state stays terse.
    signals: list[tuple[str, str, str]] = []
    try:
        from ..me.lens_edits import pending_lens_edits_count

        n_edits = pending_lens_edits_count()
        if n_edits > 0:
            signals.append((
                "lens.md edits",
                f"{n_edits} pending",
                "run `trinity-local lens` to fold them in (weight=3.0)",
            ))
    except Exception:
        pass
    # (lens contradictions — Stage 4b #141 — retired 2026-06-05; superseded by the
    # generators pass's semantic contradiction-split.)
    try:
        from .extension_repair import detect_failure_patterns, diagnose

        patterns = detect_failure_patterns(diagnose())
        code_patches = sum(1 for p in patterns if p.get("fix_kind") == "code-patch")
        user_actions = sum(1 for p in patterns if p.get("fix_kind") == "user-action")
        if code_patches:
            signals.append((
                "capture drift",
                f"{code_patches} code-patch pattern(s)",
                "run `trinity-local extension repair --auto` (no HAR)",
            ))
        if user_actions:
            signals.append((
                "auth-cookie stale",
                f"{user_actions} provider(s)",
                "refresh login + send a test message",
            ))
    except Exception:
        pass

    if signals:
        print("  Signals:   action-takeable")
        for name, count, hint in signals:
            print(f"    ⚠ {name:<22} {count}")
            print(f"       └─ {hint}")
        print()

    # State location
    print(f"  State:     {home}")
    print()


