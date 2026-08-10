import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from oqg_portfolio_sim.ingestion.base import DataSourceError
from oqg_portfolio_sim.ingestion.equities import (
    FallbackSource,
    StooqSource,
    YFinanceSource,
    build_equity_panel,
)


class YFinanceSourceTest(unittest.TestCase):
    def test_fetch_returns_price_bars_from_close_column(self) -> None:
        index = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
        df = pd.DataFrame({"Close": [100.0, 101.5, float("nan")]}, index=index)

        with patch("yfinance.download", return_value=df) as mock_download:
            bars = YFinanceSource().fetch("SPY", date(2026, 1, 1), date(2026, 1, 6))

        mock_download.assert_called_once()
        # NaN rows (e.g. a source glitch) must never become a phantom PriceBar.
        self.assertEqual(
            [(bar.as_of, bar.close) for bar in bars],
            [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 101.5)],
        )

    def test_fetch_raises_on_empty_response(self) -> None:
        with patch("yfinance.download", return_value=pd.DataFrame()):
            with self.assertRaises(DataSourceError):
                YFinanceSource().fetch("SPY", date(2026, 1, 1), date(2026, 1, 6))

    def test_fetch_wraps_download_exceptions(self) -> None:
        with patch("yfinance.download", side_effect=RuntimeError("boom")):
            with self.assertRaises(DataSourceError):
                YFinanceSource().fetch("SPY", date(2026, 1, 1), date(2026, 1, 6))


class StooqSourceTest(unittest.TestCase):
    def test_fetch_parses_csv_within_date_range(self) -> None:
        csv_text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-02,99,102,98,100.0,1000\n"
            "2026-01-05,100,103,99,101.5,1200\n"
            "2026-01-06,101,105,100,104.0,1300\n"
        )
        mock_response = MagicMock(text=csv_text)
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            bars = StooqSource().fetch("SPY", date(2026, 1, 2), date(2026, 1, 5))

        self.assertEqual(
            [(bar.as_of, bar.close) for bar in bars],
            [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 101.5)],
        )

    def test_fetch_raises_when_response_is_not_csv(self) -> None:
        mock_response = MagicMock(text="<html>bot check</html>")
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            with self.assertRaises(DataSourceError):
                StooqSource().fetch("SPY", date(2026, 1, 1), date(2026, 1, 6))


class FallbackSourceTest(unittest.TestCase):
    def test_falls_back_to_next_source_on_failure(self) -> None:
        primary = MagicMock()
        primary.fetch.side_effect = DataSourceError("primary down")
        secondary = MagicMock()
        secondary.fetch.return_value = ["bar"]

        result = FallbackSource([primary, secondary]).fetch(
            "SPY", date(2026, 1, 1), date(2026, 1, 6))

        self.assertEqual(result, ["bar"])
        primary.fetch.assert_called_once()
        secondary.fetch.assert_called_once()

    def test_raises_with_combined_errors_when_all_sources_fail(self) -> None:
        primary = MagicMock()
        primary.fetch.side_effect = DataSourceError("primary down")
        secondary = MagicMock()
        secondary.fetch.side_effect = DataSourceError("secondary down")

        with self.assertRaises(DataSourceError) as ctx:
            FallbackSource([primary, secondary]).fetch(
                "SPY", date(2026, 1, 1), date(2026, 1, 6))

        self.assertIn("primary down", str(ctx.exception))
        self.assertIn("secondary down", str(ctx.exception))

    def test_requires_at_least_one_source(self) -> None:
        with self.assertRaises(ValueError):
            FallbackSource([])


class BuildEquityPanelTest(unittest.TestCase):
    def test_builds_panel_keyed_by_instrument_id(self) -> None:
        source = MagicMock()
        source.fetch.side_effect = lambda instrument_id, start, end: [instrument_id]

        panel = build_equity_panel(
            source, ["SPY", "QQQ"], date(2026, 1, 1), date(2026, 1, 6))

        self.assertEqual(panel.series, {"SPY": ["SPY"], "QQQ": ["QQQ"]})


if __name__ == "__main__":
    unittest.main()
