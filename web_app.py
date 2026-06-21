from __future__ import annotations

import calendar
import csv
import io
import json
import os
import shutil
from time import monotonic
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
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
_scheduler: BackgroundScheduler | None = None
_market_cache: dict[str, Any] | None = None
_market_cache_fingerprint: tuple[tuple[float, int], ...] | None = None
_market_cache_loaded_at = 0.0
_market_cache_invalidated_job_id = ""
FINAL_JOB_STATUSES = {"success", "partial_success", "failed", "stopped"}
NON_CRAWLING_ADAPTER_LABELS = {
    "blocked": "수집 제외",
    "catalog": "카탈로그",
    "limited": "수집 제한",
    "permission_required": "공개 수집 제한",
}
NON_CRAWLING_ADAPTER_DETAILS = {
    "blocked": "사이트 정책/접근 제한으로 자동 수집 제외",
    "catalog": "카탈로그 정보만 등록",
    "limited": "예약 구조 확인 필요 · 자동 수집 제한",
    "permission_required": "로그인/권한/차단 구조라 공개 자동 수집 제외",
}
SERVER_CRAWL_PAUSED_STORE_IDS = {
    "imaginary_door_daehangno",
    "imaginary_door_seohyeon",
    "imaginary_door_gwangju",
    "imaginary_door_suwon",
    "imaginary_door_bupyeong",
    "imaginary_door_suwon2",
    "frank_gangnam",
}
CRAWL_STATUS_LABELS = {
    "success": "최근 수집 성공",
    "failed": "최근 수집 실패",
    "partial_success": "부분 수집 성공",
    "running": "수집 중",
    "queued": "수집 대기열",
    "stopped": "중단됨",
}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def path_fingerprint(path: Path) -> tuple[float, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0.0, 0)
    return (stat.st_mtime, stat.st_size)


def data_fingerprint() -> tuple[tuple[float, int], ...]:
    # SQLite WAL writes can live beside the main db file for a while, so include it.
    return (
        path_fingerprint(DB_PATH),
        path_fingerprint(Path(f"{DB_PATH}-wal")),
        path_fingerprint(CONFIG_PATH),
        path_fingerprint(MANUAL_ESTIMATES_PATH),
    )


def runtime_context_fields() -> dict[str, Any]:
    job = read_job_status(APP_HOME)
    return {
        "job": job,
        "job_running": job_is_running(job),
        "job_log": tail_job_log(job, max_lines=40),
    }


def invalidate_market_cache() -> None:
    global _market_cache, _market_cache_fingerprint, _market_cache_loaded_at
    _market_cache = None
    _market_cache_fingerprint = None
    _market_cache_loaded_at = 0.0


def invalidate_market_cache_for_finished_job(job: dict[str, Any] | None) -> bool:
    global _market_cache_invalidated_job_id
    if not job or str(job.get("status", "")) not in FINAL_JOB_STATUSES:
        return False
    job_id = str(job.get("job_id", ""))
    if not job_id or job_id == _market_cache_invalidated_job_id:
        return False
    invalidate_market_cache()
    _market_cache_invalidated_job_id = job_id
    return True


