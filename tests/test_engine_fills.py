import unittest

from oqg_portfolio_sim.core.models import InstrumentSpec
from oqg_portfolio_sim.engine.fills import compute_costs, simulate_fill, simulate_step
from oqg_portfolio_sim.strategies.contracts import TargetPosition

VX_SPEC = InstrumentSpec(
    instrument_id="VX2", name="test", asset_class="futures",
    multiplier=1000, tick_size=0.01, explicit_fee=2.58, spread=0.10,
    liquidity="normal",
)
ILLIQUID_SPEC = InstrumentSpec(
    instrument_id="VX4", name="test", asset_class="futures",
    multiplier=1000, tick_size=0.01, explicit_fee=2.58, spread=0.10,
    liquidity="illiquid",
)


class ComputeCostsTest(unittest.TestCase):
    def test_matches_notebook_cost_formula(self) -> None:
        # notebook: explicit=(1.58+1.0)=2.58/contract, implicit=half-spread*qty*mult
        explicit, implicit = compute_costs(4.0, VX_SPEC)
        self.assertAlmostEqual(explicit, 2.58 * 4)
        self.assertAlmostEqual(implicit, 0.5 * 0.10 * 4 * 1000)

    def test_uses_absolute_quantity(self) -> None:
        explicit_pos, implicit_pos = compute_costs(3.0, VX_SPEC)
        explicit_neg, implicit_neg = compute_costs(-3.0, VX_SPEC)
        self.assertEqual(explicit_pos, explicit_neg)
        self.assertEqual(implicit_pos, implicit_neg)


class SimulateFillTest(unittest.TestCase):
    def test_returns_none_when_no_change_needed(self) -> None:
        fill = simulate_fill("VX2", current_qty=1.0,
                              target_qty=1.0, fill_price=20.0, spec=VX_SPEC)
        self.assertIsNone(fill)

    def test_qty_is_the_delta_not_the_target(self) -> None:
        fill = simulate_fill("VX2", current_qty=-1.0,
                              target_qty=2.0, fill_price=20.0, spec=VX_SPEC)
        self.assertEqual(fill.qty, 3.0)

    def test_normal_liquidity_gets_normal_confidence(self) -> None:
        fill = simulate_fill("VX2", 0.0, 1.0, 20.0, VX_SPEC)
        self.assertEqual(fill.fill_confidence, "normal")

    def test_illiquid_instrument_gets_low_fill_confidence(self) -> None:
        fill = simulate_fill("VX4", 0.0, -1.0, 20.0, ILLIQUID_SPEC)
        self.assertEqual(fill.fill_confidence, "low")


class SimulateStepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.specs = {"VX2": VX_SPEC, "VX3": InstrumentSpec(
            "VX3", "t", "futures", 1000, 0.01, 2.58, 0.10, "normal"
        ), "VX4": ILLIQUID_SPEC}

    def test_entering_a_flat_book_produces_one_fill_per_leg(self) -> None:
        targets = [
            TargetPosition("VX2", -1, reason="entry"),
            TargetPosition("VX3", 2, reason="entry"),
            TargetPosition("VX4", -1, reason="entry"),
        ]
        fill_prices = {"VX2": 25.0, "VX3": 24.0, "VX4": 23.0}

        fills, positions = simulate_step(targets, {}, fill_prices, self.specs)

        self.assertEqual(len(fills), 3)
        self.assertEqual(positions, {"VX2": -1, "VX3": 2, "VX4": -1})
        vx4_fill = next(f for f in fills if f.instrument_id == "VX4")
        self.assertEqual(vx4_fill.fill_confidence, "low")

    def test_empty_targets_closes_out_current_holdings(self) -> None:
        current = {"VX2": -1.0, "VX3": 2.0, "VX4": -1.0}
        fill_prices = {"VX2": 25.0, "VX3": 24.0, "VX4": 23.0}

        fills, positions = simulate_step([], current, fill_prices, self.specs)

        self.assertEqual(len(fills), 3)
        self.assertEqual(positions, {"VX2": 0.0, "VX3": 0.0, "VX4": 0.0})
        self.assertTrue(all(f.reason == "target flat (exit)" for f in fills))

    def test_unchanged_instruments_produce_no_fill(self) -> None:
        current = {"VX2": -1.0}
        targets = [TargetPosition("VX2", -1.0)]

        fills, positions = simulate_step(
            targets, current, {"VX2": 25.0}, self.specs)

        self.assertEqual(fills, [])
        self.assertEqual(positions, {"VX2": -1.0})

    def test_raises_when_fill_price_missing_for_a_required_trade(self) -> None:
        targets = [TargetPosition("VX2", 1.0)]
        with self.assertRaises(KeyError):
            simulate_step(targets, {}, {}, self.specs)

    def test_raises_when_spec_missing_for_a_required_trade(self) -> None:
        targets = [TargetPosition("UNKNOWN", 1.0)]
        with self.assertRaises(KeyError):
            simulate_step(targets, {}, {"UNKNOWN": 10.0}, self.specs)


if __name__ == "__main__":
    unittest.main()
