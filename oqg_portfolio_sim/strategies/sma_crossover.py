"""Deliberately dumb reference strategy for exercising the simulation pipeline.

Rule: go long `qty` units of `instrument_id` when its last close is above its
trailing `window`-day simple moving average, otherwise stay flat. No shorting,
no sizing logic, no risk management. It exists to give the ledger/engine
something real to chew on while richer strategies (e.g. the VIX rank
butterfly) are ported.
"""

from __future__ import annotations

from datetime import date

from oqg_portfolio_sim.core.models import InstrumentSpec, load_instrument_specs

from .contracts import PricePanel, TargetPosition


class SmaCrossoverStrategy:
    def __init__(
        self,
        instrument_id: str = "SPY",
        window: int = 20,
        qty: float = 1.0,
    ) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.instrument_id = instrument_id
        self.window = window
        self.qty = qty

    def required_instruments(self) -> list[InstrumentSpec]:
        specs = load_instrument_specs()
        return [spec for spec in specs if spec.instrument_id == self.instrument_id]

    def generate_targets(self, as_of: date, history: PricePanel) -> list[TargetPosition]:
        bars = history.closes_as_of(self.instrument_id, as_of)
        if len(bars) < self.window:
            return []

        window_bars = bars[-self.window:]
        sma = sum(bar.close for bar in window_bars) / self.window
        last_close = bars[-1].close

        if last_close > sma:
            return [
                TargetPosition(
                    instrument_id=self.instrument_id,
                    qty=self.qty,
                    reason=f"close {last_close:.2f} > {self.window}d SMA {sma:.2f}",
                )
            ]
        return []
