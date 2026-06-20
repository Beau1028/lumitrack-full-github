from __future__ import annotations

import os
import io
import json
import logging
import sqlite3
import traceback
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import altair as alt
import streamlit as st
import streamlit.components.v1 as components

DEMO_MODE = os.getenv("LUMITRACK_DEMO_MODE", "").casefold() in {
    "1",
    "true",
    "yes",
    "demo",
}

from scraper.analytics import (
    MEASURABLE_STATUSES,
    booking_rate,
    combine_store_revenue_estimates,
    daily_operations,
    estimated_ticket_value,
    filter_slots,
    genre_monthly_summary,
    hourly_rates,
    load_catalog,
    load_manual_estimates,
    load_slots,
    load_store_status,
    market_radius_summary,
    operations_by,
    price_coverage,
    price_strategy_matrix,
    project_monthly_revenue,
    region_rates,
    store_efficiency,
    store_growth_trends,
    store_operations,
    store_monthly_projections,
    theme_operations,
    weekday_rates,
    weekday_weekend_rates,
)
from scraper.config import load_stores
from scraper.crawl_jobs import (
    CrawlJobAlreadyRunning,
    job_is_running,
    read_job_status,
    start_crawl_job,
    tail_job_log,
)
from scraper.database import Database
from scraper.logging_utils import configure_logging

if DEMO_MODE:
    NON_CRAWLING_ADAPTERS = {
        "catalog",
        "blocked",
        "limited",
        "permission_required",
    }
    RETIRED_STORE_IDS = {
        "goldenkey_policy",
        "murderparker_policy",
        "tickettoescape_hongdae",
    }
    run_crawl = None
else:
    from scraper.runner import NON_CRAWLING_ADAPTERS, RETIRED_STORE_IDS, run_crawl

PROJECT_DIR = Path(__file__).resolve().parent
APP_HOME = Path(os.getenv("ESCAPE_ROOM_MONITOR_HOME", PROJECT_DIR))
DEMO_DATA_DIR = PROJECT_DIR / "demo_data"
CONFIG_PATH = APP_HOME / "stores.yaml"
MANUAL_ESTIMATES_PATH = APP_HOME / "manual_estimates.yaml"
DB_PATH = (
    Path(
        os.getenv(
            "LUMITRACK_DEMO_DB",
            str(DEMO_DATA_DIR / "lumitrack_demo.sqlite"),
        )
    )
    if DEMO_MODE
    else APP_HOME / "data" / "escape_room.db"
)
KST = ZoneInfo("Asia/Seoul")
TODAY = datetime.now(KST).date()
PRODUCT_NAME = "LumiTrack"
PRODUCT_NAME_KO = "루미트랙"
PRODUCT_TAGLINE = "Escape Revenue OS"


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def is_streamlit_cloud_runtime() -> bool:
    """Best-effort detection for Streamlit Community Cloud."""
    return (
        Path("/mount/src").exists()
        or bool(os.getenv("STREAMLIT_SHARING_MODE"))
        or bool(os.getenv("STREAMLIT_CLOUD"))
    )


STREAMLIT_CLOUD_RUNTIME = is_streamlit_cloud_runtime()
CLOUD_SAFE_CRAWL = env_flag(
    "LUMITRACK_CLOUD_SAFE",
    default=STREAMLIT_CLOUD_RUNTIME,
)
CRAWL_MAX_PARALLEL_ORIGINS = env_int(
    "LUMITRACK_MAX_PARALLEL_ORIGINS",
    2 if CLOUD_SAFE_CRAWL else 8,
    1,
    8,
)
CRAWL_NAVIGATION_TIMEOUT_MS = env_int(
    "LUMITRACK_NAVIGATION_TIMEOUT_MS",
    12_000 if CLOUD_SAFE_CRAWL else 18_000,
    8_000,
    30_000,
)
CRAWL_DELAY_MIN_SECONDS = env_int(
    "LUMITRACK_DELAY_MIN_SECONDS",
    5,
    5,
    30,
)
CRAWL_DELAY_MAX_SECONDS = env_int(
    "LUMITRACK_DELAY_MAX_SECONDS",
    6 if CLOUD_SAFE_CRAWL else 8,
    CRAWL_DELAY_MIN_SECONDS,
    45,
)
ALLOW_CLOUD_FULL_WEEK = env_flag("LUMITRACK_ALLOW_CLOUD_FULL_WEEK", False)

configure_logging(APP_HOME / "logs")
LOGGER = logging.getLogger("lumitrack.app")
LOGGER.info(
    "LumiTrack booted demo=%s cloud_runtime=%s cloud_safe=%s parallel=%s timeout_ms=%s",
    DEMO_MODE,
    STREAMLIT_CLOUD_RUNTIME,
    CLOUD_SAFE_CRAWL,
    CRAWL_MAX_PARALLEL_ORIGINS,
    CRAWL_NAVIGATION_TIMEOUT_MS,
)

STATUS_LABELS = {
    "play33": "자동 수집",
    "xdungeon": "자동 수집",
    "zero_world": "자동 수집",
    "page_today": "자동 수집",
    "generic": "자동 수집",
    "cubeescape": "자동 수집",
    "earthstar": "자동 수집",
    "frank": "자동 수집",
    "horror_switch": "자동 수집",
    "sinbi": "자동 수집",
    "deepthinker": "자동 수집",
    "oasis": "자동 수집",
    "shortstories": "공개 예약창 수집",
    "keyescape": "공개 예약창 수집",
    "naver_booking": "공개 예약창 수집",
    "codek": "공개 예약표 수집",
    "murderparker": "공개 예약표 수집",
    "amazed": "공개 예약표 수집",
    "permission_required": "공식 사용 허가 필요",
    "limited": "부분 공개",
    "blocked": "접근 제한",
    "catalog": "정보만 등록",
}

REGION_COORDINATES = {
    "서울 강남": (37.4979, 127.0276),
    "서울 홍대": (37.5563, 126.9236),
    "서울 건대": (37.5404, 127.0692),
    "서울 성수": (37.5446, 127.0557),
    "서울 신촌": (37.5559, 126.9368),
    "서울 잠실": (37.5133, 127.1000),
    "서울 대학로": (37.5822, 127.0019),
    "서울": (37.5665, 126.9780),
    "경기 수원": (37.2636, 127.0286),
    "경기 성남": (37.3826, 127.1189),
    "경기 김포": (37.6152, 126.7156),
    "인천 부평": (37.5070, 126.7218),
    "인천 구월": (37.4486, 126.7010),
    "대전": (36.3504, 127.3845),
    "대구": (35.8714, 128.6014),
    "부산 서면": (35.1577, 129.0592),
    "부산": (35.1796, 129.0756),
    "광주": (35.1595, 126.8526),
    "전북 전주": (35.8242, 127.1480),
    "전국": (36.3504, 127.3845),
}
DEFAULT_MAP_COORDINATE = (37.5665, 126.9780)

