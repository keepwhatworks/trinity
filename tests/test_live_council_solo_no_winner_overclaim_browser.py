"""Browser guard: a SOLO (1-responder) council on the LIVE page must NOT claim a
winner or report consensus — the same degenerate-overclaim the SHARE CARD already
suppresses (council_card.py's ``solo = len(members) <= 1`` branch).

A council needs at least TWO voices to have a contest. When only one member
responded (a single provider was enabled, OR every other member failed leaving
one), there is no one to "win" against and no one to "agree" with. The share card
was taught this in the UX sweep (Iter 57): it renders "One model — no council.
Only one model answered, so there's no winner and nothing to agree on yet."

But the LIVE council page (``render_live_council_page``) — the surface 100% of
launchpad-launched councils land on — rendered the FULL competition framing on a
solo council:

    🏆 Claude — the answer you'd have picked.        (winner-verdict)
    Winner: Claude                                    (routing label)
    Agreed claims: • Ship it now  • Tests are green   (a single model can't agree
                                                       with itself)

…above a grid showing ONE member row. The page proved there was no contest, yet
claimed a winner and consensus. Found 2026-06-18 driving the live page with a
seeded solo council in the UX sweep — the unfixed twin of the share card's solo
suppression (sibling-surface drift).

The fix added an ``isSoloFor(seg)`` computed (responder count <= 1 on a completed
segment) gating the winner-verdict, the "Winner:" line, and the Agreed/Disagreed
blocks, and renders an honest "One model answered — no council" line instead.

This guard serves an isolated, PII-free synthetic council over http (file:// can't
carry the ``?status_token=`` query reliably) and reads the RENDERED DOM. It drives
BOTH a solo council (the overclaim must be gone) AND a 2-member council (the verdict
must SURVIVE — proving the gate is the solo condition, not a blanket suppression).
Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _solo_status(token: str) -> dict:
    """A completed SOLO council: one member responded; the chairman still emitted
    a routing_label with winner + agreed_claims (the degenerate shape)."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Should I ship the solo council before launch?",
        "council_id": "c_solo_council",
        "memberOrder": ["claude"],
        "members": {
            "claude": {
                "status": "done",
                "model": "claude-opus-4-8",
                "response_text": "Claude's lone answer.",
                "response_html": "<p>Claude's lone answer.</p>",
            },
        },
        "synthesis": {
            "status": "done",
            "response_text": "Synthesis over one answer.",
            "response_html": "<p>Synthesis over one answer.</p>",
            "routing_label": {
                "winner": "claude",
                "confidence": "high",
                "agreed_claims": ["Ship it now", "Tests are green"],
                "disagreed_claims": [],
            },
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_solo_council",
            "members": ["claude"],
        },
    }


def _same_provider_status(token: str) -> dict:
    """A completed council whose ROSTER is the same provider three times
    (claude·claude·claude) — three calls, but ONE distinct voice. The runner
    keys the on-disk members map by PROVIDER, so the three calls collapse to a
    single ``claude`` entry; ``metadata.members`` + ``memberOrder`` still carry
    the raw three-claude roster (the shape the council_status writer + outcome
    JSON actually produce). The chairman still emits a degenerate routing_label
    where winner AND runner_up are the SAME slug.

    This is the live-page twin of the share card's
    ``test_all_same_provider_council_card_suppresses_contest_framing`` and the
    static review page's ``..._for_all_same_provider`` — both already guarded.
    The static review page's source comment LEANS on this collapse ("the live
    page already collapses it via its provider-keyed members map") as a
    load-bearing claim, yet nothing proved it: the existing live solo test only
    fed a 1-member roster, so a refactor keying the members map by a unique
    member-id (to support same-provider distinct-config councils) would silently
    re-introduce the "Claude · runner-up: Claude" overclaim with no guard biting.
    """
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Same-provider triple-call council.",
        "council_id": "c_sameprov_council",
        "memberOrder": ["claude", "claude", "claude"],
        "members": {
            # Provider-keyed → three claude calls collapse to ONE row, exactly
            # as council_status.py writes it (`{provider: {...} for provider in
            # members}`). A future unique-member-id keying would make this map
            # carry three distinct rows and break the collapse.
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "Lone voice.", "response_html": "<p>Lone voice.</p>"},
        },
        "synthesis": {
            "status": "done",
            "response_text": "Synthesis over one distinct provider.",
            "response_html": "<p>Synthesis over one distinct provider.</p>",
            "routing_label": {
                # The degenerate chairman output: winner AND runner-up are the
                # SAME slug — "Claude · runner-up: Claude" is the founder symptom.
                "winner": "claude",
                "runner_up": "claude",
                "confidence": "high",
                "agreed_claims": ["Self-agreement A", "Self-agreement B"],
                "disagreed_claims": [],
            },
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_sameprov_council",
            "members": ["claude", "claude", "claude"],
        },
    }


