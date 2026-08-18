import datetime as dt

import pytest

from evescreener.clock import UTC, bar_datetime, ensure_utc, last_completed_bar_date


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="naive datetime"):
        ensure_utc(dt.datetime(2026, 8, 18, 2, 0))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        # Before the 11:05 UTC roll, yesterday's bar is not published yet.
        (dt.datetime(2026, 8, 18, 2, 20, tzinfo=UTC), dt.date(2026, 8, 16)),
        (dt.datetime(2026, 8, 18, 11, 4, 59, tzinfo=UTC), dt.date(2026, 8, 16)),
        # At and after the roll, yesterday is complete.
        (dt.datetime(2026, 8, 18, 11, 5, tzinfo=UTC), dt.date(2026, 8, 17)),
        (dt.datetime(2026, 8, 18, 23, 59, tzinfo=UTC), dt.date(2026, 8, 17)),
    ],
)
def test_last_completed_bar_date(moment, expected):
    assert last_completed_bar_date(moment) == expected


def test_last_completed_bar_date_matches_observed_live_response():
    """Live check, frozen: at 2026-08-18T02:20Z ESI's newest Forge bar for
    type 34 was dated 2026-08-16. The boundary rule must agree with that."""
    observed = dt.datetime(2026, 8, 18, 2, 20, 19, tzinfo=UTC)
    assert last_completed_bar_date(observed) == dt.date(2026, 8, 16)


def test_bar_datetime_stamps_the_downtime_boundary():
    stamped = bar_datetime(dt.date(2026, 8, 16))
    assert stamped == dt.datetime(2026, 8, 16, 11, 0, tzinfo=UTC)
    assert stamped.tzinfo is not None
