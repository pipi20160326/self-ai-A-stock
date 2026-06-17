from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.config import Settings, settings
from src.data.service import MarketDataService
from src.strategy.indicators import annualized_return, max_drawdown, sharpe_ratio
from src.strategy.scanner import TrendScanner


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict


@dataclass
class BacktestEngine:
    data: MarketDataService
    config: Settings = settings

    def run(
        self,
        start: str,
        end: str,
        board_type: str = "industry",
        top_sectors: int | None = None,
        stocks_per_sector: int | None = None,
        initial_cash: float | None = None,
    ) -> BacktestResult:
        initial_cash = initial_cash or self.config.initial_cash
        top_sectors = top_sectors or self.config.scan_top_sectors
        stocks_per_sector = stocks_per_sector or self.config.scan_top_stocks_per_sector
        benchmark = self.data.benchmark_history(self.config.benchmark_symbol, start, end)
        dates = list(pd.to_datetime(benchmark["date"]).dt.strftime("%Y%m%d"))
        if len(dates) < 70:
            raise ValueError("回测区间太短，至少需要约 70 个交易日。")

        scanner = TrendScanner(self.data, self.config)
        cash = initial_cash
        positions: dict[str, dict] = {}
        equity_rows = []
        trades = []

        for idx in range(65, len(dates) - 1):
            signal_date = dates[idx]
            trade_date = dates[idx + 1]
            window_start = dates[0]
            scan = scanner.scan(window_start, signal_date, board_type, top_sectors, stocks_per_sector)
            buys = scan[scan["signal"] == "买入"].sort_values("score", ascending=False)
            target_symbols = list(buys.head(self.config.max_positions)["symbol"])

            for symbol in list(positions):
                if symbol not in target_symbols:
                    price = self._price_on(symbol, window_start, trade_date)
                    if price is None:
                        continue
                    shares = positions[symbol]["shares"]
                    gross = shares * price
                    tax = gross * self.config.stamp_tax_rate
                    fee = gross * self.config.commission_rate
                    cash += gross - tax - fee
                    trades.append({"date": trade_date, "symbol": symbol, "side": "SELL", "price": price, "shares": shares, "fee": fee + tax})
                    del positions[symbol]

            slots = max(self.config.max_positions - len(positions), 0)
            for symbol in target_symbols:
                if slots <= 0 or symbol in positions:
                    continue
                price = self._price_on(symbol, window_start, trade_date)
                if price is None or price <= 0:
                    continue
                budget = cash / max(slots, 1)
                shares = int(budget / price / 100) * 100
                if shares <= 0:
                    continue
                gross = shares * price
                fee = gross * self.config.commission_rate
                if gross + fee > cash:
                    continue
                cash -= gross + fee
                positions[symbol] = {"shares": shares, "cost": price}
                trades.append({"date": trade_date, "symbol": symbol, "side": "BUY", "price": price, "shares": shares, "fee": fee})
                slots -= 1

            market_value = 0.0
            for symbol, pos in positions.items():
                price = self._price_on(symbol, window_start, trade_date)
                if price is not None:
                    market_value += pos["shares"] * price
            equity_rows.append({"date": pd.to_datetime(trade_date).strftime("%Y-%m-%d"), "cash": cash, "market_value": market_value, "equity": cash + market_value})

        equity = pd.DataFrame(equity_rows)
        trade_frame = pd.DataFrame(trades)
        metrics = self._metrics(equity, trade_frame, initial_cash)
        return BacktestResult(equity=equity, trades=trade_frame, metrics=metrics)

    def _price_on(self, symbol: str, start: str, date: str) -> float | None:
        try:
            hist = self.data.stock_history(symbol, start, date)
        except Exception:
            return None
        if hist.empty:
            return None
        row = hist[pd.to_datetime(hist["date"]).dt.strftime("%Y%m%d") <= date].tail(1)
        if row.empty:
            return None
        return float(row.iloc[0]["close"])

    def _metrics(self, equity: pd.DataFrame, trades: pd.DataFrame, initial_cash: float) -> dict:
        if equity.empty:
            return {
                "initial_cash": initial_cash,
                "final_equity": initial_cash,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "trade_count": 0,
                "turnover": 0.0,
            }
        eq = equity["equity"].astype(float)
        buys = trades[trades["side"] == "BUY"] if not trades.empty else pd.DataFrame()
        turnover = float((buys["price"] * buys["shares"]).sum() / initial_cash) if not buys.empty else 0.0
        return {
            "initial_cash": float(initial_cash),
            "final_equity": float(eq.iloc[-1]),
            "total_return": float(eq.iloc[-1] / initial_cash - 1),
            "annualized_return": annualized_return(pd.concat([pd.Series([initial_cash]), eq], ignore_index=True)),
            "max_drawdown": max_drawdown(pd.concat([pd.Series([initial_cash]), eq], ignore_index=True)),
            "sharpe": sharpe_ratio(eq),
            "trade_count": int(len(trades)),
            "turnover": turnover,
        }
