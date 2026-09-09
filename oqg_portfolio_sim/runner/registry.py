"""Which strategies the daily runner actually processes.

Per-strategy attribution is first-class (PROJECT_SPEC.md's ledger design),
so the runner works off an explicit list of registrations rather than
special-casing one strategy. Each entry carries its own capital base and
margin calculation, since those are strategy-structure-specific (see
engine/margin.py).

SmaCrossoverStrategy is deliberately NOT in the default registry -- it was
built as a throwaway pipeline smoke test (Phase 3), not a strategy meant
for the public track record. MaBreakoutHoldStrategy IS included, despite
also being deliberately naive: unlike the rank butterfly (which only trades
on rare VIX term-structure signals), it trades often enough to serve as a
canary -- a quick glance at its trade log or equity curve on the site is
enough to tell the pipeline is actually alive, without waiting on a rare
signal from the real strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from oqg_portfolio_sim.core.models import load_instrument_specs, load_spread_margins
from oqg_portfolio_sim.engine.margin import calendar_butterfly_margin, flat_margin_used
from oqg_portfolio_sim.strategies import MaBreakoutHoldStrategy, RankButterflyStrategy
from oqg_portfolio_sim.strategies.contracts import Strategy

MarginFn = Callable[[dict[str, float]], float]


@dataclass(frozen=True)
class StrategyRegistration:
    strategy_id: str
    name: str
    description: str
    owner: str
    asset_class: str
    strategy: Strategy
    capital_base: float
    margin_fn: MarginFn


def default_registry() -> list[StrategyRegistration]:
    spread_margins = load_spread_margins()
    butterfly = RankButterflyStrategy()
    near_id, mid_id, far_id = butterfly.leg_ids

    def butterfly_margin(positions: dict[str, float]) -> float:
        return calendar_butterfly_margin(positions, near_id, mid_id, far_id, spread_margins)

    instrument_specs = {spec.instrument_id: spec for spec in load_instrument_specs()}
    canary = MaBreakoutHoldStrategy()

    def canary_margin(positions: dict[str, float]) -> float:
        return flat_margin_used(positions, instrument_specs)

    return [
        StrategyRegistration(
            strategy_id="rank_butterfly_234",
            name="VIX Rank Butterfly (M2-M3-M4, plain 1-2-1)",
            description=(
                "Constant-rank VIX futures butterfly, ported from "
                "rank_butterflies.ipynb: z-score mean reversion on "
                "mid - 0.5*(near+far) across ranked VIX expiries."
            ),
            owner="club",
            asset_class="futures",
            strategy=butterfly,
            capital_base=100_000.0,
            margin_fn=butterfly_margin,
        ),
        StrategyRegistration(
            strategy_id="spy_ma_breakout_canary",
            name="SPY 200d MA Breakout (5-day hold, canary)",
            description=(
                "Deliberately naive reference strategy: long SPY when price "
                "crosses above its 200-day moving average, held for a fixed "
                "5 sessions regardless of price action, then closed flat. "
                "Exists as a pipeline canary -- it trades often enough to "
                "sanity-check the site is updating, unlike the rank "
                "butterfly's rare signals."
            ),
            owner="club",
            asset_class="equity",
            strategy=canary,
            capital_base=25_000.0,
            margin_fn=canary_margin,
        ),
    ]
