from __future__ import annotations

import argparse
import html
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import ROOT_DIR, settings
from src.data import MarketDataService
from src.data.providers import normalize_price_frame, retry_call
from src.strategy.sector import market_is_healthy, score_sector
from src.strategy.stock import score_stock


ALLOWED_STOCK_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "-"


def _fmt_num(value, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _is_allowed_stock(symbol: str) -> bool:
    symbol = str(symbol).zfill(6)
    return symbol.startswith(ALLOWED_STOCK_PREFIXES)


def _row(cells: list[object], row_class: str = "") -> str:
    cls = f' class="{row_class}"' if row_class else ""
    return f"<tr{cls}>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in cells) + "</tr>"


def _table(headers: list[str], rows: list[list[object] | tuple[list[object], str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    rendered = []
    for row in rows:
        if isinstance(row, tuple):
            rendered.append(_row(row[0], row[1]))
        else:
            rendered.append(_row(row))
    body = "\n".join(rendered)
    if not rows:
        body = f"<tr><td colspan=\"{len(headers)}\">暂无符合条件的数据</td></tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _stance(signal: str, score: float | None) -> tuple[str, str]:
    score = float(score or 0)
    if signal == "买入" and score >= 0.6:
        return "强势看涨", "stance-strong"
    if signal == "买入" and score >= 0.2:
        return "一般看涨", "stance-moderate"
    return "观察", "stance-watch"


def _stance_rank(signal: str, score: float | None) -> int:
    stance, _ = _stance(signal, score)
    return {"强势看涨": 0, "一般看涨": 1, "观察": 2}.get(stance, 3)


def _view_score(score: float | None) -> str:
    try:
        return f"{max(float(score), 0) * 100:.1f}"
    except Exception:
        return "-"


def _summarize_errors(errors: list[str], limit: int = 12) -> list[str]:
    if len(errors) <= limit:
        return errors
    return errors[:limit] + [f"其余 {len(errors) - limit} 条数据失败已省略，多为公开接口临时断连或无历史数据。"]


def _first_value(row: pd.Series, names: list[str]):
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
    return None


def _to_float_or_none(value):
    try:
        text = str(value).strip()
        if text in {"", "-", "nan", "None"}:
            return None
        return float(text)
    except Exception:
        return None


def _load_stock_spot(errors: list[str]) -> dict[str, dict]:
    try:
        import akshare as ak
    except ImportError:
        errors.append("A股快照失败：AkShare 未安装")
        return {}
    try:
        raw = retry_call(lambda: ak.stock_zh_a_spot_em(), attempts=1)
    except Exception as exc:
        errors.append(f"A股快照失败，个股开盘/收盘/涨幅将回退历史K线：{exc}")
        return {}
    rows: dict[str, dict] = {}
    for _, row in raw.iterrows():
        raw_code = str(row.get("代码", "")).strip()
        if not raw_code or raw_code.lower() == "nan":
            continue
        code = raw_code.zfill(6)
        rows[code] = {
            "open": _to_float_or_none(_first_value(row, ["今开", "开盘", "开盘价"])),
            "close": _to_float_or_none(_first_value(row, ["最新价", "收盘", "收盘价"])),
            "today_pct": _to_float_or_none(_first_value(row, ["涨跌幅"])),
        }
    return rows


def _load_etf_candidates(start: str, end: str, prefilter: int, limit: int, errors: list[str]) -> list[dict]:
    try:
        import akshare as ak
    except ImportError:
        errors.append("ETF 数据失败：AkShare 未安装")
        return []

    try:
        spot = retry_call(lambda: ak.fund_etf_spot_em())
    except Exception as exc:
        errors.append(f"ETF 列表失败：{exc}")
        return []

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
    etfs["pct_chg"] = pd.to_numeric(etfs.get("pct_chg"), errors="coerce")
    etfs["amount"] = pd.to_numeric(etfs.get("amount"), errors="coerce")
    etfs = etfs.sort_values(["pct_chg", "amount"], ascending=False).head(prefilter)

    rows = []
    for _, item in etfs.iterrows():
        symbol = str(item.get("symbol", "")).zfill(6)
        if not symbol:
            continue
        try:
            raw = retry_call(lambda: ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start, end_date=end, adjust=""))
            hist = normalize_price_frame(raw)
            scored = score_stock(hist, 0.0)
            if scored.get("signal") != "买入":
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": item.get("name", ""),
                    "today_pct": item.get("pct_chg", ""),
                    "amount": item.get("amount", ""),
                    **scored,
                }
            )
        except Exception as exc:
            errors.append(f"ETF {symbol} {item.get('name', '')} 历史数据失败：{exc}")
    return sorted(rows, key=lambda x: x["score"], reverse=True)[:limit]


def build_report(
    report_date: str,
    start: str,
    board_type: str,
    prefilter: int,
    top_sectors: int,
    stocks_per_sector: int,
    etf_prefilter: int,
    top_etfs: int,
    output: Path,
    member_limit: int = 40,
    refresh: bool = True,
) -> Path:
    data = MarketDataService(settings)
    errors: list[str] = []
    sectors = data.list_sectors(board_type, refresh=refresh).copy()
    if "pct_chg" in sectors.columns:
        sectors["pct_chg"] = pd.to_numeric(sectors["pct_chg"], errors="coerce")
        sectors = sectors.sort_values(["pct_chg"], ascending=False)
    candidates = sectors.head(prefilter)

    try:
        benchmark = data.benchmark_history(settings.benchmark_symbol, start, report_date, refresh=refresh)
        healthy = market_is_healthy(benchmark)
    except Exception as exc:
        benchmark = pd.DataFrame()
        healthy = True
        errors.append(f"大盘过滤数据失败，已按通过处理：{exc}")

    sector_rows = []
    stock_rows = []
    for _, item in candidates.iterrows():
        sector = item["sector"]
        try:
            print(f"[report] scoring sector: {sector}", flush=True)
            hist = data.sector_history(sector, start, report_date, board_type, refresh=refresh)
            scored = score_sector(hist, benchmark)
            sector_rows.append({"sector": sector, "code": item.get("code", ""), "today_pct": item.get("pct_chg", ""), **scored})
        except Exception as exc:
            errors.append(f"{sector} 板块历史数据失败：{exc}")

    ranked = pd.DataFrame(sector_rows).sort_values("score", ascending=False).head(top_sectors)
    for sector_rank, sector in enumerate(ranked.to_dict("records"), start=1):
        sector_key = sector.get("code") or sector["sector"]
        try:
            print(f"[report] loading members: {sector['sector']}", flush=True)
            members = data.sector_members(sector_key, board_type, refresh=refresh)
        except Exception as exc:
            errors.append(f"{sector['sector']} 成分股失败：{exc}")
            continue

        picks = []
        allowed_members = members[members["symbol"].map(_is_allowed_stock)] if "symbol" in members.columns else members
        for _, member in allowed_members.head(member_limit).iterrows():
            raw_symbol = str(member.get("symbol", "")).strip()
            if not raw_symbol:
                continue
            symbol = raw_symbol.zfill(6)
            try:
                print(f"[report] scoring stock: {sector['sector']} {symbol}", flush=True)
                hist = data.stock_history(symbol, start, report_date, refresh=False)
                scored = score_stock(hist, float(sector["score"]))
                if not pd.notna(scored.get("score")) or scored.get("score") == float("-inf"):
                    continue
                if not healthy and scored["signal"] == "买入":
                    scored["signal"] = "观察"
                    scored["reason"] = "大盘过滤未通过、" + scored["reason"]
                if scored["signal"] == "卖出":
                    continue
                scored["open"] = None
                member_pct = _to_float_or_none(member.get("pct_chg"))
                scored["close"] = None
                scored["today_pct"] = member_pct / 100 if member_pct is not None else None
                picks.append(
                    {
                        "sector_rank": sector_rank,
                        "sector": sector["sector"],
                        "symbol": symbol,
                        "name": member.get("name", ""),
                        **scored,
                    }
                )
            except Exception as exc:
                errors.append(f"{sector['sector']} {symbol} 个股数据失败：{exc}")
        stock_rows.extend(
            sorted(picks, key=lambda x: (_stance_rank(x.get("signal", ""), x.get("score", 0)), -float(x.get("score", 0))))[
                :stocks_per_sector
            ]
        )

    etf_rows = _load_etf_candidates(start, report_date, etf_prefilter, top_etfs, errors)
    errors.extend(data.warnings)

    sector_table = _table(
        ["排名", "板块", "当日涨幅", "趋势分", "20日", "60日", "相对强度", "理由"],
        [
            [
                idx + 1,
                row["sector"],
                _fmt_num(row.get("today_pct", 0)) + "%",
                _fmt_num(row.get("score", 0), 4),
                _fmt_pct(row.get("ret20", 0)),
                _fmt_pct(row.get("ret60", 0)),
                _fmt_pct(row.get("relative_strength", 0)),
                row.get("reason", ""),
            ]
            for idx, row in enumerate(ranked.to_dict("records"))
        ],
    )
    stock_table = _table(
        ["板块排名", "板块", "代码", "名称", "观点", "观点评分", "信号", "当日涨幅", "开盘", "收盘/最新", "趋势分", "20日", "60日", "理由"],
        [
            (
                [
                    row["sector_rank"],
                    row["sector"],
                    row["symbol"],
                    row["name"],
                    _stance(row.get("signal", ""), row.get("score", 0))[0],
                    _view_score(row.get("score", 0)),
                    row["signal"],
                    _fmt_pct(row.get("today_pct", 0)),
                    _fmt_num(row.get("open", 0)),
                    _fmt_num(row.get("close", 0)),
                    _fmt_num(row.get("score", 0), 4),
                    _fmt_pct(row.get("ret20", 0)),
                    _fmt_pct(row.get("ret60", 0)),
                    row.get("reason", ""),
                ],
                _stance(row.get("signal", ""), row.get("score", 0))[1],
            )
            for row in stock_rows
        ],
    )
    etf_table = _table(
        ["排名", "代码", "名称", "观点", "观点评分", "信号", "当日涨幅", "收盘", "趋势分", "20日", "60日", "理由"],
        [
            (
                [
                    idx + 1,
                    row["symbol"],
                    row["name"],
                    _stance(row.get("signal", ""), row.get("score", 0))[0],
                    _view_score(row.get("score", 0)),
                    row["signal"],
                    _fmt_num(row.get("today_pct", 0)) + "%",
                    _fmt_num(row.get("close", 0)),
                    _fmt_num(row.get("score", 0), 4),
                    _fmt_pct(row.get("ret20", 0)),
                    _fmt_pct(row.get("ret60", 0)),
                    row.get("reason", ""),
                ],
                _stance(row.get("signal", ""), row.get("score", 0))[1],
            )
            for idx, row in enumerate(etf_rows)
        ],
    )

    error_block = ""
    if errors:
        shown = _summarize_errors(errors)
        error_block = "<section><h2>数据提示</h2><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in shown) + "</ul></section>"

    now = date.today().strftime("%Y-%m-%d")
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(report_date)} A股板块趋势报告</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color: #1f2937; background: #f6f7f9; }}
    header {{ padding: 28px 36px 18px; background: #111827; color: white; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; }}
    main {{ padding: 24px 36px 40px; }}
    .meta {{ color: #d1d5db; line-height: 1.7; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 18px 0 6px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #6b7280; font-size: 13px; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #edf0f3; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #eef2f7; color: #374151; font-weight: 700; }}
    tr:last-child td {{ border-bottom: 0; }}
    .stance-strong td {{ background: #fee2e2; }}
    .stance-strong td:nth-child(5), .stance-strong td:nth-child(8) {{ color: #991b1b; font-weight: 800; }}
    .stance-moderate td {{ background: #fff7ed; }}
    .stance-moderate td:nth-child(5), .stance-moderate td:nth-child(8) {{ color: #9a3412; font-weight: 700; }}
    .stance-watch td {{ background: #f8fafc; color: #475569; }}
    ul {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 28px; }}
    .note {{ margin-top: 24px; color: #6b7280; font-size: 13px; line-height: 1.7; }}
    @media (max-width: 900px) {{ .cards {{ grid-template-columns: repeat(2, 1fr); }} main, header {{ padding-left: 18px; padding-right: 18px; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(report_date)} A股板块趋势报告</h1>
    <div class="meta">生成日期：{now} ｜ 数据源：AkShare/东方财富 ｜ 逻辑：先板块趋势，后板块内个股趋势，ETF 独立候选</div>
  </header>
  <main>
    <div class="cards">
      <div class="card"><div class="label">大盘过滤</div><div class="value">{"通过" if healthy else "谨慎"}</div></div>
      <div class="card"><div class="label">预筛板块</div><div class="value">{len(candidates)}</div></div>
      <div class="card"><div class="label">入选板块</div><div class="value">{len(ranked)}</div></div>
      <div class="card"><div class="label">候选数量</div><div class="value">{len(stock_rows) + len(etf_rows)}</div></div>
    </div>
    <section>
      <h2>板块趋势排行</h2>
      {sector_table}
    </section>
    <section>
      <h2>板块内候选个股</h2>
      {stock_table}
    </section>
    <section>
      <h2>ETF 候选</h2>
      {etf_table}
    </section>
    {error_block}
    <p class="note">说明：已过滤科创板、北交所、创业板/30 开头个股；第一版使用当前板块成分近似历史成分；日线收盘信号用于次日关注。报告仅作研究辅助，不构成投资建议。</p>
  </main>
</body>
</html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 A 股板块趋势静态 HTML 报告")
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--start", default="20240101")
    parser.add_argument("--board-type", choices=["industry", "concept"], default="industry")
    parser.add_argument("--prefilter", type=int, default=80)
    parser.add_argument("--top-sectors", type=int, default=20)
    parser.add_argument("--stocks-per-sector", type=int, default=3)
    parser.add_argument("--member-limit", type=int, default=40)
    parser.add_argument("--use-cache", action="store_true", help="使用缓存行情；默认强制刷新，避免开盘价/收盘价/涨幅滞后")
    parser.add_argument("--etf-prefilter", type=int, default=50)
    parser.add_argument("--top-etfs", type=int, default=10)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    output = Path(args.output) if args.output else ROOT_DIR / f"{pd.to_datetime(args.date).strftime('%Y-%m-%d')}-report.html"
    path = build_report(
        args.date,
        args.start,
        args.board_type,
        args.prefilter,
        args.top_sectors,
        args.stocks_per_sector,
        args.etf_prefilter,
        args.top_etfs,
        output,
        args.member_limit,
        not args.use_cache,
    )
    print(path)


if __name__ == "__main__":
    main()