@app.middleware("http")
async def no_store_dynamic_pages(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


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


def text_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def short_text(value: object, limit: int = 90) -> str:
    text = text_value(value).replace("\n", " ")
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def int_value(value: object) -> int:
    if value is None:
        return 0
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def placeholder_collection_state(
    row: pd.Series,
    theme_count: int,
    price_ready_count: int,
) -> tuple[str, str]:
    store_id = text_value(row.get("store_id"))
    adapter_type = text_value(row.get("adapter_type"))
    latest_status = text_value(row.get("latest_crawl_status"))
    latest_error = short_text(row.get("latest_error"))
    collection_note = short_text(row.get("collection_note"))
    latest_at = format_date(row.get("latest_crawl_at"))
    total_slots = int_value(row.get("total_slots"))
    reserved_slots = int_value(row.get("reserved_slots"))
    revenue_slots = int_value(row.get("revenue_slots"))

    facts = [f"테마 {theme_count:,}개", f"가격 {price_ready_count:,}개"]
    if total_slots:
        facts.append(
            f"저장 슬롯 {total_slots:,}개"
            f"/예약 {reserved_slots:,}개"
            f"/매출반영 {revenue_slots:,}개"
        )
    if adapter_type:
        facts.append(f"어댑터 {adapter_type}")
    if latest_status:
        label = CRAWL_STATUS_LABELS.get(latest_status, latest_status)
        facts.append(label)
    if latest_at != "-":
        facts.append(f"마지막 {latest_at}")
    if latest_error:
        facts.append(f"오류 {latest_error}")
    elif collection_note:
        facts.append(collection_note)

    if store_id in SERVER_CRAWL_PAUSED_STORE_IDS:
        return "서버 수집 보류", " · ".join(
            ["Hetzner 서버에서 반복 타임아웃으로 자동 수집 보류", *facts]
        )

    if adapter_type in NON_CRAWLING_ADAPTER_LABELS:
        return NON_CRAWLING_ADAPTER_LABELS[adapter_type], " · ".join(
            [NON_CRAWLING_ADAPTER_DETAILS[adapter_type], *facts]
        )

    if theme_count <= 0:
        return "테마 미등록", " · ".join(["테마 정보가 없어 매출 계산 불가", *facts])

    if latest_status == "failed":
        return "수집 실패", " · ".join(["마지막 자동 수집 실패", *facts])

    if latest_status == "success":
        if total_slots > 0 and revenue_slots == 0:
            if reserved_slots > 0 and price_ready_count <= 0:
                return "수집 완료 · 가격 미반영", " · ".join(
                    ["예약 슬롯은 저장됐지만 가격이 0원이라 매출 계산 불가", *facts]
                )
            if reserved_slots > 0:
                return "수집 완료 · 매출 재계산 필요", " · ".join(
                    ["예약 슬롯은 저장됐지만 매출 재계산이 아직 반영되지 않음", *facts]
                )
            return "수집 완료 · 예약 0건", " · ".join(
                ["예약 슬롯은 저장됐지만 예약 완료 슬롯이 없음", *facts]
            )
        return "수집 완료 · 슬롯 0개", " · ".join(
            ["최근 수집은 성공했지만 매출 계산 가능한 예약 슬롯이 없음", *facts]
        )

    if latest_status:
        return CRAWL_STATUS_LABELS.get(latest_status, "수집 상태 확인"), " · ".join(
            ["예약 슬롯 반영 대기", *facts]
        )

    return "수집 전", " · ".join(
        ["자동 수집 대상이지만 아직 성공 로그가 없음", *facts]
    )


templates.env.filters["won"] = format_won
templates.env.filters["num"] = format_number
templates.env.filters["pct"] = format_pct
templates.env.filters["datefmt"] = format_date


def today_kst() -> date:
    return datetime.now(KST).date()


def crawl_runtime_settings() -> dict[str, int]:
    delay_min = max(5, int(os.getenv("LUMITRACK_DELAY_MIN_SECONDS", "5")))
    delay_max = max(delay_min, int(os.getenv("LUMITRACK_DELAY_MAX_SECONDS", "6")))
    return {
        "delay_min_seconds": delay_min,
        "delay_max_seconds": delay_max,
        "max_parallel_origins": max(
            1, int(os.getenv("LUMITRACK_MAX_PARALLEL_ORIGINS", "4"))
        ),
        "max_navigation_timeout_ms": max(
            5_000, int(os.getenv("LUMITRACK_NAVIGATION_TIMEOUT_MS", "10000"))
        ),
        "minimum_recrawl_minutes": max(
            0, int(os.getenv("LUMITRACK_MINIMUM_RECRAWL_MINUTES", "30"))
        ),
    }


def seed_database() -> None:
    target = DB_PATH
    source = PROJECT_DIR / "data" / "escape_room.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() and source.exists():
        shutil.copy2(source, target)


def ensure_catalog_synced(*, force: bool = False) -> None:
    global _catalog_synced, _startup_error
    if _catalog_synced and not force:
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
    maybe_start_auto_prepare_job()
    start_auto_refresh_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def seven_day_readiness(slots: pd.DataFrame) -> dict[str, Any]:
    today = today_kst()
    target_dates = {today + timedelta(days=offset) for offset in range(7)}
    if slots.empty:
        return {
            "ready": False,
            "reason": "아직 저장된 예약 슬롯이 없습니다.",
            "date_count": 0,
            "target_count": 7,
            "measured_slots": 0,
            "reserved_slots": 0,
            "store_count": 0,
            "latest_crawled_at": "",
        }

    source = slots.copy()
    source["date"] = pd.to_datetime(source["date"]).dt.date
    scoped = source[source["date"].isin(target_dates)].copy()
    measurable = scoped[scoped["status"].isin(MEASURABLE_STATUSES)].copy()
    date_count = int(measurable["date"].nunique()) if not measurable.empty else 0
    measured_slots = int(len(measurable))
    reserved_slots = (
        int(measurable["status"].eq("reserved").sum()) if not measurable.empty else 0
    )
    store_count = int(measurable["store_id"].nunique()) if not measurable.empty else 0
    latest_crawled_at = ""
    latest_date_is_today = False
    if "crawled_at" in scoped.columns and not scoped.empty:
        latest = pd.to_datetime(scoped["crawled_at"], utc=True, errors="coerce").max()
        if pd.notna(latest):
            latest_kst = latest.tz_convert(KST)
            latest_crawled_at = latest_kst.strftime("%Y-%m-%d %H:%M")
            latest_date_is_today = latest_kst.date() >= today

    ready = date_count >= 7 and measured_slots > 0 and latest_date_is_today
    reason = (
        "오늘 기준 7일 데이터가 준비되었습니다."
        if ready
        else "오늘 기준 7일 예약 데이터를 먼저 준비해야 합니다."
    )
    return {
        "ready": ready,
        "reason": reason,
        "date_count": date_count,
        "target_count": 7,
        "measured_slots": measured_slots,
        "reserved_slots": reserved_slots,
        "store_count": store_count,
        "latest_crawled_at": latest_crawled_at,
    }


def start_prepare_job(
    label: str = "7일 예약 데이터 준비",
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    settings = crawl_runtime_settings()
    if force_refresh:
        settings["minimum_recrawl_minutes"] = 0
    try:
        return start_crawl_job(
            app_home=APP_HOME,
            project_dir=PROJECT_DIR,
            label=label,
            days=7,
            config_path=CONFIG_PATH,
            db_path=DB_PATH,
            store_ids=None,
            **settings,
        )
    except CrawlJobAlreadyRunning:
        return read_job_status(APP_HOME)


def maybe_start_auto_prepare_job() -> None:
    if os.getenv("LUMITRACK_AUTOSTART_7DAY", "1") != "1":
        return
    job = read_job_status(APP_HOME)
    if job_is_running(job):
        return
    try:
        slots = load_slots(DB_PATH)
        readiness = seven_day_readiness(slots)
    except Exception:
        return
    if not readiness["ready"]:
        start_prepare_job("서버 시작 시 7일 데이터 자동 준비")


def start_auto_refresh_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    refresh_hours = float(os.getenv("LUMITRACK_AUTO_REFRESH_HOURS", "2"))
    if refresh_hours <= 0:
        return
    _scheduler = BackgroundScheduler(timezone=KST)
    _scheduler.add_job(
        maybe_start_auto_prepare_job,
        "interval",
        hours=refresh_hours,
        id="prepare_7day_data",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()


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


def build_market_data() -> dict[str, Any]:
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

    readiness = seven_day_readiness(slots)

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
        "readiness": readiness,
        "startup_error": _startup_error,
    }


def load_market_data(*, force_refresh: bool = False) -> dict[str, Any]:
    global _market_cache, _market_cache_fingerprint, _market_cache_loaded_at

    if force_refresh:
        invalidate_market_cache()

    runtime = runtime_context_fields()
    invalidate_market_cache_for_finished_job(runtime["job"])
    is_running = bool(runtime["job_running"])
    now = monotonic()
    ttl_seconds = (
        env_int("LUMITRACK_RUNNING_CACHE_SECONDS", 3600, minimum=1)
        if is_running
        else env_int("LUMITRACK_IDLE_CACHE_SECONDS", 5, minimum=1)
    )
    age_seconds = now - _market_cache_loaded_at

    if _market_cache is not None:
        if is_running and age_seconds < ttl_seconds:
            return {**_market_cache, **runtime}

        if not is_running:
            current_fingerprint = data_fingerprint()
            if (
                _market_cache_fingerprint == current_fingerprint
                and age_seconds < ttl_seconds
            ):
                return {**_market_cache, **runtime}

    fresh = build_market_data()
    _market_cache = fresh
    _market_cache_fingerprint = data_fingerprint()
    _market_cache_loaded_at = monotonic()
    return {**fresh, **runtime_context_fields()}


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
    catalog = data.get("catalog", pd.DataFrame())
    status = data.get("status", pd.DataFrame())
    slots = data.get("slots", pd.DataFrame())
    automatic = data.get("auto_projection", pd.DataFrame())
    columns = [
        "store_id",
        "store_name",
        "region",
        "estimate_source",
        "booking_rate_min",
        "booking_rate_max",
        "daily_revenue_min",
        "daily_revenue_mid",
        "daily_revenue_max",
        "monthly_revenue_min",
        "monthly_revenue_mid",
        "monthly_revenue_max",
        "observed_days",
        "observed_weekdays",
        "observed_weekday_names",
        "confidence",
    ]
    if industry.empty:
        table = pd.DataFrame(columns=columns)
    else:
        table = industry.copy()

    existing_ids = (
        set(table["store_id"].dropna().astype(str))
        if "store_id" in table.columns
        else set()
    )
    if not catalog.empty and "store_id" in catalog.columns:
        catalog_source = catalog.copy()
        catalog_source["store_id"] = catalog_source["store_id"].fillna("").astype(str)
        catalog_source = catalog_source[
            catalog_source["store_id"].ne("")
            & ~catalog_source["store_id"].isin(existing_ids)
        ]
        if not catalog_source.empty and not status.empty and "store_id" in status:
            status_columns = [
                "store_id",
                "adapter_type",
                "collection_note",
                "latest_crawl_status",
                "latest_crawl_at",
                "latest_error",
            ]
            available_status_columns = [
                column for column in status_columns if column in status.columns
            ]
            status_lookup = status[available_status_columns].copy()
            status_lookup["store_id"] = (
                status_lookup["store_id"].fillna("").astype(str)
            )
            status_lookup = status_lookup.drop_duplicates("store_id")
            catalog_source = catalog_source.merge(
                status_lookup,
                on="store_id",
                how="left",
            )
        if not catalog_source.empty and not slots.empty and "store_id" in slots:
            slot_source = slots.copy()
            slot_source["store_id"] = slot_source["store_id"].fillna("").astype(str)
            slot_source["expected_revenue"] = pd.to_numeric(
                slot_source.get("expected_revenue", 0),
                errors="coerce",
            ).fillna(0)
            slot_stats = (
                slot_source.assign(
                    measured=slot_source["status"].isin(MEASURABLE_STATUSES),
                    reserved=slot_source["status"].eq("reserved"),
                    revenue_ready=slot_source["expected_revenue"].gt(0),
                )
                .groupby("store_id", as_index=False)
                .agg(
                    total_slots=("status", "size"),
                    measured_slots=("measured", "sum"),
                    reserved_slots=("reserved", "sum"),
                    revenue_slots=("revenue_ready", "sum"),
                    stored_expected_revenue=("expected_revenue", "sum"),
                )
            )
            catalog_source = catalog_source.merge(
                slot_stats,
                on="store_id",
                how="left",
            )
        if not catalog_source.empty:
            value_column = (
                "booking_value_estimate"
                if "booking_value_estimate" in catalog_source.columns
                else "price"
            )
            aggregation: dict[str, tuple[str, str]] = {
                "store_name": ("store_name", "first"),
                "region": ("region", "first"),
                "theme_count": ("theme_name", "nunique"),
                "price_ready_count": ("price_ready", "sum"),
            }
            for column in [
                "adapter_type",
                "collection_note",
                "latest_crawl_status",
                "latest_crawl_at",
                "latest_error",
                "total_slots",
                "measured_slots",
                "reserved_slots",
                "revenue_slots",
                "stored_expected_revenue",
            ]:
                if column in catalog_source.columns:
                    aggregation[column] = (column, "first")
            placeholders = (
                catalog_source.assign(
                    price_ready=pd.to_numeric(
                        catalog_source.get(value_column, 0),
                        errors="coerce",
                    ).fillna(0).gt(0)
                )
                .groupby("store_id", as_index=False)
                .agg(**aggregation)
            )
            placeholder_rows = []
            for _, row in placeholders.iterrows():
                theme_count = int(row.get("theme_count", 0) or 0)
                price_ready_count = int(row.get("price_ready_count", 0) or 0)
                estimate_source, confidence = placeholder_collection_state(
                    row,
                    theme_count,
                    price_ready_count,
                )
                placeholder_rows.append(
                    {
                        "store_id": row["store_id"],
                        "store_name": row.get("store_name", ""),
                        "region": row.get("region", ""),
                        "estimate_source": estimate_source,
                        "booking_rate_min": 0.0,
                        "booking_rate_max": 0.0,
                        "daily_revenue_min": 0.0,
                        "daily_revenue_mid": 0.0,
                        "daily_revenue_max": 0.0,
                        "monthly_revenue_min": 0.0,
                        "monthly_revenue_mid": 0.0,
                        "monthly_revenue_max": 0.0,
                        "observed_days": 0,
                        "observed_weekdays": 0,
                        "observed_weekday_names": "-",
                        "confidence": confidence,
                    }
                )
            if placeholder_rows:
                table = pd.concat(
                    [table, pd.DataFrame(placeholder_rows)],
                    ignore_index=True,
                )

    if table.empty:
        return pd.DataFrame(columns=[*columns, "booking_rate_mid"])

    for column in columns:
        if column not in table.columns:
            table[column] = 0 if column.endswith(("_min", "_mid", "_max")) else ""

    if not automatic.empty and "store_id" in automatic.columns:
        auto_columns = [
            "store_id",
            "booking_rate",
            "monthly_revenue",
            "observed_days",
            "observed_weekday_names",
            "confidence",
            "total_slots",
            "reserved_slots",
        ]
        available_auto_columns = [
            column for column in auto_columns if column in automatic.columns
        ]
        auto = automatic[available_auto_columns].drop_duplicates("store_id").copy()
        auto = auto.rename(
            columns={
                "booking_rate": "auto_booking_rate",
                "monthly_revenue": "auto_monthly_revenue",
                "observed_days": "auto_observed_days",
                "observed_weekday_names": "auto_observed_weekday_names",
                "confidence": "auto_confidence",
                "total_slots": "auto_total_slots",
                "reserved_slots": "auto_reserved_slots",
            }
        )
        table = table.merge(auto, on="store_id", how="left")
        auto_observed = pd.to_numeric(
            table.get("auto_observed_days", 0),
            errors="coerce",
        ).fillna(0)
        has_auto = auto_observed.gt(0)
        manual_mask = table["estimate_source"].fillna("").astype(str).str.contains(
            "수동", na=False
        )
        table.loc[manual_mask & has_auto, "estimate_source"] = (
            "수동 관측 + 자동 수집"
        )
        table.loc[manual_mask & has_auto, "observed_days"] = auto_observed[
            manual_mask & has_auto
        ].astype(int)
        if "auto_observed_weekday_names" in table.columns:
            table.loc[manual_mask & has_auto, "observed_weekday_names"] = table.loc[
                manual_mask & has_auto,
                "auto_observed_weekday_names",
            ].fillna("-")
        table.loc[manual_mask & has_auto, "confidence"] = (
            table.loc[manual_mask & has_auto, "confidence"].fillna("수동 범위").astype(str)
            + " · 자동 "
            + auto_observed[manual_mask & has_auto].astype(int).astype(str)
            + "일 수집 확인"
        )
    table["booking_rate_mid"] = (
        table["booking_rate_min"].fillna(0) + table["booking_rate_max"].fillna(0)
    ) / 2
    table["_has_revenue"] = table["monthly_revenue_mid"].fillna(0).gt(0).astype(int)
    result = table.sort_values(
        ["_has_revenue", "monthly_revenue_mid", "store_name"],
        ascending=[False, False, True],
    ).drop(columns=["_has_revenue"])
    return result


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
    revenue = revenue_table(data)
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
    available_columns = [column for column in revenue_columns if column in revenue.columns]
    if available_columns:
        mapped = status.merge(
            revenue[available_columns].drop_duplicates("store_id"),
            on="store_id",
            how="left",
        )
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


def market_refresh_summary(data: dict[str, Any]) -> dict[str, Any]:
    slots = data.get("slots", pd.DataFrame())
    visible_7 = data.get("visible_7", pd.DataFrame())
    revenue = revenue_table(data)
    latest_crawled_at = ""
    if not slots.empty and "crawled_at" in slots.columns:
        latest = pd.to_datetime(slots["crawled_at"], utc=True, errors="coerce").max()
        if pd.notna(latest):
            latest_crawled_at = latest.tz_convert(KST).strftime("%Y-%m-%d %H:%M")

    return {
        "latest_crawled_at": latest_crawled_at,
        "slot_count": int(len(slots)) if not slots.empty else 0,
        "visible_7_slots": int(len(visible_7)) if not visible_7.empty else 0,
        "visible_7_reserved": int(visible_7["status"].eq("reserved").sum())
        if not visible_7.empty and "status" in visible_7.columns
        else 0,
        "stores_with_slots": int(slots["store_id"].nunique())
        if not slots.empty and "store_id" in slots.columns
        else 0,
        "revenue_store_count": int(
            revenue["monthly_revenue_mid"].fillna(0).gt(0).sum()
        )
        if not revenue.empty and "monthly_revenue_mid" in revenue.columns
        else 0,
        "projected_monthly_total": float(
            data.get("metrics", {}).get("projected_monthly_total", 0) or 0
        ),
    }


def add_dashboard_context(context: dict[str, Any]) -> dict[str, Any]:
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
    return context


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    context = base_context(request, "prepare")
    if not context["readiness"]["ready"] and request.query_params.get("open") != "1":
        return templates.TemplateResponse(
            request=request, name="prepare.html", context=context
        )
    context["active"] = "home"
    add_dashboard_context(context)
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context=context
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    context = base_context(request, "home")
    add_dashboard_context(context)
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
            "rows": records(table, 1000),
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


@app.get("/prepare", response_class=HTMLResponse)
def prepare(request: Request) -> HTMLResponse:
    context = base_context(request, "prepare")
    return templates.TemplateResponse(
        request=request, name="prepare.html", context=context
    )


@app.post("/prepare/start")
def prepare_start() -> RedirectResponse:
    start_prepare_job("7일 예약 데이터 준비", force_refresh=True)
    return RedirectResponse(url="/prepare", status_code=303)


@app.post("/crawl/start")
def crawl_start(days: int = Form(...)) -> RedirectResponse:
    if days not in {1, 7}:
        raise HTTPException(status_code=400, detail="days must be 1 or 7")
    label = "오늘 예약 현황 업데이트" if days == 1 else "7일 예약/매출 업데이트"
    ensure_catalog_synced(force=True)
    invalidate_market_cache()
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
            delay_max_seconds=int(os.getenv("LUMITRACK_DELAY_MAX_SECONDS", "6")),
            max_parallel_origins=int(os.getenv("LUMITRACK_MAX_PARALLEL_ORIGINS", "4")),
            max_navigation_timeout_ms=int(
                os.getenv("LUMITRACK_NAVIGATION_TIMEOUT_MS", "10000")
            ),
            # Manual button clicks should refresh the market data even if a
            # previous attempt saved zero slots. The 30-minute skip remains only
            # for automatic background refresh jobs.
            minimum_recrawl_minutes=0,
        )
    except CrawlJobAlreadyRunning:
        pass
    return RedirectResponse(url="/status", status_code=303)


@app.post("/crawl/clear")
def crawl_clear() -> RedirectResponse:
    clear_job_status(APP_HOME)
    invalidate_market_cache()
    return RedirectResponse(url="/status", status_code=303)


@app.get("/api/crawl/status")
def crawl_status_api(log: int = 0) -> dict[str, Any]:
    job = read_job_status(APP_HOME)
    market_refreshed = invalidate_market_cache_for_finished_job(job)
    payload = {
        "job": job,
        "running": job_is_running(job),
        "market_refreshed": market_refreshed,
    }
    if log:
        payload["log"] = tail_job_log(job, max_lines=80)
    return payload


@app.post("/api/market/refresh")
def market_refresh_api() -> dict[str, Any]:
    ensure_catalog_synced(force=True)
    data = load_market_data(force_refresh=True)
    runtime = runtime_context_fields()
    return {
        "ok": True,
        "running": runtime["job_running"],
        "job_status": runtime["job"].get("status") if runtime["job"] else "idle",
        "summary": market_refresh_summary(data),
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
