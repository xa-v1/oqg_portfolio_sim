"""The strategy contract described in PROJECT_SPEC.md.

Strategies never touch data sources or the ledger directly. They receive a
point-in-time PricePanel and return TargetPosition intent; the (future)
simulation engine diffs targets against current holdings to produce orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from oqg_portfolio_sim.core.models import InstrumentSpec


@dataclass(frozen=True)
class PriceBar:
    """A single EOD observation for one instrument."""

    as_of: date
    close: float


@dataclass(frozen=True)
class PricePanel:
    """Price history keyed by instrument_id, each a list of PriceBar sorted by date."""

    series: dict[str, list[PriceBar]] = field(default_factory=dict)

    def closes_as_of(self, instrument_id: str, as_of: date) -> list[PriceBar]:
        """Bars for `instrument_id` dated <= `as_of`, oldest first.

        This is the point-in-time enforcement point: callers must never see
        bars dated after `as_of`, since that would be lookahead.
        """

        bars = self.series.get(instrument_id, [])
        return [bar for bar in bars if bar.as_of <= as_of]

    def last_close_as_of(self, instrument_id: str, as_of: date) -> float | None:
        """Most recent close for `instrument_id` dated <= `as_of`, or None."""

        bars = self.closes_as_of(instrument_id, as_of)
        return bars[-1].close if bars else None


def merge_panels(*panels: PricePanel) -> PricePanel:
    """Combines PricePanels from different sources (e.g. VIX futures + equities)
    into one, keyed by instrument_id, sorted by date within each series."""

    merged: dict[str, list[PriceBar]] = {}
    for panel in panels:
        for instrument_id, bars in panel.series.items():
            merged.setdefault(instrument_id, []).extend(bars)

    for bars in merged.values():
        bars.sort(key=lambda bar: bar.as_of)

    return PricePanel(series=merged)


@dataclass(frozen=True)
class TargetPosition:
    """Desired position for one instrument, as of a given date."""

    instrument_id: str
    qty: float
    reason: str = ""


class Strategy(Protocol):
    """Interface every strategy must conform to."""

    def required_instruments(self) -> list[InstrumentSpec]:
        """Which instruments this strategy needs data for."""
        ...

    def generate_targets(self, as_of: date, history: PricePanel) -> list[TargetPosition]:
        """Desired positions as of end-of-day `as_of`. [] means flat."""
        ...
