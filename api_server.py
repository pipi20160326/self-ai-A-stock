from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from pydantic import BaseModel

from src.backtest import BacktestEngine
from src.config import settings
from src.data import MarketDataService
from src.db import (
    add_monitor_target,
    fetch_monitor_events,
    fetch_monitor_targets,
    fetch_recent_reports,
    fetch_report_html,
    init_database,
    set_monitor_active,
)
from src.manual_score import score_sector_key, score_stock_code
from src.monitor import run_monitor, score_etf


app = FastAPI(title="AStock Trend API")


class ScoreResponse(BaseModel):
    kind: str
    code: str
    scored: dict


class BacktestRequest(BaseModel):
    start: str
    end: str
    board_type: str = "industry"
    top_sectors: int = 5
    stocks_per_sector: int = 3
    initial_cash: float = 1_000_000


class MonitorTargetRequest(BaseModel):
    target_type: str
    code: str
    name: str = ""
    min_score: float | None = None
    required_signal: str | None = None
    note: str = ""


def service() -> MarketDataService:
    return MarketDataService(settings)


@app.on_event("startup")
def startup() -> None:
    init_database()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/score", response_model=ScoreResponse)
def score(kind: str, code: str, start: str = settings.start_date, end: str | None = None) -> dict:
    end = end or date.today().strftime("%Y%m%d")
    data = service()
    if kind == "stock":
        scored, _ = score_stock_code(data, code, start, end)
    elif kind == "sector":
        scored, _ = score_sector_key(data, code, start, end, "industry", settings)
    elif kind == "etf":
        scored, _ = score_etf(code, start, end)
    else:
        raise ValueError("kind must be stock, sector, or etf")
    return {"kind": kind, "code": code, "scored": scored}


@app.post("/backtest")
def backtest(req: BacktestRequest) -> dict:
    result = BacktestEngine(service(), settings).run(
        req.start,
        req.end,
        req.board_type,
        req.top_sectors,
        req.stocks_per_sector,
        req.initial_cash,
    )
    return {
        "metrics": result.metrics,
        "equity": result.equity.tail(300).to_dict("records"),
        "trades": result.trades.tail(300).to_dict("records") if not result.trades.empty else [],
    }


@app.get("/reports")
def reports(limit: int = 30) -> list[dict]:
    return fetch_recent_reports(limit).astype(str).to_dict("records")


@app.get("/reports/{report_id}/html")
def report_html(report_id: int) -> dict:
    return {"html": fetch_report_html(report_id)}


@app.get("/monitors")
def monitors() -> list[dict]:
    return fetch_monitor_targets().astype(str).to_dict("records")


@app.post("/monitors")
def create_monitor(req: MonitorTargetRequest) -> dict:
    target_id = add_monitor_target(req.target_type, req.code, req.name, req.min_score, req.required_signal, req.note)
    return {"id": target_id}


@app.post("/monitors/{target_id}/active")
def update_monitor_active(target_id: int, active: bool) -> dict:
    set_monitor_active(target_id, active)
    return {"ok": True}


@app.post("/monitor/run")
def run_monitor_now(notify: bool = False) -> dict:
    return {"messages": run_monitor(notify_enabled=notify)}


@app.get("/monitor/events")
def monitor_events(limit: int = 100) -> list[dict]:
    return fetch_monitor_events(limit).astype(str).to_dict("records")
