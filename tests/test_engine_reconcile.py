import unittest

from oqg_portfolio_sim.engine.reconcile import (
    FillRecord,
    recompute_positions_from_fills,
    reconcile,
)


class RecomputePositionsFromFillsTest(unittest.TestCase):
    def test_sums_fills_per_instrument(self) -> None:
        fills = [
            FillRecord("VX2", -1.0),
            FillRecord("VX3", 2.0),
            FillRecord("VX2", 1.0),  # closed out later
        ]
        positions = recompute_positions_from_fills(fills)
        self.assertEqual(positions, {"VX2": 0.0, "VX3": 2.0})

    def test_empty_fills_gives_empty_positions(self) -> None:
        self.assertEqual(recompute_positions_from_fills([]), {})


class ReconcileTest(unittest.TestCase):
    def test_matching_positions_reconcile(self) -> None:
        ok, note = reconcile({"VX2": -1.0}, {"VX2": -1.0})
        self.assertTrue(ok)
        self.assertIsNone(note)

    def test_mismatch_is_reported_with_both_values(self) -> None:
        ok, note = reconcile({"VX2": -1.0}, {"VX2": -2.0})
        self.assertFalse(ok)
        self.assertIn("VX2", note)
        self.assertIn("-1.0", note)
        self.assertIn("-2.0", note)

    def test_missing_key_treated_as_zero_on_both_sides(self) -> None:
        # recomputed has an instrument the stored snapshot doesn't (flat = absent)
        ok, note = reconcile({"VX2": 0.0}, {})
        self.assertTrue(ok)
        self.assertIsNone(note)

    def test_within_tolerance_is_not_a_mismatch(self) -> None:
        ok, note = reconcile(
            {"VX2": 1.0000001}, {"VX2": 1.0}, tolerance=1e-4)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