def _duo_status(token: str) -> dict:
    """A completed TWO-member council — a real contest. The winner-verdict +
    Agreed/Disagreed MUST still render (positive control for the solo gate)."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Two-member council.",
        "council_id": "c_duo_council",
        "memberOrder": ["claude", "antigravity"],
        "members": {
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "A.", "response_html": "<p>A.</p>"},
            "antigravity": {"status": "done", "model": "gemini-3.1-pro",
                            "response_text": "B.", "response_html": "<p>B.</p>"},
        },
        "synthesis": {
            "status": "done",
            "response_text": "S.",
            "response_html": "<p>S.</p>",
            "routing_label": {
                "winner": "claude",
                "runner_up": "antigravity",
                "confidence": "high",
                "agreed_claims": ["Both agree on X"],
                "disagreed_claims": [{
                    "claim": "Y", "providers_for": ["claude"],
                    "providers_against": ["antigravity"], "why_matters": "matters",
                }],
            },
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_duo_council",
            "members": ["claude", "antigravity"],
        },
    }


def _no_winner_duo_status(token: str) -> dict:
    """A completed TWO-DISTINCT-provider council where the chairman picked NO
    winner (``routing_label.winner == ""``). This is a real, tolerated state: the
    chairman is asked to name a winner but ``CouncilRoutingLabel.winner`` defaults
    to "" and ``council_runner._resolve`` resolves an empty winner to ``None`` (a
    genuine tie / no-clear-pick on an open question).

    The trophy ``winner-verdict`` gates on ``lensPickProviderFor`` (the winner
    slug) and the ``solo-verdict`` gates on ``isSoloFor`` — BOTH false here — so
    before the fix the completed page rendered the agreed/disagreed claims with NO
    top-line verdict at all: a sighted user read a FINISHED multi-model contest and
    was never told who won OR that nobody did. The PERSISTENT static review page
    (``_render_routing_label_section`` → "No winner") and the share card both state
    the no-winner verdict explicitly; this is the live-page twin that drifted
    unfixed (the no-winner sibling of the solo line right above it)."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Should the cross-region store use a CRDT or a single-writer log?",
        "council_id": "c_nowinner_duo",
        "memberOrder": ["claude", "codex"],
        "members": {
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "CRDT.", "response_html": "<p>CRDT.</p>"},
            "codex": {"status": "done", "model": "gpt-5.5",
                      "response_text": "Single-writer log.", "response_html": "<p>Single-writer log.</p>"},
        },
        "synthesis": {
            "status": "done",
            "response_text": "Genuinely open call.",
            "response_html": "<p>Genuinely open call.</p>",
            "routing_label": {
                # The no-pick verdict: a real 2-provider contest, empty winner.
                "winner": "",
                "confidence": "low",
                "agreed_claims": ["Both agree the write path must be idempotent."],
                "disagreed_claims": [{
                    "claim": "CRDT vs single-writer log.",
                    "providers_for": ["claude"],
                    "providers_against": ["codex"],
                    "why_matters": "Automatic vs operator-driven merge.",
                }],
            },
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_nowinner_duo",
            "members": ["claude", "codex"],
        },
    }


