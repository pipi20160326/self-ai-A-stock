from __future__ import annotations

from datetime import date, timedelta
import importlib
from pathlib import Path
import time
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.config import settings
from src.daily_job import run_daily
import src.data.providers as data_providers_module
import src.data.service as data_service_module
import src.strategy.scanner as scanner_module
from src.db import (
    add_monitor_target,
    fetch_monitor_events,
    fetch_monitor_targets,
    fetch_recent_reports,
    fetch_report_html,
    fetch_sector_history,
    fetch_table,
    init_database,
    save_report_to_db,
    set_monitor_active,
)
from src.etf_utils import diversify_etf_frame, infer_etf_theme
from src.manual_score import score_sector_key, score_stock_code
from src.monitor import run_monitor
from src.reports import save_daily_scan
from src.strategy.indicators import with_indicators
from src.strategy.stock import score_stock


data_providers_module = importlib.reload(data_providers_module)
data_service_module = importlib.reload(data_service_module)
scanner_module = importlib.reload(scanner_module)
MarketDataService = data_service_module.MarketDataService
TrendScanner = scanner_module.TrendScanner
normalize_code = data_providers_module.normalize_code
retry_call = data_providers_module.retry_call


MA_OPTIONS = ["MA5", "MA10", "MA20", "MA60"]
STANCE_ORDER = {"强": 0, "中": 1, "观察": 2}


st.set_page_config(page_title="A股今日机会雷达", layout="wide")


@st.cache_resource
def service(cache_version: str = "baostock-linked-workflow-v5") -> MarketDataService:
    return MarketDataService(settings)


@st.cache_data(ttl=900)
def load_etf_candidates(prefilter: int, limit: int) -> pd.DataFrame:
    try:
        import akshare as ak

        spot = retry_call(lambda: ak.fund_etf_spot_em())
    except Exception as exc:
        return pd.DataFrame({"error": [f"ETF 列表获取失败：{exc}"]})

    etfs = spot.rename(
        columns={
            "代码": "symbol",
            "名称": "name",
            "最新价": "price",
            "涨跌幅": "pct_chg",
            "成交额": "amount",
            "换手率": "turnover",
        }
    ).copy()
    for col in ["pct_chg", "amount", "price", "turnover"]:
        if col in etfs.columns:
            etfs[col] = pd.to_numeric(etfs[col], errors="coerce")
    if "symbol" in etfs.columns:
        etfs["symbol"] = etfs["symbol"].map(normalize_code)

    etfs = etfs.dropna(subset=["symbol", "pct_chg"]).sort_values(["pct_chg", "amount"], ascending=False).head(prefilter)
    max_amount = float(etfs["amount"].max()) if "amount" in etfs.columns and pd.notna(etfs["amount"].max()) else 0.0
    etfs["score"] = 0.25 + etfs["pct_chg"].fillna(0) / 100
    if max_amount > 0 and "amount" in etfs.columns:
        etfs["score"] = etfs["score"] + etfs["amount"].fillna(0) / max_amount * 0.15
    etfs["theme"] = etfs["name"].map(infer_etf_theme) if "name" in etfs.columns else "其他"
    etfs = diversify_etf_frame(etfs, limit, max_per_theme=2)
    cols = [col for col in ["symbol", "name", "theme", "price", "pct_chg", "amount", "turnover", "score"] if col in etfs.columns]
    return etfs[cols].reset_index(drop=True)


def ymd(value: date) -> str:
    return value.strftime("%Y%m%d")


def pct(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.2%}"
    except Exception:
        return "-"


def raw_pct(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.2f}%"
    except Exception:
        return "-"


def num(value: Any, digits: int = 2) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def selected_row_index(event) -> int | None:
    try:
        rows = event.selection.rows
    except AttributeError:
        rows = event.get("selection", {}).get("rows", []) if isinstance(event, dict) else []
    if not rows:
        return None
    return int(rows[0])


def level_from_score(score: Any, signal: str = "") -> str:
    try:
        score_value = float(score)
    except Exception:
        score_value = 0.0
    if signal == "卖出":
        return "观察"
    if score_value >= 0.6:
        return "强"
    if score_value >= 0.2:
        return "中"
    return "观察"


def level_from_stance(value: Any, score: Any = 0, signal: str = "") -> str:
    text = str(value or "")
    if "强势" in text or text == "强":
        return "强"
    if "一般" in text or text == "中":
        return "中"
    if "观察" in text:
        return "观察"
    return level_from_score(score, signal)


def status_class(level: str) -> str:
    return {"强": "strong", "中": "mid", "观察": "watch"}.get(level, "watch")


