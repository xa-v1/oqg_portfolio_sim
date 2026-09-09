import unittest
from datetime import date, timedelta

from oqg_portfolio_sim.strategies import MaBreakoutHoldStrategy
from oqg_portfolio_sim.strategies.contracts import PriceBar, PricePanel


def _business_dates(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _panel_from_closes(dates: list[date], closes: list[float]) -> PricePanel:
    bars = [PriceBar(as_of=d, close=c) for d, c in zip(dates, closes)]
    return PricePanel(series={"SPY": bars})


class MaBreakoutHoldStrategyTest(unittest.TestCase):
    def test_required_instruments_returns_spy(self) -> None:
        strategy = MaBreakoutHoldStrategy()
        specs = strategy.required_instruments()
        self.assertEqual([s.instrument_id for s in specs], ["SPY"])

    def test_required_instruments_raises_for_unconfigured_instrument(self) -> None:
        strategy = MaBreakoutHoldStrategy(instrument_id="NOPE")
        with self.assertRaises(ValueError):
            strategy.required_instruments()

    def test_flat_when_insufficient_history(self) -> None:
        strategy = MaBreakoutHoldStrategy(window=10, hold_days=3)
        dates = _business_dates(5)
        panel = _panel_from_closes(dates, [100.0] * 5)

        self.assertEqual(strategy.generate_targets(dates[-1], panel), [])

    def test_flat_when_price_never_crosses(self) -> None:
        # Flat at 100 the whole time -> the first valid-SMA day can never
        # register as a "cross" (there's no prior-day SMA to compare
        # against), so this must never spuriously enter.
        strategy = MaBreakoutHoldStrategy(window=10, hold_days=3)
        dates = _business_dates(15)
        panel = _panel_from_closes(dates, [100.0] * 15)

        self.assertEqual(strategy.generate_targets(dates[-1], panel), [])

    def test_full_lifecycle_entry_hold_through_dip_then_exit(self) -> None:
        strategy = MaBreakoutHoldStrategy(
            window=10, hold_days=3, qty=1.0, instrument_id="SPY")
        dates = _business_dates(15)
        # Flat at 100 for the 10-day window, then a breakout above the MA,
        # then a sharp dip WHILE holding (must not trigger an early exit),
        # then normal prices until the fixed hold expires.
        closes = [100.0] * 10 + [110.0, 50.0, 60.0, 70.0, 80.0]
        panel = _panel_from_closes(dates, closes)

        # Day of the breakout (index 10): enters long.
        targets = strategy.generate_targets(dates[10], panel)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].instrument_id, "SPY")
        self.assertEqual(targets[0].qty, 1.0)

        # Day 1 of the hold (index 11), price crashed to 50 -- still long,
        # no early exit on the dip.
        targets = strategy.generate_targets(dates[11], panel)
        self.assertEqual([t.qty for t in targets], [1.0])

        # Day 2 of the hold (index 12) -- still long, and not re-entered
        # (qty stays exactly 1.0, not accumulated).
        targets = strategy.generate_targets(dates[12], panel)
        self.assertEqual([t.qty for t in targets], [1.0])

        # Day 3 (index 13): the fixed hold (3 sessions: 10, 11, 12) has
        # elapsed -- flat.
        targets = strategy.generate_targets(dates[13], panel)
        self.assertEqual(targets, [])

        # Stays flat afterward (no new cross has happened since).
        targets = strategy.generate_targets(dates[14], panel)
        self.assertEqual(targets, [])

    def test_no_lookahead_future_spike_is_ignored(self) -> None:
        strategy = MaBreakoutHoldStrategy(window=10, hold_days=3)
        dates = _business_dates(12)
        # Flat through day 10, then a future breakout at index 11 that must
        # not affect the target reported as of the day before it.
        closes = [100.0] * 11 + [500.0]
        panel = _panel_from_closes(dates, closes)

        targets = strategy.generate_targets(dates[10], panel)

        self.assertEqual(targets, [])


if __name__ == "__main__":
    unittest.main()
