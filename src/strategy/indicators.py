from __future__ import annotations

import numpy as np
import pandas as pd


def with_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    amount = out.get("amount", pd.Series(index=out.index, dtype=float)).astype(float)
    out["ma20"] = close.rolling(20, min_periods=20).mean()
    out["ma60"] = close.rolling(60, min_periods=60).mean()
    out["ret20"] = close.pct_change(20)
    out["ret60"] = close.pct_change(60)
    out["high20"] = close.rolling(20, min_periods=20).max()
    out["drawdown20"] = close / close.rolling(20, min_periods=20).max() - 1
    out["amount20"] = amount.rolling(20, min_periods=5).mean()
    out["amount60"] = amount.rolling(60, min_periods=20).mean()
    out["ma20_slope"] = out["ma20"].pct_change(5)
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    out["atr14"] = true_range.rolling(14, min_periods=14).mean()
    return out


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    return float(drawdown.min())


def annualized_return(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    total = equity.iloc[-1] / equity.iloc[0] - 1
    years = max(len(equity) / 244, 1 / 244)
    return float((1 + total) ** (1 / years) - 1)


def sharpe_ratio(equity: pd.Series) -> float:
    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(244))

