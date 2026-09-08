"""Trinity must run with any single provider removed, including claude.

The product claim is that you are not locked in: if a cheaper or faster service
appears, you move your dispatch to it and your memory stays put. Nothing checked
that. This is the check.

Verified live 2026-09-04 before the guard was written: a real council ran with
claude DISABLED — codex and antigravity as members, codex chairing, winner
antigravity, zero failed members. No code change was needed. These tests pin
that property so the day someone wires in a hard dependency, the suite says so
instead of the claim quietly becoming false.

The distinction the guard encodes: transcript PARSERS are legitimately
provider-specific (reading a Claude Code session is format work), while DISPATCH
must stay config-driven. A claude reference in ingest.py is correct. A claude
reference on the path from `run_council` to a verdict is not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trinity_local.config import load_config
from trinity_local.council_runner import run_council
from trinity_local.council_runtime import create_prompt_bundle
from trinity_local.providers import ProviderError, ProviderResult

CHAIRMAN_JSON = (
    "Agreed claims\n- portability holds\n\n```json\n"
    '{"winner":"codex","confidence":"medium","task_type":"comparison"}\n```\n'
)


def _stub(monkeypatch, *, alive: set[str]):
    """Providers that answer only if named in `alive`; anything else is absent."""
    class P:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            if self.name not in alive:
                raise ProviderError(f"not installed: {self.name}")
            text = CHAIRMAN_JSON if "synthesizer" in prompt.lower() else f"{self.name} answer"
            return ProviderResult(provider=self.name, stdout=text, stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider", lambda cfg: P(cfg.name))


def _disable(cfg, name):
    """ProviderConfig is frozen, so a disabled provider is a REPLACEMENT, not a
    mutation — which is also how a user would do it: edit config.json."""
    import dataclasses
    if name in cfg.providers:
        cfg.providers[name] = dataclasses.replace(cfg.providers[name], enabled=False)
    return cfg


@pytest.mark.parametrize("dropped,members,chair", [
    ("claude", ["codex", "antigravity"], "codex"),
    ("codex", ["claude", "antigravity"], "claude"),
    ("antigravity", ["claude", "codex"], "claude"),
])
def test_a_council_completes_with_any_one_provider_removed(
    patch_trinity_home, monkeypatch, dropped, members, chair
):
    cfg = _disable(load_config(), dropped)
    _stub(monkeypatch, alive=set(members))
    result = run_council(
        config=cfg,
        bundle=create_prompt_bundle(task_cluster_id="portability",
                                    task_text="name one risk", goal="test portability"),
        member_providers=members, primary_provider=chair,
        cwd=Path(patch_trinity_home),
    )
    o = result.outcome
    assert o.member_results, f"no member answered with {dropped} removed"
    assert not (o.metadata or {}).get("failed_members"), (
        f"removing {dropped} broke a member that should be independent of it"
    )
    assert o.routing_label is not None and o.routing_label.winner, (
        f"no verdict with {dropped} removed — something on the dispatch path "
        f"hard-depends on it, which makes 'not locked in' false"
    )


def test_claude_is_not_required_to_chair(patch_trinity_home, monkeypatch):
    """The chairman is the one role most likely to acquire a hidden dependency:
    it is claude by default, uses MCP sampling when available, and is the sole
    supervision signal since the user-pick layer was removed."""
    cfg = _disable(load_config(), "claude")
    _stub(monkeypatch, alive={"codex", "antigravity"})
    o = run_council(
        config=cfg,
        bundle=create_prompt_bundle(task_cluster_id="portability_chair",
                                    task_text="name one risk", goal="chair without claude"),
        member_providers=["codex", "antigravity"], primary_provider="codex",
        cwd=Path(patch_trinity_home),
    ).outcome
    assert o.primary_provider != "claude"
    assert o.primary_provider in ("codex", "antigravity")
    assert o.synthesis_output, "a non-claude chair produced no synthesis"


def test_dispatch_modules_carry_no_hard_claude_default():
    """Parsers may name claude; the dispatch path may not depend on it.

    `me_builder` picks a chairman with `available or ["claude"]`. That is SAFE —
    it fires only when NO provider is enabled, and the next lines reassign to
    available[0] — but it is the shape a real lock-in would take, so it is
    pinned: if the surrounding guard is ever removed, this fails.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "trinity_local" / "me_builder.py"
    text = src.read_text(encoding="utf-8")
    for idx in range(text.count('available or ["claude"]')):
        after = text.split('available or ["claude"]')[idx + 1][:600]
        # Assert the MECHANISM, not a nearby string. The first version of this
        # matched "chairman_config is None", which also appears in a DIFFERENT
        # guard further down the same window, so removing the real reassignment
        # left the test green. The reassignment itself is what makes the claude
        # default harmless, so that is what gets pinned.
        assert 'chairman = available[0] if available else ""' in after, (
            "a claude default in me_builder is no longer followed by the "
            "reassignment to an ENABLED provider, so a claude-less install "
            "would chair with a provider it does not have"
        )
