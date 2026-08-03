"""Identity must survive the node -> cluster -> synthesis-args hop.

This hop dropping model/effort is WHY 1,077 web members were dark and a
322-member text-match backfill had to exist (amd_0062). Forward virtual
councils must be born stamped — and only when the node actually knows.
"""
from __future__ import annotations

from trinity_local.cross_provider_pairs import (
    CrossProviderCluster,
    ProviderResponse,
    cluster_to_synthesis_args,
)


def _member(**over):
    base = dict(provider="claude_ai", prompt_text="q", response_text="a",
                node_id="n1", timestamp=None, model="claude-opus-5", effort="high")
    base.update(over)
    return ProviderResponse(**base)


def test_known_identity_reaches_synthesis_args():
    args = cluster_to_synthesis_args(
        CrossProviderCluster(representative_prompt="q",
                             members=[_member()], coherence=0.9))
    r = args["responses"][0]
    assert r["model"] == "claude-opus-5"
    assert r["effort"] == "high"


def test_unknown_identity_omits_keys_rather_than_guessing():
    args = cluster_to_synthesis_args(
        CrossProviderCluster(representative_prompt="q",
                             members=[_member(model=None, effort=None)],
                             coherence=0.9))
    r = args["responses"][0]
    assert "model" not in r and "effort" not in r
