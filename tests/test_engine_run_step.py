import tempfile
import unittest
from pathlib import Path

from oqg_portfolio_sim.core import ledger
from oqg_portfolio_sim.core.models import InstrumentSpec
from oqg_portfolio_sim.engine import Fill, calendar_butterfly_margin, process_step
from oqg_portfolio_sim.strategies.contracts import TargetPosition

SPREAD_MARGINS = {("VX2", "VX3"): 2170.0, ("VX3", "VX4"): 1220.0}
SPECS = {
    "VX2": InstrumentSpec("VX2", "t", "futures", 1000, 0.01, explicit_fee=2.58, spread=0.10),
    "VX3": InstrumentSpec("VX3", "t", "futures", 1000, 0.01, explicit_fee=2.58, spread=0.10),
    "VX4": InstrumentSpec("VX4", "t", "futures", 1000, 0.01, explicit_fee=2.58, spread=0.10, liquidity="illiquid"),
}


def _margin_fn(positions: dict[str, float]) -> float:
    return calendar_butterfly_margin(positions, "VX2", "VX3", "VX4", SPREAD_MARGINS)


class ProcessStepTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ledger.sqlite"
        ledger.bootstrap_schema(self.db_path)
        ledger.register_strategy(
            self.db_path, strategy_id="s1", name="Test Butterfly", asset_class="futures")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, as_of: str, targets, fill_prices, prior_marks, capital_base=100_000.0):
        run_id = ledger.start_run(self.db_path, as_of_date=as_of)
        result = process_step(
            self.db_path, run_id, "s1", targets, fill_prices, prior_marks,
            SPECS, capital_base, _margin_fn,
        )
        ledger.finish_run(
            self.db_path, run_id, status="completed",
            reconciliation_status="ok" if result.reconciled else "error",
        )
        return run_id, result

    def test_entry_hold_exit_lifecycle(self) -> None:
        entry_targets = [
            TargetPosition("VX2", -1, "entry"),
            TargetPosition("VX3", 2, "entry"),
            TargetPosition("VX4", -1, "entry"),
        ]

        _run1, r1 = self._run(
            "2026-01-02", entry_targets,
            fill_prices={"VX2": 25.0, "VX3": 24.0, "VX4": 23.0},
            prior_marks={},
        )
        self.assertEqual(len(r1.fills), 3)
        self.assertEqual(r1.positions, {"VX2": -1, "VX3": 2, "VX4": -1})
        self.assertEqual(r1.gross_pnl, 0.0)  # nothing held coming into today
        self.assertAlmostEqual(r1.margin_used, 3390.0)
        self.assertTrue(r1.reconciled)
        entry_cost = sum(f.explicit_cost + f.implicit_cost for f in r1.fills)
        self.assertAlmostEqual(r1.net_pnl, -entry_cost)

        _run2, r2 = self._run(
            "2026-01-05", entry_targets,  # unchanged -> hold
            fill_prices={"VX2": 26.0, "VX3": 25.0, "VX4": 24.0},
            prior_marks={"VX2": 25.0, "VX3": 24.0, "VX4": 23.0},
        )
        self.assertEqual(r2.fills, [])
        # -1*(26-25) + 2*(25-24) + -1*(24-23), all *1000 = -1000+2000-1000 = 0
        self.assertAlmostEqual(r2.gross_pnl, 0.0)
        self.assertAlmostEqual(r2.net_pnl, 0.0)
        self.assertAlmostEqual(r2.cum_net_pnl, r1.net_pnl)
        self.assertAlmostEqual(r2.margin_used, 3390.0)

        _run3, r3 = self._run(
            "2026-01-06", [],  # exit
            fill_prices={"VX2": 27.0, "VX3": 26.0, "VX4": 25.0},
            prior_marks={"VX2": 26.0, "VX3": 25.0, "VX4": 24.0},
        )
        self.assertEqual(len(r3.fills), 3)
        self.assertTrue(
            all(f.reason == "target flat (exit)" for f in r3.fills))
        self.assertEqual(r3.positions, {"VX2": 0.0, "VX3": 0.0, "VX4": 0.0})
        self.assertAlmostEqual(r3.margin_used, 0.0)
        exit_cost = sum(f.explicit_cost + f.implicit_cost for f in r3.fills)
        self.assertAlmostEqual(r3.net_pnl, r3.gross_pnl - exit_cost)
        self.assertAlmostEqual(r3.cum_net_pnl, r1.net_pnl + r2.net_pnl + r3.net_pnl)
        self.assertTrue(r3.reconciled)

        # Ledger must not resurrect the closed-out position for future runs.
        self.assertEqual(ledger.get_current_positions(self.db_path, "s1"), {})

    def test_margin_exceeded_flag(self) -> None:
        entry_targets = [
            TargetPosition("VX2", -1, "entry"),
            TargetPosition("VX3", 2, "entry"),
            TargetPosition("VX4", -1, "entry"),
        ]
        _run_id, result = self._run(
            "2026-01-02", entry_targets,
            fill_prices={"VX2": 25.0, "VX3": 24.0, "VX4": 23.0},
            prior_marks={},
            capital_base=1000.0,  # margin ($3,390) exceeds this
        )
        self.assertTrue(result.margin_exceeded)

    def test_reconciliation_fails_on_corrupted_ledger_state(self) -> None:
        # Inject a fill that simulate_step never produced, bypassing the
        # normal write path -- this should make recompute-from-fills
        # disagree with the position snapshot process_step writes next.
        phantom_run = ledger.start_run(self.db_path, as_of_date="2026-01-01")
        ledger.finish_run(self.db_path, phantom_run,
                           status="completed", reconciliation_status="ok")
        ledger.insert_fills(self.db_path, phantom_run, "s1", [
            Fill("VX2", 5.0, 20.0, 0.0, 0.0, "normal", "injected"),
        ])

        _run_id, result = self._run(
            "2026-01-02",
            [TargetPosition("VX2", -1, "entry")],
            fill_prices={"VX2": 25.0},
            prior_marks={},
        )

        self.assertFalse(result.reconciled)
        self.assertIn("VX2", result.reconciliation_note)


if __name__ == "__main__":
    unittest.main()
