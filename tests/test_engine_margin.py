import unittest

from oqg_portfolio_sim.core.models import InstrumentSpec
from oqg_portfolio_sim.engine.margin import (
    calendar_butterfly_margin,
    flat_margin_used,
    margin_exceeded,
)

SPREAD_MARGINS = {("VX2", "VX3"): 2170.0, ("VX3", "VX4"): 1220.0}


class CalendarButterflyMarginTest(unittest.TestCase):
    def test_matches_notebook_plain_margin(self) -> None:
        # PLAIN [-1, +2, -1]: q1=1 (VX2-VX3 spread), q2=1 (VX3-VX4 spread) -> $3,390
        positions = {"VX2": -1.0, "VX3": 2.0, "VX4": -1.0}
        margin = calendar_butterfly_margin(
            positions, "VX2", "VX3", "VX4", SPREAD_MARGINS)
        self.assertAlmostEqual(margin, 3390.0)

    def test_matches_notebook_hedged_margin(self) -> None:
        # HEDGED [-1, +3, -2]: q1=1, q2=2 -> 2170 + 2*1220 = $4,610
        positions = {"VX2": -1.0, "VX3": 3.0, "VX4": -2.0}
        margin = calendar_butterfly_margin(
            positions, "VX2", "VX3", "VX4", SPREAD_MARGINS)
        self.assertAlmostEqual(margin, 4610.0)

    def test_flat_position_has_zero_margin(self) -> None:
        margin = calendar_butterfly_margin(
            {}, "VX2", "VX3", "VX4", SPREAD_MARGINS)
        self.assertEqual(margin, 0.0)

    def test_scales_with_short_side(self) -> None:
        # short the fly: weights flip sign, magnitude unchanged -> same margin
        positions = {"VX2": 1.0, "VX3": -2.0, "VX4": 1.0}
        margin = calendar_butterfly_margin(
            positions, "VX2", "VX3", "VX4", SPREAD_MARGINS)
        self.assertAlmostEqual(margin, 3390.0)

    def test_raises_on_missing_spread_margin_config(self) -> None:
        with self.assertRaises(KeyError):
            calendar_butterfly_margin(
                {"VX2": -1.0}, "VX2", "VX3", "VX4", {})


class FlatMarginUsedTest(unittest.TestCase):
    def test_sums_configured_margin_requirements(self) -> None:
        specs = {
            "AAA": InstrumentSpec("AAA", "t", "futures", 1, 0.01, margin_requirement=500.0),
            "BBB": InstrumentSpec("BBB", "t", "futures", 1, 0.01, margin_requirement=200.0),
        }
        margin = flat_margin_used({"AAA": 2.0, "BBB": -3.0}, specs)
        self.assertAlmostEqual(margin, 2 * 500.0 + 3 * 200.0)

    def test_ignores_instruments_without_a_configured_margin(self) -> None:
        specs = {"SPY": InstrumentSpec(
            "SPY", "t", "equity", 1, 0.01, margin_requirement=None)}
        margin = flat_margin_used({"SPY": 100.0}, specs)
        self.assertEqual(margin, 0.0)


class MarginExceededTest(unittest.TestCase):
    def test_true_when_over_capital_base(self) -> None:
        self.assertTrue(margin_exceeded(
            margin_used=5000.0, capital_base=4000.0))

    def test_false_when_within_capital_base(self) -> None:
        self.assertFalse(margin_exceeded(
            margin_used=3000.0, capital_base=4000.0))


if __name__ == "__main__":
    unittest.main()
