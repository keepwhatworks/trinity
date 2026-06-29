"""String-contract guards for the three side-panel fixes (founder report
2026-06-16, Images #4/#5/#6) that the slow+browser tests verify end-to-end but
which need a CI-runnable canary too (the browser tests are skipped without a
real Chrome):

  • #6 spinner hang — sidepanel-shell.js must bound every host query with a
    timeout (a wedged native host can't leave the loading spinner stuck forever).
  • #5 council link "blocked by Chrome" — the in-panel nav broker: a sandboxed
    (opaque-origin) page can't self-navigate to another extension page, so the
    shared runtime hands nav UP to the shell, which swaps the iframe src; the
    shell handler is restricted to our OWN two sandbox pages (a security gate —
    the sandbox renders attacker-influenceable corpus content under a relaxed CSP).
  • #4 blank provider-status row — the running-council card filters empty member
    keys and never renders a blank provider label.

These read the committed artifacts + the runtime source, so they fail fast in
plain `pytest -q` if any fix is reverted or a rebuild drops it.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"


def test_shell_host_query_is_timeout_bounded():
    """sidepanel-shell.js must race every host query against a timeout and have a
    3-state detector — else a slow/wedged host hangs the loading spinner (#6)."""
    js = (EXT / "sidepanel-shell.js").read_text(encoding="utf-8")
    assert "setTimeout" in js and "TIMEOUT" in js, "shell host query is not timeout-bounded"
    assert "detectHost" in js, "shell lost its 3-state host detector"
    # On a timeout we prefer the launchpad (a present-but-slow host beats standalone).
    assert '"timeout"' in js or "'timeout'" in js, "shell no longer distinguishes a timeout state"


def test_runtime_has_sandbox_nav_broker():
    """The shared runtime (injected into BOTH the launchpad and the council page)
    must broker in-panel navigation through the shell instead of self-navigating
    a sandboxed page (#5)."""
    from trinity_local.launchpad_runtime import launchpad_runtime_js

    js = launchpad_runtime_js()
    assert "__trinityInSandbox" in js, "lost the sandbox detector"
    assert "__trinityNavigate" in js, "lost the nav broker"
    assert "__trinityNav" in js and "postMessage" in js, "nav is not handed to the shell"
    # A delegated capture-phase click interceptor so dynamic rail links are covered.
    assert "addEventListener('click'" in js, "no click interceptor for in-panel links"
    # navigateToReviewPath must NOT replace() to ../review_pages/ in the sandbox
    # (that's the blocked chrome-extension path). The sandbox branch must execute
    # FIRST — before the review_pages fallback — and route through the broker.
    nav_fn = js[js.index("function navigateToReviewPath"):]
    assert "__trinityInSandbox()" in nav_fn, "navigateToReviewPath no longer guards the sandbox"
    review_branch = "path.includes('/review_pages/')"
    assert review_branch in nav_fn, "navigateToReviewPath lost its review_pages branch"
    assert nav_fn.index("__trinityInSandbox()") < nav_fn.index(review_branch), (
        "navigateToReviewPath hits the ../review_pages/ branch before the sandbox guard"
    )


def test_shell_nav_handler_is_restricted_to_sandbox_pages():
    """The shell's nav broker must ONLY ever navigate the iframe to our own two
    sandbox pages — never an arbitrary URL from inside the relaxed-CSP sandbox."""
    js = (EXT / "sidepanel-bridge.js").read_text(encoding="utf-8")
    assert "__trinityNav" in js, "shell has no nav-broker handler"
    assert "launchpad.html" in js and "live_council.html" in js, "nav allowlist is missing a page"
    assert 'frame.src = "sandbox/"' in js, "nav target is not re-rooted under sandbox/"
    # No raw assignment of an attacker-supplied url straight onto frame.src.
    assert "frame.src = msg.url" not in js, "shell navigates to an unvalidated url"


def test_sandbox_pages_carry_the_nav_broker():
    """The GENERATED sandbox pages must actually contain the broker — a rebuild
    that drops the runtime would reintroduce the blocked-link bug."""
    for rel in ("sandbox/launchpad-init.js", "sandbox/live_council.html"):
        txt = (EXT / rel).read_text(encoding="utf-8")
        assert "__trinityNavigate" in txt, f"{rel} lost the nav broker (stale rebuild?)"


def test_no_raw_template_flash_on_nav():
    """Navigating in the side panel RELOADS the sandbox iframe, and the app waits
    on an async host fetch before mounting — so without a cover the panel flashes
    the raw, un-mounted template (literal {{ }} + every v-if expanded) for seconds
    (founder-caught on back-nav). Two layers must hold:
      1. v-cloak hides the un-mounted app (petite-vue strips it on mount), and
      2. the shell shows its loading spinner across the swap, dismissed on the
         sandbox's `__trinityMounted` signal with a timeout so it can't stick."""
    # Layer 1: v-cloak CSS + the signal that the shell waits on.
    from trinity_local.design_system import SHARED_CSS
    from trinity_local.launchpad_runtime import launchpad_runtime_js

    assert "[v-cloak]" in SHARED_CSS and "display: none" in SHARED_CSS, "no v-cloak hide rule"
    js = launchpad_runtime_js()
    assert "__trinityMounted" in js, "runtime never signals the shell that it mounted"
    assert "attributeFilter: ['v-cloak']" in js, "mount signal must fire on v-cloak removal"
    # The two SANDBOX-loaded roots carry v-cloak (launchpad + live council) — those
    # reload visibly in the side panel. The static unified review page (CouncilApp)
    # is file:// / 0-prod-callers, no sandbox reload, so it deliberately does NOT.
    lt = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    cr = (REPO / "src" / "trinity_local" / "council_review.py").read_text(encoding="utf-8")
    assert 'v-scope="LaunchpadApp(pageData)" v-cloak' in lt, "launchpad root lost v-cloak"
    assert 'v-scope="LiveCouncilApp(pageData)" v-cloak' in cr, "live-council root lost v-cloak"
    # Layer 2: the shell covers the swap + a non-stickable spinner.
    bridge = (EXT / "sidepanel-bridge.js").read_text(encoding="utf-8")
    assert "showNavLoader" in bridge and "hideNavLoader" in bridge, "shell doesn't cover the nav swap"
    assert "__trinityMounted" in bridge, "shell never reveals on the mount signal"
    assert "setTimeout(hideNavLoader" in bridge, "nav spinner has no never-stuck fallback"
    # The sandbox launchpad must carry both layers post-build.
    sb = (EXT / "sandbox" / "launchpad.html").read_text(encoding="utf-8")
    assert "[v-cloak]" in sb and "v-cloak" in sb, "built sandbox launchpad lost v-cloak"


def test_host_detection_uses_cheap_ping_and_holds_spinner_until_mount():
    """The side-panel spinner lingered (founder-caught 2026-06-16): the shell's
    host-detection queried `launchpad_data` — building the FULL launchpad payload
    just to check reachability — so the host built it TWICE per open (shell +
    iframe). Fix: (1) a cheap `ping` query that returns without building, and
    (2) the shell holds the spinner until the iframe signals it MOUNTED (so the
    fast ping can't reveal a blank v-cloak'd iframe)."""
    from trinity_local import capture_host

    assert "ping" in capture_host.QUERY_KINDS and "ping" in capture_host.QUERY_HANDLERS
    # ping must be cheap — return ok WITHOUT importing/building the launchpad payload.
    out = capture_host.QUERY_HANDLERS["ping"]({})
    assert out.get("ok") is True, "ping must answer ok"
    shell = (EXT / "sidepanel-shell.js").read_text(encoding="utf-8")
    assert 'query_kind: "ping"' in shell, "shell host-detection no longer uses the cheap ping"
    assert 'query_kind: "launchpad_data"' not in shell, (
        "shell still QUERIES launchpad_data (full payload build) just to detect the host"
    )
    assert "__trinityMounted" in shell, "shell no longer holds the spinner until the iframe mounts"


def test_runtime_brokers_stats_and_portal_links_not_just_two_pages():
    """The nav-broker class is bigger than launchpad.html/live_council.html. In the
    side panel /stats is a CSS view-toggle (no sandbox/stats.html exists — a real
    nav there is "blocked by Chrome", founder Image #10), and the memory-viewer
    deep links (../portal_pages/*.html) point at a page absent from the sandbox.
    Both must be intercepted, NOT self-navigated. Source-level canary (the
    real-browser proof lives in test_sidepanel_stats_view_browser.py)."""
    from trinity_local.launchpad_runtime import launchpad_runtime_js

    js = launchpad_runtime_js()
    # /stats is flipped in place, never navigated.
    assert "function setLaunchpadView" in js, "lost the in-place /stats view toggle"
    assert "lp-view-stats" in js and "lp-view-home" in js, "view toggle doesn't swap the root class"
    assert "STATS_RX" in js, "the click interceptor no longer matches the stats link"
    # portal_pages deep links get a graceful escape, not a blocked self-nav.
    assert "PORTAL_RX" in js and "__trinityOpenFullLaunchpad" in js, (
        "memory-viewer (../portal_pages/*.html) links would still self-nav → blocked by Chrome"
    )
    # The interceptor must distinguish the launchpad page (view toggle) from the
    # council page (genuine cross-page nav) via the app root's id.
    assert "getElementById('launchpad-app')" in js, "interceptor can't tell launchpad from council page"


def test_built_sandbox_launchpad_carries_the_view_toggle():
    """A rebuild that drops the runtime would reintroduce the /stats blocked-link
    bug — the GENERATED side-panel init must contain the toggle."""
    init = (EXT / "sandbox" / "launchpad-init.js").read_text(encoding="utf-8")
    assert "setLaunchpadView" in init, "built sandbox init lost the /stats view toggle (stale rebuild?)"
    assert "__trinityOpenFullLaunchpad" in init, "built sandbox init lost the portal-link escape"


def test_rail_new_council_button_is_removed():
    """"+ Ask a new council" was REMOVED (founder call 2026-06-17): the home view
    IS the composer, so a duplicate "new council" affordance in the council-HISTORY
    rail was redundant. This pins it gone so a future rebuild can't resurrect it."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    assert "Ask a new council" not in src, "the redundant rail-new button came back"
    assert "rail-new" not in src, "stale rail-new markup/CSS/wiring left behind"


def test_running_card_filters_blank_provider_rows():
    """The running-council card must drop empty member keys + never blank a
    provider label (#4 — a member with no slug rendered a blank status row)."""
    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    assert ".filter((p) => p && String(p).trim())" in src, "providerStatusRows no longer drops blank keys"
    assert "formatProviderLabel(provider) || String(provider)" in src, "provider label can still render blank"