st.set_page_config(
    page_title=f"{PRODUCT_NAME} | 방탈출 매출 인사이트",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """
    <style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css");
    :root {
        --ink: #191f28;
        --muted: #8b95a1;
        --paper: #f2f4f6;
        --card: rgba(255, 255, 255, .92);
        --line: rgba(209, 216, 224, .80);
        --blue: #3182f6;
        --blue-deep: #1b64da;
        --teal: #00a889;
        --mint: #9fe8dd;
        --coral: #ff6b6b;
        --violet: #6b4eff;
        --shadow: 0 18px 42px rgba(25, 31, 40, .08);
        --font: "Pretendard", "Inter", "Noto Sans KR", -apple-system,
            BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .stApp {
        color: var(--ink);
        font-family: var(--font);
        background:
            radial-gradient(circle at 12% -8%, rgba(49,130,246,.16), transparent 28rem),
            radial-gradient(circle at 90% 2%, rgba(0,168,137,.10), transparent 24rem),
            linear-gradient(180deg, #ffffff 0%, #f7f9fb 38%, #f2f4f6 100%),
            var(--paper);
    }
    .block-container {
        max-width: 1580px;
        padding-top: 1.45rem;
        padding-bottom: 4rem;
    }
    html, body, button, input, textarea, select {
        font-family: var(--font) !important;
    }
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    #MainMenu,
    footer {
        display: none !important;
        visibility: hidden !important;
    }
    h1, h2, h3 {
        letter-spacing: -0.045em;
        color: var(--ink);
    }
    h2 {font-size: 1.55rem !important;}
    h3 {font-size: 1.12rem !important;}
    [data-testid="stSidebar"] {
        display: none !important;
        visibility: hidden !important;
    }
    [data-testid="collapsedControl"] {
        display: none !important;
    }
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {
        color: var(--ink);
        letter-spacing: -.05em;
    }
    [data-testid="stSidebar"] label {
        color: #4e5968 !important;
        font-weight: 800 !important;
    }
    .sidebar-brand {
        margin: .4rem 0 1.15rem;
        padding: 18px 18px 16px;
        border-radius: 24px;
        background:
            radial-gradient(circle at 88% 0%, rgba(255,255,255,.28), transparent 7rem),
            linear-gradient(135deg, var(--blue), var(--blue-deep));
        color: #fff;
        box-shadow: 0 18px 38px rgba(49,130,246,.22);
    }
    .sidebar-brand small {
        display: block;
        color: rgba(255,255,255,.76);
        font-weight: 900;
        letter-spacing: .12em;
        text-transform: uppercase;
        font-size: .68rem;
    }
    .sidebar-brand b {
        display: block;
        margin-top: 6px;
        font-size: 1.36rem;
        letter-spacing: -.055em;
        line-height: 1.05;
    }
    .sidebar-brand span {
        display: block;
        margin-top: 8px;
        color: rgba(255,255,255,.82);
        font-size: .84rem;
        line-height: 1.45;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
        color: var(--muted);
    }
    [data-baseweb="select"] > div,
    [data-testid="stDateInput"] > div > div {
        min-height: 44px;
        border-radius: 14px !important;
        border-color: rgba(209,216,224,.95) !important;
        background: #fff !important;
        box-shadow: 0 4px 14px rgba(25,31,40,.035);
    }
    [data-testid="stMetric"] {
        position: relative;
        overflow: hidden;
        background:
            linear-gradient(145deg, rgba(255,255,255,.98), rgba(255,255,255,.84));
        border: 1px solid rgba(255,255,255,.95);
        border-radius: 22px;
        padding: 18px 19px;
        min-height: 112px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(18px);
        transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        border-color: rgba(49,130,246,.24);
        box-shadow: 0 24px 56px rgba(25,31,40,.11);
    }
    [data-testid="stMetric"]::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, #80b8ff, var(--blue));
    }
    [data-testid="stMetric"]::after {
        content: "";
        position: absolute;
        width: 92px;
        height: 92px;
        right: -34px;
        top: -36px;
        border-radius: 999px;
        background: radial-gradient(circle, rgba(49,130,246,.18), transparent 68%);
    }
    [data-testid="stMetricLabel"] {
        font-weight: 850;
        color: var(--muted);
        letter-spacing: -.02em;
    }
    [data-testid="stMetricValue"] {
        letter-spacing: -0.05em;
        color: var(--ink);
        font-weight: 900;
    }
    [data-testid="stTabs"] {
        background: rgba(255,255,255,.96);
        border: 1px solid rgba(255,255,255,.95);
        border-radius: 26px;
        padding: 10px;
        box-shadow: 0 18px 46px rgba(25,31,40,.075);
        backdrop-filter: blur(20px);
    }
    [data-testid="stTabs"] div[role="tablist"] {
        gap: .45rem;
        flex-wrap: wrap;
    }
    [data-testid="stTabs"] button {
        min-height: 46px;
        padding: .58rem 1.05rem;
        border-radius: 999px;
        color: #4e5968;
        background: #f2f4f6;
        font-weight: 850;
        letter-spacing: -.03em;
        transition: transform .15s ease, background .15s ease, color .15s ease, box-shadow .15s ease;
    }
    [data-testid="stTabs"] button:hover {
        color: var(--blue);
        transform: translateY(-1px);
        background: #eaf3ff;
    }
    [data-testid="stTabs"] button[aria-selected="true"] {
        color: #fff;
        background: linear-gradient(135deg, var(--blue), var(--blue-deep));
        box-shadow: 0 10px 24px rgba(49,130,246,.28);
    }
    [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
        display: none;
    }
    [data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 20px;
        overflow: hidden;
        box-shadow: 0 16px 38px rgba(15,23,42,.07);
    }
    .stButton > button {
        min-height: 48px;
        border-radius: 16px;
        border: 1px solid rgba(209,216,224,.80);
        background: #fff;
        color: var(--ink);
        font-weight: 850;
        letter-spacing: -.03em;
        transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
    }
    .stButton > button:hover {
        border-color: rgba(49,130,246,.38);
        color: var(--blue);
        transform: translateY(-1px);
        box-shadow: 0 12px 26px rgba(49,130,246,.14);
        background: #f8fbff;
    }
    .stButton > button[kind="primary"] {
        color: #fff;
        border: 0;
        background: linear-gradient(135deg, var(--blue), var(--blue-deep));
        box-shadow: 0 14px 28px rgba(49,130,246,.24);
    }
    @keyframes appFadeUp {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
    }
    @keyframes logoPulse {
        0%, 100% { transform: scale(1); box-shadow: 0 16px 34px rgba(49,130,246,.22); }
        50% { transform: scale(1.035); box-shadow: 0 20px 44px rgba(49,130,246,.30); }
    }
    @keyframes chartFloatIn {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .hero {
        position: relative;
        overflow: hidden;
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(300px, .65fr);
        gap: 18px;
        align-items: stretch;
        padding: 22px 26px;
        border-radius: 30px;
        color: var(--ink);
        background:
            radial-gradient(circle at 10% -12%, rgba(49,130,246,.22), transparent 22rem),
            radial-gradient(circle at 88% 0%, rgba(0,168,137,.12), transparent 20rem),
            linear-gradient(135deg, rgba(255,255,255,.98), rgba(247,250,255,.90));
        box-shadow: 0 28px 80px rgba(25,31,40,.095);
        margin: .25rem 0 .72rem;
        border: 1px solid rgba(255,255,255,.98);
        animation: appFadeUp .38s cubic-bezier(.16,1,.3,1) both;
    }
    .hero::after {
        content: "";
        position: absolute;
        width: 380px;
        height: 380px;
        right: -105px;
        bottom: -165px;
        border: 54px solid rgba(49,130,246,.055);
        border-radius: 50%;
    }
    .hero-content,
    .hero-panel {
        position: relative;
        z-index: 1;
    }
    .hero-kicker {
        display: flex;
        gap: .55rem;
        align-items: center;
        color: var(--blue);
        font-size: .78rem;
        font-weight: 850;
        letter-spacing: .12em;
        text-transform: uppercase;
    }
    .hero-kicker::before {
        content: "";
        width: .62rem;
        height: .62rem;
        border-radius: 999px;
        background: var(--blue);
        box-shadow: 0 0 0 7px rgba(49,130,246,.11);
    }
    .brand-lockup {
        display: flex;
        align-items: center;
        gap: 13px;
        margin-bottom: 6px;
    }
    .brand-mark {
        position: relative;
        flex: 0 0 auto;
        width: 50px;
        height: 50px;
        border-radius: 19px;
        background:
            radial-gradient(circle at 72% 20%, rgba(255,255,255,.55), transparent 1.45rem),
            linear-gradient(135deg, var(--blue), var(--teal));
        animation: logoPulse 3.2s ease-in-out infinite;
    }
    .brand-mark::before {
        content: "";
        position: absolute;
        inset: 12px;
        border: 6px solid #fff;
        border-radius: 999px;
        opacity: .96;
    }
    .brand-mark::after {
        content: "";
        position: absolute;
        width: 10px;
        height: 24px;
        right: 12px;
        bottom: 7px;
        border-radius: 999px;
        background: #fff;
        transform: rotate(-38deg);
        transform-origin: top center;
        opacity: .92;
    }
    .brand-name {
        color: var(--ink);
        font-size: 1.34rem;
        font-weight: 950;
        letter-spacing: -.055em;
        line-height: 1;
    }
    .brand-subtitle {
        margin-top: 4px;
        color: var(--blue);
        font-size: .78rem;
        font-weight: 850;
        letter-spacing: .09em;
        text-transform: uppercase;
    }
    .hero-title {
        margin-top: 8px;
        font-size: clamp(1.9rem, 3.1vw, 3.15rem);
        font-weight: 900;
        letter-spacing: -0.075em;
        line-height: 1.03;
    }
    .hero-copy {
        margin-top: 8px;
        color: #4e5968;
        line-height: 1.5;
        max-width: 620px;
        font-size: .98rem;
    }
    .hero-badge-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 12px;
    }
    .hero-badge {
        display: inline-block;
        padding: 8px 12px;
        border-radius: 999px;
        border: 1px solid rgba(49,130,246,.10);
        background: #f2f7ff;
        backdrop-filter: blur(14px);
        color: #2563c9;
        font-size: .82rem;
        font-weight: 750;
    }
    .hero-panel {
        min-height: 142px;
        padding: 18px;
        border-radius: 24px;
        color: white;
        background:
            radial-gradient(circle at 92% 0%, rgba(255,255,255,.22), transparent 10rem),
            linear-gradient(135deg, var(--blue), var(--blue-deep));
        border: 1px solid rgba(255,255,255,.18);
        box-shadow: 0 22px 48px rgba(49,130,246,.26);
    }
    .hero-panel-label {
        color: rgba(255,255,255,.78);
        font-size: .78rem;
        font-weight: 850;
        letter-spacing: .12em;
        text-transform: uppercase;
    }
    .hero-panel-value {
        margin-top: 8px;
        font-size: 1.34rem;
        font-weight: 900;
        letter-spacing: -.06em;
        line-height: 1.1;
    }
    .hero-panel-note {
        margin-top: 12px;
        color: rgba(255,255,255,.80);
        font-size: .86rem;
        line-height: 1.45;
        font-weight: 700;
    }
    .compact-hero {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
        padding: 13px 16px;
        margin: .9rem 0 .62rem;
        border-radius: 24px;
        background:
            radial-gradient(circle at 96% -18%, rgba(49,130,246,.18), transparent 10rem),
            linear-gradient(145deg, rgba(255,255,255,.96), rgba(248,251,255,.88));
        border: 1px solid rgba(229,234,240,.92);
        box-shadow: 0 10px 28px rgba(25,31,40,.055);
        animation: appFadeUp .2s cubic-bezier(.16,1,.3,1) both;
    }
    .compact-brand {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
    }
    .compact-brand .brand-mark {
        width: 34px;
        height: 34px;
        border-radius: 13px;
        animation: none;
    }
    .compact-brand .brand-mark::before {
        inset: 8px;
        border-width: 4px;
    }
    .compact-brand .brand-mark::after {
        width: 7px;
        height: 15px;
        right: 8px;
        bottom: 5px;
    }
    .compact-title {
        color: var(--ink);
        font-size: 1rem;
        font-weight: 950;
        letter-spacing: -.055em;
        line-height: 1.15;
    }
    .compact-desc {
        margin-top: 2px;
        color: var(--muted);
        font-size: .78rem;
        font-weight: 750;
        letter-spacing: -.025em;
    }
    .compact-pill {
        flex: 0 0 auto;
        padding: 8px 11px;
        border-radius: 999px;
        color: #2563c9;
        background: #eef6ff;
        font-size: .76rem;
        font-weight: 900;
        letter-spacing: -.025em;
    }
    .hero-mini-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin-top: 20px;
    }
    .hero-mini {
        padding: 12px;
        border-radius: 18px;
        background: rgba(255,255,255,.14);
        border: 1px solid rgba(255,255,255,.16);
    }
    .hero-mini b {
        display: block;
        font-size: 1.05rem;
        letter-spacing: -.04em;
    }
    .hero-mini span {
        color: rgba(255,255,255,.78);
        font-size: .78rem;
    }
    .menu-board-title {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin: .58rem 0 .42rem;
    }
    .menu-board-title h3 {
        margin: 0;
        font-size: 1.08rem !important;
        letter-spacing: -.055em;
    }
    .menu-board-title span {
        color: var(--muted);
        font-size: .84rem;
        font-weight: 700;
    }
    .app-menu-board,
    .quick-menu-board {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: .8rem;
    }
    .app-menu-card,
    .quick-menu-card {
        display: grid;
        grid-template-columns: auto 1fr;
        align-items: center;
        column-gap: 12px;
        row-gap: 3px;
        min-height: 84px;
        padding: 15px 16px;
        border-radius: 22px;
        background: rgba(255,255,255,.88);
        border: 1px solid rgba(229,234,240,.92);
        box-shadow: 0 10px 26px rgba(25,31,40,.055);
        color: var(--ink);
        text-decoration: none !important;
        transition: transform .1s ease, box-shadow .1s ease, border-color .1s ease, background .1s ease;
    }
    .app-menu-card:hover {
        transform: translateY(-1px);
        box-shadow: 0 14px 30px rgba(25,31,40,.075);
        border-color: rgba(49,130,246,.22);
        text-decoration: none !important;
    }
    .app-menu-icon {
        grid-row: span 2;
        width: 34px;
        height: 34px;
        display: grid;
        place-items: center;
        border-radius: 12px;
        background: transparent;
        color: var(--blue);
        font-size: 1.2rem;
    }
    .app-menu-card b,
    .quick-menu-card b {
        display: block;
        color: var(--ink);
        font-size: 1rem;
        letter-spacing: -.045em;
    }
    .app-menu-card small,
    .quick-menu-card span {
        display: block;
        margin-top: 4px;
        color: var(--muted);
        font-size: .82rem;
        line-height: 1.35;
    }
    .app-menu-card.active,
    .quick-menu-card.active {
        color: #fff;
        background: linear-gradient(135deg, #191f28, #263445);
        border-color: rgba(25,31,40,.08);
        box-shadow: 0 14px 30px rgba(25,31,40,.16);
    }
    .app-menu-card.active .app-menu-icon {
        color: #fff;
    }
    .app-menu-card.active b,
    .app-menu-card.active small,
    .quick-menu-card.active b,
    .quick-menu-card.active span {
        color: #fff;
    }
    .st-key-app_menu_grid {
        margin: .18rem 0 .65rem;
    }
    .st-key-app_menu_grid div[data-testid="stButton"] > button {
        min-height: 76px;
        width: 100%;
        justify-content: flex-start;
        gap: 10px;
        padding: 14px 16px;
        border-radius: 22px;
        text-align: left;
        box-shadow: 0 10px 26px rgba(25,31,40,.055);
        transition: transform .1s ease, box-shadow .1s ease, border-color .1s ease, background .1s ease;
    }
    .st-key-app_menu_grid div[data-testid="stButton"] > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 14px 30px rgba(25,31,40,.08);
    }
    .st-key-app_menu_grid div[data-testid="stButton"] > button:active {
        transform: translateY(0) scale(.992);
    }
    .st-key-app_menu_grid div[data-testid="stButton"] > button p {
        white-space: pre-line;
        line-height: 1.32;
        font-weight: 850;
        letter-spacing: -.04em;
    }
    .st-key-app_menu_grid [data-testid="stBaseButton-secondary"] {
        color: var(--ink);
        background:
            linear-gradient(145deg, rgba(255,255,255,.96), rgba(250,252,255,.88));
        border: 1px solid rgba(229,234,240,.95);
    }
    .st-key-app_menu_grid [data-testid="stBaseButton-secondary"] p::first-line {
        color: var(--ink);
        font-size: .98rem;
    }
    .st-key-app_menu_grid [data-testid="stBaseButton-secondary"] p {
        color: var(--muted);
        font-size: .78rem;
    }
    .st-key-app_menu_grid [data-testid="stBaseButton-primary"] {
        color: #fff;
        background:
            radial-gradient(circle at 92% 0%, rgba(255,255,255,.22), transparent 5.6rem),
            linear-gradient(135deg, #191f28, #263445);
        border: 1px solid rgba(25,31,40,.12);
        box-shadow: 0 14px 30px rgba(25,31,40,.16);
    }
    .st-key-app_menu_grid [data-testid="stBaseButton-primary"] p {
        color: #fff;
    }
    div[data-testid="stStatusWidget"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        width: 0 !important;
        height: 0 !important;
        pointer-events: none !important;
    }
    .home-guide {
        margin: 1rem 0 1.2rem;
        padding: 18px 20px;
        border-radius: 26px;
        background:
            linear-gradient(135deg, rgba(49,130,246,.10), rgba(0,168,137,.08)),
            #fff;
        border: 1px solid rgba(255,255,255,.95);
        box-shadow: var(--shadow);
    }
    .home-guide b {
        display: block;
        margin-bottom: 6px;
        color: var(--ink);
        font-size: 1.05rem;
        letter-spacing: -.04em;
    }
    .home-guide span {
        color: var(--muted);
        font-size: .92rem;
        line-height: 1.55;
    }
    .home-status-card {
        min-height: 82px;
        padding: 17px 19px;
        border-radius: 24px;
        background:
            linear-gradient(135deg, rgba(49,130,246,.08), rgba(0,168,137,.06)),
            rgba(255,255,255,.92);
        border: 1px solid rgba(255,255,255,.98);
        box-shadow: var(--shadow);
    }
    .home-status-card b {
        display: block;
        color: var(--ink);
        font-size: 1.02rem;
        letter-spacing: -.04em;
    }
    .home-status-card span {
        display: block;
        margin-top: 6px;
        color: var(--muted);
        font-size: .86rem;
        line-height: 1.45;
    }
    .home-kpi-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 14px;
        margin: 1rem 0 1.15rem;
    }
    .snapshot-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 14px;
        margin: 1rem 0 1.15rem;
    }
    .snapshot-card {
        position: relative;
        overflow: hidden;
        min-height: 138px;
        padding: 18px 18px 16px;
        border-radius: 24px;
        background:
            linear-gradient(145deg, rgba(255,255,255,1), rgba(250,252,255,.92));
        border: 1px solid rgba(255,255,255,1);
        box-shadow: var(--shadow);
        backdrop-filter: blur(18px);
    }
    .snapshot-card::before {
        content: "";
        position: absolute;
        inset: 0 auto 0 0;
        width: 5px;
        background: linear-gradient(180deg, #80b8ff, var(--blue));
    }
    .snapshot-card::after {
        content: "";
        position: absolute;
        width: 120px;
        height: 120px;
        right: -46px;
        top: -46px;
        border-radius: 999px;
        background: radial-gradient(circle, rgba(49,130,246,.16), transparent 68%);
    }
    .snapshot-card.violet::before {background: linear-gradient(180deg, #a5b4fc, var(--violet));}
    .snapshot-card.violet::after {background: radial-gradient(circle, rgba(107,78,255,.15), transparent 68%);}
    .snapshot-card.coral::before {background: linear-gradient(180deg, #fecdd3, var(--coral));}
    .snapshot-card.coral::after {background: radial-gradient(circle, rgba(251,113,133,.18), transparent 68%);}
    .snapshot-label {
        position: relative;
        z-index: 1;
        color: var(--muted);
        font-size: .78rem;
        font-weight: 900;
        letter-spacing: -.02em;
    }
    .snapshot-value {
        position: relative;
        z-index: 1;
        margin-top: 8px;
        color: var(--ink);
        font-size: clamp(1.32rem, 1.9vw, 2.05rem);
        font-weight: 950;
        letter-spacing: -.07em;
        line-height: 1.04;
    }
    .snapshot-detail {
        position: relative;
        z-index: 1;
        margin-top: 9px;
        color: #6b7684;
        font-size: .82rem;
        line-height: 1.45;
    }
    .summary-box {
        padding: 18px 20px;
        border: 1px solid var(--line);
        border-left: 5px solid var(--blue);
        border-radius: 20px;
        background: rgba(255,255,255,.92);
        backdrop-filter: blur(16px);
        line-height: 1.65;
        margin: 0.6rem 0 1.2rem;
        box-shadow: var(--shadow);
    }
    .detail-box {
        padding: 19px 21px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: var(--card);
        margin: .45rem 0 1rem;
        box-shadow: 0 9px 24px rgba(37,48,44,.045);
    }
    .section-note {color: var(--muted); font-size: 0.92rem;}
    .status-good {color: var(--teal); font-weight: 750;}
    .status-warn {color: #bd622d; font-weight: 750;}
    .map-shell {
        padding: 20px;
        border: 1px solid rgba(218,214,202,.95);
        border-radius: 22px;
        background:
            linear-gradient(180deg, rgba(255,255,255,.96), rgba(250,248,241,.88));
        box-shadow: 0 16px 42px rgba(37,48,44,.07);
    }
    .map-card {
        display: flex;
        gap: 13px;
        align-items: center;
        padding: 14px;
        border: 1px solid rgba(255,255,255,.75);
        border-radius: 20px;
        background: rgba(255,255,255,.80);
        box-shadow: 0 14px 34px rgba(15,23,42,.08);
        margin-bottom: 10px;
        backdrop-filter: blur(14px);
    }
    .map-card img {
        width: 48px;
        height: 48px;
        border-radius: 14px;
        object-fit: cover;
        border: 1px solid rgba(218,214,202,.95);
    }
    .map-card-title {
        font-weight: 850;
        letter-spacing: -0.035em;
        color: var(--ink);
        line-height: 1.25;
    }
    .map-card-meta {
        color: var(--muted);
        font-size: .84rem;
        line-height: 1.45;
    }
    .map-money {
        color: var(--teal);
        font-weight: 850;
    }
    [data-testid="stVegaLiteChart"] {
        animation: chartFloatIn .24s cubic-bezier(.16,1,.3,1) both;
        border-radius: 24px;
        background: rgba(255,255,255,.70);
        box-shadow: 0 12px 30px rgba(25,31,40,.055);
        transition: transform .12s ease, box-shadow .12s ease;
    }
    .filter-shell {
        margin: .85rem 0 1rem;
    }
    .filter-shell [data-testid="stExpander"] {
        border: 1px solid rgba(229,234,240,.92);
        border-radius: 24px;
        overflow: hidden;
        background:
            linear-gradient(145deg, rgba(255,255,255,.96), rgba(250,252,255,.88));
        box-shadow: 0 12px 30px rgba(25,31,40,.055);
    }
    .filter-shell [data-testid="stExpander"] summary {
        padding: 13px 16px;
        font-weight: 950;
        letter-spacing: -.045em;
    }
    .filter-summary {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin: .25rem 0 .2rem;
    }
    .filter-chip {
        display: inline-flex;
        align-items: center;
        min-height: 30px;
        padding: 6px 10px;
        border-radius: 999px;
        background: #f2f7ff;
        color: #2563c9;
        font-size: .78rem;
        font-weight: 900;
        letter-spacing: -.025em;
    }
    .refresh-loader {
        position: relative;
        overflow: hidden;
        display: flex;
        align-items: center;
        gap: 13px;
        margin: .72rem 0 1rem;
        padding: 15px 17px;
        border-radius: 22px;
        color: #fff;
        background:
            radial-gradient(circle at 96% 0%, rgba(255,255,255,.18), transparent 8rem),
            linear-gradient(135deg, #191f28, #263445);
        box-shadow: 0 18px 42px rgba(25,31,40,.18);
    }
    .refresh-loader::after {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(
            90deg,
            transparent,
            rgba(255,255,255,.12),
            transparent
        );
        animation: loaderSweep 1.2s ease-in-out infinite;
    }
    .refresh-spinner {
        position: relative;
        z-index: 1;
        width: 28px;
        height: 28px;
        border-radius: 999px;
        background: conic-gradient(from 90deg, #fff, #8bc0ff, #00c2a8, #fff);
        -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 5px));
        mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 5px));
        animation: escapeRoomOverlaySpin .72s linear infinite;
    }
    .refresh-copy {
        position: relative;
        z-index: 1;
        min-width: 0;
    }
    .refresh-copy b {
        display: block;
        font-size: .96rem;
        letter-spacing: -.04em;
    }
    .refresh-copy span {
        display: block;
        margin-top: 3px;
        color: rgba(255,255,255,.72);
        font-size: .78rem;
        font-weight: 750;
        letter-spacing: -.025em;
    }
    @keyframes loaderSweep {
        from { transform: translateX(-100%); }
        to { transform: translateX(100%); }
    }
    [data-testid="stVegaLiteChart"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 16px 34px rgba(25,31,40,.075);
    }
    .snapshot-card,
    .summary-box,
    .detail-box,
    div[data-testid="stDataFrame"] {
        animation: appFadeUp .22s cubic-bezier(.16,1,.3,1) both;
    }
    .legend-dot {
        display: inline-block;
        width: .72rem;
        height: .72rem;
        border-radius: 999px;
        margin-right: .35rem;
        vertical-align: -0.05rem;
    }
    @media (max-width: 1100px) {
        .hero {
            grid-template-columns: 1fr;
        }
        .compact-hero {
            align-items: flex-start;
            flex-direction: column;
        }
        .snapshot-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .home-kpi-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .app-menu-board,
        .quick-menu-board {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }
    @media (max-width: 720px) {
        .block-container {
            padding: 1.35rem .78rem 2.4rem;
        }
        .hero {
            gap: 12px;
            padding: 15px;
            border-radius: 22px;
            margin-top: .1rem;
        }
        .brand-lockup {
            gap: 9px;
            margin-bottom: 4px;
        }
        .brand-mark {
            width: 42px;
            height: 42px;
            border-radius: 16px;
        }
        .brand-mark::before {
            inset: 10px;
            border-width: 5px;
        }
        .brand-mark::after {
            width: 8px;
            height: 19px;
            right: 10px;
            bottom: 6px;
        }
        .brand-name {
            font-size: 1.14rem;
        }
        .brand-subtitle,
        .hero-kicker {
            font-size: .68rem;
        }
        .hero-title {
            margin-top: 5px;
            font-size: 1.62rem;
            line-height: 1.08;
            letter-spacing: -.07em;
        }
        .hero-copy {
            margin-top: 6px;
            font-size: .87rem;
            line-height: 1.42;
        }
        .hero-badge-row {
            margin-top: 8px;
            gap: 6px;
        }
        .hero-badge {
            padding: 6px 9px;
            font-size: .72rem;
        }
        .hero-panel {
            min-height: auto;
            padding: 14px;
            border-radius: 20px;
        }
        .hero-panel-value {
            margin-top: 5px;
            font-size: 1.03rem;
        }
        .hero-panel-note {
            margin-top: 8px;
            font-size: .76rem;
            line-height: 1.36;
        }
        .hero-mini-grid {
            margin-top: 12px;
            gap: 7px;
        }
        .hero-mini {
            padding: 9px;
            border-radius: 14px;
        }
        .hero-mini b {
            font-size: .88rem;
        }
        .hero-mini span {
            font-size: .68rem;
        }
        .menu-board-title {
            margin-top: .42rem;
        }
        .menu-board-title span {
            display: none;
        }
        .snapshot-grid {
            grid-template-columns: 1fr;
        }
        .home-kpi-grid {
            grid-template-columns: 1fr;
        }
        .app-menu-board,
        .quick-menu-board {
            grid-template-columns: 1fr;
        }
        .st-key-app_menu_grid div[data-testid="stButton"] > button {
            min-height: 58px;
            padding: 10px 12px;
            border-radius: 18px;
        }
        .st-key-app_menu_grid div[data-testid="stButton"] > button p {
            line-height: 1.22;
            font-size: .8rem;
        }
        .st-key-app_menu_grid [data-testid="stBaseButton-secondary"] p::first-line {
            font-size: .9rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


APP_VIEWS = [
    {
        "id": "home",
        "label": "홈",
        "icon": "🏠",
        "desc": "전체 현황과 핵심 메뉴",
    },
    {
        "id": "revenue",
        "label": "매장 매출",
        "icon": "🏢",
        "desc": "월 예상매출과 매장 순위",
    },
    {
        "id": "investor",
        "label": "투자 리포트",
        "icon": "💼",
        "desc": "성장·가격·상권 인사이트",
    },
    {
        "id": "map",
        "label": "매출 지도",
        "icon": "📍",
        "desc": "주소 기반 매장 핀",
    },
    {
        "id": "store",
        "label": "매장 분석",
        "icon": "📊",
        "desc": "매장별 예약률과 운영 타임",
    },
    {
        "id": "theme",
        "label": "테마 분석",
        "icon": "🎟️",
        "desc": "인기 테마와 가격 누락",
    },
    {
        "id": "trend",
        "label": "패턴 분석",
        "icon": "⏱️",
        "desc": "요일·시간대 예약 흐름",
    },
    {
        "id": "manual",
        "label": "수동 자료",
        "icon": "🧾",
        "desc": "수동 관측 매출 자료",
    },
    {
        "id": "status",
        "label": "수집 상태",
        "icon": "✅",
        "desc": "성공·실패·미확인 점검",
    },
    {
        "id": "raw",
        "label": "원본 슬롯",
        "icon": "🗂️",
        "desc": "예약 슬롯 원본 테이블",
    },
]


def current_app_view() -> str:
    valid_views = {item["id"] for item in APP_VIEWS}
    raw_value = st.query_params.get("view")
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None
    stored_view = str(st.session_state.get("active_view", "") or "")
    view = str(raw_value or stored_view or "home")
    if view not in valid_views:
        view = "home"
    st.session_state["active_view"] = view
    return view


def select_app_view(view: str) -> None:
    if view in {item["id"] for item in APP_VIEWS}:
        st.session_state["active_view"] = view
        st.query_params["view"] = view


def render_app_menu(active_view: str) -> None:
    active_item = next(
        (item for item in APP_VIEWS if item["id"] == active_view),
        APP_VIEWS[0],
    )
    title = f"{PRODUCT_NAME_KO} 홈" if active_view == "home" else active_item["label"]
    subtitle = (
        "필요한 메뉴만 빠르게 열어보세요."
        if active_view == "home"
        else "다른 메뉴로 바로 이동할 수 있습니다."
    )
    st.markdown(
        f"""
        <div class="menu-board-title">
          <div>
            <h3>{escape(title)}</h3>
            <span>{escape(subtitle)}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="app_menu_grid"):
        for row_start in range(0, len(APP_VIEWS), 5):
            columns = st.columns(5, gap="small")
            for column, item in zip(columns, APP_VIEWS[row_start:row_start + 5]):
                label = f"{item['icon']}  {item['label']}\n{item['desc']}"
                with column:
                    st.button(
                        label,
                        key=f"app_nav_{item['id']}",
                        type="primary" if item["id"] == active_view else "secondary",
                        use_container_width=True,
                        on_click=select_app_view,
                        args=(item["id"],),
                    )


