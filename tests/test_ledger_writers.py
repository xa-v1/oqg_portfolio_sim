import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from oqg_portfolio_sim.core import ledger


@dataclass(frozen=True)
class _Fill:
    instrument_id: str
    qty: float
    fill_price: float
    explicit_cost: float
    implicit_cost: float
    fill_confidence: str
    reason: str


class LedgerWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ledger.sqlite"
        ledger.bootstrap_schema(self.db_path)
        ledger.register_strategy(
            self.db_path, strategy_id="s1", name="Strategy One", asset_class="futures")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _completed_run(self, as_of: str) -> int:
        run_id = ledger.start_run(self.db_path, as_of_date=as_of)
        ledger.finish_run(self.db_path, run_id, status="completed",
                           reconciliation_status="ok")
        return run_id

    def test_register_strategy_is_idempotent(self) -> None:
        ledger.register_strategy(
            self.db_path, strategy_id="s1", name="Strategy One (dup)", asset_class="futures")
        # Should not raise, and should not overwrite the original row.

    def test_insert_and_fetch_fills(self) -> None:
        run_id = self._completed_run("2026-01-02")
        fills = [
            _Fill("VX2", -1.0, 25.0, 2.58, 50.0, "normal", "entry"),
            _Fill("VX3", 2.0, 24.0, 5.16, 100.0, "normal", "entry"),
        ]
        ledger.insert_fills(self.db_path, run_id, "s1", fills)

        stored = ledger.get_all_fills(self.db_path, "s1")
        self.assertEqual(sorted(stored), [("VX2", -1.0), ("VX3", 2.0)])

    def test_insert_fills_noop_on_empty_list(self) -> None:
        run_id = self._completed_run("2026-01-02")
        ledger.insert_fills(self.db_path, run_id, "s1", [])
        self.assertEqual(ledger.get_all_fills(self.db_path, "s1"), [])

    def test_insert_positions_only_stores_nonzero(self) -> None:
        run_id = self._completed_run("2026-01-02")
        ledger.insert_positions(
            self.db_path, run_id, "s1", {"VX2": -1.0, "VX3": 0.0, "VX4": 2.0})

        stored = ledger.get_positions_for_run(self.db_path, run_id, "s1")
        self.assertEqual(stored, {"VX2": -1.0, "VX4": 2.0})

    def test_get_current_positions_empty_before_any_completed_run(self) -> None:
        self.assertEqual(ledger.get_current_positions(self.db_path, "s1"), {})

    def test_get_current_positions_returns_latest_completed_snapshot(self) -> None:
        run1 = self._completed_run("2026-01-02")
        ledger.insert_positions(self.db_path, run1, "s1", {"VX2": -1.0})

        run2 = self._completed_run("2026-01-05")
        ledger.insert_positions(self.db_path, run2, "s1", {"VX2": -2.0})

        self.assertEqual(ledger.get_current_positions(
            self.db_path, "s1"), {"VX2": -2.0})

    def test_get_current_positions_does_not_resurrect_stale_positions_after_going_flat(self) -> None:
        # Regression guard: a strategy that closes out entirely writes NO
        # position rows for that run (flat = absence, not a zero row). The
        # naive "most recent run with any rows for this strategy" query
        # would then incorrectly fall back to the earlier, stale position.
        run1 = self._completed_run("2026-01-02")
        ledger.insert_positions(self.db_path, run1, "s1", {"VX2": -1.0})

        run2 = self._completed_run("2026-01-05")
        ledger.insert_positions(self.db_path, run2, "s1", {})  # went flat

        self.assertEqual(ledger.get_current_positions(self.db_path, "s1"), {})

    def test_get_current_positions_ignores_non_completed_runs(self) -> None:
        run1 = self._completed_run("2026-01-02")
        ledger.insert_positions(self.db_path, run1, "s1", {"VX2": -1.0})

        pending_run = ledger.start_run(self.db_path, as_of_date="2026-01-05")
        ledger.insert_positions(
            self.db_path, pending_run, "s1", {"VX2": -99.0})
        # Deliberately not finishing pending_run.

        self.assertEqual(ledger.get_current_positions(
            self.db_path, "s1"), {"VX2": -1.0})

    def test_insert_equity_and_get_cumulative_net_pnl(self) -> None:
        run1 = self._completed_run("2026-01-02")
        ledger.insert_equity(
            self.db_path, run1, "s1",
            gross_pnl=100.0, net_pnl=90.0, cum_net_pnl=90.0,
            capital_base=100000.0, margin_used=3390.0,
        )
        run2 = self._completed_run("2026-01-05")
        ledger.insert_equity(
            self.db_path, run2, "s1",
            gross_pnl=-20.0, net_pnl=-25.0, cum_net_pnl=65.0,
            capital_base=100000.0, margin_used=3390.0,
        )

        self.assertEqual(ledger.get_cumulative_net_pnl(
            self.db_path, "s1"), 65.0)

    def test_get_cumulative_net_pnl_is_zero_before_any_equity_row(self) -> None:
        self.assertEqual(ledger.get_cumulative_net_pnl(
            self.db_path, "s1"), 0.0)


if __name__ == "__main__":
    unittest.main()
