from datetime import datetime, timedelta, timezone

import pytest

from damai_appium.sale_clock import (
    SHANGHAI,
    SaleTimeError,
    SaleTimePast,
    normalize_sale_time,
    wait_for_sale_time,
)


class FakeClock:
    def __init__(self, wall, monotonic=100.0, oversleep=0.0):
        self.wall = wall
        self.monotonic_value = monotonic
        self.oversleep = oversleep
        self.sleeps = []
        self.wall_jump = 0.0

    def monotonic(self):
        return self.monotonic_value

    def wall_now(self):
        return self.wall

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        elapsed = seconds + self.oversleep
        self.monotonic_value += elapsed
        self.wall += timedelta(seconds=elapsed + self.wall_jump)
        self.wall_jump = 0.0


def test_normalize_sale_time_uses_shanghai_for_naive_input():
    assert normalize_sale_time("2026-11-13T20:00:00") == "2026-11-13T20:00:00+08:00"


def test_normalize_sale_time_converts_aware_input():
    assert normalize_sale_time("2026-11-13T12:00:00+00:00") == "2026-11-13T20:00:00+08:00"


@pytest.mark.parametrize("value", ["", None, "2026-11-13", "not-a-time", 123])
def test_invalid_sale_time_is_rejected(value):
    with pytest.raises(SaleTimeError):
        normalize_sale_time(value)


def test_wait_uses_coarse_and_final_sleeps_without_real_waiting():
    wall = datetime(2026, 11, 13, 19, 59, 50, tzinfo=SHANGHAI)
    clock = FakeClock(wall)

    release = wait_for_sale_time(
        "2026-11-13T20:00:00+08:00",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        wall_now=clock.wall_now,
    )

    assert clock.sleeps[0] == pytest.approx(9.75)
    assert all(item <= 0.005 for item in clock.sleeps[1:])
    assert release.target_monotonic == pytest.approx(110.0)
    assert release.released_monotonic == pytest.approx(110.0)
    assert release.released_at == datetime(2026, 11, 13, 20, 0, tzinfo=SHANGHAI)
    assert release.delay_ms == pytest.approx(0.0)


def test_wall_clock_jump_does_not_change_monotonic_target():
    wall = datetime(2026, 11, 13, 19, 59, 50, tzinfo=SHANGHAI)
    clock = FakeClock(wall)
    clock.wall_jump = 3600.0

    release = wait_for_sale_time(
        "2026-11-13T20:00:00+08:00",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        wall_now=clock.wall_now,
    )

    assert release.target_monotonic == pytest.approx(110.0)
    assert release.released_monotonic == pytest.approx(110.0)
    assert release.released_at.hour == 21


def test_oversleep_is_reported_as_positive_delay():
    wall = datetime(2026, 11, 13, 19, 59, 59, tzinfo=SHANGHAI)
    clock = FakeClock(wall, oversleep=0.001)

    release = wait_for_sale_time(
        "2026-11-13T20:00:00+08:00",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        wall_now=clock.wall_now,
    )

    assert 0 < release.delay_ms < 5


def test_past_sale_time_never_sleeps():
    clock = FakeClock(datetime(2026, 11, 13, 20, 0, 1, tzinfo=SHANGHAI))

    with pytest.raises(SaleTimePast, match="已过"):
        wait_for_sale_time(
            "2026-11-13T20:00:00+08:00",
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            wall_now=clock.wall_now,
        )

    assert clock.sleeps == []


def test_utc_target_is_anchored_at_same_instant():
    wall = datetime(2026, 11, 13, 11, 59, 59, tzinfo=timezone.utc)
    clock = FakeClock(wall)

    release = wait_for_sale_time(
        "2026-11-13T12:00:00Z",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        wall_now=clock.wall_now,
    )

    assert release.target_time == datetime(2026, 11, 13, 20, 0, tzinfo=SHANGHAI)
    assert release.target_monotonic == pytest.approx(101.0)