def set_selected_target(target_type: str, code: str, name: str = "", row: dict | None = None) -> None:
    target_row = row or {}
    st.session_state["selected_target"] = {
        "type": target_type,
        "code": str(code),
        "name": name or str(code),
        "row": target_row,
    }
    if target_type == "sector":
        st.session_state["selected_radar_sector"] = {
            "code": str(code),
            "name": name or str(target_row.get("sector", code)),
        }


def selected_target() -> dict:
    return st.session_state.get(
        "selected_target",
        {"type": "sector", "code": "", "name": "暂无选择", "row": {}},
    )


def render_price_chart(detail: pd.DataFrame, key: str, height: int = 430) -> None:
    selected_mas = st.multiselect("均线", MA_OPTIONS, default=["MA20", "MA60"], key=f"{key}_mas")
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=detail["date"],
            open=detail["open"],
            high=detail["high"],
            low=detail["low"],
            close=detail["close"],
            name="K线",
        )
    )
    ma_colors = {"MA5": "#2563eb", "MA10": "#16a34a", "MA20": "#f97316", "MA60": "#7c3aed"}
    for ma in selected_mas:
        col = ma.lower()
        if col in detail.columns:
            fig.add_trace(go.Scatter(x=detail["date"], y=detail[col], mode="lines", name=ma, line={"color": ma_colors.get(ma)}))
    fig.update_layout(height=height, xaxis_rangeslider_visible=False, margin={"l": 10, "r": 10, "t": 28, "b": 10})
    st.plotly_chart(fig, use_container_width=True)


def render_price_detail(title: str, history: pd.DataFrame, key: str) -> None:
    if history.empty:
        st.warning(f"{title} 暂无行情数据。")
        return
    detail = with_indicators(history)
    latest = detail.iloc[-1]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新收盘", num(latest.get("close")))
    c2.metric("20日收益", pct(latest.get("ret20")))
    c3.metric("60日收益", pct(latest.get("ret60")))
    c4.metric("MA20斜率", pct(latest.get("ma20_slope")))
    render_price_chart(detail, key)
    with st.expander("查看最近 K 线数据"):
        st.dataframe(detail.tail(80), use_container_width=True)


def render_score_summary(scored: dict) -> None:
    cols = st.columns(5)
    cols[0].metric("信号", scored.get("signal", "板块评分"))
    cols[1].metric("趋势分", num(scored.get("score"), 4))
    cols[2].metric("20日", pct(scored.get("ret20")))
    cols[3].metric("60日", pct(scored.get("ret60")))
    cols[4].metric("收盘", num(scored.get("close")))
    st.write(scored.get("reason", ""))


def render_fund_flow(flow: pd.DataFrame, title: str) -> None:
    st.markdown(f"#### {title}")
    if flow.empty:
        st.info("资金流数据暂不可用，已跳过展示。")
        return
    latest = flow.tail(1).iloc[0]
    cols = st.columns(4)
    cols[0].metric("主力净流入", num(latest.get("main_net_inflow"), 0))
    cols[1].metric("主力净占比", pct((latest.get("main_net_ratio") or 0) / 100 if pd.notna(latest.get("main_net_ratio")) else None))
    cols[2].metric("大单净流入", num(latest.get("big_net_inflow"), 0))
    cols[3].metric("小单净流入", num(latest.get("small_net_inflow"), 0))
    visible = [
        col
        for col in [
            "date",
            "symbol",
            "name",
            "main_net_inflow",
            "main_net_ratio",
            "super_net_inflow",
            "big_net_inflow",
            "mid_net_inflow",
            "small_net_inflow",
            "pct_chg",
        ]
        if col in flow.columns
    ]
    st.dataframe(flow[visible].tail(30), use_container_width=True)


def rank_sectors_safe(start: str, end: str, board_type: str, limit: int, refresh: bool = False) -> pd.DataFrame:
    try:
        return scanner.rank_sectors(start, end, board_type, limit, refresh=refresh)
    except TypeError as exc:
        if "refresh" not in str(exc):
            raise
        return scanner.rank_sectors(start, end, board_type, limit)


