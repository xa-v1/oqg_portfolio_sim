"""Port of the club's constant-rank VIX futures butterfly notebook.

Source: rank_butterflies.ipynb (`run_month_fly` / `run_fly_series`), ported
to this project's `Strategy` contract. The economics are unchanged from the
notebook; what changes is how a "position" is produced: the notebook ran
one big offline backtest loop and read off a trade log, whereas
`generate_targets` is called once per `as_of` date with only data <= as_of
and must return today's desired position. To stay faithful without
duplicating logic, it replays the exact same state machine over the full
point-in-time history on every call and returns wherever that walk lands.

Butterfly value: mf = mid - 0.5*(near + far), for three ranked expiries
(default M2/M3/M4, i.e. VX2/VX3/VX4). Entry when |z| > entry_z, where z is
mf's distance from its own `lookback`-day rolling mean/std (that rolling
window is itself lagged one day, so the entry threshold never leans on
today's own fly value). Exit on reversion (z crosses back through 0) or
after `max_hold` sessions, whichever comes first -- matching the notebook's
"revert/timeout exits" exactly, including next-bar execution semantics
(handled by the simulation engine's next-session fill convention, not by
this strategy).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from oqg_portfolio_sim.core.models import InstrumentSpec, load_instrument_specs

from .contracts import PricePanel, TargetPosition


@dataclass(frozen=True)
class ButterflyWeights:
    """Integer contract weights on (near, mid, far) legs when long the fly (pos=+1).

    Short the fly (pos=-1) negates all three. E.g. plain (-1, +2, -1) means:
    long the fly = short 1 near, long 2 mid, short 1 far.
    """

    near: int
    mid: int
    far: int


# Notebook's "Integer Rounded Weights": classic 1x2x1 fly.
PLAIN_1_2_1 = ButterflyWeights(near=-1, mid=2, far=-1)
# Notebook's PCA-informed hedge that zeros PC1/PC2 (level/slope) exposure,
# rounded to integer contracts; carries more curvature (PC3) exposure per
# unit margin than the plain fly, at the cost of two extra legs.
HEDGED_1_3_2 = ButterflyWeights(near=-1, mid=3, far=-2)


def _walk_position_state(
    z: pd.Series, entry_z: float, max_hold: int
) -> tuple[int, list[dict]]:
    """Replays the notebook's entry/exit state machine over a z-score series.

    Returns (final_position, trades), where each trade records entry/exit
    dates, direction, days held, and exit reason ("revert" or "timeout").
    This is a direct port of the notebook's `run_fly_series` loop body.
    """

    trades: list[dict] = []
    pos = 0
    days_held = 0
    entry_date = None

    for t in range(len(z)):
        zt = z.iloc[t]
        if pos == 0:
            if zt > entry_z:
                pos, days_held, entry_date = -1, 0, z.index[t]
            elif zt < -entry_z:
                pos, days_held, entry_date = 1, 0, z.index[t]
        else:
            days_held += 1
            reverted = (pos == -1 and zt <= 0) or (pos == 1 and zt >= 0)
            if reverted or days_held >= max_hold:
                trades.append(
                    {
                        "entry_date": entry_date,
                        "exit_date": z.index[t],
                        "dir": pos,
                        "days": days_held,
                        "reason": "revert" if reverted else "timeout",
                    }
                )
                pos, days_held, entry_date = 0, 0, None

    return pos, trades


class RankButterflyStrategy:
    def __init__(
        self,
        ranks: tuple[int, int, int] = (2, 3, 4),
        weights: ButterflyWeights = PLAIN_1_2_1,
        entry_z: float = 2.0,
        max_hold: int = 20,
        lookback: int = 252,
        min_periods: int = 200,
        instrument_prefix: str = "VX",
    ) -> None:
        self.ranks = ranks
        self.weights = weights
        self.entry_z = entry_z
        self.max_hold = max_hold
        self.lookback = lookback
        self.min_periods = min_periods
        self.instrument_prefix = instrument_prefix

    @property
    def leg_ids(self) -> tuple[str, str, str]:
        near, mid, far = self.ranks
        return (
            f"{self.instrument_prefix}{near}",
            f"{self.instrument_prefix}{mid}",
            f"{self.instrument_prefix}{far}",
        )

    def required_instruments(self) -> list[InstrumentSpec]:
        specs = {spec.instrument_id: spec for spec in load_instrument_specs()}
        missing = [
            instrument_id for instrument_id in self.leg_ids if instrument_id not in specs]
        if missing:
            raise ValueError(
                f"missing instrument spec(s) for rank butterfly legs: {missing}. "
                "Add them to config/instruments.json."
            )
        return [specs[instrument_id] for instrument_id in self.leg_ids]

    def _fly_series(self, history: PricePanel, as_of: date) -> pd.Series:
        near_id, mid_id, far_id = self.leg_ids
        columns = {}
        for instrument_id in (near_id, mid_id, far_id):
            bars = history.closes_as_of(instrument_id, as_of)
            columns[instrument_id] = pd.Series(
                {bar.as_of: bar.close for bar in bars})

        # Inner-align on dates where all three legs have data, mirroring the
        # notebook's pivot_table + dropna.
        aligned = pd.DataFrame(columns).sort_index().dropna()
        if aligned.empty:
            return pd.Series(dtype=float)

        return aligned[mid_id] - 0.5 * (aligned[near_id] + aligned[far_id])

    def generate_targets(self, as_of: date, history: PricePanel) -> list[TargetPosition]:
        mf = self._fly_series(history, as_of)
        if mf.empty:
            return []

        mu = mf.shift(1).rolling(self.lookback,
                                  min_periods=self.min_periods).mean()
        sigma = mf.shift(1).rolling(self.lookback,
                                     min_periods=self.min_periods).std()
        z = (mf - mu) / sigma

        pos, _trades = _walk_position_state(z, self.entry_z, self.max_hold)
        if pos == 0:
            return []

        near_id, mid_id, far_id = self.leg_ids
        w = self.weights
        reason = f"rank-butterfly z-score, pos={pos:+d}"
        return [
            TargetPosition(near_id, pos * w.near, reason=reason),
            TargetPosition(mid_id, pos * w.mid, reason=reason),
            TargetPosition(far_id, pos * w.far, reason=reason),
        ]
