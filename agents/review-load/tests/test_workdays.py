from datetime import UTC, datetime, timedelta

import pytest
from hackbot_agents.review_load.workdays import is_weekend, work_days_between

# 2026-10-16 is a Friday.
FRI = datetime(2026, 10, 16, 9, tzinfo=UTC)
SAT = datetime(2026, 10, 17, 9, tzinfo=UTC)
SUN = datetime(2026, 10, 18, 9, tzinfo=UTC)
MON = datetime(2026, 10, 19, 9, tzinfo=UTC)


def test_one_weekday_is_one_work_day():
    assert work_days_between(MON, MON + timedelta(days=1)) == 1


def test_friday_to_monday_is_one_work_day():
    assert work_days_between(FRI, MON) == 1


def test_weekend_time_does_not_count():
    assert work_days_between(SAT, SUN) == 0


def test_a_request_on_saturday_counts_from_monday_midnight():
    assert work_days_between(SAT, MON) == pytest.approx(9 / 24)


def test_two_weeks_is_ten_work_days():
    assert work_days_between(MON, MON + timedelta(days=14)) == 10


def test_end_before_start_is_zero():
    assert work_days_between(MON, FRI) == 0


def test_other_timezones_are_read_as_utc():
    plus_two = datetime(2026, 10, 19, 11, tzinfo=UTC).astimezone(
        datetime.now(UTC).astimezone().tzinfo
    )
    assert work_days_between(FRI, plus_two) == pytest.approx(1 + 2 / 24)


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        work_days_between(datetime(2026, 10, 16), MON)


@pytest.mark.parametrize(
    ("moment", "weekend"),
    [(FRI, False), (SAT, True), (SUN, True), (MON, False)],
)
def test_is_weekend(moment, weekend):
    assert is_weekend(moment) is weekend
