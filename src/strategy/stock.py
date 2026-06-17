from __future__ import annotations

import pandas as pd

from .indicators import with_indicators


def score_stock(history: pd.DataFrame, sector_score: float = 0.0) -> dict:
    if history.empty or len(history) < 65:
        return {"score": float("-inf"), "signal": "观察", "reason": "历史数据不足"}
    data = with_indicators(history).dropna(subset=["ma20", "ma60", "ret20", "atr14"])
    if data.empty:
        return {"score": float("-inf"), "signal": "观察", "reason": "指标数据不足"}
    latest = data.iloc[-1]
    prev = data.iloc[-2] if len(data) >= 2 else None
    today_pct = float(latest["close"] / prev["close"] - 1) if prev is not None and prev["close"] else 0.0
    prev_high20 = data["high20"].shift(1).iloc[-1]
    amount_boost = latest["amount20"] / latest["amount60"] - 1 if latest.get("amount60", 0) else 0
    breakout = bool(latest["close"] >= prev_high20) if pd.notna(prev_high20) else False
    buy = latest["close"] > latest["ma20"] > latest["ma60"] and latest["ma20_slope"] > 0
    sell = latest["close"] < latest["ma20"] or latest["close"] < latest["ma60"]
    if sell:
        signal = "卖出"
    elif buy:
        signal = "买入" if breakout or amount_boost > 0 else "观察"
    else:
        signal = "观察"
    score = (
        float(latest["ret20"]) * 0.35
        + float(latest["ret60"]) * 0.2
        + float(latest["ma20_slope"]) * 1.1
        + float(amount_boost) * 0.08
        + (0.12 if breakout else 0)
        + min(max(float(latest["drawdown20"]), -0.3), 0) * -0.2
        + sector_score * 0.2
    )
    reasons = []
    if latest["close"] > latest["ma20"] > latest["ma60"]:
        reasons.append("站上MA20且多头")
    if latest["ma20_slope"] > 0:
        reasons.append("MA20上行")
    if breakout:
        reasons.append("突破20日新高")
    if amount_boost > 0:
        reasons.append("成交额放大")
    if sell:
        reasons.append("跌破趋势线")
    return {
        "score": score,
        "signal": signal,
        "close": float(latest["close"]),
        "today_pct": today_pct,
        "ret20": float(latest["ret20"]),
        "ret60": float(latest["ret60"]),
        "ma20": float(latest["ma20"]),
        "ma60": float(latest["ma60"]),
        "atr14": float(latest["atr14"]),
        "reason": "、".join(reasons) or "等待趋势确认",
    }
