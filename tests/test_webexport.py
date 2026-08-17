import json
import math
import statistics
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from oqg_portfolio_sim.core import ledger
from oqg_portfolio_sim.webexport import compute_gaps_and_errors, compute_stats, export_site_data


@dataclass(frozen=True)
class _Fill:
    instrument_id: str
    qty: float
    fill_price: float
    explicit_cost: float
    implicit_cost: float
    fill_confidence: str
    reason: str


class ComputeStatsTest(unittest.TestCase):
    def test_empty_curve_returns_nones(self) -> None:
        stats = compute_stats([])
        self.assertEqual(stats["days_tracked"], 0)
        self.assertIsNone(stats["net_return_pct"])
        self.assertIsNone(stats["sharpe_like"])
        self.assertIsNone(stats["win_rate"])

    def test_hand_computed_example(self) -> None:
        equity_curve = [
            {"date": "2026-01-01", "gross_pnl": 100.0, "net_pnl": 90.0,
                "cum_net_pnl": 90.0, "capital_base": 10000.0, "margin_used": 0.0},
            {"date": "2026-01-02", "gross_pnl": -50.0, "net_pnl": -60.0,
                "cum_net_pnl": 30.0, "capital_base": 10000.0, "margin_used": 0.0},
            {"date": "2026-01-05", "gross_pnl": 20.0, "net_pnl": 15.0,
                "cum_net_pnl": 45.0, "capital_base": 10000.0, "margin_used": 0.0},
        ]

        stats = compute_stats(equity_curve)

        self.assertAlmostEqual(stats["net_return_pct"], 0.45)
        self.assertAlmostEqual(stats["max_drawdown_dollars"], -60.0)
        self.assertAlmostEqual(stats["max_drawdown_pct"], -0.6)
        self.assertAlmostEqual(stats["win_rate"], 2 / 3, places=4)
        self.assertEqual(stats["days_tracked"], 3)

        returns = [90.0 / 10000, -60.0 / 10000, 15.0 / 10000]
        expected_sharpe = (statistics.fmean(returns) /
                            statistics.pstdev(returns)) * math.sqrt(252)
        self.assertAlmostEqual(stats["sharpe_like"], expected_sharpe, places=4)

    def test_zero_capital_base_does_not_divide_by_zero(self) -> None:
        equity_curve = [
            {"date": "2026-01-01", "gross_pnl": 0.0, "net_pnl": 0.0,
                "cum_net_pnl": 0.0, "capital_base": 0.0, "margin_used": 0.0},
        ]
        stats = compute_stats(equity_curve)
        self.assertIsNone(stats["net_return_pct"])
        self.assertIsNone(stats["max_drawdown_pct"])


class ComputeGapsAndErrorsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ledger.sqlite"
        ledger.bootstrap_schema(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, as_of: str, status: str, reconciliation_status: str) -> None:
        run_id = ledger.start_run(self.db_path, as_of_date=as_of)
        ledger.finish_run(self.db_path, run_id, status=status,
                           reconciliation_status=reconciliation_status)

    def test_detects_a_missed_session_and_an_errored_one(self) -> None:
        # 2026-01-02 (Fri) ok, 2026-01-05 (Mon) missing entirely,
        # 2026-01-06 (Tue) errored, 2026-01-07 (Wed) ok.
        self._run("2026-01-02", "completed", "ok")
        self._run("2026-01-06", "error", "error")
        self._run("2026-01-07", "completed", "ok")

        gaps, errors = compute_gaps_and_errors(self.db_path)

        self.assertEqual(gaps, ["2026-01-05"])
        self.assertEqual(errors, ["2026-01-06"])

    def test_no_completed_runs_yields_no_gaps(self) -> None:
        gaps, errors = compute_gaps_and_errors(self.db_path)
        self.assertEqual(gaps, [])
        self.assertEqual(errors, [])


class ExportSiteDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ledger.sqlite"
        self.out_dir = Path(self._tmp.name) / "site"
        ledger.bootstrap_schema(self.db_path)
        ledger.register_strategy(
            self.db_path, strategy_id="s1", name="Test Strategy",
            description="desc", owner="club", asset_class="futures",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_only_completed_reconciled_runs_are_exported(self) -> None:
        good_run = ledger.start_run(self.db_path, as_of_date="2026-01-02")
        ledger.insert_fills(self.db_path, good_run, "s1", [
            _Fill("VX2", -1.0, 20.0, 2.58, 50.0, "normal", "entry"),
        ])
        ledger.insert_positions(self.db_path, good_run, "s1", {"VX2": -1.0})
        ledger.insert_equity(
            self.db_path, good_run, "s1", gross_pnl=0.0, net_pnl=-52.58,
            cum_net_pnl=-52.58, capital_base=100000.0, margin_used=2170.0,
        )
        ledger.finish_run(self.db_path, good_run,
                           status="completed", reconciliation_status="ok")

        bad_run = ledger.start_run(self.db_path, as_of_date="2026-01-05")
        ledger.insert_fills(self.db_path, bad_run, "s1", [
            _Fill("VX2", 5.0, 21.0, 2.58, 50.0, "normal", "should not appear"),
        ])
        ledger.insert_equity(
            self.db_path, bad_run, "s1", gross_pnl=0.0, net_pnl=9999.0,
            cum_net_pnl=9999.0, capital_base=100000.0, margin_used=0.0,
        )
        ledger.finish_run(self.db_path, bad_run, status="completed",
                           reconciliation_status="error", reconciliation_note="mismatch")

        export_site_data(self.db_path, self.out_dir)

        strategy_doc = json.loads((self.out_dir / "s1.json").read_text())
        self.assertEqual(len(strategy_doc["equity_curve"]), 1)
        self.assertEqual(strategy_doc["equity_curve"]
                          [0]["date"], "2026-01-02")
        self.assertEqual(len(strategy_doc["trades"]), 1)
        self.assertEqual(strategy_doc["trades"][0]["reason"], "entry")

        index_doc = json.loads((self.out_dir / "index.json").read_text())
        self.assertEqual(len(index_doc["strategies"]), 1)
        self.assertAlmostEqual(
            index_doc["strategies"][0]["cum_net_pnl"], -52.58)

    def test_trades_are_newest_first(self) -> None:
        run1 = ledger.start_run(self.db_path, as_of_date="2026-01-02")
        ledger.insert_fills(self.db_path, run1, "s1", [
            _Fill("VX2", -1.0, 20.0, 2.58, 50.0, "normal", "first"),
        ])
        ledger.insert_equity(
            self.db_path, run1, "s1", gross_pnl=0.0, net_pnl=-52.58,
            cum_net_pnl=-52.58, capital_base=100000.0, margin_used=0.0,
        )
        ledger.finish_run(self.db_path, run1, status="completed",
                           reconciliation_status="ok")

        run2 = ledger.start_run(self.db_path, as_of_date="2026-01-05")
        ledger.insert_fills(self.db_path, run2, "s1", [
            _Fill("VX2", 1.0, 21.0, 2.58, 50.0, "normal", "second"),
        ])
        ledger.insert_equity(
            self.db_path, run2, "s1", gross_pnl=0.0, net_pnl=-52.58,
            cum_net_pnl=-105.16, capital_base=100000.0, margin_used=0.0,
        )
        ledger.finish_run(self.db_path, run2, status="completed",
                           reconciliation_status="ok")

        export_site_data(self.db_path, self.out_dir)

        strategy_doc = json.loads((self.out_dir / "s1.json").read_text())
        reasons = [t["reason"] for t in strategy_doc["trades"]]
        self.assertEqual(reasons, ["second", "first"])

    def test_gaps_and_errors_present_in_strategy_doc(self) -> None:
        run1 = ledger.start_run(self.db_path, as_of_date="2026-01-02")
        ledger.insert_equity(
            self.db_path, run1, "s1", gross_pnl=0.0, net_pnl=0.0,
            cum_net_pnl=0.0, capital_base=100000.0, margin_used=0.0,
        )
        ledger.finish_run(self.db_path, run1, status="completed",
                           reconciliation_status="ok")

        run2 = ledger.start_run(self.db_path, as_of_date="2026-01-07")
        ledger.insert_equity(
            self.db_path, run2, "s1", gross_pnl=0.0, net_pnl=0.0,
            cum_net_pnl=0.0, capital_base=100000.0, margin_used=0.0,
        )
        ledger.finish_run(self.db_path, run2, status="completed",
                           reconciliation_status="ok")

        export_site_data(self.db_path, self.out_dir)

        strategy_doc = json.loads((self.out_dir / "s1.json").read_text())
        self.assertIn("2026-01-05", strategy_doc["gaps"])
        self.assertIn("2026-01-05", json.loads(
            (self.out_dir / "index.json").read_text())["gaps"])


if __name__ == "__main__":
    unittest.main()
