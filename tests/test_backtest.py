from __future__ import annotations

import pandas as pd

from src.backtest import BacktestEngine
from src.config import Settings


def make_history(start: str = "2024-01-01", periods: int = 95, slope: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range(start, periods=periods, freq="B")
    close = pd.Series(range(periods), dtype=float) * slope + 10
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": 1000000,
            "amount": 1000000 + pd.Series(range(periods), dtype=float) * 5000,
        }
    )


class FakeData:
    def __init__(self) -> None:
        self.hist = make_history()

    def list_sectors(self, board_type: str = "industry", refresh: bool = False):
        return pd.DataFrame([{"sector": "测试板块", "code": "BK001"}])

    def sector_history(self, sector: str, start: str, end: str, board_type: str = "industry", refresh: bool = False):
        return self._slice(self.hist, end)

    def sector_members(self, sector: str, board_type: str = "industry", refresh: bool = False):
        return pd.DataFrame([{"symbol": "000001", "name": "测试股"}])

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq", refresh: bool = False):
        return self._slice(self.hist, end)

    def benchmark_history(self, symbol: str, start: str, end: str, refresh: bool = False):
        return self._slice(self.hist, end)

    def _slice(self, frame: pd.DataFrame, end: str) -> pd.DataFrame:
        cutoff = pd.to_datetime(end).strftime("%Y-%m-%d")
        return frame[frame["date"] <= cutoff].reset_index(drop=True)


def test_backtest_outputs_metrics_and_uses_next_day_trades() -> None:
    config = Settings(initial_cash=100000, max_positions=1, scan_top_sectors=1, scan_top_stocks_per_sector=1)
    result = BacktestEngine(FakeData(), config).run("20240101", "20240510", top_sectors=1, stocks_per_sector=1, initial_cash=100000)
    assert result.metrics["trade_count"] >= 1
    assert not result.equity.empty
    assert result.trades.iloc[0]["date"] > "202403"

