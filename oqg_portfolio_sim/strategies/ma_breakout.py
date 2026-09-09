"""Deliberately naive reference strategy: SPY 200-day MA breakout, fixed 5-day hold.

This is a canary, not a serious strategy. The rank butterfly only trades on
rare VIX term-structure z-score signals, which makes it a poor way to
eyeball "is the pipeline actually alive" day to day. This strategy trades
often enough that a quick glance at the site's trade log or equity curve
answers that question on its own, without waiting on a rare signal.

Rule: enter long `qty` shares of `instrument_id` the day its close crosses
from at-or-below its `window`-day rolling mean to above it. Hold for
exactly `hold_days` sessions regardless of what price does in between --
no early exit on a dip, no re-entry on a fresh cross mid-hold. Exit
unconditionally at the end of the hold; only look for a new entry once
flat again. No shorting, no sizing logic, no risk management -- naive by
design.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from oqg_portfolio_sim.core.models import InstrumentSpec, load_instrument_specs

from .contracts import PricePanel, TargetPosition


def _walk_breakout_hold(closes: pd.Series, sma: pd.Series, hold_days: int) -> int:
    """Replays the entry/hold/exit state machine, returns the final position (0 or 1)."""

    pos = 0
    days_held = 0
    for t in range(len(closes)):
        if pd.isna(sma.iloc[t]):
            continue
        if pos == 0:
            crossed_above = (
                t > 0
                and not pd.isna(sma.iloc[t - 1])
                and closes.iloc[t] > sma.iloc[t]
                and closes.iloc[t - 1] <= sma.iloc[t - 1]
            )
            if crossed_above:
                pos, days_held = 1, 0
        else:
            days_held += 1
            if days_held >= hold_days:
                pos, days_held = 0, 0
    return pos


class MaBreakoutHoldStrategy:
    def __init__(
        self,
        instrument_id: str = "SPY",
        window: int = 200,
        hold_days: int = 5,
        qty: float = 1.0,
    ) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if hold_days < 1:
            raise ValueError("hold_days must be >= 1")
        self.instrument_id = instrument_id
        self.window = window
        self.hold_days = hold_days
        self.qty = qty

    def required_instruments(self) -> list[InstrumentSpec]:
        specs = {spec.instrument_id: spec for spec in load_instrument_specs()}
        if self.instrument_id not in specs:
            raise ValueError(
                f"missing instrument spec for {self.instrument_id}. "
                "Add it to config/instruments.json."
            )
        return [specs[self.instrument_id]]

    def generate_targets(self, as_of: date, history: PricePanel) -> list[TargetPosition]:
        bars = history.closes_as_of(self.instrument_id, as_of)
        if len(bars) < self.window + 1:
            return []

        closes = pd.Series({bar.as_of: bar.close for bar in bars})
        sma = closes.rolling(self.window).mean()

        pos = _walk_breakout_hold(closes, sma, self.hold_days)
        if pos == 0:
            return []

        return [
            TargetPosition(
                self.instrument_id,
                self.qty,
                reason=f"{self.window}d MA breakout, {self.hold_days}d hold",
            )
        ]
