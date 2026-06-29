"""Regression: clicking Refine / Continue / Auto-chain on the council
review page must NOT silently swallow dispatch failures.

Before this fix, the flow was:
  1. Click Refine → chainBusy=true, chainStatusHeading shown
  2. dispatcher.dispatch (async, no await)
  3. New segment optimistically appended to thread
  4. setTimeout(800) → chainBusy=false → status panel hidden
  5. async onResult fires: chainBusy=false (no-op), chainStatusDetail=error
  6. But chainStatusDetail is rendered INSIDE v-if="chainBusy" — hidden
  7. User sees: nothing. No banner, no error, no new segment (rolled back).

Symptom is the live-council-page sibling of the launchpad stuck-launch
bug. Two-fold fix:
  - chainError (separate state) renders OUTSIDE the chainBusy guard
  - on dispatch failure: roll back optimistic segment + restore prompt
"""
from __future__ import annotations


def _render_single():
    # The Vue scaffold for both single-council and thread pages is generated
    # by the same source module — just read it directly. Both templates +
    # both <script> blocks live in this one file.
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src" / "trinity_local" / "council_review.py"
    return src.read_text()


def test_chain_error_data_field_initialized():
    src = _render_single()
    # There are TWO Vue apps in this file (single-council + thread); both
    # need the chainError data field. Same pattern as launchpad pendingPrompt.
    assert src.count("chainError: ''") == 2, (
        "Expected chainError init in both Vue apps (single-council + thread)"
    )
    assert "_pendingChainSegmentToken: ''" in src, (
        "Thread app needs _pendingChainSegmentToken for segment rollback"
    )


def test_chain_error_banner_renders_outside_chainBusy_guard():
    src = _render_single()
    # Find the chainError banner. Must use v-if="chainError" (not nested
    # inside chainBusy v-if).
    assert src.count('v-if="chainError"') >= 2, (
        "Expected the chainError banner template in both Vue apps"
    )
    # The LIVE banner has a Dismiss link that clears chainError manually. It
    # wires to the `dismissChainError` method (not a bare inline `chainError = ''`)
    # so the dismiss ALSO re-homes keyboard focus off the link it's about to remove
    # (WCAG 2.4.3 — see test_dismiss_banner_focus_rehome_browser). Assert the live
    # wiring AND that the handler clears the error — a bare-inline regression OR a
    # handler that forgets to clear chainError both red here. (Pinning the live
    # `dismissChainError` wiring, NOT the substring `chainError = ''`, which still
    # appears in the DEAD render_unified copy + internal state-clears — a substring
    # check would stay vacuously green there.)
    assert '@click.prevent="dismissChainError"' in src, (
        "the live chainError banner's Dismiss link must wire to dismissChainError"
    )
    import re as _re
    m = _re.search(r"dismissChainError\(\) \{\{(.*?)\}\},", src, _re.DOTALL)
    assert m and "this.chainError = ''" in m.group(1), (
        "dismissChainError must clear chainError so the banner actually dismisses"
    )


def test_thread_page_chain_error_banner_is_not_nested_inside_canChainNext():
    """Regression for iter-15 bug: the thread-page chainError banner was
    originally nested inside `<section class="card chain-actions" v-if="canChainNext">`,
    which only becomes truthy after the LAST segment completes. So during
    a running council (when Stop council is most likely clicked + failed),
    chainError was correctly populated but the banner was invisible.

    The hoisted layout: a standalone `<section class="card" v-if="chainError">`
    above the `chain-actions` section, so the banner renders regardless
    of completion state.

    This test pins the structural choice — pure substring presence isn't
    enough; the banner must appear BEFORE the chain-actions section."""
    src = _render_single()
    # Thread-page chain-actions section has the canChainNext guard.
    cna_idx = src.index('v-if="canChainNext"')
    # The hoisted chainError banner appears as a standalone section just
    # above it. Look backward from cna_idx for the chainError banner.
    upstream = src[:cna_idx]
    # The LAST chainError v-if before canChainNext must be a standalone
    # <section> (the hoisted one), not nested inside the chain-actions
    # section (which doesn't exist yet at this point in the source).
    last_chain_error = upstream.rfind('v-if="chainError"')
    assert last_chain_error != -1, (
        "Thread-page chainError banner must appear BEFORE the canChainNext "
        "section in source order (hoisted out, not nested inside)"
    )
    # The banner element must be a standalone <section>, not a <div> nested
    # inside another section.
    excerpt = src[max(0, last_chain_error - 200):last_chain_error + 100]
    assert "<section" in excerpt, (
        "The hoisted chainError banner must use <section> (top-level layout), "
        "not <div> (which suggests it's still nested inside another container)"
    )


