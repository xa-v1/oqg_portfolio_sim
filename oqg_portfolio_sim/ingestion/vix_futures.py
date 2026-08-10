"""VIX futures ingestion: the existing parquet history plus daily Cboe updates.

Two data shapes are in play here:

- A per-contract table (trade_date, contract, settle, settlement_date,
  days_to_settlement) -- this is what the historical parquet already looks
  like, and what a fresh Cboe settlement pull produces once normalized.
- A rank PricePanel (VX1, VX2, VX3, ...) -- contracts ranked by
  days_to_settlement each day, which is how the club's notebooks refer to
  "M1/M2/M3/M4": a rolling label that survives contract rolls, not a fixed
  ticker. Strategies (e.g. the rank butterfly) consume the rank panel, not
  raw contract symbols.
"""

from __future__ import annotations

import re
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from oqg_portfolio_sim.strategies.contracts import PriceBar, PricePanel

from .base import DataSourceError

_DEFAULT_PARQUET_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "vix_futures_combined.parquet"
)
_CBOE_SETTLEMENT_URL = "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv"
# Standard monthly VX contracts only, e.g. "VX/Q6". Excludes weeklies
# ("VX32/Q6") and other products (VXM minis), which the historical parquet
# never contained.
_MONTHLY_SYMBOL_RE = re.compile(r"^VX/[A-Z]\d$")

_NORMALIZED_COLUMNS = ["trade_date", "contract",
                        "settle", "settlement_date", "days_to_settlement"]


def load_vix_futures_parquet(path: str | Path | None = None) -> pd.DataFrame:
    """Normalize the club's existing VIX futures history.

    Returns a per-contract table sorted by (trade_date, days_to_settlement).
    """

    target = Path(path) if path is not None else _DEFAULT_PARQUET_PATH
    if not target.exists():
        raise DataSourceError(f"VIX futures parquet not found at {target}")

    raw = pd.read_parquet(target)
    out = pd.DataFrame(
        {
            "trade_date": raw["Trade Date"].dt.date,
            "contract": raw["Futures"],
            "settle": raw["Settle"].astype(float),
            "settlement_date": raw["settlement_date"].dt.date,
            "days_to_settlement": raw["days_to_settlement"].astype(int),
        }
    )
    return out.sort_values(["trade_date", "days_to_settlement"]).reset_index(drop=True)