def install_loading_overlay(active_view: str) -> None:
    components.html(
        """
        <script>
        (() => {
          const win = window.parent;
          const doc = win.document;
          const currentView = __CURRENT_VIEW__;
          const styleId = "escape-room-loading-overlay-style";
          if (!doc.getElementById(styleId)) {
            const style = doc.createElement("style");
            style.id = styleId;
            style.textContent = `
              @keyframes escapeRoomOverlaySpin {
                to { transform: rotate(360deg); }
              }
              @keyframes escapeRoomOverlayPulse {
                0%, 100% { opacity: .52; transform: scale(.92); }
                50% { opacity: .96; transform: scale(1.05); }
              }
              #escape-room-loading-overlay {
                position: fixed;
                top: 18px;
                right: 24px;
                z-index: 999998;
                width: min(292px, calc(100vw - 32px));
                min-height: 58px;
                display: flex;
                align-items: center;
                background: rgba(25,31,40,.94);
                border: 1px solid rgba(255,255,255,.12);
                border-radius: 20px;
                box-shadow: 0 18px 48px rgba(15,23,42,.22);
                backdrop-filter: blur(18px) saturate(1.2);
                opacity: 0;
                transform: translateY(-8px) scale(.985);
                pointer-events: none;
                transition: opacity .12s ease, transform .12s ease;
              }
              #escape-room-loading-overlay.is-visible {
                opacity: 1;
                transform: translateY(0) scale(1);
              }
              #escape-room-loading-overlay .escape-loading-card {
                position: relative;
                width: 100%;
                display: grid;
                grid-template-columns: 32px 1fr;
                column-gap: 11px;
                align-items: center;
                padding: 12px 15px;
                overflow: hidden;
              }
              #escape-room-loading-overlay .escape-loading-ring {
                position: relative;
                grid-row: span 2;
                width: 30px;
                height: 30px;
                margin: 0;
                border-radius: 999px;
                background: conic-gradient(from 80deg, #ffffff, #8bc0ff, #00c2a8, #ffffff);
                -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 5px));
                mask: radial-gradient(farthest-side, transparent calc(100% - 6px), #000 calc(100% - 5px));
                animation: escapeRoomOverlaySpin .72s linear infinite;
              }
              #escape-room-loading-overlay .escape-loading-title {
                position: relative;
                color: #ffffff;
                font-size: .92rem;
                font-weight: 900;
                letter-spacing: -.04em;
                line-height: 1.2;
              }
              #escape-room-loading-overlay .escape-loading-note {
                position: relative;
                margin-top: 3px;
                color: rgba(255,255,255,.70);
                font-size: .76rem;
                font-weight: 750;
                line-height: 1.25;
                letter-spacing: -.035em;
              }
            `;
            doc.head.appendChild(style);
          }

          let overlay = doc.getElementById("escape-room-loading-overlay");
          if (!overlay) {
            overlay = doc.createElement("div");
            overlay.id = "escape-room-loading-overlay";
            overlay.innerHTML = `
              <div class="escape-loading-card" role="status" aria-live="polite">
                <div class="escape-loading-ring"></div>
                <div class="escape-loading-title">지표 불러오는 중</div>
                <div class="escape-loading-note">예약률·매출 데이터를 정리합니다</div>
              </div>
            `;
            doc.body.appendChild(overlay);
          }

          const show = (note) => {
            const noteEl = overlay.querySelector(".escape-loading-note");
            if (noteEl && note) noteEl.textContent = note;
            overlay.classList.add("is-visible");
            win.clearTimeout(win.escapeRoomLoadingTimer);
            win.escapeRoomLoadingTimer = win.setTimeout(() => {
              overlay.classList.remove("is-visible");
            }, 780);
          };
          const hide = () => {
            overlay.classList.remove("is-visible");
            win.clearTimeout(win.escapeRoomLoadingTimer);
          };

          win.escapeRoomShowLoading = show;
          win.escapeRoomHideLoading = hide;
          hide();

          if (
            win.escapeRoomLastRenderedView &&
            win.escapeRoomLastRenderedView !== currentView
          ) {
            win.requestAnimationFrame(() => {
              win.scrollTo({top: 0, left: 0, behavior: "instant"});
            });
          }
          win.escapeRoomLastRenderedView = currentView;

          if (!win.escapeRoomLoadingListenerInstalled) {
            const navLabels = [
              "홈", "매장 매출", "투자 리포트", "매출 지도", "매장 분석", "테마 분석",
              "패턴 분석", "수동 자료", "수집 상태", "원본 슬롯"
            ];
            doc.addEventListener("click", (event) => {
              const target = event.target.closest("button, a.app-menu-card");
              if (!target) return;
              const text = target.innerText || "";
              if (target.matches("a.app-menu-card") || navLabels.some((label) => text.includes(label))) {
                win.scrollTo({top: 0, left: 0, behavior: "instant"});
                show("새 화면 지표를 계산하고 있습니다");
              }
            }, true);
            win.escapeRoomLoadingListenerInstalled = true;
          }
        })();
        </script>
        """.replace("__CURRENT_VIEW__", json.dumps(active_view)),
        height=0,
    )


def config_file() -> Path:
    return CONFIG_PATH if CONFIG_PATH.exists() else PROJECT_DIR / "stores.yaml"


def manual_estimates_file() -> Path:
    return (
        MANUAL_ESTIMATES_PATH
        if MANUAL_ESTIMATES_PATH.exists()
        else PROJECT_DIR / "manual_estimates.yaml"
    )


def sync_catalog() -> None:
    if DEMO_MODE:
        return
    database = Database(DB_PATH)
    database.initialize()
    database.delete_stores_by_adapter("masterkey")
    database.delete_stores_by_adapter("sherlock")
    database.delete_stores_by_ids(RETIRED_STORE_IDS)
    stores = [
        store for store in load_stores(config_file())
        if store.store_id not in RETIRED_STORE_IDS
    ]
    database.sync_stores(stores)
    database.recalculate_slot_estimates()


