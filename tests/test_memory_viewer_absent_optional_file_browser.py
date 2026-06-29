"""Real-browser guard: a direct ?file= link to a KNOWN-but-unbuilt optional memory
(generators.md) shows "not built yet — run lens-generators", not a misleading
"Unknown memory" dead-end.

generators.md (the lens "lift") is an OPTIONAL on-demand tier: _visible_files() hides
its nav tab until the user runs `lens-generators`, so the vast majority of homes don't
show it. But a direct `memory.html?file=generators.md` link (a doc, a shared URL, a
returning user's bookmark, a future cross-link) still resolves the param — and the
client only had the FILTERED file set, so generators.md fell through to "Unknown
memory: generators.md. Pick one from the nav." That's wrong twice: it's a KNOWN memory
(not unknown), and the nav it tells you to pick from deliberately hides it — a
dead-end. Every other unbuilt memory gets a helpful "Not built yet. Run trinity-local
<verb>" empty-state; the one filtered-out file got the dead-end. (The _visible_files
docstring even CLAIMED "?file= validation still uses the full ALLOWED_FILES set" — but
that intent was never wired to the client, which only received _visible_files().)

Fix: inject the full ALLOWED_FILES as ALL_FILES so the ?file= miss can tell a
known-but-unbuilt optional file (→ header + "run lens-generators" empty-state) from a
truly-unknown name (→ "Unknown memory"). suggestionFor also gained the generators.md →
lens-generators mapping (it previously fell through to the wrong "dream").

Seeds an isolated home WITHOUT generators.md (so its tab is hidden), renders the real
viewer, and over file:// asserts:
  * ?file=generators.md → NOT "Unknown memory"; shows the generators tagline + "Not
    built yet" + the `trinity-local lens-generators` command;
  * ?file=bogus.md (truly unknown) → still "Unknown memory: bogus.md".

Mutation-proven: drop the ALL_FILES known-file branch → generators.md falls back to
"Unknown memory" → the negative assertion reds. Verified by hand 2026-06-09.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _seed(home: Path) -> None:
    (home / "memories").mkdir(parents=True)
    (home / "core.md").write_text("# Core\nidentity paragraph\n", encoding="utf-8")
    (home / "memories" / "lens.md").write_text(
        "# Lens\ntension: ship-speed vs verified-correctness\n", encoding="utf-8")
    # NB: generators.md intentionally ABSENT — that's the whole point.


def test_direct_link_to_unbuilt_generators_is_not_unknown(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed(home)

    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()
    assert "generators.md" not in mv.read_text(encoding="utf-8").split("const FILES = ")[1].split(";")[0], (
        "generators.md should be hidden from the nav (FILES) when absent"
    )

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))

            # 1) KNOWN-but-unbuilt optional file via direct ?file= link.
            page.goto(f"file://{mv}?file=generators.md", wait_until="load")
            page.wait_for_timeout(700)
            body = (page.query_selector("#content").inner_text() or "")
            low = body.lower()
            if "unknown memory" in low:
                failures.append(f"generators.md shown as 'Unknown memory' (it's a known optional file): {body[:160]!r}")
            if "not built yet" not in low:
                failures.append(f"generators.md missing the 'Not built yet' empty-state: {body[:160]!r}")
            if "lens-generators" not in low:
                failures.append(f"generators.md didn't suggest `trinity-local lens-generators`: {body[:160]!r}")

            # 2) TRULY unknown file → still the Unknown-memory error.
            page.goto(f"file://{mv}?file=bogus-not-a-memory.md", wait_until="load")
            page.wait_for_timeout(500)
            body2 = (page.query_selector("#content").inner_text() or "").lower()
            if "unknown memory" not in body2:
                failures.append(f"a truly-unknown ?file= should say 'Unknown memory', got: {body2[:160]!r}")

            if errs:
                failures.append(f"JS errors during ?file= resolution: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, "absent-optional-file handling regressed:\n  " + "\n  ".join(failures)
