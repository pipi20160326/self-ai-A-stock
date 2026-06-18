from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORT_DIR = ROOT_DIR / "reports"
DAILY_REPORT_DIR = REPORT_DIR / "daily"
BACKTEST_REPORT_DIR = REPORT_DIR / "backtest"
ENV_FILE = ROOT_DIR / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_provider: str = os.getenv("DATA_PROVIDER", "auto").lower()
    tushare_token: str = os.getenv("TUSHARE_TOKEN", "")
    scan_top_sectors: int = int(os.getenv("SCAN_TOP_SECTORS", "5"))
    scan_top_stocks_per_sector: int = int(os.getenv("SCAN_TOP_STOCKS_PER_SECTOR", "3"))
    market_filter: bool = _env_bool("MARKET_FILTER", True)
    start_date: str = os.getenv("START_DATE", "20200101")
    initial_cash: float = float(os.getenv("INITIAL_CASH", "1000000"))
    commission_rate: float = float(os.getenv("COMMISSION_RATE", "0.0003"))
    stamp_tax_rate: float = float(os.getenv("STAMP_TAX_RATE", "0.0005"))
    max_positions: int = int(os.getenv("MAX_POSITIONS", "10"))
    max_stocks_per_sector: int = int(os.getenv("MAX_STOCKS_PER_SECTOR", "3"))
    benchmark_symbol: str = os.getenv("BENCHMARK_SYMBOL", "000300")
    daily_prefilter: int = int(os.getenv("DAILY_PREFILTER", "40"))
    daily_lookback_days: int = int(os.getenv("DAILY_LOOKBACK_DAYS", "240"))
    daily_top_sectors: int = int(os.getenv("DAILY_TOP_SECTORS", "12"))
    daily_stocks_per_sector: int = int(os.getenv("DAILY_STOCKS_PER_SECTOR", "3"))
    daily_member_limit: int = int(os.getenv("DAILY_MEMBER_LIMIT", "20"))
    daily_etf_prefilter: int = int(os.getenv("DAILY_ETF_PREFILTER", "30"))
    daily_top_etfs: int = int(os.getenv("DAILY_TOP_ETFS", "10"))
    daily_refresh: bool = _env_bool("DAILY_REFRESH", False)
    cache_path: Path = CACHE_DIR / "market_cache.sqlite3"


def ensure_directories() -> None:
    for path in (CACHE_DIR, DAILY_REPORT_DIR, BACKTEST_REPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)


settings = Settings()
