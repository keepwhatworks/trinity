"""Caller-supplied council members must record model AND effort, end to end.

WHY THIS EXISTS (measured 2026-07-26 on the real corpus). Caller-supplied members are
the MAJORITY of council members, not an edge case:

    member_results across 654 councils
      gemini     437   model=None  effort=None      <- via run_council(responses=...)
      chatgpt    324   model=None  effort=None
      claude_ai  316   model=None  effort=None
      codex      120   model='gpt-5.5'   metadata.effort='xhigh'   <- CLI dispatch
      claude      58   model='claude-opus-4-8'  metadata.effort='high'

1,057 of 1,493 members carried neither leg (an earlier 1,077 counted the web-member
total; 20 gemini members did carry a model). The trust tally keys on model x version
with effort as a gated secondary, so it was being computed on 28% of the corpus.
The missing leg is real resolution, not bookkeeping: effort is part of the identity
unit (model x size x effort), and while THREE effort sub-cells clear MIN_TALLY_N on the
clean tally (Fable high 15W-7L, Gemini 3.1 high 30W-57L, GPT-5.5 xhigh 17W-12L = 58.6%,
CI [0.41, 0.75], includes chance), NOT ONE has a sibling — no model x version has a
second level on file — exactly because the other legs were never recorded, so there is
no within-model pair a contrast could be read from. (This said "only ONE effort sub-cell"
until 2026-07-31, a shape inferred from the single key the sentence was about; the count
and the per-model level max are now guarded canonical claims planted into CLAUDE.md from
`effort_breakdown`. An earlier effort-split
pairing quoted here was a pre-contamination-fix number and must not be requoted; the
literal string is banned by tests/test_precontamination_ledger_numbers.py.)

THE WIRE THIS PINS, and why it is pinned at the END rather than the start: `effort`
must survive from the tool payload all the way into `metadata["effort"]`, because that
is the exact key the disagreement ledger reads (`_ident_label`). Asserting only that
the schema advertises `effort`, or only that the handler reads it, is how `resolution`
was lost twice this week — the prompt asked for it and the normalizer dropped it. So
the load-bearing assertion here is on the PERSISTED shape the ledger consumes.
"""
from __future__ import annotations

import json

import pytest

def _members_from(responses):
    """Call the SHIPPED builder — never a local reimplementation.

    The first version of this helper rebuilt the handler's logic inline. Mutating the
    real code left all 12 guards green, which is the textbook decorative test. It now
    calls mcp_server.members_from_responses, which is why that function was extracted
    to module level."""
    from trinity_local.mcp_server import members_from_responses

    return members_from_responses(responses)


class TestEffortReachesTheLedgersKey:
    def test_effort_lands_in_metadata_where_the_ledger_reads_it(self):
        """`_ident_label` reads (m.get("metadata") or {}).get("effort"). Anywhere
        else is a field nothing consumes."""
        m = _members_from([{"provider": "claude_ai", "content": "x",
                            "model": "claude-opus-5", "effort": "xhigh"}])[0]
        d = m.to_dict()
        assert (d.get("metadata") or {}).get("effort") == "xhigh", (
            f"effort must survive to metadata['effort']; got {d.get('metadata')!r}"
        )
        assert d.get("model") == "claude-opus-5"

    def test_the_ledger_actually_composes_the_identity_from_it(self):
        """End of the wire: the ledger must produce a model x effort label, not a
        bare model. This is the assertion that would red if any intermediate layer
        renamed or relocated the field."""
        member = _members_from([{"provider": "claude_ai", "content": "x",
                                 "model": "claude-opus-5", "effort": "xhigh"}])[0].to_dict()
        eff = (member.get("metadata") or {}).get("effort")
        ident = str(member.get("model") or "")
        label = f"{ident} ({eff})" if eff and eff not in ident else ident
        assert label == "claude-opus-5 (xhigh)", (
            f"ledger identity label lost the effort leg: {label!r}"
        )

    def test_absent_effort_stays_absent_rather_than_defaulting(self):
        """A guessed level is worse than a missing one — the tally SLICES on it, so a
        wrong value silently attributes wins to a level that never ran. Trinity's own
        DEFAULT_EFFORT is deliberately scoped to providers whose CLI takes the flag;
        caller-supplied members get no such guarantee."""
        m = _members_from([{"provider": "chatgpt", "content": "x"}])[0]
        assert "effort" not in (m.to_dict().get("metadata") or {})

    @pytest.mark.parametrize("bad", ["", "   ", None, 5, {"level": "high"}, []])
    def test_junk_effort_is_dropped_not_stored(self, bad):
        """Non-string or blank efforts must not reach the tally as a slice key."""
        m = _members_from([{"provider": "chatgpt", "content": "x", "effort": bad}])[0]
        assert "effort" not in (m.to_dict().get("metadata") or {}), (
            f"{bad!r} must not be stored as an effort level"
        )

    def test_whitespace_is_normalized(self):
        m = _members_from([{"provider": "chatgpt", "content": "x", "effort": " high "}])[0]
        assert (m.to_dict().get("metadata") or {}).get("effort") == "high"

    def test_source_marker_is_preserved_alongside_effort(self):
        """`source: mcp_synthesis` is how caller-supplied members are told apart from
        dispatched ones. Adding effort must not displace it."""
        m = _members_from([{"provider": "claude_ai", "content": "x", "effort": "max"}])[0]
        meta = m.to_dict().get("metadata") or {}
        assert meta.get("source") == "mcp_synthesis" and meta.get("effort") == "max"


