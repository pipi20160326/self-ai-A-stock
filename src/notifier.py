from __future__ import annotations

import json
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime

import pandas as pd
import requests


def _safe_tables(html_path: Path) -> list[pd.DataFrame]:
    try:
        if html_path.is_file():
            return pd.read_html(html_path, encoding="utf-8")
    except Exception:
        return []
    return []


def _top_lines(frame: pd.DataFrame, columns: list[str], limit: int) -> list[str]:
    if frame.empty:
        return ["- 暂无"]
    usable = [col for col in columns if col in frame.columns]
    lines = []
    for _, row in frame.head(limit).iterrows():
        parts = [f"{col}:{row[col]}" for col in usable if str(row.get(col, "")).strip() not in {"", "nan"}]
        lines.append("- " + " ｜ ".join(parts))
    return lines or ["- 暂无"]


def build_message(report_date: str, html_path: Path, changes: list[str]) -> str:
    tables = _safe_tables(html_path)
    sectors = tables[0] if len(tables) > 0 else pd.DataFrame()
    stocks = tables[1] if len(tables) > 1 else pd.DataFrame()
    etfs = tables[2] if len(tables) > 2 else pd.DataFrame()

    strong = stocks[stocks["观点"].astype(str).eq("强势看涨")] if "观点" in stocks.columns else pd.DataFrame()
    moderate = stocks[stocks["观点"].astype(str).eq("一般看涨")] if "观点" in stocks.columns else pd.DataFrame()
    watch = stocks[stocks["观点"].astype(str).eq("观察")] if "观点" in stocks.columns else pd.DataFrame()

    lines = [
        f"# {report_date} A股板块趋势报告",
        f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"报告文件: {html_path}",
        "",
        "## 强势板块 Top 8",
        *_top_lines(sectors, ["排名", "板块", "当日涨幅", "趋势分"], 8),
        "",
        "## 板块内强势看涨",
        *_top_lines(strong, ["板块", "代码", "名称", "观点评分", "当日涨幅", "趋势分"], 8),
        "",
        "## 一般看涨",
        *_top_lines(moderate, ["板块", "代码", "名称", "观点评分", "当日涨幅", "趋势分"], 8),
        "",
        "## 观察",
        *_top_lines(watch, ["板块", "代码", "名称", "观点评分", "当日涨幅", "趋势分"], 8),
        "",
        "## ETF 候选",
        *_top_lines(etfs, ["代码", "名称", "观点", "观点评分", "当日涨幅", "趋势分"], 8),
    ]
    if changes:
        lines.append("")
        lines.append("## 板块变化提醒")
        lines.extend(f"- {item}" for item in changes[:12])
    lines.append("")
    lines.append("> 仅作研究辅助，不构成投资建议。")
    return "\n".join(lines)


def send_email(subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to = os.getenv("SMTP_TO")
    if not all([host, user, password, to]):
        return False
    port = int(os.getenv("SMTP_PORT", "465"))
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
        smtp.login(user, password)
        smtp.sendmail(user, [addr.strip() for addr in to.split(",")], msg.as_string())
    return True


def send_dingtalk(title: str, body: str) -> bool:
    webhook = os.getenv("DINGTALK_WEBHOOK")
    if not webhook:
        return False
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": body.replace("\n", "\n\n")}}
    resp = requests.post(webhook, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=20)
    resp.raise_for_status()
    return True


def notify(report_date: str, html_path: Path, changes: list[str]) -> dict[str, bool | str]:
    subject = f"{report_date} A股板块趋势报告"
    body = build_message(report_date, html_path, changes)
    result: dict[str, bool | str] = {"email": False, "dingtalk": False}
    try:
        result["email"] = send_email(subject, body)
    except Exception as exc:
        result["email_error"] = str(exc)
    try:
        result["dingtalk"] = send_dingtalk(subject, body)
    except Exception as exc:
        result["dingtalk_error"] = str(exc)
    return result
