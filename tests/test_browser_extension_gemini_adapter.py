"""Fixture-based unit test for browser-extension/adapters/gemini.js.

Task #135 (v1.8): gemini.google.com adapter. Different from claude /
chatgpt because Google's batchexecute RPC is NOT SSE — it's a chunked
length-prefixed JSON envelope with double-encoded inner payloads.

Runs the JS adapter through node against a synthetic batchexecute body;
asserts conv_id (from page URL, not the RPC URL) + best-effort
assistant_text extraction. Skips when node isn't on PATH.

Why no fixture file: Gemini's batchexecute frame shape rotates across
Google's frontend releases. Pinning a real captured body would brittle
the test against shape rotation. Instead, we generate a minimal
synthetic frame matching the documented wire format and assert the
parser's robustness primitives (frame splitter, wrb.fr extractor,
longest-prose walker).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = REPO_ROOT / "browser-extension" / "adapters" / "gemini.js"


def _make_batchexecute_body(assistant_text: str, message_id: str = "msg-abcd1234") -> str:
    """Build a synthetic batchexecute body matching Gemini's wire format.

    Wire format:
        )]}'
        <length>\n
        [["wrb.fr","<rpc>","<double-encoded inner JSON>",null,null,"<msg_id>"]]\n
    """
    inner = json.dumps([[
        "irrelevant_envelope_field",
        ["candidate_id", assistant_text, "more_metadata"],
    ]])
    frame = json.dumps([
        ["wrb.fr", "vfBeAd", inner, None, None, message_id],
    ])
    return f")]}}'\n{len(frame)}\n{frame}\n"


def _run_adapter(input_obj: dict) -> dict:
    script = f"""
    const adapter = require({json.dumps(str(ADAPTER_PATH))});
    const result = adapter.adapt({json.dumps(input_obj)});
    process.stdout.write(JSON.stringify(result));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def _node_available() -> bool:
    return shutil.which("node") is not None


def test_adapter_file_exists():
    assert ADAPTER_PATH.exists(), f"adapter missing: {ADAPTER_PATH}"


def test_adapter_reports_correct_provider():
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("The cross-provider memory layer the labs are commercially prevented from building."),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/abc123def456",
    })
    assert result["provider"] == "gemini"


def test_adapter_kind_is_adapter_stream():
    """Critical: must be `adapter_stream`, not `stream`. The capture
    host writes adapter_stream payloads under `<conv_id>.stream.json`;
    `stream` would create urlhash-keyed orphans (the v1.7 gap).
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter."),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/abc123def456",
    })
    assert result["kind"] == "adapter_stream"


def test_adapter_extracts_conv_id_from_app_path():
    """Gemini's URL shape /app/<id> is the v1 path. Adapter must pull
    conv_id from page_href because the batchexecute URL doesn't have one.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute?rpcids=...",
        "body_text": _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter."),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-id-from-url",
    })
    assert result["conv_id"] == "conv-id-from-url"


def test_adapter_extracts_conv_id_from_c_query_param():
    """Alternative URL shape: ?c=<id>"""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter."),
        "method": "POST",
        "page_href": "https://gemini.google.com/?c=conv-from-query",
    })
    assert result["conv_id"] == "conv-from-query"


def test_adapter_extracts_assistant_text_from_wrb_fr_payload():
    """Longest-prose-leaf walker pulls the model reply from the
    double-encoded inner JSON.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    reply = "Trinity Local is the cross-provider memory layer the labs are commercially prevented from building."
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body(reply),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    assert result["assistant_text"] == reply


def test_adapter_extracts_message_id():
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter.", message_id="msg-mid-12345"),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    assert result["message_id"] == "msg-mid-12345"


def test_adapter_preserves_raw_body_for_reextraction():
    """Gemini's frame shape is unstable. Raw body MUST be preserved so
    a future ingest run with an updated extractor can re-parse without
    re-capturing.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    body = _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter.")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": body,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    assert result["_raw_body"] == body


def test_adapter_suppresses_empty_body():
    """An empty body carries no conversation — the adapter returns null so
    page-hook skips the emit (the over-capture noise fix). Must not crash:
    null is a clean return, not a throw. (Was: returned an empty-content
    object; that object was exactly the disk noise we now drop.)"""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": "",
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    assert result is None, f"empty body should be suppressed (null), got: {result}"


