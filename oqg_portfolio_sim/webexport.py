"""Generates the static JSON the website reads (PROJECT_SPEC.md "Website").

The website is static and reads committed ledger data only -- no server, no
live DB queries from the browser. This module is the bridge: it reads the
SQLite ledger (the source of truth) and writes derived JSON views to
docs/data/, which GitHub Pages then serves as plain static files.

Only data from runs with status='completed' AND reconciliation_status='ok'
ever appears in an equity curve or trade log -- "never publish a run that
failed reconciliation" is enforced here, not left to the frontend to filter.
Gaps and errors are computed separately and always included, precisely
because those ARE meant to be visible, not hidden.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from oqg_portfolio_sim.core import ledger

_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "data"

# Trading days per year, used to annualize the Sharpe-like ratio -- matches
# the convention already used in rank_butterflies.ipynb's own sharpe() helper.
_TRADING_DAYS_PER_YEAR = 252


def compute_stats(equity_curve: list[dict]) -> dict:
    """Net return, Sharpe-like ratio, max drawdown, and daily win rate.

    All computed off the daily net_pnl / cum_net_pnl series, mirroring the
    notebook's own sharpe() helper methodology (mean/std annualized by
    sqrt(252)) for consistency with numbers already validated there.
    """

    if not equity_curve:
        return {
            "net_return_pct": None,
            "sharpe_like": None,
            "max_drawdown_dollars": None,
            "max_drawdown_pct": None,
            "win_rate": None,
            "days_tracked": 0,
        }

    capital_base = equity_curve[-1]["capital_base"]
    net_pnls = [row["net_pnl"] for row in equity_curve]
    cum = [row["cum_net_pnl"] for row in equity_curve]

    net_return_pct = (cum[-1] / capital_base *
                       100) if capital_base else None

    sharpe_like = None
    if capital_base and len(net_pnls) > 1:
        returns = [pnl / capital_base for pnl in net_pnls]
        mean_r = statistics.fmean(returns)
        std_r = statistics.pstdev(returns)
        if std_r > 0:
            sharpe_like = (mean_r / std_r) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    peak = float("-inf")
    max_drawdown_dollars = 0.0
    for value in cum:
        peak = max(peak, value)
        max_drawdown_dollars = min(max_drawdown_dollars, value - peak)
    max_drawdown_pct = (max_drawdown_dollars /
                         capital_base * 100) if capital_base else None

    win_rate = sum(1 for pnl in net_pnls if pnl > 0) / len(net_pnls)

    return {
        "net_return_pct": round(net_return_pct, 4) if net_return_pct is not None else None,
        "sharpe_like": round(sharpe_like, 4) if sharpe_like is not None else None,
        "max_drawdown_dollars": round(max_drawdown_dollars, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 4) if max_drawdown_pct is not None else None,
        "win_rate": round(win_rate, 4),
        "days_tracked": len(equity_curve),
    }


def compute_gaps_and_errors(
    db_path: str | Path, calendar_name: str = "CFE"
) -> tuple[list[str], list[str]]:
    """(gaps, errors): trading sessions with no run row at all, vs sessions
    with a run row that ended in status='error'.

    Gaps are only computed within the track record's own operating window
    (first completed run through last completed run) -- there's no gap in
    "today" just because today's cron hasn't fired yet.
    """

    completed_dates = set(ledger.list_completed_run_dates(db_path))
    all_runs = ledger.list_all_runs(db_path)
    error_dates = sorted({run["as_of_date"]
                          for run in all_runs if run["status"] == "error"})

    if not completed_dates:
        return [], error_dates

    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar(calendar_name)
    sessions = calendar.valid_days(
        start_date=min(completed_dates), end_date=max(completed_dates))
    expected = {session.date().isoformat() for session in sessions}

    known = completed_dates | set(error_dates)
    gaps = sorted(expected - known)
    return gaps, error_dates


def export_site_data(
    db_path: str | Path,
    output_dir: str | Path | None = None,
    calendar_name: str = "CFE",
) -> Path:
    """Writes docs/data/index.json and docs/data/{strategy_id}.json.

    Safe to call after every daily run, successful or not: it always
    reflects the ledger's current state, including any gaps/errors, without
    ever including data from a run that failed reconciliation.
    """

    target_dir = Path(output_dir) if output_dir is not None else _DEFAULT_OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    strategies = ledger.list_strategies(db_path)
    gaps, errors = compute_gaps_and_errors(db_path, calendar_name)

    index_entries = []
    for strategy in strategies:
        strategy_id = strategy["strategy_id"]
        equity_curve = ledger.get_equity_curve(db_path, strategy_id)
        trades = ledger.get_trade_log(db_path, strategy_id)
        stats = compute_stats(equity_curve)
        capital_base = equity_curve[-1]["capital_base"] if equity_curve else None

        strategy_doc = {
            "strategy_id": strategy_id,
            "name": strategy["name"],
            "description": strategy["description"],
            "owner": strategy["owner"],
            "asset_class": strategy["asset_class"],
            "active": bool(strategy["active"]),
            "capital_base": capital_base,
            "stats": stats,
            "equity_curve": equity_curve,
            "trades": list(reversed(trades)),  # newest first for the trade log
            "gaps": gaps,
            "errors": errors,
        }
        (target_dir / f"{strategy_id}.json").write_text(
            json.dumps(strategy_doc, indent=2))

        index_entries.append({
            "strategy_id": strategy_id,
            "name": strategy["name"],
            "description": strategy["description"],
            "asset_class": strategy["asset_class"],
            "active": bool(strategy["active"]),
            "capital_base": capital_base,
            "cum_net_pnl": equity_curve[-1]["cum_net_pnl"] if equity_curve else 0.0,
            "net_return_pct": stats["net_return_pct"],
            "last_date": equity_curve[-1]["date"] if equity_curve else None,
        })

    index_doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategies": index_entries,
        "gaps": gaps,
        "errors": errors,
    }
    (target_dir / "index.json").write_text(json.dumps(index_doc, indent=2))

    return target_dir
