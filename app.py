from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.backtest import BacktestEngine
from src.config import settings
from src.data import MarketDataService
from src.reports import save_backtest, save_daily_scan
from src.strategy import TrendScanner
from src.strategy.indicators import with_indicators
from src.manual_score import score_sector_key, score_stock_code
from src.db import (
    add_monitor_target,
    fetch_monitor_events,
    fetch_monitor_targets,
    fetch_recent_reports,
    fetch_report_html,
    fetch_table,
    fetch_sector_history,
    init_database,
    set_monitor_active,
)
from src.monitor import run_monitor
from src.daily_job import run_daily
from src.db import save_report_to_db


st.set_page_config(page_title="A股板块趋势扫描", layout="wide")


@st.cache_resource
def service() -> MarketDataService:
    return MarketDataService(settings)


def ymd(value) -> str:
    return value.strftime("%Y%m%d")


def pct(value: float) -> str:
    return f"{value:.2%}"


data = service()
scanner = TrendScanner(data, settings)

st.title("A股板块趋势扫描")
st.caption("先板块、后个股；日线收盘信号；仅用于研究，不构成投资建议。")

with st.sidebar:
    board_type = st.radio("板块类型", ["industry", "concept"], format_func=lambda x: "行业板块" if x == "industry" else "概念板块")
    start_date = st.date_input("数据起点", pd.to_datetime(settings.start_date))
    end_date = st.date_input("结束日期", date.today())
    top_sectors = st.slider("关注板块数", 1, 20, settings.scan_top_sectors)
    stocks_per_sector = st.slider("每板块个股数", 1, 10, settings.scan_top_stocks_per_sector)

tabs = st.tabs(["今日扫描", "板块排行", "个股详情", "回测", "手动评分", "历史报告", "监控", "任务中心"])

with tabs[0]:
    st.subheader("今日扫描")
    if st.button("运行扫描", type="primary"):
        with st.spinner("正在拉取数据并计算趋势..."):
            try:
                scan = scanner.scan(ymd(start_date), ymd(end_date), board_type, top_sectors, stocks_per_sector)
                st.session_state["scan"] = scan
                path = save_daily_scan(scan, end_date.strftime("%Y-%m-%d"))
                st.success(f"扫描完成：{path}")
            except Exception as exc:
                st.error(f"扫描失败：{exc}")
    scan = st.session_state.get("scan")
    if isinstance(scan, pd.DataFrame) and not scan.empty:
        st.dataframe(scan, use_container_width=True)
        st.download_button(
            "下载扫描结果",
            scan.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{end_date:%Y-%m-%d}_scan.csv",
            mime="text/csv",
        )
    else:
        st.info("点击“运行扫描”生成板块和个股关注清单。")

with tabs[1]:
    st.subheader("板块排行")
    if st.button("刷新板块排行"):
        with st.spinner("正在计算板块趋势分..."):
            try:
                ranked = scanner.rank_sectors(ymd(start_date), ymd(end_date), board_type, top_sectors)
                st.session_state["ranked"] = ranked
            except Exception as exc:
                st.error(f"板块排行失败：{exc}")
    ranked = st.session_state.get("ranked")
    if isinstance(ranked, pd.DataFrame) and not ranked.empty:
        st.dataframe(ranked, use_container_width=True)
        fig = go.Figure(go.Bar(x=ranked["sector"], y=ranked["score"], text=ranked["reason"]))
        fig.update_layout(height=420, xaxis_title="板块", yaxis_title="趋势得分")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("点击“刷新板块排行”查看趋势得分。")

