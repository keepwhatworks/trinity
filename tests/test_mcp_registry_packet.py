"""Pin the MCP-registry submission packet shape (task #114).

The packet at docs/MCP_REGISTRY_SUBMISSIONS.md is the marketing-prep
deliverable for workstream 1 of the launch arc — ready-to-paste copy
for when registry outreach starts. If the doc loses the per-registry
sections or the canonical pitch line, future-me submitting from this
checklist hits a half-empty doc.

Five low-friction guards: each named registry has a section + a
'Tailored pitch' subsection, and the canonical install one-liner +
the wedge framing both appear in the doc.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
PACKET = REPO / "docs" / "MCP_REGISTRY_SUBMISSIONS.md"


@pytest.mark.parametrize("registry_name", [
    "Anthropic / Claude Desktop",
    "Cursor MCP marketplace",
    "Cline",
    "Continue",
    "Codex CLI",
])
def test_each_registry_has_section(registry_name):
    """Every named registry must have its own section header. If the
    doc drifts and a section disappears, we'd silently lose the
    tailored copy for that audience."""
    text = PACKET.read_text(encoding="utf-8")
    assert registry_name in text, (
        f"docs/MCP_REGISTRY_SUBMISSIONS.md missing section for "
        f"{registry_name!r} — a registry was dropped from the packet."
    )


def test_each_section_has_tailored_pitch():
    """Each registry section must have a 'Tailored pitch' block.
    Generic pitches lose to tailored ones; the WHOLE POINT of having
    per-registry sections is that the lede paragraph is different for
    each audience."""
    text = PACKET.read_text(encoding="utf-8")
    # Crude but reliable: count Registry sections vs Tailored pitch
    # blocks. They should match.
    registry_count = text.count("## Registry ")
    tailored_count = text.count("**Tailored pitch**")
    assert registry_count == 5, f"expected 5 registry sections, found {registry_count}"
    assert tailored_count == 5, (
        f"expected 5 tailored pitches, found {tailored_count}. "
        f"Some registry section lost its tailored copy."
    )


def test_canonical_install_one_liner_present():
    """The install one-liner is what every registry submission ends
    up linking to. If the doc drops it, submission editors have to
    invent their own — leading to drift across registries.

    Trinity ships as a git clone via curl|sh — `scripts/install.sh`
    drops the skill, writes shell wrappers, and registers MCP. There
    is no PyPI publish; see docs/INSTALL-pip.md."""
    text = PACKET.read_text(encoding="utf-8")
    assert "scripts/install.sh | bash" in text


def test_wedge_framing_present():
    """The structural-asymmetry framing is the pitch's load-bearing
    bit ('labs are commercially prevented from building this'). If a
    rewrite removes it, the packet reduces to a feature list and we
    lose the moat narrative across all five submissions at once."""
    text = PACKET.read_text(encoding="utf-8").lower()
    assert "commercially prevented" in text, (
        "Wedge framing lost. The packet's load-bearing claim is that "
        "the labs CAN'T build cross-provider memory — without that, "
        "the pitch is just 'we wrap three CLIs.'"
    )


def test_submission_status_table_has_all_registries():
    """The status table at the bottom is the user's checklist as they
    work through outreach. If a registry is in the doc but not in the
    table, they'll forget to submit it."""
    text = PACKET.read_text(encoding="utf-8")
    for registry in ["Claude Desktop", "Cursor", "Cline", "Continue", "Codex CLI"]:
        assert f"| {registry} |" in text, (
            f"Status table missing row for {registry!r}. "
            f"User will forget to track that submission."
        )


def test_failure_modes_section_present():
    """The 'Failure modes to guard against' section pre-empts the
    most common ways registry copy goes off-rails ('sounds like a
    wrapper', 'yet another MCP server'). Removing it forces every
    future submission to relearn the same lessons."""
    text = PACKET.read_text(encoding="utf-8")
    assert "Failure modes to guard against" in text