def test_dispatch_failure_sets_chainError_not_chainStatusDetail():
    """Both onResult failure paths must write to chainError (visible outside the
    chainBusy guard), via the shared dispatchErrorMessage() helper.

    The error copy moved into dispatchErrorMessage() (2026-05-31) so it can be
    accurate per failure-reason instead of always saying "is the extension
    installed?" — which was wrong + frustrating when it WAS installed. Both
    onResult handlers must route through it.
    """
    src = _render_single()
    # The helper is defined exactly once and translates dispatcher reason codes.
    assert src.count("function dispatchErrorMessage(") == 1, (
        "dispatchErrorMessage helper must be defined once"
    )
    # It must NOT blindly tell the user to install an extension they may have.
    assert "is the Chrome extension installed? Run trinity-local install-extension if not." not in src, (
        "the misleading 'is the extension installed?' fallback must be gone — "
        "it fired even when the extension WAS installed (founder report)"
    )
    # Every dispatch-failure handler (refine single + thread, stop-council)
    # routes through the helper into chainError — none left on a raw fallback.
    assert src.count("this.chainError = dispatchErrorMessage(r)") >= 2, (
        "the Vue app onResult failure handlers must set "
        "this.chainError = dispatchErrorMessage(r)"
    )


def test_dispatch_error_message_is_reason_specific():
    """The helper must give an accurate message per reason code, NOT the old
    blanket "is the extension installed?" — that's the founder's actual
    complaint: the extension was installed and the dispatcher had refused on a
    stale probe flag (fixed in launchpad_runtime)."""
    src = _render_single()
    # native-host-unavailable → re-wire, not re-install.
    assert "native-host-unavailable" in src
    # extension-unreachable → it may be disabled / asleep, reload it.
    assert "extension-unreachable" in src
    assert "chrome://extensions" in src, (
        "a reachable-but-asleep extension should be reloaded, not reinstalled"
    )


def test_thread_segment_rollback_on_dispatch_failure():
    """The thread page appends a new segment optimistically. When dispatch
    fails, that segment must be removed so polling doesn't hammer a
    non-existent status file + the thread visual stays accurate."""
    src = _render_single()
    assert "_pendingChainSegmentToken" in src
    # The thread onResult failure path uses this token to find + splice the segment.
    assert "findIndex((s) => s.statusToken === this._pendingChainSegmentToken)" in src
    assert "this.segments.splice(idx, 1)" in src


def test_refine_prompt_restored_on_dispatch_failure():
    """User shouldn't have to retype the refinement prompt after a
    dispatch failure — restore it so they can edit + retry."""
    src = _render_single()
    # The thread version receives refinementText and restores it.
    assert "if (refinementText) this.refinePrompt = refinementText" in src