with tabs[2]:
    st.subheader("个股详情")
    symbol = st.text_input("股票代码", value="")
    if st.button("查看个股") and symbol.strip():
        with st.spinner("正在加载个股行情..."):
            try:
                hist = data.stock_history(symbol.strip(), ymd(start_date), ymd(end_date))
                detail = with_indicators(hist)
            except Exception as exc:
                st.error(f"个股行情失败：{exc}")
                hist = pd.DataFrame()
                detail = pd.DataFrame()
        if hist.empty:
            st.warning("没有获取到行情数据。")
        else:
            st.metric("最新收盘", f"{detail.iloc[-1]['close']:.2f}")
            col1, col2, col3 = st.columns(3)
            col1.metric("20日收益", pct(detail.iloc[-1].get("ret20", 0)))
            col2.metric("60日收益", pct(detail.iloc[-1].get("ret60", 0)))
            col3.metric("MA20斜率", pct(detail.iloc[-1].get("ma20_slope", 0)))
            fig = go.Figure()
            fig.add_trace(go.Candlestick(x=detail["date"], open=detail["open"], high=detail["high"], low=detail["low"], close=detail["close"], name="K线"))
            fig.add_trace(go.Scatter(x=detail["date"], y=detail["ma20"], name="MA20"))
            fig.add_trace(go.Scatter(x=detail["date"], y=detail["ma60"], name="MA60"))
            fig.update_layout(height=560, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(detail.tail(80), use_container_width=True)

with tabs[3]:
    st.subheader("回测")
    bt_start = st.date_input("回测开始", pd.to_datetime(settings.start_date), key="bt_start")
    bt_end = st.date_input("回测结束", date.today(), key="bt_end")
    initial_cash = st.number_input("初始资金", min_value=10000, value=int(settings.initial_cash), step=10000)
    if st.button("运行回测", type="primary"):
        with st.spinner("正在回测，首次运行会较慢..."):
            try:
                result = BacktestEngine(data, settings).run(
                    ymd(bt_start),
                    ymd(bt_end),
                    board_type,
                    top_sectors,
                    stocks_per_sector,
                    float(initial_cash),
                )
                st.session_state["bt"] = result
                paths = save_backtest(result.equity, result.trades, result.metrics, f"{ymd(bt_start)}_{ymd(bt_end)}")
                st.success(f"回测完成：{paths['metrics']}")
            except Exception as exc:
                st.error(f"回测失败：{exc}")
    result = st.session_state.get("bt")
    if result:
        metrics = result.metrics
        cols = st.columns(6)
        cols[0].metric("总收益", pct(metrics["total_return"]))
        cols[1].metric("年化", pct(metrics["annualized_return"]))
        cols[2].metric("最大回撤", pct(metrics["max_drawdown"]))
        cols[3].metric("夏普", f"{metrics['sharpe']:.2f}")
        cols[4].metric("交易次数", metrics["trade_count"])
        cols[5].metric("换手", f"{metrics['turnover']:.2f}x")
        if not result.equity.empty:
            fig = go.Figure(go.Scatter(x=result.equity["date"], y=result.equity["equity"], name="权益"))
            fig.update_layout(height=420, xaxis_title="日期", yaxis_title="账户权益")
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(result.trades, use_container_width=True)

with tabs[4]:
    st.subheader("手动评分")
    score_kind = st.radio("评分对象", ["stock", "sector"], horizontal=True, format_func=lambda x: "个股" if x == "stock" else "板块")
    code = st.text_input("代码或名称", placeholder="个股如 600519；板块如 BK1625 或 钨")
    if st.button("计算评分", type="primary") and code.strip():
        with st.spinner("正在计算评分..."):
            try:
                if score_kind == "stock":
                    scored, hist = score_stock_code(data, code.strip(), ymd(start_date), ymd(end_date))
                else:
                    scored, hist = score_sector_key(data, code.strip(), ymd(start_date), ymd(end_date), board_type, settings)
                cols = st.columns(5)
                cols[0].metric("信号", scored.get("signal", "板块评分"))
                cols[1].metric("趋势分", f"{scored.get('score', 0):.4f}")
                cols[2].metric("20日", pct(scored.get("ret20", 0)))
                cols[3].metric("60日", pct(scored.get("ret60", 0)))
                cols[4].metric("收盘", f"{scored.get('close', 0):.2f}")
                st.write(scored.get("reason", ""))
                detail = with_indicators(hist)
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=detail["date"], open=detail["open"], high=detail["high"], low=detail["low"], close=detail["close"], name="K线"))
                fig.add_trace(go.Scatter(x=detail["date"], y=detail["ma20"], name="MA20"))
                fig.add_trace(go.Scatter(x=detail["date"], y=detail["ma60"], name="MA60"))
                fig.update_layout(height=520, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
            except Exception as exc:
                st.error(f"评分失败：{exc}")

with tabs[5]:
    st.subheader("历史报告")
    st.caption("可以手动生成今天报告；生成后会写入数据库，并在本页直接加载 HTML。")
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
    else:
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

        view = st.radio("查看内容", ["变化提醒", "板块趋势", "候选个股", "ETF", "原始报告"], horizontal=True)
        if view == "变化提醒":
            st.dataframe(changes[["change_type", "target_name", "old_rank", "new_rank", "old_score", "new_score", "message"]], use_container_width=True)
        elif view == "板块趋势":
            st.dataframe(sectors[["rank_no", "sector", "today_pct", "score", "ret20", "ret60", "reason"]], use_container_width=True)
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
            st.dataframe(etfs[["rank_no", "symbol", "name", "signal_text", "today_pct", "close_price", "score", "ret20", "ret60", "reason"]], use_container_width=True)
        else:
            html_text = fetch_report_html(selected_id)
            components.html(html_text, height=900, scrolling=True)

with tabs[6]:
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

    targets = fetch_monitor_targets()
    events = fetch_monitor_events(100)
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

with tabs[7]:
    st.subheader("任务中心")
    st.caption("这里的按钮会把结果写入 MySQL，之后可在“历史报告”和“监控”页查看。")
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
