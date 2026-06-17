from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Protocol

import pandas as pd

_REQUEST_TIMEOUT_INSTALLED = False


def install_requests_timeout() -> None:
    global _REQUEST_TIMEOUT_INSTALLED
    if _REQUEST_TIMEOUT_INSTALLED:
        return
    try:
        import requests
    except ImportError:
        return
    original = requests.sessions.Session.request
    timeout = float(os.getenv("DATA_REQUEST_TIMEOUT", "15"))

    def request_with_timeout(self, method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(self, method, url, **kwargs)

    requests.sessions.Session.request = request_with_timeout
    _REQUEST_TIMEOUT_INSTALLED = True


def normalize_code(code: str) -> str:
    return str(code).split(".")[0].zfill(6)


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover",
        "涨跌幅": "pct_chg",
    }
    out = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns}).copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for col in ["open", "close", "high", "low", "volume", "amount", "turnover", "pct_chg"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _market_prefix(symbol: str) -> str:
    code = normalize_code(symbol)
    return f"sh{code}" if code.startswith(("5", "6", "9")) else f"sz{code}"


def retry_call(func, attempts: int | None = None, delay: float | None = None):
    attempts = attempts or int(os.getenv("DATA_RETRY_ATTEMPTS", "2"))
    delay = delay if delay is not None else float(os.getenv("DATA_RETRY_DELAY", "1"))
    last_error = None
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay * (attempt + 1))
    raise last_error


class MarketDataProvider(Protocol):
    name: str

    def list_sectors(self, board_type: str = "industry") -> pd.DataFrame:
        ...

    def sector_history(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        ...

    def sector_members(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        ...

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        ...

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ...


@dataclass
class AkShareProvider:
    name: str = "akshare"

    def _ak(self):
        install_requests_timeout()
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("AkShare 未安装，请先运行 pip install -r requirements.txt") from exc
        return ak

    def list_sectors(self, board_type: str = "industry") -> pd.DataFrame:
        ak = self._ak()
        raw = retry_call(lambda: ak.stock_board_concept_name_em() if board_type == "concept" else ak.stock_board_industry_name_em())
        out = raw.rename(
            columns={
                "板块名称": "sector",
                "板块代码": "code",
                "涨跌幅": "pct_chg",
                "总市值": "market_cap",
                "换手率": "turnover",
                "上涨家数": "advancers",
                "下跌家数": "decliners",
            }
        )
        return out[[c for c in ["sector", "code", "pct_chg", "market_cap", "turnover", "advancers", "decliners"] if c in out.columns]]

    def sector_history(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        ak = self._ak()
        fn = ak.stock_board_concept_hist_em if board_type == "concept" else ak.stock_board_industry_hist_em
        raw = retry_call(lambda: fn(symbol=sector_name, start_date=start, end_date=end, period="日k", adjust=""))
        return normalize_price_frame(raw)

    def sector_members(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        ak = self._ak()
        fn = ak.stock_board_concept_cons_em if board_type == "concept" else ak.stock_board_industry_cons_em
        raw = retry_call(lambda: fn(symbol=sector_name))
        out = raw.rename(
            columns={
                "代码": "symbol",
                "名称": "name",
                "最新价": "price",
                "涨跌幅": "pct_chg",
                "成交额": "amount",
                "换手率": "turnover",
            }
        )
        if "symbol" in out.columns:
            out["symbol"] = out["symbol"].map(normalize_code)
        return out[[c for c in ["symbol", "name", "price", "pct_chg", "amount", "turnover"] if c in out.columns]]

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        ak = self._ak()
        try:
            raw = retry_call(lambda: ak.stock_zh_a_hist(symbol=normalize_code(symbol), period="daily", start_date=start, end_date=end, adjust=adjust))
            return normalize_price_frame(raw)
        except Exception:
            raw = retry_call(lambda: ak.stock_zh_a_daily(symbol=_market_prefix(symbol), start_date=start, end_date=end, adjust=adjust))
            return normalize_price_frame(raw)

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ak = self._ak()
        code = normalize_code(symbol)
        try:
            raw = retry_call(lambda: ak.index_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end))
            return normalize_price_frame(raw)
        except Exception:
            pass
        try:
            raw = retry_call(lambda: ak.stock_zh_index_daily_em(symbol=f"sh{code}", start_date=start, end_date=end))
            return normalize_price_frame(raw)
        except Exception:
            raw = retry_call(lambda: ak.stock_zh_index_daily(symbol=f"sh{code}"))
            frame = normalize_price_frame(raw)
            frame["date_key"] = pd.to_datetime(frame["date"]).dt.strftime("%Y%m%d")
            frame = frame[(frame["date_key"] >= start) & (frame["date_key"] <= end)].drop(columns=["date_key"])
            return frame.reset_index(drop=True)


@dataclass
class TushareProvider:
    token: str
    name: str = "tushare"

    def _pro(self):
        if not self.token:
            raise RuntimeError("Tushare token 未配置，请设置 TUSHARE_TOKEN 或使用 AkShare。")
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError("Tushare 未安装，请先运行 pip install -r requirements.txt") from exc
        ts.set_token(self.token)
        return ts.pro_api()

    def list_sectors(self, board_type: str = "industry") -> pd.DataFrame:
        pro = self._pro()
        raw = pro.ths_index(type="I" if board_type == "industry" else "N")
        return raw.rename(columns={"ts_code": "code", "name": "sector"})[["sector", "code"]]

    def sector_history(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("Tushare 板块历史行情需按账户权限适配；当前自动降级请使用 DATA_PROVIDER=auto。")

    def sector_members(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("Tushare 板块成分需按账户权限适配；当前自动降级请使用 DATA_PROVIDER=auto。")

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        pro = self._pro()
        ts_code = f"{normalize_code(symbol)}.SH" if normalize_code(symbol).startswith("6") else f"{normalize_code(symbol)}.SZ"
        raw = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        raw = raw.rename(columns={"trade_date": "date", "vol": "volume"})
        raw["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
        return normalize_price_frame(raw)

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        pro = self._pro()
        raw = pro.index_daily(ts_code=f"{normalize_code(symbol)}.SH", start_date=start, end_date=end)
        raw = raw.rename(columns={"trade_date": "date", "vol": "volume"})
        raw["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
        return normalize_price_frame(raw)
