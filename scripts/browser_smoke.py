#!/usr/bin/env python3
"""scripts/browser_smoke.py — v1 launch-day UI smoke.

Drives Trinity's launchpad through ~34 testable surfaces via headless
playwright, asserts on DOM + console, saves a screenshot per surface to
docs/smoke/, exits non-zero if any surface fails.

The exact surface count is derived at render time by
`scripts/render_docs.canonical_smoke_surface_count()` (it counts distinct
"Surface NN" labels printed below) and pinned across claude.md /
product-spec.md / CONTRIBUTING.md via canonical placeholders. Do NOT
hardcode the count in this docstring; if you add or remove a surface,
the doc surfaces auto-update on the next render_docs pass.

Cheaper than Autobrowse (no per-page API token cost) and deterministic — same
result every run unless the underlying data changes. This is the launch gate
for "did the brand land in the browser, do all clicks still work."

Usage:
    python scripts/browser_smoke.py                    # headless
    python scripts/browser_smoke.py --headed           # watch it run
    python scripts/browser_smoke.py --port 8765        # different server port
    python scripts/browser_smoke.py --skip-regen       # don't re-run portal-html

Prereqs (installed once):
    pip install playwright
    playwright install chromium

Surfaces:
    1. Launchpad cold-render: Ratings chart bars rendered
    2. Settings gear: modal opens, telemetry-sharing toggle present
       (auto-chain + polish-auto-iterate global toggles retired
       2026-05-18 — auto-chain is now per-council on the review page)
    3. Personal routing table: >=1 row + every row links to routing.json viewer
       (tick #20); plus cortex basin links → picks.json viewer (tick #19)
    4. Lenses Copy-for-sharing: clipboard write fires with non-empty text
    5. Recent council click: live page loads with chairman synthesis
    6. Live council: Back to Launchpad returns home; winner persists on reload
    7. Launch Council button: present + clickable (full e2e gated on macOS Shortcut)
    8. Telemetry guard: no example.invalid console errors
    9. Multi-round thread render: 3 chain segments visible with round numbers
   10. Recent council card content: title + winner + rounds badge per card,
       plus cross-memory chips → picks + routing viewer (tick #15)
   12. Settings toggle binding: each :checked reflects underlying telemetry state
   13. Lens card render: paired-lenses block populates when tasteLenses exists,
       includes "View full lens →" cross-link to memory viewer (tick #12)
   14. Memory viewer + launchpad link: chip links → memory.html loads + renders file
   15. Memory-health row: 5 drift signals (core stale / picks overrides /
       picks audit / topology / cortex-stale-vs-new-outcomes per tick #106)
       surface inline with click-to-copy command chips that capture clipboard correctly
   16. Per-file health banner: same signal travels into the memory viewer when a stale
       file is opened, with chip mirroring Surface 15 + nav-dot indicator (tick #18)
   17. (retired 2026-06-05 — pick-veto chip removed with the user-pick layer)
   18. Rebuild chip: every memory viewer header carries a persistent
       .viewer-rebuild-chip that copies the rebuild CLI even when the file
       is fresh — closes the action-side for memories without a staleness
       signal (tick #27)
   19. Topic-graph launch chip: clicking a basin node opens a detail panel
       with a .topics-launch-chip that copies `trinity-local council-launch
       --task "<headline>"` — the action-side of the topology view (tick #28)
   20. Per-rep replay chip: every representative thread in the basin detail
       panel carries a .topics-rep-replay that copies the same command with
       that rep's headline as the seed — lets the user replay any thread,
       not just the closest-to-centroid one (tick #29). Verifies
       stopPropagation so the chip doesn't toggle the expand state.
   21. Topology → picks cross-link: if a basin has been consolidated into
       a routing rule (picks.json carries .basin_id pointing back at the
       topology), the basin detail panel shows a .topics-pick-xlink that
       deep-links to picks.html?file=picks.json&task=<task_type> (tick #30).
   22. Pick-basin node styling: SVG circles for basins crystallized into
       routing rules get a .pick-basin class (warm-brown ring), so the
       user sees which basins matter at a glance (tick #31).
   23. Picks → topology cross-link: each pick card with a centroid match
       renders 'View in topology →' targeting topics.html?basin=<id>;
       topology auto-opens the matching basin's detail panel + highlights
       its neighborhood on load (tick #32).
   24. Routing → topology chip: each routing-table row whose task_type has
       a centroid-matched basin renders a small .routing-topology-chip
       next to the task name, completing the routing/picks/topology
       triangle of cross-links (tick #33).
   25. Launchpad recent-card → topology chip: when the council's task_type
       has a centroid match, the card grows a third → topology chip
       alongside → pick and → routing, so the user can jump from a
       council straight to its topology basin (tick #34).
   26. Cortex picks card → topology chip: each cortex rule row whose
       basin_id has a centroid match grows a .cortex-topology-chip
       alongside the basin name (tick #35). Same matcher as Surfaces
       24 + 25 — three surfaces, one source of truth.
   27. Lens card → basin chips: each paired lens on the launchpad
       renders its basins_spanned[] as small .lens-basin-chip deep-links
       to the topology view focused on that basin (tick #36). Closes the
       forward-arc "lens card → source prompts" gap.
   28. Stale-basin banner: when ?basin=<id> deep-links to a basin that
       isn't in the current topology (lens-build re-ran with different
       cluster ids), the topology view surfaces a "not found" banner
       inside the detail panel + the lens-build copy chip — instead of
       landing silently with no panel open (tick #40).
   29. Handoff demo nudge banner: post-#115 launchpad-side mirror of
       the doctor 'try this next' hint. Asserts that pageData.handoffNudge.applicable
       agrees with whether the banner renders, and when rendered the
       command interpolates the picked target. Data/DOM mismatch in
       either direction = v-if guard regression.
   30. Personalized benchmark (eval summary) card: launchpad surface for
       the eval harness (#122/#116). Card is always present — empty
       state with CTA when no eval-run results exist, populated with
       per-axis bars + target headline when results are on disk.

Exit codes:
    0 — all surfaces pass
    1 — at least one surface failed
    2 — setup error (server, regen, etc.)
    3 — playwright not installed
"""

from __future__ import annotations