@st.cache_data(ttl=300, show_spinner=False)
def read_data(
    db_mtime: float | None,
    config_mtime: float | None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    del db_mtime, config_mtime
    sync_catalog()
    return load_slots(DB_PATH), load_catalog(DB_PATH), load_store_status(DB_PATH)


@st.cache_data(ttl=300, show_spinner=False)
def read_manual_data(
    manual_mtime: float | None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    del manual_mtime
    return load_manual_estimates(manual_estimates_file())


def read_metric_snapshots_readonly(db_path: Path) -> pd.DataFrame:
    columns = [
        "id",
        "snapshot_date",
        "created_at",
        "scope_label",
        "store_count",
        "theme_count",
        "measured_slots",
        "reserved_slots",
        "booking_rate",
        "period_revenue",
        "projected_monthly_revenue",
        "average_store_monthly_revenue",
        "payload_json",
    ]
    if not db_path.exists():
        return pd.DataFrame(columns=columns)
    query = """
        SELECT
            id, snapshot_date, created_at, scope_label, store_count,
            theme_count, measured_slots, reserved_slots, booking_rate,
            period_revenue, projected_monthly_revenue,
            average_store_monthly_revenue, payload_json
        FROM metric_snapshots
        ORDER BY snapshot_date DESC, created_at DESC
        LIMIT 180
    """
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            return pd.read_sql_query(query, connection)
    except (sqlite3.DatabaseError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=columns)


def won(value: float) -> str:
    return f"{value:,.0f}원"


def won_range(minimum: float, maximum: float) -> str:
    if round(minimum) == round(maximum):
        return won(minimum)
    return f"{won(minimum)} ~ {won(maximum)}"


def compact_won(value: float) -> str:
    return won(value)


def compact_won_range(minimum: float, maximum: float) -> str:
    if round(minimum) == round(maximum):
        return won(minimum)
    return won_range(minimum, maximum)


def _brand_name(row: pd.Series) -> str:
    explicit = str(row.get("brand_name", "") or "").strip()
    if explicit:
        return explicit
    store_name = str(row.get("store_name", "") or "").strip()
    return store_name.split()[0] if store_name else "Escape"


def _brand_logo_url(row: pd.Series) -> str:
    explicit = str(row.get("brand_logo_url", "") or "").strip()
    if explicit:
        return explicit
    brand = _brand_name(row)
    return (
        "https://ui-avatars.com/api/"
        f"?name={quote(brand)}&background=147d72&color=fff"
        "&bold=true&rounded=true&size=128&format=png"
    )


def _stable_offset(seed_text: str) -> tuple[float, float]:
    seed = sum((index + 1) * ord(char) for index, char in enumerate(seed_text))
    lat_offset = ((seed % 17) - 8) * 0.00075
    lon_offset = (((seed // 17) % 17) - 8) * 0.0009
    return lat_offset, lon_offset


def _coerce_coordinate(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _region_coordinate(region: str) -> tuple[float, float]:
    if region in REGION_COORDINATES:
        return REGION_COORDINATES[region]
    for key in sorted(REGION_COORDINATES, key=len, reverse=True):
        if key and key in region:
            return REGION_COORDINATES[key]
    return DEFAULT_MAP_COORDINATE


def _map_position(row: pd.Series) -> tuple[float | None, float | None, str]:
    latitude = _coerce_coordinate(row.get("latitude"))
    longitude = _coerce_coordinate(row.get("longitude"))
    if latitude is not None and longitude is not None:
        return latitude, longitude, "확인 좌표"
    return None, None, "좌표 미확인"


def build_store_map_frame(
    status_frame: pd.DataFrame,
    estimate_frame: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "store_id",
        "store_name",
        "region",
        "booking_url",
        "adapter_type",
        "collection_note",
        "address",
        "brand_name",
        "brand_logo_url",
        "map_note",
        "latitude",
        "longitude",
        "coordinate_accuracy",
        "estimate_source",
        "booking_rate_min",
        "booking_rate_max",
        "monthly_revenue_min",
        "monthly_revenue_mid",
        "monthly_revenue_max",
        "daily_revenue_mid",
        "observed_days",
        "confidence",
        "collection_status",
        "monthly_label",
        "booking_rate_label",
        "booking_rate_mid",
        "monthly_short",
        "pin_radius",
        "color",
        "tooltip",
    ]
    if status_frame.empty and estimate_frame.empty:
        return pd.DataFrame(columns=columns)

    base_columns = [
        "store_id",
        "store_name",
        "region",
        "booking_url",
        "adapter_type",
        "collection_note",
        "address",
        "latitude",
        "longitude",
        "brand_name",
        "brand_logo_url",
        "map_note",
    ]
    base = status_frame.reindex(columns=base_columns).drop_duplicates("store_id")
    estimate_columns = [
        "store_id",
        "store_name",
        "region",
        "estimate_source",
        "booking_rate_min",
        "booking_rate_max",
        "daily_revenue_mid",
        "monthly_revenue_min",
        "monthly_revenue_mid",
        "monthly_revenue_max",
        "observed_days",
        "confidence",
    ]
    estimates = (
        estimate_frame.reindex(columns=estimate_columns)
        if not estimate_frame.empty
        else pd.DataFrame(columns=estimate_columns)
    )
    if not estimates.empty:
        missing_base = estimates[
            ~estimates["store_id"].astype(str).isin(base["store_id"].astype(str))
        ].copy()
        if not missing_base.empty:
            supplemental = pd.DataFrame(columns=base_columns)
            supplemental["store_id"] = missing_base["store_id"].astype(str)
            supplemental["store_name"] = missing_base["store_name"]
            supplemental["region"] = missing_base["region"]
            for column in base_columns:
                if column not in supplemental.columns:
                    supplemental[column] = ""
            base = pd.concat([base, supplemental[base_columns]], ignore_index=True)
    frame = base.merge(
        estimates,
        on=["store_id"],
        how="left",
        suffixes=("", "_estimate"),
    )
    for display_column in ["store_name", "region"]:
        estimate_column = f"{display_column}_estimate"
        if estimate_column in frame.columns:
            estimate_values = frame[estimate_column].fillna("").astype(str).str.strip()
            frame[display_column] = estimate_values.where(
                estimate_values.ne(""),
                frame[display_column],
            )
            frame = frame.drop(columns=[estimate_column])
    for column in [
        "booking_rate_min",
        "booking_rate_max",
        "daily_revenue_mid",
        "monthly_revenue_min",
        "monthly_revenue_mid",
        "monthly_revenue_max",
        "observed_days",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["estimate_source"] = frame["estimate_source"].fillna("데이터 없음")
    frame["confidence"] = frame["confidence"].fillna("수집 데이터 없음")
    frame["collection_status"] = (
        frame["adapter_type"].map(STATUS_LABELS).fillna("확인 필요")
    )

    positions = frame.apply(_map_position, axis=1, result_type="expand")
    frame["latitude"] = positions[0]
    frame["longitude"] = positions[1]
    frame["coordinate_accuracy"] = positions[2]
    frame["brand_name"] = frame.apply(_brand_name, axis=1)
    frame["brand_logo_url"] = frame.apply(_brand_logo_url, axis=1)

    frame["monthly_label"] = frame.apply(
        lambda row: (
            won_range(row["monthly_revenue_min"], row["monthly_revenue_max"])
            if row["monthly_revenue_mid"] > 0
            else "매출 데이터 없음"
        ),
        axis=1,
    )
    frame["booking_rate_label"] = frame.apply(
        lambda row: (
            f"{row['booking_rate_min']:.1f}% ~ {row['booking_rate_max']:.1f}%"
            if row["booking_rate_max"] > row["booking_rate_min"]
            else (
                f"{row['booking_rate_max']:.1f}%"
                if row["booking_rate_max"] > 0
                else "예약률 데이터 없음"
            )
        ),
        axis=1,
    )
    frame["booking_rate_mid"] = (
        frame["booking_rate_min"] + frame["booking_rate_max"]
    ) / 2
    frame["monthly_short"] = frame["monthly_revenue_mid"].map(compact_won)
    max_monthly = float(frame["monthly_revenue_mid"].max() or 0)
    if max_monthly > 0:
        frame["pin_radius"] = (
            5
            + (frame["monthly_revenue_mid"] / max_monthly).pow(0.5) * 4
        )
    else:
        frame["pin_radius"] = 5

    color_map = {
        "자동 수집": [20, 165, 144, 230],
        "수동 관측": [113, 89, 222, 230],
        "데이터 없음": [150, 154, 150, 180],
    }
    frame["color"] = frame["estimate_source"].map(color_map).apply(
        lambda value: value if isinstance(value, list) else [240, 107, 79, 210]
    )
    frame["tooltip"] = frame.apply(
        lambda row: (
            f"<b>{row['store_name']}</b><br>"
            f"{row['region']} · {row['estimate_source']}<br>"
            f"월 예상: {row['monthly_label']}<br>"
            f"예약률: {row['booking_rate_label']}<br>"
            f"상태: {row['collection_status']}"
        ),
        axis=1,
    )
    return frame[columns].sort_values(
        ["monthly_revenue_mid", "booking_rate_max"],
        ascending=[False, False],
    )


def _map_links(row: pd.Series) -> tuple[str, str]:
    address = str(row.get("address", "") or "").strip()
    query = f"{row.get('store_name', '')} {address}".strip()
    encoded_query = quote(query)
    return (
        f"https://map.naver.com/p/search/{encoded_query}",
        f"https://www.google.com/maps/search/{encoded_query}",
    )


def build_leaflet_map_html(map_frame: pd.DataFrame) -> str:
    markers: list[dict[str, object]] = []
    for _, row in map_frame.iterrows():
        latitude = _coerce_coordinate(row.get("latitude"))
        longitude = _coerce_coordinate(row.get("longitude"))
        if latitude is None or longitude is None:
            continue
        source = str(row.get("estimate_source", "") or "")
        monthly_revenue = float(row.get("monthly_revenue_mid", 0) or 0)
        color = "#969a96"
        if monthly_revenue > 0:
            color = "#6557c8" if "수동" in source else "#147d72"
        naver_url, google_url = _map_links(row)
        markers.append(
            {
                "lat": latitude,
                "lon": longitude,
                "name": str(row.get("store_name", "") or ""),
                "region": str(row.get("region", "") or ""),
                "address": str(row.get("address", "") or ""),
                "source": source,
                "status": str(row.get("collection_status", "") or ""),
                "monthly": str(row.get("monthly_label", "") or ""),
                "bookingRate": str(row.get("booking_rate_label", "") or ""),
                "logo": str(row.get("brand_logo_url", "") or ""),
                "naverUrl": naver_url,
                "googleUrl": google_url,
                "color": color,
            }
        )

    marker_json = json.dumps(markers, ensure_ascii=False)
    return f"""
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    crossorigin=""
  />
  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin="">
  </script>
  <style>
    .leaflet-container {{
      overflow: hidden;
      position: relative;
      outline: 0;
    }}
    .leaflet-pane,
    .leaflet-tile,
    .leaflet-marker-icon,
    .leaflet-marker-shadow,
    .leaflet-tile-container,
    .leaflet-pane > svg,
    .leaflet-pane > canvas,
    .leaflet-zoom-box,
    .leaflet-image-layer,
    .leaflet-layer {{
      position: absolute;
      left: 0;
      top: 0;
    }}
    .leaflet-map-pane,
    .leaflet-tile-pane,
    .leaflet-overlay-pane,
    .leaflet-shadow-pane,
    .leaflet-marker-pane,
    .leaflet-tooltip-pane,
    .leaflet-popup-pane {{
      position: absolute;
      left: 0;
      top: 0;
    }}
    .leaflet-map-pane {{ z-index: 400; }}
    .leaflet-tile-pane {{ z-index: 200; }}
    .leaflet-overlay-pane {{ z-index: 400; }}
    .leaflet-shadow-pane {{ z-index: 500; }}
    .leaflet-marker-pane {{ z-index: 600; }}
    .leaflet-tooltip-pane {{ z-index: 650; }}
    .leaflet-popup-pane {{ z-index: 700; }}
    .leaflet-tile,
    .leaflet-marker-icon,
    .leaflet-marker-shadow {{
      user-select: none;
      -webkit-user-drag: none;
      display: block;
      border: 0;
    }}
    .leaflet-tile {{
      width: 256px;
      height: 256px;
      visibility: hidden;
    }}
    .leaflet-tile-loaded {{
      visibility: inherit;
    }}
    .leaflet-zoom-animated {{
      transform-origin: 0 0;
    }}
    .leaflet-top,
    .leaflet-bottom {{
      position: absolute;
      z-index: 1000;
      pointer-events: none;
    }}
    .leaflet-top {{ top: 0; }}
    .leaflet-right {{ right: 0; }}
    .leaflet-bottom {{ bottom: 0; }}
    .leaflet-left {{ left: 0; }}
    .leaflet-control {{
      position: relative;
      z-index: 800;
      pointer-events: auto;
      float: left;
      clear: both;
      background: white;
      border-radius: 10px;
      box-shadow: 0 4px 16px rgba(23, 33, 31, .15);
      margin: 10px;
      overflow: hidden;
    }}
    .leaflet-control a {{
      display: block;
      width: 30px;
      height: 30px;
      line-height: 30px;
      text-align: center;
      color: #17211f;
      text-decoration: none;
      font-weight: 800;
      border-bottom: 1px solid #e6e1d7;
    }}
    .leaflet-popup {{
      position: absolute;
      text-align: center;
      margin-bottom: 20px;
    }}
    .leaflet-popup-content-wrapper,
    .leaflet-popup-tip {{
      background: white;
    }}
    .leaflet-popup-tip-container {{
      width: 40px;
      height: 20px;
      position: absolute;
      left: 50%;
      margin-left: -20px;
      overflow: hidden;
      pointer-events: none;
    }}
    .leaflet-popup-tip {{
      width: 17px;
      height: 17px;
      padding: 1px;
      margin: -10px auto 0;
      transform: rotate(45deg);
      box-shadow: 0 3px 14px rgba(23, 33, 31, .18);
    }}
    html, body, #map {{
      height: 100%;
      margin: 0;
      background: #f6f3ec;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    #map {{
      border: 1px solid #dedbd1;
      border-radius: 18px;
      overflow: hidden;
    }}
    .pin-wrap {{
      background: transparent;
      border: 0;
      pointer-events: auto;
    }}
    .pin-marker {{
      width: 18px;
      height: 18px;
      border-radius: 50% 50% 50% 0;
      transform: rotate(-45deg);
      border: 2px solid #fff;
      box-shadow: 0 8px 18px rgba(23, 33, 31, .24);
      position: relative;
    }}
    .pin-marker::after {{
      content: "";
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: rgba(255, 255, 255, .9);
      position: absolute;
      left: 4px;
      top: 4px;
    }}
    .leaflet-tooltip {{
      position: absolute;
      padding: 0;
      pointer-events: none;
      white-space: nowrap;
    }}
    .pin-tooltip {{
      background: rgba(23, 33, 31, .95);
      border: 0;
      border-radius: 14px;
      box-shadow: 0 16px 32px rgba(23, 33, 31, .24);
      color: white;
    }}
    .pin-tip {{
      display: grid;
      gap: 3px;
      min-width: 165px;
      padding: 9px 11px;
      text-align: left;
    }}
    .pin-tip b {{
      font-size: 13px;
      letter-spacing: -0.03em;
    }}
    .pin-tip span {{
      color: #7adbc9;
      font-size: 13px;
      font-weight: 800;
    }}
    .pin-tip small {{
      color: #d7dedb;
      font-size: 11px;
    }}
    .leaflet-popup-content-wrapper {{
      border-radius: 16px;
      box-shadow: 0 18px 38px rgba(23, 33, 31, .2);
    }}
    .leaflet-popup-content {{
      margin: 13px 14px;
      min-width: 245px;
      color: #17211f;
    }}
    .popup-head {{
      display: flex;
      gap: 10px;
      align-items: center;
      margin-bottom: 9px;
    }}
    .popup-head img {{
      width: 38px;
      height: 38px;
      border-radius: 11px;
      border: 1px solid #e5e0d5;
      object-fit: cover;
      background: #147d72;
    }}
    .popup-title {{
      font-weight: 800;
      font-size: 15px;
      letter-spacing: -0.03em;
    }}
    .popup-meta {{
      color: #64716c;
      font-size: 12px;
      line-height: 1.45;
    }}
    .popup-money {{
      margin: 9px 0;
      padding: 9px 10px;
      border-radius: 12px;
      background: #f5f3ed;
      font-size: 12px;
      line-height: 1.55;
    }}
    .popup-money b {{
      color: #147d72;
      font-size: 14px;
    }}
    .popup-links a {{
      display: inline-block;
      margin-right: 6px;
      padding: 6px 9px;
      border-radius: 999px;
      background: #17211f;
      color: white;
      text-decoration: none;
      font-size: 12px;
      font-weight: 700;
    }}
    .popup-links a:last-child {{
      background: #147d72;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <script>
    const markers = {marker_json};
    const map = L.map("map", {{
      scrollWheelZoom: true,
      zoomControl: true
    }});

    L.tileLayer("https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }}).addTo(map);

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }}[ch]));
    }}

    const bounds = [];
    const seoulBounds = [];
    markers.forEach((marker) => {{
      const icon = L.divIcon({{
        className: "pin-wrap",
        html: `<div class="pin-marker" style="background:${{marker.color}}"></div>`,
        iconSize: [24, 28],
        iconAnchor: [12, 28],
        popupAnchor: [0, -28]
      }});
      const popupHtml = `
        <div class="popup-head">
          <img src="${{escapeHtml(marker.logo)}}" alt="">
          <div>
            <div class="popup-title">${{escapeHtml(marker.name)}}</div>
            <div class="popup-meta">${{escapeHtml(marker.region)}} · ${{escapeHtml(marker.source)}}</div>
          </div>
        </div>
        <div class="popup-meta">${{escapeHtml(marker.address)}}</div>
        <div class="popup-money">
          월 예상 <b>${{escapeHtml(marker.monthly)}}</b><br>
          예약률 ${{escapeHtml(marker.bookingRate)}}<br>
          상태 ${{escapeHtml(marker.status)}}
        </div>
        <div class="popup-links">
          <a href="${{escapeHtml(marker.naverUrl)}}" target="_blank" rel="noopener">네이버 지도</a>
          <a href="${{escapeHtml(marker.googleUrl)}}" target="_blank" rel="noopener">Google 지도</a>
        </div>
      `;
      const tooltipHtml = `
        <div class="pin-tip">
          <b>${{escapeHtml(marker.name)}}</b>
          <span>${{escapeHtml(marker.monthly)}}</span>
          <small>${{escapeHtml(marker.bookingRate)}} · ${{escapeHtml(marker.status)}}</small>
        </div>
      `;
      const markerLayer = L.marker([marker.lat, marker.lon], {{icon}})
        .addTo(map)
        .bindPopup(popupHtml, {{maxWidth: 320}})
        .bindTooltip(tooltipHtml, {{
          direction: "top",
          offset: [0, -24],
          sticky: true,
          opacity: 1,
          className: "pin-tooltip"
        }});
      markerLayer.on("mouseover", () => markerLayer.openTooltip());
      markerLayer.on("mouseout", () => markerLayer.closeTooltip());
      const markerElement = markerLayer.getElement();
      if (markerElement) {{
        markerElement.addEventListener("mouseenter", () => markerLayer.openTooltip());
        markerElement.addEventListener("mouseover", () => markerLayer.openTooltip());
        markerElement.addEventListener("mousemove", () => markerLayer.openTooltip());
        markerElement.addEventListener("mouseleave", () => markerLayer.closeTooltip());
      }}
      bounds.push([marker.lat, marker.lon]);
      if (String(marker.region || "").includes("서울")) {{
        seoulBounds.push([marker.lat, marker.lon]);
      }}
    }});

    function fitMapToMarkers() {{
      map.invalidateSize();
      const preferredBounds = (
        bounds.length > 15 && seoulBounds.length >= 8
      ) ? seoulBounds : bounds;
      if (preferredBounds.length === 1) {{
        map.setView(preferredBounds[0], 15);
      }} else if (preferredBounds.length > 1) {{
        map.fitBounds(preferredBounds, {{padding: [34, 34], maxZoom: 13}});
      }} else {{
        map.setView([37.5665, 126.9780], 11);
      }}
    }}

    requestAnimationFrame(fitMapToMarkers);
    setTimeout(fitMapToMarkers, 150);
    setTimeout(fitMapToMarkers, 700);
    window.addEventListener("resize", fitMapToMarkers);
  </script>
</body>
</html>
"""


def modern_bar_chart(
    frame: pd.DataFrame,
    category: str,
    value: str,
    *,
    value_title: str,
    horizontal: bool = True,
    color: str = "#3182f6",
    height: int | None = None,
) -> alt.Chart:
    chart_frame = frame[[category, value]].dropna().copy()
    chart_height = height or min(max(220, len(chart_frame) * 30), 500)
    tooltip = [
        alt.Tooltip(f"{category}:N", title="구분"),
        alt.Tooltip(f"{value}:Q", title=value_title, format=",.1f"),
    ]
    if horizontal:
        base = alt.Chart(chart_frame)
        bars = (
            base
            .mark_bar(cornerRadiusEnd=10, color=color, opacity=0.92)
            .encode(
                x=alt.X(
                    f"{value}:Q",
                    title=value_title,
                    axis=alt.Axis(gridColor="#eef2f7"),
                ),
                y=alt.Y(
                    f"{category}:N",
                    title=None,
                    sort="-x",
                    axis=alt.Axis(labelLimit=210),
                ),
                tooltip=tooltip,
            )
        )
        labels = (
            base.mark_text(
                align="left",
                baseline="middle",
                dx=6,
                color="#4e5968",
                fontWeight=800,
            )
            .encode(
                x=alt.X(f"{value}:Q"),
                y=alt.Y(f"{category}:N", sort="-x"),
                text=alt.Text(f"{value}:Q", format=",.0f"),
            )
        )
    else:
        base = alt.Chart(chart_frame)
        bars = (
            base
            .mark_bar(
                cornerRadiusTopLeft=10,
                cornerRadiusTopRight=10,
                color=color,
                opacity=0.92,
            )
            .encode(
                x=alt.X(
                    f"{category}:N",
                    title=None,
                    sort=None,
                    axis=alt.Axis(labelAngle=0, labelLimit=100),
                ),
                y=alt.Y(
                    f"{value}:Q",
                    title=value_title,
                    axis=alt.Axis(gridColor="#eef2f7"),
                ),
                tooltip=tooltip,
            )
        )
        labels = (
            base.mark_text(
                dy=-7,
                color="#4e5968",
                fontWeight=800,
            )
            .encode(
                x=alt.X(f"{category}:N", sort=None),
                y=alt.Y(f"{value}:Q"),
                text=alt.Text(f"{value}:Q", format=",.0f"),
            )
        )
    return (
        (bars + labels).properties(height=chart_height)
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor="#6b7684",
            titleColor="#6b7684",
            domainColor="#d8dee8",
            tickColor="#d8dee8",
        )
    )


def modern_line_chart(
    frame: pd.DataFrame,
    x: str,
    y: str,
    *,
    value_title: str,
    extra_tooltips: list[str] | None = None,
    color: str = "#3182f6",
) -> alt.Chart:
    tooltip: list[alt.Tooltip] = [
        alt.Tooltip(f"{x}:T", title="날짜"),
        alt.Tooltip(f"{y}:Q", title=value_title, format=",.0f"),
    ]
    for column in extra_tooltips or []:
        tooltip.append(alt.Tooltip(f"{column}:Q", title=column, format=",.0f"))
    base = alt.Chart(frame).encode(
        x=alt.X(f"{x}:T", title=None, axis=alt.Axis(format="%m/%d")),
        y=alt.Y(
            f"{y}:Q",
            title=value_title,
            scale=alt.Scale(zero=True),
            axis=alt.Axis(gridColor="#eef2f7"),
        ),
        tooltip=tooltip,
    )
    area = base.mark_area(
        color=color,
        opacity=0.16,
        interpolate="monotone",
    )
    line = base.mark_line(
        color=color,
        strokeWidth=3.4,
        point=alt.OverlayMarkDef(size=72, filled=True),
        interpolate="monotone",
    )
    return (
        (area + line)
        .properties(height=320)
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor="#6b7684",
            titleColor="#6b7684",
            domainColor="#d8dee8",
            tickColor="#d8dee8",
        )
    )


def rate_label(frame: pd.DataFrame) -> str:
    return f"{booking_rate(frame):.1f}%" if not frame.empty else "-"


def current_month_bounds(reference: date) -> tuple[date, date]:
    start = reference.replace(day=1)
    next_month = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, next_month - timedelta(days=1)


def report_value(value: object, column: str = "") -> str:
    if value is None or pd.isna(value):
        return "-"
    if isinstance(value, (int, float)):
        if "rate" in column or "coverage" in column or "delta_pct" in column:
            return f"{float(value):.1f}%"
        if "delta" in column and "revenue" not in column:
            return f"{float(value):+.1f}"
        if any(token in column for token in ("revenue", "price", "value")):
            return won(float(value))
        return f"{float(value):,.1f}" if float(value) % 1 else f"{int(value):,}"
    return str(value)


def report_table_html(
    frame: pd.DataFrame,
    columns: list[str],
    labels: dict[str, str],
    *,
    limit: int = 12,
) -> str:
    if frame.empty:
        return "<p class='muted'>표시할 데이터가 없습니다.</p>"
    header = "".join(f"<th>{escape(labels.get(column, column))}</th>" for column in columns)
    rows: list[str] = []
    for _, row in frame.head(limit).iterrows():
        cells = "".join(
            f"<td>{escape(report_value(row.get(column), column))}</td>"
            for column in columns
        )
        rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def report_table_rows(
    frame: pd.DataFrame,
    columns: list[str],
    labels: dict[str, str],
    *,
    limit: int = 12,
) -> list[list[str]]:
    rows = [[labels.get(column, column) for column in columns]]
    if frame.empty:
        rows.append(["데이터 없음", *["" for _ in columns[1:]]])
        return rows
    for _, row in frame.head(limit).iterrows():
        rows.append([report_value(row.get(column), column) for column in columns])
    return rows


def build_investor_report_html(
    *,
    metrics: dict[str, str],
    top_stores: pd.DataFrame,
    growth: pd.DataFrame,
    price_strategy: pd.DataFrame,
    efficiency: pd.DataFrame,
    radius: pd.DataFrame,
    snapshots: pd.DataFrame,
) -> bytes:
    now_text = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    metric_cards = "".join(
        f"<div class='metric'><span>{escape(label)}</span><b>{escape(value)}</b></div>"
        for label, value in metrics.items()
    )
    html = f"""
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{PRODUCT_NAME} 투자자 리포트</title>
<style>
body {{font-family: Pretendard, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color:#191f28; margin:32px; background:#f8fafc;}}
h1 {{font-size:30px; margin:0 0 6px; letter-spacing:-.04em;}}
h2 {{font-size:19px; margin:28px 0 10px;}}
.muted {{color:#6b7684;}}
.metrics {{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:22px 0;}}
.metric {{background:white; border:1px solid #e5e8ef; border-radius:18px; padding:16px;}}
.metric span {{display:block; color:#6b7684; font-size:12px; font-weight:800;}}
.metric b {{display:block; margin-top:8px; font-size:21px;}}
table {{width:100%; border-collapse:collapse; background:white; border-radius:16px; overflow:hidden; margin-bottom:12px;}}
th, td {{border-bottom:1px solid #eef2f7; padding:10px 11px; text-align:left; font-size:12px;}}
th {{background:#f1f5f9; color:#4e5968; font-weight:900;}}
.note {{background:#eef6ff; border:1px solid #dbeafe; padding:14px 16px; border-radius:16px; color:#334155;}}
@media print {{body {{background:white; margin:18mm;}} .metric, table {{break-inside:avoid;}}}}
</style>
</head>
<body>
<h1>{PRODUCT_NAME} 투자자 리포트</h1>
<div class="muted">생성 시각 {escape(now_text)} · 공개 예약표 기반 추정치, 실제 결제 매출 아님</div>
<section class="metrics">{metric_cards}</section>
<div class="note">가격, 평균 인원, 예약 상태가 모두 공개 정보로 확인된 범위만 계산합니다. 수동 관측 자료는 자동 수집과 출처를 분리해 합산합니다.</div>
<h2>월 예상매출 상위 매장</h2>
{report_table_html(top_stores, ["store_name", "region", "estimate_source", "monthly_revenue_mid", "confidence"], {"store_name":"매장","region":"지역","estimate_source":"출처","monthly_revenue_mid":"월 예상","confidence":"신뢰도"})}
<h2>성장 추세</h2>
{report_table_html(growth, ["store_name", "current_revenue", "previous_revenue", "revenue_delta", "revenue_delta_pct", "trend_label"], {"store_name":"매장","current_revenue":"최근 7일","previous_revenue":"이전 7일","revenue_delta":"증감","revenue_delta_pct":"증감률","trend_label":"판정"})}
<h2>가격 전략</h2>
{report_table_html(price_strategy, ["strategy", "store_name", "theme_name", "booking_rate", "per_person_estimate", "estimated_revenue"], {"strategy":"전략","store_name":"매장","theme_name":"테마","booking_rate":"예약률","per_person_estimate":"추정 인당","estimated_revenue":"기간 매출"})}
<h2>회차·시간 효율</h2>
{report_table_html(efficiency, ["store_name", "booking_rate", "revenue_per_measured_slot", "revenue_per_operating_hour", "estimated_revenue"], {"store_name":"매장","booking_rate":"예약률","revenue_per_measured_slot":"공개 회차당","revenue_per_operating_hour":"운영시간당","estimated_revenue":"기간 매출"})}
<h2>상권 반경 분석</h2>
{report_table_html(radius, ["anchor_store_name", "nearby_store_count", "monthly_revenue_sum", "average_store_monthly_revenue", "top_store_name"], {"anchor_store_name":"중심 매장","nearby_store_count":"반경 내 매장","monthly_revenue_sum":"반경 월매출","average_store_monthly_revenue":"매장 평균","top_store_name":"상권 1위"})}
<h2>스냅샷 기록</h2>
{report_table_html(snapshots, ["snapshot_date", "scope_label", "booking_rate", "projected_monthly_revenue", "average_store_monthly_revenue"], {"snapshot_date":"기준일","scope_label":"범위","booking_rate":"예약률","projected_monthly_revenue":"월 예상","average_store_monthly_revenue":"매장 평균"})}
</body>
</html>
"""
    return html.encode("utf-8")


def build_investor_report_pdf(
    *,
    metrics: dict[str, str],
    top_stores: pd.DataFrame,
    growth: pd.DataFrame,
    price_strategy: pd.DataFrame,
    efficiency: pd.DataFrame,
    radius: pd.DataFrame,
    snapshots: pd.DataFrame,
) -> bytes | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        return None

    font_name = "Helvetica"
    for font_path in [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgunsl.ttf",
    ]:
        if font_path.exists():
            try:
                pdfmetrics.registerFont(TTFont("MalgunGothic", str(font_path)))
                font_name = "MalgunGothic"
                break
            except Exception:
                continue
    if font_name == "Helvetica":
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
            font_name = "HYSMyeongJo-Medium"
        except Exception:
            pass

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "KTitle", parent=styles["Title"], fontName=font_name, fontSize=20, leading=25
    )
    heading = ParagraphStyle(
        "KHeading", parent=styles["Heading2"], fontName=font_name, fontSize=13, leading=17
    )
    body = ParagraphStyle(
        "KBody", parent=styles["BodyText"], fontName=font_name, fontSize=8.5, leading=11
    )

    def paragraph(value: object) -> Paragraph:
        return Paragraph(escape(str(value)), body)

    def add_table(elements: list[object], frame: pd.DataFrame, columns: list[str], labels: dict[str, str]) -> None:
        rows = report_table_rows(frame, columns, labels, limit=10)
        table = Table([[paragraph(cell) for cell in row] for row in rows], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(table)
        elements.append(Spacer(1, 6))

    elements: list[object] = [
        Paragraph(f"{PRODUCT_NAME} 투자자 리포트", title),
        Paragraph(
            f"생성 시각 {datetime.now(KST):%Y-%m-%d %H:%M} · 공개 예약표 기반 추정치",
            body,
        ),
        Spacer(1, 8),
    ]
    metric_rows = [[paragraph(label), paragraph(value)] for label, value in metrics.items()]
    metric_table = Table(metric_rows, colWidths=[48 * mm, 122 * mm])
    metric_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee8")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
            ]
        )
    )
    elements.extend([metric_table, Spacer(1, 8)])

    sections = [
        ("월 예상매출 상위 매장", top_stores, ["store_name", "region", "estimate_source", "monthly_revenue_mid", "confidence"], {"store_name":"매장","region":"지역","estimate_source":"출처","monthly_revenue_mid":"월 예상","confidence":"신뢰도"}),
        ("성장 추세", growth, ["store_name", "current_revenue", "previous_revenue", "revenue_delta_pct", "trend_label"], {"store_name":"매장","current_revenue":"최근 7일","previous_revenue":"이전 7일","revenue_delta_pct":"증감률","trend_label":"판정"}),
        ("가격 전략", price_strategy, ["strategy", "store_name", "theme_name", "booking_rate", "per_person_estimate"], {"strategy":"전략","store_name":"매장","theme_name":"테마","booking_rate":"예약률","per_person_estimate":"추정 인당"}),
        ("회차·시간 효율", efficiency, ["store_name", "revenue_per_measured_slot", "revenue_per_operating_hour", "estimated_revenue"], {"store_name":"매장","revenue_per_measured_slot":"공개 회차당","revenue_per_operating_hour":"운영시간당","estimated_revenue":"기간 매출"}),
        ("상권 반경 분석", radius, ["anchor_store_name", "nearby_store_count", "monthly_revenue_sum", "average_store_monthly_revenue", "top_store_name"], {"anchor_store_name":"중심 매장","nearby_store_count":"반경 내","monthly_revenue_sum":"반경 월매출","average_store_monthly_revenue":"매장 평균","top_store_name":"상권 1위"}),
        ("스냅샷 기록", snapshots, ["snapshot_date", "scope_label", "booking_rate", "projected_monthly_revenue"], {"snapshot_date":"기준일","scope_label":"범위","booking_rate":"예약률","projected_monthly_revenue":"월 예상"}),
    ]
    for section_title, frame, columns, labels in sections:
        elements.append(Paragraph(section_title, heading))
        add_table(elements, frame, columns, labels)
    doc.build(elements)
    return buffer.getvalue()


def freshness_text(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "수집 데이터 없음"
    latest_by_store = frame.groupby("store_id")["crawled_at"].max()
    oldest = latest_by_store.min()
    newest = latest_by_store.max()
    if pd.isna(oldest) or pd.isna(newest):
        return "수집 시각 확인 불가"
    oldest_kst = oldest.tz_convert(KST)
    newest_kst = newest.tz_convert(KST)
    if oldest_kst.floor("min") == newest_kst.floor("min"):
        return newest_kst.strftime("%Y-%m-%d %H:%M")
    return f"{oldest_kst:%Y-%m-%d %H:%M} ~ {newest_kst:%H:%M}"


def run_online_refresh(
    store_ids: set[str] | None = None,
    days: int = 1,
    progress_callback=None,
) -> dict[str, int]:
    if DEMO_MODE or run_crawl is None:
        return {"success": 0, "failed": 0, "slots": 0, "skipped": 0}
    stores = [
        store
        for store in load_stores(config_file())
        if store.store_id not in RETIRED_STORE_IDS
        if store.adapter_type not in NON_CRAWLING_ADAPTERS
        and (not store_ids or store.store_id in store_ids)
    ]
    LOGGER.info(
        "Starting crawl days=%s stores=%s selected=%s delay=%s-%s parallel=%s timeout_ms=%s",
        days,
        len(stores),
        bool(store_ids),
        CRAWL_DELAY_MIN_SECONDS,
        CRAWL_DELAY_MAX_SECONDS,
        CRAWL_MAX_PARALLEL_ORIGINS,
        CRAWL_NAVIGATION_TIMEOUT_MS,
    )
    return run_crawl(
        stores=stores,
        target_dates=[TODAY + timedelta(days=offset) for offset in range(days)],
        database=Database(DB_PATH),
        delay_min_seconds=CRAWL_DELAY_MIN_SECONDS,
        delay_max_seconds=CRAWL_DELAY_MAX_SECONDS,
        minimum_recrawl_minutes=0,
        max_parallel_origins=CRAWL_MAX_PARALLEL_ORIGINS,
        max_navigation_timeout_ms=CRAWL_NAVIGATION_TIMEOUT_MS,
        progress_callback=progress_callback,
    )


def progress_ui(label: str):
    loader = st.empty()
    bar = st.progress(0, text=f"{label} 준비 중...")
    detail = st.empty()

    def update(event: dict[str, object]) -> None:
        completed = int(event.get("completed", 0) or 0)
        total = int(event.get("total", 0) or 0)
        stores_completed = int(event.get("stores_completed", 0) or 0)
        stores_total = int(event.get("stores_total", 0) or 0)
        percent = int(completed / total * 100) if total else 100
        current_store = str(event.get("current_store", "") or "")
        current_date = str(event.get("current_date", "") or "")
        phase = str(event.get("phase", "running"))
        text = (
            f"{label} {percent}% · 매장 {stores_completed}/{stores_total}곳 · "
            f"날짜 작업 {completed}/{total}건"
        )
        if phase == "complete":
            text = f"{label} 완료 · 매장 {stores_total}곳 · 날짜 작업 {total}건"
            loader.empty()
        else:
            loader.markdown(
                f"""
                <div class="refresh-loader">
                  <div class="refresh-spinner"></div>
                  <div class="refresh-copy">
                    <b>{escape(label)} 중</b>
                    <span>{escape(current_store or "공개 예약표 연결 중")} · 느린 페이지는 18초 안에 넘깁니다</span>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        bar.progress(min(percent, 100), text=text)
        if current_store:
            detail.caption(
                f"현재 반영: {current_store} · {current_date} · "
                f"성공 {int(event.get('success', 0) or 0)}건 · "
                f"실패 {int(event.get('failed', 0) or 0)}건 · "
                f"슬롯 {int(event.get('slots', 0) or 0):,}개"
            )

    return update


def render_refresh_notice() -> None:
    notice = st.session_state.pop("refresh_notice", None)
    if not notice:
        return
    level = str(notice.get("level", "info"))
    message = str(notice.get("message", ""))
    detail = str(notice.get("detail", ""))
    if level == "success":
        st.success(message)
    elif level == "warning":
        st.warning(message)
    elif level == "error":
        st.error(message)
    else:
        st.info(message)
    if detail:
        with st.expander("자세한 오류 보기", expanded=False):
            st.code(detail, language="text")


def format_job_timestamp(value: object) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def render_crawl_job_status() -> None:
    status = read_job_status(APP_HOME)
    if not status:
        return

    raw_status = str(status.get("status", ""))
    display_status = raw_status
    if raw_status == "starting":
        display_status = "running"
    if raw_status == "running" and not job_is_running(status):
        display_status = "stopped"

    progress = status.get("progress") or {}
    completed = int(progress.get("completed", 0) or 0)
    total = int(progress.get("total", 0) or 0)
    percent = int(completed / total * 100) if total else 0
    label = str(status.get("label", "예약 현황 업데이트"))
    current_store = str(progress.get("current_store", "") or "")
    current_date = str(progress.get("current_date", "") or "")
    success_count = int(progress.get("success", 0) or 0)
    failed_count = int(progress.get("failed", 0) or 0)
    slots_count = int(progress.get("slots", 0) or 0)

    if display_status == "running":
        st.markdown(
            f"""
            <div class="refresh-loader">
              <div class="refresh-spinner"></div>
              <div class="refresh-copy">
                <b>{escape(label)} 진행 중 · {percent}%</b>
                <span>{escape(current_store or "공개 예약표 연결 중")} {escape(current_date)} · 화면을 닫아도 서버에서 계속 수집합니다</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.progress(
            min(percent, 100),
            text=(
                f"{completed:,}/{total:,}건 · 성공 {success_count:,} · "
                f"실패 {failed_count:,} · 슬롯 {slots_count:,}개"
            ),
        )
        status_cols = st.columns([1, 1, 2.2])
        with status_cols[0]:
            if st.button("수집 상태 새로고침", width="stretch"):
                st.rerun()
        with status_cols[1]:
            st.caption(f"시작 {format_job_timestamp(status.get('started_at'))}")
        with status_cols[2]:
            st.caption("수집 중에도 다른 메뉴는 볼 수 있습니다. 데이터는 완료 후 다시 불러오면 반영됩니다.")
    elif display_status == "success":
        summary = status.get("summary") or {}
        st.success(
            f"{label} 완료 · 성공 {int(summary.get('success', 0) or 0):,}건 · "
            f"슬롯 {int(summary.get('slots', 0) or 0):,}개"
        )
        if st.button("완료된 데이터 다시 불러오기", width="stretch"):
            read_data.clear()
            st.rerun()
    elif display_status == "partial_success":
        summary = status.get("summary") or {}
        st.warning(
            f"{label} 완료 · 성공 {int(summary.get('success', 0) or 0):,}건 · "
            f"실패 {int(summary.get('failed', 0) or 0):,}건 · "
            f"슬롯 {int(summary.get('slots', 0) or 0):,}개"
        )
        if st.button("수집된 데이터 다시 불러오기", width="stretch"):
            read_data.clear()
            st.rerun()
    elif display_status == "stopped":
        st.error(
            f"{label}가 중간에 멈췄습니다. 서버 자원 또는 특정 예약 페이지 응답 문제일 수 있습니다."
        )
    elif display_status == "failed":
        st.error(f"{label} 실패. 아래 로그를 확인해 주세요.")

    log_text = tail_job_log(status, max_lines=60)
    if log_text and display_status in {"running", "partial_success", "failed", "stopped"}:
        with st.expander("수집 로그 보기", expanded=False):
            st.code(log_text, language="text")
    if display_status == "failed" and status.get("error"):
        with st.expander("오류 상세 보기", expanded=False):
            st.code(str(status["error"]), language="text")


def run_refresh_action(
    label: str,
    target_ids: set[str] | None,
    days: int,
) -> None:
    if (
        CLOUD_SAFE_CRAWL
        and STREAMLIT_CLOUD_RUNTIME
        and days >= 7
        and not target_ids
        and not ALLOW_CLOUD_FULL_WEEK
    ):
        message = (
            "Streamlit Cloud 무료 서버에서는 전체 매장 7일 수집을 한 번에 실행하면 "
            "브라우저 프로세스가 많아져 화면이 하얗게 멈출 수 있습니다. "
            "검색/필터에서 지역이나 매장을 선택해서 나눠 실행하거나, "
            "전체 7일 수집은 로컬 컴퓨터/VPS에서 실행해 주세요."
        )
        LOGGER.warning("Blocked full 7-day crawl on Streamlit Cloud safe mode.")
        st.warning(message)
        return

    LOGGER.info(
        "Refresh action requested label=%s days=%s selected=%s",
        label,
        days,
        bool(target_ids),
    )
    try:
        job = start_crawl_job(
            app_home=APP_HOME,
            project_dir=PROJECT_DIR,
            label=label,
            days=days,
            config_path=config_file(),
            db_path=DB_PATH,
            store_ids=target_ids,
            delay_min_seconds=CRAWL_DELAY_MIN_SECONDS,
            delay_max_seconds=CRAWL_DELAY_MAX_SECONDS,
            max_parallel_origins=CRAWL_MAX_PARALLEL_ORIGINS,
            max_navigation_timeout_ms=CRAWL_NAVIGATION_TIMEOUT_MS,
        )
    except CrawlJobAlreadyRunning:
        LOGGER.warning("Refresh action ignored because another crawl is running.")
        st.session_state["refresh_notice"] = {
            "level": "warning",
            "message": "이미 예약 수집이 실행 중입니다. 수집 상태 카드에서 진행률을 확인해 주세요.",
        }
        st.rerun()
    except Exception as exc:
        LOGGER.exception("Refresh job start failed label=%s days=%s", label, days)
        st.session_state["refresh_notice"] = {
            "level": "error",
            "message": (
                f"{label}를 시작하지 못했습니다. 서버의 Python/Docker 실행 상태를 확인해야 합니다."
            ),
            "detail": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        }
        st.rerun()
        return

    LOGGER.info("Refresh job started label=%s days=%s job_id=%s", label, days, job["job_id"])
    st.session_state["refresh_notice"] = {
        "level": "info",
        "message": (
            f"{label}를 서버 백그라운드에서 시작했습니다. "
            "화면을 닫거나 다른 메뉴로 이동해도 계속 진행됩니다."
        ),
    }
    st.rerun()


active_view = current_app_view()
active_item = next(
    (item for item in APP_VIEWS if item["id"] == active_view),
    APP_VIEWS[0],
)
if active_view == "home":
    st.markdown(
        """
        <div class="hero">
          <div class="hero-content">
            <div class="brand-lockup">
              <div class="brand-mark" aria-hidden="true"></div>
              <div>
                <div class="brand-name">LumiTrack</div>
                <div class="brand-subtitle">Escape Revenue OS</div>
              </div>
            </div>
            <div class="hero-title">방탈출 매출을 빠르게 본다</div>
            <div class="hero-copy">
              예약률, 객단가, 월 예상매출만 깔끔하게.
            </div>
            <div class="hero-badge-row">
              <div class="hero-badge">공개 예약표</div>
              <div class="hero-badge">평균 2.7명</div>
              <div class="hero-badge">추정 매출</div>
            </div>
          </div>
          <div class="hero-panel">
            <div class="hero-panel-label">LUMITRACK LIVE</div>
            <div class="hero-panel-value">예약률 · 객단가 · 월매출</div>
            <div class="hero-panel-note">매장, 지도, 테마, 패턴을 메뉴에서 바로 확인.</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f"""
        <div class="compact-hero">
          <div class="compact-brand">
            <div class="brand-mark" aria-hidden="true"></div>
            <div>
              <div class="compact-title">LumiTrack · {escape(active_item["label"])}</div>
              <div class="compact-desc">{escape(active_item["desc"])}</div>
            </div>
          </div>
          <div class="compact-pill">빠른 보기</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
render_app_menu(active_view)
install_loading_overlay(active_view)

db_mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else None
config_mtime = config_file().stat().st_mtime if config_file().exists() else None
data_loader = st.empty()
data_loader.markdown(
    """
    <div class="refresh-loader">
      <div class="refresh-spinner"></div>
      <div class="refresh-copy">
        <b>저장된 예약 데이터 불러오는 중</b>
        <span>필터와 매출 지표를 준비합니다</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)
data, catalog, store_status = read_data(db_mtime, config_mtime)
data_loader.empty()
if DEMO_MODE and not DB_PATH.exists():
    st.error(
        "데모 데이터베이스를 찾지 못했습니다. "
        "`demo_data/lumitrack_demo.sqlite` 파일을 프로젝트에 포함해 주세요."
    )
    st.stop()
manual_mtime = (
    manual_estimates_file().stat().st_mtime
    if manual_estimates_file().exists()
    else None
)
manual_stores, manual_themes, manual_metadata = read_manual_data(manual_mtime)

month_start, month_end = current_month_bounds(TODAY)
selected_regions: list[str] = []
selected_stores: list[str] = []
selected_themes: list[str] = []
period = "오늘"
start_date = end_date = TODAY
scoped_status = store_status
scoped_catalog = catalog

st.markdown('<div class="filter-shell">', unsafe_allow_html=True)
with st.expander("검색 / 필터", expanded=False):
    st.caption("지역, 매장, 테마, 기간을 검색해서 모든 표와 차트에 바로 적용합니다.")
    filter_row_1 = st.columns([1, 1.2, 1.2], gap="medium")
    with filter_row_1[0]:
        selected_regions = st.multiselect(
            "지역",
            sorted(store_status["region"].dropna().unique()),
            placeholder="전체 지역",
        )
    scoped_status = (
        store_status[store_status["region"].isin(selected_regions)]
        if selected_regions
        else store_status
    )
    with filter_row_1[1]:
        selected_stores = st.multiselect(
            "매장",
            sorted(scoped_status["store_name"].dropna().unique()),
            placeholder="전체 매장",
        )
    scoped_catalog = (
        catalog[catalog["region"].isin(selected_regions)]
        if selected_regions
        else catalog
    )
    if selected_stores:
        scoped_catalog = scoped_catalog[
            scoped_catalog["store_name"].isin(selected_stores)
        ]
    with filter_row_1[2]:
        selected_themes = st.multiselect(
            "테마",
            sorted(scoped_catalog["theme_name"].dropna().unique()),
            placeholder="전체 테마",
        )

    filter_row_2 = st.columns([1.2, 1.8], gap="medium")
    with filter_row_2[0]:
        period = st.radio(
            "기간",
            ["오늘", "오늘부터 7일", "이번 달 남은 기간", "직접 선택"],
            horizontal=True,
        )
    with filter_row_2[1]:
        if period == "오늘":
            start_date = end_date = TODAY
            st.caption(f"조회 기간: {start_date}")
        elif period == "오늘부터 7일":
            start_date, end_date = TODAY, TODAY + timedelta(days=6)
            st.caption(f"조회 기간: {start_date} ~ {end_date}")
        elif period == "이번 달 남은 기간":
            start_date, end_date = TODAY, month_end
            st.caption(f"조회 기간: {start_date} ~ {end_date}")
        else:
            minimum = min(data["date"]) if not data.empty else TODAY
            maximum = max(max(data["date"]), TODAY) if not data.empty else TODAY
            selected_dates = st.date_input(
                "날짜 범위",
                value=(TODAY, maximum),
                min_value=minimum,
                max_value=maximum,
            )
            if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
                start_date, end_date = selected_dates
            else:
                start_date = end_date = selected_dates
st.markdown("</div>", unsafe_allow_html=True)

filter_summary: list[str] = []
if selected_regions:
    filter_summary.append(f"지역 {len(selected_regions)}개")
if selected_stores:
    filter_summary.append(f"매장 {len(selected_stores)}개")
if selected_themes:
    filter_summary.append(f"테마 {len(selected_themes)}개")
if period != "오늘":
    filter_summary.append(
        f"{start_date} ~ {end_date}" if start_date != end_date else str(start_date)
    )
if filter_summary:
    st.markdown(
        """
        <div class="filter-summary">
          <span class="filter-chip">적용 중인 필터</span>
          {}
        </div>
        """.format(
            "".join(
                f'<span class="filter-chip">{escape(str(item))}</span>'
                for item in filter_summary
            )
        ),
        unsafe_allow_html=True,
    )
render_refresh_notice()
render_crawl_job_status()

filtered = filter_slots(
    data,
    regions=selected_regions,
    stores=selected_stores,
    themes=selected_themes,
    start_date=start_date,
    end_date=end_date,
)
projection_source = filter_slots(
    data,
    regions=selected_regions,
    stores=selected_stores,
    themes=selected_themes,
    start_date=TODAY,
    end_date=month_end,
)
filtered_status = scoped_status
if selected_stores:
    filtered_status = filtered_status[
        filtered_status["store_name"].isin(selected_stores)
    ]

selected_store_ids = set(filtered_status["store_id"].astype(str))
manual_store_scope = manual_stores[
    manual_stores["store_id"].isin(selected_store_ids)
].copy()
manual_theme_scope = manual_themes[
    manual_themes["store_id"].isin(selected_store_ids)
].copy()
if selected_themes:
    manual_theme_scope = manual_theme_scope[
        manual_theme_scope["theme_name"].isin(selected_themes)
    ]
    manual_industry_source = (
        manual_theme_scope.groupby(
            ["store_id", "store_name", "region"],
            as_index=False,
        )
        .agg(
            booking_rate_min=("booking_rate_min", "min"),
            booking_rate_max=("booking_rate_max", "max"),
            daily_revenue_min=("daily_revenue_min", "sum"),
            daily_revenue_max=("daily_revenue_max", "sum"),
            monthly_revenue_min=("monthly_revenue_min", "sum"),
            monthly_revenue_max=("monthly_revenue_max", "sum"),
        )
    )
else:
    manual_industry_source = manual_store_scope
active_selected_ids = set(
    filtered_status.loc[
        ~filtered_status["adapter_type"].isin(NON_CRAWLING_ADAPTERS),
        "store_id",
    ].astype(str)
)
active_total = int(
    (~store_status["adapter_type"].isin(NON_CRAWLING_ADAPTERS)).sum()
)

refresh_col, week_col, scope_col, info_col = st.columns([1.1, 1.1, 1, 2.3])
with refresh_col:
    refresh_label = (
        "선택 매장 오늘 예약 업데이트"
        if selected_regions or selected_stores
        else "오늘 예약 현황 업데이트"
    )
    if DEMO_MODE:
        st.button(
            "데모 데이터 보기",
            type="primary",
            width="stretch",
            disabled=True,
            help="Streamlit Cloud 데모에서는 온라인 수집을 실행하지 않고 저장된 데모 DB만 읽습니다.",
        )
    elif st.button(
        refresh_label,
        type="primary",
        width="stretch",
        help=(
            f"같은 사이트의 매장은 {CRAWL_DELAY_MIN_SECONDS}~{CRAWL_DELAY_MAX_SECONDS}초 간격으로 "
            f"순차 확인하고, 서로 다른 사이트는 최대 {CRAWL_MAX_PARALLEL_ORIGINS}개까지 병렬 확인합니다."
        ),
    ):
        target_ids = active_selected_ids if selected_regions or selected_stores else None
        run_refresh_action("오늘 예약 현황 업데이트", target_ids, days=1)
with week_col:
    week_label = (
        "선택 매장 7일 예약 업데이트"
        if selected_regions or selected_stores
        else "7일 예약 현황 업데이트"
    )
    if DEMO_MODE:
        st.button(
            "온라인 수집 꺼짐",
            width="stretch",
            disabled=True,
            help="데모 링크에서는 Playwright와 자동 수집을 실행하지 않습니다.",
        )
    elif st.button(
        week_label,
        width="stretch",
        help=(
            "매장당 브라우저를 한 번만 열어 오늘부터 7일을 연속 확인합니다. "
            f"느린 사이트는 {CRAWL_NAVIGATION_TIMEOUT_MS // 1000}초 안에 다음 작업으로 넘기고 "
            f"서로 다른 사이트는 최대 {CRAWL_MAX_PARALLEL_ORIGINS}개까지 병렬 확인합니다."
        ),
    ):
        target_ids = active_selected_ids if selected_regions or selected_stores else None
        run_refresh_action("7일 예약 현황 업데이트", target_ids, days=7)
with scope_col:
    st.metric(
        "예약 확인 대상",
        f"{len(active_selected_ids):,}곳"
        if selected_regions or selected_stores
        else f"{active_total:,}곳",
        help="접근 제한 매장은 우회하지 않습니다.",
    )
with info_col:
    crawl_mode_label = (
        "서버 안전 모드"
        if CLOUD_SAFE_CRAWL
        else "고속 수집 모드"
    )
    if active_view == "home":
        st.markdown(
            f"""
            <div class="home-status-card">
              <b>{start_date} ~ {end_date}</b>
              <span>자동 {len(active_selected_ids):,}곳 · 수동 {manual_store_scope['store_id'].nunique():,}곳 · 최신 {escape(freshness_text(filtered))}</span>
              <span>{crawl_mode_label} · 병렬 {CRAWL_MAX_PARALLEL_ORIGINS}개 · 제한 {CRAWL_NAVIGATION_TIMEOUT_MS // 1000}초</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="summary-box">
            <b>조회 기간</b> {start_date} ~ {end_date}<br>
            <b>매장별 공개 예약표 확인 시각</b> {freshness_text(filtered)}<br>
            <b>수집 모드</b> {crawl_mode_label} · 병렬 {CRAWL_MAX_PARALLEL_ORIGINS}개 · 제한 {CRAWL_NAVIGATION_TIMEOUT_MS // 1000}초<br>
            타임 시작 전에 예약 완료로 확인된 경우만 예약으로 집계합니다.
            이미 지난 뒤 처음 확인한 타임은 확인 불가로 제외합니다.
            예약 1건당 평균 2.7명을 기준으로 인원별 총액표를 보간합니다.
            최소 인원이 3명이면 3인 금액, 회차 고정가는 고정 총액을 적용합니다.
            실제 결제, 현장 취소, 노쇼, 할인은 알 수 없어 매출은 추정치입니다.
            </div>
            """,
            unsafe_allow_html=True,
        )

registered_stores = int(filtered_status["store_id"].nunique())
collected_stores = int(filtered["store_id"].nunique()) if not filtered.empty else 0
all_slots = int(len(filtered))
measured_slots = int(filtered["status"].isin(MEASURABLE_STATUSES).sum())
reserved_slots = int(filtered["status"].eq("reserved").sum()) if all_slots else 0
available_slots = int(filtered["status"].eq("available").sum()) if all_slots else 0
unknown_slots = int(filtered["status"].isin({"closed", "unknown"}).sum())
period_revenue = float(filtered["expected_revenue"].sum()) if all_slots else 0.0
projection = project_monthly_revenue(
    projection_source, TODAY.year, TODAY.month
)
observed_store_projection = store_monthly_projections(
    projection_source, TODAY.year, TODAY.month
)
projection_store_base = filtered_status.loc[
    ~filtered_status["adapter_type"].isin(NON_CRAWLING_ADAPTERS),
    ["store_id", "store_name", "region"],
].drop_duplicates()
monthly_store_projection = projection_store_base.merge(
    observed_store_projection,
    on=["store_id", "store_name", "region"],
    how="left",
)
for column in [
    "observed_days",
    "observed_weekdays",
    "coverage",
    "total_slots",
    "reserved_slots",
    "booking_rate",
]:
    monthly_store_projection[column] = monthly_store_projection[column].fillna(0)
monthly_store_projection["observed_weekday_names"] = (
    monthly_store_projection["observed_weekday_names"].fillna("-")
)
monthly_store_projection["confidence"] = monthly_store_projection[
    "confidence"
].fillna("수집 데이터 없음")
monthly_store_projection = monthly_store_projection.sort_values(
    ["monthly_revenue", "booking_rate"],
    ascending=[False, False],
    na_position="last",
)
projected_with_data = monthly_store_projection[
    monthly_store_projection["observed_days"].gt(0)
    & monthly_store_projection["monthly_revenue"].notna()
]
days_in_current_month = (month_end - month_start).days + 1
industry_store_projection = combine_store_revenue_estimates(
    projected_with_data,
    manual_industry_source,
    days_in_current_month,
)
industry_store_count = int(len(industry_store_projection))
automatic_industry_count = int(
    industry_store_projection["estimate_source"].eq("자동 수집").sum()
)
manual_industry_count = int(
    industry_store_projection["estimate_source"].eq("수동 관측").sum()
)
industry_monthly_min = float(
    industry_store_projection["monthly_revenue_min"].sum()
)
industry_monthly_mid = float(
    industry_store_projection["monthly_revenue_mid"].sum()
)
industry_monthly_max = float(
    industry_store_projection["monthly_revenue_max"].sum()
)
average_store_monthly_min = (
    industry_monthly_min / industry_store_count if industry_store_count else 0.0
)
average_store_monthly_mid = (
    industry_monthly_mid / industry_store_count if industry_store_count else 0.0
)
average_store_monthly_max = (
    industry_monthly_max / industry_store_count if industry_store_count else 0.0
)
average_store_daily_min = average_store_monthly_min / days_in_current_month
average_store_daily_max = average_store_monthly_max / days_in_current_month
genre_revenue = genre_monthly_summary(
    projection_source, TODAY.year, TODAY.month
)
confirmed_reserved = filtered[
    filtered["finalized_status"].eq("reserved")
]
observed_days = int(projection["observed_days"])
complete_week_stores = (
    int(monthly_store_projection["observed_weekdays"].eq(7).sum())
    if not monthly_store_projection.empty
    else 0
)
projected_store_count = int(len(projection_store_base))
growth_scope = filter_slots(
    data,
    regions=selected_regions,
    stores=selected_stores,
    themes=selected_themes,
)
growth_reference = end_date if isinstance(end_date, date) else TODAY
growth_trends = store_growth_trends(growth_scope, growth_reference)
price_strategy = price_strategy_matrix(filtered, scoped_catalog)
efficiency_table = store_efficiency(filtered)
snapshot_scope_label = "전체"
if filter_summary:
    snapshot_scope_label = " · ".join(filter_summary)
snapshot_payload = {
    "period": f"{start_date} ~ {end_date}",
    "industry_monthly_min": industry_monthly_min,
    "industry_monthly_mid": industry_monthly_mid,
    "industry_monthly_max": industry_monthly_max,
    "automatic_store_count": automatic_industry_count,
    "manual_store_count": manual_industry_count,
    "source": "public_booking_monitor",
}
snapshot_database = None if DEMO_MODE else Database(DB_PATH)
if snapshot_database is not None and not filter_summary and period == "오늘":
    snapshot_database.save_metric_snapshot(
        snapshot_date=TODAY,
        scope_label="전체",
        store_count=industry_store_count,
        theme_count=int(scoped_catalog["theme_name"].dropna().nunique()),
        measured_slots=measured_slots,
        reserved_slots=reserved_slots,
        booking_rate=booking_rate(filtered),
        period_revenue=period_revenue,
        projected_monthly_revenue=industry_monthly_mid,
        average_store_monthly_revenue=average_store_monthly_mid,
        payload=snapshot_payload,
        replace=False,
    )
snapshot_history = (
    read_metric_snapshots_readonly(DB_PATH)
    if DEMO_MODE
    else pd.DataFrame(
        [dict(row) for row in snapshot_database.load_metric_snapshots()]
    )
)
if not snapshot_history.empty:
    snapshot_history["created_at"] = pd.to_datetime(
        snapshot_history["created_at"], utc=True, errors="coerce"
    ).dt.tz_convert(KST)
if active_view == "home":
    st.markdown(
        f"""
        <div class="home-kpi-grid">
          <div class="snapshot-card">
            <div class="snapshot-label">예약률</div>
            <div class="snapshot-value">{escape(rate_label(filtered))}</div>
            <div class="snapshot-detail">공개 예약표 기준</div>
          </div>
          <div class="snapshot-card coral">
            <div class="snapshot-label">{TODAY.month}월 업계 추정 매출</div>
            <div class="snapshot-value">{escape(compact_won(industry_monthly_mid))}</div>
            <div class="snapshot-detail">자동+수동 합산</div>
          </div>
          <div class="snapshot-card violet">
            <div class="snapshot-label">매장당 월평균</div>
            <div class="snapshot-value">{escape(compact_won(average_store_monthly_mid))}</div>
            <div class="snapshot-detail">{industry_store_count:,}개 매장</div>
          </div>
          <div class="snapshot-card">
            <div class="snapshot-label">확인 타임</div>
            <div class="snapshot-value">{reserved_slots:,}/{measured_slots:,}</div>
            <div class="snapshot-detail">예약 / 확인 가능</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f"""
        <div class="snapshot-grid">
          <div class="snapshot-card">
            <div class="snapshot-label">공개 예약률</div>
            <div class="snapshot-value">{escape(rate_label(filtered))}</div>
            <div class="snapshot-detail">타임 시작 전 확인된 슬롯만 분모에 사용</div>
          </div>
          <div class="snapshot-card violet">
            <div class="snapshot-label">예약 / 확인 가능 타임</div>
            <div class="snapshot-value">{reserved_slots:,} / {measured_slots:,}</div>
            <div class="snapshot-detail">예약 가능 {available_slots:,}타임 · 확인 불가 {unknown_slots:,}타임</div>
          </div>
          <div class="snapshot-card">
            <div class="snapshot-label">선택 기간 추정매출</div>
            <div class="snapshot-value">{escape(won(period_revenue))}</div>
            <div class="snapshot-detail">가격이 확인된 예약 타임 합산</div>
          </div>
          <div class="snapshot-card coral">
            <div class="snapshot-label">{TODAY.month}월 업계 추정 매출</div>
            <div class="snapshot-value">{escape(compact_won(industry_monthly_mid))}</div>
            <div class="snapshot-detail">자동 {automatic_industry_count}곳 + 수동 {manual_industry_count}곳</div>
          </div>
          <div class="snapshot-card violet">
            <div class="snapshot-label">매장당 월평균</div>
            <div class="snapshot-value">{escape(compact_won(average_store_monthly_mid))}</div>
            <div class="snapshot-detail">총 {industry_store_count:,}개 매장 기준 추정치</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

priced_frame = filtered[filtered["booking_value_estimate"].gt(0)]
average_person_price = (
    float(priced_frame["per_person_estimate"].mean())
    if not priced_frame.empty
    else 0.0
)
average_booking_value = estimated_ticket_value(filtered)
unpriced_reserved_slots = int(
    (
        filtered["status"].eq("reserved")
        & filtered["booking_value_estimate"].le(0)
    ).sum()
)

if active_view != "home":
    st.caption("핵심 숫자만 먼저 보여줍니다. 가격 반영률과 계산 기준은 아래 접힌 영역에 정리했습니다.")
    with st.expander("데이터 품질 · 계산 기준 보기", expanded=False):
        coverage_1, coverage_2, coverage_3, coverage_4, coverage_5 = st.columns(5)
        coverage_1.metric(
            "예약표 확인 매장",
            f"{collected_stores:,} / {registered_stores:,}",
        )
        coverage_2.metric("슬롯 가격 반영률", f"{price_coverage(filtered):.1f}%")
        coverage_3.metric(
            "가격 미반영 예약",
            f"{unpriced_reserved_slots:,}타임",
            help="예약 완료로 확인됐지만 공식 가격이 없어 매출 합계에서 제외된 타임입니다.",
        )
        coverage_4.metric(
            "평균 추정 인당 부담",
            won(average_person_price),
            help=(
                "예약 1건 추정액을 실제 계산에 사용한 인원으로 나눈 참고값입니다. "
                "기본은 2.7명이며 최소 3인 테마는 3명으로 계산합니다."
            ),
        )
        coverage_5.metric(
            "확인 불가 타임",
            f"{unknown_slots:,}" if collected_stores else "-",
            help="이미 지난 뒤 처음 확인했거나 상태를 판별할 수 없는 타임입니다.",
        )

        average_1, average_2, average_3, average_4 = st.columns(4)
        average_1.metric(
            "추정 예약 1건 매출",
            won(average_booking_value),
            help="평균 인원, 최소 인원, 인원별 가격표와 회차 고정가를 반영한 추정액입니다.",
        )
        average_2.metric(
            "매장당 평균 월매출",
            compact_won(average_store_monthly_mid),
            help=(
                f"자동 {automatic_industry_count}곳과 수동 관측 "
                f"{manual_industry_count}곳, 총 {industry_store_count}곳의 평균입니다."
            ),
        )
        average_3.metric(
            "매장당 평균 일매출",
            compact_won_range(average_store_daily_min, average_store_daily_max),
            help="통합 매장당 평균 월매출을 이번 달 일수로 나눈 값입니다.",
        )
        average_4.metric(
            "지나간 확정 예약 기록",
            f"{len(confirmed_reserved):,}타임",
            help="타임 시작 전에 실제로 예약됨을 관측한 뒤 시간이 지난 기록만 셉니다.",
        )

    st.caption(
        "업계 추정 매출과 매장 순위에는 키이스케이프 지점도 포함됩니다. "
        "자동 수집값과 수동 관측 범위는 출처를 구분해 표시합니다."
    )

if active_view == "revenue":
    st.subheader("매장별 월 예상매출")
    st.caption(
        "날짜별 합산표 대신 매장별 돈을 먼저 보여줍니다. 월 예상매출은 공개 예약표에서 "
        "확인된 예약 타임과 가격표, 평균 2.7명 규칙을 반영한 추정치입니다."
    )
    if industry_store_projection.empty:
        st.info("매장별 월 예상매출 데이터가 없습니다.")
    else:
        store_revenue_table = industry_store_projection.copy()
        store_revenue_table["booking_rate_mid"] = (
            store_revenue_table["booking_rate_min"]
            + store_revenue_table["booking_rate_max"]
        ) / 2

        st.markdown(
            f"""
            <div class="summary-box">
            <b>매장당 평균 월 예상매출: {compact_won(average_store_monthly_mid)}</b><br>
            자동 수집 {automatic_industry_count}곳과 수동 관측
            {manual_industry_count}곳, 총 {industry_store_count}곳 기준입니다.<br>
            월~일을 모두 확인한 매장 {complete_week_stores}/{projected_store_count}곳이며,
            빠진 요일은 해당 매장의 관측 평균으로 보완해 신뢰도에 표시합니다.<br>
            모든 금액은 실제 결제액이 아니라 공개 예약 상태 기반 추정치입니다.
            </div>
            """,
            unsafe_allow_html=True,
        )

        left, right = st.columns([1.25, 1])
        with left:
            st.markdown("#### 월 예상매출 상위 매장")
            st.altair_chart(
                modern_bar_chart(
                    store_revenue_table.head(20),
                    "store_name",
                    "monthly_revenue_mid",
                    value_title="월 예상매출(원)",
                ),
                width="stretch",
            )
        with right:
            st.markdown("#### 매장별 요약")
            st.dataframe(
                store_revenue_table[
                    [
                        "store_name",
                        "region",
                        "estimate_source",
                        "booking_rate_mid",
                        "monthly_revenue_mid",
                        "monthly_revenue_min",
                        "monthly_revenue_max",
                        "observed_days",
                        "confidence",
                    ]
                ].head(50),
                hide_index=True,
                width="stretch",
                column_config={
                    "store_name": "매장",
                    "region": "지역",
                    "estimate_source": "출처",
                    "booking_rate_mid": st.column_config.ProgressColumn(
                        "예약률", min_value=0, max_value=100, format="%.1f%%"
                    ),
                    "monthly_revenue_mid": st.column_config.NumberColumn(
                        "월 예상", format="%,d원"
                    ),
                    "monthly_revenue_min": st.column_config.NumberColumn(
                        "하한", format="%,d원"
                    ),
                    "monthly_revenue_max": st.column_config.NumberColumn(
                        "상한", format="%,d원"
                    ),
                    "observed_days": "관측일",
                    "confidence": "신뢰도",
                },
            )

        st.subheader("지역별 매장당 평균 월 예상매출")
        region_monthly = (
            store_revenue_table
            .groupby("region", as_index=False)
            .agg(
                store_count=("store_id", "nunique"),
                average_monthly_revenue=("monthly_revenue_mid", "mean"),
            )
            .sort_values("average_monthly_revenue", ascending=False)
            .head(15)
        )
        if region_monthly.empty:
            st.info("지역별 월 예상매출 데이터가 없습니다.")
        else:
            region_left, region_right = st.columns([1.1, 1])
            with region_left:
                st.altair_chart(
                    modern_bar_chart(
                        region_monthly,
                        "region",
                        "average_monthly_revenue",
                        value_title="매장당 월평균(원)",
                        color="#6557c8",
                    ),
                    width="stretch",
                )
            with region_right:
                st.dataframe(
                    region_monthly,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "region": "지역",
                        "store_count": "매장 수",
                        "average_monthly_revenue": st.column_config.NumberColumn(
                            "매장당 월평균", format="%,d원"
                        ),
                    },
                )

        st.subheader("장르별 평균 예상매출")
        st.caption(
            "장르별 매장 월 예상매출의 평균입니다. 장르가 확인되지 않은 테마는 "
            "미분류로 따로 표시하며, 모든 금액은 공개 예약표 기반 추정치입니다."
        )
        if genre_revenue.empty:
            st.info("장르별 매출을 계산할 데이터가 없습니다.")
        else:
            genre_left, genre_right = st.columns([1, 1.35])
            with genre_left:
                st.altair_chart(
                    modern_bar_chart(
                        genre_revenue.head(15),
                        "genre",
                        "average_monthly_revenue",
                        value_title="매장당 월평균(원)",
                        color="#f06b4f",
                    ),
                    width="stretch",
                )
            with genre_right:
                st.dataframe(
                    genre_revenue[
                        [
                            "genre",
                            "store_count",
                            "theme_count",
                            "booking_rate",
                            "average_daily_revenue",
                            "average_monthly_revenue",
                        ]
                    ].head(50),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "genre": "장르",
                        "store_count": "관측 매장",
                        "theme_count": "테마 수",
                        "booking_rate": st.column_config.ProgressColumn(
                            "예약률", min_value=0, max_value=100, format="%.1f%%"
                        ),
                        "average_daily_revenue": st.column_config.NumberColumn(
                            "매장당 일평균", format="%,d원"
                        ),
                        "average_monthly_revenue": st.column_config.NumberColumn(
                            "매장당 월평균", format="%,d원"
                        ),
                    },
                )

if active_view == "investor":
    st.subheader("투자자용 시장 인사이트")
    st.caption(
        "성장 추세, 가격 전략, 회차 효율, 상권 반경, 스냅샷을 한 화면에서 봅니다. "
        "모든 금액은 공개 예약표와 수동 관측을 섞되 출처를 분리한 추정치입니다."
    )
    report_metrics = {
        "업계 추정 월매출": compact_won(industry_monthly_mid),
        "매장당 월평균": compact_won(average_store_monthly_mid),
        "공개 예약률": rate_label(filtered),
        "예약 확인 매장": f"{collected_stores:,}/{registered_stores:,}곳",
        "가격 반영률": f"{price_coverage(filtered):.1f}%",
        "추정 예약 1건": won(average_booking_value),
    }
    metric_cols = st.columns(6)
    for column, (label, value) in zip(metric_cols, report_metrics.items()):
        column.metric(label, value)

    st.markdown("#### 투자자 리포트 다운로드")
    top_report_stores = industry_store_projection.head(20).copy()
    radius_for_report = market_radius_summary(
        industry_store_projection,
        filtered_status,
        radius_meters=700,
    ).head(20)
    report_html = build_investor_report_html(
        metrics=report_metrics,
        top_stores=top_report_stores,
        growth=growth_trends.head(20),
        price_strategy=price_strategy.head(30),
        efficiency=efficiency_table.head(20),
        radius=radius_for_report,
        snapshots=snapshot_history.head(30),
    )
    report_pdf = build_investor_report_pdf(
        metrics=report_metrics,
        top_stores=top_report_stores,
        growth=growth_trends.head(20),
        price_strategy=price_strategy.head(30),
        efficiency=efficiency_table.head(20),
        radius=radius_for_report,
        snapshots=snapshot_history.head(30),
    )
    download_cols = st.columns([1, 1, 2])
    with download_cols[0]:
        st.download_button(
            "인쇄용 HTML 리포트",
            data=report_html,
            file_name=f"lumitrack-investor-report-{TODAY}.html",
            mime="text/html",
            width="stretch",
        )
    with download_cols[1]:
        if report_pdf:
            st.download_button(
                "PDF 리포트",
                data=report_pdf,
                file_name=f"lumitrack-investor-report-{TODAY}.pdf",
                mime="application/pdf",
                width="stretch",
            )
        else:
            st.button(
                "PDF 엔진 없음",
                disabled=True,
                width="stretch",
                help="reportlab 설치 시 앱에서 바로 PDF를 생성합니다. 현재는 HTML을 브라우저에서 PDF로 저장할 수 있습니다.",
            )
    with download_cols[2]:
        if DEMO_MODE:
            st.button(
                "데모는 읽기 전용",
                width="stretch",
                disabled=True,
                help="Streamlit Cloud 데모에서는 SQLite 파일을 수정하지 않습니다.",
            )
        elif st.button("현재 필터 스냅샷 저장", width="stretch"):
            snapshot_database.save_metric_snapshot(
                snapshot_date=TODAY,
                scope_label=snapshot_scope_label,
                store_count=industry_store_count,
                theme_count=int(scoped_catalog["theme_name"].dropna().nunique()),
                measured_slots=measured_slots,
                reserved_slots=reserved_slots,
                booking_rate=booking_rate(filtered),
                period_revenue=period_revenue,
                projected_monthly_revenue=industry_monthly_mid,
                average_store_monthly_revenue=average_store_monthly_mid,
                payload=snapshot_payload,
                replace=True,
            )
            st.success("현재 화면 기준 스냅샷을 저장했습니다.")
            st.rerun()

    st.divider()
    growth_left, growth_right = st.columns([1.25, 1])
    with growth_left:
        st.markdown("#### 1. 매장 성장 추세")
        st.caption("선택 기준일의 최근 7일과 직전 7일을 비교합니다.")
        if growth_trends.empty:
            st.info("성장 추세를 비교할 2주치 데이터가 아직 없습니다.")
        else:
            st.altair_chart(
                modern_bar_chart(
                    growth_trends.head(15),
                    "store_name",
                    "revenue_delta",
                    value_title="최근 7일 매출 증감(원)",
                    color="#3182f6",
                ),
                width="stretch",
            )
    with growth_right:
        if not growth_trends.empty:
            st.dataframe(
                growth_trends[
                    [
                        "store_name",
                        "current_revenue",
                        "previous_revenue",
                        "revenue_delta_pct",
                        "booking_rate_delta",
                        "trend_label",
                    ]
                ].head(20),
                hide_index=True,
                width="stretch",
                height=390,
                column_config={
                    "store_name": "매장",
                    "current_revenue": st.column_config.NumberColumn(
                        "최근 7일", format="%,d원"
                    ),
                    "previous_revenue": st.column_config.NumberColumn(
                        "이전 7일", format="%,d원"
                    ),
                    "revenue_delta_pct": st.column_config.NumberColumn(
                        "매출 증감률", format="%+.1f%%"
                    ),
                    "booking_rate_delta": st.column_config.NumberColumn(
                        "예약률 증감(%p)", format="%+.1f"
                    ),
                    "trend_label": "판정",
                },
            )

    st.divider()
    price_left, price_right = st.columns([1, 1.35])
    with price_left:
        st.markdown("#### 2. 가격 전략 분석")
        if price_strategy.empty:
            st.info("가격과 예약률이 모두 있는 테마가 부족합니다.")
        else:
            strategy_summary = (
                price_strategy.groupby("strategy", as_index=False)
                .agg(
                    theme_count=("theme_name", "count"),
                    average_booking_rate=("booking_rate", "mean"),
                    average_person_price=("per_person_estimate", "mean"),
                )
                .sort_values("theme_count", ascending=False)
            )
            st.altair_chart(
                modern_bar_chart(
                    strategy_summary,
                    "strategy",
                    "theme_count",
                    value_title="테마 수",
                    color="#6557c8",
                ),
                width="stretch",
            )
            st.caption(
                "고가격/저가격은 현재 관측 테마의 추정 인당가 중앙값, "
                "고예약률은 60% 또는 중앙값 중 높은 기준으로 나눕니다."
            )
    with price_right:
        if not price_strategy.empty:
            st.dataframe(
                price_strategy[
                    [
                        "strategy",
                        "store_name",
                        "theme_name",
                        "genre",
                        "booking_rate",
                        "per_person_estimate",
                        "booking_value_estimate",
                        "estimated_revenue",
                    ]
                ].head(80),
                hide_index=True,
                width="stretch",
                height=430,
                column_config={
                    "strategy": "전략",
                    "store_name": "매장",
                    "theme_name": "테마",
                    "genre": "장르",
                    "booking_rate": st.column_config.ProgressColumn(
                        "예약률", min_value=0, max_value=100, format="%.1f%%"
                    ),
                    "per_person_estimate": st.column_config.NumberColumn(
                        "추정 인당", format="%,d원"
                    ),
                    "booking_value_estimate": st.column_config.NumberColumn(
                        "예약 1건", format="%,d원"
                    ),
                    "estimated_revenue": st.column_config.NumberColumn(
                        "기간 매출", format="%,d원"
                    ),
                },
            )

    st.divider()
    efficiency_left, efficiency_right = st.columns([1.2, 1])
    with efficiency_left:
        st.markdown("#### 3. 회차·시간당 매출 효율")
        if efficiency_table.empty:
            st.info("효율을 계산할 공개 예약 타임이 없습니다.")
        else:
            st.dataframe(
                efficiency_table[
                    [
                        "store_name",
                        "theme_count",
                        "measured_slots",
                        "reserved_slots",
                        "booking_rate",
                        "revenue_per_measured_slot",
                        "revenue_per_operating_hour",
                        "revenue_per_theme",
                        "estimated_revenue",
                    ]
                ].head(80),
                hide_index=True,
                width="stretch",
                height=430,
                column_config={
                    "store_name": "매장",
                    "theme_count": "테마 수",
                    "measured_slots": "공개 회차",
                    "reserved_slots": "예약 회차",
                    "booking_rate": st.column_config.ProgressColumn(
                        "예약률", min_value=0, max_value=100, format="%.1f%%"
                    ),
                    "revenue_per_measured_slot": st.column_config.NumberColumn(
                        "공개 회차당", format="%,d원"
                    ),
                    "revenue_per_operating_hour": st.column_config.NumberColumn(
                        "운영시간당", format="%,d원"
                    ),
                    "revenue_per_theme": st.column_config.NumberColumn(
                        "테마당", format="%,d원"
                    ),
                    "estimated_revenue": st.column_config.NumberColumn(
                        "기간 매출", format="%,d원"
                    ),
                },
            )
    with efficiency_right:
        if not efficiency_table.empty:
            st.altair_chart(
                modern_bar_chart(
                    efficiency_table.head(15),
                    "store_name",
                    "revenue_per_operating_hour",
                    value_title="운영시간당 추정매출(원)",
                    color="#147d72",
                ),
                width="stretch",
            )

    st.divider()
    st.markdown("#### 4. 상권 반경 분석")
    radius_meters = st.radio(
        "반경",
        [500, 700, 1000, 1500],
        index=1,
        format_func=lambda value: f"{value:,}m",
        horizontal=True,
    )
    radius_summary = market_radius_summary(
        industry_store_projection,
        filtered_status,
        radius_meters=radius_meters,
    )
    if radius_summary.empty:
        st.info("정확한 좌표와 월매출이 있는 매장이 부족해 상권 반경을 계산할 수 없습니다.")
    else:
        radius_left, radius_right = st.columns([1, 1.2])
        with radius_left:
            st.altair_chart(
                modern_bar_chart(
                    radius_summary.head(15),
                    "anchor_store_name",
                    "monthly_revenue_sum",
                    value_title=f"반경 {radius_meters:,}m 월매출 합계(원)",
                    color="#f06b4f",
                ),
                width="stretch",
            )
        with radius_right:
            st.dataframe(
                radius_summary[
                    [
                        "anchor_store_name",
                        "region",
                        "nearby_store_count",
                        "revenue_store_count",
                        "monthly_revenue_sum",
                        "average_store_monthly_revenue",
                        "top_store_name",
                        "competition_density",
                    ]
                ].head(80),
                hide_index=True,
                width="stretch",
                height=430,
                column_config={
                    "anchor_store_name": "중심 매장",
                    "region": "지역",
                    "nearby_store_count": "반경 내 매장",
                    "revenue_store_count": "매출 계산 매장",
                    "monthly_revenue_sum": st.column_config.NumberColumn(
                        "상권 월매출", format="%,d원"
                    ),
                    "average_store_monthly_revenue": st.column_config.NumberColumn(
                        "매장 평균", format="%,d원"
                    ),
                    "top_store_name": "상권 1위",
                    "competition_density": st.column_config.NumberColumn(
                        "밀도/㎢", format="%.1f"
                    ),
                },
            )

    st.divider()
    st.markdown("#### 5. 데이터 스냅샷")
    st.caption(
        "앱은 전체 기준 오늘 스냅샷을 하루 한 번 자동 보관합니다. "
        "필터를 적용한 상태는 위 버튼으로 별도 저장할 수 있습니다."
    )
    if snapshot_history.empty:
        st.info("저장된 스냅샷이 아직 없습니다.")
    else:
        st.dataframe(
            snapshot_history[
                [
                    "snapshot_date",
                    "scope_label",
                    "store_count",
                    "theme_count",
                    "booking_rate",
                    "projected_monthly_revenue",
                    "average_store_monthly_revenue",
                    "created_at",
                ]
            ],
            hide_index=True,
            width="stretch",
            height=360,
            column_config={
                "snapshot_date": "기준일",
                "scope_label": "범위",
                "store_count": "매장 수",
                "theme_count": "테마 수",
                "booking_rate": st.column_config.ProgressColumn(
                    "예약률", min_value=0, max_value=100, format="%.1f%%"
                ),
                "projected_monthly_revenue": st.column_config.NumberColumn(
                    "월 예상", format="%,d원"
                ),
                "average_store_monthly_revenue": st.column_config.NumberColumn(
                    "매장당 월평균", format="%,d원"
                ),
                "created_at": "저장 시각",
            },
        )

if active_view == "map":
    st.subheader("매장 위치와 월 예상매출")
    st.caption(
        "지도에는 주소와 좌표가 확인된 매장만 핀으로 표시합니다. 단일 주소가 아직 확정되지 않은 매장은 "
        "지역 중심에 임시로 찍지 않고 아래 '주소 확인 필요' 목록에 따로 남깁니다. "
        "전체 지역에서는 서울권 매장을 먼저 확대해 보여줍니다."
    )
    map_status_frame = filtered_status.copy()
    if selected_themes:
        theme_store_ids = set(
            scoped_catalog.loc[
                scoped_catalog["theme_name"].isin(selected_themes),
                "store_id",
            ].astype(str)
        )
        map_status_frame = map_status_frame[
            map_status_frame["store_id"].astype(str).isin(theme_store_ids)
        ]
    map_frame_all = build_store_map_frame(
        map_status_frame,
        industry_store_projection,
    )
    if map_frame_all.empty:
        st.info("지도에 표시할 매장이 없습니다.")
    else:
        map_frame = map_frame_all.dropna(subset=["latitude", "longitude"]).copy()
        missing_location = map_frame_all[
            map_frame_all["latitude"].isna() | map_frame_all["longitude"].isna()
        ].copy()
        mapped_count = int(len(map_frame))
        revenue_store_count = int(map_frame["monthly_revenue_mid"].gt(0).sum())
        map_monthly_mid = float(map_frame["monthly_revenue_mid"].sum())
        map_metric_1, map_metric_2, map_metric_3, map_metric_4 = st.columns(4)
        map_metric_1.metric(
            "정확 좌표 매장",
            f"{mapped_count:,}곳",
            delta=f"매출 계산 {revenue_store_count:,}곳",
        )
        map_metric_2.metric("주소 확인 필요", f"{len(missing_location):,}곳")
        map_metric_3.metric("월매출 합계", compact_won(map_monthly_mid))
        if map_frame.empty:
            map_metric_4.metric("월매출 1위", "-")
        else:
            top_map_row = map_frame.iloc[0]
            map_metric_4.metric(
                "월매출 1위",
                str(top_map_row["store_name"]),
                delta=compact_won(float(top_map_row["monthly_revenue_mid"])),
            )

        st.markdown(
            """
            <span class="legend-dot" style="background:#147d72"></span>자동 수집
            &nbsp;&nbsp;
            <span class="legend-dot" style="background:#6557c8"></span>수동 관측
            &nbsp;&nbsp;
            <span class="legend-dot" style="background:#969a96"></span>매출 데이터 없음
            """,
            unsafe_allow_html=True,
        )

        if map_frame.empty:
            st.warning(
                "현재 필터에 맞는 매장 중 주소/좌표가 확인된 매장이 없습니다. "
                "아래 주소 확인 필요 목록에서 확인이 필요한 매장을 볼 수 있습니다."
            )
        else:
            map_left, map_right = st.columns([1.65, 0.85])
            with map_left:
                components.html(
                    build_leaflet_map_html(map_frame),
                    height=650,
                    scrolling=False,
                )
            with map_right:
                st.markdown("#### 매출 상위 매장")
                top_cards = map_frame[map_frame["monthly_revenue_mid"].gt(0)].head(6)
                if top_cards.empty:
                    st.info("월 예상매출이 계산된 매장이 아직 없습니다.")
                else:
                    for _, row in top_cards.iterrows():
                        st.markdown(
                            f"""
                            <div class="map-card">
                              <img src="{escape(str(row['brand_logo_url']), quote=True)}">
                              <div>
                                <div class="map-card-title">
                                  {escape(str(row['store_name']))}
                                </div>
                                <div class="map-card-meta">
                                  {escape(str(row['region']))} ·
                                  {escape(str(row['estimate_source']))}<br>
                                  <span class="map-money">
                                    {escape(str(row['monthly_label']))}
                                  </span><br>
                                  예약률 {escape(str(row['booking_rate_label']))}
                                </div>
                              </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

        st.markdown("#### 좌표 확인 매장 요약")
        st.caption(
            "지도에 실제로 찍힌 매장만 보여줍니다. 긴 수집 메모와 외부 지도 링크는 아래 접힌 영역에서 확인합니다."
        )
        map_table = map_frame[
            [
                "store_name",
                "region",
                "address",
                "estimate_source",
                "collection_status",
                "booking_rate_mid",
                "monthly_revenue_mid",
                "confidence",
            ]
        ].copy()
        st.dataframe(
            map_table,
            hide_index=True,
            width="stretch",
            column_config={
                "store_name": "매장",
                "region": "지역",
                "address": "주소",
                "estimate_source": "출처",
                "collection_status": "상태",
                "booking_rate_mid": st.column_config.ProgressColumn(
                    "예약률", min_value=0, max_value=100, format="%.1f%%"
                ),
                "monthly_revenue_mid": st.column_config.NumberColumn(
                    "월 예상", format="%,d원"
                ),
                "confidence": "신뢰도",
            },
        )
        if not missing_location.empty:
            with st.expander("주소 확인 필요 매장 보기", expanded=False):
                missing_table = missing_location[
                    [
                        "store_name",
                        "region",
                        "collection_status",
                        "estimate_source",
                        "monthly_revenue_mid",
                        "collection_note",
                    ]
                ].copy()
                st.dataframe(
                    missing_table,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "store_name": "매장",
                        "region": "지역",
                        "collection_status": "상태",
                        "estimate_source": "출처",
                        "monthly_revenue_mid": st.column_config.NumberColumn(
                            "월 예상", format="%,d원"
                        ),
                        "collection_note": "수집 메모",
                    },
                )
        with st.expander("위치·수집 메모 보기", expanded=False):
            detail_table = map_frame[
                [
                    "store_name",
                    "region",
                    "coordinate_accuracy",
                    "address",
                    "collection_note",
                    "map_note",
                    "booking_url",
                ]
            ].copy()
            if not detail_table.empty:
                links = map_frame.apply(_map_links, axis=1)
                detail_table["naver_map"] = [link[0] for link in links]
                detail_table["google_map"] = [link[1] for link in links]
            st.dataframe(
                detail_table,
                hide_index=True,
                width="stretch",
                column_config={
                    "store_name": "매장",
                    "region": "지역",
                    "coordinate_accuracy": "좌표",
                    "address": "주소",
                    "collection_note": "수집 메모",
                    "map_note": "지도 메모",
                    "booking_url": st.column_config.LinkColumn("예약 URL"),
                    "naver_map": st.column_config.LinkColumn("네이버 지도"),
                    "google_map": st.column_config.LinkColumn("Google 지도"),
                },
            )

if active_view == "store":
    st.subheader("매장 상세 보기")
    detail_names = sorted(
        industry_store_projection["store_name"].dropna().unique()
    )
    if not detail_names:
        st.info("상세 분석할 매장이 없습니다.")
    else:
        default_detail = (
            selected_stores[0]
            if selected_stores and selected_stores[0] in detail_names
            else detail_names[0]
        )
        detail_store_name = st.selectbox(
            "분석할 매장",
            detail_names,
            index=detail_names.index(default_detail),
        )
        detail_frame = projection_source[
            projection_source["store_name"].eq(detail_store_name)
        ]
        detail_projection = industry_store_projection[
            industry_store_projection["store_name"].eq(detail_store_name)
        ]
        detail_row = (
            detail_projection.iloc[0]
            if not detail_projection.empty
            else None
        )
        detail_all = int(len(detail_frame))
        detail_total = int(
            detail_frame["status"].isin(MEASURABLE_STATUSES).sum()
        )
        detail_reserved = int(
            detail_frame["status"].eq("reserved").sum()
        ) if detail_all else 0
        detail_available = int(
            detail_frame["status"].eq("available").sum()
        ) if detail_all else 0
        is_manual_detail = (
            detail_row is not None
            and detail_row["estimate_source"] == "수동 관측"
        )
        detail_monthly_label = (
            won_range(
                float(detail_row["monthly_revenue_min"]),
                float(detail_row["monthly_revenue_max"]),
            )
            if detail_row is not None
            else "-"
        )
        detail_rate_label = (
            (
                f"{float(detail_row['booking_rate_min']):.1f}%"
                if detail_row["booking_rate_min"]
                == detail_row["booking_rate_max"]
                else (
                    f"{float(detail_row['booking_rate_min']):.1f}% ~ "
                    f"{float(detail_row['booking_rate_max']):.1f}%"
                )
            )
            if detail_row is not None
            else "-"
        )

        st.markdown(
            f"""
            <div class="detail-box">
            <b>{detail_store_name}</b><br>
            확인된 요일: {
                detail_row["observed_weekday_names"]
                if detail_row is not None else "-"
            } · 신뢰도: {
                detail_row["confidence"] if detail_row is not None else "-"
            } · 출처: {
                detail_row["estimate_source"] if detail_row is not None else "-"
            }<br>
            매출은 기본 2.7명과 테마별 최소 인원·회차 고정가를 반영한 추정치입니다.
            </div>
            """,
            unsafe_allow_html=True,
        )

        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric(
            "예약률",
            detail_rate_label,
        )
        d2.metric(
            "예약 / 확인 가능 타임",
            f"{detail_reserved:,} / {detail_total:,}"
            if not is_manual_detail else "-",
        )
        d3.metric(
            "남은 타임",
            f"{detail_available:,}" if not is_manual_detail else "-",
        )
        d4.metric(
            "일평균 추정매출",
            won_range(
                float(detail_row["daily_revenue_min"]),
                float(detail_row["daily_revenue_max"]),
            )
            if detail_row is not None
            else "-",
        )
        d5.metric(f"{TODAY.month}월 예상매출", detail_monthly_label)

        if is_manual_detail:
            st.info(
                "이 매장은 공개 예약표를 자동 수집한 값이 아니라 수동 관측 범위입니다. "
                "날짜별 슬롯 통계 대신 키이스케이프 자료 탭에서 산출 근거를 확인할 수 있습니다."
            )

        daily_detail = daily_operations(detail_frame)
        weekday_detail = operations_by(
            detail_frame, ["weekday_number", "weekday"]
        ).sort_values("weekday_number")
        detail_left, detail_right = st.columns([1.25, 1])
        with detail_left:
            st.subheader("일자별 매출과 예약")
            if daily_detail.empty:
                st.info("일자별 데이터가 없습니다.")
            else:
                st.altair_chart(
                    modern_line_chart(
                        daily_detail,
                        "date",
                        "estimated_revenue",
                        value_title="추정매출(원)",
                        extra_tooltips=["reserved_slots", "total_slots"],
                    ),
                    width="stretch",
                )
                st.dataframe(
                    daily_detail[
                        [
                            "date",
                            "weekday",
                            "total_slots",
                            "reserved_slots",
                            "available_slots",
                            "booking_rate",
                            "estimated_revenue",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "date": "날짜",
                        "weekday": "요일",
                        "total_slots": "총 타임",
                        "reserved_slots": "예약",
                        "available_slots": "남음",
                        "booking_rate": st.column_config.ProgressColumn(
                            "예약률", min_value=0, max_value=100, format="%.1f%%"
                        ),
                        "estimated_revenue": st.column_config.NumberColumn(
                            "추정매출", format="%,d원"
                        ),
                    },
                )
        with detail_right:
            st.subheader("요일별 성과")
            if weekday_detail.empty:
                st.info("요일별 데이터가 없습니다.")
            else:
                st.altair_chart(
                    modern_bar_chart(
                        weekday_detail,
                        "weekday",
                        "booking_rate",
                        value_title="예약률(%)",
                        horizontal=False,
                        color="#6557c8",
                    ),
                    width="stretch",
                )
                st.dataframe(
                    weekday_detail[
                        [
                            "weekday",
                            "total_slots",
                            "reserved_slots",
                            "booking_rate",
                            "estimated_revenue",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "weekday": "요일",
                        "total_slots": "총 타임",
                        "reserved_slots": "예약",
                        "booking_rate": st.column_config.ProgressColumn(
                            "예약률", min_value=0, max_value=100, format="%.1f%%"
                        ),
                        "estimated_revenue": st.column_config.NumberColumn(
                            "추정매출", format="%,d원"
                        ),
                    },
                )

        theme_detail = theme_operations(detail_frame)
        hour_detail = operations_by(
            detail_frame, ["hour", "time_band"]
        ).sort_values("hour")
        detail_left, detail_right = st.columns(2)
        with detail_left:
            st.subheader("테마별 성과")
            if theme_detail.empty:
                st.info("테마 데이터가 없습니다.")
            else:
                st.dataframe(
                    theme_detail[
                        [
                            "theme_name",
                            "total_slots",
                            "reserved_slots",
                            "booking_rate",
                            "estimated_revenue",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                    height=390,
                    column_config={
                        "theme_name": "테마",
                        "total_slots": "총 타임",
                        "reserved_slots": "예약",
                        "booking_rate": st.column_config.ProgressColumn(
                            "예약률", min_value=0, max_value=100, format="%.1f%%"
                        ),
                        "estimated_revenue": st.column_config.NumberColumn(
                            "추정매출", format="%,d원"
                        ),
                    },
                )
        with detail_right:
            st.subheader("시간대별 성과")
            if hour_detail.empty:
                st.info("시간대 데이터가 없습니다.")
            else:
                st.altair_chart(
                    modern_bar_chart(
                        hour_detail,
                        "time_band",
                        "booking_rate",
                        value_title="예약률(%)",
                        horizontal=False,
                        color="#f06b4f",
                    ),
                    width="stretch",
                )
                st.dataframe(
                    hour_detail[
                        [
                            "time_band",
                            "total_slots",
                            "reserved_slots",
                            "booking_rate",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                    height=260,
                    column_config={
                        "time_band": "시간대",
                        "total_slots": "총 타임",
                        "reserved_slots": "예약",
                        "booking_rate": st.column_config.ProgressColumn(
                            "예약률", min_value=0, max_value=100, format="%.1f%%"
                        ),
                    },
                )

    st.divider()
    st.subheader("전체 매장 비교")
    stores = store_operations(filtered)
    if stores.empty:
        st.info("선택한 기간에 매장별 예약표가 없습니다.")
    else:
        latest = (
            filtered.groupby("store_id", as_index=False)["crawled_at"]
            .max()
            .rename(columns={"crawled_at": "latest_crawl"})
        )
        stores = stores.merge(latest, on="store_id", how="left")
        stores["latest_crawl"] = (
            stores["latest_crawl"]
            .dt.tz_convert(KST)
            .dt.strftime("%Y-%m-%d %H:%M")
        )
        st.dataframe(
            stores[
                [
                    "region",
                    "store_name",
                    "total_slots",
                    "measured_slots",
                    "reserved_slots",
                    "available_slots",
                    "booking_rate",
                    "estimated_revenue",
                    "price_coverage",
                    "latest_crawl",
                ]
            ],
            hide_index=True,
            width="stretch",
            height=650,
            column_config={
                "region": "지역",
                "store_name": "매장",
                "total_slots": "전체 공개 타임",
                "measured_slots": "상태 확인 타임",
                "reserved_slots": "예약 타임",
                "available_slots": "남은 타임",
                "booking_rate": st.column_config.ProgressColumn(
                    "예약률", min_value=0, max_value=100, format="%.1f%%"
                ),
                "estimated_revenue": st.column_config.NumberColumn(
                    "추정매출", format="%,d원"
                ),
                "price_coverage": st.column_config.ProgressColumn(
                    "가격 반영", min_value=0, max_value=100, format="%.1f%%"
                ),
                "latest_crawl": "온라인 확인 시각",
            },
        )

    with st.expander("업계 추정 매출 통합표", expanded=True):
        industry_view = industry_store_projection.copy()
        industry_view["booking_rate_range"] = industry_view.apply(
            lambda row: (
                f"{row['booking_rate_min']:.1f}%"
                if row["booking_rate_min"] == row["booking_rate_max"]
                else (
                    f"{row['booking_rate_min']:.1f}%"
                    f" ~ {row['booking_rate_max']:.1f}%"
                )
            ),
            axis=1,
        )
        st.dataframe(
            industry_view[
                [
                    "region",
                    "store_name",
                    "estimate_source",
                    "booking_rate_range",
                    "monthly_revenue_min",
                    "monthly_revenue_mid",
                    "monthly_revenue_max",
                    "confidence",
                ]
            ],
            hide_index=True,
            width="stretch",
            height=620,
            column_config={
                "region": "지역",
                "store_name": "매장",
                "estimate_source": "자료 출처",
                "booking_rate_range": "예약률",
                "monthly_revenue_min": st.column_config.NumberColumn(
                    "월매출 하한", format="%,d원"
                ),
                "monthly_revenue_mid": st.column_config.NumberColumn(
                    "월매출 중간값", format="%,d원"
                ),
                "monthly_revenue_max": st.column_config.NumberColumn(
                    "월매출 상한", format="%,d원"
                ),
                "confidence": "신뢰도",
            },
        )

    with st.expander("자동 수집 매장 월~일 예상매출 상세표"):
        st.caption(
            "확인되지 않은 요일은 해당 매장의 관측 일평균으로 보완됩니다."
        )
        st.dataframe(
            monthly_store_projection[
                [
                    "region",
                    "store_name",
                    "observed_weekday_names",
                    "confidence",
                    "booking_rate",
                    "월_daily_revenue",
                    "화_daily_revenue",
                    "수_daily_revenue",
                    "목_daily_revenue",
                    "금_daily_revenue",
                    "토_daily_revenue",
                    "일_daily_revenue",
                    "monthly_revenue",
                ]
            ],
            hide_index=True,
            width="stretch",
            height=620,
            column_config={
                "region": "지역",
                "store_name": "매장",
                "observed_weekday_names": "확인 요일",
                "confidence": "신뢰도",
                "booking_rate": st.column_config.ProgressColumn(
                    "예약률", min_value=0, max_value=100, format="%.1f%%"
                ),
                **{
                    f"{day}_daily_revenue": st.column_config.NumberColumn(
                        f"{day} 일매출", format="%,d원"
                    )
                    for day in ["월", "화", "수", "목", "금", "토", "일"]
                },
                "monthly_revenue": st.column_config.NumberColumn(
                    "6월 예상매출", format="%,d원"
                ),
            },
        )

if active_view == "theme":
    st.subheader("테마별 예약률과 추정매출")
    themes = theme_operations(filtered)
    if themes.empty:
        st.info("선택한 기간에 테마 데이터가 없습니다.")
    else:
        price_info = scoped_catalog[
            [
                "store_id",
                "theme_name",
                "price",
                "booking_value_estimate",
                "effective_people_estimate",
                "per_person_estimate",
                "pricing_summary",
                "price_note",
                "price_source_url",
                "price_verified_at",
            ]
        ].drop_duplicates(["store_id", "theme_name"])
        themes = themes.merge(
            price_info,
            on=["store_id", "theme_name"],
            how="left",
        )
        themes["가격 구분"] = "미확인"
        themes.loc[
            themes["booking_value_estimate"].fillna(0).gt(0),
            "가격 구분",
        ] = "공개 가격 반영"
        themes.loc[
            themes["price_note"]
            .fillna("")
            .str.contains("추정|임시|실제 결제액 확인 필요"),
            "가격 구분",
        ] = "검증 필요 · 계산 제외"
        ranking = st.radio(
            "정렬",
            ["추정매출 높은 순", "예약률 높은 순", "예약률 낮은 순"],
            horizontal=True,
        )
        if ranking == "예약률 높은 순":
            themes = themes.sort_values(
                ["booking_rate", "total_slots"], ascending=[False, False]
            )
        elif ranking == "예약률 낮은 순":
            themes = themes.sort_values(
                ["booking_rate", "total_slots"], ascending=[True, False]
            )
        st.dataframe(
            themes[
                [
                    "store_name",
                    "theme_name",
                    "genre",
                    "가격 구분",
                    "pricing_summary",
                    "booking_value_estimate",
                    "effective_people_estimate",
                    "per_person_estimate",
                    "price_note",
                    "price_source_url",
                    "price_verified_at",
                    "total_slots",
                    "measured_slots",
                    "reserved_slots",
                    "available_slots",
                    "booking_rate",
                    "estimated_revenue",
                    "price_coverage",
                ]
            ].head(200),
            hide_index=True,
            width="stretch",
            height=650,
            column_config={
                "store_name": "매장",
                "theme_name": "테마",
                "genre": "장르",
                "pricing_summary": "공개 가격표",
                "booking_value_estimate": st.column_config.NumberColumn(
                    "인원 규칙 반영 예약액", format="%,d원"
                ),
                "effective_people_estimate": st.column_config.NumberColumn(
                    "계산 인원", format="%.1f명"
                ),
                "per_person_estimate": st.column_config.NumberColumn(
                    "추정 인당 부담", format="%,d원"
                ),
                "price_note": "가격 근거",
                "price_source_url": st.column_config.LinkColumn(
                    "가격 출처", display_text="열기"
                ),
                "price_verified_at": "가격 확인일",
                "total_slots": "전체 공개 타임",
                "measured_slots": "상태 확인 타임",
                "reserved_slots": "예약 타임",
                "available_slots": "남은 타임",
                "booking_rate": st.column_config.ProgressColumn(
                    "예약률", min_value=0, max_value=100, format="%.1f%%"
                ),
                "estimated_revenue": st.column_config.NumberColumn(
                    "추정매출", format="%,d원"
                ),
                "price_coverage": st.column_config.ProgressColumn(
                    "가격 반영", min_value=0, max_value=100, format="%.1f%%"
                ),
            },
        )

if active_view == "trend":
    left, right = st.columns(2)
    with left:
        st.subheader("요일별 예약률")
        weekday = weekday_rates(filtered)
        if weekday.empty:
            st.info("요일 데이터가 없습니다.")
        else:
            st.altair_chart(
                modern_bar_chart(
                    weekday,
                    "weekday",
                    "booking_rate",
                    value_title="예약률(%)",
                    horizontal=False,
                ),
                width="stretch",
            )
    with right:
        st.subheader("시간대별 예약률")
        hourly = hourly_rates(filtered)
        if hourly.empty:
            st.info("시간대 데이터가 없습니다.")
        else:
            st.altair_chart(
                modern_bar_chart(
                    hourly,
                    "time_band",
                    "booking_rate",
                    value_title="예약률(%)",
                    horizontal=False,
                    color="#f06b4f",
                ),
                width="stretch",
            )

    left, right = st.columns(2)
    with left:
        st.subheader("지역별 예약률")
        regions = region_rates(filtered)
        if regions.empty:
            st.info("지역 데이터가 없습니다.")
        else:
            st.altair_chart(
                modern_bar_chart(
                    regions.head(20),
                    "region",
                    "booking_rate",
                    value_title="예약률(%)",
                    color="#6557c8",
                ),
                width="stretch",
            )
    with right:
        st.subheader("평일과 주말")
        weekpart = weekday_weekend_rates(filtered)
        if weekpart.empty:
            st.info("평일·주말 데이터가 없습니다.")
        else:
            st.altair_chart(
                modern_bar_chart(
                    weekpart,
                    "day_type",
                    "booking_rate",
                    value_title="예약률(%)",
                    horizontal=False,
                    color="#147d72",
                ),
                width="stretch",
            )

if active_view == "manual":
    st.subheader("키이스케이프 관측 자료와 산출 근거")
    st.warning(
        f"{manual_metadata.get('source_label', '수동 자료')} · "
        f"관측 기준일 {manual_metadata.get('observed_at', '-')}. "
        "자동 크롤링 결과가 아니며 실제 결제 매출도 아닙니다."
    )
    st.caption(manual_metadata.get("note", ""))
    if manual_store_scope.empty:
        st.info("현재 필터에 해당하는 키이스케이프 관측 자료가 없습니다.")
    else:
        manual_store_view = manual_store_scope.copy()
        manual_store_view["예약률 범위"] = manual_store_view.apply(
            lambda row: (
                f"{row['booking_rate_min']:.1f}%"
                if row["booking_rate_min"] == row["booking_rate_max"]
                else (
                    f"{row['booking_rate_min']:.1f}%"
                    f" ~ {row['booking_rate_max']:.1f}%"
                )
            ),
            axis=1,
        )
        st.subheader("매장 합계")
        st.dataframe(
            manual_store_view[
                [
                    "region",
                    "store_name",
                    "예약률 범위",
                    "daily_revenue_min",
                    "daily_revenue_max",
                    "monthly_revenue_min",
                    "monthly_revenue_max",
                ]
            ].sort_values("monthly_revenue_max", ascending=False),
            hide_index=True,
            width="stretch",
            column_config={
                "region": "지역",
                "store_name": "매장",
                "daily_revenue_min": st.column_config.NumberColumn(
                    "일평균 하한", format="%,d원"
                ),
                "daily_revenue_max": st.column_config.NumberColumn(
                    "일평균 상한", format="%,d원"
                ),
                "monthly_revenue_min": st.column_config.NumberColumn(
                    "월 추정 하한", format="%,d원"
                ),
                "monthly_revenue_max": st.column_config.NumberColumn(
                    "월 추정 상한", format="%,d원"
                ),
            },
        )

        st.subheader("테마별 관측 범위")
        manual_theme_view = manual_theme_scope.copy()
        manual_theme_view["예약률 범위"] = manual_theme_view.apply(
            lambda row: (
                f"{row['booking_rate_min']:.1f}%"
                if row["booking_rate_min"] == row["booking_rate_max"]
                else (
                    f"{row['booking_rate_min']:.1f}%"
                    f" ~ {row['booking_rate_max']:.1f}%"
                )
            ),
            axis=1,
        )
        st.dataframe(
            manual_theme_view[
                [
                    "store_name",
                    "display_name",
                    "예약률 범위",
                    "daily_revenue_min",
                    "daily_revenue_max",
                    "monthly_revenue_min",
                    "monthly_revenue_max",
                ]
            ].sort_values(
                ["store_name", "monthly_revenue_max"],
                ascending=[True, False],
            ),
            hide_index=True,
            width="stretch",
            height=620,
            column_config={
                "store_name": "매장",
                "display_name": "테마",
                "daily_revenue_min": st.column_config.NumberColumn(
                    "일평균 하한", format="%,d원"
                ),
                "daily_revenue_max": st.column_config.NumberColumn(
                    "일평균 상한", format="%,d원"
                ),
                "monthly_revenue_min": st.column_config.NumberColumn(
                    "월 추정 하한", format="%,d원"
                ),
                "monthly_revenue_max": st.column_config.NumberColumn(
                    "월 추정 상한", format="%,d원"
                ),
            },
        )

if active_view == "status":
    st.subheader("매장별 수집 가능 여부")
    overview = filtered_status.copy()
    overview["수집 구분"] = (
        overview["adapter_type"].map(STATUS_LABELS).fillna("확인 필요")
    )
    overview["최근 결과"] = overview["latest_crawl_status"].map(
        {
            "success": "성공",
            "failed": "실패",
            "running": "수집 중",
        }
    ).fillna("-")
    permission_mask = overview["adapter_type"].eq("permission_required")
    overview.loc[permission_mask, "최근 결과"] = "공식 허가 필요"
    overview.loc[permission_mask, "latest_error"] = ""
    manual_ids = set(manual_stores["store_id"].astype(str))
    overview["수동 자료"] = overview["store_id"].map(
        lambda store_id: (
            f"있음 ({manual_metadata.get('observed_at', '-')})"
            if str(store_id) in manual_ids
            else "-"
        )
    )
    overview["최근 확인"] = (
        overview["latest_crawl_at"]
        .dt.tz_convert(KST)
        .dt.strftime("%Y-%m-%d %H:%M")
        .fillna("-")
    )
    automatic_mask = overview["adapter_type"].isin(
        {
            "play33",
            "xdungeon",
            "zero_world",
            "page_today",
            "generic",
            "cubeescape",
            "earthstar",
            "frank",
            "horror_switch",
            "sinbi",
            "deepthinker",
            "oasis",
            "shortstories",
            "keyescape",
            "naver_booking",
        }
    )
    success_mask = overview["latest_crawl_status"].eq("success")
    failed_mask = overview["latest_crawl_status"].eq("failed")
    never_crawled_mask = overview["latest_crawl_status"].isna() & automatic_mask
    status_columns = st.columns(4)
    status_columns[0].metric("전체 등록 매장", f"{len(overview):,}개")
    status_columns[1].metric(
        "자동 수집 성공",
        f"{int((automatic_mask & success_mask).sum()):,}개",
    )
    status_columns[2].metric(
        "최근 수집 실패",
        f"{int((automatic_mask & failed_mask).sum()):,}개",
    )
    status_columns[3].metric(
        "아직 수집 없음",
        f"{int(never_crawled_mask.sum()):,}개",
    )
    st.caption(
        "공식 허가 필요·접근 제한·부분 공개 매장은 자동 통계에서 제외됩니다. "
        "설명과 최근 오류 열에서 매장별 사유를 확인할 수 있습니다."
    )
    st.dataframe(
        overview[
            [
                "region",
                "store_name",
                "수집 구분",
                "최근 결과",
                "수동 자료",
                "최근 확인",
                "latest_error",
                "collection_note",
                "booking_url",
            ]
        ],
        hide_index=True,
        width="stretch",
        height=680,
        column_config={
            "region": "지역",
            "store_name": "매장",
            "latest_error": "최근 오류",
            "collection_note": "설명",
            "booking_url": st.column_config.LinkColumn(
                "공개 예약 페이지", display_text="열기"
            ),
        },
    )

if active_view == "raw":
    st.subheader("지나간 확정 예약 기록")
    st.caption(
        "타임 시작 전에 공개 페이지에서 예약됨을 직접 관측한 슬롯만 영구 보존합니다. "
        "시간이 지났다는 이유만으로 예약으로 추정하지 않습니다."
    )
    if confirmed_reserved.empty:
        st.info("선택한 기간에 지나간 확정 예약 기록이 없습니다.")
    else:
        finalized = confirmed_reserved.copy()
        finalized["finalized_at"] = finalized["finalized_at"].dt.tz_convert(KST)
        st.dataframe(
            finalized[
                [
                    "region",
                    "store_name",
                    "theme_name",
                    "date",
                    "time",
                    "pricing_summary",
                    "booking_value_estimate",
                    "effective_people_estimate",
                    "per_person_estimate",
                    "expected_revenue",
                    "finalized_at",
                ]
            ].sort_values(["date", "store_name", "theme_name", "time"]),
            hide_index=True,
            width="stretch",
            height=360,
            column_config={
                "region": "지역",
                "store_name": "매장",
                "theme_name": "테마",
                "date": "날짜",
                "time": "시간",
                "pricing_summary": "공개 가격표",
                "booking_value_estimate": st.column_config.NumberColumn(
                    "예약 1건 추정액", format="%,d원"
                ),
                "effective_people_estimate": st.column_config.NumberColumn(
                    "계산 인원", format="%.1f명"
                ),
                "per_person_estimate": st.column_config.NumberColumn(
                    "추정 인당 부담", format="%,d원"
                ),
                "expected_revenue": st.column_config.NumberColumn(
                    "확정 추정매출", format="%,d원"
                ),
                "finalized_at": "기록 확정 시각",
            },
        )
    st.divider()
    st.subheader("원본 예약 슬롯")
    if filtered.empty:
        st.info("선택한 기간에 원본 슬롯이 없습니다.")
    else:
        raw = filtered.copy()
        raw["상태"] = raw["status"].map(
            {
                "available": "예약 가능",
                "reserved": "예약됨/예약 불가",
                "closed": "마감",
                "unknown": "확인 필요",
            }
        )
        raw["crawled_at"] = raw["crawled_at"].dt.tz_convert(KST)
        st.dataframe(
            raw[
                [
                    "region",
                    "store_name",
                    "theme_name",
                    "date",
                    "time",
                    "상태",
                    "pricing_summary",
                    "booking_value_estimate",
                    "effective_people_estimate",
                    "per_person_estimate",
                    "expected_revenue",
                    "crawled_at",
                ]
            ].sort_values(["date", "store_name", "theme_name", "time"]),
            hide_index=True,
            width="stretch",
            height=700,
            column_config={
                "region": "지역",
                "store_name": "매장",
                "theme_name": "테마",
                "date": "날짜",
                "time": "시간",
                "pricing_summary": "공개 가격표",
                "booking_value_estimate": st.column_config.NumberColumn(
                    "예약 1건 추정액", format="%,d원"
                ),
                "effective_people_estimate": st.column_config.NumberColumn(
                    "계산 인원", format="%.1f명"
                ),
                "per_person_estimate": st.column_config.NumberColumn(
                    "추정 인당 부담", format="%,d원"
                ),
                "expected_revenue": st.column_config.NumberColumn(
                    "추정매출", format="%,d원"
                ),
                "crawled_at": "온라인 확인 시각",
            },
        )

