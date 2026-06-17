from __future__ import annotations

import pandas as pd

from .indicators import with_indicators


def score_sector(history: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict:
    if history.empty or len(history) < 65:
        return {"score": float("-inf"), "reason": "历史数据不足"}
    data = with_indicators(history).dropna(subset=["ma20", "ma60", "ret20", "ret60"])
    if data.empty:
        return {"score": float("-inf"), "reason": "指标数据不足"}
    latest = data.iloc[-1]
    amount_boost = latest["amount20"] / latest["amount60"] - 1 if latest.get("amount60", 0) else 0
    relative = 0.0
    if benchmark is not None and not benchmark.empty and len(benchmark) >= 65:
        bench = with_indicators(benchmark).dropna(subset=["ret20"])
        if not bench.empty:
            relative = float(latest["ret20"] - bench.iloc[-1]["ret20"])
    ma_bonus = 0.12 if latest["ma20"] > latest["ma60"] and latest["close"] > latest["ma20"] else -0.12
    score = (
        float(latest["ret20"]) * 0.35
        + float(latest["ret60"]) * 0.25
        + float(latest["ma20_slope"]) * 1.2
        + float(relative) * 0.25
        + float(amount_boost) * 0.08
        + ma_bonus
    )
    reasons = []
    if latest["close"] > latest["ma20"] > latest["ma60"]:
        reasons.append("多头排列")
    if latest["ma20_slope"] > 0:
        reasons.append("MA20上行")
    if relative > 0:
        reasons.append("强于沪深300")
    if amount_boost > 0:
        reasons.append("成交额放大")
    return {
        "score": score,
        "close": float(latest["close"]),
        "ret20": float(latest["ret20"]),
        "ret60": float(latest["ret60"]),
        "ma20_slope": float(latest["ma20_slope"]),
        "relative_strength": relative,
        "amount_boost": float(amount_boost),
        "reason": "、".join(reasons) or "趋势一般",
    }


def market_is_healthy(benchmark: pd.DataFrame) -> bool:
    if benchmark.empty or len(benchmark) < 65:
        return True
    latest = with_indicators(benchmark).dropna(subset=["ma20", "ma60"]).iloc[-1]
    return bool(latest["close"] > latest["ma60"] and latest["ma20_slope"] >= -0.01)