class CboeSettlementSource:
    """Pulls one day's VIX futures settlement snapshot from Cboe's public feed."""

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_settlement(self, trade_date: date) -> pd.DataFrame:
        """One day's monthly-VX settlement rows, normalized like the parquet."""

        try:
            response = requests.get(
                _CBOE_SETTLEMENT_URL,
                params={"dt": trade_date.isoformat()},
                timeout=self.timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DataSourceError(
                f"Cboe settlement request failed for {trade_date}: {exc}") from exc

        text = response.text
        if not text.startswith("Product,"):
            raise DataSourceError(
                f"Cboe returned an unexpected response for {trade_date} "
                "(likely blocked, or not a trading day)"
            )

        raw = pd.read_csv(StringIO(text))
        raw = raw[raw["Product"] == "VX"]
        raw = raw[raw["Symbol"].str.match(_MONTHLY_SYMBOL_RE)]
        if raw.empty:
            raise DataSourceError(
                f"no monthly VX settlement rows returned for {trade_date}")

        settlement_date = pd.to_datetime(raw["Expiration Date"]).dt.date
        out = pd.DataFrame(
            {
                "trade_date": trade_date,
                "contract": raw["Symbol"].to_numpy(),
                "settle": raw["Price"].astype(float).to_numpy(),
                "settlement_date": settlement_date.to_numpy(),
            }
        )
        out["days_to_settlement"] = [
            (settle_date - trade_date).days for settle_date in out["settlement_date"]
        ]
        return out.sort_values("days_to_settlement").reset_index(drop=True)


def merge_settlement_updates(history: pd.DataFrame, updates: pd.DataFrame) -> pd.DataFrame:
    """Append fresh settlement rows onto the historical table, idempotently.

    Re-running with the same day's update is a no-op: rows are de-duplicated
    on (trade_date, contract), keeping the newest value.
    """

    combined = pd.concat(
        [history[_NORMALIZED_COLUMNS], updates[_NORMALIZED_COLUMNS]], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["trade_date", "contract"], keep="last")
    return combined.sort_values(["trade_date", "days_to_settlement"]).reset_index(drop=True)


def refresh_history(
    history: pd.DataFrame,
    through: date,
    calendar_name: str = "CFE",
    source: CboeSettlementSource | None = None,
    max_backfill_sessions: int = 30,
) -> tuple[pd.DataFrame, list[str]]:
    """Pulls and merges any trading-session settlements missing between the
    latest date already in `history` and `through` (inclusive).

    A failure fetching `through` itself is raised -- the runner can't
    proceed without today's fill price. A failure fetching an older missing
    session (e.g. a transient outage during a prior run) is recorded as a
    warning and skipped, rather than blocking today's cycle; that session
    simply remains a gap in the raw price history.

    Each missing session is one live HTTP request, fetched sequentially.
    In steady daily operation there's normally at most one. If the gap is
    larger than `max_backfill_sessions` (e.g. the runner hasn't run in
    months, or `through` is a bad date far in the future), this raises
    rather than silently issuing hundreds of sequential requests -- that's
    a data-pipeline problem needing a real bulk backfill, not something to
    paper over inside a daily cron step.
    """

    import pandas_market_calendars as mcal

    source = source or CboeSettlementSource()
    last_date = history["trade_date"].max()

    calendar = mcal.get_calendar(calendar_name)
    sessions = calendar.valid_days(start_date=last_date, end_date=through)
    missing = sorted({s.date() for s in sessions if s.date() > last_date})

    if len(missing) > max_backfill_sessions:
        raise DataSourceError(
            f"{len(missing)} sessions missing between {last_date} and {through} "
            f"(> max_backfill_sessions={max_backfill_sessions}); needs a manual "
            "bulk backfill, not a daily-cycle catch-up"
        )

    warnings: list[str] = []
    for session_date in missing:
        try:
            updates = source.fetch_settlement(session_date)
        except DataSourceError as exc:
            if session_date == through:
                raise
            warnings.append(f"skipped {session_date}: {exc}")
            continue
        history = merge_settlement_updates(history, updates)

    return history, warnings


def rank_panel(history: pd.DataFrame, n_ranks: int = 4, prefix: str = "VX") -> PricePanel:
    """Build continuous rank series (VX1..VXN) from a per-contract table.

    Each trading day, contracts are ranked by days_to_settlement ascending
    (nearest expiry = rank 1, i.e. "M1"). This is what lets a strategy
    target a stable instrument id like "VX2" across contract rolls, instead
    of tracking raw expiring symbols itself.

    Contracts with days_to_settlement <= 0 (a contract's own settlement day)
    are excluded before ranking, matching the source notebook. Without this,
    the about-to-expire front contract would still occupy rank 1 on its own
    settlement day and shift every other rank down by one for that session.
    """

    series: dict[str, list[PriceBar]] = {
        f"{prefix}{i}": [] for i in range(1, n_ranks + 1)}

    unexpired = history[history["days_to_settlement"] > 0]
    for trade_date, day_rows in unexpired.groupby("trade_date"):
        ranked = day_rows.sort_values(
            "days_to_settlement").reset_index(drop=True)
        for rank in range(min(n_ranks, len(ranked))):
            instrument_id = f"{prefix}{rank + 1}"
            series[instrument_id].append(
                PriceBar(as_of=trade_date, close=float(
                    ranked.loc[rank, "settle"]))
            )

    for bars in series.values():
        bars.sort(key=lambda bar: bar.as_of)

    return PricePanel(series=series)
