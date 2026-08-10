"""Swappable EOD data source interface.

Per PROJECT_SPEC.md: "Abstract behind one interface so sources are
swappable -- free sources break often." Every source returns the same
shape (a list of PriceBar) regardless of where the data came from, and
raises DataSourceError -- never silently returns partial/garbage data --
when it can't produce a trustworthy answer.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from oqg_portfolio_sim.strategies.contracts import PriceBar


class DataSourceError(RuntimeError):
    """A source failed, was blocked, or returned data we don't trust."""


class EodSource(Protocol):
    def fetch(self, instrument_id: str, start: date, end: date) -> list[PriceBar]:
        """EOD closes for `instrument_id` in [start, end], oldest first.

        Raises DataSourceError if the source is unavailable or the
        response can't be parsed as trustworthy price data.
        """
        ...
