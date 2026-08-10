"""Strategy contract and reference implementations."""

from .contracts import PriceBar, PricePanel, Strategy, TargetPosition, merge_panels
from .rank_butterfly import HEDGED_1_3_2, PLAIN_1_2_1, ButterflyWeights, RankButterflyStrategy
from .sma_crossover import SmaCrossoverStrategy

__all__ = [
    "PriceBar",
    "PricePanel",
    "Strategy",
    "TargetPosition",
    "merge_panels",
    "SmaCrossoverStrategy",
    "RankButterflyStrategy",
    "ButterflyWeights",
    "PLAIN_1_2_1",
    "HEDGED_1_3_2",
]
