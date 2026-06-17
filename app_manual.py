from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.config import ROOT_DIR, settings
from src.html_report import build_report


st.set_page_config(page_title="A股手动报告-无数据库版", layout="wide")


def ymd(value: date) -> str:
    return value.strftime("%Y%m%d")


st.title("A股板块趋势报告")
st.caption("无数据库分支：只手动生成本地 HTML 报告，不连接 MySQL，不定时，不入库。")

with st.sidebar:
    report_day = st.date_input("报告日期", date.today())
    start_date = st.date_input("数据起点", pd.to_datetime(settings.start_date))
    board_type = st.radio("板块类型", ["industry", "concept"], format_func=lambda x: "行业板块" if x == "industry" else "概念板块")
    prefilter = st.number_input("预筛板块数", min_value=5, max_value=200, value=settings.daily_prefilter, step=5)
    top_sectors = st.number_input("入选板块数", min_value=1, max_value=50, value=settings.daily_top_sectors, step=1)
    member_limit = st.number_input("每板块扫描成分股", min_value=3, max_value=80, value=settings.daily_member_limit, step=1)
    stocks_per_sector = st.number_input("每板块输出候选", min_value=1, max_value=10, value=settings.daily_stocks_per_sector, step=1)
    etf_prefilter = st.number_input("ETF 预筛数", min_value=0, max_value=100, value=settings.daily_etf_prefilter, step=5)
    top_etfs = st.number_input("ETF 输出数", min_value=0, max_value=30, value=settings.daily_top_etfs, step=1)
    use_cache = st.checkbox("使用缓存行情", value=False)


output = ROOT_DIR / f"{report_day:%Y-%m-%d}-report.html"
if st.button("手动生成报告", type="primary"):
    with st.spinner("正在生成报告，默认会刷新行情数据..."):
        try:
            path = build_report(
                report_date=ymd(report_day),
                start=ymd(start_date),
                board_type=board_type,
                prefilter=int(prefilter),
                top_sectors=int(top_sectors),
                stocks_per_sector=int(stocks_per_sector),
                etf_prefilter=int(etf_prefilter),
                top_etfs=int(top_etfs),
                output=output,
                member_limit=int(member_limit),
                refresh=not use_cache,
            )
            st.session_state["manual_report_path"] = str(path.resolve())
            st.session_state["manual_report_html"] = path.read_text(encoding="utf-8")
            st.success(f"报告已生成：{path.resolve()}")
        except Exception as exc:
            st.error(f"生成失败：{exc}")

latest_path = st.session_state.get("manual_report_path")
latest_html = st.session_state.get("manual_report_html")
if latest_path and latest_html:
    st.info(f"本地报告位置：{latest_path}")
    components.html(latest_html, height=900, scrolling=True)
elif output.exists():
    st.info(f"已有本地报告：{output.resolve()}")
    if st.button("加载已有报告"):
        st.session_state["manual_report_path"] = str(output.resolve())
        st.session_state["manual_report_html"] = output.read_text(encoding="utf-8")
        st.rerun()
else:
    st.info("点击“手动生成报告”开始。")
