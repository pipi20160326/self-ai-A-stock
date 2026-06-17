from __future__ import annotations

import pandas as pd

from src.config import Settings, settings
from src.data import MarketDataService
from src.strategy.sector import score_sector
from src.strategy.stock import score_stock


def score_stock_code(data: MarketDataService, symbol: str, start: str, end: str) -> tuple[dict, pd.DataFrame]:
    history = data.stock_history(symbol.strip(), start, end)
    return score_stock(history, 0.0), history


def score_sector_key(
    data: MarketDataService,
    sector_key: str,
    start: str,
    end: str,
    board_type: str = "industry",
    config: Settings = settings,
) -> tuple[dict, pd.DataFrame]:
    history = data.sector_history(sector_key.strip(), start, end, board_type)
    try:
        benchmark = data.benchmark_history(config.benchmark_symbol, start, end)
    except Exception:
        benchmark = pd.DataFrame()
    return score_sector(history, benchmark), history