def scan_safe(
    start: str,
    end: str,
    board_type: str,
    top_sectors: int,
    stocks_per_sector: int,
    member_limit: int | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    try:
        return scanner.scan(
            start,
            end,
            board_type,
            top_sectors,
            stocks_per_sector,
            member_limit=member_limit,
            refresh=refresh,
        )
    except TypeError as exc:
        message = str(exc)
        if "refresh" not in message and "member_limit" not in message:
            raise
        return scanner.scan(start, end, board_type, top_sectors, stocks_per_sector)


def stock_history_safe(symbol: str, start: str, end: str, refresh: bool = False) -> pd.DataFrame:
    try:
        return data.stock_history(symbol, start, end, refresh=refresh)
    except TypeError as exc:
        if "refresh" not in str(exc):
            raise
        return data.stock_history(symbol, start, end)


def etf_history_safe(symbol: str, start: str, end: str, refresh: bool = False) -> pd.DataFrame:
    if hasattr(data, "etf_history"):
        try:
            return data.etf_history(symbol, start, end, refresh=refresh)
        except TypeError as exc:
            if "refresh" not in str(exc):
                raise
            return data.etf_history(symbol, start, end)
    provider = getattr(data, "provider", None)
    if provider is not None and hasattr(provider, "etf_history"):
        return provider.etf_history(symbol, start, end)
    fresh = MarketDataService(settings)
    return fresh.etf_history(symbol, start, end, refresh=refresh)


def sector_history_safe(sector: str, start: str, end: str, board_type: str, refresh: bool = False) -> pd.DataFrame:
    try:
        return data.sector_history(sector, start, end, board_type, refresh=refresh)
    except TypeError as exc:
        if "refresh" not in str(exc):
            raise
        return data.sector_history(sector, start, end, board_type)


def score_etf_safe(symbol: str, start: str, end: str) -> tuple[dict, pd.DataFrame]:
    history = etf_history_safe(symbol, start, end)
    return score_stock(history, 0.0), history


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.2rem; }
        div[data-testid="stMetric"] {
            background: #fff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 12px 14px;
        }
        .radar-card {
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 14px;
            background: #fff;
            min-height: 154px;
            margin-bottom: 10px;
        }
        .radar-title { font-weight: 800; font-size: 1rem; margin-bottom: 2px; }
        .radar-code { color: #667085; font-size: .82rem; margin-bottom: 10px; }
        .radar-reason { color: #475467; font-size: .86rem; line-height: 1.45; margin-top: 8px; }
        .tag {
            display: inline-block;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: .78rem;
            font-weight: 800;
        }
        .tag.strong { color: #b42318; background: #fff1f1; }
        .tag.mid { color: #9a5b13; background: #fff7e8; }
        .tag.watch { color: #526173; background: #f1f4f8; }
        .note-box {
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            background: #fff;
            padding: 12px 14px;
            color: #475467;
            line-height: 1.55;
        }
        .warn-box {
            border: 1px solid #f6d58f;
            border-radius: 8px;
            background: #fffbeb;
            color: #9a5b13;
            padding: 12px 14px;
            line-height: 1.55;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def card_markup(title: str, code: str, level: str, score: Any, ret20: Any, reason: str, subtitle: str = "") -> str:
    return f"""
    <div class="radar-card">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
        <div>
          <div class="radar-title">{title}</div>
          <div class="radar-code">{code}{' · ' + subtitle if subtitle else ''}</div>
        </div>
        <span class="tag {status_class(level)}">{level}</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div><b>{num(score, 3)}</b><br><span style="color:#667085;font-size:.78rem;">趋势分</span></div>
        <div><b>{pct(ret20)}</b><br><span style="color:#667085;font-size:.78rem;">20日</span></div>
      </div>
      <div class="radar-reason">{reason}</div>
    </div>
    """


def normalize_sector_rows(ranked: pd.DataFrame, limit: int = 3) -> list[dict]:
    if ranked is None or ranked.empty:
        return []
    rows = []
    for _, row in ranked.head(limit).iterrows():
        score = row.get("score", 0)
        rows.append(
            {
                "type": "sector",
                "code": str(row.get("code", "") or row.get("sector", "")),
                "name": str(row.get("sector", "")),
                "level": level_from_score(score),
                "score": score,
                "ret20": row.get("ret20"),
                "ret60": row.get("ret60"),
                "reason": str(row.get("reason", "")),
                "row": row.to_dict(),
            }
        )
    return rows


def normalize_stock_rows(scan: pd.DataFrame, limit: int = 6) -> list[dict]:
    if scan is None or scan.empty:
        return []
    rows = []
    ordered = scan.copy()
    if "score" in ordered.columns:
        ordered = ordered.sort_values("score", ascending=False)
    for _, row in ordered.head(limit).iterrows():
        signal = str(row.get("signal", ""))
        score = row.get("score", 0)
        rows.append(
            {
                "type": "stock",
                "code": str(row.get("symbol", "")).zfill(6),
                "name": str(row.get("name", "")),
                "sector": str(row.get("sector", "")),
                "level": level_from_stance(row.get("stance"), score, signal),
                "score": score,
                "ret20": row.get("ret20"),
                "ret60": row.get("ret60"),
                "reason": str(row.get("reason", "")),
                "row": row.to_dict(),
            }
        )
    return rows


def normalize_etf_rows(etfs: pd.DataFrame, limit: int = 5) -> list[dict]:
    if etfs is None or etfs.empty or "error" in etfs.columns:
        return []
    rows = []
    ordered = diversify_etf_frame(etfs, limit, max_per_theme=2)
    if "score" in ordered.columns:
        ordered = ordered.sort_values("score", ascending=False)
    for _, row in ordered.head(limit).iterrows():
        score = row.get("score", 0)
        theme = row.get("theme") or infer_etf_theme(row.get("name", ""))
        rows.append(
            {
                "type": "etf",
                "code": str(row.get("symbol", "")).zfill(6),
                "name": str(row.get("name", "")),
                "sector": str(theme),
                "level": level_from_score(score),
                "score": score,
                "ret20": None,
                "ret60": None,
                "reason": f"实时涨幅 {raw_pct(row.get('pct_chg'))}，成交额 {num(row.get('amount'), 0)}。",
                "row": row.to_dict(),
            }
        )
    return rows


def run_radar_scan(
    scanner: TrendScanner,
    start: str,
    end: str,
    board_type: str,
    top_sectors: int,
    stocks_per_sector: int,
    etf_prefilter: int,
    top_etfs: int,
) -> None:
    progress = st.progress(0, text="准备扫描任务...")
    started = time.monotonic()
    errors: list[str] = []
    ranked = pd.DataFrame()
    scan = pd.DataFrame()
    etfs = pd.DataFrame()

    try:
        progress.progress(15, text="获取强势板块排行...")
        ranked = rank_sectors_safe(start, end, board_type, top_sectors)
    except Exception as exc:
        errors.append(f"板块排行失败：{exc}")

    try:
        progress.progress(48, text="拉取板块成分并评分个股...")
        scan = scan_safe(start, end, board_type, top_sectors, max(stocks_per_sector, 6), member_limit=settings.daily_member_limit)
        if not scan.empty:
            save_daily_scan(scan, pd.to_datetime(end).strftime("%Y-%m-%d"))
    except Exception as exc:
        errors.append(f"个股扫描失败：{exc}")

    try:
        progress.progress(76, text="筛选 ETF 候选...")
        etfs = load_etf_candidates(etf_prefilter, top_etfs)
        if "error" in etfs.columns:
            errors.append(str(etfs.iloc[0]["error"]))
    except Exception as exc:
        errors.append(f"ETF 候选失败：{exc}")

    progress.progress(100, text="整理结果和异常提示...")
    elapsed = time.monotonic() - started
    st.session_state["radar_ranked"] = ranked
    st.session_state["radar_scan"] = scan
    st.session_state["radar_etfs"] = etfs
    st.session_state["radar_errors"] = errors + scanner.data.warnings
    st.session_state["radar_elapsed"] = elapsed
    if not ranked.empty and "sector" in ranked.columns:
        sector_names = ranked["sector"].astype(str).tolist()
        current = st.session_state.get("selected_radar_sector", {})
        if not current or str(current.get("name", "")) not in sector_names:
            first = ranked.iloc[0]
            set_selected_target("sector", str(first.get("code", "") or first.get("sector", "")), str(first.get("sector", "")), first.to_dict())
    if errors and (not ranked.empty or not scan.empty or (not etfs.empty and "error" not in etfs.columns)):
        st.session_state["radar_status"] = "部分失败"
    elif errors:
        st.session_state["radar_status"] = "失败"
    else:
        st.session_state["radar_status"] = "完成"
    time.sleep(0.2)
    progress.empty()


def infer_search_type(text: str, selected: str) -> str:
    if selected != "auto":
        return selected
    code = normalize_code(text) if text.isdigit() else text
    if str(code).isdigit() and str(code).zfill(6).startswith(("5", "1")):
        return "etf"
    if str(code).isdigit() and len(str(code).zfill(6)) == 6:
        return "stock"
    return "sector"


def render_candidate_list(title: str, note: str, rows: list[dict], empty_text: str, key_prefix: str) -> None:
    if key_prefix == "stock":
        selected_sector = st.session_state.get("selected_radar_sector", {})
        sector_name = str(selected_sector.get("name", ""))
        title = f"{sector_name} 强势股 Top 6" if sector_name else "板块强势股 Top 6"
        note = "点击强势板块后，这里只展示该板块内候选；已过滤科创、30 开头、北交所和 ST。"
        empty_text = "当前板块暂无个股候选，可重新扫描或调大每板块个股数。"
    elif key_prefix == "etf":
        title = f"多方向 ETF Top {len(rows) if rows else 0}"
        note = "全市场独立筛选，同一主题默认最多展示 2 只，避免同一板块 ETF 刷屏。"
        empty_text = "暂无 ETF 候选。"
    st.markdown(f"#### {title}")
    st.caption(note)
    if not rows:
        st.info(empty_text)
        return
    for idx, item in enumerate(rows):
        st.markdown(
            card_markup(
                item["name"],
                item["code"],
                item["level"],
                item["score"],
                item.get("ret20"),
                item["reason"],
                item.get("sector", ""),
            ),
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        if c1.button("详情", key=f"{key_prefix}_detail_{idx}_{item['code']}", use_container_width=True):
            set_selected_target(item["type"], item["code"], item["name"], item["row"])
            if item["type"] == "sector":
                st.rerun()
        if c2.button("收藏", key=f"{key_prefix}_fav_{idx}_{item['code']}", use_container_width=True):
            favorites = st.session_state.setdefault("favorites", [])
            favorite_id = f"{item['type']}:{item['code']}"
            if favorite_id not in [fav["id"] for fav in favorites]:
                favorites.append({"id": favorite_id, **item})
                st.toast(f"已收藏：{item['name']}")


def render_selected_detail(data: MarketDataService, start: str, end: str, board_type: str) -> None:
    target = selected_target()
    row = target.get("row", {})
    target_type = target.get("type", "sector")
    code = str(target.get("code", ""))
    name = str(target.get("name", ""))
    level = level_from_stance(row.get("stance"), row.get("score", 0), row.get("signal", ""))

    st.markdown(f"### {name}")
    st.markdown(f"<span class='tag {status_class(level)}'>{level}</span> <span style='color:#667085'>{target_type} · {code}</span>", unsafe_allow_html=True)
    detail_tabs = st.tabs(["评分", "K线", "数据"])
    with detail_tabs[0]:
        if row:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("趋势分", num(row.get("score"), 4))
            c2.metric("20日", pct(row.get("ret20")))
            c3.metric("60日", pct(row.get("ret60")))
            c4.metric("相对强度", pct(row.get("relative_strength", row.get("relativeStrength"))))
            st.write(row.get("reason", "暂无评分理由。"))
        else:
            st.info("请选择候选或使用搜索评分。")
    with detail_tabs[1]:
        st.caption("K 线按需从行情接口获取，可写入行情缓存，但不默认写入报告数据库。")
        if not code:
            st.info("请选择个股、ETF 或板块后查看 K 线。")
        else:
            try:
                with st.spinner("正在按需获取 K 线..."):
                    if target_type == "stock":
                        history = stock_history_safe(code, start, end)
                    elif target_type == "etf":
                        history = etf_history_safe(code, start, end)
                    else:
                        sector_key = row.get("code") or name or code
                        history = sector_history_safe(str(sector_key), start, end, board_type)
                render_price_detail(f"{code} {name}", history, f"detail_{target_type}_{code}")
            except Exception as exc:
                st.error(f"K 线接口暂不可用：{exc}")
    with detail_tabs[2]:
        rows = [
            ("数据源", "Baostock 日线/指数/ETF；AKShare ETF 列表增强"),
            ("K 线存储", "按需接口获取，可缓存，不默认进入报告库"),
            ("报告存储", "HTML、候选摘要、变化提醒和生成参数入库"),
            ("异常策略", "单接口失败不阻塞整次扫描，优先展示成功结果和缓存"),
        ]
        st.dataframe(pd.DataFrame(rows, columns=["项目", "说明"]), hide_index=True, use_container_width=True)


def render_radar_workspace(data: MarketDataService, scanner: TrendScanner, board_type: str, start_date: date, end_date: date, top_sectors: int, stocks_per_sector: int, etf_prefilter: int, top_etfs: int) -> None:
    st.markdown("## 今日机会雷达")
    st.caption("极简清单先给结论；点击后再看评分、K 线和数据来源。报告沉淀，K 线按需接口获取。")

    status = st.session_state.get("radar_status", "空闲")
    ranked = st.session_state.get("radar_ranked", pd.DataFrame())
    scan = st.session_state.get("radar_scan", pd.DataFrame())
    etfs = st.session_state.get("radar_etfs", pd.DataFrame())
    errors = st.session_state.get("radar_errors", [])
    elapsed = st.session_state.get("radar_elapsed", 0.0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("报告日期", end_date.strftime("%Y-%m-%d"))
    c2.metric("市场状态", "中性偏强" if status != "失败" else "谨慎")
    c3.metric("扫描状态", status)
    c4.metric("耗时", f"{elapsed:.1f}s" if elapsed else "-")
    c5.metric("观察数量", len(st.session_state.get("favorites", [])))

    if status in {"部分失败", "失败"} and errors:
        st.markdown("<div class='warn-box'><b>数据提示</b><br>" + "<br>".join(errors[:6]) + "</div>", unsafe_allow_html=True)

    actions = st.columns([1, 1, 1, 3])
    if actions[0].button("重新扫描", type="primary", use_container_width=True):
        run_radar_scan(scanner, ymd(start_date), ymd(end_date), board_type, top_sectors, stocks_per_sector, etf_prefilter, top_etfs)
        st.rerun()
    if actions[1].button("生成报告", use_container_width=True):
        with st.spinner("正在生成报告、入库并推送..."):
            try:
                generated_path = run_daily(date.today(), force=True, notify_enabled=True)
                st.session_state["last_report_path"] = str(generated_path) if generated_path else ""
                st.success(f"报告已生成：{generated_path}")
            except Exception as exc:
                st.error(f"生成失败：{exc}")
    if actions[2].button("分享报告", use_container_width=True):
        if st.session_state.get("last_report_path"):
            st.info("分享入口已预留：后续可把报告 HTML 发布到公网只读链接。当前先使用本地 HTML 或报告中心查看。")
        else:
            st.warning("请先生成报告，再创建分享链接。")

    with st.container(border=True):
        s1, s2, s3 = st.columns([2, 1, 1])
        search_text = s1.text_input("搜索评分", placeholder="输入 600519、510300、机器人 等", label_visibility="collapsed")
        search_type = s2.selectbox("类型", ["auto", "stock", "sector", "etf"], format_func=lambda x: {"auto": "自动识别", "stock": "个股", "sector": "板块", "etf": "ETF"}[x], label_visibility="collapsed")
        if s3.button("临时评分", use_container_width=True) and search_text.strip():
            kind = infer_search_type(search_text.strip(), search_type)
            with st.spinner("正在临时评分..."):
                try:
                    if kind == "stock":
                        scored, _ = score_stock_code(data, search_text.strip(), ymd(start_date), ymd(end_date))
                        name = search_text.strip()
                    elif kind == "etf":
                        scored, _ = score_etf_safe(search_text.strip(), ymd(start_date), ymd(end_date))
                        name = search_text.strip()
                    else:
                        scored, _ = score_sector_key(data, search_text.strip(), ymd(start_date), ymd(end_date), board_type, settings)
                        name = search_text.strip()
                    row = {**scored, "reason": scored.get("reason", "临时评分结果")}
                    set_selected_target(kind, search_text.strip(), name, row)
                    st.success("临时评分已展示在右侧详情区。")
                except Exception as exc:
                    st.error(f"临时评分失败：{exc}")

    sector_rows = normalize_sector_rows(ranked, 3)
    selected_sector = st.session_state.get("selected_radar_sector", {})
    selected_sector_name = str(selected_sector.get("name", ""))
    if not selected_sector_name and sector_rows:
        selected_sector_name = sector_rows[0]["name"]
    stock_source = scan
    if selected_sector_name and isinstance(scan, pd.DataFrame) and not scan.empty and "sector" in scan.columns:
        scoped = scan[scan["sector"].astype(str).eq(selected_sector_name)].copy()
        stock_source = scoped
    stock_rows = normalize_stock_rows(stock_source, 6)
    etf_rows = normalize_etf_rows(etfs, top_etfs)

    left, right = st.columns([2.1, 1])
    with left:
        filter_tab = st.radio("候选类型", ["全部", "板块", "个股", "ETF"], horizontal=True, label_visibility="collapsed")
        columns = st.columns(3)
        with columns[0]:
            if filter_tab in {"全部", "板块"}:
                render_candidate_list("强势板块 Top 3", "先确认市场奖励的方向。", sector_rows, "暂无板块结果，点击重新扫描。", "sector")
        with columns[1]:
            if filter_tab in {"全部", "个股"}:
                render_candidate_list("板块强势股 Top 6", "已过滤科创、30 开头、北交所和 ST。", stock_rows, "暂无个股候选，点击重新扫描。", "stock")
        with columns[2]:
            if filter_tab in {"全部", "ETF"}:
                render_candidate_list("ETF 替代观察 Top 5", "不选股时，用 ETF 跟踪方向。", etf_rows, "暂无 ETF 候选。", "etf")

    with right:
        render_selected_detail(data, ymd(start_date), ymd(end_date), board_type)
        st.markdown("### 观察清单")
        favorites = st.session_state.get("favorites", [])
        if not favorites:
            st.info("还没有收藏。点击候选卡片下方的“收藏”加入观察。")
        else:
            for idx, fav in enumerate(favorites):
                row_cols = st.columns([3, 1])
                row_cols[0].write(f"**{fav['name']}**  \n{fav['type']} · {fav['code']} · {fav['level']}")
                if row_cols[1].button("移除", key=f"remove_fav_{idx}"):
                    favorites.pop(idx)
                    st.rerun()


def render_reports() -> None:
    st.subheader("报告中心")
    st.caption("报告 HTML 和候选摘要会入库；K 线详情仍按需接口获取，不默认跟随报告存库。")
    if st.button("手动生成今天报告并推送", type="primary", key="history_generate_today"):
        with st.spinner("正在刷新行情、生成今天报告、写入数据库并推送..."):
            try:
                generated_path = run_daily(date.today(), force=True, notify_enabled=True)
                if generated_path:
                    st.session_state["history_generated_path"] = str(generated_path.resolve())
                    st.session_state["history_generated_html"] = generated_path.read_text(encoding="utf-8")
                    st.success(f"报告已生成：{generated_path.resolve()}")
                else:
                    st.info("今天不是交易日，已跳过生成。")
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    if st.session_state.get("history_generated_html"):
        st.info(f"本地报告位置：{st.session_state.get('history_generated_path')}")
        components.html(st.session_state["history_generated_html"], height=900, scrolling=True)

    try:
        init_database()
        reports = fetch_recent_reports(60)
    except Exception as exc:
        reports = pd.DataFrame()
        st.error(f"读取数据库失败：{exc}")

    if reports.empty:
        st.info("数据库里还没有历史报告。可以先运行 `python -m src.daily_job --force`。")
        return

    options = {f"{row.report_date} - {row.title}": int(row.id) for row in reports.itertuples()}
    selected_label = st.selectbox("选择报告", list(options.keys()))
    selected_id = options[selected_label]
    changes = fetch_table("changes", selected_id)
    sectors = fetch_table("sectors", selected_id)
    stocks = fetch_table("stocks", selected_id)
    etfs = fetch_table("etfs", selected_id)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("板块", len(sectors))
    c2.metric("个股", len(stocks))
    c3.metric("ETF", len(etfs))
    c4.metric("变化提醒", len(changes))

    view = st.radio("查看内容", ["变化提醒", "板块趋势", "候选个股", "ETF", "原始报告", "分享"], horizontal=True)
    if view == "变化提醒":
        change_cols = [col for col in ["change_type", "target_name", "old_rank", "new_rank", "old_score", "new_score", "message"] if col in changes.columns]
        st.dataframe(changes[change_cols], use_container_width=True)
    elif view == "板块趋势":
        sector_cols = [col for col in ["rank_no", "sector", "today_pct", "score", "ret20", "ret60", "reason"] if col in sectors.columns]
        st.dataframe(sectors[sector_cols], use_container_width=True)
        history = fetch_sector_history(10)
        if not history.empty:
            top = history.groupby("sector")["score"].max().sort_values(ascending=False).head(12).index
            chart = history[history["sector"].isin(top)].copy()
            fig = go.Figure()
            for sector_name, group in chart.groupby("sector"):
                fig.add_trace(go.Scatter(x=group["report_date"], y=group["rank_no"], mode="lines+markers", name=sector_name))
            fig.update_layout(height=460, yaxis_title="排名", xaxis_title="日期", yaxis_autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)
    elif view == "候选个股":
        stock_cols = [
            col
            for col in [
                "sector_rank",
                "sector",
                "symbol",
                "name",
                "stance_text",
                "stance_score",
                "signal_text",
                "today_pct",
                "open_price",
                "close_price",
                "score",
                "ret20",
                "ret60",
                "reason",
            ]
            if col in stocks.columns
        ]
        st.dataframe(stocks[stock_cols], use_container_width=True)
    elif view == "ETF":
        etf_cols = [col for col in ["rank_no", "symbol", "name", "signal_text", "today_pct", "close_price", "score", "ret20", "ret60", "reason"] if col in etfs.columns]
        st.dataframe(etfs[etf_cols], use_container_width=True)
    elif view == "分享":
        st.markdown(
            "<div class='note-box'>分享功能已预留。后续可以把报告 HTML 上传到对象存储或静态站点，生成公网只读链接；分享页只暴露报告内容，不暴露本地路径、数据库信息或接口 token。</div>",
            unsafe_allow_html=True,
        )
    else:
        html_text = fetch_report_html(selected_id)
        components.html(html_text, height=900, scrolling=True)


def render_monitors() -> None:
    st.subheader("监控中心")
    try:
        init_database()
    except Exception as exc:
        st.error(f"初始化监控表失败：{exc}")

    with st.form("add_monitor"):
        c1, c2, c3 = st.columns(3)
        monitor_type = c1.selectbox("类型", ["stock", "sector", "etf"], format_func=lambda x: {"stock": "个股", "sector": "板块", "etf": "ETF"}[x])
        monitor_code = c2.text_input("代码/名称", placeholder="600519 / BK1625 / 510300")
        monitor_name = c3.text_input("名称", placeholder="可选")
        c4, c5 = st.columns(2)
        min_score = c4.number_input("最低趋势分", value=0.2, step=0.05)
        required_signal = c5.selectbox("要求信号", ["买入", "", "观察", "卖出"], index=0)
        note = st.text_input("备注", value="")
        submitted = st.form_submit_button("添加/更新监控")
        if submitted and monitor_code.strip():
            try:
                target_id = add_monitor_target(
                    monitor_type,
                    monitor_code.strip(),
                    monitor_name.strip(),
                    float(min_score),
                    required_signal or None,
                    note,
                )
                st.success(f"监控已保存：{target_id}")
            except Exception as exc:
                st.error(f"保存失败：{exc}")

    if st.button("立即运行监控"):
        with st.spinner("正在评估监控目标..."):
            messages = run_monitor(notify_enabled=True)
        if messages:
            st.write("\n".join(messages))
        else:
            st.info("没有触发条件的目标。")

    try:
        targets = fetch_monitor_targets()
        events = fetch_monitor_events(100)
    except Exception as exc:
        targets = pd.DataFrame()
        events = pd.DataFrame()
        st.error(f"读取监控数据失败：{exc}")
    st.markdown("#### 监控目标")
    if not targets.empty:
        st.dataframe(targets, use_container_width=True)
        disable_id = st.number_input("停用/启用目标 ID", min_value=0, value=0, step=1)
        c1, c2 = st.columns(2)
        if c1.button("启用") and disable_id:
            set_monitor_active(int(disable_id), True)
            st.success("已启用")
        if c2.button("停用") and disable_id:
            set_monitor_active(int(disable_id), False)
            st.success("已停用")
    else:
        st.info("暂无监控目标。")
    st.markdown("#### 最近触发")
    st.dataframe(events, use_container_width=True)


def render_tasks() -> None:
    st.subheader("任务中心")
    st.caption("这里的按钮会把结果写入 MySQL，之后可在“报告”和“监控”页查看。")
    task_date = st.date_input("任务日期", date.today(), key="task_date")
    c0, c1, c2, c3 = st.columns(4)

    if c0.button("生成今天报告并推送", type="primary"):
        with st.spinner("正在生成今天报告、写入数据库并推送..."):
            try:
                path = run_daily(date.today(), force=True, notify_enabled=True)
                st.success(f"已生成并推送：{path}")
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    if c1.button("按日期生成并入库"):
        with st.spinner("正在生成日报、写入数据库并运行监控..."):
            try:
                path = run_daily(task_date, force=True, notify_enabled=True)
                st.success(f"已生成并入库：{path}")
            except Exception as exc:
                st.error(f"生成失败：{exc}")

    if c2.button("只运行监控"):
        with st.spinner("正在运行监控..."):
            try:
                messages = run_monitor(task_date, notify_enabled=True)
                if messages:
                    st.success(f"触发 {len(messages)} 条")
                    st.write("\n".join(messages))
                else:
                    st.info("没有触发条件的目标。")
            except Exception as exc:
                st.error(f"监控失败：{exc}")

    html_name = f"{task_date:%Y-%m-%d}-report.html"
    html_path = Path(html_name)
    if c3.button("补录本地HTML"):
        try:
            if not html_path.exists():
                st.error(f"未找到：{html_path.resolve()}")
            else:
                report_id = save_report_to_db(task_date.strftime("%Y-%m-%d"), html_path)
                st.success(f"已补录入库：report_id={report_id}")
        except Exception as exc:
            st.error(f"补录失败：{exc}")

    st.markdown("#### 最近报告记录")
    try:
        st.dataframe(fetch_recent_reports(20), use_container_width=True)
    except Exception as exc:
        st.error(f"读取报告记录失败：{exc}")

    st.markdown("#### 最近监控触发")
    try:
        st.dataframe(fetch_monitor_events(50), use_container_width=True)
    except Exception as exc:
        st.error(f"读取监控记录失败：{exc}")


inject_styles()
data = service("baostock-linked-workflow-v4")
scanner = TrendScanner(data, settings)

with st.sidebar:
    st.markdown("### 扫描参数")
    board_type = st.radio("板块类型", ["industry", "concept"], format_func=lambda x: "行业板块" if x == "industry" else "概念板块")
    if "start_date" not in st.session_state:
        st.session_state["start_date"] = pd.to_datetime(settings.start_date).date()
    if "end_date" not in st.session_state:
        st.session_state["end_date"] = date.today()
    if st.button("日期切到昨天"):
        st.session_state["end_date"] = date.today() - timedelta(days=1)
    start_date = st.date_input("数据起点", key="start_date")
    end_date = st.date_input("工作台/报告日期", key="end_date")
    top_sectors = st.slider("关注板块数", 1, 20, settings.scan_top_sectors)
    stocks_per_sector = st.slider("每板块个股数", 1, 10, settings.scan_top_stocks_per_sector)
    etf_prefilter = st.slider("ETF 预筛数量", 10, 100, settings.daily_etf_prefilter)
    top_etfs = st.slider("ETF 展示数量", 3, 30, settings.daily_top_etfs)

tabs = st.tabs(["今日雷达", "报告中心", "监控中心", "任务中心"])

with tabs[0]:
    render_radar_workspace(data, scanner, board_type, start_date, end_date, top_sectors, stocks_per_sector, etf_prefilter, top_etfs)

with tabs[1]:
    render_reports()

with tabs[2]:
    render_monitors()

with tabs[3]:
    render_tasks()
