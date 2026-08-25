"""The identifier that ships rotates; the permanent one never leaves.

The payload contract (#231) was always strong — allowlisted categorical labels,
never text. The IDENTIFIER was the weak half: a permanent random token meant
every event from one install linked to every other, forever. Months of
{task_type, winner} under one stable key is a behavioural profile of one
person's work even when no single row contains PII.

What ships is sha256(local_id + period)[:16]: stable inside a period so
distinct-install counting still answers "did a second human activate", and
uncorrelatable across periods so no long-horizon profile can be assembled.
"""
from __future__ import annotations

from datetime import datetime, timezone

from trinity_local.telemetry import ROTATION_PERIOD, outbound_client_id

AUG_A = datetime(2026, 8, 3, tzinfo=timezone.utc)
AUG_B = datetime(2026, 8, 27, tzinfo=timezone.utc)
SEP = datetime(2026, 9, 4, tzinfo=timezone.utc)


class TestWhatMeasurementNeedsSurvives:
    def test_one_install_is_stable_within_a_period(self):
        assert outbound_client_id("share_a", now=AUG_A) == outbound_client_id("share_a", now=AUG_B)

    def test_two_installs_stay_distinct(self):
        assert outbound_client_id("share_a", now=AUG_A) != outbound_client_id("share_b", now=AUG_A)


class TestWhatMeasurementDoesNotNeedIsGone:
    def test_the_same_install_is_unlinkable_across_periods(self):
        assert outbound_client_id("share_a", now=AUG_A) != outbound_client_id("share_a", now=SEP)

    def test_the_local_id_never_appears_in_the_outbound_value(self):
        local = "share_deadbeefcafe"
        assert local not in outbound_client_id(local, now=AUG_A)

    def test_the_outbound_id_is_not_reversible_by_length_or_shape(self):
        out = outbound_client_id("share_a", now=AUG_A)
        assert len(out) == 16 and all(c in "0123456789abcdef" for c in out)


class TestTheWireUsesIt:
    def test_the_emitter_sends_the_rotating_id_not_the_local_one(self):
        import inspect

        from trinity_local import telemetry

        src = inspect.getsource(telemetry)
        assert '"client_id": outbound_client_id(settings.share_install_id)' in src, (
            "the emitter is back to shipping the permanent install id")

    def test_an_absent_local_id_yields_no_identifier(self):
        assert outbound_client_id("") == ""


class TestTheRotationIsRealAndDeclared:
    def test_the_period_is_a_named_constant_not_a_literal(self):
        assert ROTATION_PERIOD and "%" in ROTATION_PERIOD

    def test_a_year_boundary_still_rotates(self):
        dec = datetime(2026, 12, 20, tzinfo=timezone.utc)
        jan = datetime(2027, 1, 5, tzinfo=timezone.utc)
        assert outbound_client_id("share_a", now=dec) != outbound_client_id("share_a", now=jan)
