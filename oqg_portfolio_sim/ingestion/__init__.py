"""EOD data ingestion layer: per-source adapters producing normalized PricePanels."""

from .base import DataSourceError, EodSource
from .equities import FallbackSource, StooqSource, YFinanceSource, build_equity_panel
from .vix_futures import (
    CboeSettlementSource,
    load_vix_futures_parquet,
    merge_settlement_updates,
    rank_panel,
    refresh_history,
)

__all__ = [
    "DataSourceError",
    "EodSource",
    "YFinanceSource",
    "StooqSource",
    "FallbackSource",
    "build_equity_panel",
    "CboeSettlementSource",
    "load_vix_futures_parquet",
    "merge_settlement_updates",
    "rank_panel",
    "refresh_history",
]
