from __future__ import annotations

import pandas as pd

from src.strategy.sector import score_sector
from src.strategy.stock import score_stock
from src.strategy.indicators import with_indicators


def make_history(start: str = "2024-01-01", periods: int = 90, slope: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range(start, periods=periods, freq="B")
    close = pd.Series(range(periods), dtype=float) * slope + 10
    open_ = close * 0.995
    high = close * 1.02
    low = close * 0.98
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000000,
            "amount": pd.Series(range(periods), dtype=float) * 10000 + 1000000,
        }
    )


def test_sector_uptrend_scores_above_downtrend() -> None:
    up = make_history(slope=1.0)
    down = make_history(slope=-0.05)
    up_score = score_sector(up)["score"]
    down_score = score_sector(down)["score"]
    assert up_score > down_score


def test_stock_uptrend_breakout_is_buy_candidate() -> None:
    hist = make_history(slope=1.0)
    scored = score_stock(hist, sector_score=0.2)
    assert scored["signal"] == "买入"
    assert scored["score"] > 0


def test_stock_losing_trend_is_sell() -> None:
    hist = make_history(slope=-0.02)
    scored = score_stock(hist, sector_score=-0.1)
    assert scored["signal"] in {"卖出", "观察"}


def test_indicators_include_short_and_long_moving_averages() -> None:
    detail = with_indicators(make_history(periods=12))
    assert {"ma5", "ma10", "ma20", "ma60"}.issubset(detail.columns)
    assert pd.notna(detail.iloc[-1]["ma5"])
    assert pd.notna(detail.iloc[-1]["ma10"])
    assert pd.isna(detail.iloc[-1]["ma20"])
