from __future__ import annotations

import pandas as pd

from src.config import Settings
from src.data import MarketDataService


class FakeProvider:
    def __init__(self, name: str = "fake", close: float = 1.2) -> None:
        self.name = name
        self.close = close
        self.etf_calls = 0

    def etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        self.etf_calls += 1
        return pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, self.close],
                "amount": [1000, 1200],
            }
        )


def test_etf_history_uses_service_cache(tmp_path) -> None:
    provider = FakeProvider()
    config = Settings(cache_path=tmp_path / "cache.sqlite3")
    data = MarketDataService(config=config, provider=provider)

    first = data.etf_history("510300", "20240101", "20240110")
    second = data.etf_history("510300", "20240101", "20240110")

    assert provider.etf_calls == 1
    assert first.equals(second)
    assert list(first["close"]) == [1.1, 1.2]


def test_cache_is_isolated_by_provider_name(tmp_path) -> None:
    config = Settings(cache_path=tmp_path / "cache.sqlite3")
    baostock = FakeProvider(name="baostock", close=1.2)
    akshare = FakeProvider(name="akshare", close=2.2)

    first = MarketDataService(config=config, provider=baostock).etf_history("510300", "20240101", "20240110")
    second = MarketDataService(config=config, provider=akshare).etf_history("510300", "20240101", "20240110")

    assert list(first["close"]) == [1.1, 1.2]
    assert list(second["close"]) == [1.1, 2.2]
    assert baostock.etf_calls == 1
    assert akshare.etf_calls == 1
