from __future__ import annotations

import json
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

import requests


def build_message(report_date: str, html_path: Path, changes: list[str]) -> str:
    lines = [f"{report_date} A股板块趋势报告已生成", f"文件: {html_path}"]
    if changes:
        lines.append("")
        lines.append("板块变化:")
        lines.extend(f"- {item}" for item in changes[:12])
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
