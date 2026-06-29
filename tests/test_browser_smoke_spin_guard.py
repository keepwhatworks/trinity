"""Unit tests for the browser-smoke poller-spin guard (v1.7.214).

`scripts/browser_smoke.py` drives the launchpad through a real browser but is
NOT part of `pytest` (it needs Playwright + a real ~/.trinity), so its assertions
silently rot. The recurring 404-spin bug — a council_status poller that never
hits its MAX_MISSING_POLLS give-up and 404s forever, leaving the UI stuck
"running" (live_council_two_pollers: THREE independent pollers, each needs its
own cap) — used to be invisible to the smoke: it asserted on surface DOM, not on
the HTTP-log 404s. The smoke now tallies council_status 404s per token and fails
on a spin. The tally→fail logic is extracted as `detect_poller_spins` so it can
be exercised here without launching the browser; this keeps the guard itself
from silently rotting.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _bs():
    """Import scripts/browser_smoke lazily — INSIDE a function, never at module
    level. A module-level `sys.path.insert` leaks into the whole suite (caught by
    test_no_module_level_env_mutation). Playwright is lazy-imported inside the
    script's main(), so this import pulls in no browser deps."""
    import sys

    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import browser_smoke

    return browser_smoke


class TestDetectPollerSpins:
    def test_healthy_single_probe_is_not_a_spin(self):
        # A no-extension dispatch fires one optimistic status probe then rolls
        # back — one 404 per token is the healthy steady state, not a spin.
        bs = _bs()
        assert bs.detect_poller_spins({"council_status_launch_abc": 1}) == []

    def test_at_the_cap_is_not_a_spin(self):
        # A capped-but-uncancelled poll gives up at MAX_MISSING_POLLS (<=20); the
        # threshold sits above that, so a poll that legitimately reaches its cap
        # must NOT be flagged.
        bs = _bs()
        fails = bs.detect_poller_spins({"council_status_launch_abc": bs.SPIN_THRESHOLD})
        assert fails == [], "a token AT the threshold is not yet a spin"

    def test_over_threshold_is_flagged_as_spin(self):
        bs = _bs()
        n = bs.SPIN_THRESHOLD + 1
        fails = bs.detect_poller_spins({"council_status_launch_abc": n})
        assert len(fails) == 1
        surface_num, name, reason = fails[0]
        assert surface_num == 0
        assert "spin" in name
        assert "council_status_launch_abc" in reason
        assert "MAX_MISSING_POLLS" in reason

    def test_multiple_spinning_tokens_each_reported_worst_first(self):
        bs = _bs()
        counts = {
            "council_status_a": bs.SPIN_THRESHOLD + 5,
            "council_status_b": bs.SPIN_THRESHOLD + 50,
            "council_status_healthy": 1,
        }
        fails = bs.detect_poller_spins(counts)
        assert len(fails) == 2  # only the two over-threshold tokens
        # Worst offender first (sorted by count desc).
        assert "council_status_b" in fails[0][2]
        assert "council_status_a" in fails[1][2]
        assert all("council_status_healthy" not in f[2] for f in fails)

    def test_empty_counts_no_fail(self):
        assert _bs().detect_poller_spins(Counter()) == []

    def test_threshold_is_above_the_largest_poller_cap(self):
        # The launchpad poller caps at MAX_MISSING_POLLS = 20; the spin threshold
        # must sit strictly above it, or a poll that legitimately reaches its cap
        # would be mis-flagged as a spin (false positive). Pin the relationship.
        import re

        bs = _bs()
        launchpad_tmpl = (
            REPO / "src" / "trinity_local" / "launchpad_template.py"
        ).read_text(encoding="utf-8")
        caps = [int(m) for m in re.findall(r"MAX_MISSING_POLLS\s*=\s*(\d+)", launchpad_tmpl)]
        assert caps, "expected a MAX_MISSING_POLLS cap in the launchpad poller"
        assert bs.SPIN_THRESHOLD > max(caps), (
            f"SPIN_THRESHOLD ({bs.SPIN_THRESHOLD}) must exceed the largest "
            f"launchpad poller cap ({max(caps)}) or it false-positives")


class TestSpinHandlerTalliesStatus404s:
    """The handler's send_error must increment the per-token tally only for
    council_status 404s — not other 404s, not non-404 errors. Each test
    monkeypatches the SUPERCLASS send_error to a no-op (so no real HTTP write
    happens) and builds the handler via __new__ to skip the socket __init__."""

    def _handler_for(self, bs, path: str, monkeypatch):
        monkeypatch.setattr(
            bs.http.server.SimpleHTTPRequestHandler,
            "send_error",
            lambda *a, **k: None,
        )
        h = bs._SpinTrackingHandler.__new__(bs._SpinTrackingHandler)
        h.path = path
        return h

    def test_status_404_increments_tally(self, monkeypatch):
        bs = _bs()
        bs.STATUS_404_COUNTS.clear()
        h = self._handler_for(
            bs, "/portal_pages/status/council_status_launch_xyz.js?t=123", monkeypatch
        )
        h.send_error(404)
        h.send_error(404)
        assert bs.STATUS_404_COUNTS["council_status_launch_xyz"] == 2

    def test_non_status_404_not_tallied(self, monkeypatch):
        bs = _bs()
        bs.STATUS_404_COUNTS.clear()
        h = self._handler_for(bs, "/portal_pages/some_other_missing_file.js", monkeypatch)
        h.send_error(404)
        assert sum(bs.STATUS_404_COUNTS.values()) == 0

    def test_non_404_status_not_tallied(self, monkeypatch):
        bs = _bs()
        bs.STATUS_404_COUNTS.clear()
        h = self._handler_for(
            bs, "/portal_pages/status/council_status_launch_xyz.js", monkeypatch
        )
        h.send_error(500)  # a server error on a status path is not a missing-poll
        assert sum(bs.STATUS_404_COUNTS.values()) == 0