def _all_failed_completed_status(token: str) -> dict:
    """A council whose status is ``completed`` but where EVERY member FAILED — ZERO
    distinct responders (Iter 269). The runner raises ``ProviderError`` before it
    ever persists a 0-responder council, so this shape only reaches the page via a
    hand-edited / corrupt status JSON (the #258 hand-editable-state class) — but the
    page must render whatever lands on disk HONESTLY.

    ``isSoloFor`` gates on ``respondedMembersFor(seg) <= 1``, which is TRUE for 0
    responders too — so before the Iter 269 fix the page painted the SOLO verdict
    'One model answered — no council' DIRECTLY ON TOP of the honest all-failed line
    'Every provider attempted but failed to respond — there's no synthesis to show'.
    A flat self-contradiction (one model answered vs every model failed). The solo
    claim now requires ``respondedMembersFor(seg) >= 1``."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Every provider failed on this one.",
        "council_id": "c_all_failed",
        "memberOrder": ["claude", "codex"],
        "members": {
            # Both members FAILED — 0 status:'done' rows, so respondedMembersFor==0.
            "claude": {"status": "failed", "model": "claude-opus-4-8",
                       "error": "rate limited"},
            "codex": {"status": "failed", "model": "gpt-5.5",
                      "error": "auth failed"},
        },
        "synthesis": {
            # A corrupt status can still carry a stale routing_label with a winner
            # the chairman emitted before the providers actually failed.
            "status": "done",
            "response_text": "",
            "response_html": "",
            "routing_label": {
                "winner": "claude",
                "confidence": "low",
                "agreed_claims": [],
                "disagreed_claims": [],
            },
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_all_failed",
            "members": ["claude", "codex"],
            "failed_members": ["claude", "codex"],
        },
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _drive(tmp_path, port, token):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            verdict = page.evaluate(
                "() => { const e = document.querySelector('.winner-verdict');"
                " return e ? e.textContent.trim() : ''; }"
            )
            body = page.evaluate("() => document.body.innerText")
            rows = page.evaluate(
                "() => document.querySelectorAll('.provider-status-row').length"
            )
            return verdict, body, rows
        finally:
            browser.close()


def _seed(tmp_path, monkeypatch, status):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    token = status["status_token"]
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(status)};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(sidecar, encoding="utf-8")


def test_solo_council_suppresses_winner_overclaim(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_solo_overclaim"
    _seed(tmp_path, monkeypatch, _solo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        verdict, body, rows = _drive(tmp_path, port, token)
    finally:
        httpd.shutdown()

    # The page itself proves there's no contest: ONE member row.
    assert rows == 1, f"expected exactly 1 member row on a solo council, got {rows}"
    # THE BUG: the trophy "the answer you'd have picked" verdict is a fabricated
    # contest win on a council where one model answered.
    assert "the answer you'd have picked" not in body, (
        "the LIVE page rendered the trophy winner-verdict ('X — the answer you'd "
        "have picked') on a SOLO 1-responder council — a fabricated contest win the "
        "share card already suppresses. Verdict text: " + repr(verdict)
    )
    # The "Winner:" routing line and the consensus blocks are equally degenerate:
    # a single model has no one to beat or agree/disagree with.
    assert "Winner:" not in body, "solo council still rendered a 'Winner:' routing line"
    assert "Agreed claims" not in body, (
        "solo council still rendered an 'Agreed claims' block — a single model can't "
        "agree with itself"
    )
    assert "Disagreed claims" not in body, "solo council still rendered a 'Disagreed claims' block"
    # The honest framing IS present (not a blank suppression).
    assert "One model answered — no council" in verdict, (
        "the solo verdict line ('One model answered — no council …') did not render — "
        f"the suppression must REPLACE the overclaim, not blank it. Verdict: {verdict!r}"
    )
    assert "no winner and nothing to agree on yet" in body, (
        "the routing-label solo line ('Only one model responded, so there's no winner "
        "and nothing to agree on yet') did not render"
    )


def test_two_member_council_still_shows_winner_verdict(tmp_path, monkeypatch):
    """Positive control: the solo gate must NOT suppress a real 2-member contest —
    otherwise a passing solo test could be a blanket suppression bug."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_duo_control"
    _seed(tmp_path, monkeypatch, _duo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        verdict, body, rows = _drive(tmp_path, port, token)
    finally:
        httpd.shutdown()

    assert rows == 2, f"expected 2 member rows on a duo council, got {rows}"
    assert "the answer you'd have picked" in verdict, (
        "the winner-verdict vanished on a real 2-member council — the solo gate "
        f"over-suppressed. Verdict: {verdict!r}"
    )
    assert "One model answered — no council" not in body, (
        "the solo line leaked onto a real 2-member council"
    )
    assert "Winner:" in body, "the routing 'Winner:' line vanished on a 2-member council"
    assert "Agreed claims" in body, "the 'Agreed claims' block vanished on a 2-member council"
    assert "Disagreed claims" in body, "the 'Disagreed claims' block vanished on a 2-member council"


def test_no_winner_multi_provider_council_states_the_verdict(tmp_path, monkeypatch):
    """A real TWO-DISTINCT-provider contest that the chairman left WITHOUT a winner
    (``routing_label.winner == ""``) must STATE the no-winner verdict, not leave the
    completed page headed straight into the claims with no top-line outcome.

    Before the fix the trophy ``winner-verdict`` (gated on the winner slug) and the
    ``solo-verdict`` (gated on ``isSoloFor``) BOTH suppressed, so the page showed
    "Agreed claims / Disagreed claims / Routing lesson" with NO verdict line at all —
    a sighted user finished reading a multi-model contest never told who won OR that
    nobody did. The static review page + share card both say "No winner" here; this
    live-page branch is the unfixed twin.

    Mutation-proven to bite: delete the ``no-winner-verdict`` / ``No clear winner``
    grid branches from ``render_live_council_page`` and this REDS — the completed
    multi-provider page paints the claims with no verdict, exactly the founder
    symptom. It is the no-winner sibling of the solo-suppression guards above; the
    duo positive control (``..._still_shows_winner_verdict``) proves the new branch
    does NOT fire when a winner WAS picked, and this council has 2 rows so it can't
    be a solo artifact."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_nowinner_duo"
    _seed(tmp_path, monkeypatch, _no_winner_duo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        verdict, body, rows = _drive(tmp_path, port, token)
    finally:
        httpd.shutdown()

    # A real contest: two distinct member rows prove this is NOT a solo artifact.
    assert rows == 2, (
        "expected exactly 2 member rows on a two-distinct-provider council, got "
        f"{rows} — without 2 voices this would (correctly) read as solo, not no-winner"
    )
    # The bug: with no winner, both the trophy and the solo line suppress, leaving
    # the verdict region EMPTY. The honest no-winner line must render instead.
    assert "No clear winner" in verdict, (
        "the LIVE page rendered a completed MULTI-provider council with an empty "
        "winner ('') but showed NO verdict line — the trophy gates on the winner slug "
        "and the solo line gates on isSoloFor, so both suppressed and a sighted user "
        "saw the agreed/disagreed claims with no top-line outcome at all (the static "
        f"review page says 'No winner' here). Verdict region text: {verdict!r}"
    )
    # The no-winner statement is also present in the routing-label grid (mirrors the
    # static page's 'No winner' line).
    assert "No clear winner." in body, (
        "the routing-label grid did not state the no-winner verdict — the grid jumped "
        "straight from the 'Routing label' eyebrow into the claims with no outcome line"
    )
    # It must NOT trip the SOLO framing — this is a real 2-provider contest, not a
    # 1-responder council.
    assert "One model answered — no council" not in body, (
        "the solo line leaked onto a real two-provider no-winner council — the "
        "no-winner branch must be distinct from the solo branch"
    )
    assert "the answer you'd have picked" not in body, (
        "the trophy winner-verdict rendered on a council with no winner"
    )
    # The claims still render — the no-winner verdict POINTS the user at them.
    assert "Disagreed claims" in body, (
        "the disagreed-claims block vanished on a no-winner council — the no-winner "
        "verdict's whole value is directing the user to where the models split"
    )


def test_same_provider_council_suppresses_winner_overclaim(tmp_path, monkeypatch):
    """The same-provider degenerate twin: a claude·claude·claude roster has three
    calls but ONE distinct voice, so the live page must read it as solo — NO
    "Claude — the answer you'd have picked" verdict, NO "Winner: Claude ·
    runner-up: Claude" line, NO self-agreement consensus block. The share card +
    static review page already guard this; this is the LIVE-page guard the static
    page's "the live page already collapses it" comment leans on but nothing
    proved. Mutation-proven to bite: revert isSoloFor to gate on the raw roster
    length (memberOrder/metadata.members) instead of the provider-collapsed
    responder count and this REDS — the page repaints "Claude · runner-up: Claude"
    on a council that had no contest."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_sameprov_overclaim"
    _seed(tmp_path, monkeypatch, _same_provider_status(token))
    httpd, port = _serve(tmp_path)
    try:
        verdict, body, rows = _drive(tmp_path, port, token)
    finally:
        httpd.shutdown()

    # The provider-keyed members map collapses the three claude calls to ONE row:
    # the page PROVES there's one distinct voice, so any winner claim is fabricated.
    assert rows == 1, (
        "expected exactly 1 member row on a same-provider (claude·claude·claude) "
        f"council — the provider-keyed members map must collapse the roster, got {rows}"
    )
    # THE BUG: the trophy verdict on a council with one distinct voice.
    assert "the answer you'd have picked" not in body, (
        "the LIVE page rendered the trophy winner-verdict on a same-provider "
        "(claude·claude·claude) council — a fabricated contest win the share card + "
        "static review page already suppress. Verdict text: " + repr(verdict)
    )
    # The founder symptom: "Winner: Claude · runner-up: Claude" — a model can't be
    # its own runner-up. The whole "Winner:" routing line must be gone.
    assert "Winner:" not in body, (
        "same-provider council still rendered a 'Winner:' routing line — winner AND "
        "runner-up keyed on the SAME slug ('Claude · runner-up: Claude'), the exact "
        "degenerate-overclaim founder symptom"
    )
    assert "Agreed claims" not in body, (
        "same-provider council still rendered an 'Agreed claims' block — one distinct "
        "voice can't reach consensus with itself"
    )
    assert "Disagreed claims" not in body, (
        "same-provider council still rendered a 'Disagreed claims' block"
    )
    # The honest framing REPLACES the overclaim (not a blank suppression).
    assert "One model answered — no council" in verdict, (
        "the solo verdict line did not render on a same-provider council — the "
        f"suppression must REPLACE the overclaim, not blank it. Verdict: {verdict!r}"
    )
    assert "no winner and nothing to agree on yet" in body, (
        "the routing-label solo line did not render on a same-provider council"
    )


def _same_provider_outcome() -> dict:
    """A persisted OUTCOME (the ``?council_id=`` post-hoc path, NOT the poll path)
    for an all-same-provider council. Unlike the on-disk STATUS sidecar — which is
    written ALREADY provider-collapsed (``{provider: {...} for provider in members}``)
    — the outcome JSON carries ``member_results`` as a LIST of three DISTINCT claude
    entries, exactly as ``council_runner`` records them. The collapse to one distinct
    voice happens in the page's ``outcomeToRunState`` (it keys the members map by
    ``m.provider``), so this path exercises a DIFFERENT collapse site than the poll
    path's ``_same_provider_status``. ``write_unified_council_page`` writes the
    ``review_pages/<id>.html`` redirect that lands a user on exactly this
    ``live_council.html?council_id=<id>`` surface for a completed council — a real,
    reachable post-hoc view, not a synthetic-only path."""
    return {
        "council_run_id": "c_sameprov_outcome",
        "task_text": "Same-provider triple-call council (outcome path).",
        "winner_provider": "claude",
        "synthesis_output": "Synthesis over one distinct provider.",
        "synthesis_output_clean": "Synthesis over one distinct provider.",
        "synthesis_html": "<p>Synthesis over one distinct provider.</p>",
        # THE LIST the outcome JSON actually carries — three distinct claude rows,
        # NOT a pre-collapsed map. outcomeToRunState must key by provider to collapse.
        "member_results": [
            {"provider": "claude", "model": "claude-opus-4-8",
             "output_text": "Claude run 1.", "output_html": "<p>Claude run 1.</p>"},
            {"provider": "claude", "model": "claude-opus-4-8",
             "output_text": "Claude run 2.", "output_html": "<p>Claude run 2.</p>"},
            {"provider": "claude", "model": "claude-opus-4-8",
             "output_text": "Claude run 3.", "output_html": "<p>Claude run 3.</p>"},
        ],
        "routing_label": {
            # The degenerate chairman output — winner IS its own runner-up.
            "winner": "claude",
            "runner_up": "claude",
            "confidence": "high",
            "agreed_claims": ["Self-agreement A", "Self-agreement B"],
            "disagreed_claims": [],
        },
        "metadata": {
            "chairman_provider": "claude",
            "council_id": "c_sameprov_outcome",
            "task_text": "Same-provider triple-call council (outcome path).",
        },
    }


def _seed_outcome(tmp_path, monkeypatch, outcome) -> str:
    """Seed the OUTCOME sidecar (``council_outcomes/<id>.js``) the ``?council_id=``
    path loads via ``loadOutcomeScript`` — distinct from ``_seed``'s status sidecar."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import review_pages_dir, state_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    outcomes_dir = state_dir() / "council_outcomes"
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    cid = outcome["council_run_id"]
    sidecar = (
        "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
        f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(cid)}] = {json.dumps(outcome)};\n"
    )
    (outcomes_dir / f"{cid}.js").write_text(sidecar, encoding="utf-8")
    return cid


def _drive_outcome(port, cid):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={cid}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            verdict = page.evaluate(
                "() => { const e = document.querySelector('.winner-verdict');"
                " return e ? e.textContent.trim() : ''; }"
            )
            body = page.evaluate("() => document.body.innerText")
            rows = page.evaluate(
                "() => document.querySelectorAll('.provider-status-row').length"
            )
            return verdict, body, rows
        finally:
            browser.close()


def test_same_provider_OUTCOME_path_suppresses_winner_overclaim(tmp_path, monkeypatch):
    """The ``?council_id=`` OUTCOME path twin of
    ``test_same_provider_council_suppresses_winner_overclaim``. The poll-path guard
    fed an ALREADY-collapsed provider-keyed members map; this one feeds the RAW
    ``member_results`` LIST (three distinct claude entries) the outcome JSON actually
    stores, so it exercises the ``outcomeToRunState`` collapse site the poll-path
    guard never touched. ``write_unified_council_page`` routes a real user here
    (``review_pages/<id>.html`` redirect → ``live_council.html?council_id=<id>``), so
    an all-same-provider post-hoc view must read solo just like the poll path.

    Mutation-proven to bite (UX sweep Iter 211): key ``outcomeToRunState``'s members
    map by member-INDEX (``m.provider + '__' + i``) instead of ``m.provider`` — the
    refactor the poll-path test's docstring WARNS about — and the three claude rows
    stop collapsing → respondedMembers=3 → ``isSoloFor`` false → the page repaints
    "🏆 Claude — the answer you'd have picked." + "Winner: Claude · runner-up: Claude"
    on a council with no contest. With provider-keying restored this stays green."""
    pytest.importorskip("playwright.sync_api")
    cid = _seed_outcome(tmp_path, monkeypatch, _same_provider_outcome())
    httpd, port = _serve(tmp_path)
    try:
        verdict, body, rows = _drive_outcome(port, cid)
    finally:
        httpd.shutdown()

    # outcomeToRunState keys the members map by provider → three claude
    # member_results collapse to ONE distinct row.
    assert rows == 1, (
        "expected exactly 1 member row on the OUTCOME path for a same-provider "
        "(claude·claude·claude) council — outcomeToRunState must collapse the "
        f"member_results LIST by provider, got {rows}"
    )
    assert "the answer you'd have picked" not in body, (
        "the LIVE page (?council_id= OUTCOME path) rendered the trophy winner-verdict "
        "on a same-provider council — the member_results list didn't collapse to one "
        "distinct voice. Verdict text: " + repr(verdict)
    )
    assert "Winner:" not in body, (
        "same-provider OUTCOME-path council still rendered a 'Winner:' routing line — "
        "'Claude · runner-up: Claude' (winner is its own runner-up), the degenerate "
        "founder symptom on the post-hoc ?council_id= surface"
    )
    assert "Agreed claims" not in body, (
        "same-provider OUTCOME-path council still rendered an 'Agreed claims' block"
    )
    assert "Disagreed claims" not in body, (
        "same-provider OUTCOME-path council still rendered a 'Disagreed claims' block"
    )
    assert "One model answered — no council" in verdict, (
        "the solo verdict line did not render on the OUTCOME path for a same-provider "
        f"council — the suppression must REPLACE the overclaim, not blank it. Verdict: {verdict!r}"
    )
    assert "no winner and nothing to agree on yet" in body, (
        "the routing-label solo line did not render on the OUTCOME path for a "
        "same-provider council"
    )


def _drive_chain(port, token):
    """Drive the live page and read the CHAIN-COMPOSER surface: whether the
    'Continue the thread' card is visibly rendered, plus the count of Continue/
    Auto-chain buttons and Quote ↓ buttons. A geometry/visibility read (computed
    display + live element counts), not a string-presence grep — the composer's
    `v-if="canChainNext"` either mounts the card subtree or it doesn't."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 393, "height": 900}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            return page.evaluate(
                "() => {"
                " const c = document.querySelector('.chain-actions');"
                " return {"
                "  chainVisible: c ? getComputedStyle(c).display !== 'none' : false,"
                "  chainButtons: document.querySelectorAll('.chain-button-row button').length,"
                "  quoteBtns: document.querySelectorAll('.quote-member-btn').length,"
                "  refineInput: !!document.querySelector('.chain-refine-input'),"
                "  rows: document.querySelectorAll('.provider-status-row').length,"
                "  soloLine: (document.querySelector('.solo-verdict') || {}).textContent || '',"
                "  rawLeak: (document.body.innerHTML || '').includes('{{'),"
                "  mounted: !!document.querySelector('#live-council-app'),"
                " };"
                "}"
            )
        finally:
            browser.close()


