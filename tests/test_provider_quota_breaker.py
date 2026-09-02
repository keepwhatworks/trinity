"""A usage wall is recognised, remembered for the process, and never silent.

Provider quota exhaustion killed three measurement runs in one week (res_081,
res_098, res_112) and each time the loop kept dispatching into a wall it had
already hit. The experiment harness grew a circuit breaker; the product never
did — a user on a subscription hit the same wall and got "member failed" with a
raw stderr excerpt, which reads as a Trinity bug.

The classifier ALSO had the wrong string: it carried "usage limit reached",
which nothing emits, while codex prints "You've hit your usage limit ... try
again at 4:12 AM" (captured 2026-08-30 and 2026-08-31). That real banner
classified as UNKNOWN with retry_with_other_provider=False — the single verdict
that tells the caller not to try anybody else.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from trinity_local.dispatch_errors import (
    DispatchErrorKind,
    classify_dispatch_failure,
    parse_retry_after,
)
from trinity_local import provider_quota

# Verbatim, as captured from the founder's codex CLI on 2026-08-30.
REAL_CODEX_BANNER = (
    "You've hit your usage limit. Upgrade to Pro "
    "(https://chatgpt.com/explore/pro), visit "
    "https://chatgpt.com/codex/settings/usage to purchase more credits "
    "or try again at 4:12 AM."
)


@pytest.fixture(autouse=True)
def _clean_registry():
    provider_quota.clear()
    yield
    provider_quota.clear()


class TestTheRealBannerClassifies:
    def test_captured_codex_banner_is_rate_limited_not_unknown(self):
        f = classify_dispatch_failure(provider="codex", returncode=1, stderr=REAL_CODEX_BANNER)
        assert f.kind is DispatchErrorKind.RATE_LIMITED, (
            "the string codex actually prints must classify as a quota wall; "
            "UNKNOWN here also sets retry_with_other_provider=False, which tells "
            "the caller not to try any other provider either"
        )
        assert f.retry_with_other_provider is True

    def test_the_stated_reset_time_survives(self):
        f = classify_dispatch_failure(provider="codex", returncode=1, stderr=REAL_CODEX_BANNER)
        assert f.retry_after == "4:12 AM", (
            "the banner states when the wall lifts; dropping it leaves the user "
            "with 'member failed' and no way to plan"
        )
        assert "retry_after" in f.to_dict()

    def test_the_sentence_period_is_not_part_of_the_time(self):
        assert parse_retry_after("try again at 4:12 AM.") == "4:12 AM"
        assert parse_retry_after("try again at 09:22") == "09:22"
        assert parse_retry_after("no reset stated here") is None

    def test_the_legacy_phrasing_still_matches(self):
        assert classify_dispatch_failure(
            provider="codex", returncode=1, stderr="usage limit reached"
        ).kind is DispatchErrorKind.RATE_LIMITED

    def test_an_ordinary_failure_is_untouched(self):
        assert classify_dispatch_failure(
            provider="codex", returncode=1, stderr="Traceback: KeyError('x')"
        ).kind is DispatchErrorKind.UNKNOWN


class TestTheRegistry:
    def test_unmarked_provider_is_not_skipped(self):
        assert provider_quota.is_exhausted("codex") is False

    def test_marked_provider_is_skipped_and_describes_itself(self):
        e = provider_quota.mark_exhausted("codex", kind="rate_limited", retry_after="4:12 AM")
        assert provider_quota.is_exhausted("codex") is True
        assert "4:12 AM" in e.describe() and "codex" in e.describe()

    def test_a_wall_with_no_stated_time_still_describes_itself(self):
        e = provider_quota.mark_exhausted("claude", kind="rate_limited", retry_after=None)
        assert "until" not in e.describe(), "do not invent a reset time the CLI never stated"
        assert provider_quota.is_exhausted("claude") is True

    def test_one_wall_does_not_skip_the_others(self):
        provider_quota.mark_exhausted("codex", kind="rate_limited")
        assert provider_quota.is_exhausted("claude") is False
        assert provider_quota.is_exhausted("antigravity") is False

    def test_the_wall_expires_so_a_recovered_provider_returns(self):
        past = datetime.now() - timedelta(hours=2)
        provider_quota.mark_exhausted("codex", kind="rate_limited", now=past)
        assert provider_quota.is_exhausted("codex") is False, (
            "a stale skip silently drops a provider that already recovered"
        )
        assert "codex" not in provider_quota.exhausted()

    def test_exhausted_never_lists_a_lifted_wall(self):
        provider_quota.mark_exhausted("codex", kind="rate_limited",
                                      now=datetime.now() - timedelta(hours=2))
        provider_quota.mark_exhausted("claude", kind="rate_limited")
        assert set(provider_quota.exhausted()) == {"claude"}


class TestStatusDisclosesIt:
    def test_status_prints_the_wall(self, tmp_path, monkeypatch, capsys):
        from unittest.mock import patch
        from trinity_local.health_checks import DoctorReport
        from trinity_local.commands import status as status_mod
        from trinity_local.commands.status import handle_status

        provider_quota.mark_exhausted("codex", kind="rate_limited", retry_after="4:12 AM")
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "t").mkdir(exist_ok=True)
        with patch.object(status_mod, "run_doctor", lambda: DoctorReport(checks=[])), \
             patch.object(status_mod, "state_dir", lambda: tmp_path), \
             patch.object(status_mod, "tasks_dir", lambda: tmp_path / "t"), \
             patch.object(status_mod, "check_all_adapters", lambda: []), \
             patch.object(status_mod, "check_drift", lambda: []):
            handle_status(type("A", (), {"as_json": False})())
        out = capsys.readouterr().out
        assert "codex: usage limit reached until 4:12 AM" in out, (
            "a skipped provider must be visible in status; an undisclosed skip is "
            "the silent-degradation shape this repo exists to catch"
        )
