"""Work-day arithmetic in UTC. Saturday and Sunday do not count; holidays do."""

from bisect import bisect_right
from datetime import UTC, datetime, time, timedelta

_DAY = timedelta(days=1)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(f"{moment} has no timezone")
    return moment.astimezone(UTC)


def is_weekend(moment: datetime) -> bool:
    return _utc(moment).weekday() >= 5


def work_days_between(start: datetime, end: datetime) -> float:
    """The work days from ``start`` to ``end``, as a fraction. Zero if ``end`` is earlier."""
    cursor, end = _utc(start), _utc(end)
    total = timedelta()
    while cursor < end:
        next_midnight = datetime.combine(cursor.date() + _DAY, time(), UTC)
        segment_end = min(next_midnight, end)
        if cursor.weekday() < 5:
            total += segment_end - cursor
        cursor = segment_end
    return total / _DAY


def bucket_index(age: float, edges: list[int]) -> int:
    """The bucket for ``age``: 0 is below ``edges[0]``, ``len(edges)`` is at or above the last edge."""
    return bisect_right(edges, age)