def test_solo_council_suppresses_chain_composer(tmp_path, monkeypatch):
    """SOLO suppression for the 'Continue the thread' chain composer (the isSoloFor
    class, applied to the chain affordance). On a 1-responder council the composer's
    entire value proposition is a MULTI-model dynamic that can't happen — its copy
    promises "each model sees the OTHERS' answers and refines" and "every model runs
    again", the Auto-chain button would re-run the same single model up to 3× (burning
    quota), and the Quote ↓ buttons stack "each member's answer". Worse, the page
    renders this composer DIRECTLY BELOW the solo verdict that tells the user the
    honest next step — "Enable a second provider to run a real contest" — a flat
    self-contradiction.

    Founder symptom: a 'Continue the thread / each model sees the others' answers'
    composer offered on a council where one model answered and the page just said
    'no council, enable a second provider'.

    Mutation-proven to bite (UX sweep 2026-06-21): revert canChainNext to drop the
    `if (this.isSoloFor(last)) return false;` line — the composer + its Continue/
    Auto-chain buttons + the Quote ↓ buttons all re-appear on the solo council and
    this REDS on chainVisible / chainButtons / quoteBtns. The duo positive control
    below proves the gate is the solo condition, not a blanket suppression.
    """
    pytest.importorskip("playwright.sync_api")
    token = "tok_solo_chain"
    _seed(tmp_path, monkeypatch, _solo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        s = _drive_chain(port, token)
    finally:
        httpd.shutdown()

    # BITE PRECONDITION (A): the live app mounted with no raw template leak — the
    # suppression asserts below aren't vacuously passing on an un-mounted page.
    assert s["mounted"] and not s["rawLeak"], (
        f"the live council app didn't mount cleanly (mounted={s['mounted']}, "
        f"rawLeak={s['rawLeak']}) — the chain-composer assertions would be hollow"
    )
    # BITE PRECONDITION (B): the seed is genuinely solo — exactly ONE member row, and
    # the honest solo line rendered (so the page DID prove there's no contest, which
    # is what makes the composer a contradiction). Checked render-independently below
    # via _solo_status's single-member roster.
    assert s["rows"] == 1, f"expected exactly 1 member row on a solo council, got {s['rows']}"
    assert "Enable a second provider" in s["soloLine"], (
        "the solo verdict line ('… Enable a second provider to run a real contest') "
        f"must render — it's the honest guidance the composer contradicts. Got: {s['soloLine']!r}"
    )
    # THE FIX: the multi-model chain composer must NOT render on a solo council.
    assert not s["chainVisible"], (
        "the 'Continue the thread' chain composer rendered on a SOLO 1-responder "
        "council — its copy promises 'each model sees the OTHERS' answers and refines' "
        "and Auto-chain re-runs the same single model up to 3×, offered DIRECTLY BELOW "
        "'Enable a second provider to run a real contest'. canChainNext must gate on "
        "isSoloFor (the live-page winner/agreed-claims solo-suppression class)."
    )
    assert s["chainButtons"] == 0, (
        f"the Continue / Auto-chain buttons rendered on a solo council ({s['chainButtons']} "
        "buttons) — a multi-model affordance on a one-model council"
    )
    assert s["quoteBtns"] == 0, (
        f"Quote ↓ buttons rendered on a solo council ({s['quoteBtns']}) — there are no "
        "OTHER members' answers to stack into a refine input that itself shouldn't exist"
    )
    assert not s["refineInput"], "the chain refine input rendered on a solo council"


def test_two_member_council_still_shows_chain_composer(tmp_path, monkeypatch):
    """Positive control: the solo gate on canChainNext must NOT suppress the chain
    composer on a real 2-member contest — otherwise a passing solo test could be a
    blanket suppression bug. The composer, both Continue/Auto-chain buttons, the
    refine input, and a Quote ↓ per member must all render."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_duo_chain"
    _seed(tmp_path, monkeypatch, _duo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        s = _drive_chain(port, token)
    finally:
        httpd.shutdown()

    assert s["rows"] == 2, f"expected 2 member rows on a duo council, got {s['rows']}"
    assert s["chainVisible"], (
        "the 'Continue the thread' chain composer vanished on a real 2-member council "
        "— the canChainNext solo gate over-suppressed a genuine contest"
    )
    assert s["chainButtons"] == 2, (
        f"expected Continue + Auto-chain (2 buttons) on a duo council, got {s['chainButtons']}"
    )
    assert s["quoteBtns"] == 2, (
        f"expected one Quote ↓ per done member (2) on a duo council, got {s['quoteBtns']}"
    )
    assert s["refineInput"], "the chain refine input vanished on a real 2-member council"
    assert "Enable a second provider" not in s["soloLine"], (
        "the solo line leaked onto a real 2-member council"
    )


def _drive_badge(port, token):
    """Drive the live page and read the per-member WINNER-MARK surface: the count of
    'Lens pick' badges and the count of rows carrying the `.winner-reveal` highlight
    class, plus the badge-explainer caption text. Both the badge and the highlight
    gate on the `isLensPick(seg, row)` getter — a geometry/visibility read (live
    element counts + the rendered caption), not a string-presence grep."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}"
            )
            page.wait_for_timeout(2600)
            assert not errs, f"JS pageerrors: {errs[:3]}"
            return page.evaluate(
                "() => {"
                " const badge = Array.from(document.querySelectorAll('.provider-status-badge'))"
                "   .filter(e => e.textContent.trim() === 'Lens pick').length;"
                " const reveal = document.querySelectorAll('.provider-status-row.winner-reveal').length;"
                " return {"
                "  lensPickBadges: badge,"
                "  winnerReveal: reveal,"
                "  rows: document.querySelectorAll('.provider-status-row').length,"
                "  caption: document.body.innerText,"
                "  rawLeak: (document.body.innerHTML || '').includes('{{'),"
                "  mounted: !!document.querySelector('#live-council-app'),"
                " };"
                "}"
            )
        finally:
            browser.close()


