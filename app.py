from __future__ import annotations

from datetime import date, timedelta
import importlib
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.config import settings
from src.daily_job import run_daily
import src.data.providers as data_providers_module
import src.data.service as data_service_module
import src.db as db_api
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


st.set_page_config(page_title="A股板块趋势工作台", layout="wide")


@st.cache_resource
def service(cache_version: str = "baostock-linked-workflow-v3") -> MarketDataService:
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
    cols = [col for col in ["symbol", "name", "price", "pct_chg", "amount", "turnover", "score"] if col in etfs.columns]
    return etfs[cols].head(limit).reset_index(drop=True)


def ymd(value) -> str:
    return value.strftime("%Y%m%d")


def pct(value) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.2%}"
    except Exception:
        return "-"


def num(value, digits: int = 2) -> str:
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


def set_selected_sector(sector: str, code: str = "") -> None:
    if st.session_state.get("selected_sector") != sector:
        st.session_state.pop("selected_stock", None)
        st.session_state.pop("selected_stock_name", None)
    st.session_state["selected_sector"] = sector
    st.session_state["selected_sector_code"] = code


def render_price_chart(detail: pd.DataFrame, key: str, height: int = 540) -> None:
    selected_mas = st.multiselect("均线", MA_OPTIONS, default=MA_OPTIONS, key=f"{key}_mas")
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
    st.markdown(f"#### {title}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最新收盘", num(latest.get("close")))
    c2.metric("20日收益", pct(latest.get("ret20")))
    c3.metric("60日收益", pct(latest.get("ret60")))
    c4.metric("MA20斜率", pct(latest.get("ma20_slope")))
    render_price_chart(detail, key)
    st.dataframe(detail.tail(80), use_container_width=True)


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


def render_score_summary(scored: dict) -> None:
    cols = st.columns(5)
    cols[0].metric("信号", scored.get("signal", "板块评分"))
    cols[1].metric("趋势分", num(scored.get("score"), 4))
    cols[2].metric("20日", pct(scored.get("ret20")))
    cols[3].metric("60日", pct(scored.get("ret60")))
    cols[4].metric("收盘", num(scored.get("close")))
    st.write(scored.get("reason", ""))


