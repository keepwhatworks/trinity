"""A fresh install gets ONE next command, not five.

Verified on a real curl|bash install into a throwaway HOME (2026-08-31): every
soft health check fired at once and a brand-new user saw five ⚠ lines pointing
at five different commands, with the one meant to be "the one-command cold
start" listed last. Each check is correct alone; together they are five answers
to "what do I do first". A ladder has exactly one next rung, and the rung
depends on whether there is history to mine yet.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import dataclass, field

from trinity_local.commands import status as status_cmd


@dataclass
class _Check:
    name: str
    ok: bool = True
    fix: str = ""
    detail: str = ""


@dataclass
class _Health:
    checks: list = field(default_factory=list)


def _fresh_health():
    # what run_doctor() returns on an empty home: every cold-start check soft-fails
    return _Health([
        _Check("prompts_seeded", True, "trinity-local import-export <p>", "no transcripts seeded yet"),
        _Check("lens_built", True, "trinity-local lens", "lens not built yet"),
        _Check("core_distilled", True, "trinity-local lens", "core.md not distilled yet"),
        _Check("lens_freshness", True, "trinity-local lens --deep", "lens vocab + topics not built yet"),
        _Check("embedding_backend", True, "download-embedder", "embedder can't produce vectors"),  # NOT cold-start (#273)
        _Check("retired_dirs_reclaimable", True, "rm -rf …", "reclaimable: cache/"),  # NOT cold-start
    ])


class TestTheRung:
    def test_no_history_points_at_a_council(self):
        rung = status_cmd._first_run_rung(_fresh_health(), council_count=0, total_transcripts=0)
        assert rung is not None
        assert "council" in rung["fix"] and "/trinity" in rung["fix"], (
            "with nothing to mine, the honest first step is a council, which needs no history"
        )

    def test_history_points_at_the_deep_build(self):
        rung = status_cmd._first_run_rung(_fresh_health(), council_count=0, total_transcripts=20_000)
        assert rung == {"detail": rung["detail"], "fix": "trinity-local lens --deep"}
        assert "20,000" in rung["detail"]

    def test_one_council_ends_the_first_run(self):
        assert status_cmd._first_run_rung(_fresh_health(), council_count=1, total_transcripts=0) is None

    def test_a_built_lens_ends_the_first_run(self):
        h = _fresh_health()
        h.checks = [c for c in h.checks if c.name != "lens_built"]
        assert status_cmd._first_run_rung(h, council_count=0, total_transcripts=0) is None


class TestTheScreen:
    """The rung must SUPPRESS the checks it subsumes and leave the rest alone."""

    def _render(self, health, councils, transcripts):
        buf = io.StringIO()
        first = status_cmd._first_run_rung(health, councils, transcripts)
        with redirect_stdout(buf):
            if first:
                print(f"             ▶ {first['detail']}")
                print(f"               └─ run: {first['fix']}")
            for c in health.checks:
                if first and c.name in status_cmd._COLD_START_CHECKS:
                    continue
                if c.ok and c.fix:
                    print(f"             ⚠ {c.detail}")
                    print(f"               └─ run: {c.fix}")
        return buf.getvalue()

    def test_fresh_screen_has_exactly_one_rung_and_no_cold_start_noise(self):
        out = self._render(_fresh_health(), 0, 0)
        assert out.count("▶ First run") == 1
        for noise in ("lens not built yet", "core.md not distilled", "no transcripts seeded", "vocab + topics"):
            assert noise not in out, f"cold-start check leaked past the rung: {noise!r}"
        assert "reclaimable" in out, "unrelated soft checks must still print on a fresh install"
        assert "embedder" in out, "the TF-IDF degradation must never go silent, even on a fresh home (#273)"

    def test_used_install_never_sees_the_rung(self):
        out = self._render(_fresh_health(), 3, 0)
        assert "▶ First run" not in out
        assert "lens not built yet" in out, "once used, the per-check advice returns as before"

    def test_every_suppressed_name_is_a_real_health_check(self):
        from trinity_local import health_checks
        real = {c.name for c in health_checks.run_doctor().checks}
        unknown = status_cmd._COLD_START_CHECKS - real
        assert not unknown, f"suppression list names checks that do not exist: {unknown}"
