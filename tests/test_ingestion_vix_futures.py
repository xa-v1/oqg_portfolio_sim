import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from oqg_portfolio_sim.ingestion.base import DataSourceError
from oqg_portfolio_sim.ingestion.vix_futures import (
    CboeSettlementSource,
    load_vix_futures_parquet,
    merge_settlement_updates,
    rank_panel,
    refresh_history,
)


def _contract_row(trade_date, contract, settle, settlement_date, days_to_settlement):
    return {
        "trade_date": trade_date,
        "contract": contract,
        "settle": settle,
        "settlement_date": settlement_date,
        "days_to_settlement": days_to_settlement,
    }


class LoadVixFuturesParquetTest(unittest.TestCase):
    def test_loads_real_committed_history(self) -> None:
        df = load_vix_futures_parquet()

        self.assertGreater(len(df), 0)
        self.assertEqual(
            list(df.columns),
            ["trade_date", "contract", "settle",
                "settlement_date", "days_to_settlement"],
        )
        # Sorted by trade_date then days_to_settlement (nearest expiry first).
        self.assertTrue(
            df.groupby("trade_date")["days_to_settlement"]
            .apply(lambda s: s.is_monotonic_increasing)
            .all()
        )

    def test_raises_when_file_missing(self) -> None:
        with self.assertRaises(DataSourceError):
            load_vix_futures_parquet("/nonexistent/path.parquet")


class CboeSettlementSourceTest(unittest.TestCase):
    def test_fetch_settlement_keeps_only_monthly_vx_contracts(self) -> None:
        csv_text = (
            "Product,Symbol,Expiration Date,Price\n"
            "VX,VX32/Q6,2026-08-12,16.99\n"  # weekly -> dropped
            "VX,VX/Q6,2026-08-19,16.99\n"    # monthly -> kept
            "VX,VX/U6,2026-09-16,18.66\n"    # monthly -> kept
            "VXM,VXM/Q6,2026-08-19,16.99\n"  # different product -> dropped
        )
        mock_response = MagicMock(text=csv_text)
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            df = CboeSettlementSource().fetch_settlement(date(2026, 8, 7))

        self.assertEqual(sorted(df["contract"]), ["VX/Q6", "VX/U6"])
        row = df[df["contract"] == "VX/Q6"].iloc[0]
        self.assertEqual(row["settlement_date"], date(2026, 8, 19))
        self.assertEqual(row["days_to_settlement"], 12)

    def test_raises_when_response_is_not_csv(self) -> None:
        mock_response = MagicMock(text="<html>blocked</html>")
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            with self.assertRaises(DataSourceError):
                CboeSettlementSource().fetch_settlement(date(2026, 8, 7))

    def test_raises_when_no_monthly_rows_present(self) -> None:
        csv_text = "Product,Symbol,Expiration Date,Price\nVX,VX32/Q6,2026-08-12,16.99\n"
        mock_response = MagicMock(text=csv_text)
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            with self.assertRaises(DataSourceError):
                CboeSettlementSource().fetch_settlement(date(2026, 8, 7))


