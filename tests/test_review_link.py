"""Tests for review-link: mobile-safe council review URLs."""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from trinity_local.council_runtime import (
    create_council_outcome,
    create_prompt_bundle,
    save_council_outcome,
    save_prompt_bundle,
)
from trinity_local.council_schema import CouncilMemberResult, CouncilRoutingLabel


def _seed_council(task_text: str) -> str:
    bundle = create_prompt_bundle(
        task_cluster_id="cluster_review_link",
        task_text=task_text,
        goal="Pick the strongest answer.",
        comparison_instructions="Prefer the answer the user can act on.",
    )
    save_prompt_bundle(bundle)
    outcome = create_council_outcome(
        bundle=bundle,
        primary_provider="claude",
        member_results=[
            CouncilMemberResult(
                provider="claude",
                model="claude-sonnet",
                output_text="Use the local review page.",
            ),
            CouncilMemberResult(
                provider="antigravity",
                model="gemini-pro",
                output_text="Use a mobile-safe review link.",
            ),
        ],
        winner_provider="antigravity",
        synthesis_output="Gemini wins because it names the mobile review loop.",
        # iter #106 strict contract: routing_label is required for save.
        routing_label=CouncilRoutingLabel(winner="antigravity", confidence="medium"),
    )
    save_council_outcome(outcome)
    return outcome.council_run_id


def _run_review_link(council_id: str, **overrides) -> str:
    from trinity_local.commands.portal import handle_review_link

    defaults = {
        "council_id": council_id,
        "as_json": True,
    }
    defaults.update(overrides)
    buf = io.StringIO()
    with redirect_stdout(buf):
        handle_review_link(argparse.Namespace(**defaults))
    return buf.getvalue()


class TestReviewLink:
    def test_json_links_carry_only_council_id(self, patch_trinity_home: Path):
        task_text = "Sensitive prompt: price the private acquisition plan"
        council_id = _seed_council(task_text)

        out = _run_review_link(council_id)
        data = json.loads(out)

        assert data["council_id"] == council_id
        assert data["file_url"].startswith("file://")
        assert data["deep_link"] == f"trinity://review/{council_id}"
        assert "web_url" not in data
        assert task_text not in out
        assert "private acquisition" not in data["file_url"]
        assert "private acquisition" not in data["deep_link"]

    def test_generates_review_artifact_for_mobile_to_open(self, patch_trinity_home: Path):
        council_id = _seed_council("Review this from my phone")

        data = json.loads(_run_review_link(council_id))

        review_path = Path(data["review_path"])
        assert review_path.exists()
        assert review_path.name == f"{council_id}.html"
        assert (patch_trinity_home / "review_pages" / "live_council.html").exists()
