from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from src.config import ROOT_DIR, settings
from src.html_report import build_report


app = FastAPI(title="AStock Manual Report API")


class ManualReportRequest(BaseModel):
    report_date: str | None = None
    start: str = settings.start_date
    board_type: str = "industry"
    prefilter: int = settings.daily_prefilter
    top_sectors: int = settings.daily_top_sectors
    stocks_per_sector: int = settings.daily_stocks_per_sector
    member_limit: int = settings.daily_member_limit
    etf_prefilter: int = settings.daily_etf_prefilter
    top_etfs: int = settings.daily_top_etfs
    refresh: bool = True


@app.get("/health")
def health() -> dict:
    return {"ok": True, "database": False}


@app.post("/daily-report/run")
def run_manual_report(req: ManualReportRequest) -> dict:
    report_day = date.today() if not req.report_date else date.fromisoformat(req.report_date)
    output = ROOT_DIR / f"{report_day:%Y-%m-%d}-report.html"
    path = build_report(
        report_date=report_day.strftime("%Y%m%d"),
        start=req.start,
        board_type=req.board_type,
        prefilter=req.prefilter,
        top_sectors=req.top_sectors,
        stocks_per_sector=req.stocks_per_sector,
        etf_prefilter=req.etf_prefilter,
        top_etfs=req.top_etfs,
        output=output,
        member_limit=req.member_limit,
        refresh=req.refresh,
    )
    return {"ok": True, "report_date": report_day.isoformat(), "path": str(path)}


@app.get("/daily-report/html")
def report_html(report_date: str | None = None) -> dict:
    report_day = date.today() if not report_date else date.fromisoformat(report_date)
    path = ROOT_DIR / f"{report_day:%Y-%m-%d}-report.html"
    return {"path": str(path), "exists": path.exists(), "html": path.read_text(encoding="utf-8") if path.exists() else ""}
