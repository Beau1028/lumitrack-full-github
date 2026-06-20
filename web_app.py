from __future__ import annotations

import calendar
import csv
import io
import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scraper.analytics import (
    MEASURABLE_STATUSES,
    combine_store_revenue_estimates,
    genre_monthly_summary,
    hourly_rates,
    load_catalog,
    load_manual_estimates,
    load_slots,
    load_store_status,
    region_rates,
    store_monthly_projections,
    theme_rates,
    weekday_rates,
)
from scraper.config import load_stores
from scraper.crawl_jobs import (
    CrawlJobAlreadyRunning,
    clear_job_status,
    job_is_running,
    read_job_status,
    start_crawl_job,
    tail_job_log,
)
from scraper.database import Database


PROJECT_DIR = Path(__file__).resolve().parent
APP_HOME = Path(os.getenv("ESCAPE_ROOM_MONITOR_HOME", str(PROJECT_DIR)))
DB_PATH = Path(
    os.getenv("ESCAPE_ROOM_DB_PATH", str(APP_HOME / "data" / "escape_room.db"))
)
CONFIG_PATH = Path(os.getenv("LUMITRACK_CONFIG_PATH", str(PROJECT_DIR / "stores.yaml")))
MANUAL_ESTIMATES_PATH = PROJECT_DIR / "manual_estimates.yaml"
KST = ZoneInfo("Asia/Seoul")

WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]


app = FastAPI(title="LumiTrack", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=PROJECT_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PROJECT_DIR / "templates")

_catalog_synced = False
_startup_error = ""


def format_won(value: object) -> str:
    try:
        amount = int(round(float(value or 0)))
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:,}원"


def format_number(value: object) -> str:
    try:
        number = int(round(float(value or 0)))
    except (TypeError, ValueError):
        number = 0
    return f"{number:,}"


def format_pct(value: object) -> str:
    try:
        percent = float(value or 0)
    except (TypeError, ValueError):
        percent = 0.0
    return f"{percent:.1f}%"


def format_date(value: object) -> str:
    if value is None or value == "":
        return "-"
    try:
        if pd.isna(value):
            return "-"
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(KST)
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


templates.env.filters["won"] = format_won
templates.env.filters["num"] = format_number
templates.env.filters["pct"] = format_pct
templates.env.filters["datefmt"] = format_date


def today_kst() -> date:
    return datetime.now(KST).date()


def seed_database() -> None:
    target = DB_PATH
    source = PROJECT_DIR / "data" / "escape_room.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() and source.exists():
        shutil.copy2(source, target)


def ensure_catalog_synced() -> None:
    global _catalog_synced, _startup_error
    if _catalog_synced:
        return
    try:
        seed_database()
        database = Database(DB_PATH)
        database.initialize()
        stores = load_stores(CONFIG_PATH)
        database.sync_stores(stores)
        database.recalculate_slot_estimates()
        _catalog_synced = True
        _startup_error = ""
    except Exception as exc:  # pragma: no cover - shown in UI for operators.
        _startup_error = f"{type(exc).__name__}: {exc}"