def test_adapter_handles_missing_xssi_prefix():
    """Some Gemini frontend variants ship batchexecute responses WITHOUT
    the )]}' XSSI prefix. Adapter must handle both.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    body_without_prefix = _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter.")
    body_without_prefix = body_without_prefix.replace(")]}'\n", "", 1)
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": body_without_prefix,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    assert "Trinity" in result["assistant_text"] or "Gemini" in result["assistant_text"]


def test_adapter_skips_malformed_frames_without_crashing():
    """A truncated mid-frame body (network drop) must not crash the
    adapter — capture must never break the page.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    truncated = ")]}'\n100\n[[\"wrb.fr\",\"abc\","  # length 100 but body cut short
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": truncated,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    # Doesn't crash (null is a clean return). A truncated frame yields no prose
    # and no prompt → suppressed, same as telemetry. The point of this test is
    # "no throw on bad input" — null satisfies that.
    assert result is None


def test_adapter_suppresses_telemetry_rpc():
    """THE over-capture fix. `/batchexecute` is a generic Google RPC endpoint —
    every gemini turn also fires control/telemetry RPCs (bard_activity_enabled,
    config, …) that classifyRequest can't distinguish from the conversation
    (their rpcids aren't stable enough to allowlist). A telemetry frame parses
    fine but carries NO prose answer and NO user prompt — pure noise that filled
    the captures dir ~98% with garbage. The adapter must return null so the emit
    is skipped and nothing lands on disk. Content-defined, not rpcid-defined."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    # A real batchexecute frame whose inner payload is only config tokens /
    # booleans / short ids — nothing that clears the ≥30-char, ≥5-word prose bar.
    inner = json.dumps([["bard_activity_enabled", 1, True, "v2", "ABCD1234"]])
    frame = json.dumps([["wrb.fr", "vfBeAd", inner, None, None, "msg-telemetry"]])
    telemetry_body = f")]}}'\n{len(frame)}\n{frame}\n"
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute?rpcids=...",
        "body_text": telemetry_body,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-real-thread",
        # NO request_body → no user prompt either
    })
    assert result is None, f"telemetry RPC must be suppressed (null), got: {result}"


def test_adapter_keeps_prompt_only_rpc_when_reply_absent():
    """Accept side: the chat-send RPC carries the user PROMPT in the request
    body but no assistant prose (the reply streams later via StreamGenerate).
    user_text alone must keep the capture — the suppression gate fires only when
    BOTH prompt and answer are empty. (Pairs with the telemetry test —
    [[test_the_boundary_and_the_action]].)"""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    prompt = "Design a cross-provider memory layer the labs can't build because they can't see across each other."
    # f.req shape the adapter's longest-prose walker reads the prompt out of.
    import urllib.parse
    f_req = json.dumps([[["StreamGenerate", json.dumps([[prompt]]), None, "generic"]]])
    request_body = "f.req=" + urllib.parse.quote(f_req) + "&at=tok"
    # Empty-content response (reply not in this RPC).
    empty_frame = json.dumps([["wrb.fr", "vfBeAd", json.dumps([["x"]]), None, None, "m"]])
    body = f")]}}'\n{len(empty_frame)}\n{empty_frame}\n"
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": body,
        "request_body": request_body,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-real-thread",
    })
    assert result is not None, "a prompt-only RPC must be kept (the prompt is the corpus value)"
    assert prompt[:20] in (result.get("user_text") or ""), f"user prompt lost: {result.get('user_text')!r}"


def test_adapter_extracts_user_prompt_from_request_body():
    """Critical for Gemini: the user's prompt only exists in the
    batchexecute REQUEST body (Google's RPC response is reply-only).
    Without this extraction, gemini captures contribute zero
    PromptTurn entries — iter_prompt_turns only yields user-facing
    turns and the assistant-only session has none.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    # Simulate Gemini's batchexecute request body shape:
    #   f.req=<url-encoded JSON>&at=<csrf-token>
    user_prompt = "Refactor the auth flow to use OAuth instead of API keys."
    inner = json.dumps([user_prompt, 0, None, [["context"]]])
    rpc_args = json.dumps([[["StreamGenerate", inner, None, "generic"]]])
    # URL-encode the outer JSON the way batchexecute does
    import urllib.parse
    request_body = "f.req=" + urllib.parse.quote(rpc_args) + "&at=anti-csrf-token"
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("Sure, here's how to refactor the auth flow to use OAuth properly."),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
        "request_body": request_body,
    })
    assert result["user_text"] == user_prompt


