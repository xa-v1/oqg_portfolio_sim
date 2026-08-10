"""Which strategies the daily runner actually processes.

Per-strategy attribution is first-class (PROJECT_SPEC.md's ledger design),
so the runner works off an explicit list of registrations rather than
special-casing one strategy. Each entry carries its own capital base and
margin calculation, since those are strategy-structure-specific (see
engine/margin.py).

SmaCrossoverStrategy is deliberately NOT in the default registry -- it was
built as a throwaway pipeline smoke test (Phase 3), not a strategy meant
for the public track record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from oqg_portfolio_sim.core.models import load_spread_margins
from oqg_portfolio_sim.engine.margin import calendar_butterfly_margin
from oqg_portfolio_sim.strategies import RankButterflyStrategy
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
    ]