import argparse
import collections
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_trinity_home() -> Path:
    """Honor ``$TRINITY_HOME`` exactly like ``state_paths.trinity_home()`` (minus the
    import-time mkdir). The regen subprocess in main() already respects the env var
    (it runs ``portal-html`` with ``env=dict(os.environ)`` → ``trinity_home()``), so a
    hardcoded ``~/.trinity`` here would DIVERGE: regen writes the synthetic home's
    pages while the smoke serves + reads the real one. Honoring the env var keeps all
    three (regen / serve / read) on one home AND lets the whole 36-surface gate run
    against an isolated synthetic home — PII-free, in any environment, not just the
    founder's machine."""
    env = os.environ.get("TRINITY_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".trinity"


TRINITY_HOME = _resolve_trinity_home()
SHOTS_DIR = REPO_ROOT / "docs" / "smoke"
DEFAULT_PORT = 8765


def _visible_memory_files() -> list[str]:
    """The memory-viewer tabs the viewer ACTUALLY renders, in nav order — the 6
    core memories always, plus the OPTIONAL generators.md (the lens "lift" tier,
    shipped 2026-06-05) when it exists. Derived from the same _visible_files()
    contract the viewer renders from, so Surface 14b's nav-count and Surface 37's
    tab sweep track the real tab set instead of a hardcoded six (which rotted the
    moment the generators tab landed in the founder's home)."""
    try:
        from trinity_local.memory_viewer import _visible_files
        return [f["name"] for f in _visible_files()]
    except Exception:
        # Floor: the six core memories always render. If the package can't be
        # imported (shouldn't happen — the smoke also runs portal-html via it),
        # fall back to the always-on set so the gate degrades to under- not
        # over-strict.
        return ["core.md", "lens.md", "topics.json",
                "vocabulary.md", "picks.json", "routing.json"]

# ── Poller-spin guard (live_council_two_pollers) ──────────────────────────────
# A council_status_<token>.js poll that never reaches its MAX_MISSING_POLLS
# give-up keeps 404-ing forever, leaving the UI stuck "running" with zero
# feedback. There are THREE independent status pollers — launchpad
# startOperationPolling (cap 20), council_review startPolling + _pollChainStatus
# (cap 8 each) — and each needs its OWN cap; the bug has recurred three times.
# A spin floods the HTTP log, but the smoke only ASSERTED on surface DOM, so a
# regression went green while the log silently filled with 404s (the smoke's
# HTTP-log 404s are a free poller-health signal — this turns the manual eyeball
# into a gate). A healthy poll fires once (no-ext rollback / completion) or gives
# up at the cap (<=20); more than SPIN_THRESHOLD probes of one token means no cap.
STATUS_404_COUNTS: collections.Counter = collections.Counter()
SPIN_THRESHOLD = 30  # comfortably above the largest MAX_MISSING_POLLS (20)


class _SpinTrackingHandler(http.server.SimpleHTTPRequestHandler):
    """Tallies 404s on council_status_<token>.js so main() can fail the smoke
    when a status poller spins instead of giving up. Static-serving behaviour is
    otherwise identical to SimpleHTTPRequestHandler."""

    def send_error(self, code, message=None, explain=None):  # type: ignore[override]
        try:
            if int(code) == 404 and "council_status_" in (self.path or ""):
                m = re.search(r"(council_status_[A-Za-z0-9_]+)\.js", self.path)
                if m:
                    STATUS_404_COUNTS[m.group(1)] += 1
        except Exception:
            pass
        return super().send_error(code, message, explain)


def detect_poller_spins(counts, threshold: int = SPIN_THRESHOLD) -> list[tuple[int, str, str]]:
    """Given a {council_status_token: 404_count} mapping, return a `fails`-shaped
    list (surface_num, name, reason) for any token probed MORE than `threshold`
    times — a status poller that never hit its MAX_MISSING_POLLS give-up. Pure +
    side-effect-free so the guard logic is unit-testable without the full browser
    smoke (which isn't in pytest)."""
    out: list[tuple[int, str, str]] = []
    for tok, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n > threshold:
            out.append((
                0,
                "council_status poller spin",
                f"{tok} 404'd {n}× (> {threshold}) — a status poller never hit "
                f"its MAX_MISSING_POLLS give-up; the UI would stay stuck 'running'",
            ))
    return out


def _topics_basin_count() -> int:
    """Count basins in the real topics.json so the topology surfaces can tell
    an EMPTY install (legit 0 graph nodes → skip) from a BROKEN d3 render (0
    nodes despite real basins → fail). Surfaces 19/21/22 skip unconditionally on
    0 nodes; without this a graph that silently stopped binding its data would
    read GREEN on the founder's populated corpus — the exact "green check while
    the data is degenerate" trap (data_sampling_principle). Returns 0 on any
    error (missing file / legacy schema → treated as empty, so the skip stands)."""
    try:
        data = json.loads((TRINITY_HOME / "memories" / "topics.json").read_text(encoding="utf-8"))
        return len(data.get("basins", [])) if isinstance(data, dict) else 0
    except Exception:
        return 0


def _lens_basin_span_count() -> int:
    """Count lens tensions that span topology basins — the source of Surface
    27's `.lens-basin-chip` deep-links. >0 means chips SHOULD render, so 0 chips
    is a broken render, NOT the 'legacy lenses' (pre-basins_spanned) case the
    skip assumes. Returns 0 on any error (missing/legacy lens → skip stands)."""
    try:
        text = (TRINITY_HOME / "memories" / "lens.md").read_text(encoding="utf-8")
        return text.count("Tension evidence spans basins:")
    except Exception:
        return 0


def _council_with_disagreed_claims() -> tuple[str, str] | None:
    """Find a real (council_run_id, first-disagreed-claim) whose routing_label
    carries disagreed_claims AND has a per-council outcome script on disk — so
    Surface 35 can assert the PAINKILLER (the cross-provider disagreement, the
    product differentiator) actually renders on the live council page. Returns
    None when no council has recorded a disagreement yet (legit on a fresh
    install → the surface skips)."""
    import glob

    out_dir = TRINITY_HOME / "council_outcomes"
    try:
        for jf in sorted(
            glob.glob(str(out_dir / "council_*.json")),
            key=lambda p: Path(p).stat().st_mtime,
            reverse=True,
        ):
            d = json.loads(Path(jf).read_text(encoding="utf-8"))
            rl = d.get("routing_label") or {}
            dis = rl.get("disagreed_claims") or []
            crid = d.get("council_run_id")
            if not (dis and crid):
                continue
            claim = dis[0].get("claim") if isinstance(dis[0], dict) else None
            if claim and (out_dir / f"{Path(jf).stem}.js").exists():
                return (crid, claim)
    except (OSError, ValueError, KeyError):
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--skip-regen", action="store_true", help="skip portal-html regeneration")
    args = parser.parse_args()

    # Step 1: regenerate launchpad pages from current source
    if not args.skip_regen:
        print("[setup] regenerating launchpad via portal-html...")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "trinity_local.main", "portal-html"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print("[setup] portal-html timed out — fatal")
            return 2
        if result.returncode != 0:
            print(f"[setup] portal-html failed (exit {result.returncode}):\n{result.stderr[:500]}")
            return 2

    # Step 2: ensure a server is responding
    server_handle = _ensure_server(args.port)
    if server_handle is None and not _is_server_up(args.port):
        print(f"[setup] could not start nor reach server on :{args.port}")
        return 2

    # Step 3: open playwright
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[setup] playwright not installed. Run:")
        print("    pip install playwright")
        print("    playwright install chromium")
        return 3

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url = f"http://127.0.0.1:{args.port}"

    fails: list[tuple[int, str, str]] = []  # (surface_num, name, reason)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=not args.headed)
        except Exception as exc:
            print(f"[setup] chromium launch failed: {exc}")
            print("    Run: playwright install chromium")
            return 3
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )

        # ─── Surface 1: launchpad cold-render ────────────────────────────────
        page.goto(f"{base_url}/portal_pages/launchpad.html", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)  # petite-vue + Chart.js settle
        chart_data = page.evaluate(
            """() => {
              const c = document.getElementById('personal-preference-chart');
              if (!c) return {chart_attached: false, reason: 'canvas missing'};
              const ch = (window.Chart && Chart.getChart) ? Chart.getChart(c) : null;
              if (!ch) return {chart_attached: false, reason: 'no chart instance'};
              const has_bars = ch.data.datasets.some(d => d.data.some(v => v !== null));
              return {chart_attached: true, has_bars, labels: ch.data.labels, first_dataset: ch.data.datasets[0]?.data};
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "1-launchpad.png"), full_page=True)
        if chart_data.get("chart_attached") and chart_data.get("has_bars"):
            print(f"[ ✓ ] Surface 1 launchpad: chart bars present {chart_data['first_dataset']}")
        else:
            reason = chart_data.get("reason") or "no bars in any dataset"
            print(f"[ ✗ ] Surface 1 launchpad: {reason}")
            fails.append((1, "launchpad cold-render", reason))

        # ─── Surface 2: Settings gear ────────────────────────────────────────
        gear_clicked = page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '⚙');
              if (!btn) return {ok: false, reason: 'gear button missing'};
              btn.click();
              return {ok: true};
            }"""
        )
        page.wait_for_timeout(400)
        settings_state = page.evaluate(
            """() => {
              const modal = document.querySelector('.settings-modal');
              const toggles = document.querySelectorAll('.settings-modal input[type="checkbox"]');
              const endpoint = Array.from(document.querySelectorAll('.settings-modal .meta'))
                .find(el => /https?:\\/\\//.test(el.textContent))?.textContent?.trim();
              return {modal_visible: !!modal, toggle_count: toggles.length, has_endpoint: !!endpoint};
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "2-settings.png"))
        # Close the modal so it doesn't intercept subsequent clicks.
        # Petite-vue binds @click="settingsOpen = false" on the × button;
        # find it by content + dispatch a real click. Verify the modal
        # actually went away before moving on.
        page.evaluate(
            """() => {
              const buttons = Array.from(document.querySelectorAll('.settings-modal button'));
              const x = buttons.find(b => b.textContent.trim() === '×');
              if (x) x.click();
            }"""
        )
        # Wait for petite-vue to re-render the modal away
        page.wait_for_function(
            "() => !document.querySelector('.settings-modal')",
            timeout=3000,
        )
        page.wait_for_timeout(200)
        # Toggle count: telemetry-sharing only (auto-chain + polish-auto
        # toggles were retired 2026-05-18 per the simplification pass —
        # auto-chain is now per-council on the review page, no global
        # toggle).
        if gear_clicked.get("ok") and settings_state.get("modal_visible") and settings_state.get("toggle_count", 0) >= 1:
            print(f"[ ✓ ] Surface 2 settings: modal opens with {settings_state['toggle_count']} toggle")
        else:
            reason = f"{gear_clicked} / {settings_state}"
            print(f"[ ✗ ] Surface 2 settings: {reason}")
            fails.append((2, "settings gear", reason))

        # ─── Surface 3: Personal routing table + cortex basin links ──────────
        routing_state = page.evaluate(
            """() => {
              // Exclude .cortex-rules-table — that's the "what Trinity learned"
              // card which shares the .routing-table base class but has its
              // own first-cell shape (plain text, no .benchmark-category div).
              // We want the personal routing table specifically.
              const personalRows = Array.from(document.querySelectorAll('table.routing-table:not(.cortex-rules-table) tbody tr'));
              const first = personalRows[0];
              const first_task = first?.querySelector('td:first-child .benchmark-category')?.textContent?.trim();
              // Tick #20: personal routing rows now deep-link to the
              // routing.json viewer with the row's task_type focused.
              const personalLinkCount = personalRows.filter(r =>
                r.querySelector('td:first-child a[href*="memory.html?file=routing.json&task="]')
              ).length;
              const personalFirstHref = first?.querySelector('td:first-child a')?.getAttribute('href') || null;
              // Tick #19: cortex basin_id is a deep-link to the picks viewer.
              // Pull the first basin link's href for assertion. Count only
              // the basin link per row (not the optional .cortex-topology-chip
              // anchor added in tick #35).
              const cortexLink = document.querySelector('.cortex-rules-table tbody tr td:first-child a:not(.cortex-topology-chip)');
              const cortexHref = cortexLink?.getAttribute('href') || null;
              const cortexLinkCount = Array.from(document.querySelectorAll('.cortex-rules-table tbody tr'))
                .filter(r => r.querySelector('td:first-child a:not(.cortex-topology-chip)'))
                .length;
              const cortexRowCount = document.querySelectorAll('.cortex-rules-table tbody tr').length;
              return {
                row_count: personalRows.length,
                first_task,
                personalLinkCount,
                personalFirstHref,
                cortexHref,
                cortexLinkCount,
                cortexRowCount,
              };
            }"""
        )
        # Cortex link is optional — installs without cortex consolidation
        # don't render the table at all (the section is v-if). When the
        # table IS rendered, every row must carry a memory.html?file=picks.json
        # deep-link in the first cell.
        cortex_rows = routing_state.get("cortexRowCount") or 0
        cortex_links_ok = (
            cortex_rows == 0  # no cortex table → nothing to assert
            or (
                routing_state.get("cortexLinkCount") == cortex_rows
                and "memory.html?file=picks.json&task=" in (routing_state.get("cortexHref") or "")
            )
        )
        # Tick #20: personal routing rows must each carry a deep-link
        # to the routing.json viewer. Symmetric with the cortex check.
        personal_rows = routing_state.get("row_count") or 0
        personal_links_ok = (
            personal_rows == 0
            or (
                routing_state.get("personalLinkCount") == personal_rows
                and "memory.html?file=routing.json&task=" in (routing_state.get("personalFirstHref") or "")
            )
        )
        if (
            personal_rows >= 1
            and routing_state.get("first_task")
            and cortex_links_ok
            and personal_links_ok
        ):
            cortex_note = f" · cortex {cortex_rows} rows w/ basin links" if cortex_rows else ""
            print(f"[ ✓ ] Surface 3 routing table: {personal_rows} rows (first: '{routing_state['first_task']}', all linked){cortex_note}")
        else:
            reason = (
                f"row_count={personal_rows} personal_links_ok={personal_links_ok}"
                f" cortex_links_ok={cortex_links_ok} cortex_rows={cortex_rows}"
            )
            print(f"[ ✗ ] Surface 3 routing table: {reason}")
            fails.append((3, "routing table", reason))

        # Focused screenshot — scroll to the routing table so visual review
        # gets the section in isolation, not buried in a fullPage shot.
        page.evaluate("() => document.querySelector('table.routing-table')?.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS_DIR / "3-routing-table.png"))

        # ─── Surface 4: Copy-for-sharing clipboard write ─────────────────────
        # Scroll the lens section into view FIRST so the post-click screenshot
        # frames the right surface. Clicking flips the button label to
        # "Copied!" which would break any text-based selector run after.
        page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('button')).find(b => /(Copy.*shar|Copy as text)/i.test(b.textContent));
              btn?.scrollIntoView({block: 'center'});
            }"""
        )
        page.wait_for_timeout(300)

        copy_state = page.evaluate(
            """() => new Promise(resolve => {
              let copied = null;
              const orig = navigator.clipboard?.writeText;
              if (orig) navigator.clipboard.writeText = async (t) => { copied = t; return Promise.resolve(); };
              const btn = Array.from(document.querySelectorAll('button')).find(b => /(Copy.*shar|Copy as text)/i.test(b.textContent));
              if (!btn) { if (orig) navigator.clipboard.writeText = orig; resolve({ok: false, reason: 'btn missing'}); return; }
              btn.click();
              setTimeout(() => {
                if (orig) navigator.clipboard.writeText = orig;
                resolve({ok: true, len: copied?.length, preview: copied?.slice(0, 80)});
              }, 300);
            })"""
        )
        if copy_state.get("ok") and (copy_state.get("len") or 0) > 0:
            print(f"[ ✓ ] Surface 4 copy-for-sharing: {copy_state['len']} chars copied ('{copy_state['preview']}...')")
        else:
            reason = copy_state.get("reason") or "nothing copied"
            print(f"[ ✗ ] Surface 4 copy-for-sharing: {reason}")
            fails.append((4, "copy-for-sharing", reason))

        page.screenshot(path=str(SHOTS_DIR / "4-copy-for-sharing.png"))

        # ─── Surface 5: Recent council click ─────────────────────────────────
        # Council history lives in the persistent left rail (.council-rail)
        # since founder 2026-06-01 ("why dup council history on the main page");
        # the old in-page .council-card-link grid was removed. Rail items are
        # `<a class="rail-council">` linking to live_council.html?thread_id=...,
        # so the click-through + live-page assertions below are unchanged — only
        # the selector moved.
        recent_state = page.evaluate(
            """() => {
              const links = document.querySelectorAll('.council-rail a.rail-council');
              return {count: links.length, first_href: links[0]?.getAttribute('href')};
            }"""
        )
        if recent_state.get("count", 0) == 0:
            print("[ ✗ ] Surface 5 recent council: no rail councils (need to run trinity-local council first)")
            fails.append((5, "recent council click", "no rail councils rendered"))
            council_page_loaded = False
        else:
            page.click(".council-rail a.rail-council", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=10000)
            page.wait_for_timeout(2000)
            live_state = page.evaluate(
                """() => {
                  const h2 = document.querySelector('h2');
                  const has_failed_card = !!Array.from(document.querySelectorAll('strong,h2,h3,p'))
                    .find(el => /Council failed/i.test(el.textContent));
                  const has_winner = !!Array.from(document.querySelectorAll('h2,h3'))
                    .find(el => /winner/i.test(el.textContent));
                  return {url: window.location.href, h2: h2?.textContent?.trim(), has_failed_card, has_winner};
                }"""
            )
            page.screenshot(path=str(SHOTS_DIR / "5-recent-council.png"), full_page=True)
            council_page_loaded = "live_council" in live_state.get("url", "") and not live_state.get("has_failed_card")
            if council_page_loaded:
                print(f"[ ✓ ] Surface 5 recent council: live page renders ('{live_state.get('h2')}')")
            else:
                reason = "Council failed card" if live_state.get("has_failed_card") else f"didn't reach live page: {live_state}"
                print(f"[ ✗ ] Surface 5 recent council: {reason}")
                fails.append((5, "recent council click", reason))

        # ─── Surface 6: Live council back-trip + winner persist ──────────────
        if council_page_loaded:
            # Wait for the navigation in the same context as the click —
            # otherwise wait_for_load_state can return on the still-current
            # page before the link fires its navigation.
            # Sub-pages now use the shared .trinity-topbar pattern with
            # .topbar-back as the up-navigation control (label: "← Launchpad").
            # Earlier pages used a.button.ghost with "Back to Launchpad".
            back_present = page.evaluate(
                """() => !!document.querySelector('.trinity-topbar a.topbar-back')"""
            )
            if not back_present:
                print("[ ✗ ] Surface 6 live council back-trip: back btn missing")
                fails.append((6, "live council back-trip", "back btn missing"))
            else:
                try:
                    with page.expect_navigation(timeout=10000):
                        page.evaluate(
                            """() => document.querySelector('.trinity-topbar a.topbar-back')?.click()"""
                        )
                    page.wait_for_timeout(500)
                    if "launchpad.html" in page.url:
                        print("[ ✓ ] Surface 6 live council back-trip: returned to launchpad")
                        # Focused capture — launchpad-after-back-from-council.
                        # Should look identical to the cold-render except scroll
                        # position; visual review catches state leakage.
                        page.evaluate("() => window.scrollTo(0, 0)")
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(SHOTS_DIR / "6-back-to-launchpad.png"))
                except Exception as exc:
                    print(f"[ ✗ ] Surface 6 live council back-trip: navigation failed ({exc})")
                    fails.append((6, "live council back-trip", str(exc)[:120]))
        else:
            print("[ - ] Surface 6 live council back-trip: SKIPPED (surface 5 failed)")
            fails.append((6, "live council back-trip", "skipped (s5 failed)"))

        # ─── Surface 7: Launch Council button presence ───────────────────────
        # E2E (fire a real council) is gated on macOS Shortcut; assert button is wired.
        # If we navigated mid-Surface-6, wait for the new page to be ready.
        if "launchpad.html" not in page.url:
            page.goto(f"{base_url}/portal_pages/launchpad.html", wait_until="networkidle", timeout=10000)
        page.wait_for_timeout(800)
        launch_state = page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('button'))
                .find(b => /Launch Council/i.test(b.textContent));
              return {present: !!btn, disabled: btn?.disabled || false};
            }"""
        )
        if launch_state.get("present") and not launch_state.get("disabled"):
            print("[ ✓ ] Surface 7 Launch Council button: present + enabled (e2e gated on macOS Shortcut, not asserted)")
        else:
            print(f"[ ✗ ] Surface 7 Launch Council button: {launch_state}")
            fails.append((7, "Launch Council button", str(launch_state)))

        # Focused screenshot of the Council card with Launch button visible.
        page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('button')).find(b => /Launch Council/i.test(b.textContent));
              btn?.scrollIntoView({block: 'center'});
            }"""
        )
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS_DIR / "7-launch-button.png"))

        # ─── Surface 8: Telemetry guard (no example.invalid in console) ──────
        invalid_errs = [e for e in console_errors if "example.invalid" in e or "ERR_NAME_NOT_RESOLVED" in e]
        if not invalid_errs:
            print("[ ✓ ] Surface 8 telemetry guard: no example.invalid console errors")
        else:
            print(f"[ ✗ ] Surface 8 telemetry guard: {len(invalid_errs)} stray errors")
            fails.append((8, "telemetry guard", f"{len(invalid_errs)} errors: {invalid_errs[:2]}"))

        # Focused screenshot — settings modal showing the "Not configured"
        # display fix (endpoint guard). Open modal, capture, close cleanly.
        page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '⚙');
              btn?.click();
            }"""
        )
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS_DIR / "8-settings-endpoint-guard.png"))
        # Close the modal cleanly before subsequent surfaces interact with
        # the launchpad — petite-vue mounts it over the whole viewport and
        # eats clicks if left open.
        page.evaluate(
            """() => {
              const buttons = Array.from(document.querySelectorAll('.settings-modal button'));
              const x = buttons.find(b => b.textContent.trim() === '×');
              if (x) x.click();
            }"""
        )
        page.wait_for_function(
            "() => !document.querySelector('.settings-modal')",
            timeout=3000,
        )

        # ─── Surface 9: Multi-round thread + refinement + Quote chip ──────────
        # The thread-per-page UX (commit 3c44bbd fixed a regression where
        # auto-chain iteration rounds got lost). Pick a known 3-round bundle
        # from disk and assert all three segments render with the expected
        # round numbers in order. Skips if no multi-round bundle is on disk.
        # Extended (tick #56): also asserts the refinement directive ("↳ <text>")
        # renders for rounds past 1. That bug (outcome.metadata.user_refinement
        # captured but not hydrated into seg.refinementText in
        # _loadOutcomeIntoSegment) made every refinement vanish on reload —
        # observed on bundle_42f8cea9c9e705e5 with "Stop copy-pasting prompts.
        # Own your context. Forge your core memories." The selector tracks
        # .refinement-prompt structurally, not the user's exact text.
        # Extended (tick #61): also asserts the Quote chip (tick #60) is
        # present on completed member cards AND clicking it actually
        # populates the refinement input. The behavior is the regression
        # target — a future refactor that drops quoteMember() or breaks
        # the click.stop binding would silently regress the cherry-pick
        # workflow this surface protects.
        thread_id = _find_multi_round_thread()
        if thread_id is None:
            print("[ - ] Surface 9 multi-round thread: SKIPPED (no 3+ round bundle on disk)")
        else:
            page.goto(
                f"{base_url}/review_pages/live_council.html?thread_id={thread_id}",
                wait_until="networkidle",
                timeout=15000,
            )
            page.wait_for_timeout(1500)  # petite-vue hydration
            thread_state = page.evaluate(
                """() => {
                  const segments = Array.from(document.querySelectorAll('.chain-segment'));
                  const roundLabels = segments.map(seg => {
                    const eyebrow = seg.querySelector('.eyebrow');
                    const m = eyebrow?.textContent?.match(/Round\\s+(\\d+)/);
                    return m ? parseInt(m[1], 10) : null;
                  });
                  const hasSynthesis = segments.map(seg =>
                    !!seg.querySelector('.synthesis-section .markdown-body')
                  );
                  const refinementCount = segments.filter(seg => {
                    const r = seg.querySelector('.refinement-prompt');
                    return !!(r && r.textContent && r.textContent.trim().length > 1);
                  }).length;
                  const quoteButtonCount = document.querySelectorAll('.quote-member-btn').length;
                  return {seg_count: segments.length, roundLabels, hasSynthesis, refinementCount, quoteButtonCount};
                }"""
            )
            # Behavior check: click the first Quote chip and confirm it
            # populates the refinement input. Light wait for petite-vue
            # to react before reading the input value. The chip uses
            # @click.stop so this MUST NOT trigger the parent article's
            # pick-winner click — if it does, the rate_council shortcut
            # would fire and we'd see a selectedProvider mutation.
            quote_behavior = page.evaluate(
                """() => {
                  const btn = document.querySelector('.quote-member-btn');
                  const input = document.querySelector('.chain-refine-input');
                  if (!btn || !input) return {clicked: false};
                  const before = input.value || '';
                  btn.click();
                  return {clicked: true, before_len: before.length};
                }"""
            )
            page.wait_for_timeout(200)
            quote_after = page.evaluate(
                """() => {
                  const input = document.querySelector('.chain-refine-input');
                  return {after_len: input ? (input.value || '').length : 0};
                }"""
            )
            page.screenshot(path=str(SHOTS_DIR / "9-multi-round-thread.png"), full_page=True)
            rounds = thread_state.get("roundLabels", [])
            seg_count = thread_state.get("seg_count", 0)
            synth_ok = all(thread_state.get("hasSynthesis", []))
            refinement_count = thread_state.get("refinementCount", 0)
            quote_count = thread_state.get("quoteButtonCount", 0)
            quote_click_ok = (
                quote_behavior.get("clicked")
                and quote_after.get("after_len", 0) > quote_behavior.get("before_len", 0)
            )
            # Each segment must (a) exist, (b) carry its own round number,
            # (c) render its own chairman synthesis. Round numbers should be
            # monotonic — first is 1, then 2, then 3. Additionally, when
            # the picker selected a refinement-bearing thread (preferred
            # path), at least one segment must show a refinement directive
            # ("↳ <text>"). The picker falls back to any 3+ thread when no
            # refinement-bearing thread exists (legacy installs); in that
            # case the refinement assertion is informational only.
            picked_has_refinement = _thread_has_refinement(thread_id)
            structural_ok = seg_count >= 3 and rounds[:3] == [1, 2, 3] and synth_ok
            refinement_ok = (not picked_has_refinement) or refinement_count >= 1
            # Quote chip must be present on at least one completed member
            # card AND clicking it must grow the refinement input. The
            # button is gated on row.statusClass === 'done', so a
            # completed thread (which is what Surface 9 picks) is
            # guaranteed to have ≥1 candidate card with the chip.
            quote_ok = quote_count >= 1 and quote_click_ok
            if structural_ok and refinement_ok and quote_ok:
                refinement_note = (
                    f", refinement_count={refinement_count}"
                    if picked_has_refinement
                    else " (no-refinement thread; assertion skipped)"
                )
                print(f"[ ✓ ] Surface 9 multi-round thread: {seg_count} segments, rounds={rounds}, synthesis per round{refinement_note}, quote_chips={quote_count} (click populates input)")
            else:
                reason = (
                    f"seg_count={seg_count} rounds={rounds} synth_per_round_ok={synth_ok} "
                    f"refinement_count={refinement_count} (picked_has_refinement={picked_has_refinement}) "
                    f"quote_count={quote_count} quote_click_ok={quote_click_ok}"
                )
                print(f"[ ✗ ] Surface 9 multi-round thread: {reason}")
                fails.append((9, "multi-round thread render", reason))

            # Bounce back to the launchpad for the next surfaces.
            page.goto(f"{base_url}/portal_pages/launchpad.html", wait_until="networkidle", timeout=10000)
            page.wait_for_timeout(800)

        # ─── Surface 10: Recent council card content ──────────────────────────
        # Surface 5 confirms the rail councils click through; this confirms each
        # rail item actually carries the title + meta block ("winner · date")
        # and the href routes to live_council.html with a thread_id param.
        #
        # Council history moved to the persistent left rail (.council-rail) on
        # 2026-06-01 — rail items are `<a class="rail-council">` wrapping a
        # `.rail-council-title` span and a `.rail-council-meta` span
        # ("winner · relative-date"); the old in-page card grid + its
        # .council-title/.meta classes were removed. See
        # launchpad_data.build_recent_sidebar_html for the canonical shape.
        cards_state = page.evaluate(
            """() => {
              const links = Array.from(document.querySelectorAll('.council-rail a.rail-council'));
              const sample = links.slice(0, 3).map(a => {
                const title = a.querySelector('.rail-council-title')?.textContent?.trim();
                const meta = a.querySelector('.rail-council-meta')?.textContent?.trim();
                const hrefHasThread = (a.getAttribute('href') || '').includes('thread_id=');
                return {
                  title_ok: !!title && title.length > 0,
                  meta_ok: !!meta && meta.length > 0,
                  hrefHasThread,
                };
              });
              return {count: links.length, sample};
            }"""
        )
        sample = cards_state.get("sample", [])
        cards_ok = (
            cards_state.get("count", 0) >= 1
            and all(s.get("title_ok") and s.get("meta_ok") and s.get("hrefHasThread") for s in sample)
        )

        if cards_ok:
            print(
                f"[ ✓ ] Surface 10 recent cards: {cards_state['count']} rail councils "
                "with title/meta/thread_id"
            )
        else:
            reason = f"count={cards_state.get('count')} sample={sample}"
            print(f"[ ✗ ] Surface 10 recent cards: {reason}")
            fails.append((10, "recent card content", reason))

        # ─── Surface 12: Settings toggle binding ─────────────────────────────
        # Surface 2 confirms toggles are present. This confirms each toggle's
        # `:checked` binding actually reflects the underlying telemetry state
        # in page-data — catches a regression where the binding is wired to
        # the wrong reactive key or a stale closure.
        #
        # NOTE: we don't simulate a click. Real toggles call
        # `triggerSettingsAction` which fires `scheduleLaunchpadReload(1400)`
        # — the page navigates mid-test, destroying any pending evaluate.
        # The full mutation path is covered by unit tests + end-to-end
        # `trinity-local telemetry-{enable,disable}` calls; here we just
        # verify the rendered DOM matches the bound state.
        page.evaluate(
            """() => {
              const gear = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '⚙');
              gear?.click();
            }"""
        )
        page.wait_for_selector(".settings-modal input[type='checkbox']", state="attached", timeout=2000)
        toggle_state = page.evaluate(
            """() => {
              const script = document.getElementById('page-data');
              const data = script ? JSON.parse(script.textContent || '{}') : {};
              const tel = data.telemetry || {};
              const toggles = Array.from(document.querySelectorAll('.settings-modal input[type="checkbox"]'));
              // Post-simplification (2026-05-18): only the telemetry-sharing
              // toggle remains; auto-chain + polish-auto-iterate toggles
              // were retired per commit 1fed7fc.
              // The checkbox binds to the Vue-derived `telemetry.enabled`, which
              // is `!!pageData.telemetry.settings.sharing_enabled` (template
              // data() line ~2508). page-data has NO top-level `telemetry.enabled`
              // key — reading it gave a phantom `false` and a false-red whenever
              // sharing_enabled defaulted ON. Assert against the real bound field.
              const expected = [
                ['sharing_enabled', (tel.settings || {}).sharing_enabled],
              ];
              const rows = toggles.slice(0, 1).map((t, i) => ({
                key: expected[i][0],
                expected: !!expected[i][1],
                actual: t.checked,
                matches: t.checked === !!expected[i][1],
              }));
              return {toggle_count: toggles.length, rows};
            }"""
        )
        rows = toggle_state.get("rows", [])
        bindings_ok = (
            toggle_state.get("toggle_count", 0) >= 1
            and len(rows) == 1
            and all(r.get("matches") for r in rows)
        )
        if bindings_ok:
            summary = ", ".join(f"{r['key']}={r['actual']}" for r in rows)
            print(f"[ ✓ ] Surface 12 toggle bindings: 1 toggle matches page-data ({summary})")
        else:
            reason = f"count={toggle_state.get('toggle_count')} rows={rows}"
            print(f"[ ✗ ] Surface 12 toggle bindings: {reason}")
            fails.append((12, "settings toggle binding", reason))

        # Close modal cleanly before next surface.
        page.evaluate(
            """() => {
              const buttons = Array.from(document.querySelectorAll('.settings-modal button'));
              const x = buttons.find(b => b.textContent.trim() === '×');
              if (x) x.click();
            }"""
        )
        page.wait_for_function(
            "() => !document.querySelector('.settings-modal')",
            timeout=3000,
        )

        # ─── Surface 13: Lens card render ────────────────────────────────────
        # The taste-card is v-if="tasteLenses" — if lens-build hasn't run
        # we get an empty-state card instead. Either is valid; assert
        # whichever path the data takes is structurally complete.
        lens_state = page.evaluate(
            """() => {
              const tasteCard = document.querySelector('section.taste-card');
              if (tasteCard) {
                const paired = tasteCard.querySelectorAll('.taste-block .taste-list li').length;
                const vocab = tasteCard.querySelectorAll('.taste-vocab-chip').length;
                const shareBtn = !!Array.from(tasteCard.querySelectorAll('button')).find(b => /copy/i.test(b.textContent));
                // Cross-memory link: the rich preview here should link to
                // the full lens.md viewer (commit dd283b0's queued item).
                const fullLensLink = !!Array.from(tasteCard.querySelectorAll('a')).find(
                  a => /view full lens/i.test(a.textContent) && (a.getAttribute('href') || '').includes('memory.html')
                );
                return {variant: 'lenses', paired_count: paired, vocab_count: vocab, has_share_btn: shareBtn, has_full_lens_link: fullLensLink};
              }
              // Empty-state — the section right below the routing card carrying the lens-build CTA.
              const emptyHeading = Array.from(document.querySelectorAll('h2'))
                .find(h => /lens|taste|me-build|lens-build/i.test(h.textContent));
              return {variant: 'empty-state', has_cta: !!emptyHeading};
            }"""
        )
        if lens_state.get("variant") == "lenses":
            ok = (
                lens_state.get("paired_count", 0) >= 1
                and lens_state.get("has_share_btn")
                and lens_state.get("has_full_lens_link")
            )
            if ok:
                print(f"[ ✓ ] Surface 13 lens card: {lens_state['paired_count']} lens items + share button + full-lens link")
            else:
                reason = f"lens card present but incomplete: {lens_state}"
                print(f"[ ✗ ] Surface 13 lens card: {reason}")
                fails.append((13, "lens card render", reason))
        elif lens_state.get("variant") == "empty-state" and lens_state.get("has_cta"):
            print("[ ✓ ] Surface 13 lens card: empty-state CTA shown (lens-build not run on this install)")
        else:
            reason = f"neither lens card nor empty-state CTA: {lens_state}"
            print(f"[ ✗ ] Surface 13 lens card: {reason}")
            fails.append((13, "lens card render", reason))

        # ─── Surface 14: Memory viewer + launchpad chip link ─────────────────
        # The launchpad's "Your memories, raw" card should expose chip links
        # to the generic memory viewer (memory.html?file=<name>). This
        # asserts (a) chips exist for each of the six memories, (b) clicking
        # one navigates to memory.html and the viewer renders the file body.
        chips_state = page.evaluate(
            """() => {
              const chips = Array.from(document.querySelectorAll('a.memory-chip'));
              return {
                count: chips.length,
                names: chips.map(a => a.querySelector('code')?.textContent?.trim()).filter(Boolean),
                first_href: chips[0]?.getAttribute('href'),
              };
            }"""
        )
        # Memory chips = the 4 cognitive surfaces (core.md, lens.md,
        # topics.json, vocabulary.md). picks.json + routing.json live on
        # the scoreboard card now per the 2026-05-17 state-layout
        # reshuffle — they're operational bookkeeping, not cognitive
        # memory, so they don't appear in the .memory-chip row.
        expected_names = {"lens.md", "topics.json", "vocabulary.md", "core.md"}
        actual_names = set(chips_state.get("names") or [])
        if chips_state.get("count", 0) >= 4 and expected_names.issubset(actual_names):
            print(f"[ ✓ ] Surface 14a memory chips: {chips_state['count']} cognitive-memory links present")
        else:
            reason = f"count={chips_state.get('count')} names={sorted(actual_names)}"
            print(f"[ ✗ ] Surface 14a memory chips: {reason}")
            fails.append((14, "memory viewer chips", reason))

        # Click the first chip (lens.md) and verify the viewer renders body.
        try:
            with page.expect_navigation(timeout=8000):
                page.evaluate("""() => document.querySelector('a.memory-chip')?.click()""")
            page.wait_for_timeout(800)  # let fetch + DOM render settle
            viewer_state = page.evaluate(
                """() => {
                  // .md → .markdown-body (rendered via marked + DOMParser)
                  // .json → .pick-card / .routing-table / .json-body (raw view)
                  // Either way, the rendered region carries text.
                  const renderRoot =
                    document.querySelector('.markdown-body') ||
                    document.querySelector('.pick-card') ||
                    document.querySelector('.routing-table') ||
                    document.querySelector('.json-body') ||
                    document.querySelector('pre.body');
                  return {
                    url: window.location.href,
                    title: document.querySelector('.content-header h2')?.textContent,
                    bodyLen: (renderRoot?.textContent || '').length,
                    hasMarkdownBody: !!document.querySelector('.markdown-body'),
                    activeNav: document.querySelector('.memory-nav-link.active')?.dataset.file,
                    navCount: document.querySelectorAll('.memory-nav-link').length,
                  };
                }"""
            )
            page.screenshot(path=str(SHOTS_DIR / "14-memory-viewer.png"), full_page=True)
            on_viewer = "memory.html" in viewer_state.get("url", "")
            has_body = (viewer_state.get("bodyLen") or 0) > 50  # any real memory has more than 50 chars
            empty_ok = viewer_state.get("bodyLen") == 0 and viewer_state.get("title")  # empty-state is also OK
            expected_nav = len(_visible_memory_files())  # 6 core + generators.md when present
            if on_viewer and viewer_state.get("navCount") == expected_nav and (has_body or empty_ok):
                print(f"[ ✓ ] Surface 14b memory viewer: '{viewer_state['title']}' rendered ({viewer_state['bodyLen']} chars, active={viewer_state['activeNav']})")
            else:
                reason = f"viewer_state={viewer_state}"
                print(f"[ ✗ ] Surface 14b memory viewer: {reason}")
                fails.append((14, "memory viewer load", reason))
        except Exception as exc:
            print(f"[ ✗ ] Surface 14b memory viewer: navigation failed ({exc})")
            fails.append((14, "memory viewer load", str(exc)[:120]))

        # ─── Surface 15: Memory-health row ───────────────────────────────────
        # The launchpad surfaces a "what's stale, what should I do" row
        # built from five signals (core.md staleness; picks override_count;
        # picks audit_status disagreed; pre-thread-aware topology;
        # picks.json cortex-stale — councils newer than last consolidate,
        # added tick #106). The card renders only when issues exist — a
        # silent absence on a fresh install is ALSO valid. The check:
        # page-data carries memoryHealth, and the rendered DOM matches
        # the data (no issues → no card; issues → card with N items +
        # valid action hints + click-to-copy chip that fires `command`).
        if "launchpad.html" not in page.url:
            page.goto(f"{base_url}/portal_pages/launchpad.html", wait_until="networkidle", timeout=10000)
            page.wait_for_timeout(800)
        health_state = page.evaluate(
            """() => {
              const script = document.getElementById('page-data');
              const data = script ? JSON.parse(script.textContent || '{}') : {};
              const mh = data.memoryHealth || {};
              const issues = mh.issues || [];
              const cardEl = document.querySelector('.memory-health-card');
              const renderedItems = document.querySelectorAll('.memory-health-card li').length;
              return {
                issue_count: issues.length,
                ok_count: mh.ok_count,
                total_count: mh.total_count,
                card_rendered: !!cardEl,
                rendered_item_count: renderedItems,
                first_hint: issues[0]?.hint || null,
                first_name: issues[0]?.name || null,
              };
            }"""
        )
        ic = health_state.get("issue_count") or 0
        card = health_state.get("card_rendered")
        rendered_n = health_state.get("rendered_item_count") or 0
        # Consistency: if issues > 0, card must render with N items; if 0,
        # card must NOT render (v-if guards the silent-fresh state).
        if ic == 0 and not card:
            print("[ ✓ ] Surface 15 memory-health: silent (all 5 signals fresh)")
        elif ic > 0 and card and rendered_n == ic:
            hint_ok = bool(health_state.get("first_hint"))
            name_ok = bool(health_state.get("first_name"))
            # Action-from-view: when an issue carries a `command`, the
            # rendered row must include a click-to-copy chip. Validate by
            # intercepting clipboard.writeText, clicking the chip, and
            # confirming the captured value matches issue.command.
            copy_state = page.evaluate(
                """() => new Promise(resolve => {
                  // Target the per-issue copy-to-clipboard chip in the
                  // issues list, not the action buttons in the card
                  // eyebrow (Refresh memory / Repair extension — those
                  // dispatch via Chrome extension and don't write to
                  // the clipboard).
                  const btn = document.querySelector('.memory-health-list button');
                  if (!btn) { resolve({skipped: true, reason: 'no chip (issue may be href-only)'}); return; }
                  const expected = btn.textContent.trim();
                  let captured = null;
                  const orig = navigator.clipboard?.writeText;
                  if (orig) navigator.clipboard.writeText = async (t) => { captured = t; return Promise.resolve(); };
                  btn.click();
                  setTimeout(() => {
                    const after = btn.textContent.trim();
                    if (orig) navigator.clipboard.writeText = orig;
                    resolve({expected, captured, after, matches: captured === expected, flipped: after.startsWith('✓')});
                  }, 200);
                })"""
            )
            chip_ok = copy_state.get("skipped") or (copy_state.get("matches") and copy_state.get("flipped"))
            if hint_ok and name_ok and chip_ok:
                chip_note = " (action chip works)" if not copy_state.get("skipped") else " (no action chip — href-only issue)"
                print(f"[ ✓ ] Surface 15 memory-health: {ic} issue(s) rendered ({health_state['ok_count']}/{health_state['total_count']} healthy) — first: {health_state['first_name']}{chip_note}")
            else:
                reason = f"first issue missing hint/name/chip: hint={hint_ok} name={name_ok} chip_state={copy_state}"
                print(f"[ ✗ ] Surface 15 memory-health: {reason}")
                fails.append((15, "memory-health row", reason))
        else:
            reason = f"issue_count={ic} card_rendered={card} rendered_item_count={rendered_n} — data/DOM mismatch"
            print(f"[ ✗ ] Surface 15 memory-health: {reason}")
            fails.append((15, "memory-health row", reason))

        # ─── Surface 16: Per-file health banner in the memory viewer ─────────
        # The launchpad's memory-health row tells the user "core.md stale".
        # When they click through to inspect core.md, the same warning has
        # to travel with the file. Tick #13 shipped this; Surface 16 is the
        # regression guard. Strategy:
        #   - Read the inlined memory-health payload (already populated by
        #     render_memory_viewer_html).
        #   - For the FIRST file with a stale issue, navigate to its viewer,
        #     assert banner present + chip works (mirrors Surface 15).
        #   - Also navigate to a known-healthy file (whichever the payload
        #     reports as fresh) and assert banner is ABSENT.
        # Skip cleanly when all four signals are fresh — same silent-fresh
        # semantics as Surface 15.
        stale_target = None
        healthy_target = None
        if ic > 0:
            stale_target = (health_state.get("first_name") or "").strip() or None
        # Healthy = a memory NOT in the issues list. Pick lens.md if no issue
        # mentions it, else picks.json, else core.md (one of these is always
        # in the file set unless the install is completely empty).
        if ic == 0:
            healthy_target = "lens.md"
        else:
            issue_names = set()
            issue_names_state = page.evaluate(
                """() => {
                  const script = document.getElementById('page-data');
                  const data = script ? JSON.parse(script.textContent || '{}') : {};
                  return ((data.memoryHealth || {}).issues || []).map(i => i.name);
                }"""
            )
            issue_names = set(issue_names_state or [])
            for candidate in ("lens.md", "picks.json", "vocabulary.md", "core.md"):
                if candidate not in issue_names:
                    healthy_target = candidate
                    break

        if stale_target:
            page.goto(
                f"{base_url}/portal_pages/memory.html?file={stale_target}",
                wait_until="networkidle",
                timeout=10000,
            )
            page.wait_for_timeout(800)
            banner_check = page.evaluate(
                """() => new Promise(resolve => {
                  const banner = document.querySelector('.viewer-health-banner');
                  // Tick #18: nav chip for the same file should also carry
                  // a stale-dot indicator. Collect alongside banner state.
                  const navStale = Array.from(document.querySelectorAll('.memory-nav-link-stale'))
                    .map(a => a.dataset.file);
                  const navDots = document.querySelectorAll('.memory-nav-dot').length;
                  if (!banner) { resolve({present: false, navStale, navDots}); return; }
                  const chip = document.querySelector('.viewer-health-cmd');
                  if (!chip) { resolve({present: true, chip: false, navStale, navDots}); return; }
                  const expected = chip.textContent.trim();
                  let captured = null;
                  const orig = navigator.clipboard?.writeText;
                  if (orig) navigator.clipboard.writeText = async (t) => { captured = t; return Promise.resolve(); };
                  chip.click();
                  setTimeout(() => {
                    const after = chip.textContent.trim();
                    if (orig) navigator.clipboard.writeText = orig;
                    resolve({
                      present: true,
                      chip: true,
                      expected,
                      captured,
                      matches: captured === expected,
                      flipped: after.startsWith('✓'),
                      navStale,
                      navDots,
                    });
                  }, 200);
                })"""
            )
            # Tick #18: stale-dot indicator on the matching nav chip
            # should match the banner state. Nav dot count > 0 AND the
            # current file's chip is flagged stale.
            nav_stale_ok = (
                stale_target in (banner_check.get("navStale") or [])
                and (banner_check.get("navDots") or 0) > 0
            )
            stale_ok = (
                banner_check.get("present")
                and (
                    not banner_check.get("chip")  # banner without command (href-only) still valid
                    or (banner_check.get("matches") and banner_check.get("flipped"))
                )
                and nav_stale_ok
            )
        else:
            stale_ok = True
            banner_check = {"skipped": "all signals fresh on launchpad"}

        healthy_ok = True
        healthy_present = None
        if healthy_target:
            page.goto(
                f"{base_url}/portal_pages/memory.html?file={healthy_target}",
                wait_until="networkidle",
                timeout=10000,
            )
            page.wait_for_timeout(500)
            healthy_present = page.evaluate(
                "() => !!document.querySelector('.viewer-health-banner')"
            )
            healthy_ok = not healthy_present

        if stale_ok and healthy_ok:
            note = []
            if stale_target:
                if banner_check.get("chip"):
                    nav_n = banner_check.get("navDots") or 0
                    note.append(f"stale={stale_target} (chip works · {nav_n} nav dot{'s' if nav_n != 1 else ''})")
                else:
                    note.append(f"stale={stale_target} (href-only)")
            else:
                note.append("no stale files")
            if healthy_target:
                note.append(f"healthy={healthy_target} (silent)")
            print(f"[ ✓ ] Surface 16 per-file health banner: {' · '.join(note)}")
        else:
            reason = f"stale_target={stale_target} banner={banner_check} healthy_target={healthy_target} healthy_present={healthy_present}"
            print(f"[ ✗ ] Surface 16 per-file health banner: {reason}")
            fails.append((16, "per-file health banner", reason))

        # (Surface 17 — the pick-veto chip — retired 2026-06-05 with the
        # user-pick layer. There is no per-rule veto; the chairman's pick
        # stands alone. Surfaces keep their original numbers; 17 is absent.)

        # ─── Surface 18: Persistent rebuild chip in viewer header ────────────
        # Every memory's header carries a .viewer-rebuild-chip that copies
        # the corresponding trinity-local subcommand. This is the always-on
        # action affordance (vs the staleness chip which only fires when
        # _memory_health flags an issue). Walk three memories with distinct
        # rebuild CLIs to verify the per-file mapping isn't broken.
        rebuild_targets = [
            ("lens.md", "trinity-local lens"),
            ("picks.json", "trinity-local consolidate"),
            # core.md previously suggested `distill` but the CLI was
            # hidden in commit c9b1f9d; the rebuild chip now points at
            # the live path (dream Phase 5 handles distillation).
            ("core.md", "trinity-local dream"),
        ]
        rebuild_results = []
        for file_name, expected_cmd in rebuild_targets:
            page.goto(
                f"{base_url}/portal_pages/memory.html?file={file_name}",
                wait_until="networkidle",
                timeout=10000,
            )
            page.wait_for_timeout(200)
            r = page.evaluate(
                """(expected) => new Promise(resolve => {
                  const chip = document.querySelector('.viewer-rebuild-chip');
                  if (!chip) { resolve({ok: false, reason: 'no .viewer-rebuild-chip in header'}); return; }
                  let copied = null;
                  const orig = navigator.clipboard?.writeText;
                  if (orig) navigator.clipboard.writeText = async (t) => { copied = t; return Promise.resolve(); };
                  chip.click();
                  setTimeout(() => {
                    if (orig) navigator.clipboard.writeText = orig;
                    resolve({
                      ok: copied === expected,
                      copied: copied,
                      expected: expected,
                      file: chip.dataset.file,
                      flashed: chip.textContent.includes('Copied'),
                    });
                  }, 200);
                })""",
                expected_cmd,
            )
            rebuild_results.append((file_name, r))
        page.screenshot(path=str(SHOTS_DIR / "18-rebuild-chip.png"))
        all_ok = all(r.get("ok") for _, r in rebuild_results)
        if all_ok:
            note = " · ".join(f"{f}→ok" for f, _ in rebuild_results)
            print(f"[ ✓ ] Surface 18 rebuild chip: {note} (all 3 memories)")
        else:
            failures = [(f, r) for f, r in rebuild_results if not r.get("ok")]
            reason = "; ".join(
                f"{f}: copied={r.get('copied')!r} expected={r.get('expected')!r} ({r.get('reason') or 'mismatch'})"
                for f, r in failures
            )
            print(f"[ ✗ ] Surface 18 rebuild chip: {reason}")
            fails.append((18, "rebuild chip", reason))

        # ─── Surface 19: Topic-graph launch-council chip ─────────────────────
        # The topic graph node detail panel now carries a .topics-launch-chip
        # that copies a `trinity-local council --task "<headline>"`
        # command, using the closest-to-centroid representative as the seed.
        # Gracefully SKIP if topics.json has no representatives (legacy
        # schema or empty install) — the chip can't render without a seed.
        # First, count real basins so a 0-node graph on a POPULATED corpus
        # fails loudly instead of silently skipping (see _topics_basin_count).
        topics_basins = _topics_basin_count()
        page.goto(
            f"{base_url}/portal_pages/memory.html?file=topics.json",
            wait_until="networkidle",
            timeout=10000,
        )
        page.wait_for_timeout(400)  # wait for d3-force to attach nodes
        launch_state = page.evaluate(
            """() => new Promise(resolve => {
              const nodes = document.querySelectorAll('.topics-graph-svg .node');
              if (!nodes.length) { resolve({ok: true, skipped: true, reason: 'no topic-graph nodes (empty topics.json)'}); return; }
              // Click the first node to open detail panel (d3-injected click).
              const evt = new MouseEvent('click', {bubbles: true});
              nodes[0].dispatchEvent(evt);
              setTimeout(() => {
                const chip = document.querySelector('.topics-launch-chip');
                if (!chip) {
                  // No chip — either basin has no representative or wiring broken.
                  // Treat "no rep" as SKIPPED; anything else is a failure.
                  const reps = document.querySelectorAll('.topics-graph-detail .topics-reps-list li');
                  if (!reps.length) { resolve({ok: true, skipped: true, reason: 'first basin has no representatives'}); return; }
                  resolve({ok: false, reason: 'detail panel has reps but no .topics-launch-chip'});
                  return;
                }
                let copied = null;
                const orig = navigator.clipboard?.writeText;
                if (orig) navigator.clipboard.writeText = async (t) => { copied = t; return Promise.resolve(); };
                chip.click();
                setTimeout(() => {
                  if (orig) navigator.clipboard.writeText = orig;
                  resolve({
                    ok: copied && copied.startsWith('trinity-local council --task "'),
                    copied: (copied || '').slice(0, 80),
                    flashed: chip.textContent.includes('Copied'),
                    basin: chip.dataset.basin,
                  });
                }, 200);
              }, 200);
            })"""
        )
        page.screenshot(path=str(SHOTS_DIR / "19-topic-launch-chip.png"))
        if launch_state.get("skipped"):
            # Distinguish an EMPTY install (legit skip) from a BROKEN d3 render.
            # The JS skips on 0 graph nodes assuming "empty topics.json", but if
            # topics.json actually carries basins the graph SHOULD have nodes —
            # 0 nodes then means the d3 data-binding broke and every topology
            # surface (19/21/22) would silently skip-as-green on real data.
            if "no topic-graph nodes" in (launch_state.get("reason") or "") and topics_basins > 0:
                reason = (
                    f"topology graph rendered 0 nodes despite topics.json carrying "
                    f"{topics_basins} basins — d3 data-binding broke (NOT an empty "
                    f"install); surfaces 19/21/22 would mask this by skipping"
                )
                print(f"[ ✗ ] Surface 19 topic-launch: {reason}")
                fails.append((19, "topology graph render", reason))
            else:
                print(f"[ - ] Surface 19 topic-launch: SKIPPED ({launch_state['reason']})")
        elif launch_state.get("ok"):
            print(
                f"[ ✓ ] Surface 19 topic-launch: basin={launch_state['basin']!r} "
                f"copied='{launch_state['copied']}...' (flash={launch_state['flashed']})"
            )
        else:
            reason = launch_state.get("reason") or f"copied={launch_state.get('copied')!r}"
            print(f"[ ✗ ] Surface 19 topic-launch: {reason}")
            fails.append((19, "topic-launch chip", reason))

        # ─── Surface 20: Per-representative replay chip + stopPropagation ────
        # The basin detail panel renders one .topics-rep-replay per
        # representative thread, sitting in the rep's headRow. Clicking
        # must (a) copy a council-launch command derived from THIS rep's
        # headline (not the basin's first rep — that's what Surface 19
        # checks) and (b) NOT toggle the surrounding expand state.
        # The basin detail panel is still open from Surface 19's click.
        rep_state = page.evaluate(
            """() => new Promise(resolve => {
              const chips = document.querySelectorAll('.topics-rep-replay');
              if (!chips.length) { resolve({ok: true, skipped: true, reason: 'no .topics-rep-replay chips (basin has no reps)'}); return; }
              // Prefer a chip on a multi-turn (expandable) rep so we can
              // also assert stopPropagation doesn't toggle the expand.
              let target = null;
              for (const c of chips) {
                if (c.closest('.topics-rep.expandable')) { target = c; break; }
              }
              const isExpandable = !!target;
              if (!target) target = chips[0];
              const li = target.closest('.topics-rep');
              const wasOpen = li ? li.classList.contains('open') : false;
              let copied = null;
              const orig = navigator.clipboard?.writeText;
              if (orig) navigator.clipboard.writeText = async (t) => { copied = t; return Promise.resolve(); };
              target.click();
              setTimeout(() => {
                if (orig) navigator.clipboard.writeText = orig;
                const stillSameExpand = li ? (li.classList.contains('open') === wasOpen) : true;
                resolve({
                  ok: copied && copied.startsWith('trinity-local council --task "') && stillSameExpand,
                  copied: (copied || '').slice(0, 90),
                  flashed: target.textContent.includes('Copied'),
                  total_chips: chips.length,
                  is_expandable: isExpandable,
                  expand_unchanged: stillSameExpand,
                });
              }, 200);
            })"""
        )
        page.screenshot(path=str(SHOTS_DIR / "20-rep-replay-chip.png"))
        if rep_state.get("skipped"):
            print(f"[ - ] Surface 20 rep-replay: SKIPPED ({rep_state['reason']})")
        elif rep_state.get("ok"):
            note = (
                f"{rep_state['total_chips']} chip(s) · "
                f"copied='{rep_state['copied']}...' · "
                f"flash={rep_state['flashed']} · "
                f"expandable={rep_state['is_expandable']}, expand-unchanged={rep_state['expand_unchanged']}"
            )
            print(f"[ ✓ ] Surface 20 rep-replay: {note}")
        else:
            reason = f"copied={rep_state.get('copied')!r} expand_unchanged={rep_state.get('expand_unchanged')}"
            print(f"[ ✗ ] Surface 20 rep-replay: {reason}")
            fails.append((20, "rep-replay chip", reason))

        # ─── Surface 21: Topology → picks cross-link ─────────────────────────
        # The basin detail panel renders a .topics-pick-xlink ONLY when
        # this basin has been consolidated into a routing rule (picks
        # carries a .basin_id pointing back). If no basin in the current
        # topology has a corresponding pick, SKIP rather than fail — same
        # legacy-data tolerance pattern as surfaces 17/19/20.
        # Open topics.json fresh so we can scan all basins for one that
        # has a pick link (the basin Surface 19 clicked may not).
        page.goto(
            f"{base_url}/portal_pages/memory.html?file=topics.json",
            wait_until="networkidle",
            timeout=10000,
        )
        page.wait_for_timeout(400)
        link_state = page.evaluate(
            """() => new Promise(resolve => {
              const nodes = Array.from(document.querySelectorAll('.topics-graph-svg .node'));
              if (!nodes.length) { resolve({ok: true, skipped: true, reason: 'no graph nodes'}); return; }
              // Walk basins one-by-one looking for the first that surfaces
              // a .topics-pick-xlink on click. If none do, treat as SKIPPED
              // (this install hasn't consolidated any basin into picks).
              const tryClick = (i) => {
                if (i >= nodes.length) { resolve({ok: true, skipped: true, reason: 'no basin has a pick xlink'}); return; }
                nodes[i].dispatchEvent(new MouseEvent('click', {bubbles: true}));
                setTimeout(() => {
                  const xlink = document.querySelector('.topics-pick-xlink');
                  if (xlink) {
                    const href = xlink.getAttribute('href') || '';
                    resolve({
                      ok: href.startsWith('memory.html?file=picks.json&task='),
                      href: href,
                      label: xlink.textContent,
                      basin_index: i,
                      total_basins: nodes.length,
                    });
                  } else {
                    tryClick(i + 1);
                  }
                }, 150);
              };
              tryClick(0);
            })"""
        )
        page.screenshot(path=str(SHOTS_DIR / "21-topic-pick-xlink.png"))
        if link_state.get("skipped"):
            print(f"[ - ] Surface 21 topic→pick: SKIPPED ({link_state['reason']})")
        elif link_state.get("ok"):
            print(
                f"[ ✓ ] Surface 21 topic→pick: basin #{link_state['basin_index']}/{link_state['total_basins']} "
                f"links to '{link_state['href']}' (label: '{link_state['label']}')"
            )
        else:
            reason = f"href={link_state.get('href')!r}"
            print(f"[ ✗ ] Surface 21 topic→pick: {reason}")
            fails.append((21, "topic→pick xlink", reason))

        # ─── Surface 22: Pick-basin SVG node styling ─────────────────────────
        # Visual companion to Surface 21 — basins with picks should have
        # class="node pick-basin" so they read at a glance. SKIPPED if no
        # basin in the current topology was matched to a pick (same path
        # Surface 21 takes when there are no picks). The topology view is
        # already loaded from Surface 21.
        node_state = page.evaluate(
            """() => {
              const all = document.querySelectorAll('.topics-graph-svg .node');
              const marked = document.querySelectorAll('.topics-graph-svg .node.pick-basin');
              // Also inspect the title element on a pick-basin node — it
              // must surface the routing-rule label for passive discovery.
              let tooltipOk = null;
              if (marked.length) {
                const t = marked[0].querySelector('title');
                tooltipOk = t && /Routing rule:/.test(t.textContent || '');
              }
              return {
                total: all.length,
                marked: marked.length,
                tooltipHasRoutingRule: tooltipOk,
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "22-pick-basin-style.png"))
        if node_state.get("total", 0) == 0:
            print("[ - ] Surface 22 pick-basin styling: SKIPPED (no graph nodes)")
        elif node_state.get("marked", 0) == 0:
            # Topology has nodes but none mapped to picks — same shape as
            # Surface 21's no-pick skip.
            print(
                f"[ - ] Surface 22 pick-basin styling: SKIPPED "
                f"(0/{node_state['total']} nodes matched to picks)"
            )
        else:
            ok = node_state["marked"] > 0 and node_state.get("tooltipHasRoutingRule") is True
            if ok:
                print(
                    f"[ ✓ ] Surface 22 pick-basin styling: "
                    f"{node_state['marked']}/{node_state['total']} nodes carry .pick-basin · "
                    f"tooltip surfaces routing rule"
                )
            else:
                reason = f"marked={node_state['marked']} tooltipRoutingRule={node_state.get('tooltipHasRoutingRule')}"
                print(f"[ ✗ ] Surface 22 pick-basin styling: {reason}")
                fails.append((22, "pick-basin styling", reason))

        # ─── Surface 23: Picks → topology cross-link + ?basin= deep-link ─────
        # Two halves of the bidirectional bridge:
        #   (a) picks Reader renders a 'View in topology →' xlink per
        #       pick with a centroid match
        #   (b) topology view opens that basin's detail panel when
        #       loaded with ?basin=<id>
        # SKIPPED if picks Reader has zero topology xlinks (means the
        # current install has no picks matched to topology basins —
        # same shape as Surface 21/22 skip).
        page.goto(
            f"{base_url}/portal_pages/memory.html?file=picks.json",
            wait_until="networkidle",
            timeout=10000,
        )
        page.wait_for_timeout(300)
        xlink_info = page.evaluate(
            """() => {
              const links = Array.from(document.querySelectorAll('.pick-xlink')).filter(
                a => /View in topology/i.test(a.textContent || '')
              );
              if (!links.length) return {ok: true, skipped: true, reason: 'no topology xlinks rendered (no picks match topology basins)'};
              const href = links[0].getAttribute('href') || '';
              const m = href.match(/[?&]basin=([^&]+)/);
              return {
                ok: !!m,
                total: links.length,
                href: href,
                basin: m ? decodeURIComponent(m[1]) : null,
              };
            }"""
        )
        if xlink_info.get("skipped"):
            print(f"[ - ] Surface 23 picks→topology: SKIPPED ({xlink_info['reason']})")
        elif not xlink_info.get("ok"):
            reason = f"href={xlink_info.get('href')!r} (no basin param)"
            print(f"[ ✗ ] Surface 23 picks→topology: {reason}")
            fails.append((23, "picks→topology xlink", reason))
        else:
            # Now follow the link and verify the topology view opens the
            # matching basin's detail panel — closes the round trip.
            target_basin = xlink_info["basin"]
            page.goto(
                f"{base_url}/portal_pages/memory.html?file=topics.json&basin={target_basin}",
                wait_until="networkidle",
                timeout=10000,
            )
            page.wait_for_timeout(500)
            page.screenshot(path=str(SHOTS_DIR / "23-picks-to-topology.png"))
            panel_state = page.evaluate(
                """() => {
                  const detail = document.querySelector('.topics-graph-detail');
                  const empty = detail && detail.querySelector('.empty');
                  const basinSpan = detail && detail.querySelector('.basin-id');
                  // Highlight check — at least one node should be at opacity 1
                  // and at least one at <1 if highlightNeighborhood ran.
                  const opacities = Array.from(document.querySelectorAll('.topics-graph-svg .node'))
                    .map(n => Number(getComputedStyle(n).opacity) || 1);
                  const dimmed = opacities.filter(o => o < 0.5).length;
                  return {
                    panel_open: !empty && !!basinSpan,
                    panel_basin: basinSpan ? basinSpan.textContent : null,
                    nodes_dimmed: dimmed,
                    // Dimming PREREQUISITE: highlightNeighborhood dims nodes OUTSIDE
                    // the focused basin's neighborhood, so it can only fire when the
                    // focused node HAS non-neighbors — i.e. the graph isn't complete.
                    // The small synthetic seeder (4 orthogonal basins) renders a
                    // COMPLETE graph K4 (6 edges = 4·3/2): every node neighbors every
                    // other, so 0 dimmed is CORRECT there, not a regression. The real
                    // corpus is a large sparse graph, so it must still dim. Report the
                    // node/edge counts so the gate can tell "complete → can't dim"
                    // apart from "sparse but dimming regressed".
                    node_count: document.querySelectorAll('.topics-graph-svg .node').length,
                    link_count: document.querySelectorAll('.topics-graph-svg .link').length,
                  };
                }"""
            )
            _core_ok = (
                panel_state.get("panel_open")
                and panel_state.get("panel_basin") == target_basin
            )
            # Dimming is only POSSIBLE on a non-complete graph (the focused node has
            # a non-neighbor to dim). On a complete graph the cross-link resolving to
            # the right basin IS the pass — dimming can't fire and isn't required.
            _n = panel_state.get("node_count", 0)
            _complete = _n >= 2 and panel_state.get("link_count", 0) >= _n * (_n - 1) / 2
            _dim_ok = panel_state.get("nodes_dimmed", 0) > 0 or _complete
            if _core_ok and _dim_ok:
                _dim_note = (
                    f"{panel_state['nodes_dimmed']} nodes dimmed via highlight"
                    if panel_state.get("nodes_dimmed", 0) > 0
                    else "dimming not exercised (complete graph — no non-neighbors to dim)"
                )
                print(
                    f"[ ✓ ] Surface 23 picks→topology: {xlink_info['total']} xlink(s) · "
                    f"?basin={target_basin} opened detail panel · {_dim_note}"
                )
            else:
                reason = (
                    f"panel_open={panel_state.get('panel_open')} "
                    f"panel_basin={panel_state.get('panel_basin')!r} "
                    f"expected={target_basin!r} "
                    f"nodes_dimmed={panel_state.get('nodes_dimmed')} "
                    f"link_count={panel_state.get('link_count')}"
                )
                print(f"[ ✗ ] Surface 23 picks→topology: {reason}")
                fails.append((23, "picks→topology xlink", reason))

        # ─── Surface 24: Routing → topology chip ─────────────────────────────
        # The routing table renders a small .routing-topology-chip next
        # to task names whose task_type maps to a topology basin (via
        # the shared taskToBasinId map). Closes the routing/picks/topology
        # cross-link triangle. SKIPPED if zero rows have a chip (means
        # this install has no picks bridging routing↔topology).
        page.goto(
            f"{base_url}/portal_pages/memory.html?file=routing.json",
            wait_until="networkidle",
            timeout=10000,
        )
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS_DIR / "24-routing-to-topology.png"))
        chip_state = page.evaluate(
            """() => {
              const chips = Array.from(document.querySelectorAll('a.routing-topology-chip'));
              if (!chips.length) return {ok: true, skipped: true, reason: 'no routing rows have a topology match'};
              const href = chips[0].getAttribute('href') || '';
              const m = href.match(/[?&]basin=([^&]+)/);
              const rows = document.querySelectorAll('table.routing-table tbody tr');
              return {
                ok: !!m && href.includes('topics.json'),
                chip_count: chips.length,
                row_count: rows.length,
                href: href,
                basin: m ? decodeURIComponent(m[1]) : null,
              };
            }"""
        )
        if chip_state.get("skipped"):
            print(f"[ - ] Surface 24 routing→topology: SKIPPED ({chip_state['reason']})")
        elif chip_state.get("ok"):
            print(
                f"[ ✓ ] Surface 24 routing→topology: "
                f"{chip_state['chip_count']}/{chip_state['row_count']} rows chip-linked "
                f"to topology · first → basin={chip_state['basin']}"
            )
        else:
            reason = f"href={chip_state.get('href')!r}"
            print(f"[ ✗ ] Surface 24 routing→topology: {reason}")
            fails.append((24, "routing→topology chip", reason))

        # ─── Surface 25: Recent-card cross-memory chips (RETIRED — regression guard) ──
        # Sunset 2026-05-21 alongside the rest of the user-rating UX
        # (see launchpad_data.py:1764). Each recent-council card used to
        # grow → pick / → routing / → topology / → share PNG chips; the
        # card is now just title / winner / date per "chairman picks;
        # user refines" prime directive. Iter #101 cleaned the orphaned
        # build-once cache; iter #103 converts this surface to a
        # retirement guard (Principle #14 sunset-pattern, mirrors
        # Surface 32's rate-limit-saves regression). Catches accidental
        # re-introduction — same retired-feature shape iter #73 used.
        page.goto(
            f"{base_url}/portal_pages/launchpad.html",
            wait_until="networkidle",
            timeout=10000,
        )
        page.wait_for_timeout(300)
        xlink_state = page.evaluate(
            """() => {
              // The retired chips (→ pick / → routing / → topology / → share)
              // lived on each recent-council card as `.council-xlink` anchors.
              // The in-page card grid (.council-card-wrapper) was REMOVED
              // 2026-06-06; councils now live only in the left rail. The
              // retirement intent holds on the surviving surface: a rail row is
              // just title / winner / date, with NO cross-memory chips. Asserting
              // against the live rail (not the deleted grid) keeps this guard
              // meaningful instead of vacuously-true over 0 cards.
              const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
              const xlinks_in_rows = [];
              for (const row of rows) {
                xlinks_in_rows.push(...row.querySelectorAll('.council-xlink'));
              }
              // The deleted grid must also stay deleted (no accidental re-introduction).
              const grid_resurrected = document.querySelectorAll('.council-card-wrapper').length;
              return {
                ok: xlinks_in_rows.length === 0 && grid_resurrected === 0,
                count: xlinks_in_rows.length,
                card_count: rows.length,
                grid_resurrected,
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "25-recent-card-no-xlinks.png"))
        if xlink_state.get("ok"):
            print(
                f"[ ✓ ] Surface 25 recent-council cross-memory chips: "
                f"retired (0 .council-xlink in {xlink_state['card_count']} rail rows; grid stays deleted)"
            )
        else:
            reason = (
                f".council-xlink count={xlink_state.get('count')}, "
                f"grid_resurrected={xlink_state.get('grid_resurrected')} "
                f"(expected 0/0 — chips sunset 2026-05-21, grid removed 2026-06-06)"
            )
            print(f"[ ✗ ] Surface 25 recent-council cross-memory chips: {reason}")
            fails.append((25, "recent-council cross-memory chips retired", reason))

        # ─── Surface 26: Cortex picks card → topology chip ───────────────────
        # The cortex picks table on the launchpad annotates each row with
        # a → topology chip when the rule's basin_id maps to a topology
        # basin (same matcher as Surfaces 24 + 25). SKIPPED when no
        # cortex rule matches a topology basin (cold install or no
        # consolidation). The launchpad is already loaded from Surface 25.
        cortex_chip_state = page.evaluate(
            """() => {
              const chips = Array.from(document.querySelectorAll('a.cortex-topology-chip'));
              if (!chips.length) return {ok: true, skipped: true, reason: 'no cortex rows have a → topology chip'};
              const href = chips[0].getAttribute('href') || '';
              const m = href.match(/[?&]basin=([^&]+)/);
              return {
                ok: !!m && href.includes('topics.json'),
                total: chips.length,
                href: href,
                basin: m ? decodeURIComponent(m[1]) : null,
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "26-cortex-topology-chip.png"))
        if cortex_chip_state.get("skipped"):
            print(f"[ - ] Surface 26 cortex→topology: SKIPPED ({cortex_chip_state['reason']})")
        elif cortex_chip_state.get("ok"):
            print(
                f"[ ✓ ] Surface 26 cortex→topology: "
                f"{cortex_chip_state['total']} chip(s) · first → basin={cortex_chip_state['basin']}"
            )
        else:
            reason = f"href={cortex_chip_state.get('href')!r}"
            print(f"[ ✗ ] Surface 26 cortex→topology: {reason}")
            fails.append((26, "cortex→topology chip", reason))

        # ─── Surface 27: Lens card → topology basin chips ────────────────────
        # Each paired lens carries basins_spanned[] (topology basins the
        # tension lives across). The lens card now renders each id as a
        # .lens-basin-chip deep-link to topics.html?basin=<id>. SKIPPED
        # when no paired lens has basins_spanned (legacy data — early
        # lens-build runs didn't populate the field).
        chip_state = page.evaluate(
            """() => {
              const chips = Array.from(document.querySelectorAll('a.lens-basin-chip'));
              if (!chips.length) return {ok: true, skipped: true, reason: 'no .lens-basin-chip on the page (legacy lenses?)'};
              const href = chips[0].getAttribute('href') || '';
              const m = href.match(/[?&]basin=([^&]+)/);
              // tick #38: tooltip should surface basin top-terms when
              // topics.json carries them. Falls back to "Open basin <id>".
              const tooltip = chips[0].getAttribute('title') || '';
              return {
                ok: !!m && href.includes('topics.json'),
                total: chips.length,
                href: href,
                basin: m ? decodeURIComponent(m[1]) : null,
                tooltip: tooltip,
                tooltip_has_terms: /Basin .* — /.test(tooltip),
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "27-lens-basin-chips.png"))
        if chip_state.get("skipped"):
            # Distinguish legacy lenses (no basins_spanned → legit skip) from a
            # BROKEN chip render: a lens.md with N basin-spanning tensions MUST
            # produce .lens-basin-chips, so 0 there means the render broke.
            _spans_n = _lens_basin_span_count()
            if _spans_n > 0:
                reason = (
                    f"0 .lens-basin-chip rendered despite lens.md holding {_spans_n} "
                    f"basin-spanning tensions — chip render broke (NOT legacy lenses)"
                )
                print(f"[ ✗ ] Surface 27 lens→basin: {reason}")
                fails.append((27, "lens-basin chip render", reason))
            else:
                print(f"[ - ] Surface 27 lens→basin: SKIPPED ({chip_state['reason']})")
        elif chip_state.get("ok"):
            tooltip_note = (
                " · tooltip has top-terms" if chip_state.get("tooltip_has_terms")
                else " · tooltip is fallback (basin label map empty)"
            )
            print(
                f"[ ✓ ] Surface 27 lens→basin: {chip_state['total']} chip(s) · "
                f"first → basin={chip_state['basin']}{tooltip_note}"
            )
        else:
            reason = f"href={chip_state.get('href')!r}"
            print(f"[ ✗ ] Surface 27 lens→basin: {reason}")
            fails.append((27, "lens→basin chips", reason))

        # ─── Surface 28: Stale-basin banner ──────────────────────────────────
        # Navigate to topics.html with a fabricated basin id that won't
        # exist in the current topology (a stale lens chip would land
        # like this). The detail panel must surface a "not found"
        # banner with a rebuild chip, NOT silently render the empty
        # "click a basin" message.
        stale_basin_id = "b__stale_unlikely_to_exist__zzz999"
        page.goto(
            f"{base_url}/portal_pages/memory.html?file=topics.json&basin={stale_basin_id}",
            wait_until="networkidle",
            timeout=10000,
        )
        page.wait_for_timeout(400)
        stale_state = page.evaluate(
            """() => {
              const banner = document.querySelector('.topics-graph-detail .viewer-health-banner');
              if (!banner) return {ok: false, reason: 'no stale-basin banner rendered'};
              const status = banner.querySelector('.viewer-health-status');
              const cmd = banner.querySelector('.viewer-health-cmd');
              const hint = banner.querySelector('.viewer-health-hint');
              return {
                ok: !!status && !!cmd && /not found/i.test(status.textContent || ''),
                status: status?.textContent || null,
                cmd_label: cmd?.textContent || null,
                hint_excerpt: (hint?.textContent || '').slice(0, 80),
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "28-stale-basin-banner.png"))
        if stale_state.get("ok"):
            print(
                f"[ ✓ ] Surface 28 stale-basin banner: "
                f"status={stale_state['status']!r} cmd={stale_state['cmd_label']!r}"
            )
        else:
            reason = stale_state.get("reason") or f"status={stale_state.get('status')!r}"
            print(f"[ ✗ ] Surface 28 stale-basin banner: {reason}")
            fails.append((28, "stale-basin banner", reason))

        # ─── Surface 29: Handoff demo nudge banner ────────────────────────────
        # The launchpad-side mirror of the doctor 'try this next' hint
        # (post-#115 tick). pageData.handoffNudge.applicable gates whether
        # the banner renders. When ≥2 CLI-class providers are enabled AND
        # ≥1 prompt is indexed, the banner surfaces the demo command with
        # a non-claude target. When the conditions aren't met, the banner
        # is silent — both behaviors are valid; the check is that data
        # and DOM agree (no orphan render, no silent omission).
        if "launchpad.html" not in page.url:
            page.goto(f"{base_url}/portal_pages/launchpad.html", wait_until="networkidle", timeout=10000)
            page.wait_for_timeout(600)
        nudge_state = page.evaluate(
            """() => {
              const script = document.getElementById('page-data');
              const data = script ? JSON.parse(script.textContent || '{}') : {};
              const nudge = data.handoffNudge || {};
              // Find the banner: it carries the eyebrow text 'Try the 60-second demo'
              const eyebrows = Array.from(document.querySelectorAll('.eyebrow'));
              const eyebrow = eyebrows.find(el => /Try the 60-second demo/i.test(el.textContent || ''));
              const banner = eyebrow ? eyebrow.closest('section.card') : null;
              const code = banner ? banner.querySelector('code') : null;
              return {
                data_applicable: !!nudge.applicable,
                data_target: nudge.target || null,
                data_source_count: nudge.source_count || 0,
                banner_rendered: !!banner,
                command_text: code ? code.textContent.trim() : null,
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "29-handoff-nudge.png"))
        applicable = nudge_state.get("data_applicable")
        rendered = nudge_state.get("banner_rendered")
        # Data/DOM agreement is the load-bearing invariant — both true
        # or both false. A drift (data says yes, banner missing) means
        # the v-if guard regressed; the opposite means a stale banner
        # got left in the template.
        if applicable and rendered:
            target = nudge_state.get("data_target")
            cmd = nudge_state.get("command_text") or ""
            target_in_cmd = target and target in cmd
            if not target_in_cmd:
                reason = f"banner rendered but target {target!r} missing from command {cmd!r}"
                print(f"[ ✗ ] Surface 29 handoff nudge: {reason}")
                fails.append((29, "handoff nudge banner", reason))
            else:
                print(
                    f"[ ✓ ] Surface 29 handoff nudge: target={target!r} "
                    f"sources={nudge_state['data_source_count']} command rendered"
                )
        elif not applicable and not rendered:
            print("[ ✓ ] Surface 29 handoff nudge: silent (conditions not met)")
        else:
            reason = (
                f"data/DOM mismatch: applicable={applicable} rendered={rendered} "
                f"— v-if guard regressed in one direction"
            )
            print(f"[ ✗ ] Surface 29 handoff nudge: {reason}")
            fails.append((29, "handoff nudge banner", reason))

        # ─── Surface 30: Personalized benchmark (eval summary) card ──────────
        # The launchpad-side surface for the eval harness (#122 / #116).
        # Card is ALWAYS rendered (empty state OR populated). When no
        # eval results exist on disk, the card shows the CTA — flavored
        # depending on whether the user has built an eval set yet:
        #   no set     → "trinity-local eval-build" + "trinity-local eval-run --target antigravity"
        #   set exists → "trinity-local eval-run --target antigravity"
        # (Slug `gemini` was the pre-task-#127 target name; the rebrand
        # flipped it to `antigravity` to match config.json's provider
        # dict. The card text now uses pageData.evalSummary.target which
        # comes from the live results, so example provider in this
        # comment is illustrative, not load-bearing.)
        # When results exist, the card shows the per-axis breakdown with
        # tabular-numeric bars.
        eval_state = page.evaluate(
            """() => {
              const script = document.getElementById('page-data');
              const data = script ? JSON.parse(script.textContent || '{}') : {};
              const summary = data.evalSummary || {};
              // When evalSummary.has_results, the template emits TWO
              // "Personalized benchmark" eyebrow chips — one in the
              // <details><summary> collapsed-pill header, one inside
              // the wrapped <section.card>. The summary chip has no
              // section.card ancestor (it's a sibling within details),
              // so the old eyebrow-then-closest() approach picked the
              // first match and got null. Query the card directly by
              // its dedicated class — added precisely for this anchor.
              const card = document.querySelector('section.eval-summary-card');
              const eyebrow = card ? card.querySelector('.eyebrow') : null;
              const headline = card ? card.querySelector('h2') : null;
              const codes = card ? Array.from(card.querySelectorAll('code')).map(c => c.textContent) : [];
              return {
                has_results: !!summary.has_results,
                eval_set_available: !!summary.eval_set_available,
                target: summary.target || null,
                // Headline renders `target_display || target` (#275 branding:
                // antigravity→Gemini, codex→GPT). The smoke must check the
                // SAME branded string the template shows, not the raw slug.
                target_display: summary.target_display || null,
                axes_count: (summary.axes || []).length,
                card_rendered: !!card,
                headline: headline ? headline.textContent.replace(/\\s+/g, ' ').trim().slice(0, 80) : null,
                cta_commands: codes,
              };
            }"""
        )
        page.screenshot(path=str(SHOTS_DIR / "30-eval-summary.png"))
        if eval_state.get("has_results"):
            # Populated branch: axes rendered + the BRANDED target visible in
            # the headline. The h2 renders `target_display || target` (#275:
            # antigravity→Gemini, codex→GPT, claude→Claude), so assert against
            # the same fallback chain — asserting the raw slug would falsely
            # fail whenever the brand display name differs from the slug.
            target = eval_state.get("target")
            expected_label = eval_state.get("target_display") or target
            target_ok = bool(expected_label) and expected_label in (eval_state.get("headline") or "")
            axes_ok = eval_state.get("axes_count", 0) > 0
            if not eval_state.get("card_rendered"):
                reason = "eval summary card missing despite has_results=true"
                print(f"[ ✗ ] Surface 30 eval summary: {reason}")
                fails.append((30, "eval summary card", reason))
            elif target_ok and axes_ok:
                print(
                    f"[ ✓ ] Surface 30 eval summary: populated · target={target!r} "
                    f"axes={eval_state['axes_count']}"
                )
            else:
                reason = f"populated but target_ok={target_ok} axes_ok={axes_ok}"
                print(f"[ ✗ ] Surface 30 eval summary: {reason}")
                fails.append((30, "eval summary card", reason))
        else:
            # No eval results yet — card is intentionally silent (the empty-
            # state CTA branch was killed; users discover eval-run via README).
            print(
                "[ ✓ ] Surface 30 eval summary: silent (no results yet — "
                "empty-state card retired)"
            )

        # ─── (Surface 31 reserved — never shipped) ────────────────────────────
        # The IDs are stable across releases; nothing was numbered 31. Gap
        # preserved so existing references to Surface 32/33 don't shift.
        # `scripts/render_docs.canonical_smoke_surface_count()` counts what
        # exists (34 distinct labels: 1, 1b, 2–13, 14a, 14b, 15–30, 32, 33),
        # which is correct — the count tracks reality, not ID density.

        # ─── Surface 32: Rate-limit-saves card (RETIRED — regression guard) ──
        # The card + its backing data field (`_rate_limit_saves` +
        # `pageData.rateLimitSaves`) were retired 2026-05-21 alongside
        # the rest of the rating UX (see `retired_names.py`). The card
        # used to surface a Day-1 launch metric ("# of times Trinity
        # routed around a rate-limited primary"); the launchpad surface
        # was deleted because the rate-prompt friction it shipped under
        # didn't earn its keep. Successor: `eval-run` corpus-based
        # benchmark, surfaced via the Personalized Benchmark card
        # (Surface 30).
        #
        # This surface is preserved as a REGRESSION GUARD: assert the
        # card stays absent. A future PR that re-adds `pageData
        # .rateLimitSaves` (or any `.eyebrow` saying "Rate-limit saves")
        # trips this surface and announces the retirement was reversed
        # without going through the retired_names.py registry first.
        rate_limit_state = page.evaluate(
            """() => {
              const script = document.getElementById('page-data');
              const data = script ? JSON.parse(script.textContent || '{}') : {};
              const eyebrows = Array.from(document.querySelectorAll('.eyebrow'));
              const eyebrow = eyebrows.find(el => /Rate-limit saves/i.test(el.textContent || ''));
              const card = eyebrow ? eyebrow.closest('section.card') : null;
              return {
                pagedata_field_present: 'rateLimitSaves' in data,
                card_rendered: !!card,
              };
            }"""
        )
        if rate_limit_state.get("pagedata_field_present"):
            reason = (
                "pageData.rateLimitSaves field re-introduced — was retired "
                "2026-05-21 (see retired_names.py). If this is intentional, "
                "remove the retirement entry first."
            )
            print(f"[ ✗ ] Surface 32 rate-limit saves: {reason}")
            fails.append((32, "rate-limit saves card", reason))
        elif rate_limit_state.get("card_rendered"):
            reason = (
                "rate-limit-saves card re-rendered on launchpad — was retired "
                "2026-05-21 (see retired_names.py)."
            )
            print(f"[ ✗ ] Surface 32 rate-limit saves: {reason}")
            fails.append((32, "rate-limit saves card", reason))
        else:
            print("[ ✓ ] Surface 32 rate-limit saves: card stays retired (2026-05-21)")

        # ─── Surface 33: Browser-capture card (v1.6 silent-breakage signal) ──
        # Per docs/spec-v1.6.md line 479-497. Card surfaces capture
        # activity so silent breakage of the browser extension is
        # VISIBLE within 24h (stale flag → warning border).
        #
        # Card is conditional: renders in EITHER state when
        # `pageData.browserCapture` is set. Two valid outcomes:
        #   has_data=true  → populated card with per-provider bars
        #   has_data=false → empty-state CTA card with install command
        # Unlike Surface 32 (silent on empty), Surface 33 SHOWS the
        # empty state because there's a user action available (install
        # the extension).
        browser_capture_state = page.evaluate(
            """() => {
              const script = document.getElementById('page-data');
              const data = script ? JSON.parse(script.textContent || '{}') : {};
              const cap = data.browserCapture || {};
              const cards = Array.from(document.querySelectorAll('section.browser-capture-card'));
              return {
                has_data: !!cap.has_data,
                total_captured: cap.total_captured || 0,
                stale: !!cap.stale,
                provider_count: (cap.providers || []).length,
                card_count: cards.length,
                install_command: cap.install_command || null,
                card_has_install_pre: cards.some(c =>
                  /trinity-local install-extension/.test(c.textContent || '')
                ),
              };
            }"""
        )
        if browser_capture_state.get("card_count") == 0:
            reason = (
                "browserCapture card not rendered in EITHER state. "
                "Card should always appear — populated when captures exist, "
                "CTA when they don't."
            )
            print(f"[ ✗ ] Surface 33 browser capture: {reason}")
            fails.append((33, "browser capture card", reason))
        elif browser_capture_state.get("has_data"):
            # Populated state must mention the per-provider bar count
            if browser_capture_state.get("provider_count", 0) < 1:
                reason = (
                    f"has_data=true but providers list empty: "
                    f"{browser_capture_state}"
                )
                print(f"[ ✗ ] Surface 33 browser capture: {reason}")
                fails.append((33, "browser capture card", reason))
            else:
                stale_tag = " STALE" if browser_capture_state.get("stale") else ""
                print(
                    f"[ ✓ ] Surface 33 browser capture: populated · "
                    f"total={browser_capture_state['total_captured']} "
                    f"providers={browser_capture_state['provider_count']}{stale_tag}"
                )
        else:
            # Empty-state branch MUST surface the install command — that's
            # the user action the card exists to suggest.
            if not browser_capture_state.get("card_has_install_pre"):
                reason = (
                    "has_data=false but card doesn't surface "
                    "`trinity-local install-extension` — the CTA the empty "
                    "state exists for is missing."
                )
                print(f"[ ✗ ] Surface 33 browser capture: {reason}")
                fails.append((33, "browser capture card", reason))
            else:
                print("[ ✓ ] Surface 33 browser capture: empty state with install CTA")

        # ─── Surface 34: Launchpad topology deep-link RESOLVES ───────────────
        # Surface 3 asserts the launchpad EMITS the memory.html?file=...&basin=
        # deep-links (string-presence only). This pairs that with a real
        # navigation — the dogfood lesson: never ship a string-presence assert
        # un-paired with a browser hop. The launchpad's topology chips derive
        # their basin id from launchpad_data._task_to_topology_basin, a
        # DIFFERENT bridge than the picks-Reader xlink Surface 23 follows, so a
        # drift there (an emitted basin id absent from topics.json) lands the
        # user on a silent "not found" banner no other surface catches.
        # SKIPPED when the launchpad emits no ?basin= chip (un-consolidated
        # install — same shape as Surface 21/23).
        page.goto(
            f"{base_url}/portal_pages/launchpad.html",
            wait_until="networkidle",
            timeout=15000,
        )
        page.wait_for_timeout(1200)
        lp_link = page.evaluate(
            """() => {
              const a = Array.from(document.querySelectorAll(
                'a[href*="memory.html?file=topics.json&basin="]'))[0];
              if (!a) return null;
              const href = a.getAttribute('href') || '';
              const m = href.match(/[?&]basin=([^&]+)/);
              return {
                qs: href.split('memory.html')[1] || '',
                basin: m ? decodeURIComponent(m[1]) : null,
              };
            }"""
        )
        if not lp_link:
            print(
                "[ - ] Surface 34 launchpad deep-link: SKIPPED "
                "(no ?basin= topology chip emitted — un-consolidated install)"
            )
        else:
            page.goto(
                f"{base_url}/portal_pages/memory.html{lp_link['qs']}",
                wait_until="networkidle",
                timeout=10000,
            )
            page.wait_for_timeout(700)
            page.screenshot(path=str(SHOTS_DIR / "34-launchpad-deeplink.png"))
            res = page.evaluate(
                """() => {
                  const stale = Array.from(document.querySelectorAll('.viewer-health-status'))
                    .map(e => (e.textContent || '').trim().toLowerCase())
                    .filter(s => s === 'not found' || s === 'not yet');
                  const detail = document.querySelector('.topics-graph-detail');
                  const basinSpan = detail && detail.querySelector('.basin-id');
                  const opac = Array.from(document.querySelectorAll('.topics-graph-svg .node'))
                    .map(n => Number(getComputedStyle(n).opacity) || 1);
                  return {
                    stale: stale,
                    panel_open: !!(detail && basinSpan),
                    panel_basin: basinSpan ? basinSpan.textContent : null,
                    nodes_dimmed: opac.filter(o => o < 0.5).length,
                    // Dimming can only fire on a NON-complete graph (see Surface 23):
                    // the synthetic seeder's K4 has no non-neighbors to dim, so 0 is
                    // correct there; the real sparse corpus must still dim.
                    node_count: document.querySelectorAll('.topics-graph-svg .node').length,
                    link_count: document.querySelectorAll('.topics-graph-svg .link').length,
                  };
                }"""
            )
            _core_ok = (
                not res.get("stale")
                and res.get("panel_open")
                and res.get("panel_basin") == lp_link["basin"]
            )
            _n = res.get("node_count", 0)
            _complete = _n >= 2 and res.get("link_count", 0) >= _n * (_n - 1) / 2
            _dim_ok = res.get("nodes_dimmed", 0) > 0 or _complete
            if _core_ok and _dim_ok:
                _dim_note = (
                    f"{res['nodes_dimmed']} nodes dimmed"
                    if res.get("nodes_dimmed", 0) > 0
                    else "dimming not exercised (complete graph — no non-neighbors)"
                )
                print(
                    f"[ ✓ ] Surface 34 launchpad deep-link: ?basin={lp_link['basin']} "
                    f"resolved (detail panel + {_dim_note}, no stale banner)"
                )
            else:
                reason = (
                    f"stale={res.get('stale')} panel_open={res.get('panel_open')} "
                    f"panel_basin={res.get('panel_basin')!r} "
                    f"expected={lp_link['basin']!r} dimmed={res.get('nodes_dimmed')} "
                    f"link_count={res.get('link_count')}"
                )
                print(f"[ ✗ ] Surface 34 launchpad deep-link: {reason}")
                fails.append((34, "launchpad deep-link resolution", reason))

        # ─── Surface 35: Live council page renders the PAINKILLER ─────────────
        # The product differentiator is the synthesized cross-provider answer
        # WITH disagreed_claims (where the models diverged). Surfaces 5/9 render
        # the live council but only assert synthesis + quote chips — never the
        # agreed/disagreed claims. Load a real council that recorded a
        # disagreement and assert the "Disagreed claims" section renders its
        # content, AND that the page logs no resource-load (404) error — the
        # thread-manifest probe must not 404 on a standalone council (v1.7.190:
        # `_maybeOfferThreadLink` now only fires when chain_root_id is explicit,
        # so 98.8% of councils stop probing a `_thread_council_<id>.js` that
        # never exists). SKIPPED when no council has a disagreement yet.
        cd = _council_with_disagreed_claims()
        if not cd:
            print("[ - ] Surface 35 council painkiller: SKIPPED (no council with disagreed_claims yet)")
        else:
            pk_crid, pk_claim = cd
            err_before = len(console_errors)
            page.goto(
                f"{base_url}/review_pages/live_council.html?council_id={pk_crid}",
                wait_until="load",
                timeout=15000,
            )
            page.wait_for_timeout(2500)  # outcome-script hydration + petite-vue mount
            page.screenshot(path=str(SHOTS_DIR / "35-council-painkiller.png"))
            pk = page.evaluate(
                """(claimFrag) => {
                  const strongs = Array.from(document.querySelectorAll('strong')).map(e => (e.textContent||'').trim());
                  const body = document.body.textContent || '';
                  return {
                    disagreed_header: strongs.includes('Disagreed claims'),
                    agreed_header: strongs.includes('Agreed claims'),
                    synth: !!document.querySelector('.synthesis-section .markdown-body'),
                    claim_rendered: body.includes(claimFrag),
                  };
                }""",
                pk_claim[:40],
            )
            new_errs = console_errors[err_before:]
            load_fails = [
                e for e in new_errs
                if "Failed to load resource" in e or "404" in e or "ERR_FILE_NOT_FOUND" in e
            ]
            ok = (
                pk.get("disagreed_header")
                and pk.get("synth")
                and pk.get("claim_rendered")
                and not load_fails
            )
            if ok:
                print(
                    f"[ ✓ ] Surface 35 council painkiller: ?council_id={pk_crid[:20]} "
                    f"renders Disagreed claims + synthesis · no thread-probe 404"
                )
            else:
                reason = (
                    f"disagreed_header={pk.get('disagreed_header')} synth={pk.get('synth')} "
                    f"claim_rendered={pk.get('claim_rendered')} load_fails={load_fails[:1]}"
                )
                print(f"[ ✗ ] Surface 35 council painkiller: {reason}")
                fails.append((35, "council painkiller render", reason))

        # ─── Surface 36: No-extension first-run dispatch rolls back + banners ─
        # A freshly pip-installed user (no Chrome extension yet) types a question
        # and clicks Launch Council. The launchpad must NOT get stuck (busy
        # forever / card on QUEUED): it must roll back `.busy`, restore the
        # prompt, and open the install-extension banner. handleDispatchResult's
        # install-prompt path is source-tested (test_launchpad_dispatch_failure_
        # rollback), but petite-vue REACTIVITY — does the button actually
        # re-enable, does the banner actually render? — can only be confirmed in
        # a real browser (the dogfood lesson). We stub __TRINITY_DISPATCH__
        # ATOMICALLY before the click (no real messaging → zero dispatch risk) to
        # simulate "no extension". The transient status-poll 404 for the
        # not-yet-written status file is EXPECTED optimistic-poll behavior
        # (clearOperation stops it), so we assert the FLOW, not console-cleanliness.
        page.goto(f"{base_url}/portal_pages/launchpad.html", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1200)
        page.evaluate(
            """() => {
              window.__TRINITY_DISPATCH__ = {
                dispatch: ({ onResult }) => {
                  if (onResult) onResult({ tier: 'install-prompt', ok: false, reason: 'extension-unreachable' });
                  return Promise.resolve({ tier: 'install-prompt', ok: false });
                },
                probe: () => Promise.resolve('absent'),
                onStateChange: () => (() => {}),
                get state() { return 'absent'; },
              };
            }"""
        )
        if page.query_selector('[placeholder="Ask a council question..."]') is None:
            print("[ - ] Surface 36 no-ext dispatch: SKIPPED (no dispatch input on this build)")
        else:
            typed = "smoke: best standing desk under 400"
            page.fill('[placeholder="Ask a council question..."]', typed)
            page.click('button:has-text("Launch Council")')
            page.wait_for_timeout(900)
            page.screenshot(path=str(SHOTS_DIR / "36-no-ext-dispatch.png"))
            disp = page.evaluate(
                """(typed) => {
                  const launchBtn = Array.from(document.querySelectorAll('button'))
                    .find(b => /Launch Council/.test(b.textContent || ''));
                  const banner = Array.from(document.querySelectorAll('section.memory-health-card'))
                    .find(s => /No dispatch path is wired up|Install the Trinity browser extension/.test(s.textContent || ''));
                  const promptEl = document.querySelector('[placeholder="Ask a council question..."]');
                  return {
                    rolled_back: launchBtn ? launchBtn.disabled === false : null,
                    banner_visible: !!(banner && banner.offsetParent !== null),
                    prompt_restored: promptEl ? promptEl.value === typed : null,
                  };
                }""",
                typed,
            )
            if disp.get("rolled_back") and disp.get("banner_visible") and disp.get("prompt_restored"):
                print(
                    "[ ✓ ] Surface 36 no-ext dispatch: rolled back .busy + showed "
                    "install banner + restored prompt"
                )
            else:
                reason = (
                    f"rolled_back={disp.get('rolled_back')} banner={disp.get('banner_visible')} "
                    f"prompt_restored={disp.get('prompt_restored')}"
                )
                print(f"[ ✗ ] Surface 36 no-ext dispatch: {reason}")
                fails.append((36, "no-extension dispatch rollback", reason))

        # ─── Surface 37: Memory viewer — every file tab renders clean ─────────
        # The viewer renders corpus-derived content (lens.md / topics.json /
        # vocabulary.md / picks.json / routing.json / core.md) via renderMarkdown +
        # the JSON readers + the d3 topology. Other surfaces drive the routing/picks
        # cross-links; this pins that ALL SIX tabs render real content with NO
        # console error / template leak — so a renderMarkdown change, a vendor bump,
        # or a newly-added file breaks the smoke instead of silently blanking a tab.
        # Verified-clean 2026-06-02 (full real-data drive); this converts that into
        # a standing gate.
        # Drive EVERY visible tab — the 6 core memories + the optional generators.md
        # lens-lift tier when it exists (2026-06-05) — from the same contract the
        # viewer renders, so a newly-added file is swept, not silently skipped.
        mem_files = _visible_memory_files()
        mem_bad = []
        for mf in mem_files:
            err_before = len(console_errors)
            page.goto(f"{base_url}/portal_pages/memory.html?file={mf}",
                      wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(800)
            st = page.evaluate(
                "() => ({ bodyLen: (document.body.innerText || '').trim().length,"
                " leak: /\\{\\{|\\}\\}/.test(document.body.innerText || '') })"
            )
            new_errs = console_errors[err_before:]
            if st["bodyLen"] < 50 or st["leak"] or new_errs:
                mem_bad.append(
                    f"{mf}(body={st['bodyLen']}, leak={st['leak']}, errs={new_errs[:1]})"
                )
        if not mem_bad:
            print(f"[ ✓ ] Surface 37 memory tabs: all {len(mem_files)} file tabs "
                  "render clean (no console error / template leak)")
        else:
            reason = "; ".join(mem_bad)
            print(f"[ ✗ ] Surface 37 memory tabs: {reason}")
            fails.append((37, "memory viewer file tabs render", reason))

        browser.close()

    # Poller-spin guard (live_council_two_pollers): after every surface has run,
    # any council_status token probed past the give-up threshold means a status
    # poller never hit its MAX_MISSING_POLLS cap — the recurring 404-spin bug.
    # Observable end-to-end only here; without this it was a manual log eyeball.
    if STATUS_404_COUNTS:
        worst_n = STATUS_404_COUNTS.most_common(1)[0][1]
        print(f"[poller-health] {len(STATUS_404_COUNTS)} council_status token(s) "
              f"404'd; worst={worst_n} probe(s) (spin threshold {SPIN_THRESHOLD})")
    fails.extend(detect_poller_spins(STATUS_404_COUNTS))

    print()
    print("─" * 60)
    if not fails:
        print(f"✓ All surfaces pass. Screenshots in {SHOTS_DIR.relative_to(REPO_ROOT)}/")
        return 0
    else:
        print(f"✗ {len(fails)} surface(s) failed:")
        for n, name, reason in fails:
            print(f"    Surface {n} ({name}): {reason[:120]}")
        return 1


def _find_multi_round_thread() -> str | None:
    """Pick a thread bundle on disk with 3+ segments, for Surface 9.

    Walks `~/.trinity/council_outcomes/_thread_*.js`, parses the embedded
    JSON, returns the chain_root_id of the first qualifying thread.
    Prefers threads where at least one segment's outcome carries
    user_refinement (so the refinement-directive assertion in Surface 9
    has something to assert against); falls back to any 3+ thread when
    no refinement-bearing thread exists (e.g. legacy bundles predating
    council-iterate). Returns None if no such bundle exists — Surface 9
    then skips.
    """
    threads_dir = TRINITY_HOME / "council_outcomes"
    if not threads_dir.is_dir():
        return None
    import re as _re
    # File shape (one line):
    #   window.__TRINITY_COUNCIL_THREAD__ = window.__TRINITY_COUNCIL_THREAD__ || {};
    #   window.__TRINITY_COUNCIL_THREAD__["bundle_X"] = {<payload>};
    pattern = _re.compile(r'__TRINITY_COUNCIL_THREAD__\[[^\]]+\]\s*=\s*({.*})\s*;', _re.DOTALL)
    fallback: str | None = None
    for path in sorted(threads_dir.glob("_thread_*.js")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = pattern.search(text)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        segments = payload.get("segments") or []
        if len(segments) < 3:
            continue
        chain_root = payload.get("chain_root_id")
        if not chain_root:
            continue
        if fallback is None:
            fallback = chain_root
        # Check whether any segment has user_refinement on its outcome —
        # this is the regression target for tick #56 (the bug that lost
        # refinement directives on reload).
        has_refinement = False
        for seg in segments:
            cid = seg.get("council_id")
            if not cid:
                continue
            outcome_path = threads_dir / f"{cid}.json"
            if not outcome_path.is_file():
                continue
            try:
                outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (outcome.get("metadata") or {}).get("user_refinement"):
                has_refinement = True
                break
        if has_refinement:
            return chain_root
    return fallback


def _thread_has_refinement(chain_root_id: str) -> bool:
    """True if any segment of this thread carries user_refinement in
    its outcome.metadata. Used by Surface 9 to decide whether to
    enforce the refinement-directive assertion."""
    threads_dir = TRINITY_HOME / "council_outcomes"
    thread_manifest = threads_dir / f"_thread_{chain_root_id}.js"
    if not thread_manifest.is_file():
        return False
    import re as _re
    pattern = _re.compile(r'__TRINITY_COUNCIL_THREAD__\[[^\]]+\]\s*=\s*({.*})\s*;', _re.DOTALL)
    try:
        text = thread_manifest.read_text(encoding="utf-8")
    except OSError:
        return False
    m = pattern.search(text)
    if not m:
        return False
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return False
    for seg in payload.get("segments") or []:
        cid = seg.get("council_id")
        if not cid:
            continue
        outcome_path = threads_dir / f"{cid}.json"
        if not outcome_path.is_file():
            continue
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (outcome.get("metadata") or {}).get("user_refinement"):
            return True
    return False


def _is_server_up(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/portal_pages/launchpad.html", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _ensure_server(port: int) -> threading.Thread | None:
    """Return a thread handle if we started a new server, None if one was already up."""
    if _is_server_up(port):
        return None

    home = TRINITY_HOME
    if not home.is_dir():
        print(f"[setup] TRINITY_HOME not found at {home} — run portal-html first")
        return None

    handler = lambda *a, **kw: _SpinTrackingHandler(*a, directory=str(home), **kw)
    try:
        httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        print(f"[setup] could not bind 127.0.0.1:{port} — {exc}")
        return None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.6)
    return thread


if __name__ == "__main__":
    sys.exit(main())