def get_database_overview() -> dict[str, int | str | None]:
    if hasattr(db_api, "fetch_database_overview"):
        return db_api.fetch_database_overview()
    init_database()
    with db_api.mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    count(*) as report_count,
                    min(report_date) as first_report_date,
                    max(report_date) as latest_report_date,
                    coalesce(sum(sector_count), 0) as sector_rows,
                    coalesce(sum(stock_count), 0) as stock_rows,
                    coalesce(sum(etf_count), 0) as etf_rows
                from reports
                """
            )
            row = cur.fetchone() or {}
            cur.execute("select count(*) as event_count from monitor_events")
            events = cur.fetchone() or {}
    return {
        "report_count": int(row.get("report_count") or 0),
        "first_report_date": str(row.get("first_report_date")) if row.get("first_report_date") else None,
        "latest_report_date": str(row.get("latest_report_date")) if row.get("latest_report_date") else None,
        "sector_rows": int(row.get("sector_rows") or 0),
        "stock_rows": int(row.get("stock_rows") or 0),
        "etf_rows": int(row.get("etf_rows") or 0),
        "monitor_event_count": int(events.get("event_count") or 0),
    }


def remove_report(report_id: int) -> None:
    if hasattr(db_api, "delete_report"):
        db_api.delete_report(report_id)
        return
    init_database()
    with db_api.mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from reports where id=%s", (report_id,))


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


def score_etf_safe(symbol: str, start: str, end: str) -> tuple[dict, pd.DataFrame]:
    history = etf_history_safe(symbol, start, end)
    return score_stock(history, 0.0), history


def stock_fund_flow_safe(symbol: str) -> pd.DataFrame:
    if hasattr(data, "stock_fund_flow"):
        return data.stock_fund_flow(symbol)
    return pd.DataFrame()


def sector_fund_flow_safe(sector: str, board_type: str) -> pd.DataFrame:
    if hasattr(data, "sector_fund_flow"):
        return data.sector_fund_flow(sector, board_type)
    return pd.DataFrame()


def load_workspace_for_date(
    scan_date: date,
    board_type: str,
    top_sectors: int,
    stocks_per_sector: int,
    force_refresh: bool = False,
) -> None:
    start = ymd(st.session_state["start_date"])
    end = ymd(scan_date)
    ranked = rank_sectors_safe(start, end, board_type, top_sectors, refresh=force_refresh)
    st.session_state["ranked"] = ranked
    if not ranked.empty:
        set_selected_sector(str(ranked.iloc[0]["sector"]), str(ranked.iloc[0].get("code", "")))
    scan = scan_safe(
        start,
        end,
        board_type,
        top_sectors,
        stocks_per_sector,
        member_limit=settings.daily_member_limit,
        refresh=force_refresh,
    )
    st.session_state["scan"] = scan
    st.session_state["workspace_loaded_date"] = scan_date.strftime("%Y-%m-%d")
    if not scan.empty:
        save_daily_scan(scan, scan_date.strftime("%Y-%m-%d"))


data = service("baostock-linked-workflow-v3")
scanner = TrendScanner(data, settings)

st.title("A股板块趋势工作台")
st.caption("先选强势板块，再看板块内强势个股；个股不合适时，用 ETF 候选做替代观察。仅用于研究，不构成投资建议。")

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

tabs = st.tabs(["工作台", "报告", "监控", "任务中心"])

with tabs[0]:
    st.subheader("工作台")
    c1, c2, c3 = st.columns([1, 1, 1])
    load_workspace = c1.button("加载所选日期工作台", type="primary")
    refresh_from_baostock = c2.button("从BaoStock强制刷新")
    refresh_rank = c3.button("只加载板块排行")

    if load_workspace or refresh_from_baostock:
        with st.spinner("正在准备板块和个股工作台数据..."):
            try:
                load_workspace_for_date(end_date, board_type, top_sectors, stocks_per_sector, force_refresh=refresh_from_baostock)
                source = "BaoStock最新数据" if refresh_from_baostock else "本地缓存优先"
                st.success(f"{end_date:%Y-%m-%d} 工作台已加载：{source}")
            except Exception as exc:
                st.error(f"工作台加载失败：{exc}")
    elif refresh_rank:
        with st.spinner("正在计算强势板块..."):
            try:
                ranked = rank_sectors_safe(ymd(start_date), ymd(end_date), board_type, top_sectors)
                st.session_state["ranked"] = ranked
                st.session_state["workspace_loaded_date"] = end_date.strftime("%Y-%m-%d")
                if not ranked.empty:
                    set_selected_sector(str(ranked.iloc[0]["sector"]), str(ranked.iloc[0].get("code", "")))
            except Exception as exc:
                st.error(f"板块排行失败：{exc}")

    if st.session_state.get("workspace_loaded_date"):
        st.caption(f"当前工作台数据日期：{st.session_state['workspace_loaded_date']}")

    ranked = st.session_state.get("ranked")
    scan = st.session_state.get("scan")

    st.markdown("### 1. 选强势板块")
    if isinstance(ranked, pd.DataFrame) and not ranked.empty:
        sector_cols = [col for col in ["sector", "code", "score", "ret20", "ret60", "relative_strength", "reason"] if col in ranked.columns]
        sector_event = st.dataframe(
            ranked[sector_cols],
            use_container_width=True,
            hide_index=True,
            key="sector_rank_table",
            on_select="rerun",
            selection_mode="single-row",
        )
        sector_idx = selected_row_index(sector_event)
        if sector_idx is not None:
            selected = ranked.iloc[sector_idx]
            set_selected_sector(str(selected["sector"]), str(selected.get("code", "")))
        selected_sector = st.session_state.get("selected_sector", str(ranked.iloc[0]["sector"]))
        st.success(f"当前板块：{selected_sector}")
        render_fund_flow(sector_fund_flow_safe(selected_sector, board_type), "板块资金流")
    else:
        selected_sector = ""
        st.info("点击“刷新工作台”开始。")

    st.markdown("### 2. 从强势板块选强势股票")
    stock_candidates = pd.DataFrame()
    if selected_sector and isinstance(scan, pd.DataFrame) and not scan.empty:
        stock_candidates = scan[scan["sector"].astype(str).eq(str(selected_sector))].copy()
        if stock_candidates.empty:
            st.info("当前板块还没有筛出的个股候选，可以刷新工作台或扩大每板块个股数。")
        else:
            stock_cols = [
                col
                for col in [
                    "sector_rank",
                    "sector",
                    "symbol",
                    "name",
                    "stance",
                    "stance_score",
                    "signal",
                    "today_pct",
                    "score",
                    "ret20",
                    "ret60",
                    "reason",
                ]
                if col in stock_candidates.columns
            ]
            stock_event = st.dataframe(
                stock_candidates[stock_cols],
                use_container_width=True,
                hide_index=True,
                key="stock_candidate_table",
                on_select="rerun",
                selection_mode="single-row",
            )
            stock_idx = selected_row_index(stock_event)
            if stock_idx is not None:
                selected = stock_candidates.iloc[stock_idx]
                st.session_state["selected_stock"] = str(selected["symbol"]).zfill(6)
                st.session_state["selected_stock_name"] = str(selected.get("name", ""))
            elif not st.session_state.get("selected_stock") and not stock_candidates.empty:
                selected = stock_candidates.iloc[0]
                st.session_state["selected_stock"] = str(selected["symbol"]).zfill(6)
                st.session_state["selected_stock_name"] = str(selected.get("name", ""))
    elif selected_sector:
        st.info("板块已选好，点击“刷新工作台”生成板块内个股候选。")

    selected_stock = st.session_state.get("selected_stock", "")
    if selected_stock:
        st.markdown("### 3. 查看强势股走势")
        with st.spinner(f"正在加载 {selected_stock} 行情..."):
            try:
                stock_hist = stock_history_safe(selected_stock, ymd(start_date), ymd(end_date))
                render_price_detail(f"{selected_stock} {st.session_state.get('selected_stock_name', '')}", stock_hist, "workspace_stock")
                render_fund_flow(stock_fund_flow_safe(selected_stock), "个股资金流")
            except Exception as exc:
                st.error(f"个股详情失败：{exc}")

    st.markdown("### 4. ETF 替代选择")
    etfs = load_etf_candidates(etf_prefilter, top_etfs)
    if "error" in etfs.columns:
        st.warning(etfs.iloc[0]["error"])
    elif etfs.empty:
        st.info("暂无 ETF 候选。")
    else:
        etf_event = st.dataframe(
            etfs,
            use_container_width=True,
            hide_index=True,
            key="etf_candidate_table",
            on_select="rerun",
            selection_mode="single-row",
        )
        etf_idx = selected_row_index(etf_event)
        if etf_idx is not None:
            selected = etfs.iloc[etf_idx]
            st.session_state["selected_etf"] = str(selected["symbol"]).zfill(6)
            st.session_state["selected_etf_name"] = str(selected.get("name", ""))
        elif not st.session_state.get("selected_etf"):
            selected = etfs.iloc[0]
            st.session_state["selected_etf"] = str(selected["symbol"]).zfill(6)
            st.session_state["selected_etf_name"] = str(selected.get("name", ""))

    selected_etf = st.session_state.get("selected_etf", "")
    if selected_etf:
        with st.spinner(f"正在加载 ETF {selected_etf} 行情..."):
            try:
                etf_hist = etf_history_safe(selected_etf, ymd(start_date), ymd(end_date))
                render_price_detail(f"ETF {selected_etf} {st.session_state.get('selected_etf_name', '')}", etf_hist, "workspace_etf")
            except Exception as exc:
                st.error(f"ETF 详情失败：{exc}")

    st.markdown("### 5. 生成报告")
    report_cols = st.columns(3)
    if report_cols[0].button("生成今天报告并推送", key="workspace_generate_today"):
        with st.spinner("正在生成报告、入库并推送..."):
            try:
                generated_path = run_daily(date.today(), force=True, notify_enabled=True)
                st.success(f"报告已生成：{generated_path}")
            except Exception as exc:
                st.error(f"生成失败：{exc}")
    if report_cols[1].button("下载当前扫描 CSV", disabled=not isinstance(scan, pd.DataFrame) or scan.empty):
        if isinstance(scan, pd.DataFrame) and not scan.empty:
            st.download_button(
                "确认下载",
                scan.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"{end_date:%Y-%m-%d}_scan.csv",
                mime="text/csv",
            )

    with st.expander("手动评分 / 单独查看"):
        score_kind = st.radio(
            "评分对象",
            ["stock", "sector", "etf"],
            horizontal=True,
            format_func=lambda x: {"stock": "个股", "sector": "板块", "etf": "ETF"}[x],
        )
        code = st.text_input("代码或名称", placeholder="个股如 600519；板块如 BK1625 或 钨；ETF 如 510300")
        if st.button("计算评分", type="primary") and code.strip():
            with st.spinner("正在计算评分..."):
                try:
                    if score_kind == "stock":
                        scored, hist = score_stock_code(data, code.strip(), ymd(start_date), ymd(end_date))
                        flow = stock_fund_flow_safe(code.strip())
                    elif score_kind == "sector":
                        scored, hist = score_sector_key(data, code.strip(), ymd(start_date), ymd(end_date), board_type, settings)
                        flow = sector_fund_flow_safe(code.strip(), board_type)
                    else:
                        scored, hist = score_etf_safe(code.strip(), ymd(start_date), ymd(end_date))
                        flow = pd.DataFrame()
                    render_score_summary(scored)
                    render_price_detail(code.strip(), hist, f"manual_{score_kind}")
                    if score_kind != "etf":
                        render_fund_flow(flow, "资金流")
                except Exception as exc:
                    st.error(f"评分失败：{exc}")

with tabs[1]:
    st.subheader("报告")
    st.caption("按日期生成、查看或删除报告；删除后可重新生成，避免旧报告缓存影响判断。")
    report_date = st.date_input("报告日期", value=end_date, key="report_date")
    r0, r1, r2 = st.columns(3)
    if r0.button("生成所选日期报告", type="primary", key="history_generate_selected"):
        with st.spinner("正在生成报告、写入数据库并推送..."):
            try:
                generated_path = run_daily(report_date, force=True, notify_enabled=True)
                if generated_path:
                    st.session_state["history_generated_path"] = str(generated_path.resolve())
                    st.session_state["history_generated_html"] = generated_path.read_text(encoding="utf-8")
                    st.success(f"报告已生成：{generated_path.resolve()}")
                else:
                    st.info("非交易日，已跳过生成。")
            except Exception as exc:
                st.error(f"生成失败：{exc}")
    if r1.button("从BaoStock刷新后生成", key="history_refresh_generate"):
        with st.spinner("正在强制刷新工作台数据并生成报告..."):
            try:
                load_workspace_for_date(report_date, board_type, top_sectors, stocks_per_sector, force_refresh=True)
                generated_path = run_daily(report_date, force=True, notify_enabled=True)
                st.success(f"已刷新并生成：{generated_path}")
            except Exception as exc:
                st.error(f"刷新生成失败：{exc}")
    if r2.button("只预热所选日期数据", key="history_warm_selected"):
        with st.spinner("正在从 BaoStock 拉取板块和个股数据..."):
            try:
                load_workspace_for_date(report_date, board_type, top_sectors, stocks_per_sector, force_refresh=True)
                st.success(f"{report_date:%Y-%m-%d} 板块+个股数据已拉取到本地缓存。")
            except Exception as exc:
                st.error(f"预热失败：{exc}")

    if st.session_state.get("history_generated_html"):
        st.info(f"本地报告位置：{st.session_state.get('history_generated_path')}")
        components.html(st.session_state["history_generated_html"], height=900, scrolling=True)

    try:
        init_database()
        overview = get_database_overview()
        reports = fetch_recent_reports(60)
    except Exception as exc:
        overview = {}
        reports = pd.DataFrame()
        st.error(f"读取数据库失败：{exc}")

    if overview:
        cache_stats = data.cache.stats()
        d1, d2, d3, d4, d5, d6 = st.columns(6)
        d1.metric("报告数", overview.get("report_count", 0))
        d2.metric("个股记录", overview.get("stock_rows", 0))
        d3.metric("ETF记录", overview.get("etf_rows", 0))
        d4.metric("最新报告", overview.get("latest_report_date") or "-")
        d5.metric("缓存表", cache_stats.get("table_count", 0))
        d6.metric("缓存行", cache_stats.get("row_count", 0))

    if reports.empty:
        st.info("数据库里还没有历史报告。可以先运行 `python -m src.daily_job --force`。")
    else:
        options = {f"{row.report_date} - {row.title}": int(row.id) for row in reports.itertuples()}
        selected_label = st.selectbox("选择报告", list(options.keys()))
        selected_id = options[selected_label]
        selected_report_row = reports[reports["id"].astype(int).eq(selected_id)].iloc[0]
        selected_report_date = pd.to_datetime(selected_report_row["report_date"]).date()
        del1, del2 = st.columns(2)
        if del1.button("删除选中报告", key="delete_selected_report"):
            try:
                remove_report(selected_id)
                st.success("已删除选中报告。")
                st.rerun()
            except Exception as exc:
                st.error(f"删除失败：{exc}")
        if del2.button("删除并重新生成所选日期", key="delete_regenerate_report"):
            try:
                remove_report(selected_id)
                generated_path = run_daily(selected_report_date, force=True, notify_enabled=True)
                st.success(f"已删除并重新生成：{generated_path}")
                st.rerun()
            except Exception as exc:
                st.error(f"删除重生成失败：{exc}")
        changes = fetch_table("changes", selected_id)
        sectors = fetch_table("sectors", selected_id)
        stocks = fetch_table("stocks", selected_id)
        etfs = fetch_table("etfs", selected_id)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("板块", len(sectors))
        c2.metric("个股", len(stocks))
        c3.metric("ETF", len(etfs))
        c4.metric("变化提醒", len(changes))

        view = st.radio("查看内容", ["变化提醒", "板块趋势", "候选个股", "ETF", "原始报告"], horizontal=True)
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
        else:
            html_text = fetch_report_html(selected_id)
            components.html(html_text, height=900, scrolling=True)

with tabs[2]:
    st.subheader("监控")
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

with tabs[3]:
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

    p0, p1 = st.columns(2)
    if p0.button("从BaoStock拉取任务日期板块+个股"):
        with st.spinner("正在强制刷新任务日期的板块和个股数据..."):
            try:
                load_workspace_for_date(task_date, board_type, top_sectors, stocks_per_sector, force_refresh=True)
                st.success(f"{task_date:%Y-%m-%d} 工作台数据已刷新到本地缓存。")
            except Exception as exc:
                st.error(f"拉取失败：{exc}")
    if p1.button("加载任务日期本地缓存"):
        with st.spinner("正在从本地缓存加载任务日期工作台..."):
            try:
                load_workspace_for_date(task_date, board_type, top_sectors, stocks_per_sector, force_refresh=False)
                st.success(f"{task_date:%Y-%m-%d} 工作台数据已从本地缓存加载。")
            except Exception as exc:
                st.error(f"加载失败：{exc}")

    st.markdown("#### 数据状态")
    cache_stats = data.cache.stats()
    try:
        overview = get_database_overview()
    except Exception as exc:
        overview = {}
        st.info(f"MySQL 状态暂不可用：{exc}")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("报告数", overview.get("report_count", 0) if overview else 0)
    s2.metric("最新报告", overview.get("latest_report_date") if overview else "-")
    s3.metric("本地缓存表", cache_stats.get("table_count", 0))
    s4.metric("本地缓存行", cache_stats.get("row_count", 0))

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
