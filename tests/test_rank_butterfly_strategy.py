import math
import unittest
from datetime import date, timedelta

import pandas as pd

from oqg_portfolio_sim.ingestion import load_vix_futures_parquet, rank_panel
from oqg_portfolio_sim.strategies import (
    HEDGED_1_3_2,
    PLAIN_1_2_1,
    PricePanel,
    RankButterflyStrategy,
)
from oqg_portfolio_sim.strategies.contracts import PriceBar
from oqg_portfolio_sim.strategies.rank_butterfly import _walk_position_state


def _flat_leg_panel(dates: list[date], mf_values: list[float]) -> PricePanel:
    """A synthetic 3-leg panel where near=far=0, so mf = mid = mf_values.

    Lets tests specify the desired butterfly-value path directly instead of
    deriving it from three separate contract prices.
    """
    zero_bars = [PriceBar(as_of=d, close=0.0) for d in dates]
    mid_bars = [PriceBar(as_of=d, close=v)
                for d, v in zip(dates, mf_values)]
    return PricePanel(series={"VX2": zero_bars, "VX3": mid_bars, "VX4": zero_bars})


def _business_dates(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


class RankButterflyFidelityTest(unittest.TestCase):
    """Ports run_month_fly(month_px_full, 2.0, 3.0, 4.0) from rank_butterflies.ipynb
    and checks it against the notebook's own printed train-period summary
    (trades=21, mean_pnl=0.5407, win_rate=0.8095, mean_days=13.3333)."""

    def test_matches_notebook_train_period_summary(self) -> None:
        history = load_vix_futures_parquet()
        history = history[history["trade_date"] >=
                           pd.Timestamp("2007-07-01").date()]
        panel = rank_panel(history, n_ranks=4)

        def series_from(instrument_id: str) -> pd.Series:
            return pd.Series({bar.as_of: bar.close for bar in panel.series[instrument_id]})

        aligned = pd.DataFrame(
            {"near": series_from("VX2"), "mid": series_from(
                "VX3"), "far": series_from("VX4")}
        ).sort_index().dropna()
        mf = aligned["mid"] - 0.5 * (aligned["near"] + aligned["far"])

        mu = mf.shift(1).rolling(252, min_periods=200).mean()
        sigma = mf.shift(1).rolling(252, min_periods=200).std()
        z = (mf - mu) / sigma

        _pos, trades = _walk_position_state(z, entry_z=2.0, max_hold=20)
        tr = pd.DataFrame(trades)

        mf_exec = mf.shift(-1)
        tr["F_entry"] = mf_exec.reindex(tr["entry_date"]).to_numpy()
        tr["F_exit"] = mf_exec.reindex(tr["exit_date"]).to_numpy()
        tr["pnl"] = tr["dir"] * (tr["F_exit"] - tr["F_entry"])

        train_end = pd.Timestamp("2017-12-31").date()
        train = tr[tr["entry_date"] <= train_end]

        self.assertEqual(len(train), 21)
        self.assertAlmostEqual(train["pnl"].mean(), 0.5407, places=4)
        self.assertAlmostEqual(train["pnl"].sum(), 11.355, places=3)
        self.assertAlmostEqual((train["pnl"] > 0).mean(), 0.8095, places=4)
        self.assertAlmostEqual(train["days"].mean(), 13.3333, places=3)


class RankButterflyStrategyTest(unittest.TestCase):
    def test_required_instruments_returns_configured_legs(self) -> None:
        strategy = RankButterflyStrategy()
        specs = strategy.required_instruments()

        self.assertEqual([s.instrument_id for s in specs], [
                          "VX2", "VX3", "VX4"])

    def test_required_instruments_raises_when_spec_missing(self) -> None:
        strategy = RankButterflyStrategy(instrument_prefix="ZZ")

        with self.assertRaises(ValueError):
            strategy.required_instruments()

    def test_flat_when_insufficient_history(self) -> None:
        strategy = RankButterflyStrategy(lookback=20, min_periods=15)
        dates = _business_dates(3)
        panel = _flat_leg_panel(dates, [0.0, 0.1, -0.1])

        targets = strategy.generate_targets(dates[-1], panel)

        self.assertEqual(targets, [])

    def test_flat_when_no_signal(self) -> None:
        strategy = RankButterflyStrategy(lookback=20, min_periods=15)
        dates = _business_dates(25)
        # Small oscillation, no spike -- z should stay within +-2 throughout.
        mf_values = [0.1 * math.sin(i) for i in range(len(dates))]
        panel = _flat_leg_panel(dates, mf_values)

        targets = strategy.generate_targets(dates[-1], panel)

        self.assertEqual(targets, [])

    def test_long_entry_uses_plain_weights_with_correct_sign(self) -> None:
        strategy = RankButterflyStrategy(
            lookback=20, min_periods=15, weights=PLAIN_1_2_1)
        dates = _business_dates(21)
        mf_values = [0.1 * math.sin(i) for i in range(20)] + [-100.0]
        panel = _flat_leg_panel(dates, mf_values)

        targets = strategy.generate_targets(dates[-1], panel)

        by_instrument = {t.instrument_id: t.qty for t in targets}
        # z << -entry_z -> pos=+1 (long the fly) -> weights unchanged: (-1, +2, -1).
        self.assertEqual(by_instrument, {"VX2": -1, "VX3": 2, "VX4": -1})

    def test_short_entry_uses_hedged_weights_with_flipped_sign(self) -> None:
        strategy = RankButterflyStrategy(
            lookback=20, min_periods=15, weights=HEDGED_1_3_2)
        dates = _business_dates(21)
        mf_values = [0.1 * math.sin(i) for i in range(20)] + [100.0]
        panel = _flat_leg_panel(dates, mf_values)

        targets = strategy.generate_targets(dates[-1], panel)

        by_instrument = {t.instrument_id: t.qty for t in targets}
        # z >> entry_z -> pos=-1 (short the fly) -> weights flip sign: (+1, -3, +2).
        self.assertEqual(by_instrument, {"VX2": 1, "VX3": -3, "VX4": 2})

    def test_no_lookahead_future_spike_is_ignored(self) -> None:
        strategy = RankButterflyStrategy(lookback=20, min_periods=15)
        dates = _business_dates(22)
        mf_values = [0.1 * math.sin(i) for i in range(21)] + [500.0]
        panel = _flat_leg_panel(dates, mf_values)

        as_of = dates[-2]  # the day before the future spike
        targets = strategy.generate_targets(as_of, panel)

        self.assertEqual(targets, [])


if __name__ == "__main__":
    unittest.main()
