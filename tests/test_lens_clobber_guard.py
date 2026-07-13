"""Guard: the 5-stage lens-build path must not DOWNGRADE a populated lens.

Earned 2026-06-02. `build_me_via_council` (the old path) refuses to overwrite
the persisted lens when the chairman returns empty/poisoned output ("existing
/me preserved. Re-run lens-build.", me_builder.py L466-488). The current default
path `build_me_via_lens_pipeline` lost that protection: it renders the lens
DETERMINISTICALLY via `render_me_markdown`, so the old structural
`_REQUIRED_ME_SECTIONS` check is both the wrong header vocabulary (renderer now
emits `# Lens`, not `# /me`) AND a permanent no-op (the renderer always emits
well-formed headers). Its real corruption mode is a *content-less downgrade*:

    empty Stage 3 (chairman timeout/quota) → stage3_parse([]) → accepted == []
    + tension-registry read raises (e.g. schema skew after upgrade)
    → render_pairs == [] → render_me_markdown emits the
      "(No paired tensions found yet …)" placeholder
    → path.write_text(...) overwrites the founder's accumulated 16-tension lens

`_would_clobber_populated_lens` refuses that write. These tests pin its marker
(`_POPULATED_TENSION_HEADING`) to the renderer's *actual* output, so a future
change to the tension-heading format can't silently disable the guard
(anti-rot, principle #33 — the verification pins the invariant against its real
producer, not a hand-copied string).
"""
from __future__ import annotations

from trinity_local.me_builder import (
    _POPULATED_TENSION_HEADING,
    _would_clobber_populated_lens,
)
from trinity_local.me.pair_mining import LensPair
from trinity_local.me.pipeline import render_me_markdown


def _populated_lens_text() -> str:
    """The renderer's real output for a lens carrying one paired tension."""
    pair = LensPair(
        pole_a="concrete",
        pole_b="abstract",
        failure_a="vague hand-waving",
        failure_b="brittle over-fitting",
        basins_spanned=["b1", "b2"],
    )
    return render_me_markdown([pair], [])


def _empty_lens_text() -> str:
    """The renderer's real output for a lens with no tensions (cold start)."""
    return render_me_markdown([], [])


# --- anti-rot: the marker must track the real renderer, not a copied string ---

def test_marker_matches_a_real_populated_render():
    """If the renderer's tension-heading format changes, this fails — forcing
    `_POPULATED_TENSION_HEADING` to be updated so the guard keeps firing."""
    assert _POPULATED_TENSION_HEADING.search(_populated_lens_text()), (
        "the populated-tension marker no longer matches render_me_markdown's "
        "actual tension heading — the clobber guard would silently stop "
        "detecting a populated lens. Update _POPULATED_TENSION_HEADING to track "
        "the renderer (me/pipeline.py render_me_markdown)."
    )


def test_marker_does_not_match_the_empty_placeholder():
    """The cold-start placeholder must NOT read as a populated lens, or every
    legitimate first build would be wrongly treated as a downgrade."""
    assert not _POPULATED_TENSION_HEADING.search(_empty_lens_text())


# --- the predicate: fire ONLY on a genuine downgrade ---

def test_downgrade_of_populated_lens_is_refused():
    """Empty new render + an existing populated lens → preserve (the bug case)."""
    assert _would_clobber_populated_lens(
        has_new_tensions=False, existing_text=_populated_lens_text()
    ) is True


def test_cold_start_writes_normally():
    """No existing file + empty new render → write the placeholder (nothing to
    protect). Must NOT block a genuine first build."""
    assert _would_clobber_populated_lens(
        has_new_tensions=False, existing_text=None
    ) is False


def test_existing_placeholder_is_not_protected():
    """An existing tension-less placeholder carries no taste to lose → writing a
    fresh placeholder over it is fine (not a downgrade)."""
    assert _would_clobber_populated_lens(
        has_new_tensions=False, existing_text=_empty_lens_text()
    ) is False


def test_populated_new_render_always_writes():
    """A render that DOES carry tensions is never a downgrade, even over an
    existing populated lens — that's the normal accumulating update."""
    assert _would_clobber_populated_lens(
        has_new_tensions=True, existing_text=_populated_lens_text()
    ) is False
    assert _would_clobber_populated_lens(
        has_new_tensions=True, existing_text=None
    ) is False


def test_empty_existing_string_is_cold_start():
    """An empty-string lens.md (zero-byte file) is a cold start, not a populated
    lens — write normally rather than treating '' as something to preserve."""
    assert _would_clobber_populated_lens(
        has_new_tensions=False, existing_text=""
    ) is False
