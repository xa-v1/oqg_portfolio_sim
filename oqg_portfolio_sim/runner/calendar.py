"""Market calendar awareness (PROJECT_SPEC.md: "account for the market
calendar -- skip holidays/weekends"), via pandas_market_calendars.

GitHub Actions cron fires on a fixed schedule regardless of holidays; this
module is what lets the runner recognize "there's nothing to do today" and
exit cleanly instead of trying to process a non-trading day.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas_market_calendars as mcal

DEFAULT_CALENDAR = "CFE"

# Generous enough to bridge any real holiday cluster (e.g. year-end) while
# staying cheap to query.
_LOOKBACK_DAYS = 21


def is_trading_day(day: date, calendar_name: str = DEFAULT_CALENDAR) -> bool:
    calendar = mcal.get_calendar(calendar_name)
    sessions = calendar.valid_days(start_date=day, end_date=day)
    return len(sessions) > 0


def previous_session(day: date, calendar_name: str = DEFAULT_CALENDAR) -> date:
    """The trading session strictly before `day`."""

    calendar = mcal.get_calendar(calendar_name)
    sessions = calendar.valid_days(
        start_date=day - timedelta(days=_LOOKBACK_DAYS), end_date=day)
    prior = [session.date() for session in sessions if session.date() < day]
    if not prior:
        raise ValueError(
            f"no trading session found in the {_LOOKBACK_DAYS} days before {day} "
            f"on the {calendar_name} calendar"
        )
    return prior[-1]
