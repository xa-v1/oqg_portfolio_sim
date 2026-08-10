import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from oqg_portfolio_sim.core import ledger
from oqg_portfolio_sim.runner import RunnerError, default_registry, run_daily_cycle
from oqg_portfolio_sim.runner.registry import StrategyRegistration
from oqg_portfolio_sim.strategies.contracts import PricePanel, TargetPosition


class _FailingStrategy:
    def required_instruments(self) -> list:
        return []

    def generate_targets(self, as_of: date, history: PricePanel) -> list[TargetPosition]:
        raise ValueError("boom")


class _FlatStrategy:
    def required_instruments(self) -> list:
        return []

    def generate_targets(self, as_of: date, history: PricePanel) -> list[TargetPosition]:
        return []


def _registration(strategy_id: str, strategy) -> StrategyRegistration:
    return StrategyRegistration(
        strategy_id=strategy_id,
        name=strategy_id,
        description="",
        owner="test",
        asset_class="futures",
        strategy=strategy,
        capital_base=100_000.0,
        margin_fn=lambda positions: 0.0,
    )


class DailyCycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ledger.sqlite"
        ledger.bootstrap_schema(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _runs(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT as_of_date, status, reconciliation_status FROM runs ORDER BY run_id"
            ).fetchall()

    def test_skips_non_trading_day_without_writing_a_run_row(self) -> None:
        outcome = run_daily_cycle(
            self.db_path, [], today=date(2026, 7, 18))  # Saturday

        self.assertTrue(outcome.skipped)
        self.assertIn("not a", outcome.reason)
        self.assertEqual(self._runs(), [])

    def test_real_default_registry_completes_on_a_real_historical_date(self) -> None:
        # 2026-07-20 is covered by the committed parquet -- no network call needed.
        outcome = run_daily_cycle(
            self.db_path, default_registry(), today=date(2026, 7, 20))

        self.assertFalse(outcome.skipped)
        self.assertEqual(outcome.fill_date, date(2026, 7, 20))
        self.assertEqual(outcome.signal_date, date(2026, 7, 17))
        self.assertEqual(self._runs(), [
                          ("2026-07-20", "completed", "ok")])

    def test_is_idempotent_on_repeated_invocation(self) -> None:
        registry = [_registration("flat", _FlatStrategy())]
        run_daily_cycle(self.db_path, registry, today=date(2026, 7, 20))

        outcome2 = run_daily_cycle(
            self.db_path, registry, today=date(2026, 7, 20))

        self.assertTrue(outcome2.skipped)
        self.assertIn("already completed", outcome2.reason)
        self.assertEqual(len(self._runs()), 1)

    def test_strategy_failure_records_error_run_and_raises(self) -> None:
        registry = [_registration("boom", _FailingStrategy())]

        with self.assertRaises(RunnerError):
            run_daily_cycle(self.db_path, registry, today=date(2026, 7, 20))

        self.assertEqual(self._runs(), [("2026-07-20", "error", "error")])

    def test_failed_date_can_be_retried_after_fixing_the_registry(self) -> None:
        failing_registry = [_registration("s1", _FailingStrategy())]
        with self.assertRaises(RunnerError):
            run_daily_cycle(self.db_path, failing_registry,
                             today=date(2026, 7, 20))

        working_registry = [_registration("s1", _FlatStrategy())]
        outcome = run_daily_cycle(
            self.db_path, working_registry, today=date(2026, 7, 20))

        self.assertFalse(outcome.skipped)
        statuses = [row[1] for row in self._runs()]
        self.assertEqual(statuses, ["error", "completed"])

    def test_halts_and_skips_later_strategies_after_one_fails(self) -> None:
        registry = [
            _registration("first_fails", _FailingStrategy()),
            _registration("second_never_runs", _FlatStrategy()),
        ]

        with self.assertRaises(RunnerError):
            run_daily_cycle(self.db_path, registry, today=date(2026, 7, 20))

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM strategies WHERE strategy_id = 'second_never_runs'"
            ).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
