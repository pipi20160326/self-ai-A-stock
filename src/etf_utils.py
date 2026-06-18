from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

import pandas as pd


ETF_THEME_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("人工智能/科技", ("人工智能", "AI", "AIGC", "机器人", "云计算", "大数据", "软件", "信创", "计算机", "传媒", "游戏")),
    ("半导体/芯片", ("半导体", "芯片", "集成电路", "科创芯片", "芯片设计")),
    ("新能源", ("新能源", "光伏", "电池", "锂电", "储能", "电力设备", "智能电动车", "汽车")),
    ("医药医疗", ("医药", "医疗", "创新药", "生物", "中药", "疫苗", "CRO")),
    ("消费", ("消费", "白酒", "食品", "家电", "旅游", "农业", "养殖")),
    ("金融地产", ("证券", "券商", "银行", "保险", "地产", "金融")),
    ("资源周期", ("有色", "稀土", "煤炭", "钢铁", "黄金", "资源", "化工", "石油")),
    ("军工", ("军工", "国防", "航天", "航空")),
    ("宽基指数", ("沪深300", "中证500", "中证1000", "上证50", "创业板", "科创50", "A500", "双创", "红利", "央企", "国企")),
    ("港美海外", ("恒生", "港股", "纳斯达克", "标普", "日经", "德国", "海外", "QDII")),
)

ETF_SUFFIX_RE = re.compile(r"(ETF|LOF|QDII|基金|指数|联接|增强|发起式|交易型|开放式|场内)", re.IGNORECASE)


def infer_etf_theme(name: object) -> str:
    text = str(name or "").strip()
    upper = text.upper()
    for theme, keywords in ETF_THEME_RULES:
        if any(keyword.upper() in upper for keyword in keywords):
            return theme

    cleaned = ETF_SUFFIX_RE.sub("", text)
    cleaned = re.sub(r"[\sA-Za-z0-9（）()_-]+", "", cleaned)
    return cleaned[:8] or text[:8] or "其他"


def diversify_etf_frame(frame: pd.DataFrame, limit: int, max_per_theme: int = 2) -> pd.DataFrame:
    if frame is None or frame.empty or limit <= 0:
        return pd.DataFrame() if frame is None else frame.head(0).copy()

    ordered = frame.copy()
    if "theme" not in ordered.columns:
        ordered["theme"] = ordered.get("name", "").map(infer_etf_theme)
    sort_cols = [col for col in ["score", "pct_chg", "amount"] if col in ordered.columns]
    if sort_cols:
        ordered = ordered.sort_values(sort_cols, ascending=False)

    selected_indices: list[int] = []
    theme_counts: defaultdict[str, int] = defaultdict(int)
    for idx, row in ordered.iterrows():
        theme = str(row.get("theme", "其他"))
        if theme_counts[theme] >= max_per_theme:
            continue
        selected_indices.append(idx)
        theme_counts[theme] += 1
        if len(selected_indices) >= limit:
            break

    if len(selected_indices) < limit:
        for idx in ordered.index:
            if idx in selected_indices:
                continue
            selected_indices.append(idx)
            if len(selected_indices) >= limit:
                break

    return ordered.loc[selected_indices].reset_index(drop=True)


def diversify_etf_rows(rows: Iterable[dict], limit: int, max_per_theme: int = 2) -> list[dict]:
    items = [dict(row) for row in rows]
    if not items or limit <= 0:
        return []
    for item in items:
        item.setdefault("theme", infer_etf_theme(item.get("name", "")))
    items.sort(key=lambda item: float(item.get("score") or 0), reverse=True)

    selected: list[dict] = []
    theme_counts: defaultdict[str, int] = defaultdict(int)
    for item in items:
        theme = str(item.get("theme", "其他"))
        if theme_counts[theme] >= max_per_theme:
            continue
        selected.append(item)
        theme_counts[theme] += 1
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        selected_ids = {id(item) for item in selected}
        for item in items:
            if id(item) in selected_ids:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
    return selected
