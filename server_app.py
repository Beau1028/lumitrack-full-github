from __future__ import annotations

import os
import traceback
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from scraper.analytics import (
    MEASURABLE_STATUSES,
    filter_slots,
    genre_monthly_summary,
    load_catalog,
    load_manual_estimates,
    load_slots,
    load_store_status,
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
from scraper.logging_utils import configure_logging
from scraper.runner import NON_CRAWLING_ADAPTERS, RETIRED_STORE_IDS


PROJECT_DIR = Path(__file__).resolve().parent
APP_HOME = Path(os.getenv("ESCAPE_ROOM_MONITOR_HOME", PROJECT_DIR))
DB_PATH = APP_HOME / "data" / "escape_room.db"
CONFIG_PATH = PROJECT_DIR / "stores.yaml"
MANUAL_PATH = PROJECT_DIR / "manual_estimates.yaml"
KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).date()

CRAWL_MAX_PARALLEL_ORIGINS = int(os.getenv("LUMITRACK_MAX_PARALLEL_ORIGINS", "4"))
CRAWL_NAVIGATION_TIMEOUT_MS = int(os.getenv("LUMITRACK_NAVIGATION_TIMEOUT_MS", "15000"))
CRAWL_DELAY_MIN_SECONDS = int(os.getenv("LUMITRACK_DELAY_MIN_SECONDS", "5"))
CRAWL_DELAY_MAX_SECONDS = int(os.getenv("LUMITRACK_DELAY_MAX_SECONDS", "7"))


st.set_page_config(
    page_title="LumiTrack",
    page_icon="L",
    layout="wide",
    initial_sidebar_state="collapsed",
)
configure_logging(APP_HOME / "logs")


st.markdown(
    """
    <style>
    :root {
      --blue: #3182f6;
      --ink: #191f28;
      --muted: #6b7684;
      --line: #e5e8ef;
      --card: #ffffff;
      --bg: #f8fafc;
    }
    .stApp {
      background:
        radial-gradient(circle at 8% 0%, rgba(49,130,246,.12), transparent 18rem),
        linear-gradient(180deg, #f9fbff 0%, #f6f8fb 42%, #ffffff 100%);
      color: var(--ink);
    }
    .block-container { padding-top: 1.4rem; max-width: 1320px; }
    div[data-testid="stMetric"] {
      background: rgba(255,255,255,.9);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 16px 18px;
      box-shadow: 0 14px 36px rgba(15,23,42,.06);
    }
    .hero-card {
      padding: 24px 26px;
      border-radius: 30px;
      color: white;
      background:
        radial-gradient(circle at 90% 10%, rgba(255,255,255,.18), transparent 16rem),
        linear-gradient(135deg, #191f28 0%, #27364a 55%, #147d72 100%);
      box-shadow: 0 22px 56px rgba(25,31,40,.18);
      margin-bottom: 14px;
    }
    .hero-card h1 { margin: 0; font-size: 2.25rem; letter-spacing: -.07em; }
    .hero-card p { margin: 8px 0 0; color: rgba(255,255,255,.72); font-weight: 700; }
    .status-card {
      padding: 16px 18px;
      border-radius: 22px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.92);
      box-shadow: 0 14px 36px rgba(15,23,42,.055);
      margin: 10px 0 16px;
    }
    .status-card b { font-size: 1rem; letter-spacing: -.035em; }
    .status-card span { display: block; margin-top: 4px; color: var(--muted); font-size: .86rem; font-weight: 700; }
    .section-title { margin: 26px 0 10px; font-size: 1.18rem; font-weight: 950; letter-spacing: -.045em; }
    </style>
    """,
    unsafe_allow_html=True,
)


