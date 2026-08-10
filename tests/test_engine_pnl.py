import unittest

from oqg_portfolio_sim.core.models import InstrumentSpec
from oqg_portfolio_sim.engine.fills import Fill
from oqg_portfolio_sim.engine.pnl import gross_pnl, net_pnl

SPEC = InstrumentSpec("VX2", "t", "futures", 1000, 0.01)


class GrossPnlTest(unittest.TestCase):
    def test_marks_held_position_at_price_change_times_multiplier(self) -> None:
        pnl = gross_pnl(
            prior_positions={"VX2": 2.0},
            prior_marks={"VX2": 20.0},
            today_marks={"VX2": 20.5},
            specs={"VX2": SPEC},
        )
        self.assertAlmostEqual(pnl, 2.0 * 0.5 * 1000)

    def test_short_position_profits_when_price_falls(self) -> None:
        pnl = gross_pnl(
            prior_positions={"VX2": -1.0},
            prior_marks={"VX2": 20.0},
            today_marks={"VX2": 19.0},
            specs={"VX2": SPEC},
        )
        self.assertAlmostEqual(pnl, -1.0 * -1.0 * 1000)  # positive

    def test_zero_qty_positions_are_skipped(self) -> None:
        pnl = gross_pnl(
            prior_positions={"VX2": 0.0},
            prior_marks={},
            today_marks={},
            specs={"VX2": SPEC},
        )
        self.assertEqual(pnl, 0.0)

    def test_raises_when_mark_missing_for_held_position(self) -> None:
        with self.assertRaises(KeyError):
            gross_pnl(
                prior_positions={"VX2": 1.0},
                prior_marks={},
                today_marks={"VX2": 20.0},
                specs={"VX2": SPEC},
            )


class NetPnlTest(unittest.TestCase):
    def test_subtracts_explicit_and_implicit_costs(self) -> None:
        fills = [
            Fill("VX2", 1.0, 20.0, explicit_cost=2.58,
                 implicit_cost=50.0, fill_confidence="normal", reason=""),
            Fill("VX3", -2.0, 21.0, explicit_cost=5.16,
                 implicit_cost=100.0, fill_confidence="normal", reason=""),
        ]
        net = net_pnl(gross=1000.0, fills=fills)
        self.assertAlmostEqual(net, 1000.0 - (2.58 + 50.0 + 5.16 + 100.0))

    def test_no_fills_leaves_gross_unchanged(self) -> None:
        self.assertEqual(net_pnl(gross=42.0, fills=[]), 42.0)


if __name__ == "__main__":
    unittest.main()