@app.on_event("startup")
def on_startup() -> None:
    ensure_catalog_synced()


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat() if not pd.isna(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            return str(value)
    return value


def records(frame: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    source = frame.head(limit).copy() if limit else frame.copy()
    return [
        {column: clean_value(value) for column, value in row.items()}
        for row in source.to_dict(orient="records")
    ]


def chart_payload(
    frame: pd.DataFrame,
    label_column: str,
    value_column: str,
    *,
    limit: int = 12,
) -> str:
    if frame.empty or label_column not in frame or value_column not in frame:
        payload = {"labels": [], "values": []}
    else:
        source = frame.head(limit)
        payload = {
            "labels": [str(value) for value in source[label_column].fillna("-")],
            "values": [
                float(value or 0)
                for value in pd.to_numeric(source[value_column], errors="coerce")
                .fillna(0)
                .tolist()
            ],
        }
    return json.dumps(payload, ensure_ascii=False)


def load_market_data() -> dict[str, Any]:
    ensure_catalog_synced()
    today = today_kst()
    slots = load_slots(DB_PATH)
    catalog = load_catalog(DB_PATH)
    status = load_store_status(DB_PATH)
    manual_stores, manual_themes, manual_meta = load_manual_estimates(
        MANUAL_ESTIMATES_PATH
    )

    if not slots.empty:
        slots = slots.copy()
        slots["date"] = pd.to_datetime(slots["date"]).dt.date

    days_in_month = calendar.monthrange(today.year, today.month)[1]
    auto_projection = store_monthly_projections(slots, today.year, today.month)
    if not auto_projection.empty:
        auto_projection = auto_projection[
            auto_projection["observed_days"].fillna(0).gt(0)
            & auto_projection["monthly_revenue"].fillna(0).gt(0)
        ].copy()

    industry = combine_store_revenue_estimates(
        auto_projection, manual_stores, days_in_month
    )

    last_7_start = today
    last_7_end = today + timedelta(days=6)
    if slots.empty:
        visible_7 = slots
        today_slots = slots
    else:
        visible_7 = slots[
            (slots["date"] >= last_7_start) & (slots["date"] <= last_7_end)
        ].copy()
        today_slots = slots[slots["date"] == today].copy()

    measured_slots = int(visible_7["status"].isin(MEASURABLE_STATUSES).sum()) if not visible_7.empty else 0
    reserved_slots = int(visible_7["status"].eq("reserved").sum()) if not visible_7.empty else 0
    booking_rate = reserved_slots / measured_slots * 100 if measured_slots else 0.0

    projected_monthly_total = (
        float(industry["monthly_revenue_mid"].sum()) if not industry.empty else 0.0
    )
    store_count_with_revenue = (
        int(industry["store_id"].nunique()) if not industry.empty else 0
    )
    average_store_monthly = (
        projected_monthly_total / store_count_with_revenue
        if store_count_with_revenue
        else 0.0
    )
    today_revenue = (
        float(today_slots["expected_revenue"].sum()) if not today_slots.empty else 0.0
    )
    theme_count = (
        int(catalog[["store_id", "theme_name"]].dropna().drop_duplicates().shape[0])
        if not catalog.empty
        else 0
    )
    price_missing = 0
    if not catalog.empty and "booking_value_estimate" in catalog.columns:
        price_missing = int(catalog["booking_value_estimate"].fillna(0).le(0).sum())

    job = read_job_status(APP_HOME)
    job_log = tail_job_log(job, max_lines=40)

    return {
        "today": today,
        "month_label": f"{today.year}년 {today.month}월",
        "slots": slots,
        "catalog": catalog,
        "status": status,
        "manual_stores": manual_stores,
        "manual_themes": manual_themes,
        "manual_meta": manual_meta,
        "auto_projection": auto_projection,
        "industry": industry,
        "visible_7": visible_7,
        "today_slots": today_slots,
        "metrics": {
            "booking_rate": booking_rate,
            "measured_slots": measured_slots,
            "reserved_slots": reserved_slots,
            "projected_monthly_total": projected_monthly_total,
            "store_count_with_revenue": store_count_with_revenue,
            "average_store_monthly": average_store_monthly,
            "today_revenue": today_revenue,
            "theme_count": theme_count,
            "price_missing": price_missing,
            "manual_store_count": int(manual_stores["store_id"].nunique())
            if not manual_stores.empty
            else 0,
        },
        "job": job,
        "job_running": job_is_running(job),
        "job_log": job_log,
        "startup_error": _startup_error,
    }


def base_context(request: Request, active: str) -> dict[str, Any]:
    data = load_market_data()
    return {
        "request": request,
        "active": active,
        "app_name": "LumiTrack",
        "tagline": "방탈출 예약률 · 추정 매출 모니터",
        **data,
    }


def revenue_table(data: dict[str, Any]) -> pd.DataFrame:
    industry = data["industry"]
    if industry.empty:
        return pd.DataFrame(
            columns=[
                "store_id",
                "store_name",
                "region",
                "estimate_source",
                "booking_rate_mid",
                "monthly_revenue_mid",
                "daily_revenue_mid",
                "observed_days",
                "confidence",
            ]
        )
    table = industry.copy()
    table["booking_rate_mid"] = (
        table["booking_rate_min"].fillna(0) + table["booking_rate_max"].fillna(0)
    ) / 2
    return table.sort_values("monthly_revenue_mid", ascending=False)


def theme_table(data: dict[str, Any]) -> pd.DataFrame:
    source = data["visible_7"]
    if source.empty:
        return pd.DataFrame()
    table = theme_rates(source).copy()
    revenue = (
        source.groupby(["store_id", "theme_name"], as_index=False)
        .agg(estimated_revenue=("expected_revenue", "sum"))
    )
    catalog = data["catalog"]
    price_columns = [
        "store_id",
        "theme_name",
        "booking_value_estimate",
        "per_person_estimate",
        "pricing_summary",
        "price_verified_at",
    ]
    available_columns = [column for column in price_columns if column in catalog.columns]
    if available_columns:
        prices = catalog[available_columns].drop_duplicates(["store_id", "theme_name"])
        table = table.merge(prices, on=["store_id", "theme_name"], how="left")
    table = table.merge(revenue, on=["store_id", "theme_name"], how="left")
    table["estimated_revenue"] = table["estimated_revenue"].fillna(0)
    return table.sort_values(
        ["booking_rate", "estimated_revenue", "total_slots"],
        ascending=[False, False, False],
    )


def map_table(data: dict[str, Any]) -> pd.DataFrame:
    status = data["status"]
    industry = data["industry"]
    if status.empty:
        return pd.DataFrame()
    revenue_columns = [
        "store_id",
        "estimate_source",
        "monthly_revenue_mid",
        "booking_rate_min",
        "booking_rate_max",
        "confidence",
    ]
    available_columns = [column for column in revenue_columns if column in industry.columns]
    if available_columns:
        mapped = status.merge(industry[available_columns], on="store_id", how="left")
    else:
        mapped = status.copy()
    mapped["monthly_revenue_mid"] = mapped.get("monthly_revenue_mid", 0).fillna(0)
    mapped["booking_rate_min"] = mapped.get("booking_rate_min", 0).fillna(0)
    mapped["booking_rate_max"] = mapped.get("booking_rate_max", 0).fillna(0)
    mapped["booking_rate_mid"] = (
        mapped["booking_rate_min"] + mapped["booking_rate_max"]
    ) / 2
    mapped = mapped.dropna(subset=["latitude", "longitude"]).copy()
    return mapped


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    context = base_context(request, "home")
    visible_7 = context["visible_7"]
    revenue = revenue_table(context)
    genre = genre_monthly_summary(
        context["slots"], context["today"].year, context["today"].month
    )
    context.update(
        {
            "top_revenue": records(revenue, 10),
            "genre_rows": records(genre, 8),
            "region_chart_json": chart_payload(
                region_rates(visible_7), "region", "booking_rate", limit=10
            ),
            "store_chart_json": chart_payload(
                revenue, "store_name", "monthly_revenue_mid", limit=10
            ),
            "hour_chart_json": chart_payload(
                hourly_rates(visible_7), "time_band", "booking_rate", limit=18
            ),
            "weekday_chart_json": chart_payload(
                weekday_rates(visible_7), "weekday", "booking_rate", limit=7
            ),
        }
    )
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context=context
    )


@app.get("/revenue", response_class=HTMLResponse)
def revenue(request: Request) -> HTMLResponse:
    context = base_context(request, "revenue")
    table = revenue_table(context)
    genre = genre_monthly_summary(
        context["slots"], context["today"].year, context["today"].month
    )
    context.update(
        {
            "rows": records(table, 250),
            "genre_rows": records(genre, 100),
            "store_chart_json": chart_payload(
                table, "store_name", "monthly_revenue_mid", limit=18
            ),
            "genre_chart_json": chart_payload(
                genre, "genre", "average_monthly_revenue", limit=12
            ),
        }
    )
    return templates.TemplateResponse(
        request=request, name="revenue.html", context=context
    )


@app.get("/themes", response_class=HTMLResponse)
def themes(request: Request) -> HTMLResponse:
    context = base_context(request, "themes")
    table = theme_table(context)
    low = table.sort_values(
        ["booking_rate", "total_slots"], ascending=[True, False]
    ) if not table.empty else table
    context.update(
        {
            "top_rows": records(table, 80),
            "low_rows": records(low, 50),
            "theme_chart_json": chart_payload(
                table, "theme_name", "booking_rate", limit=18
            ),
        }
    )
    return templates.TemplateResponse(
        request=request, name="themes.html", context=context
    )


@app.get("/map", response_class=HTMLResponse)
def revenue_map(request: Request) -> HTMLResponse:
    context = base_context(request, "map")
    mapped = map_table(context)
    markers = records(mapped, 500)
    if markers:
        center_lat = sum(float(row["latitude"]) for row in markers) / len(markers)
        center_lon = sum(float(row["longitude"]) for row in markers) / len(markers)
    else:
        center_lat, center_lon = 37.5563, 126.9236
    context.update(
        {
            "markers_json": json.dumps(markers, ensure_ascii=False),
            "center_lat": center_lat,
            "center_lon": center_lon,
            "mapped_count": len(markers),
            "map_rows": markers,
        }
    )
    return templates.TemplateResponse(
        request=request, name="map.html", context=context
    )


@app.get("/status", response_class=HTMLResponse)
def status(request: Request) -> HTMLResponse:
    context = base_context(request, "status")
    status_frame = context["status"].copy()
    if not status_frame.empty:
        status_frame["latest_crawl_at"] = status_frame["latest_crawl_at"].map(format_date)
    context.update({"store_status_rows": records(status_frame, 500)})
    return templates.TemplateResponse(
        request=request, name="status.html", context=context
    )


@app.get("/raw", response_class=HTMLResponse)
def raw_slots(request: Request) -> HTMLResponse:
    context = base_context(request, "raw")
    source = context["slots"]
    if not source.empty:
        source = source.sort_values(["date", "store_name", "theme_name", "time"], ascending=[False, True, True, True])
    context.update({"slot_rows": records(source, 800)})
    return templates.TemplateResponse(
        request=request, name="raw.html", context=context
    )


@app.get("/report", response_class=HTMLResponse)
def report(request: Request) -> HTMLResponse:
    context = base_context(request, "report")
    table = revenue_table(context)
    context.update({"rows": records(table, 80)})
    return templates.TemplateResponse(
        request=request, name="report.html", context=context
    )


@app.post("/crawl/start")
def crawl_start(days: int = Form(...)) -> RedirectResponse:
    if days not in {1, 7}:
        raise HTTPException(status_code=400, detail="days must be 1 or 7")
    label = "오늘 예약 현황 업데이트" if days == 1 else "7일 예약/매출 업데이트"
    try:
        start_crawl_job(
            app_home=APP_HOME,
            project_dir=PROJECT_DIR,
            label=label,
            days=days,
            config_path=CONFIG_PATH,
            db_path=DB_PATH,
            store_ids=None,
            delay_min_seconds=int(os.getenv("LUMITRACK_DELAY_MIN_SECONDS", "5")),
            delay_max_seconds=int(os.getenv("LUMITRACK_DELAY_MAX_SECONDS", "8")),
            max_parallel_origins=int(os.getenv("LUMITRACK_MAX_PARALLEL_ORIGINS", "4")),
            max_navigation_timeout_ms=int(
                os.getenv("LUMITRACK_NAVIGATION_TIMEOUT_MS", "12000")
            ),
        )
    except CrawlJobAlreadyRunning:
        pass
    return RedirectResponse(url="/status", status_code=303)


@app.post("/crawl/clear")
def crawl_clear() -> RedirectResponse:
    clear_job_status(APP_HOME)
    return RedirectResponse(url="/status", status_code=303)


@app.get("/api/crawl/status")
def crawl_status_api() -> dict[str, Any]:
    job = read_job_status(APP_HOME)
    return {
        "job": job,
        "running": job_is_running(job),
        "log": tail_job_log(job, max_lines=80),
    }


@app.get("/download/store_revenue.csv")
def download_store_revenue() -> Response:
    data = load_market_data()
    table = revenue_table(data)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "매장",
            "지역",
            "추정방식",
            "예약률",
            "월예상매출",
            "일평균매출",
            "관측일수",
            "신뢰도",
        ]
    )
    for row in records(table):
        writer.writerow(
            [
                row.get("store_name", ""),
                row.get("region", ""),
                row.get("estimate_source", ""),
                format_pct(row.get("booking_rate_mid", 0)),
                format_won(row.get("monthly_revenue_mid", 0)),
                format_won(row.get("daily_revenue_mid", 0)),
                row.get("observed_days", 0),
                row.get("confidence", ""),
            ]
        )
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=lumitrack_store_revenue.csv"},
    )


@app.get("/download/report.html")
def download_report_html(request: Request) -> HTMLResponse:
    context = base_context(request, "report")
    table = revenue_table(context)
    context.update({"rows": records(table, 80), "download_mode": True})
    html = templates.get_template("report.html").render(context)
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": "attachment; filename=lumitrack_report.html"},
    )
