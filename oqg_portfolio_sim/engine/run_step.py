"""Orchestrates one strategy's step within a run: fill, cost, mark, margin,
ledger write, reconcile.

This is NOT the GitHub Actions runner (Phase 5) -- it doesn't decide which
as_of/fill dates to process, pull data, or iterate over multiple strategies
across a market calendar. It's the reusable unit Phase 5 will call once per
strategy per day: given already-computed targets and already-resolved fill
prices, do everything from "diff against current holdings" through
"append to the ledger and reconcile."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from oqg_portfolio_sim.core import ledger
from oqg_portfolio_sim.core.models import InstrumentSpec
from oqg_portfolio_sim.strategies.contracts import TargetPosition

from .fills import Fill, simulate_step
from .margin import margin_exceeded
from .pnl import gross_pnl, net_pnl
from .reconcile import FillRecord, recompute_positions_from_fills, reconcile


@dataclass(frozen=True)
class StepResult:
    fills: list[Fill]
    positions: dict[str, float]
    gross_pnl: float
    net_pnl: float
    cum_net_pnl: float
    margin_used: float
    margin_exceeded: bool
    reconciled: bool
    reconciliation_note: str | None


def process_step(
    db_path: str | Path,
    run_id: int,
    strategy_id: str,
    targets: list[TargetPosition],
    fill_prices: dict[str, float],
    prior_marks: dict[str, float],
    specs: dict[str, InstrumentSpec],
    capital_base: float,
    margin_fn: Callable[[dict[str, float]], float],
) -> StepResult:
    """Runs one strategy's step and appends fills/positions/equity to the ledger.

    `margin_fn(positions) -> float` computes approximate margin usage for
    the resulting book; callers pick the calculation that fits their
    strategy's structure (flat per-instrument vs calendar-butterfly spread
    margin -- see engine/margin.py).

    Reconciliation compares positions recomputed from every fill this
    strategy has ever had (including this step's, just written) against the
    snapshot just written for this run. A mismatch here means simulate_step
    and the ledger writers disagree with each other -- an engine bug, not a
    data issue -- so callers should treat it as fatal (halt and mark the run
    `status = error`, per PROJECT_SPEC.md), not retry past it.
    """

    current_positions = ledger.get_current_positions(db_path, strategy_id)
    fills, new_positions = simulate_step(
        targets, current_positions, fill_prices, specs)

    gross = gross_pnl(current_positions, prior_marks, fill_prices, specs)
    net = net_pnl(gross, fills)
    cum_net = ledger.get_cumulative_net_pnl(db_path, strategy_id) + net

    margin_used = margin_fn(new_positions)
    exceeded = margin_exceeded(margin_used, capital_base)

    ledger.insert_fills(db_path, run_id, strategy_id, fills)
    ledger.insert_positions(db_path, run_id, strategy_id, new_positions)
    ledger.insert_equity(
        db_path, run_id, strategy_id,
        gross_pnl=gross, net_pnl=net, cum_net_pnl=cum_net,
        capital_base=capital_base, margin_used=margin_used,
    )

    all_fills = ledger.get_all_fills(db_path, strategy_id)
    recomputed = recompute_positions_from_fills(
        FillRecord(instrument_id, qty) for instrument_id, qty in all_fills
    )
    stored = ledger.get_positions_for_run(db_path, run_id, strategy_id)
    reconciled, note = reconcile(recomputed, stored)

    return StepResult(
        fills=fills,
        positions=new_positions,
        gross_pnl=gross,
        net_pnl=net,
        cum_net_pnl=cum_net,
        margin_used=margin_used,
        margin_exceeded=exceeded,
        reconciled=reconciled,
        reconciliation_note=note,
    )
