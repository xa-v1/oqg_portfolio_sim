"""Daily mark-to-market P&L.

Mirrors the notebook's own `daily_pnl` helper: a position held coming into
today accrues P&L from the price move since it was last marked (yesterday's
close to today's close/fill price); qty added today, by construction,
enters at today's price and so contributes zero P&L today. Costs are
tracked separately (see engine/fills.py) and netted out here.
"""

from __future__ import annotations

from oqg_portfolio_sim.core.models import InstrumentSpec

from .fills import Fill


def gross_pnl(
    prior_positions: dict[str, float],
    prior_marks: dict[str, float],
    today_marks: dict[str, float],
    specs: dict[str, InstrumentSpec],
) -> float:
    """Mark-to-market P&L on positions held coming into today.

    Only instruments present in both `prior_positions` and both mark dicts
    contribute; an instrument missing a mark is a data gap the caller
    should have caught before calling this (silently skipping it here would
    understate P&L without any signal that something's wrong), so it raises.
    """

    total = 0.0
    for instrument_id, qty in prior_positions.items():
        if qty == 0:
            continue
        if instrument_id not in prior_marks or instrument_id not in today_marks:
            raise KeyError(
                f"missing mark for held position {instrument_id}")
        spec = specs[instrument_id]
        price_change = today_marks[instrument_id] - prior_marks[instrument_id]
        total += qty * price_change * spec.multiplier
    return total


def net_pnl(gross: float, fills: list[Fill]) -> float:
    """Gross P&L minus this run's explicit + implicit trading costs."""

    total_cost = sum(fill.explicit_cost + fill.implicit_cost for fill in fills)
    return gross - total_cost