def test_adapter_returns_empty_user_text_when_no_request_body():
    """Pre-v1.8 captures (without request_body field) — adapter must
    not crash; just return empty user_text.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("Some Gemini reply with sufficient prose to pass the length filter."),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-x",
    })
    assert result["user_text"] == ""


def test_adapter_file_stem_uses_message_id_when_present():
    """file_stem (per-call discriminator for the on-disk filename) should
    prefer the assistant message_id when extractable. Without this, every
    gemini RPC for a conversation overwrites the previous on disk
    (#145 — caught live 2026-05-23 when StreamGenerate content was
    masked by trailing batchexecute telemetry)."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("A Gemini reply with sufficient prose to pass the length filter.", message_id="msg-deadbeef9999"),
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-xyz",
        "captured_at": "2026-05-24T00:30:47.199Z",
    })
    assert result["conv_id"] == "conv-xyz"
    assert result["message_id"] == "msg-deadbeef9999"
    assert result["file_stem"] == "conv-xyz__msg-deadbeef9999"


def test_adapter_file_stem_falls_back_to_captured_at():
    """When no message_id is extractable (some RPC frames don't carry
    one), file_stem falls back to the captured_at timestamp so RPCs
    still land distinctly."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    # Synthesize a body where extractMessageId() returns null — message_id
    # field is empty/non-hex-like.
    frame = json.dumps([["wrb.fr", "vfBeAd", json.dumps([["x", "Some prose to pass the filter long enough"]]), None]])
    body = f")]}}'\n{len(frame)}\n{frame}\n"
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": body,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-no-msg",
        "captured_at": "2026-05-24T00:30:47.199Z",
    })
    assert result["conv_id"] == "conv-no-msg"
    assert result["message_id"] is None
    # YYYYMMDDHHMMSSXXX (captured_at digits, first 17)
    assert result["file_stem"].startswith("conv-no-msg__2026052400")


def test_parser_ignores_unreliable_length_prefix():
    """Google's framing prefix is off-by-N in live captures (2026-05-23):
    declared `36816`, actual JSON char count `36814`. Old parser sliced
    by the declared length → trailing chars made JSON.parse fail →
    `frames_count: 0` → empty content. New parser uses a brace-depth
    scan to find the real end of each JSON value and ignores the prefix.

    This test synthesizes a body with a deliberately-wrong length prefix
    (claims 2 chars too many — matches the live drift exactly) and
    asserts the parser still extracts the frame.
    """
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    inner = json.dumps([[
        "irrelevant_envelope",
        ["candidate_id", "An assistant reply with enough prose to pass the length filter."],
    ]])
    frame = json.dumps([["wrb.fr", "vfBeAd", inner, None, None, "msg-test123"]])
    # Lie about the length: claim 2 chars too many (matches the
    # live-capture off-by-2 drift). Real parser must still extract.
    fake_length = len(frame) + 2
    body = f")]}}'\n\n{fake_length}\n{frame}\n"
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": body,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-test",
    })
    assert result["frames_count"] >= 1, (
        f"parser bailed on off-by-N length prefix; got frames_count="
        f"{result['frames_count']}"
    )
    assert "An assistant reply" in (result["assistant_text"] or "")


def test_parser_handles_multi_frame_body():
    """Real gemini responses contain multiple length-prefixed frames
    (the main wrb.fr row + housekeeping `di` + `af.httprm` + `e` frames).
    Parser must walk all of them, not stop at the first."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    main_inner = json.dumps([["x", ["cand", "Long assistant reply prose with multiple words."]]])
    main_frame = json.dumps([["wrb.fr", "vfBeAd", main_inner, None, None, "msg-main"]])
    di_frame = json.dumps([["di", 208], ["af.httprm", 208, "-839", 18]])
    e_frame = json.dumps([["e", 4, None, None, 166]])
    # Off-by-1 / off-by-2 / exact — mix to stress the brace-depth scan.
    body = (
        f")]}}'\n\n"
        f"{len(main_frame) + 2}\n{main_frame}\n"
        f"{len(di_frame)}\n{di_frame}\n"
        f"{len(e_frame) - 1}\n{e_frame}\n"
    )
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": body,
        "method": "POST",
        "page_href": "https://gemini.google.com/app/conv-multi",
    })
    assert result["frames_count"] >= 2, (
        f"parser stopped early on multi-frame body; got frames_count="
        f"{result['frames_count']}"
    )
    assert "Long assistant reply" in (result["assistant_text"] or "")


