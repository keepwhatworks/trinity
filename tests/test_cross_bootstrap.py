"""Cross-bootstrap tests — each install entry point gracefully suggests
the other.

Two install paths exist:
  1. Chrome extension first (Web Store / sideload) → popup's setup card
     copies a "paste into Claude Code" install brief that runs install.sh
  2. curl|bash first (terminal) → install.sh prints "now install the
     extension for browser capture + auto-update"

This file pins:
  - install.sh's tail mentions the Chrome extension + the docs path
  - The launchpad's proactive "Install extension" CTA fires when the
    extension isn't configured AND hides when it IS configured
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ─── install.sh tail ────────────────────────────────────────────────

class TestInstallScriptTail:
    @pytest.fixture
    def install_script(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        return (repo_root / "scripts" / "install.sh").read_text()

    def test_mentions_chrome_extension(self, install_script):
        """A curl|bash user should see, at the end of install, a clear
        pointer to the Chrome extension. Without this they don't know
        the second tier exists."""
        # Check the tail (last 30 lines) specifically — earlier mentions
        # could be in unrelated documentation comments.
        tail = "\n".join(install_script.splitlines()[-55:])
        assert "Chrome extension" in tail, (
            "install.sh tail must mention the Chrome extension so "
            "terminal-first users discover the browser-capture path."
        )

    def test_mentions_browser_capture(self, install_script):
        """The value proposition for installing the extension must be
        on screen — otherwise the user reads 'install the extension'
        and shrugs. 'Capture' or 'conversations' from claude.ai etc.
        is the killer feature."""
        tail = "\n".join(install_script.splitlines()[-55:])
        lowered = tail.lower()
        # Either 'capture' or the specific domains must be named.
        assert (
            "capture" in lowered
            or "claude.ai" in lowered
            or "chatgpt.com" in lowered
        ), "install.sh tail must explain WHY the user would install the extension."

    def test_points_at_install_extension_doc(self, install_script):
        """The tail must point at the docs file with the actual install
        steps — relying on the user to guess is fragile."""
        tail = "\n".join(install_script.splitlines()[-55:])
        assert "INSTALL-extension" in tail, (
            "install.sh tail must reference docs/INSTALL-extension.md "
            "so the user has a single canonical install path."
        )

    def test_mentions_embedder_prefetch_verb(self, install_script):
        """The tail must point at `trinity-local download-embedder` so
        users can pre-fetch the ~600 MB model before they hit the gate
        in lens-build / dream / vocabulary. Closes the loop between
        the install step and the embedder-gated commands."""
        tail = "\n".join(install_script.splitlines()[-55:])
        assert "download-embedder" in tail, (
            "install.sh tail must mention the download-embedder verb so "
            "new installs can pre-fetch the model — otherwise the user's "
            "first encounter with the requirement is mid-command via "
            "the embedder gate."
        )
        # The optional-step framing must NOT make the verb sound required —
        # the embedder is genuinely optional (councils + launchpad work
        # without it; only lens-build/dream/vocabulary need it).
        assert "Optional" in tail or "optional" in tail, (
            "The embedder pre-fetch must be framed as optional — only "
            "deeper-memory commands need it. Mis-framing it as required "
            "would scare users off the install."
        )


# ─── Launchpad proactive CTA ────────────────────────────────────────

class TestLaunchpadExtensionCTA:
    @pytest.fixture
    def launchpad_template(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        return (repo_root / "src" / "trinity_local" / "launchpad_template.py").read_text()

    def test_cta_gated_on_extension_not_configured(self, launchpad_template):
        """The CTA card must render ONLY when the extension is NOT
        configured. v-if expression has to negate browserExtension.configured."""
        # Match the v-if predicate guarding the cross-bootstrap card.
        assert re.search(
            r'v-if="pageData\.browserExtension\s*&&\s*!pageData\.browserExtension\.configured"',
            launchpad_template,
        ), (
            "Launchpad must include a proactive 'Install Chrome extension' "
            "card with v-if=\"pageData.browserExtension && "
            "!pageData.browserExtension.configured\" so it shows only "
            "when needed."
        )

    def test_cta_mentions_browser_capture(self, launchpad_template):
        """The CTA copy must explain browser capture (the unique
        capability the extension unlocks) — not just "install this
        because we said so"."""
        # The CTA section lives near the dispatch banner; search for
        # the cross-bootstrap eyebrow + the capture value.
        assert "Cross-bootstrap" in launchpad_template
        # The CTA must mention at least one of the captured domains so
        # the user knows what "capture" means.
        assert any(
            domain in launchpad_template
            for domain in ("claude.ai", "chatgpt.com", "gemini.google.com")
        ), (
            "Launchpad CTA must name the captured domains so "
            "'browser capture' isn't an empty phrase."
        )

    def test_cta_links_to_install_doc(self, launchpad_template):
        """The CTA must link to the docs file so a click actually goes
        somewhere useful — drift here = dangling CTA."""
        # Match an anchor pointing at INSTALL-extension.md.
        assert re.search(
            r'INSTALL-extension\.md',
            launchpad_template,
        ), "Launchpad CTA must link to docs/INSTALL-extension.md."
