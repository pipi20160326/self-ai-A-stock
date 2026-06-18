from __future__ import annotations

import pandas as pd

from src.config import Settings
from src.data import MarketDataService
from src.data.providers import BaostockProvider


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


def test_baostock_sector_has_local_code_and_resolves_search_key() -> None:
    provider = BaostockProvider()
    provider._board_list_from_ak = lambda board_type="industry": pd.DataFrame()
    provider._board_members_from_ak = lambda sector_name, board_type="industry": pd.DataFrame(columns=["symbol", "name"])
    provider._industry_cache = pd.DataFrame(
        [
            {"symbol": "600001", "name": "A", "sector": "Steel", "classification": "industry"},
            {"symbol": "600002", "name": "B", "sector": "Steel", "classification": "industry"},
            {"symbol": "600003", "name": "C", "sector": "Bank", "classification": "industry"},
        ]
    )

    sectors = provider.list_sectors()
    steel_code = str(sectors.loc[sectors["sector"].eq("Steel"), "code"].iloc[0])

    assert steel_code.startswith("BSI")
    assert provider.sector_members(steel_code)["symbol"].tolist() == ["600001", "600002"]
    assert provider.sector_members("Ste")["symbol"].tolist() == ["600001", "600002"]


def test_baostock_resolves_real_board_code_and_keyword() -> None:
    provider = BaostockProvider()
    provider._board_list_from_ak = lambda board_type="industry": pd.DataFrame(
        [
            {"sector": "证券", "code": "BK0473", "pct_chg": 1.2},
            {"sector": "机器人概念", "code": "BK1100", "pct_chg": 0.8},
        ]
    )

    assert provider._resolve_sector_name("BK0473") == "证券"
    assert provider._resolve_sector_name("机器人") == "机器人概念"
