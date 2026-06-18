from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import time
from typing import Protocol

import pandas as pd

_REQUEST_TIMEOUT_INSTALLED = False
_BAOSTOCK_LOGGED_IN = False


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


def _yyyymmdd_to_iso(value: str) -> str:
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _market_prefix(symbol: str, sep: str = "") -> str:
    code = normalize_code(symbol)
    market = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{market}{sep}{code}"


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "\u65e5\u671f": "date",
        "\u5f00\u76d8": "open",
        "\u5f00\u76d8\u4ef7": "open",
        "\u6536\u76d8": "close",
        "\u6536\u76d8\u4ef7": "close",
        "\u6700\u9ad8": "high",
        "\u6700\u9ad8\u4ef7": "high",
        "\u6700\u4f4e": "low",
        "\u6700\u4f4e\u4ef7": "low",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u6362\u624b\u7387": "turnover",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "trade_date": "date",
        "vol": "volume",
        "turn": "turnover",
        "turnover_rate": "turnover",
        "pctChg": "pct_chg",
        "pct_chg": "pct_chg",
    }
    out = frame.rename(columns={k: v for k, v in rename_map.items() if k in frame.columns}).copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "close", "high", "low", "volume", "amount", "turnover", "pct_chg"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "date" not in out.columns:
        return pd.DataFrame()
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"] if c in out.columns]
    return out[keep].dropna(subset=["date"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def normalize_fund_flow_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(normalize_code)
    for col in out.columns:
        if col not in {"date", "symbol", "name"}:
            numeric = pd.to_numeric(out[col], errors="coerce")
            if numeric.notna().any():
                out[col] = numeric
    return out.reset_index(drop=True)


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

    def etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ...

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ...

    def stock_fund_flow(self, symbol: str) -> pd.DataFrame:
        ...

    def sector_fund_flow(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        ...


@dataclass
class BaostockProvider:
    name: str = "baostock"
    _industry_cache: pd.DataFrame | None = field(default=None, init=False, repr=False)

    def _ak(self):
        install_requests_timeout()
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("AkShare is not installed. Run pip install -r requirements.txt.") from exc
        return ak

    def _sector_code(self, sector_name: str) -> str:
        digest = hashlib.blake2b(str(sector_name).encode("utf-8"), digest_size=3).hexdigest().upper()
        return f"BSI{digest}"

    def _board_list_from_ak(self, board_type: str = "industry") -> pd.DataFrame:
        ak = self._ak()
        loader = ak.stock_board_industry_name_em if board_type == "industry" else ak.stock_board_concept_name_em
        try:
            raw = retry_call(loader)
        except Exception:
            backup = ak.stock_board_industry_name_ths if board_type == "industry" else ak.stock_board_concept_name_ths
            raw = retry_call(backup)
        if raw.empty:
            return pd.DataFrame(columns=["sector", "code"])
        out = raw.rename(
            columns={
                "板块名称": "sector",
                "板块代码": "code",
                "name": "sector",
                "code": "code",
                "涨跌幅": "pct_chg",
                "总市值": "market_value",
                "换手率": "turnover",
                "上涨家数": "up_count",
                "下跌家数": "down_count",
            }
        ).copy()
        if "sector" not in out.columns:
            return pd.DataFrame(columns=["sector", "code"])
        if "code" not in out.columns:
            out["code"] = out["sector"].map(self._sector_code)
        for col in ["pct_chg", "market_value", "turnover", "up_count", "down_count"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        keep = [c for c in ["sector", "code", "pct_chg", "market_value", "turnover", "up_count", "down_count"] if c in out.columns]
        return out[keep].dropna(subset=["sector"]).reset_index(drop=True)

    def _board_history_from_ak(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        ak = self._ak()
        symbol = self._resolve_sector_name(sector_name, board_type)
        if board_type == "industry":
            try:
                raw = retry_call(
                    lambda: ak.stock_board_industry_hist_em(
                        symbol=symbol,
                        start_date=str(start),
                        end_date=str(end),
                        period="日k",
                        adjust="",
                    )
                )
            except Exception:
                raw = retry_call(
                    lambda: ak.stock_board_industry_index_ths(
                        symbol=symbol,
                        start_date=str(start),
                        end_date=str(end),
                    )
                )
        else:
            try:
                raw = retry_call(
                    lambda: ak.stock_board_concept_hist_em(
                        symbol=symbol,
                        start_date=str(start),
                        end_date=str(end),
                        period="daily",
                        adjust="",
                    )
                )
            except Exception:
                raw = retry_call(
                    lambda: ak.stock_board_concept_index_ths(
                        symbol=symbol,
                        start_date=str(start),
                        end_date=str(end),
                    )
                )
        return normalize_price_frame(raw)

    def _board_members_from_ak(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        ak = self._ak()
        symbol = self._resolve_sector_name(sector_name, board_type)
        loader = ak.stock_board_industry_cons_em if board_type == "industry" else ak.stock_board_concept_cons_em
        raw = retry_call(lambda: loader(symbol=symbol))
        if raw.empty:
            return pd.DataFrame(columns=["symbol", "name"])
        out = raw.rename(columns={"代码": "symbol", "名称": "name"}).copy()
        if "symbol" not in out.columns:
            return pd.DataFrame(columns=["symbol", "name"])
        if "name" not in out.columns:
            out["name"] = ""
        out["symbol"] = out["symbol"].map(normalize_code)
        return out[["symbol", "name"]].dropna(subset=["symbol"]).reset_index(drop=True)

    def _bs(self):
        global _BAOSTOCK_LOGGED_IN
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError("Baostock is not installed. Run pip install -r requirements.txt.") from exc
        if not _BAOSTOCK_LOGGED_IN:
            login = retry_call(lambda: bs.login())
            if getattr(login, "error_code", "0") != "0":
                raise RuntimeError(f"Baostock login failed: {login.error_msg}")
            _BAOSTOCK_LOGGED_IN = True
        return bs

    def _query(self, query_result) -> pd.DataFrame:
        if getattr(query_result, "error_code", "0") != "0":
            raise RuntimeError(f"Baostock query failed: {query_result.error_msg}")
        rows = []
        while query_result.next():
            rows.append(query_result.get_row_data())
        return pd.DataFrame(rows, columns=query_result.fields)

    def _history(self, code: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        bs = self._bs()
        adjustflag = {"qfq": "2", "hfq": "1", "": "3", None: "3"}.get(adjust, "2")
        fields = "date,code,open,high,low,close,preclose,volume,amount,turn,pctChg"
        raw = retry_call(
            lambda: self._query(
                bs.query_history_k_data_plus(
                    code,
                    fields,
                    start_date=_yyyymmdd_to_iso(start),
                    end_date=_yyyymmdd_to_iso(end),
                    frequency="d",
                    adjustflag=adjustflag,
                )
            )
        )
        return normalize_price_frame(raw)

    def _industry(self) -> pd.DataFrame:
        if self._industry_cache is not None:
            return self._industry_cache.copy()
        bs = self._bs()
        raw = retry_call(lambda: self._query(bs.query_stock_industry()))
        if raw.empty:
            self._industry_cache = pd.DataFrame(columns=["symbol", "name", "sector", "classification"])
            return self._industry_cache.copy()
        out = raw.rename(
            columns={
                "code": "bs_code",
                "code_name": "name",
                "industry": "sector",
                "industryClassification": "classification",
            }
        )
        out["symbol"] = out["bs_code"].astype(str).str.split(".").str[-1].map(normalize_code)
        out = out.dropna(subset=["sector"])
        out = out[out["sector"].astype(str).str.strip().ne("")]
        self._industry_cache = out[["symbol", "name", "sector", "classification"]].reset_index(drop=True)
        return self._industry_cache.copy()

    def list_sectors(self, board_type: str = "industry") -> pd.DataFrame:
        try:
            boards = self._board_list_from_ak(board_type)
            if not boards.empty:
                return boards
        except Exception:
            if board_type != "industry":
                return pd.DataFrame(columns=["sector", "code"])

        industry = self._industry()
        if industry.empty:
            return pd.DataFrame(columns=["sector", "code"])
        out = industry.groupby("sector", as_index=False).size().rename(columns={"size": "stock_count"})
        out["code"] = out["sector"].map(self._sector_code)
        return out.sort_values("stock_count", ascending=False).reset_index(drop=True)

    def _resolve_sector_name(self, sector_name: str, board_type: str = "industry") -> str:
        key = str(sector_name).strip()
        if not key:
            return key
        sectors = self.list_sectors(board_type)
        if sectors.empty:
            return key

        code_match = sectors[sectors["code"].astype(str).str.upper().eq(key.upper())]
        if not code_match.empty:
            return str(code_match.iloc[0]["sector"])

        exact_match = sectors[sectors["sector"].astype(str).eq(key)]
        if not exact_match.empty:
            return str(exact_match.iloc[0]["sector"])

        keyword_match = sectors[sectors["sector"].astype(str).str.contains(key, case=False, regex=False, na=False)]
        if not keyword_match.empty:
            return str(keyword_match.iloc[0]["sector"])

        return key

    def sector_members(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        try:
            members = self._board_members_from_ak(sector_name, board_type)
            if not members.empty:
                return members
        except Exception:
            if board_type != "industry":
                return pd.DataFrame(columns=["symbol", "name"])

        industry = self._industry()
        resolved = self._resolve_sector_name(sector_name, board_type)
        out = industry[industry["sector"].astype(str).eq(str(resolved))].copy()
        return out[[c for c in ["symbol", "name"] if c in out.columns]].reset_index(drop=True)

    def sector_history(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        try:
            hist = self._board_history_from_ak(sector_name, start, end, board_type)
            if not hist.empty:
                return hist
        except Exception:
            if board_type != "industry":
                raise

        members = self.sector_members(sector_name, board_type)
        if members.empty:
            raise RuntimeError(f"No Baostock members found for sector: {sector_name}")
        limit = int(os.getenv("BAOSTOCK_SECTOR_MEMBER_LIMIT", "8"))
        frames = []
        for symbol in members["symbol"].head(limit):
            hist = self.stock_history(str(symbol), start, end)
            if hist.empty or "close" not in hist.columns:
                continue
            base = hist["close"].dropna()
            if base.empty or float(base.iloc[0]) == 0:
                continue
            normalized = hist.copy()
            first_close = float(base.iloc[0])
            for col in ["open", "high", "low", "close"]:
                if col in normalized.columns:
                    normalized[col] = normalized[col] / first_close * 100
            frames.append(normalized)
        if not frames:
            raise RuntimeError(f"No usable Baostock history for sector: {sector_name}")

        combined = pd.concat(frames, ignore_index=True)
        aggregations = {
            "open": "mean",
            "high": "mean",
            "low": "mean",
            "close": "mean",
            "volume": "sum",
            "amount": "sum",
        }
        available = {key: value for key, value in aggregations.items() if key in combined.columns}
        out = combined.groupby("date", as_index=False).agg(available)
        if "close" in out.columns:
            out["pct_chg"] = out["close"].pct_change() * 100
        return normalize_price_frame(out)

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        return self._history(_market_prefix(symbol, "."), start, end, adjust)

    def etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self._history(_market_prefix(symbol, "."), start, end, "")

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        code = normalize_code(symbol)
        return self._history(f"sh.{code}", start, end, "")

    def stock_fund_flow(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def sector_fund_flow(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        return pd.DataFrame()


@dataclass
class AkShareProvider:
    name: str = "akshare"

    def _ak(self):
        install_requests_timeout()
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("AkShare is not installed. Run pip install -r requirements.txt.") from exc
        return ak

    def list_sectors(self, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("AkShare provider is kept for compatibility; use DATA_PROVIDER=baostock.")

    def sector_history(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("AkShare provider is kept for compatibility; use DATA_PROVIDER=baostock.")

    def sector_members(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("AkShare provider is kept for compatibility; use DATA_PROVIDER=baostock.")

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        ak = self._ak()
        raw = retry_call(lambda: ak.stock_zh_a_hist(symbol=normalize_code(symbol), period="daily", start_date=start, end_date=end, adjust=adjust))
        return normalize_price_frame(raw)

    def etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ak = self._ak()
        raw = retry_call(lambda: ak.fund_etf_hist_em(symbol=normalize_code(symbol), period="daily", start_date=start, end_date=end, adjust=""))
        return normalize_price_frame(raw)

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        ak = self._ak()
        raw = retry_call(lambda: ak.index_zh_a_hist(symbol=normalize_code(symbol), period="daily", start_date=start, end_date=end))
        return normalize_price_frame(raw)

    def stock_fund_flow(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def sector_fund_flow(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        return pd.DataFrame()


@dataclass
class TushareProvider:
    token: str
    name: str = "tushare"

    def _pro(self):
        if not self.token:
            raise RuntimeError("Tushare token is not configured.")
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError("Tushare is not installed. Run pip install -r requirements.txt.") from exc
        ts.set_token(self.token)
        return ts.pro_api()

    def list_sectors(self, board_type: str = "industry") -> pd.DataFrame:
        pro = self._pro()
        raw = pro.ths_index(type="I" if board_type == "industry" else "N")
        return raw.rename(columns={"ts_code": "code", "name": "sector"})[["sector", "code"]]

    def sector_history(self, sector_name: str, start: str, end: str, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("Tushare sector history is not adapted.")

    def sector_members(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        raise NotImplementedError("Tushare sector members are not adapted.")

    def stock_history(self, symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
        pro = self._pro()
        ts_code = f"{normalize_code(symbol)}.SH" if normalize_code(symbol).startswith("6") else f"{normalize_code(symbol)}.SZ"
        raw = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        raw = raw.rename(columns={"trade_date": "date", "vol": "volume"})
        return normalize_price_frame(raw)

    def etf_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("Tushare ETF history is not adapted.")

    def benchmark_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        pro = self._pro()
        raw = pro.index_daily(ts_code=f"{normalize_code(symbol)}.SH", start_date=start, end_date=end)
        raw = raw.rename(columns={"trade_date": "date", "vol": "volume"})
        return normalize_price_frame(raw)

    def stock_fund_flow(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def sector_fund_flow(self, sector_name: str, board_type: str = "industry") -> pd.DataFrame:
        return pd.DataFrame()