def test_chain_dispatch_payload_shape_is_correct():
    """The chain action (Refine / Continue / Auto-chain) must dispatch the
    correct extensionAction payload — `kind:'council-iterate'` (the only kind
    capture_host.ACTION_ALLOWLIST accepts for chaining; any other silently
    no-ops at the host), the CURRENT council id, a status-token, and the
    per-action args (refine → prompt, auto-chain → rounds). The failure-handling
    tests above cover the !ok path but NOT the payload CONSTRUCTION — a
    regression that broke `kind`, dropped `council`, or lost the refine `prompt`
    would dispatch a wrong/no-op council and keep every failure test green.
    Browser-verified 2026-06-02 on a real council: all three payloads correct —
    Refine carries the prompt, Continue carries none, Auto-chain carries
    rounds:'3'.
    """
    src = _render_single()
    # BOTH Vue apps (single-council `_startChainAction` ~L531 + thread ~L1796)
    # build the chain payload. Assert by COUNT so a mutation to EITHER app's
    # copy reds the test — a presence-only `in src` stays green on the
    # un-mutated copy (the mutation_testing_validates_regression_coverage trap;
    # this guard's first draft missed exactly that).
    assert src.count("kind: 'council-iterate',") == 2, (
        "both apps must dispatch kind 'council-iterate' (the only ACTION_ALLOWLIST "
        "chain kind; any other silently no-ops at capture_host)"
    )
    # Each app targets the correct council: the single page targets the page's
    # council; the thread targets the LATEST round's council (last.councilId).
    assert src.count("council: pageData.councilId,") == 1, (
        "single-council app must target pageData.councilId"
    )
    assert src.count("council: last.councilId,") == 1, (
        "thread app must target the latest round's council (last.councilId)"
    )
    # The key MUST be `status_token` (underscore) — capture_host's
    # ACTION_ALLOWLIST reads payload['status_token']. The hyphen CLI-flag
    # spelling ('status-token') silently dropped the token, so council-iterate
    # ran under a fresh bundle_id and the page polled a token nothing was
    # written under → "council never started" (founder 2026-06-12). Assert the
    # underscore form AND that the hyphen form is gone, in BOTH apps.
    assert src.count("status_token: statusToken,") == 1, (
        "single-council payload must carry its status token under the underscore key"
    )
    assert src.count("status_token: newToken,") == 1, (
        "thread payload must carry its (new-round) status token under the underscore key"
    )
    assert "'status-token':" not in src and '"status-token":' not in src, (
        "a chain dispatch payload uses the hyphen 'status-token' key — capture_host "
        "reads payload['status_token'], so the token is silently dropped (the "
        "2026-06-12 'council never started' bug)"
    )
    # Per-action args folded onto the payload in BOTH apps.
    assert src.count("if (args.prompt) extensionAction.prompt = args.prompt;") == 2, (
        "both apps must fold the refine prompt onto the payload"
    )
    assert src.count("if (args.max_rounds) extensionAction.rounds = String(args.max_rounds);") == 2, (
        "both apps must fold the auto-chain rounds onto the payload"
    )


def test_probe_distinguishes_stale_extension_from_absent():
    """An installed-but-OLD extension rejects new sender pages with
    'rejected-sender' (it needs a RELOAD, not a reinstall). The dispatcher
    probe must map that to a distinct 'stale' state, NOT 'absent' (which drives
    install hints). Founder report 2026-05-31: the council page hit exactly this
    — the extension was installed but predated the council-page sender rule."""
    from pathlib import Path
    rt = (Path(__file__).resolve().parent.parent
          / "src" / "trinity_local" / "launchpad_runtime.py").read_text()
    assert "r.error === 'rejected-sender'" in rt and "setState('stale')" in rt, (
        "probe must map a rejected-sender ping to state 'stale' (reload), not "
        "'absent' (reinstall)."
    )


def test_council_page_proactively_warns_on_stale_extension():
    """The live council page must surface the reload hint PROACTIVELY on load
    (via chainError) when the dispatcher reports 'stale' — so the user fixes it
    before clicking Refine and hitting the rejection, not after."""
    src = _render_single()
    # init() wires onStateChange + a forced probe and sets chainError on 'stale'.
    assert "onStateChange" in src
    assert "st === 'stale'" in src, (
        "council init must react to dispatcher state 'stale'"
    )
    assert "out of date" in src and "chrome://extensions" in src, (
        "the proactive stale banner must tell the user to reload at "
        "chrome://extensions (not reinstall)."
    )
