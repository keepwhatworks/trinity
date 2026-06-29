"""Security invariant: the Chrome extension's `page-hook.js` (the fetch-wrapping
capture source, MAIN world) must capture only the request/response BODIES — the
conversation prompts + answers — NEVER the request HEADERS.

Provider API calls (claude.ai / chatgpt.com / gemini.google.com) carry the
user's session credential in `init.headers` (Authorization: Bearer …, Cookie).
If page-hook ever serialized `init.headers` into a captured payload, those
tokens would be written to ~/.trinity/conversations/<provider>/*.json on disk —
a credential leak distinct from the corpus-content leak. Audited clean
2026-06-01 (page-hook captures `init.body` → `request_body` + `response.body`
→ `body_text`, no header access at all); this guards against a regression that
re-introduces header capture into the untrusted-data boundary.
"""
from __future__ import annotations

from pathlib import Path

PAGE_HOOK = Path(__file__).resolve().parent.parent / "browser-extension" / "page-hook.js"


def _src() -> str:
    return PAGE_HOOK.read_text(encoding="utf-8")


def test_page_hook_never_reads_request_headers():
    src = _src()
    # Any access to init.headers in the capture source warrants a security
    # review — the captured payload must not be able to carry the auth header.
    assert "init.headers" not in src, (
        "page-hook.js must NOT read init.headers — the provider Authorization/"
        "Cookie token would land in ~/.trinity/conversations/*.json on disk. "
        "If a header IS genuinely needed (e.g. content-type), extract only that "
        "field explicitly and update this guard with the rationale."
    )


def test_page_hook_has_no_auth_token_material():
    src = _src()
    for token in ("Authorization", "authorization", "Cookie", "Bearer"):
        assert token not in src, (
            f"page-hook.js references {token!r} — the capture source must never "
            f"touch credential material"
        )


def test_request_body_sourced_from_init_body_only():
    src = _src()
    # The captured `request_body` (the user's prompt) is sourced from the
    # request BODY, never headers. Pins the safe provenance.
    assert "request_body = init.body" in src, (
        "request_body must be the request body (init.body), not headers"
    )
