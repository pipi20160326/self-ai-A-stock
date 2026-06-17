from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.config import BACKTEST_REPORT_DIR, DAILY_REPORT_DIR, ensure_directories


def save_daily_scan(scan: pd.DataFrame, report_date: str | None = None) -> Path:
    ensure_directories()
    report_date = report_date or date.today().strftime("%Y-%m-%d")
    path = DAILY_REPORT_DIR / f"{report_date}_scan.csv"
    scan.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_backtest(equity: pd.DataFrame, trades: pd.DataFrame, metrics: dict, name: str) -> dict[str, Path]:
    ensure_directories()
    base = BACKTEST_REPORT_DIR / name
    paths = {
        "equity": base.with_name(f"{base.name}_equity.csv"),
        "trades": base.with_name(f"{base.name}_trades.csv"),
        "metrics": base.with_name(f"{base.name}_metrics.csv"),
    }
    equity.to_csv(paths["equity"], index=False, encoding="utf-8-sig")
    trades.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
    pd.DataFrame([metrics]).to_csv(paths["metrics"], index=False, encoding="utf-8-sig")
    return paths

