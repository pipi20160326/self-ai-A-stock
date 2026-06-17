from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import ROOT_DIR, settings
from src.db import init_database, mysql_conn, save_report_to_db
from src.html_report import build_report
from src.monitor import run_monitor
from src.notifier import notify


def is_trade_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    try:
        import akshare as ak

        dates = ak.tool_trade_date_hist_sina()
        trade_dates = set(pd.to_datetime(dates["trade_date"]).dt.date)
        return day in trade_dates
    except Exception:
        return True


def report_changes(report_id: int) -> list[str]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select message
                from report_changes
                where report_id=%s
                order by
                    case change_type
                        when 'new_strong' then 1
                        when 'strengthened' then 2
                        when 'weakened' then 3
                        else 4
                    end,
                    id
                limit 20
                """,
                (report_id,),
            )
            return [row["message"] for row in cur.fetchall()]


def run_daily(report_date: date, force: bool = False, notify_enabled: bool = True) -> Path | None:
    if not force and not is_trade_day(report_date):
        print(f"{report_date} 非交易日，跳过。")
        return None

    init_database()
    ymd = report_date.strftime("%Y%m%d")
    iso = report_date.strftime("%Y-%m-%d")
    output = ROOT_DIR / f"{iso}-report.html"
    path = build_report(
        report_date=ymd,
        start=settings.start_date,
        board_type="industry",
        prefilter=settings.daily_prefilter,
        top_sectors=settings.daily_top_sectors,
        stocks_per_sector=settings.daily_stocks_per_sector,
        etf_prefilter=settings.daily_etf_prefilter,
        top_etfs=settings.daily_top_etfs,
        output=output,
        member_limit=settings.daily_member_limit,
        refresh=settings.daily_refresh,
    )
    report_id = save_report_to_db(iso, path)
    changes = report_changes(report_id)
    print(f"报告已生成并入库: {path}, report_id={report_id}")
    for item in changes[:12]:
        print(f"- {item}")
    if notify_enabled:
        print(notify(iso, path, changes))
        monitor_messages = run_monitor(report_date, notify_enabled=True)
        for item in monitor_messages:
            print(f"[monitor] {item}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="每日生成 A 股策略报告并入库")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--html", help="直接把已有 HTML 报告入库，不重新抓行情")
    args = parser.parse_args()
    report_date = pd.to_datetime(args.date).date()
    if args.html:
        init_database()
        report_id = save_report_to_db(report_date.strftime("%Y-%m-%d"), Path(args.html))
        changes = report_changes(report_id)
        print(f"已有 HTML 已入库: {args.html}, report_id={report_id}")
        if not args.no_notify:
            print(notify(report_date.strftime("%Y-%m-%d"), Path(args.html), changes))
        return
    run_daily(report_date, force=args.force, notify_enabled=not args.no_notify)


if __name__ == "__main__":
    main()
