from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.config import Settings, settings
from src.data.cache import SqliteCache
from src.data.providers import AkShareProvider, BaostockProvider, MarketDataProvider, TushareProvider


@dataclass
class MarketDataService:
    config: Settings = settings
    provider: MarketDataProvider | None = None

    def __post_init__(self) -> None:
        self.cache = SqliteCache(self.config.cache_path)
        self.warnings: list[str] = []
        if self.provider is None:
            self.provider = self._build_provider()

    def _build_provider(self) -> MarketDataProvider:
        if self.config.data_provider in {"auto", "baostock"}:
            return BaostockProvider()
        if self.config.data_provider == "tushare":
            return TushareProvider(self.config.tushare_token)
        return AkShareProvider()

    def _cached(self, key: tuple[str, ...], loader) -> pd.DataFrame:
        cached = self.cache.read(*key)
        if cached is not None and not cached.empty:
            return cached
        try:
            frame = loader()
        except Exception as exc:
            raise RuntimeError(f"数据获取失败且无可用缓存: {'/'.join(key)}。原始错误: {exc}") from exc
        if frame is not None and not frame.empty:
            self.cache.write(frame, *key)
        return frame

    def _refresh_or_cached(self, key: tuple[str, ...], loader) -> pd.DataFrame:
        try:
            frame = loader()
            if frame is not None and not frame.empty:
                self.cache.write(frame, *key)
                return frame
        except Exception as exc:
            cached = self.cache.read(*key)
            if cached is not None and not cached.empty:
                self.warnings.append(f"刷新失败，已使用缓存: {'/'.join(key)}。原始错误: {exc}")
                return cached
            raise RuntimeError(f"数据刷新失败且无可用缓存: {'/'.join(key)}。原始错误: {exc}") from exc
        cached = self.cache.read(*key)
        if cached is not None and not cached.empty:
            self.warnings.append(f"刷新结果为空，已使用缓存: {'/'.join(key)}")
            return cached
        raise RuntimeError(f"数据刷新结果为空且无可用缓存: {'/'.join(key)}")

    def list_sectors(self, board_type: str = "industry", refresh: bool = False) -> pd.DataFrame:
        key = ("sectors", board_type)
        if refresh:
            return self._refresh_or_cached(key, lambda: self.provider.list_sectors(board_type))
        return self._cached(key, lambda: self.provider.list_sectors(board_type))

    def sector_history(self, sector: str, start: str, end: str, board_type: str = "industry", refresh: bool = False) -> pd.DataFrame:
        key = ("sector_history", board_type, sector, start, end)
        if refresh:
            return self._refresh_or_cached(key, lambda: self.provider.sector_history(sector, start, end, board_type))
        return self._cached(key, lambda: self.provider.sector_history(sector, start, end, board_type))

    def sector_members(self, sector: str, board_type: str = "industry", refresh: bool = False) -> pd.DataFrame:
        key = ("sector_members", board_type, sector)
        if refresh:
            return self._refresh_or_cached(key, lambda: self.provider.sector_members(sector, board_type))
        return self._cached(key, lambda: self.provider.sector_members(sector, board_type))

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq", refresh: bool = False) -> pd.DataFrame:
        key = ("stock_history", symbol, start, end, adjust)
        if refresh:
            return self._refresh_or_cached(key, lambda: self.provider.stock_history(symbol, start, end, adjust))
        return self._cached(key, lambda: self.provider.stock_history(symbol, start, end, adjust))

    def etf_history(self, symbol: str, start: str, end: str, refresh: bool = False) -> pd.DataFrame:
        key = ("etf_history", symbol, start, end)
        if refresh:
            return self._refresh_or_cached(key, lambda: self.provider.etf_history(symbol, start, end))
        return self._cached(key, lambda: self.provider.etf_history(symbol, start, end))

    def benchmark_history(self, symbol: str, start: str, end: str, refresh: bool = False) -> pd.DataFrame:
        key = ("benchmark_history", symbol, start, end)
        if refresh:
            return self._refresh_or_cached(key, lambda: self.provider.benchmark_history(symbol, start, end))
        return self._cached(key, lambda: self.provider.benchmark_history(symbol, start, end))

    def stock_fund_flow(self, symbol: str, refresh: bool = False) -> pd.DataFrame:
        key = ("stock_fund_flow", symbol)
        try:
            if refresh:
                return self._refresh_or_cached(key, lambda: self.provider.stock_fund_flow(symbol))
            return self._cached(key, lambda: self.provider.stock_fund_flow(symbol))
        except Exception as exc:
            self.warnings.append(f"个股资金流获取失败: {symbol}。原始错误: {exc}")
            return pd.DataFrame()

    def sector_fund_flow(self, sector: str, board_type: str = "industry", refresh: bool = False) -> pd.DataFrame:
        key = ("sector_fund_flow", board_type, sector)
        try:
            if refresh:
                return self._refresh_or_cached(key, lambda: self.provider.sector_fund_flow(sector, board_type))
            return self._cached(key, lambda: self.provider.sector_fund_flow(sector, board_type))
        except Exception as exc:
            self.warnings.append(f"板块资金流获取失败: {sector}。原始错误: {exc}")
            return pd.DataFrame()
