"""Equities/ETF EOD sources: yfinance (primary), Stooq (fallback).

Both conform to EodSource. FallbackSource chains sources in priority order
and only moves to the next one on a DataSourceError, so a strategy runner
never has to know which source actually answered.
"""

from __future__ import annotations

from datetime import date, timedelta
from io import StringIO

import pandas as pd
import requests

from oqg_portfolio_sim.strategies.contracts import PriceBar, PricePanel

from .base import DataSourceError, EodSource

_STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


class YFinanceSource:
    """Primary equities/ETF source."""

    def fetch(self, instrument_id: str, start: date, end: date) -> list[PriceBar]:
        import yfinance as yf

        try:
            df = yf.download(
                instrument_id,
                start=start,
                end=end + timedelta(days=1),  # yfinance's `end` is exclusive
                progress=False,
                auto_adjust=False,
            )
        except Exception as exc:  # yfinance raises assorted exception types
            raise DataSourceError(
                f"yfinance fetch failed for {instrument_id}: {exc}") from exc

        if df.empty:
            raise DataSourceError(
                f"yfinance returned no data for {instrument_id} in [{start}, {end}]")

        closes = df["Close"]
        if hasattr(closes, "columns"):  # multi-index column when yfinance batches tickers
            closes = closes[instrument_id]

        return [
            PriceBar(as_of=ts.date(), close=float(value))
            for ts, value in closes.items()
            if pd.notna(value)
        ]


class StooqSource:
    """Fallback equities/ETF source, backed by Stooq's free CSV export.

    Stooq occasionally serves a bot-check page instead of CSV; that is
    treated as a hard failure so FallbackSource moves on rather than
    parsing HTML as prices.
    """

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, instrument_id: str, start: date, end: date) -> list[PriceBar]:
        symbol = f"{instrument_id.lower()}.us"
        url = _STOOQ_URL.format(symbol=symbol)
        try:
            response = requests.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DataSourceError(
                f"stooq request failed for {instrument_id}: {exc}") from exc

        text = response.text
        if not text.startswith("Date,"):
            raise DataSourceError(
                f"stooq returned an unexpected response for {instrument_id} "
                "(likely blocked or an invalid symbol)"
            )

        df = pd.read_csv(StringIO(text), parse_dates=["Date"])
        mask = (df["Date"].dt.date >= start) & (df["Date"].dt.date <= end)
        df = df.loc[mask].sort_values("Date")

        return [
            PriceBar(as_of=row.Date.date(), close=float(row.Close))
            for row in df.itertuples()
        ]


class FallbackSource:
    """Tries each source in order; returns the first successful result."""

    def __init__(self, sources: list[EodSource]) -> None:
        if not sources:
            raise ValueError("FallbackSource requires at least one source")
        self.sources = sources

    def fetch(self, instrument_id: str, start: date, end: date) -> list[PriceBar]:
        errors: list[str] = []
        for source in self.sources:
            try:
                return source.fetch(instrument_id, start, end)
            except DataSourceError as exc:
                errors.append(f"{type(source).__name__}: {exc}")

        joined = "; ".join(errors)
        raise DataSourceError(
            f"all sources failed for {instrument_id} in [{start}, {end}]: {joined}"
        )


def build_equity_panel(
    source: EodSource, instrument_ids: list[str], start: date, end: date
) -> PricePanel:
    """Fetch each instrument from `source` and assemble a normalized PricePanel."""

    return PricePanel(
        series={
            instrument_id: source.fetch(instrument_id, start, end)
            for instrument_id in instrument_ids
        }
    )