def test_solo_council_suppresses_lens_pick_badge(tmp_path, monkeypatch):
    """SOLO suppression for the per-member 'Lens pick' badge AND the `winner-reveal`
    row highlight (both gate on the `isLensPick(seg, row)` getter — the solo-blind
    sibling of the winner-verdict / Winner: line / chain composer that already
    suppress via isSoloFor).

    A solo council's chairman STILL emits ``routing_label.winner = <the lone
    provider>`` (the chairman runs regardless of member count — see
    ``_solo_status`` where winner == "claude"). So ``lensPickProviderFor`` resolves
    to the lone member and ``isLensPick`` returned true → the live page painted a
    'Lens pick' badge AND a green winner-reveal highlight on the ONE member row,
    directly under its own 'One model answered — no council' verdict. A 'Lens pick'
    badge means "the chairman picked THIS over the others" — degenerate framing on a
    council with no others. The SHARE CARD never paints a per-member winner mark in
    its solo branch; this was the live-page twin that drifted unfixed.

    Founder symptom: a 'Lens pick' winner badge + winner highlight on the lone
    member of a 1-responder council that the page itself called 'no council'.

    Mutation-proven to bite (UX sweep 2026-06-22): remove the
    ``if (this.isSoloFor(seg)) return false;`` line from ``isLensPick`` — the badge
    + highlight re-appear on the solo member and this REDS on lensPickBadges /
    winnerReveal. The duo positive control below proves the gate is the solo
    condition, not a blanket suppression.
    """
    pytest.importorskip("playwright.sync_api")
    token = "tok_solo_badge"
    _seed(tmp_path, monkeypatch, _solo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        s = _drive_badge(port, token)
    finally:
        httpd.shutdown()

    # BITE PRECONDITION (A): the live app mounted with no raw template leak — the
    # suppression asserts below aren't vacuously passing on an un-mounted page.
    assert s["mounted"] and not s["rawLeak"], (
        f"the live council app didn't mount cleanly (mounted={s['mounted']}, "
        f"rawLeak={s['rawLeak']}) — the badge assertions would be hollow"
    )
    # BITE PRECONDITION (B): the seed is genuinely solo (exactly ONE member row) AND
    # carries a chairman winner (checked render-independently: _solo_status sets
    # routing_label.winner == "claude" == the lone member, the degenerate shape that
    # makes isLensPick fire without the gate).
    assert s["rows"] == 1, f"expected exactly 1 member row on a solo council, got {s['rows']}"
    assert _solo_status(token)["synthesis"]["routing_label"]["winner"] == "claude", (
        "the solo fixture must carry a chairman winner == the lone provider — that's "
        "the degenerate shape that makes the badge fire without the isSoloFor gate"
    )
    # THE FIX: no per-member winner mark on a solo council.
    assert s["lensPickBadges"] == 0, (
        f"a 'Lens pick' winner badge rendered on a SOLO 1-responder council "
        f"({s['lensPickBadges']} badge(s)) — the chairman emits winner=<lone provider> "
        "on a solo council, so isLensPick fired on the lone member and painted a "
        "'chairman picked this over the others' badge with no others to pick over. "
        "isLensPick must gate on isSoloFor."
    )
    assert s["winnerReveal"] == 0, (
        f"the green 'winner-reveal' highlight rendered on a SOLO member row "
        f"({s['winnerReveal']} row(s)) — same isLensPick overclaim (the badge + the "
        "highlight share the getter)"
    )
    # The honest solo badge-explainer IS present (the suppression REPLACES the
    # 'Lens pick badge marks the chairman's pick' caption, not blanks it).
    assert "With a second provider enabled, the chairman would mark" in s["caption"], (
        "the solo badge-explainer ('… With a second provider enabled, the chairman "
        "would mark a Lens pick here') did not render — the suppression must replace "
        "the multi-model caption, not leave the section captionless"
    )


def test_two_member_council_still_shows_lens_pick_badge(tmp_path, monkeypatch):
    """Positive control: the isSoloFor gate on ``isLensPick`` must NOT suppress the
    'Lens pick' badge + winner-reveal highlight on a real 2-member contest where the
    chairman picked a winner — otherwise a passing solo test could be a blanket
    suppression bug. Exactly ONE badge + ONE highlighted row (the winner)."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_duo_badge"
    _seed(tmp_path, monkeypatch, _duo_status(token))
    httpd, port = _serve(tmp_path)
    try:
        s = _drive_badge(port, token)
    finally:
        httpd.shutdown()

    assert s["rows"] == 2, f"expected 2 member rows on a duo council, got {s['rows']}"
    assert s["lensPickBadges"] == 1, (
        f"expected exactly one 'Lens pick' badge on a 2-member council with a chairman "
        f"winner, got {s['lensPickBadges']} — the isSoloFor gate over-suppressed a real contest"
    )
    assert s["winnerReveal"] == 1, (
        f"expected exactly one winner-reveal highlighted row on a duo council, got "
        f"{s['winnerReveal']}"
    )
    assert "With a second provider enabled, the chairman would mark" not in s["caption"], (
        "the solo badge-explainer leaked onto a real 2-member council"
    )


def test_all_failed_completed_council_does_not_claim_one_model_answered(tmp_path, monkeypatch):
    """Iter 269: a completed-but-ALL-FAILED council (0 distinct responders) must NOT
    paint the SOLO verdict 'One model answered — no council' — ZERO models answered.
    The honest all-failed line ('Every provider attempted but failed to respond …')
    is the verdict here; the solo claim sat on top of it as a flat self-contradiction.

    ``isSoloFor`` is TRUE for 0 responders (responder count <= 1), so the solo-verdict /
    solo routing line / 'the one model that answered' caption all fired with no
    responder-count floor. The fix requires ``respondedMembersFor(seg) >= 1`` for the
    SOLO claim, leaving the all-failed disclosure as the sole verdict.

    Mutation-proven to bite: revert the ``respondedMembersFor(seg) >= 1`` gate on the
    solo-verdict in ``render_live_council_page`` and this REDS — 'One model answered'
    re-appears under the all-failed banner (the founder symptom)."""
    pytest.importorskip("playwright.sync_api")
    token = "tok_all_failed"
    status = _all_failed_completed_status(token)
    _seed(tmp_path, monkeypatch, status)
    httpd, port = _serve(tmp_path)
    try:
        verdict, body, rows = _drive(tmp_path, port, token)
    finally:
        httpd.shutdown()

    # Bite-precondition (A): the page mounted and painted (no raw template leak).
    assert "{{" not in body, f"petite-vue did not mount — raw template leak: {body[:120]!r}"
    # Bite-precondition (B): the fixture IS the all-failed shape — BOTH members
    # carry status 'failed' (checked render-independently on the fixture constants),
    # so respondedMembersFor == 0.
    assert all(m["status"] == "failed" for m in status["members"].values()), (
        "fixture is not all-failed — every member must be status:'failed'"
    )
    assert len(status["members"]) == 2
    # THE BUG: "One model answered" must NOT appear when ZERO models answered.
    assert "One model answered" not in body, (
        "FOUNDER SYMPTOM (Iter 269): the LIVE page painted the SOLO verdict 'One "
        "model answered — no council' on a completed-but-ALL-FAILED council (0 "
        "responders) — a flat lie that sat directly on top of the honest all-failed "
        f"line. Verdict region: {verdict!r}"
    )
    assert "Only one model responded" not in body, (
        "the solo routing-label line ('Only one model responded …') leaked onto a "
        "0-responder all-failed council"
    )
    assert "The one model that answered" not in body, (
        "the solo 'Full Responses' caption ('The one model that answered') leaked "
        "onto a 0-responder all-failed council"
    )
    # The honest all-failed verdict IS present (the suppression REPLACES, not blanks).
    assert "no synthesis to show" in body, (
        "the honest all-failed line ('Every provider attempted but failed to respond "
        "— there's no synthesis to show') did not render — the page must state the "
        "total failure, not go silent"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
