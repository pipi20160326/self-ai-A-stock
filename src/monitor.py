from __future__ import annotations

from datetime import date

import pandas as pd

from src.config import settings
from src.data import MarketDataService
from src.data.providers import normalize_price_frame
from src.db import fetch_monitor_targets, save_monitor_event
from src.manual_score import score_sector_key, score_stock_code
from src.notifier import notify
from src.strategy.stock import score_stock


def retry_call(func, attempts: int = 5, delay: float = 1.5):
    import time

    last_error = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay * (attempt + 1))
    raise last_error


def score_etf(symbol: str, start: str, end: str) -> tuple[dict, pd.DataFrame]:
    import akshare as ak

    raw = retry_call(lambda: ak.fund_etf_hist_em(symbol=symbol.strip(), period="daily", start_date=start, end_date=end, adjust=""))
    history = normalize_price_frame(raw)
    return score_stock(history, 0.0), history


def score_monitor_target(data: MarketDataService, target: dict, start: str, end: str) -> tuple[dict, pd.DataFrame]:
    target_type = target["target_type"]
    code = target["code"]
    if target_type == "stock":
        return score_stock_code(data, code, start, end)
    if target_type == "sector":
        return score_sector_key(data, code, start, end, "industry", settings)
    if target_type == "etf":
        return score_etf(code, start, end)
    raise ValueError(f"未知监控类型: {target_type}")


def is_triggered(target: dict, scored: dict) -> bool:
    min_score = target.get("min_score")
    required_signal = target.get("required_signal")
    score = scored.get("score")
    if min_score is not None and score is not None and float(score) < float(min_score):
        return False
    if required_signal and scored.get("signal") != required_signal:
        return False
    return True


def run_monitor(report_date: date | None = None, notify_enabled: bool = True) -> list[str]:
    day = report_date or date.today()
    end = day.strftime("%Y%m%d")
    data = MarketDataService(settings)
    targets = fetch_monitor_targets(active_only=True)
    messages: list[str] = []
    for target in targets.to_dict("records"):
        try:
            scored, _ = score_monitor_target(data, target, settings.start_date, end)
            if not is_triggered(target, scored):
                continue
            message = (
                f"{target['target_type']} {target['code']} {target.get('name') or ''} "
                f"触发监控: signal={scored.get('signal', '板块评分')}, score={float(scored.get('score', 0)):.4f}, "
                f"reason={scored.get('reason', '')}"
            )
            save_monitor_event(target, day.strftime("%Y-%m-%d"), scored, message)
            messages.append(message)
        except Exception as exc:
            messages.append(f"{target['target_type']} {target['code']} 监控失败: {exc}")
    if notify_enabled and messages:
        notify(day.strftime("%Y-%m-%d"), settings.cache_path, messages)
    return messages


def main() -> None:
    messages = run_monitor()
    for message in messages:
        print(message)


if __name__ == "__main__":
    main()
