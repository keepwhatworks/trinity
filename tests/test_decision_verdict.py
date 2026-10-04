"""The chairman answers the user's question, and every split names its check.

92% of 107 chairman Recommendations named a model ("If you value X -> choose
Claude"), so a verdict could never win or lose later (council_10e38944ddbdb84c,
amd_0264). A new verdict field has to survive four chokepoints -- the prompt,
the parse normalizer, the dataclass declaration and to_dict -- and `resolution`
was once lost at the second. So the round trip starts from a chairman's raw
output and ends in the payload an MCP caller reads.
"""
from __future__ import annotations

import asyncio
import json

from trinity_local.council_runtime import (
    create_council_outcome,
    create_prompt_bundle,
    load_council_outcome,
    parse_routing_label,
    render_primary_council_prompt,
    save_council_outcome,
)
from trinity_local.council_schema import CouncilMemberResult

CHAIRMAN = """## Decision
- Do not offer on Lot 3 until the sewer letter arrives.

## What would change it
- A written EGCWSA capacity letter for 30 taps.

## Contested
- Codex survives on the grade question; check the county's 15% street rule.

```routing-json
{"winner": "codex", "runner_up": "claude", "confidence": "medium",
 "task_type": "acquisition", "task_domain": "real_estate",
 "decision": "Do not offer on Lot 3 until the sewer letter arrives.",
 "flip_condition": "A written EGCWSA capacity letter for 30 taps.",
 "agreed_claims": ["Lot 3 is zoned R1."],
 "disagreed_claims": [
   {"claim": "The 15% street-grade cap rules out the ridge access.",
    "providers_for": ["claude"], "providers_against": ["codex"],
    "resolution": "Codex survives: the cap binds streets, not driveways.",
    "why_matters": "It decides whether the ridge pads are reachable.",
    "check": {"procedure": "Read Ellijay subdivision code Sec. 6.4 street standards",
              "decision_rule": "If 6.4 caps private driveways at 15%, claude wins; if it covers streets only, codex wins."}}
 ]}
```"""


def _members():
    return [CouncilMemberResult(provider=p, model="m", session_id=None, output_text=f"{p} answer")
            for p in ("claude", "codex")]


def test_decision_fields_survive_every_chokepoint(patch_trinity_home):
    from trinity_local.mcp_server import _outcome_summary

    label, err = parse_routing_label(CHAIRMAN)
    assert err is None and label is not None
    outcome = create_council_outcome(
        bundle=create_prompt_bundle(task_cluster_id="c", task_text="Offer on Lot 3?", goal="decide"),
        primary_provider="codex", member_results=_members(), synthesis_output=CHAIRMAN,
        routing_label=label, winner_provider=label.winner)
    save_council_outcome(outcome)
    summary = _outcome_summary(load_council_outcome(outcome.council_run_id))
    assert summary["decision"] == "Do not offer on Lot 3 until the sewer letter arrives."
    assert summary["flip_condition"].startswith("A written EGCWSA")
    check = summary["disagreed_claims"][0]["check"]
    assert check["procedure"].startswith("Read Ellijay") and "codex wins" in check["decision_rule"]
    assert summary["winner"] == "codex", "the founder-locked JSON winner is untouched"
    assert "routing-json" not in summary["synthesis_output"]


def test_a_bare_string_check_is_kept_as_the_procedure():
    raw = CHAIRMAN.replace(
        '"check": {"procedure": "Read Ellijay subdivision code Sec. 6.4 street standards",\n'
        '              "decision_rule": "If 6.4 caps private driveways at 15%, claude wins; if it covers streets only, codex wins."}',
        '"check": "Read Sec. 6.4"')
    label, _ = parse_routing_label(raw)
    assert label.disagreed_claims[0]["check"] == {"procedure": "Read Sec. 6.4"}


def test_the_prompt_asks_for_a_decision_not_a_model_menu(monkeypatch):
    monkeypatch.delenv("TRINITY_DECISION_VERDICT", raising=False)
    bundle = create_prompt_bundle(task_cluster_id="c", task_text="Offer on Lot 3?", goal="decide")
    p = render_primary_council_prompt(bundle, _members())
    part1 = p[p.index("PART 1"):p.index("PART 2")]
    assert "## Decision" in part1 and "## What would change it" in part1
    assert "If you value" not in p and "## Winner" not in part1 and "## Recommendation" not in part1
    assert '"decision"' in p and '"flip_condition"' in p and '"check"' in p


def test_the_kill_switch_restores_the_old_template(monkeypatch):
    monkeypatch.setenv("TRINITY_DECISION_VERDICT", "0")
    bundle = create_prompt_bundle(task_cluster_id="c", task_text="Offer on Lot 3?", goal="decide")
    p = render_primary_council_prompt(bundle, _members())
    assert "If you value X → choose Provider" in p and "## Decision" not in p and '"check"' not in p


def test_every_outcome_records_which_format_produced_it(patch_trinity_home, monkeypatch):
    """A format change can move the winner distribution the ledger learns from."""
    from trinity_local import mcp_server, provider_quota
    from trinity_local.providers import ProviderResult

    class Fake:
        def __init__(self, name): self.name = name

        def run(self, prompt, cwd):
            out = CHAIRMAN if "synthesizer" in prompt.lower() else f"{self.name} answer"
            return ProviderResult(provider=self.name, stdout=out, stderr="", returncode=0)

    monkeypatch.setattr("trinity_local.council_runner.make_provider", lambda cfg: Fake(cfg.name))
    provider_quota.clear()
    r = json.loads(asyncio.run(mcp_server._run_council(
        {"task": "Offer on Lot 3?", "members": ["claude", "codex"], "primary_provider": "codex"}))[0]["text"])
    provider_quota.clear()
    assert r["outcome"]["chairman_format"] == "decision_v1"
    assert r["outcome"]["decision"].startswith("Do not offer")
