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

2026-07-24: the Refine / Continue / Auto-chain composer was removed with the
council-iterate verb, so the four tests pinning that dispatch path went with it.
The surviving tests still matter — chainError, the reason-specific dispatch
message, and the stale-extension probe are shared by Stop council and the
failed-council "Try again" retry, which both still dispatch.
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
    assert src.count("chainError: ''") == 1, (
        "Expected chainError init on the live council page's chain app "
        "(the static render_unified copy was removed, #311/#8)"
    )
    assert "_pendingChainSegmentToken: ''" in src, (
        "Thread app needs _pendingChainSegmentToken for segment rollback"
    )


def test_chain_error_banner_renders_outside_chainBusy_guard():
    src = _render_single()
    # Find the chainError banner. Must use v-if="chainError" (not nested
    # inside chainBusy v-if).
    assert src.count('v-if="chainError"') >= 1, (
        "Expected the chainError banner template on the live council page"
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