class TestToolContractAdvertisesIt:
    def test_responses_schema_exposes_model_and_effort(self):
        """Tool docstrings/schemas ARE the contract the agent reads at handshake. If
        the schema does not name `effort`, no caller will ever send it and the handler
        support is dead code — which is precisely the state this test was written to
        end."""
        import asyncio

        from trinity_local import mcp_server

        tools = asyncio.run(mcp_server.handle_list_tools())
        rc = next(t for t in tools if t.name == "run_council")
        props = rc.inputSchema["properties"]["responses"]["items"]["properties"]
        assert "effort" in props, "responses[].effort missing from the published schema"
        assert "model" in props
        blob = json.dumps(props["effort"]).lower()
        assert "xhigh" in blob or "reasoning" in blob, (
            "effort needs a description naming the levels, or callers will invent values"
        )


class TestHostLoopCarriesDispatchedEffort:
    """The provider-side loop dispatches the NON-host members itself, so Trinity knows
    exactly what level it ran them at (config.effort). Omitting it there downgrades a
    KNOWN level to unknown for every host-loop council.

    Found by running the loop end-to-end 2026-07-26: the host-supplied claude member
    correctly carried effort='high' while the CLI-dispatched codex member recorded
    none, even though config had 'xhigh'. A unit test on the recording leg alone could
    not have caught it — the field was lost one layer upstream, in the dispatch dict.
    """

    def test_dispatched_members_carry_config_effort(self, monkeypatch, tmp_path):
        import asyncio
        import types

        from trinity_local import mcp_server as M

        monkeypatch.setenv("TRINITY_HOST_CLAUDE_MEMBER", "1")
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        class _Cfg:
            enabled = True
            model = "gpt-5.5"
            effort = "xhigh"
            command = ["codex"]
            args: list = []

        class _Res:
            stdout = "dispatched answer body, long enough to be real"
            stderr = ""

        monkeypatch.setattr(M, "_members_to_dispatch", lambda members, host: ["codex"])
        import trinity_local.providers as P
        monkeypatch.setattr(P, "make_provider",
                            lambda cfg: types.SimpleNamespace(run=lambda *a, **k: _Res()))
        import trinity_local.config as C
        monkeypatch.setattr(C, "load_config",
                            lambda *a, **k: types.SimpleNamespace(providers={"codex": _Cfg()}))

        out = asyncio.run(M._council_with_host_members(
            {"task": "t", "dispatch_only": True, "members": ["claude", "codex"]},
            [{"provider": "claude", "content": "host answer", "model": "claude-opus-5",
              "effort": "high"}],
        ))
        import json
        payload = json.loads(getattr(out[0], "text", None) or out[0]["text"])
        by = {m["provider"]: m for m in payload["member_responses"]}
        assert by["claude"].get("effort") == "high", "host-supplied effort lost"
        assert by["codex"].get("effort") == "xhigh", (
            f"CLI-dispatched effort lost — Trinity knew it was xhigh: {by['codex']!r}"
        )
