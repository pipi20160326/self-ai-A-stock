from __future__ import annotations

import argparse
from datetime import date

from src.backtest import BacktestEngine
from src.config import ensure_directories, settings
from src.data import MarketDataService
from src.reports import save_backtest, save_daily_scan
from src.strategy import TrendScanner
from src.manual_score import score_sector_key, score_stock_code


def cmd_scan(args: argparse.Namespace) -> None:
    service = MarketDataService(settings)
    scanner = TrendScanner(service, settings)
    scan = scanner.scan(args.start or settings.start_date, args.end or date.today().strftime("%Y%m%d"), args.board_type)
    path = save_daily_scan(scan, args.end)
    print(f"扫描完成: {path}")
    print(scan.head(30).to_string(index=False))


def cmd_backtest(args: argparse.Namespace) -> None:
    service = MarketDataService(settings)
    engine = BacktestEngine(service, settings)
    result = engine.run(args.start, args.end, args.board_type, args.top_sectors, args.stocks_per_sector, args.initial_cash)
    paths = save_backtest(result.equity, result.trades, result.metrics, f"{args.start}_{args.end}")
    print("回测完成:")
    for key, path in paths.items():
        print(f"- {key}: {path}")
    print(result.metrics)


def cmd_update_data(args: argparse.Namespace) -> None:
    service = MarketDataService(settings)
    start = args.start or settings.start_date
    end = args.end or date.today().strftime("%Y%m%d")
    service.list_sectors(args.board_type, refresh=True)
    service.benchmark_history(settings.benchmark_symbol, start, end, refresh=True)
    if args.warm_workspace:
        scanner = TrendScanner(service, settings)
        ranked = scanner.rank_sectors(start, end, args.board_type, args.top_sectors)
        print(f"预热板块: {len(ranked)}")
        for _, sector_row in ranked.iterrows():
            sector = str(sector_row["sector"])
            try:
                members = service.sector_members(sector, args.board_type, refresh=True)
            except Exception as exc:
                print(f"- {sector} 成分股失败: {exc}")
                continue
            warmed = 0
            for _, member in members.head(args.member_limit).iterrows():
                symbol = str(member.get("symbol", "")).strip().zfill(6)
                if not symbol:
                    continue
                try:
                    service.stock_history(symbol, start, end, refresh=True)
                    warmed += 1
                except Exception as exc:
                    print(f"  {symbol} K线失败: {exc}")
            print(f"- {sector}: 已预热 {warmed} 只")
        scan = scanner.scan(start, end, args.board_type, args.top_sectors, args.stocks_per_sector, member_limit=args.member_limit)
        path = save_daily_scan(scan, pd.to_datetime(end).strftime("%Y-%m-%d"))
        print(f"工作台扫描已生成: {path}")
    print("基础数据缓存已更新。")


def cmd_score(args: argparse.Namespace) -> None:
    service = MarketDataService(settings)
    end = args.end or date.today().strftime("%Y%m%d")
    start = args.start or settings.start_date
    if args.kind == "stock":
        scored, _ = score_stock_code(service, args.code, start, end)
    else:
        scored, _ = score_sector_key(service, args.code, start, end, args.board_type, settings)
    print(f"{args.kind} {args.code} 评分:")
    for key, value in scored.items():
        print(f"- {key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A 股板块趋势扫描与回测工具")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--start")
    scan.add_argument("--end")
    scan.add_argument("--board-type", choices=["industry", "concept"], default="industry")
    scan.set_defaults(func=cmd_scan)

    backtest = sub.add_parser("backtest")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--board-type", choices=["industry", "concept"], default="industry")
    backtest.add_argument("--top-sectors", type=int, default=settings.scan_top_sectors)
    backtest.add_argument("--stocks-per-sector", type=int, default=settings.scan_top_stocks_per_sector)
    backtest.add_argument("--initial-cash", type=float, default=settings.initial_cash)
    backtest.set_defaults(func=cmd_backtest)

    update = sub.add_parser("update-data")
    update.add_argument("--start")
    update.add_argument("--end")
    update.add_argument("--board-type", choices=["industry", "concept"], default="industry")
    update.add_argument("--warm-workspace", action="store_true", help="预热工作台所需板块、成分股K线并生成扫描CSV")
    update.add_argument("--top-sectors", type=int, default=settings.scan_top_sectors)
    update.add_argument("--stocks-per-sector", type=int, default=settings.scan_top_stocks_per_sector)
    update.add_argument("--member-limit", type=int, default=settings.daily_member_limit)
    update.set_defaults(func=cmd_update_data)

    score = sub.add_parser("score")
    score.add_argument("--kind", choices=["stock", "sector"], required=True)
    score.add_argument("--code", required=True)
    score.add_argument("--start")
    score.add_argument("--end")
    score.add_argument("--board-type", choices=["industry", "concept"], default="industry")
    score.set_defaults(func=cmd_score)
    return parser


def main() -> None:
    ensure_directories()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