def won(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "0원"
    return f"{int(round(float(value))):,}원"


def pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "0.0%"
    return f"{float(value):.1f}%"


def sync_catalog() -> None:
    database = Database(DB_PATH)
    database.initialize()
    database.delete_stores_by_adapter("masterkey")
    database.delete_stores_by_adapter("sherlock")
    database.delete_stores_by_ids(RETIRED_STORE_IDS)
    stores = [
        store
        for store in load_stores(CONFIG_PATH)
        if store.store_id not in RETIRED_STORE_IDS
    ]
    database.sync_stores(stores)
    database.recalculate_slot_estimates()


@st.cache_data(ttl=120, show_spinner=False)
def read_frames(db_mtime: float | None, manual_mtime: float | None):
    del db_mtime, manual_mtime
    sync_catalog()
    slots = load_slots(DB_PATH)
    catalog = load_catalog(DB_PATH)
    status = load_store_status(DB_PATH)
    manual_stores, manual_themes, _ = load_manual_estimates(MANUAL_PATH)
    return slots, catalog, status, manual_stores, manual_themes


def current_month_bounds() -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(TODAY).replace(day=1)
    next_month = start + pd.offsets.MonthBegin(1)
    return start, next_month - pd.Timedelta(days=1)


def combine_revenue(auto_projection: pd.DataFrame, manual_stores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if not auto_projection.empty:
        for row in auto_projection.to_dict("records"):
            monthly = float(row.get("monthly_revenue", 0) or 0)
            rows.append(
                {
                    "store_id": row.get("store_id", ""),
                    "store_name": row.get("store_name", ""),
                    "region": row.get("region", ""),
                    "booking_rate": float(row.get("booking_rate", 0) or 0),
                    "monthly_revenue": monthly,
                    "source": "자동 수집",
                    "confidence": row.get("confidence", ""),
                    "observed_days": int(row.get("observed_days", 0) or 0),
                }
            )
    if not manual_stores.empty:
        for row in manual_stores.to_dict("records"):
            monthly = (
                float(row.get("monthly_revenue_min", 0) or 0)
                + float(row.get("monthly_revenue_max", 0) or 0)
            ) / 2
            booking = (
                float(row.get("booking_rate_min", 0) or 0)
                + float(row.get("booking_rate_max", 0) or 0)
            ) / 2
            rows.append(
                {
                    "store_id": row.get("store_id", ""),
                    "store_name": row.get("store_name", ""),
                    "region": row.get("region", ""),
                    "booking_rate": booking,
                    "monthly_revenue": monthly,
                    "source": "수동 관측",
                    "confidence": "수동 범위",
                    "observed_days": 0,
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "store_id",
                "store_name",
                "region",
                "booking_rate",
                "monthly_revenue",
                "source",
                "confidence",
                "observed_days",
            ]
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values("monthly_revenue", ascending=False)


def render_job_status() -> None:
    status = read_job_status(APP_HOME)
    if not status:
        return
    running = job_is_running(status)
    state = str(status.get("status", ""))
    if state in {"starting", "running"} and running:
        progress = status.get("progress") or {}
        completed = int(progress.get("completed", 0) or 0)
        total = int(progress.get("total", 0) or 0)
        percent = int(completed / total * 100) if total else 0
        current_store = str(progress.get("current_store", "") or "공개 예약표 연결 중")
        current_date = str(progress.get("current_date", "") or "")
        st.markdown(
            f"""
            <div class="status-card">
              <b>{escape(str(status.get("label", "예약 업데이트")))} 진행 중 · {percent}%</b>
              <span>{escape(current_store)} {escape(current_date)} · 화면을 닫아도 서버에서 계속 수집합니다.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.progress(
            min(percent, 100),
            text=f"{completed:,}/{total:,}건 · 성공 {int(progress.get('success', 0) or 0):,} · 실패 {int(progress.get('failed', 0) or 0):,}",
        )
        if st.button("수집 상태 새로고침", use_container_width=True):
            st.rerun()
    elif state in {"success", "partial_success"}:
        summary = status.get("summary") or {}
        st.success(
            f"{status.get('label', '예약 업데이트')} 완료 · "
            f"성공 {int(summary.get('success', 0) or 0):,}건 · "
            f"실패 {int(summary.get('failed', 0) or 0):,}건 · "
            f"슬롯 {int(summary.get('slots', 0) or 0):,}개"
        )
        if st.button("완료된 데이터 다시 불러오기", use_container_width=True):
            read_frames.clear()
            clear_job_status(APP_HOME)
            st.rerun()
    else:
        st.warning("이전 수집 상태가 남아 있습니다. 정리 후 다시 실행해 주세요.")
        if st.button("수집 상태 정리", use_container_width=True):
            clear_job_status(APP_HOME)
            st.rerun()

    logs = tail_job_log(status, max_lines=80)
    if logs:
        with st.expander("수집 로그 보기", expanded=False):
            st.code(logs, language="text")
    if status.get("error"):
        with st.expander("오류 상세", expanded=False):
            st.code(str(status["error"]), language="text")


def start_refresh(label: str, days: int, store_ids: set[str] | None) -> None:
    try:
        start_crawl_job(
            app_home=APP_HOME,
            project_dir=PROJECT_DIR,
            label=label,
            days=days,
            config_path=CONFIG_PATH,
            db_path=DB_PATH,
            store_ids=store_ids,
            delay_min_seconds=CRAWL_DELAY_MIN_SECONDS,
            delay_max_seconds=CRAWL_DELAY_MAX_SECONDS,
            max_parallel_origins=CRAWL_MAX_PARALLEL_ORIGINS,
            max_navigation_timeout_ms=CRAWL_NAVIGATION_TIMEOUT_MS,
        )
        st.success(f"{label}를 서버에서 시작했습니다.")
        st.rerun()
    except CrawlJobAlreadyRunning:
        st.warning("이미 수집이 실행 중입니다. 진행률을 확인해 주세요.")
    except Exception as exc:
        st.error(f"{label} 시작 실패")
        st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


db_mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else None
manual_mtime = MANUAL_PATH.stat().st_mtime if MANUAL_PATH.exists() else None

with st.spinner("LumiTrack 데이터를 불러오는 중입니다..."):
    slots, catalog, store_status, manual_stores, manual_themes = read_frames(
        db_mtime,
        manual_mtime,
    )

month_start, month_end = current_month_bounds()

st.markdown(
    """
    <div class="hero-card">
      <h1>LumiTrack</h1>
      <p>방탈출 예약률과 월 예상매출을 서버에서 안정적으로 모니터링합니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

regions = sorted(store_status["region"].dropna().unique()) if not store_status.empty else []
selected_regions = st.multiselect("지역 필터", regions, placeholder="전체 지역")
scoped_status = (
    store_status[store_status["region"].isin(selected_regions)]
    if selected_regions and not store_status.empty
    else store_status
)
store_options = sorted(scoped_status["store_name"].dropna().unique()) if not scoped_status.empty else []
selected_stores = st.multiselect("매장 필터", store_options, placeholder="전체 매장")
period = st.radio("조회 기간", ["오늘", "오늘부터 7일", "이번 달"], horizontal=True)

if period == "오늘":
    start_date = end_date = TODAY
elif period == "오늘부터 7일":
    start_date, end_date = TODAY, TODAY + timedelta(days=6)
else:
    start_date, end_date = month_start.date(), month_end.date()

filtered = filter_slots(
    slots,
    regions=selected_regions,
    stores=selected_stores,
    start_date=start_date,
    end_date=end_date,
)
active_store_ids = set(
    scoped_status.loc[
        ~scoped_status["adapter_type"].isin(NON_CRAWLING_ADAPTERS),
        "store_id",
    ].astype(str)
) if not scoped_status.empty else set()
if selected_stores and not scoped_status.empty:
    active_store_ids = set(
        scoped_status.loc[
            scoped_status["store_name"].isin(selected_stores)
            & ~scoped_status["adapter_type"].isin(NON_CRAWLING_ADAPTERS),
            "store_id",
        ].astype(str)
    )

button_cols = st.columns([1, 1, 2])
with button_cols[0]:
    if st.button("오늘 예약 업데이트", type="primary", use_container_width=True):
        start_refresh(
            "오늘 예약 업데이트",
            1,
            active_store_ids if selected_regions or selected_stores else None,
        )
with button_cols[1]:
    if st.button("7일 예약 업데이트", use_container_width=True):
        start_refresh(
            "7일 예약 업데이트",
            7,
            active_store_ids if selected_regions or selected_stores else None,
        )
with button_cols[2]:
    st.caption(
        f"서버 수집 설정: 병렬 {CRAWL_MAX_PARALLEL_ORIGINS}개 · "
        f"타임아웃 {CRAWL_NAVIGATION_TIMEOUT_MS // 1000}초 · "
        f"요청 간격 {CRAWL_DELAY_MIN_SECONDS}~{CRAWL_DELAY_MAX_SECONDS}초"
    )

render_job_status()

current_month_slots = filter_slots(
    slots,
    regions=selected_regions,
    stores=selected_stores,
    start_date=month_start.date(),
    end_date=month_end.date(),
)
auto_projection = store_monthly_projections(
    current_month_slots,
    TODAY.year,
    TODAY.month,
)
manual_scope = manual_stores.copy()
if selected_regions and not manual_scope.empty:
    manual_scope = manual_scope[manual_scope["region"].isin(selected_regions)]
if selected_stores and not manual_scope.empty:
    manual_scope = manual_scope[manual_scope["store_name"].isin(selected_stores)]
combined = combine_revenue(auto_projection, manual_scope)

measurable = filtered[filtered["status"].isin(MEASURABLE_STATUSES)] if not filtered.empty else filtered
reserved_count = int(measurable["status"].eq("reserved").sum()) if not measurable.empty else 0
total_count = int(len(measurable)) if measurable is not None else 0
booking_rate_value = reserved_count / total_count * 100 if total_count else 0
industry_monthly = float(combined["monthly_revenue"].sum()) if not combined.empty else 0
average_store_monthly = float(combined["monthly_revenue"].mean()) if not combined.empty else 0
auto_count = int(combined["source"].eq("자동 수집").sum()) if not combined.empty else 0
manual_count = int(combined["source"].eq("수동 관측").sum()) if not combined.empty else 0

metric_cols = st.columns(5)
metric_cols[0].metric("예약률", pct(booking_rate_value))
metric_cols[1].metric("업계 추정 월매출", won(industry_monthly))
metric_cols[2].metric("매장당 월 평균", won(average_store_monthly))
metric_cols[3].metric("분석 매장", f"{len(combined):,}곳")
metric_cols[4].metric("출처", f"자동 {auto_count:,} · 수동 {manual_count:,}")

view = st.segmented_control(
    "화면",
    ["요약", "매장 매출", "테마 분석", "장르 분석", "지도", "수집 상태", "원본 슬롯"],
    default="요약",
)

if view == "요약":
    st.markdown('<div class="section-title">월 예상매출 TOP 매장</div>', unsafe_allow_html=True)
    show = combined.head(20).copy()
    if not show.empty:
        show["예약률"] = show["booking_rate"].map(pct)
        show["월 예상매출"] = show["monthly_revenue"].map(won)
        st.dataframe(
            show[["store_name", "region", "예약률", "월 예상매출", "source", "confidence"]],
            use_container_width=True,
            hide_index=True,
        )
        chart = (
            alt.Chart(show.head(12))
            .mark_bar(cornerRadiusTopLeft=8, cornerRadiusTopRight=8)
            .encode(
                x=alt.X("monthly_revenue:Q", title="월 예상매출"),
                y=alt.Y("store_name:N", sort="-x", title="매장"),
                color=alt.Color("source:N", title="출처"),
                tooltip=["store_name", "region", "source", "monthly_revenue"],
            )
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("수집 또는 수동 매출 데이터가 아직 없습니다.")

    st.markdown('<div class="section-title">요일별 예약률</div>', unsafe_allow_html=True)
    weekdays = weekday_rates(filtered)
    if not weekdays.empty:
        st.altair_chart(
            alt.Chart(weekdays)
            .mark_bar(cornerRadiusTopLeft=8, cornerRadiusTopRight=8)
            .encode(
                x=alt.X("weekday:N", title="요일"),
                y=alt.Y("booking_rate:Q", title="예약률", scale=alt.Scale(domain=[0, 100])),
                tooltip=["weekday", "reserved_slots", "total_slots", "booking_rate"],
            ),
            use_container_width=True,
        )
    else:
        st.caption("조회 기간에 예약 슬롯 데이터가 없습니다.")

elif view == "매장 매출":
    table = combined.copy()
    if not table.empty:
        table["예약률"] = table["booking_rate"].map(pct)
        table["월 예상매출"] = table["monthly_revenue"].map(won)
        st.dataframe(
            table[
                [
                    "store_name",
                    "region",
                    "예약률",
                    "월 예상매출",
                    "source",
                    "confidence",
                    "observed_days",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("매장 매출 데이터가 아직 없습니다.")

elif view == "테마 분석":
    themes = theme_rates(filtered)
    if not themes.empty:
        themes["예약률"] = themes["booking_rate"].map(pct)
        st.dataframe(
            themes.head(100),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("테마별 예약률 데이터가 아직 없습니다.")

elif view == "장르 분석":
    genre = genre_monthly_summary(current_month_slots, TODAY.year, TODAY.month)
    if not genre.empty:
        genre["평균 월매출"] = genre["average_monthly_revenue"].map(won)
        genre["장르 총 월매출"] = genre["total_monthly_revenue"].map(won)
        genre["예약률"] = genre["booking_rate"].map(pct)
        st.dataframe(
            genre[
                [
                    "genre",
                    "store_count",
                    "theme_count",
                    "예약률",
                    "평균 월매출",
                    "장르 총 월매출",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("장르별 데이터가 아직 없습니다.")

elif view == "지도":
    map_frame = store_status.copy()
    if not combined.empty and not map_frame.empty:
        map_frame = map_frame.merge(
            combined[["store_id", "monthly_revenue", "booking_rate", "source"]],
            on="store_id",
            how="left",
        )
    map_frame = map_frame.dropna(subset=["latitude", "longitude"])
    if selected_regions and not map_frame.empty:
        map_frame = map_frame[map_frame["region"].isin(selected_regions)]
    if selected_stores and not map_frame.empty:
        map_frame = map_frame[map_frame["store_name"].isin(selected_stores)]
    if not map_frame.empty:
        st.map(
            map_frame.rename(columns={"latitude": "lat", "longitude": "lon"}),
            latitude="lat",
            longitude="lon",
            size=40,
        )
        display = map_frame.copy()
        display["월 예상매출"] = display["monthly_revenue"].map(won)
        display["예약률"] = display["booking_rate"].map(pct)
        st.dataframe(
            display[["store_name", "region", "address", "예약률", "월 예상매출", "source"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("지도에 표시할 좌표 데이터가 없습니다.")

elif view == "수집 상태":
    status_table = store_status.copy()
    if not status_table.empty:
        status_table["latest_crawl_at"] = status_table["latest_crawl_at"].dt.tz_convert(KST).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(
            status_table[
                [
                    "store_name",
                    "region",
                    "adapter_type",
                    "latest_crawl_status",
                    "latest_crawl_at",
                    "latest_error",
                    "collection_note",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("매장 상태 데이터가 없습니다.")

else:
    raw = filtered.copy()
    if not raw.empty:
        raw["expected_revenue"] = raw["expected_revenue"].map(won)
        st.dataframe(raw, use_container_width=True, hide_index=True)
    else:
        st.info("조회 기간에 원본 슬롯 데이터가 없습니다.")

