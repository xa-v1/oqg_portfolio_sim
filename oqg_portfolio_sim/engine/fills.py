"""Fill model + cost model (PROJECT_SPEC.md "Simulation engine").

A strategy only ever states intent (TargetPosition). This module is where
that intent becomes a trade: diff the target against current holdings,
price the resulting order at a caller-supplied fill price (the "next
session's settle/close", per the fill convention -- this module does not
pick that price itself; see PROJECT_SPEC's fill convention note), and
attach explicit + implicit costs as separate, inspectable numbers. Costs
are per-instrument-per-fill-event, mirroring the notebook's cost structure
(CFE exchange fee + OCC clearing + NFA fee + assumed broker fee, summed
into `explicit_fee`; half the bid/ask `spread` crossed on every fill).
"""

from __future__ import annotations

from dataclasses import dataclass

from oqg_portfolio_sim.core.models import InstrumentSpec
from oqg_portfolio_sim.strategies.contracts import TargetPosition


@dataclass(frozen=True)
class Fill:
    """One simulated trade: the qty delta needed to move to a target position."""

    instrument_id: str
    qty: float
    fill_price: float
    explicit_cost: float
    implicit_cost: float
    fill_confidence: str
    reason: str


def compute_costs(qty_delta: float, spec: InstrumentSpec) -> tuple[float, float]:
    """(explicit_cost, implicit_cost) in dollars for one fill of `qty_delta` contracts.

    explicit_cost = spec.explicit_fee * |qty|  (exchange/clearing/broker fees, flat per contract)
    implicit_cost = 0.5 * spec.spread * |qty| * spec.multiplier  (half-spread crossed this fill)
    """

    qty = abs(qty_delta)
    explicit_cost = spec.explicit_fee * qty
    implicit_cost = 0.5 * spec.spread * qty * spec.multiplier
    return explicit_cost, implicit_cost


def simulate_fill(
    instrument_id: str,
    current_qty: float,
    target_qty: float,
    fill_price: float,
    spec: InstrumentSpec,
    reason: str = "",
) -> Fill | None:
    """Diffs target vs current holdings for one instrument and prices the trade.

    Returns None if there's nothing to trade (target already matches current
    holdings) -- no phantom zero-qty fill gets recorded.
    """

    qty_delta = target_qty - current_qty
    if qty_delta == 0:
        return None

    explicit_cost, implicit_cost = compute_costs(qty_delta, spec)
    fill_confidence = "low" if spec.liquidity == "illiquid" else "normal"

    return Fill(
        instrument_id=instrument_id,
        qty=qty_delta,
        fill_price=fill_price,
        explicit_cost=explicit_cost,
        implicit_cost=implicit_cost,
        fill_confidence=fill_confidence,
        reason=reason,
    )


def simulate_step(
    targets: list[TargetPosition],
    current_positions: dict[str, float],
    fill_prices: dict[str, float],
    specs: dict[str, InstrumentSpec],
) -> tuple[list[Fill], dict[str, float]]:
    """Diffs all `targets` against `current_positions` and simulates every resulting fill.

    Instruments held but absent from `targets` are treated as flat (target
    qty 0), matching the strategy contract's "[] means flat". Returns
    (fills, new_positions) where new_positions is the full post-fill book
    (unchanged instruments carried over as-is).

    Raises KeyError if an instrument that needs to trade has no fill price
    or instrument spec -- a strategy trading an instrument the engine can't
    price or spec is a configuration error, not something to silently skip.
    """

    target_qty = {t.instrument_id: t.qty for t in targets}
    all_ids = set(target_qty) | set(current_positions)

    fills: list[Fill] = []
    new_positions = dict(current_positions)

    for instrument_id in sorted(all_ids):
        current_qty = current_positions.get(instrument_id, 0.0)
        desired_qty = target_qty.get(instrument_id, 0.0)
        if current_qty == desired_qty:
            continue

        if instrument_id not in fill_prices:
            raise KeyError(
                f"no fill price supplied for {instrument_id}, which needs a trade")
        if instrument_id not in specs:
            raise KeyError(
                f"no instrument spec supplied for {instrument_id}, which needs a trade")

        matching_targets = [
            t for t in targets if t.instrument_id == instrument_id]
        reason = matching_targets[0].reason if matching_targets else "target flat (exit)"

        fill = simulate_fill(
            instrument_id,
            current_qty,
            desired_qty,
            fill_prices[instrument_id],
            specs[instrument_id],
            reason=reason,
        )
        if fill is not None:
            fills.append(fill)
            new_positions[instrument_id] = desired_qty

    return fills, new_positions