def test_adapter_file_stem_null_when_no_conv_id():
    """No conv_id (user on /app root) → file_stem null → capture host's
    conv_id-required gate still drops it (per existing semantics)."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "body_text": _make_batchexecute_body("A Gemini reply with sufficient prose to pass the length filter."),
        "method": "POST",
        "page_href": "https://gemini.google.com/",
        "captured_at": "2026-05-24T00:30:47.199Z",
    })
    assert result["conv_id"] is None
    assert result["file_stem"] is None


# ── Over-capture gate: the "generic" envelope-marker leak ──────────────────────
# gemini.google.com fires the GENERIC /batchexecute for every control/telemetry
# RPC (config, sidebar, activity) alongside the real chat RPC. The 2026-06-05
# content gate (adapt() → null when neither user prompt NOR assistant prose) is
# supposed to drop those. But extractUserPrompt walked the f.req envelope and
# treated raw unparseable top-level strings as prompt candidates — so the literal
# "generic" source-path marker at [0][0][3] won the longest-prose contest for a
# telemetry RPC (which has no real prose), making user_text="generic" (non-empty)
# and DEFEATING the gate. Every control batchexecute then emitted a junk capture
# (~98% of the captures dir; 58 files per real conversation, observed 2026-06-06).
# Fix: don't push the structural envelope tokens (rpcid + "generic") as prompt
# candidates — the user's prompt is always in the parseable inner RPC args.


def _make_request_body(rpcid: str, inner_args) -> str:
    """Build a gemini batchexecute POST body: f.req=<url-encoded outer envelope>.

    The outer envelope is [[[rpcid, <double-encoded inner args JSON>, null,
    "generic"]]] — "generic" is Google's structural source-path marker, NOT a
    user prompt. inner_args is JSON-encoded again (gemini double-encodes)."""
    import urllib.parse
    outer = [[[rpcid, json.dumps(inner_args), None, "generic"]]]
    return "f.req=" + urllib.parse.quote(json.dumps(outer)) + "&at=ANtoken123"


def test_telemetry_rpc_with_generic_marker_is_dropped():
    """A control/telemetry batchexecute (no user prose in the inner args, only
    the structural "generic" marker in the envelope) must DROP to null — not
    emit a capture whose user_text is the literal "generic"."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "/_/BardChatUi/data/batchexecute?rpcids=VxUbXb&source-path=%2Fapp%2Fabc123",
        "page_href": "https://gemini.google.com/app/abc123def456",
        # inner args carry no prose — just structural ints (telemetry payload).
        "request_body": _make_request_body("VxUbXb", [1, [2], None, 0]),
        "body_text": ")]}'\n10\n[[\"wrb.fr\",\"VxUbXb\",\"[]\",null,null,null,\"generic\"]]\n",
        "method": "POST",
        "captured_at": "2026-06-06T17:37:50.367Z",
    })
    assert result is None, (
        "a telemetry batchexecute leaked a capture — the 'generic' envelope "
        f"marker defeated the over-capture gate; got: {result!r}"
    )


def test_real_chat_prompt_extracted_from_inner_args_not_dropped():
    """The real chat-send RPC carries the user's prompt double-encoded in the
    inner args. It must be extracted (not blocked by the over-capture fix) so
    the conversation is captured."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    prompt = "Why did Greenspan hike rates in the late 90s?"
    result = _run_adapter({
        "url": "/_/BardChatUi/data/batchexecute?rpcids=Bsxleb&source-path=%2Fapp%2Fabc123",
        "page_href": "https://gemini.google.com/app/abc123def456",
        "request_body": _make_request_body("Bsxleb", [[prompt, 0, None]]),
        "body_text": ")]}'\n10\n[[\"wrb.fr\",\"Bsxleb\",\"[[null,[\\\"reply\\\"]]]\",null,null,\"msg-x1\"]]\n",
        "method": "POST",
        "captured_at": "2026-06-06T17:40:00.000Z",
    })
    assert result is not None, "the real chat-send RPC was wrongly dropped"
    assert result["user_text"] == prompt, (
        f"the real prompt was not extracted from the inner args; got "
        f"user_text={result.get('user_text')!r}"
    )


def test_one_word_real_prompt_in_inner_args_survives():
    """A genuine 1-word prompt ('continue') lives in the parseable inner args,
    so the structural-token skip must NOT drop it — only the OUTER envelope
    tokens (rpcid, 'generic') are excluded, never inner-args content."""
    if not _node_available():
        import pytest
        pytest.skip("node not available")
    result = _run_adapter({
        "url": "/_/BardChatUi/data/batchexecute?rpcids=Bsxleb&source-path=%2Fapp%2Fzzz999",
        "page_href": "https://gemini.google.com/app/zzz999aaa111",
        "request_body": _make_request_body("Bsxleb", [["continue", 0, None]]),
        "body_text": ")]}'\n10\n[[\"wrb.fr\",\"Bsxleb\",\"[[null,[\\\"ok\\\"]]]\",null,null,\"msg-y2\"]]\n",
        "method": "POST",
        "captured_at": "2026-06-06T18:00:00.000Z",
    })
    assert result is not None and result["user_text"] == "continue", (
        f"a 1-word real prompt in the inner args was dropped; got {result!r}"
    )