class RefreshHistoryTest(unittest.TestCase):
    def test_raises_without_any_network_call_when_gap_exceeds_cap(self) -> None:
        # last date 2026-01-01 (Thursday), through far in the future -> a
        # huge gap. This must fail fast on the cap check, not attempt
        # hundreds of sequential fetch_settlement calls.
        history = pd.DataFrame(
            [_contract_row(date(2026, 1, 1), "VX/F6",
                            15.0, date(2026, 1, 21), 20)]
        )
        source = MagicMock()

        with self.assertRaises(DataSourceError):
            refresh_history(history, date(2030, 1, 1),
                             source=source, max_backfill_sessions=5)

        source.fetch_settlement.assert_not_called()

    def test_merges_a_small_number_of_missing_sessions(self) -> None:
        history = pd.DataFrame(
            [_contract_row(date(2026, 1, 1), "VX/F6",
                            15.0, date(2026, 1, 21), 20)]
        )
        source = MagicMock()
        source.fetch_settlement.return_value = pd.DataFrame(
            [_contract_row(date(2026, 1, 2), "VX/F6",
                            15.2, date(2026, 1, 21), 19)]
        )

        updated, warnings = refresh_history(
            history, date(2026, 1, 2), source=source, max_backfill_sessions=5)

        self.assertEqual(warnings, [])
        self.assertIn(date(2026, 1, 2), set(updated["trade_date"]))

    def test_failure_on_an_older_missing_session_is_a_warning_not_a_raise(self) -> None:
        history = pd.DataFrame(
            [_contract_row(date(2026, 1, 1), "VX/F6",
                            15.0, date(2026, 1, 21), 20)]
        )
        # 2026-01-02 fails, 2026-01-05 (the `through` date) succeeds.
        source = MagicMock()

        def fetch(trade_date):
            if trade_date == date(2026, 1, 2):
                raise DataSourceError("transient outage")
            return pd.DataFrame(
                [_contract_row(trade_date, "VX/F6", 15.5,
                                date(2026, 1, 21), 16)]
            )
        source.fetch_settlement.side_effect = fetch

        updated, warnings = refresh_history(
            history, date(2026, 1, 5), source=source, max_backfill_sessions=5)

        self.assertEqual(len(warnings), 1)
        self.assertIn("2026-01-02", warnings[0])
        self.assertIn(date(2026, 1, 5), set(updated["trade_date"]))
        self.assertNotIn(date(2026, 1, 2), set(updated["trade_date"]))

    def test_failure_on_through_date_itself_raises(self) -> None:
        history = pd.DataFrame(
            [_contract_row(date(2026, 1, 1), "VX/F6",
                            15.0, date(2026, 1, 21), 20)]
        )
        source = MagicMock()
        source.fetch_settlement.side_effect = DataSourceError("down")

        with self.assertRaises(DataSourceError):
            refresh_history(history, date(2026, 1, 2),
                             source=source, max_backfill_sessions=5)


class MergeSettlementUpdatesTest(unittest.TestCase):
    def test_merge_appends_new_day(self) -> None:
        history = pd.DataFrame(
            [_contract_row(date(2026, 8, 6), "VX/Q6", 17.0, date(2026, 8, 19), 13)])
        updates = pd.DataFrame(
            [_contract_row(date(2026, 8, 7), "VX/Q6", 16.99, date(2026, 8, 19), 12)])

        merged = merge_settlement_updates(history, updates)

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            sorted(merged["trade_date"]), [date(2026, 8, 6), date(2026, 8, 7)])

    def test_merge_is_idempotent_on_repeated_day(self) -> None:
        history = pd.DataFrame(
            [_contract_row(date(2026, 8, 7), "VX/Q6", 16.99, date(2026, 8, 19), 12)])
        updates = pd.DataFrame(
            [_contract_row(date(2026, 8, 7), "VX/Q6", 16.99, date(2026, 8, 19), 12)])

        merged_once = merge_settlement_updates(history, updates)
        merged_twice = merge_settlement_updates(merged_once, updates)

        self.assertEqual(len(merged_once), 1)
        self.assertEqual(len(merged_twice), 1)


class RankPanelTest(unittest.TestCase):
    def test_ranks_contracts_by_days_to_settlement_each_day(self) -> None:
        history = pd.DataFrame(
            [
                _contract_row(date(2026, 1, 1), "A",
                              10.0, date(2026, 1, 31), 30),
                _contract_row(date(2026, 1, 1), "B",
                              11.0, date(2026, 3, 2), 60),
                _contract_row(date(2026, 1, 1), "C",
                              12.0, date(2026, 4, 1), 90),
            ]
        )

        panel = rank_panel(history, n_ranks=3)

        self.assertEqual(panel.series["VX1"][0].close, 10.0)
        self.assertEqual(panel.series["VX2"][0].close, 11.0)
        self.assertEqual(panel.series["VX3"][0].close, 12.0)

    def test_rank_series_rolls_across_contract_expiry(self) -> None:
        # Day 1: A is nearest (rank 1). Day 2: A has expired/dropped off the
        # feed, so B -- previously rank 2 -- becomes rank 1. This is the
        # contract roll a strategy should see as one continuous VX1 series.
        history = pd.DataFrame(
            [
                _contract_row(date(2026, 1, 1), "A",
                              10.0, date(2026, 1, 2), 1),
                _contract_row(date(2026, 1, 1), "B",
                              11.0, date(2026, 2, 1), 31),
                _contract_row(date(2026, 1, 2), "B",
                              11.5, date(2026, 2, 1), 30),
                _contract_row(date(2026, 1, 2), "C",
                              12.5, date(2026, 3, 3), 60),
            ]
        )

        panel = rank_panel(history, n_ranks=2)

        vx1_closes = [bar.close for bar in panel.series["VX1"]]
        self.assertEqual(vx1_closes, [10.0, 11.5])


if __name__ == "__main__":
    unittest.main()
