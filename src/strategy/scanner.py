from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.config import Settings, settings
from src.data.service import MarketDataService
from .sector import market_is_healthy, score_sector
from .stock import score_stock


@dataclass
class TrendScanner:
    data: MarketDataService
    config: Settings = settings

    def rank_sectors(self, start: str, end: str, board_type: str = "industry", limit: int | None = None) -> pd.DataFrame:
        sectors = self.data.list_sectors(board_type)
        benchmark = self.data.benchmark_history(self.config.benchmark_symbol, start, end)
        rows = []
        for _, row in sectors.iterrows():
            sector = row["sector"]
            try:
                hist = self.data.sector_history(sector, start, end, board_type)
                scored = score_sector(hist, benchmark)
                rows.append({"sector": sector, "code": row.get("code", ""), **scored})
            except Exception as exc:
                rows.append({"sector": sector, "code": row.get("code", ""), "score": float("-inf"), "reason": f"数据失败: {exc}"})
        ranked = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
        if limit:
            ranked = ranked.head(limit)
        return ranked

    def scan(
        self,
        start: str | None = None,
        end: str | None = None,
        board_type: str = "industry",
        top_sectors: int | None = None,
        stocks_per_sector: int | None = None,
    ) -> pd.DataFrame:
        start = start or self.config.start_date
        end = end or date.today().strftime("%Y%m%d")
        top_sectors = top_sectors or self.config.scan_top_sectors
        stocks_per_sector = stocks_per_sector or self.config.scan_top_stocks_per_sector
        ranked = self.rank_sectors(start, end, board_type, top_sectors)
        benchmark = self.data.benchmark_history(self.config.benchmark_symbol, start, end)
        healthy = market_is_healthy(benchmark) if self.config.market_filter else True
        rows = []
        for sector_rank, sector_row in ranked.iterrows():
            sector = sector_row["sector"]
            try:
                members = self.data.sector_members(sector, board_type)
            except Exception as exc:
                rows.append({"sector": sector, "signal": "观察", "reason": f"成分股失败: {exc}"})
                continue
            candidates = []
            for _, member in members.iterrows():
                symbol = member.get("symbol")
                if not symbol:
                    continue
                try:
                    hist = self.data.stock_history(symbol, start, end)
                    scored = score_stock(hist, float(sector_row["score"]))
                    if not healthy and scored["signal"] == "买入":
                        scored["signal"] = "观察"
                        scored["reason"] = f"大盘过滤未通过、{scored['reason']}"
                    candidates.append(
                        {
                            "scan_date": pd.to_datetime(end).strftime("%Y-%m-%d"),
                            "sector_rank": sector_rank + 1,
                            "sector": sector,
                            "sector_score": sector_row["score"],
                            "symbol": symbol,
                            "name": member.get("name", ""),
                            **scored,
                        }
                    )
                except Exception:
                    continue
            rows.extend(sorted(candidates, key=lambda x: x["score"], reverse=True)[:stocks_per_sector])
        return pd.DataFrame(rows)

