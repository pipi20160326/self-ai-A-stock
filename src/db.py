from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pymysql


DB_NAME = os.getenv("MYSQL_DATABASE", "astock_strategy")


@dataclass(frozen=True)
class MySQLConfig:
    host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    port: int = int(os.getenv("MYSQL_PORT", "3306"))
    user: str = os.getenv("MYSQL_USER", "root")
    password: str = os.getenv("MYSQL_PASSWORD", "")
    database: str = DB_NAME


db_config = MySQLConfig()


@contextmanager
def mysql_conn(database: str | None = DB_NAME) -> Iterator[pymysql.Connection]:
    conn = pymysql.connect(
        host=db_config.host,
        port=db_config.port,
        user=db_config.user,
        password=db_config.password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    with mysql_conn(database=None) as conn:
        with conn.cursor() as cur:
            cur.execute(f"create database if not exists `{DB_NAME}` default character set utf8mb4 collate utf8mb4_unicode_ci")

    statements = [
        """
        create table if not exists reports (
            id bigint primary key auto_increment,
            report_date date not null unique,
            title varchar(128) not null,
            html mediumtext not null,
            file_path varchar(512) not null,
            sector_count int not null default 0,
            stock_count int not null default 0,
            etf_count int not null default 0,
            created_at datetime not null,
            summary json null
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
        """
        create table if not exists report_sectors (
            id bigint primary key auto_increment,
            report_id bigint not null,
            report_date date not null,
            rank_no int not null,
            sector varchar(128) not null,
            today_pct decimal(12,4) null,
            score decimal(16,6) null,
            ret20 decimal(12,4) null,
            ret60 decimal(12,4) null,
            relative_strength decimal(12,4) null,
            reason varchar(512) null,
            unique key uq_report_sector (report_id, sector),
            index idx_sector_date (sector, report_date),
            constraint fk_report_sectors_report foreign key (report_id) references reports(id) on delete cascade
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
        """
        create table if not exists report_stocks (
            id bigint primary key auto_increment,
            report_id bigint not null,
            report_date date not null,
            sector_rank int null,
            sector varchar(128) null,
            symbol varchar(16) not null,
            name varchar(128) null,
            signal_text varchar(32) null,
            close_price decimal(16,4) null,
            score decimal(16,6) null,
            ret20 decimal(12,4) null,
            ret60 decimal(12,4) null,
            reason varchar(512) null,
            index idx_stock_date (symbol, report_date),
            constraint fk_report_stocks_report foreign key (report_id) references reports(id) on delete cascade
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
        """
        create table if not exists report_etfs (
            id bigint primary key auto_increment,
            report_id bigint not null,
            report_date date not null,
            rank_no int not null,
            symbol varchar(16) not null,
            name varchar(128) null,
            signal_text varchar(32) null,
            today_pct decimal(12,4) null,
            close_price decimal(16,4) null,
            score decimal(16,6) null,
            ret20 decimal(12,4) null,
            ret60 decimal(12,4) null,
            reason varchar(512) null,
            index idx_etf_date (symbol, report_date),
            constraint fk_report_etfs_report foreign key (report_id) references reports(id) on delete cascade
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
        """
        create table if not exists report_changes (
            id bigint primary key auto_increment,
            report_id bigint not null,
            report_date date not null,
            change_type varchar(32) not null,
            target_type varchar(32) not null,
            target_name varchar(128) not null,
            old_rank int null,
            new_rank int null,
            old_score decimal(16,6) null,
            new_score decimal(16,6) null,
            message varchar(512) not null,
            created_at datetime not null,
            index idx_changes_date (report_date, change_type),
            constraint fk_report_changes_report foreign key (report_id) references reports(id) on delete cascade
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
        """
        create table if not exists monitor_targets (
            id bigint primary key auto_increment,
            target_type varchar(32) not null,
            code varchar(64) not null,
            name varchar(128) null,
            min_score decimal(16,6) null,
            required_signal varchar(32) null,
            active tinyint not null default 1,
            note varchar(512) null,
            created_at datetime not null,
            updated_at datetime not null,
            unique key uq_monitor_target (target_type, code)
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
        """
        create table if not exists monitor_events (
            id bigint primary key auto_increment,
            target_id bigint not null,
            event_date date not null,
            target_type varchar(32) not null,
            code varchar(64) not null,
            name varchar(128) null,
            signal_text varchar(32) null,
            score decimal(16,6) null,
            message varchar(512) not null,
            payload json null,
            created_at datetime not null,
            index idx_monitor_events_date (event_date, target_type, code),
            constraint fk_monitor_events_target foreign key (target_id) references monitor_targets(id) on delete cascade
        ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
        """,
    ]
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for sql in statements:
                cur.execute(sql)


def _to_decimal(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-", "nan", "None"}:
        return None
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text)
    except ValueError:
        return None


def _query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return pd.DataFrame(rows)


def read_report_tables(html_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tables = pd.read_html(html_path, encoding="utf-8")
    sector = tables[0] if len(tables) > 0 else pd.DataFrame()
    stock = tables[1] if len(tables) > 1 else pd.DataFrame()
    etf = tables[2] if len(tables) > 2 else pd.DataFrame()
    return sector, stock, etf


def latest_previous_sectors(report_date: str, limit: int = 30) -> dict[str, dict]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select rs.sector, rs.rank_no, rs.score
                from report_sectors rs
                join (
                    select max(report_date) as prev_date
                    from reports
                    where report_date < %s
                ) p on rs.report_date = p.prev_date
                where rs.rank_no <= %s
                """,
                (report_date, limit),
            )
            return {row["sector"]: row for row in cur.fetchall()}


def build_sector_changes(report_id: int, report_date: str, sector_df: pd.DataFrame, top_n: int = 20) -> list[dict]:
    prev = latest_previous_sectors(report_date, top_n)
    changes: list[dict] = []
    current = {}
    for _, row in sector_df.head(top_n).iterrows():
        sector = str(row["板块"])
        rank_no = int(row["排名"])
        score = _to_decimal(row["趋势分"])
        current[sector] = {"rank_no": rank_no, "score": score}
        old = prev.get(sector)
        if old is None:
            changes.append(
                {
                    "report_id": report_id,
                    "report_date": report_date,
                    "change_type": "new_strong",
                    "target_type": "sector",
                    "target_name": sector,
                    "old_rank": None,
                    "new_rank": rank_no,
                    "old_score": None,
                    "new_score": score,
                    "message": f"新进入强势板块前{top_n}: {sector}，当前排名 {rank_no}",
                }
            )
            continue
        old_rank = int(old["rank_no"])
        old_score = float(old["score"]) if old["score"] is not None else None
        if old_rank - rank_no >= 3:
            changes.append(
                {
                    "report_id": report_id,
                    "report_date": report_date,
                    "change_type": "strengthened",
                    "target_type": "sector",
                    "target_name": sector,
                    "old_rank": old_rank,
                    "new_rank": rank_no,
                    "old_score": old_score,
                    "new_score": score,
                    "message": f"{sector} 排名由 {old_rank} 升至 {rank_no}",
                }
            )
        if rank_no - old_rank >= 3:
            changes.append(
                {
                    "report_id": report_id,
                    "report_date": report_date,
                    "change_type": "weakened",
                    "target_type": "sector",
                    "target_name": sector,
                    "old_rank": old_rank,
                    "new_rank": rank_no,
                    "old_score": old_score,
                    "new_score": score,
                    "message": f"{sector} 排名由 {old_rank} 降至 {rank_no}",
                }
            )

    for sector, old in prev.items():
        if sector not in current:
            changes.append(
                {
                    "report_id": report_id,
                    "report_date": report_date,
                    "change_type": "dropped",
                    "target_type": "sector",
                    "target_name": sector,
                    "old_rank": int(old["rank_no"]),
                    "new_rank": None,
                    "old_score": float(old["score"]) if old["score"] is not None else None,
                    "new_score": None,
                    "message": f"{sector} 跌出强势板块前{top_n}",
                }
            )
    return changes


def save_report_to_db(report_date: str, html_path: Path) -> int:
    init_database()
    html_text = html_path.read_text(encoding="utf-8")
    sector_df, stock_df, etf_df = read_report_tables(html_path)
    now = datetime.now()
    summary = {
        "sector_count": int(len(sector_df)),
        "stock_count": int(len(stock_df)),
        "etf_count": int(len(etf_df)),
    }

    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from reports where report_date=%s", (report_date,))
            existing = cur.fetchone()
            if existing:
                report_id = int(existing["id"])
                cur.execute(
                    """
                    update reports
                    set title=%s, html=%s, file_path=%s, sector_count=%s, stock_count=%s,
                        etf_count=%s, created_at=%s, summary=%s
                    where id=%s
                    """,
                    (
                        f"{report_date} A股板块趋势报告",
                        html_text,
                        str(html_path),
                        len(sector_df),
                        len(stock_df),
                        len(etf_df),
                        now,
                        json.dumps(summary, ensure_ascii=False),
                        report_id,
                    ),
                )
                for table in ("report_sectors", "report_stocks", "report_etfs", "report_changes"):
                    cur.execute(f"delete from {table} where report_id=%s", (report_id,))
            else:
                cur.execute(
                    """
                    insert into reports
                    (report_date, title, html, file_path, sector_count, stock_count, etf_count, created_at, summary)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        report_date,
                        f"{report_date} A股板块趋势报告",
                        html_text,
                        str(html_path),
                        len(sector_df),
                        len(stock_df),
                        len(etf_df),
                        now,
                        json.dumps(summary, ensure_ascii=False),
                    ),
                )
                report_id = int(cur.lastrowid)

            for _, row in sector_df.iterrows():
                cur.execute(
                    """
                    insert into report_sectors
                    (report_id, report_date, rank_no, sector, today_pct, score, ret20, ret60, relative_strength, reason)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        report_id,
                        report_date,
                        int(row["排名"]),
                        str(row["板块"]),
                        _to_decimal(row["当日涨幅"]),
                        _to_decimal(row["趋势分"]),
                        _to_decimal(row["20日"]),
                        _to_decimal(row["60日"]),
                        _to_decimal(row["相对强度"]),
                        str(row.get("理由", "")),
                    ),
                )

            for _, row in stock_df.iterrows():
                cur.execute(
                    """
                    insert into report_stocks
                    (report_id, report_date, sector_rank, sector, symbol, name, signal_text, close_price, score, ret20, ret60, reason)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        report_id,
                        report_date,
                        int(row["板块排名"]),
                        str(row["板块"]),
                        str(row["代码"]).zfill(6),
                        str(row["名称"]),
                        str(row["信号"]),
                        _to_decimal(row["收盘"]),
                        _to_decimal(row["趋势分"]),
                        _to_decimal(row["20日"]),
                        _to_decimal(row["60日"]),
                        str(row.get("理由", "")),
                    ),
                )

            for _, row in etf_df.iterrows():
                cur.execute(
                    """
                    insert into report_etfs
                    (report_id, report_date, rank_no, symbol, name, signal_text, today_pct, close_price, score, ret20, ret60, reason)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        report_id,
                        report_date,
                        int(row["排名"]),
                        str(row["代码"]).zfill(6),
                        str(row["名称"]),
                        str(row["信号"]),
                        _to_decimal(row["当日涨幅"]),
                        _to_decimal(row["收盘"]),
                        _to_decimal(row["趋势分"]),
                        _to_decimal(row["20日"]),
                        _to_decimal(row["60日"]),
                        str(row.get("理由", "")),
                    ),
                )

            changes = build_sector_changes(report_id, report_date, sector_df)
            for change in changes:
                cur.execute(
                    """
                    insert into report_changes
                    (report_id, report_date, change_type, target_type, target_name, old_rank, new_rank,
                     old_score, new_score, message, created_at)
                    values (%(report_id)s,%(report_date)s,%(change_type)s,%(target_type)s,%(target_name)s,
                            %(old_rank)s,%(new_rank)s,%(old_score)s,%(new_score)s,%(message)s,%(created_at)s)
                    """,
                    {**change, "created_at": now},
                )
            return report_id


def fetch_recent_reports(limit: int = 30) -> pd.DataFrame:
    return _query_df(
        """
        select id, report_date, title, sector_count, stock_count, etf_count, created_at
        from reports
        order by report_date desc
        limit %s
        """,
        (limit,),
    )


def fetch_report_html(report_id: int) -> str:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select html from reports where id=%s", (report_id,))
            row = cur.fetchone()
            return row["html"] if row else ""


def fetch_table(table: str, report_id: int) -> pd.DataFrame:
    allowed = {
        "sectors": "report_sectors",
        "stocks": "report_stocks",
        "etfs": "report_etfs",
        "changes": "report_changes",
    }
    if table not in allowed:
        raise ValueError(f"unknown table: {table}")
    return _query_df(f"select * from {allowed[table]} where report_id=%s order by id", (report_id,))


def fetch_sector_history(days: int = 10) -> pd.DataFrame:
    return _query_df(
        """
        select report_date, sector, rank_no, score
        from report_sectors
        where report_date >= (
            select coalesce(min(report_date), current_date)
            from (
                select report_date
                from reports
                order by report_date desc
                limit %s
            ) x
        )
        order by report_date, rank_no
        """,
        (days,),
    )


def add_monitor_target(
    target_type: str,
    code: str,
    name: str = "",
    min_score: float | None = None,
    required_signal: str | None = None,
    note: str = "",
) -> int:
    init_database()
    now = datetime.now()
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into monitor_targets
                (target_type, code, name, min_score, required_signal, active, note, created_at, updated_at)
                values (%s,%s,%s,%s,%s,1,%s,%s,%s)
                on duplicate key update
                    name=values(name), min_score=values(min_score), required_signal=values(required_signal),
                    note=values(note), active=1, updated_at=values(updated_at)
                """,
                (target_type, code, name, min_score, required_signal, note, now, now),
            )
            if cur.lastrowid:
                return int(cur.lastrowid)
            cur.execute("select id from monitor_targets where target_type=%s and code=%s", (target_type, code))
            return int(cur.fetchone()["id"])


def set_monitor_active(target_id: int, active: bool) -> None:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update monitor_targets set active=%s, updated_at=%s where id=%s", (1 if active else 0, datetime.now(), target_id))


def fetch_monitor_targets(active_only: bool = False) -> pd.DataFrame:
    init_database()
    sql = "select * from monitor_targets"
    if active_only:
        sql += " where active=1"
    sql += " order by active desc, target_type, code"
    return _query_df(sql)


def save_monitor_event(target: dict, event_date: str, scored: dict, message: str) -> int:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into monitor_events
                (target_id, event_date, target_type, code, name, signal_text, score, message, payload, created_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    int(target["id"]),
                    event_date,
                    target["target_type"],
                    target["code"],
                    target.get("name") or "",
                    scored.get("signal"),
                    scored.get("score"),
                    message,
                    json.dumps(scored, ensure_ascii=False, default=str),
                    datetime.now(),
                ),
            )
            return int(cur.lastrowid)


def fetch_monitor_events(limit: int = 100) -> pd.DataFrame:
    init_database()
    return _query_df(
        """
        select *
        from monitor_events
        order by created_at desc
        limit %s
        """,
        (limit,),
    )
