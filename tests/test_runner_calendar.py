import unittest
from datetime import date

from oqg_portfolio_sim.runner.calendar import is_trading_day, previous_session


class IsTradingDayTest(unittest.TestCase):
    def test_weekday_is_a_trading_day(self) -> None:
        self.assertTrue(is_trading_day(date(2026, 7, 20)))  # Monday

    def test_saturday_is_not_a_trading_day(self) -> None:
        self.assertFalse(is_trading_day(date(2026, 7, 18)))

    def test_sunday_is_not_a_trading_day(self) -> None:
        self.assertFalse(is_trading_day(date(2026, 7, 19)))

    def test_new_years_day_is_not_a_trading_day(self) -> None:
        self.assertFalse(is_trading_day(date(2026, 1, 1)))


class PreviousSessionTest(unittest.TestCase):
    def test_returns_prior_weekday(self) -> None:
        self.assertEqual(previous_session(
            date(2026, 7, 21)), date(2026, 7, 20))

    def test_skips_weekend(self) -> None:
        # Monday 2026-07-20's previous session is Friday 2026-07-17.
        self.assertEqual(previous_session(
            date(2026, 7, 20)), date(2026, 7, 17))


if __name__ == "__main__":
    unittest.main()
