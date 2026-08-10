import unittest
from datetime import date, timedelta

from oqg_portfolio_sim.strategies import PriceBar, PricePanel, SmaCrossoverStrategy


def _panel_from_closes(instrument_id: str, closes: list[float], start: date) -> PricePanel:
    bars = [
        PriceBar(as_of=start + timedelta(days=i), close=close)
        for i, close in enumerate(closes)
    ]
    return PricePanel(series={instrument_id: bars})


class SmaCrossoverStrategyTest(unittest.TestCase):
    def test_required_instruments_returns_matching_config_spec(self) -> None:
        strategy = SmaCrossoverStrategy(instrument_id="SPY")
        specs = strategy.required_instruments()

        self.assertEqual([spec.instrument_id for spec in specs], ["SPY"])

    def test_flat_when_history_shorter_than_window(self) -> None:
        strategy = SmaCrossoverStrategy(instrument_id="SPY", window=20)
        start = date(2026, 1, 1)
        panel = _panel_from_closes("SPY", [100.0] * 5, start)

        targets = strategy.generate_targets(start + timedelta(days=4), panel)

        self.assertEqual(targets, [])

    def test_goes_long_when_close_above_sma(self) -> None:
        strategy = SmaCrossoverStrategy(instrument_id="SPY", window=5, qty=2.0)
        start = date(2026, 1, 1)
        # Flat at 100 for 4 days, then a jump to 110 -> last close > 5d SMA.
        closes = [100.0, 100.0, 100.0, 100.0, 110.0]
        panel = _panel_from_closes("SPY", closes, start)
        as_of = start + timedelta(days=4)

        targets = strategy.generate_targets(as_of, panel)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].instrument_id, "SPY")
        self.assertEqual(targets[0].qty, 2.0)

    def test_flat_when_close_at_or_below_sma(self) -> None:
        strategy = SmaCrossoverStrategy(instrument_id="SPY", window=5)
        start = date(2026, 1, 1)
        # Downtrend -> last close sits below the trailing average.
        closes = [110.0, 108.0, 106.0, 104.0, 100.0]
        panel = _panel_from_closes("SPY", closes, start)
        as_of = start + timedelta(days=4)

        targets = strategy.generate_targets(as_of, panel)

        self.assertEqual(targets, [])

    def test_no_lookahead_future_bars_are_ignored(self) -> None:
        strategy = SmaCrossoverStrategy(instrument_id="SPY", window=5)
        start = date(2026, 1, 1)
        # As of day 4 this is a flat/downtrend (flat signal). Day 5 (future,
        # relative to as_of) spikes up -- it must NOT influence day 4's target.
        closes = [110.0, 108.0, 106.0, 104.0, 100.0, 500.0]
        panel = _panel_from_closes("SPY", closes, start)
        as_of = start + timedelta(days=4)

        targets = strategy.generate_targets(as_of, panel)

        self.assertEqual(targets, [])

    def test_closes_as_of_excludes_bars_after_as_of(self) -> None:
        start = date(2026, 1, 1)
        panel = _panel_from_closes("SPY", [1.0, 2.0, 3.0], start)

        bars = panel.closes_as_of("SPY", start + timedelta(days=1))

        self.assertEqual([bar.close for bar in bars], [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
