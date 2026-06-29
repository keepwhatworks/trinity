"""Lens empty state: provider-side prompt CTAs.

The lens-empty card (`v-if="!tasteLenses"`) used to advertise only
`trinity-local lens`, which assumes the user already has local
transcripts. The provider-side loop (`lens-prompt | pbcopy` →
`lens-import --provider …`) is the parallel path for users who don't
have transcripts indexed locally. This test pins those chips so a
future template cleanup doesn't quietly drop the discovery surface.
"""
from __future__ import annotations


def _render_with_no_lens():
    """Render the launchpad in the cold-install lens state."""
    from trinity_local.launchpad_template import render_launchpad_html
    # tasteLenses missing / falsy → empty-state lens card renders.
    return render_launchpad_html(page_data={})


class TestLensEmptyStateChips:
    def test_lens_build_chip_remains_primary_cta(self):
        """Primary path stays in front: lens-build is still the
        first thing the user sees in the empty card."""
        html = _render_with_no_lens()
        assert "trinity-local lens" in html

    def test_lens_prompt_chip_renders(self):
        html = _render_with_no_lens()
        assert "trinity-local lens-prompt | pbcopy" in html
        assert "lens-prompt-copy" in html  # unique copyText key

    def test_lens_import_chip_renders_with_provider_flag(self):
        html = _render_with_no_lens()
        # The CTA shows the canonical doc invocation pattern.
        assert "trinity-local lens-import --provider claude" in html
        assert "lens-import-copy" in html
