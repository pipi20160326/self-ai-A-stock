from __future__ import annotations

import pandas as pd

from src.config import Settings
from src.data import MarketDataService


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.etf_calls = 0

    def etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        self.etf_calls += 1
        return pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, 1.2],
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
