from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence
import calendar

import pandas as pd
import yaml

from scraper.models import effective_party_size, estimate_booking_value

MEASURABLE_STATUSES = {"available", "reserved"}

WEEKDAY_NAMES = {
    0: "월",
    1: "화",
    2: "수",
    3: "목",
    4: "금",
    5: "토",
    6: "일",
}

GENRE_BUCKET_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("공포", ("공포", "호러", "귀신", "오컬트")),
    ("스릴러", ("스릴러", "서스펜스", "범죄")),
    ("드라마", ("드라마", "감성", "일상", "스토리")),
    ("미스터리", ("미스터리", "미스테리", "비밀")),
    ("추리", ("추리", "수사", "탐정")),
    ("코믹", ("코믹", "코미디", "개그")),
    ("판타지/SF", ("판타지", "sf", "에스에프", "동화", "마법")),
    ("액션", ("액션", "잠입", "전쟁")),
    ("로맨스", ("로맨스", "사랑")),
    ("어드벤처", ("어드벤처", "모험")),
)


def normalize_genre(value: object) -> str:
    """Collapse detailed theme genres into investor-readable buckets."""
    text = str(value or "").strip()
    if not text:
        return "미분류"
    lowered = text.casefold()
    for bucket, keywords in GENRE_BUCKET_RULES:
        if any(keyword.casefold() in lowered for keyword in keywords):
            return bucket
    return "기타"

MANUAL_ESTIMATE_COLUMNS = [
    "store_id",
    "store_name",
    "region",
    "booking_rate_min",
    "booking_rate_max",
    "daily_revenue_min",
    "daily_revenue_max",
    "monthly_revenue_min",
    "monthly_revenue_max",
    "observed_at",
    "source_label",
    "note",
]

INDUSTRY_STORE_ESTIMATE_COLUMNS = [
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


def load_manual_estimates(
    path: str | Path = "manual_estimates.yaml",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """Load user-provided manual ranges separately from automatic crawl data."""
    source_path = Path(path)
    theme_columns = [
        *MANUAL_ESTIMATE_COLUMNS[:3],
        "theme_name",
        "display_name",
        *MANUAL_ESTIMATE_COLUMNS[3:],
    ]
    if not source_path.exists():
        return (
            pd.DataFrame(columns=MANUAL_ESTIMATE_COLUMNS),
            pd.DataFrame(columns=theme_columns),
            {},
        )
    with source_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    metadata = {
        "catalog_version": str(raw.get("catalog_version", "")),
        "observed_at": str(raw.get("observed_at", "")),
        "source_type": str(raw.get("source_type", "")),
        "source_label": str(raw.get("source_label", "")),
        "note": str(raw.get("note", "")),
    }
    store_rows: list[dict[str, object]] = []
    theme_rows: list[dict[str, object]] = []
    numeric_fields = MANUAL_ESTIMATE_COLUMNS[3:9]
    for raw_store in raw.get("stores", []):
        if not isinstance(raw_store, dict):
            continue
        common = {
            "store_id": str(raw_store.get("store_id", "")).strip(),
            "store_name": str(raw_store.get("store_name", "")).strip(),
            "region": str(raw_store.get("region", "")).strip(),
            "observed_at": metadata["observed_at"],
            "source_label": metadata["source_label"],
            "note": metadata["note"],
        }
        store_row = dict(common)
        for field in numeric_fields:
            store_row[field] = float(raw_store.get(field, 0) or 0)
        store_rows.append(store_row)

        for raw_theme in raw_store.get("themes", []):
            if not isinstance(raw_theme, dict):
                continue
            theme_row = dict(common)
            theme_row["theme_name"] = str(
                raw_theme.get("theme_name", "")
            ).strip()
            theme_row["display_name"] = str(
                raw_theme.get("display_name", theme_row["theme_name"])
            ).strip()
            for field in numeric_fields:
                theme_row[field] = float(raw_theme.get(field, 0) or 0)
            theme_rows.append(theme_row)

    return (
        pd.DataFrame(store_rows, columns=MANUAL_ESTIMATE_COLUMNS),
        pd.DataFrame(theme_rows, columns=theme_columns),
        metadata,
    )


def combine_store_revenue_estimates(
    automatic: pd.DataFrame,
    manual: pd.DataFrame,
    days_in_month: int,
) -> pd.DataFrame:
    """Combine automatic projections and manual ranges into one store market."""
    if days_in_month <= 0:
        raise ValueError("days_in_month must be positive")

    manual_ids = (
        set(manual["store_id"].dropna().astype(str))
        if "store_id" in manual.columns
        else set()
    )
    rows: list[dict[str, object]] = []

    for _, source in automatic.iterrows():
        store_id = str(source.get("store_id", ""))
        monthly = source.get("monthly_revenue")
        if store_id in manual_ids or pd.isna(monthly):
            continue
        monthly_value = float(monthly)
        booking = float(source.get("booking_rate", 0) or 0)
        rows.append(
            {
                "store_id": store_id,
                "store_name": source.get("store_name", ""),
                "region": source.get("region", ""),
                "estimate_source": "자동 수집",
                "booking_rate_min": booking,
                "booking_rate_max": booking,
                "daily_revenue_min": monthly_value / days_in_month,
                "daily_revenue_mid": monthly_value / days_in_month,
                "daily_revenue_max": monthly_value / days_in_month,
                "monthly_revenue_min": monthly_value,
                "monthly_revenue_mid": monthly_value,
                "monthly_revenue_max": monthly_value,
                "observed_days": int(source.get("observed_days", 0) or 0),
                "observed_weekdays": int(
                    source.get("observed_weekdays", 0) or 0
                ),
                "observed_weekday_names": source.get(
                    "observed_weekday_names", "-"
                ),
                "confidence": source.get("confidence", "수집 데이터 없음"),
            }
        )

    for _, source in manual.iterrows():
        monthly_min = float(source.get("monthly_revenue_min", 0) or 0)
        monthly_max = float(source.get("monthly_revenue_max", 0) or 0)
        daily_min = float(source.get("daily_revenue_min", 0) or 0)
        daily_max = float(source.get("daily_revenue_max", 0) or 0)
        rows.append(
            {
                "store_id": str(source.get("store_id", "")),
                "store_name": source.get("store_name", ""),
                "region": source.get("region", ""),
                "estimate_source": "수동 관측",
                "booking_rate_min": float(
                    source.get("booking_rate_min", 0) or 0
                ),
                "booking_rate_max": float(
                    source.get("booking_rate_max", 0) or 0
                ),
                "daily_revenue_min": daily_min,
                "daily_revenue_mid": (daily_min + daily_max) / 2,
                "daily_revenue_max": daily_max,
                "monthly_revenue_min": monthly_min,
                "monthly_revenue_mid": (monthly_min + monthly_max) / 2,
                "monthly_revenue_max": monthly_max,
                "observed_days": 0,
                "observed_weekdays": 0,
                "observed_weekday_names": "수동 관측 범위",
                "confidence": "수동 범위",
            }
        )

    return pd.DataFrame(
        rows, columns=INDUSTRY_STORE_ESTIMATE_COLUMNS
    ).sort_values(
        ["monthly_revenue_mid", "booking_rate_max"],
        ascending=[False, False],
    )


def load_slots(db_path: str | Path = "data/escape_room.db") -> pd.DataFrame:
    path = Path(db_path)
    columns = [
        "id",
        "store_id",
        "store_name",
        "region",
        "theme_name",
        "genre",
        "duration_minutes",
        "date",
        "time",
        "status",
        "price",
        "avg_people",
        "expected_revenue",
        "booking_value_estimate",
        "effective_people_estimate",
        "per_person_estimate",
        "pricing_summary",
        "crawled_at",
        "prestart_status",
        "prestart_crawled_at",
        "finalized_status",
        "finalized_at",
    ]
    if not path.exists():
        return pd.DataFrame(columns=columns)

    query = """
        SELECT
            rs.id,
            rs.store_id,
            s.store_name,
            s.region,
            rs.theme_name,
            COALESCE(t.genre, '') AS genre,
            COALESCE(t.duration_minutes, 0) AS duration_minutes,
            rs.date,
            rs.time,
            rs.status,
            rs.price,
            rs.avg_people,
            rs.expected_revenue,
            COALESCE(t.min_people, 1) AS min_people,
            COALESCE(t.max_people, 0) AS max_people,
            COALESCE(t.party_prices_json, '{}') AS party_prices_json,
            COALESCE(t.weekday_party_prices_json, '{}')
                AS weekday_party_prices_json,
            COALESCE(t.weekend_party_prices_json, '{}')
                AS weekend_party_prices_json,
            rs.crawled_at,
            rs.prestart_status,
            rs.prestart_crawled_at,
            rs.finalized_status,
            rs.finalized_at
        FROM reservation_slots AS rs
        JOIN stores AS s ON s.store_id = rs.store_id
        LEFT JOIN themes AS t
          ON t.store_id = rs.store_id AND t.theme_name = rs.theme_name
    """
    try:
        with sqlite3.connect(path) as connection:
            frame = pd.read_sql_query(query, connection)
    except (sqlite3.DatabaseError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=columns)

    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["genre"] = frame["genre"].map(normalize_genre)
    frame["crawled_at"] = pd.to_datetime(frame["crawled_at"], utc=True)
    frame["prestart_crawled_at"] = pd.to_datetime(
        frame["prestart_crawled_at"], utc=True, errors="coerce"
    )
    frame["finalized_at"] = pd.to_datetime(
        frame["finalized_at"], utc=True, errors="coerce"
    )
    frame["is_reserved"] = frame["status"].eq("reserved")
    frame["weekday_number"] = pd.to_datetime(frame["date"]).dt.dayofweek
    frame["weekday"] = frame["weekday_number"].map(WEEKDAY_NAMES)
    frame["day_type"] = frame["weekday_number"].ge(5).map(
        {True: "주말", False: "평일"}
    )
    frame["hour"] = frame["time"].str.slice(0, 2).astype("Int64")
    frame["time_band"] = frame["hour"].map(
        lambda hour: f"{int(hour):02d}시" if pd.notna(hour) else "미상"
    )
    frame["booking_value_estimate"] = frame.apply(_row_booking_value, axis=1)
    frame["effective_people_estimate"] = frame.apply(
        _row_effective_people, axis=1
    )
    frame["per_person_estimate"] = (
        frame["booking_value_estimate"] / frame["effective_people_estimate"]
    )
    frame["pricing_summary"] = frame.apply(_pricing_summary, axis=1)
    return frame


def load_catalog(db_path: str | Path = "data/escape_room.db") -> pd.DataFrame:
    path = Path(db_path)
    columns = [
        "store_id",
        "store_name",
        "region",
        "booking_url",
        "adapter_type",
        "avg_people",
        "collection_note",
        "address",
        "latitude",
        "longitude",
        "brand_name",
        "brand_logo_url",
        "map_note",
        "theme_name",
        "genre",
        "price",
        "duration_minutes",
        "price_note",
        "price_source_url",
        "price_verified_at",
        "min_people",
        "max_people",
        "party_prices_json",
        "weekday_party_prices_json",
        "weekend_party_prices_json",
        "booking_value_estimate",
        "effective_people_estimate",
        "per_person_estimate",
        "pricing_summary",
    ]
    if not path.exists():
        return pd.DataFrame(columns=columns)

    query = """
        SELECT
            s.store_id,
            s.store_name,
            s.region,
            s.booking_url,
            s.adapter_type,
            s.avg_people,
            s.collection_note,
            s.address,
            s.latitude,
            s.longitude,
            s.brand_name,
            s.brand_logo_url,
            s.map_note,
            t.theme_name,
            t.genre,
            t.price,
            t.duration_minutes,
            t.price_note,
            t.price_source_url,
            t.price_verified_at,
            t.min_people,
            t.max_people,
            t.party_prices_json,
            t.weekday_party_prices_json,
            t.weekend_party_prices_json
        FROM stores AS s
        LEFT JOIN themes AS t
          ON t.store_id = s.store_id
         AND (
            s.adapter_type <> 'xdungeon'
            OR EXISTS (
                SELECT 1
                FROM reservation_slots AS rs
                WHERE rs.store_id = t.store_id
                  AND rs.theme_name = t.theme_name
            )
         )
        ORDER BY s.region, s.store_name, t.theme_name
    """
    try:
        with sqlite3.connect(path) as connection:
            frame = pd.read_sql_query(query, connection)
    except (sqlite3.DatabaseError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=columns)
    if frame.empty:
        return frame
    frame["genre"] = frame["genre"].map(normalize_genre)
    frame["booking_value_estimate"] = frame.apply(
        lambda row: _row_booking_value(row, date.today()), axis=1
    )
    frame["effective_people_estimate"] = frame.apply(
        _row_effective_people, axis=1
    )
    frame["per_person_estimate"] = (
        frame["booking_value_estimate"] / frame["effective_people_estimate"]
    )
    frame["pricing_summary"] = frame.apply(_pricing_summary, axis=1)
    return frame


def _price_map(raw_value: object) -> dict[int, int]:
    if raw_value is None or pd.isna(raw_value):
        return {}
    try:
        parsed = json.loads(str(raw_value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[int, int] = {}
    for raw_people, raw_price in parsed.items():
        try:
            people = int(raw_people)
            total_price = int(raw_price)
        except (TypeError, ValueError):
            continue
        if people > 0 and total_price > 0:
            result[people] = total_price
    return result


def _safe_int(value: object, default: int = 0) -> int:
    if value is None or pd.isna(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _row_booking_value(
    row: pd.Series, fallback_date: date | None = None
) -> float:
    raw_date = row.get("date", fallback_date)
    target_date = fallback_date
    if raw_date is not None and not pd.isna(raw_date):
        try:
            target_date = (
                raw_date
                if isinstance(raw_date, date)
                else pd.to_datetime(raw_date).date()
            )
        except (TypeError, ValueError):
            target_date = fallback_date
    return estimate_booking_value(
        avg_people=_safe_float(row.get("avg_people"), 1.0),
        target_date=target_date,
        price=_safe_int(row.get("price")),
        min_people=_safe_int(row.get("min_people"), 1),
        party_prices=_price_map(row.get("party_prices_json", "{}")),
        weekday_party_prices=_price_map(
            row.get("weekday_party_prices_json", "{}")
        ),
        weekend_party_prices=_price_map(
            row.get("weekend_party_prices_json", "{}")
        ),
    )


def _row_effective_people(row: pd.Series) -> float:
    return effective_party_size(
        _safe_float(row.get("avg_people"), 1.0),
        _safe_int(row.get("min_people"), 1),
    )


def _pricing_summary(row: pd.Series) -> str:
    def format_tiers(label: str, tiers: dict[int, int]) -> str:
        values = ", ".join(
            f"{people}인 {total_price:,}원"
            for people, total_price in sorted(tiers.items())
        )
        return f"{label}{values}" if values else ""

    sections = [
        format_tiers("", _price_map(row.get("party_prices_json", "{}"))),
        format_tiers(
            "평일 ", _price_map(row.get("weekday_party_prices_json", "{}"))
        ),
        format_tiers(
            "주말 ", _price_map(row.get("weekend_party_prices_json", "{}"))
        ),
    ]
    sections = [section for section in sections if section]
    if sections:
        return " / ".join(sections)
    price = _safe_int(row.get("price"))
    return f"1인 {price:,}원" if price > 0 else "공식 가격 미확인"


def load_store_status(
    db_path: str | Path = "data/escape_room.db",
) -> pd.DataFrame:
    path = Path(db_path)
    columns = [
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
        "latest_crawl_status",
        "latest_crawl_at",
        "latest_error",
    ]
    if not path.exists():
        return pd.DataFrame(columns=columns)

    query = """
        SELECT
            s.store_id,
            s.store_name,
            s.region,
            s.booking_url,
            s.adapter_type,
            s.collection_note,
            s.address,
            s.latitude,
            s.longitude,
            s.brand_name,
            s.brand_logo_url,
            s.map_note,
            (
                SELECT cl.status
                FROM crawl_logs AS cl
                WHERE cl.store_id = s.store_id
                ORDER BY cl.started_at DESC
                LIMIT 1
            ) AS latest_crawl_status,
            (
                SELECT cl.started_at
                FROM crawl_logs AS cl
                WHERE cl.store_id = s.store_id
                ORDER BY cl.started_at DESC
                LIMIT 1
            ) AS latest_crawl_at,
            (
                SELECT cl.error_message
                FROM crawl_logs AS cl
                WHERE cl.store_id = s.store_id
                ORDER BY cl.started_at DESC
                LIMIT 1
            ) AS latest_error
        FROM stores AS s
        ORDER BY s.region, s.store_name
    """
    try:
        with sqlite3.connect(path) as connection:
            frame = pd.read_sql_query(query, connection)
    except (sqlite3.DatabaseError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=columns)
    frame["latest_crawl_at"] = pd.to_datetime(
        frame["latest_crawl_at"], utc=True, errors="coerce"
    )
    return frame


def filter_slots(
    frame: pd.DataFrame,
    regions: Sequence[str] | None = None,
    stores: Sequence[str] | None = None,
    themes: Sequence[str] | None = None,
    start_date: object | None = None,
    end_date: object | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    if regions:
        result = result[result["region"].isin(regions)]
    if stores:
        result = result[result["store_name"].isin(stores)]
    if themes:
        result = result[result["theme_name"].isin(themes)]
    if start_date is not None:
        result = result[result["date"] >= start_date]
    if end_date is not None:
        result = result[result["date"] <= end_date]
    return result


def booking_rate(frame: pd.DataFrame) -> float:
    measurable = frame[frame["status"].isin(MEASURABLE_STATUSES)]
    if measurable.empty:
        return 0.0
    return float(measurable["status"].eq("reserved").mean() * 100)


def estimated_ticket_value(frame: pd.DataFrame) -> float:
    """Average estimated booking value, excluding themes with unknown price."""
    priced_reserved = frame[
        frame["status"].eq("reserved") & frame["expected_revenue"].gt(0)
    ]
    if priced_reserved.empty:
        return 0.0
    return float(priced_reserved["expected_revenue"].mean())


def price_coverage(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    value_column = (
        "booking_value_estimate"
        if "booking_value_estimate" in frame.columns
        else "price"
    )
    return float(frame[value_column].gt(0).mean() * 100)


def rate_by(frame: pd.DataFrame, groups: str | list[str]) -> pd.DataFrame:
    group_columns = [groups] if isinstance(groups, str) else groups
    if frame.empty:
        return pd.DataFrame(
            columns=[*group_columns, "reserved_slots", "total_slots", "booking_rate"]
        )

    result = (
        frame.assign(
            reserved=frame["status"].eq("reserved").astype(int),
            measurable=frame["status"].isin(MEASURABLE_STATUSES).astype(int),
        )
        .groupby(group_columns, dropna=False)
        .agg(
            reserved_slots=("reserved", "sum"),
            total_slots=("measurable", "sum"),
        )
        .reset_index()
    )
    result["booking_rate"] = (
        result["reserved_slots"]
        .div(result["total_slots"].where(result["total_slots"].gt(0)))
        .fillna(0)
        .mul(100)
        .round(2)
    )
    return result


def region_rates(frame: pd.DataFrame) -> pd.DataFrame:
    return rate_by(frame, "region").sort_values("booking_rate", ascending=False)


def store_rates(frame: pd.DataFrame) -> pd.DataFrame:
    return rate_by(frame, ["store_id", "store_name", "region"]).sort_values(
        "booking_rate", ascending=False
    )


def theme_rates(frame: pd.DataFrame) -> pd.DataFrame:
    return rate_by(
        frame, ["store_id", "store_name", "theme_name", "genre"]
    ).sort_values(["booking_rate", "total_slots"], ascending=[False, False])


def weekday_rates(frame: pd.DataFrame) -> pd.DataFrame:
    result = rate_by(frame, ["weekday_number", "weekday"])
    return result.sort_values("weekday_number")


def hourly_rates(frame: pd.DataFrame) -> pd.DataFrame:
    result = rate_by(frame, ["hour", "time_band"])
    return result.sort_values("hour")


def weekday_weekend_rates(frame: pd.DataFrame) -> pd.DataFrame:
    result = rate_by(frame, "day_type")
    order = pd.Categorical(result["day_type"], categories=["평일", "주말"], ordered=True)
    return result.assign(_order=order).sort_values("_order").drop(columns="_order")


def daily_revenue(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "expected_revenue"])
    return (
        frame.groupby("date", as_index=False)["expected_revenue"]
        .sum()
        .sort_values("date")
    )


def monthly_revenue(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["month", "expected_revenue"])
    result = frame.copy()
    result["month"] = pd.to_datetime(result["date"]).dt.to_period("M").astype(str)
    return (
        result.groupby("month", as_index=False)["expected_revenue"]
        .sum()
        .sort_values("month")
    )


def operations_by(
    frame: pd.DataFrame, groups: str | list[str]
) -> pd.DataFrame:
    """Summarize operating slots and estimated revenue for readable tables."""
    group_columns = [groups] if isinstance(groups, str) else groups
    columns = [
        *group_columns,
        "reserved_slots",
        "available_slots",
        "closed_slots",
        "unknown_slots",
        "measured_slots",
        "total_slots",
        "booking_rate",
        "estimated_revenue",
        "priced_slots",
        "price_coverage",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    result = (
        frame.assign(
            reserved=frame["status"].eq("reserved").astype(int),
            available=frame["status"].eq("available").astype(int),
            closed=frame["status"].eq("closed").astype(int),
            unknown=frame["status"].eq("unknown").astype(int),
            measurable=frame["status"].isin(MEASURABLE_STATUSES).astype(int),
            priced=(
                frame[
                    "booking_value_estimate"
                    if "booking_value_estimate" in frame.columns
                    else "price"
                ]
                .gt(0)
                .astype(int)
            ),
        )
        .groupby(group_columns, dropna=False)
        .agg(
            reserved_slots=("reserved", "sum"),
            available_slots=("available", "sum"),
            closed_slots=("closed", "sum"),
            unknown_slots=("unknown", "sum"),
            measured_slots=("measurable", "sum"),
            total_slots=("id", "count"),
            estimated_revenue=("expected_revenue", "sum"),
            priced_slots=("priced", "sum"),
        )
        .reset_index()
    )
    result["booking_rate"] = (
        result["reserved_slots"]
        .div(result["measured_slots"].where(result["measured_slots"].gt(0)))
        .fillna(0)
        .mul(100)
        .round(1)
    )
    result["price_coverage"] = (
        result["priced_slots"] / result["total_slots"] * 100
    ).round(1)
    return result[columns]


def daily_operations(frame: pd.DataFrame) -> pd.DataFrame:
    return operations_by(frame, ["date", "weekday_number", "weekday"]).sort_values(
        "date"
    )


def store_operations(frame: pd.DataFrame) -> pd.DataFrame:
    return operations_by(
        frame, ["store_id", "store_name", "region"]
    ).sort_values(
        ["estimated_revenue", "booking_rate", "total_slots"],
        ascending=[False, False, False],
    )


def theme_operations(frame: pd.DataFrame) -> pd.DataFrame:
    return operations_by(
        frame, ["store_id", "store_name", "region", "theme_name", "genre"]
    ).sort_values(
        ["estimated_revenue", "booking_rate", "total_slots"],
        ascending=[False, False, False],
    )


def store_growth_trends(
    frame: pd.DataFrame,
    reference_date: date,
    window_days: int = 7,
) -> pd.DataFrame:
    """Compare each store's recent window with the previous same-size window."""
    columns = [
        "store_id",
        "store_name",
        "region",
        "current_start",
        "current_end",
        "previous_start",
        "previous_end",
        "current_revenue",
        "previous_revenue",
        "revenue_delta",
        "revenue_delta_pct",
        "current_booking_rate",
        "previous_booking_rate",
        "booking_rate_delta",
        "current_reserved_slots",
        "previous_reserved_slots",
        "trend_label",
    ]
    if frame.empty or window_days <= 0:
        return pd.DataFrame(columns=columns)

    current_end = reference_date
    current_start = reference_date - timedelta(days=window_days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)

    scoped = frame[
        (frame["date"] >= previous_start) & (frame["date"] <= current_end)
    ].copy()
    if scoped.empty:
        return pd.DataFrame(columns=columns)

    def summarize(period_frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        if period_frame.empty:
            return pd.DataFrame(
                columns=[
                    "store_id",
                    "store_name",
                    "region",
                    f"{prefix}_revenue",
                    f"{prefix}_reserved_slots",
                    f"{prefix}_measured_slots",
                    f"{prefix}_booking_rate",
                ]
            )
        summary = (
            period_frame.assign(
                reserved=period_frame["status"].eq("reserved").astype(int),
                measurable=period_frame["status"].isin(MEASURABLE_STATUSES).astype(int),
            )
            .groupby(["store_id", "store_name", "region"], as_index=False)
            .agg(
                **{
                    f"{prefix}_revenue": ("expected_revenue", "sum"),
                    f"{prefix}_reserved_slots": ("reserved", "sum"),
                    f"{prefix}_measured_slots": ("measurable", "sum"),
                }
            )
        )
        summary[f"{prefix}_booking_rate"] = (
            summary[f"{prefix}_reserved_slots"]
            .div(summary[f"{prefix}_measured_slots"].where(summary[f"{prefix}_measured_slots"].gt(0)))
            .fillna(0)
            .mul(100)
            .round(1)
        )
        return summary

    current = summarize(
        scoped[(scoped["date"] >= current_start) & (scoped["date"] <= current_end)],
        "current",
    )
    previous = summarize(
        scoped[(scoped["date"] >= previous_start) & (scoped["date"] <= previous_end)],
        "previous",
    )
    result = current.merge(
        previous,
        on=["store_id", "store_name", "region"],
        how="outer",
    )
    for column in [
        "current_revenue",
        "previous_revenue",
        "current_reserved_slots",
        "previous_reserved_slots",
        "current_measured_slots",
        "previous_measured_slots",
        "current_booking_rate",
        "previous_booking_rate",
    ]:
        result[column] = result[column].fillna(0)

    result["revenue_delta"] = (
        result["current_revenue"] - result["previous_revenue"]
    ).round(2)
    result["revenue_delta_pct"] = (
        result["revenue_delta"]
        .div(result["previous_revenue"].where(result["previous_revenue"].gt(0)))
        .mul(100)
        .fillna(0)
        .round(1)
    )
    result["booking_rate_delta"] = (
        result["current_booking_rate"] - result["previous_booking_rate"]
    ).round(1)

    def label(row: pd.Series) -> str:
        if row["previous_measured_slots"] <= 0 and row["current_measured_slots"] > 0:
            return "신규 관측"
        if row["current_measured_slots"] <= 0:
            return "최근 데이터 없음"
        if row["revenue_delta_pct"] >= 20 or row["booking_rate_delta"] >= 8:
            return "상승"
        if row["revenue_delta_pct"] <= -20 or row["booking_rate_delta"] <= -8:
            return "하락"
        return "유지"

    result["trend_label"] = result.apply(label, axis=1)
    result["current_start"] = current_start
    result["current_end"] = current_end
    result["previous_start"] = previous_start
    result["previous_end"] = previous_end
    return result[columns].sort_values(
        ["revenue_delta", "current_revenue"],
        ascending=[False, False],
    )


def price_strategy_matrix(
    frame: pd.DataFrame,
    catalog: pd.DataFrame,
) -> pd.DataFrame:
    """Classify themes by demand and public price level."""
    columns = [
        "strategy",
        "store_id",
        "store_name",
        "region",
        "theme_name",
        "genre",
        "booking_rate",
        "per_person_estimate",
        "booking_value_estimate",
        "reserved_slots",
        "measured_slots",
        "estimated_revenue",
        "price_coverage",
    ]
    if frame.empty or catalog.empty:
        return pd.DataFrame(columns=columns)

    operations = theme_operations(frame)
    if operations.empty:
        return pd.DataFrame(columns=columns)
    price_info = catalog[
        [
            "store_id",
            "theme_name",
            "booking_value_estimate",
            "per_person_estimate",
        ]
    ].drop_duplicates(["store_id", "theme_name"])
    result = operations.merge(
        price_info,
        on=["store_id", "theme_name"],
        how="left",
    )
    result = result[
        result["measured_slots"].gt(0)
        & result["booking_value_estimate"].fillna(0).gt(0)
        & result["per_person_estimate"].fillna(0).gt(0)
    ].copy()
    if result.empty:
        return pd.DataFrame(columns=columns)

    price_threshold = float(result["per_person_estimate"].median())
    demand_threshold = max(60.0, float(result["booking_rate"].median()))

    def classify(row: pd.Series) -> str:
        high_price = float(row["per_person_estimate"]) >= price_threshold
        high_demand = float(row["booking_rate"]) >= demand_threshold
        if high_price and high_demand:
            return "프리미엄 강세"
        if not high_price and high_demand:
            return "가격 인상 여지"
        if high_price and not high_demand:
            return "가격 저항 가능성"
        return "수요 육성 구간"

    result["strategy"] = result.apply(classify, axis=1)
    return result[columns].sort_values(
        ["strategy", "estimated_revenue", "booking_rate"],
        ascending=[True, False, False],
    )


def store_efficiency(frame: pd.DataFrame) -> pd.DataFrame:
    """Measure revenue efficiency per slot, theme and visible operating hour."""
    columns = [
        "store_id",
        "store_name",
        "region",
        "theme_count",
        "measured_slots",
        "reserved_slots",
        "booking_rate",
        "estimated_revenue",
        "observed_hours",
        "revenue_per_measured_slot",
        "revenue_per_reserved_slot",
        "revenue_per_theme",
        "revenue_per_operating_hour",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    source = frame.copy()
    if "duration_minutes" not in source.columns:
        source["duration_minutes"] = 60
    source["duration_minutes"] = (
        pd.to_numeric(source["duration_minutes"], errors="coerce")
        .fillna(60)
        .where(lambda value: value.gt(0), 60)
    )
    source["measurable"] = source["status"].isin(MEASURABLE_STATUSES).astype(int)
    source["reserved"] = source["status"].eq("reserved").astype(int)
    source["measured_hours"] = (
        source["duration_minutes"] / 60 * source["measurable"]
    )
    grouped = (
        source.groupby(["store_id", "store_name", "region"], as_index=False)
        .agg(
            theme_count=("theme_name", "nunique"),
            measured_slots=("measurable", "sum"),
            reserved_slots=("reserved", "sum"),
            estimated_revenue=("expected_revenue", "sum"),
            observed_hours=("measured_hours", "sum"),
        )
    )
    grouped["booking_rate"] = (
        grouped["reserved_slots"]
        .div(grouped["measured_slots"].where(grouped["measured_slots"].gt(0)))
        .fillna(0)
        .mul(100)
        .round(1)
    )
    grouped["revenue_per_measured_slot"] = (
        grouped["estimated_revenue"]
        .div(grouped["measured_slots"].where(grouped["measured_slots"].gt(0)))
        .fillna(0)
        .round(2)
    )
    grouped["revenue_per_reserved_slot"] = (
        grouped["estimated_revenue"]
        .div(grouped["reserved_slots"].where(grouped["reserved_slots"].gt(0)))
        .fillna(0)
        .round(2)
    )
    grouped["revenue_per_theme"] = (
        grouped["estimated_revenue"]
        .div(grouped["theme_count"].where(grouped["theme_count"].gt(0)))
        .fillna(0)
        .round(2)
    )
    grouped["revenue_per_operating_hour"] = (
        grouped["estimated_revenue"]
        .div(grouped["observed_hours"].where(grouped["observed_hours"].gt(0)))
        .fillna(0)
        .round(2)
    )
    grouped["observed_hours"] = grouped["observed_hours"].round(2)
    return grouped[columns].sort_values(
        ["revenue_per_operating_hour", "estimated_revenue"],
        ascending=[False, False],
    )


def _haversine_meters(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    earth_radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    hav = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius * 2 * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def market_radius_summary(
    store_projection: pd.DataFrame,
    store_status: pd.DataFrame,
    radius_meters: int = 700,
) -> pd.DataFrame:
    """Aggregate estimated revenue around each mapped store."""
    columns = [
        "anchor_store_id",
        "anchor_store_name",
        "region",
        "radius_meters",
        "nearby_store_count",
        "revenue_store_count",
        "monthly_revenue_sum",
        "average_store_monthly_revenue",
        "top_store_name",
        "top_store_monthly_revenue",
        "competition_density",
    ]
    if store_projection.empty or store_status.empty:
        return pd.DataFrame(columns=columns)

    locations = store_status[
        ["store_id", "store_name", "region", "latitude", "longitude"]
    ].drop_duplicates("store_id")
    market = locations.merge(
        store_projection[
            ["store_id", "monthly_revenue_mid", "estimate_source"]
        ],
        on="store_id",
        how="left",
    )
    market["monthly_revenue_mid"] = market["monthly_revenue_mid"].fillna(0)
    market = market.dropna(subset=["latitude", "longitude"]).copy()
    if market.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    area_km2 = math.pi * (radius_meters / 1000) ** 2
    for _, anchor in market.iterrows():
        distances = market.apply(
            lambda row: _haversine_meters(
                float(anchor["latitude"]),
                float(anchor["longitude"]),
                float(row["latitude"]),
                float(row["longitude"]),
            ),
            axis=1,
        )
        nearby = market[distances <= radius_meters].copy()
        if nearby.empty:
            continue
        top = nearby.sort_values(
            "monthly_revenue_mid", ascending=False
        ).iloc[0]
        revenue_store_count = int(nearby["monthly_revenue_mid"].gt(0).sum())
        monthly_sum = float(nearby["monthly_revenue_mid"].sum())
        rows.append(
            {
                "anchor_store_id": anchor["store_id"],
                "anchor_store_name": anchor["store_name"],
                "region": anchor["region"],
                "radius_meters": radius_meters,
                "nearby_store_count": int(len(nearby)),
                "revenue_store_count": revenue_store_count,
                "monthly_revenue_sum": round(monthly_sum, 2),
                "average_store_monthly_revenue": round(
                    monthly_sum / revenue_store_count
                    if revenue_store_count
                    else 0.0,
                    2,
                ),
                "top_store_name": top["store_name"],
                "top_store_monthly_revenue": round(
                    float(top["monthly_revenue_mid"] or 0), 2
                ),
                "competition_density": round(len(nearby) / area_km2, 2)
                if area_km2
                else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["monthly_revenue_sum", "nearby_store_count"],
        ascending=[False, False],
    )


def project_monthly_revenue(
    frame: pd.DataFrame, year: int, month: int
) -> dict[str, float | int]:
    """
    Project a calendar month from observed daily revenue.

    Each observed weekday uses its own average. Weekdays without observations
    use the overall observed daily average. This remains an estimate, not
    actual sales, because public reservation state, average party size and
    listed prices may differ from final payment.
    """
    if frame.empty:
        return {
            "projected_revenue": 0.0,
            "observed_revenue": 0.0,
            "observed_days": 0,
            "daily_average": 0.0,
        }

    month_frame = frame[
        pd.to_datetime(frame["date"]).dt.to_period("M")
        == pd.Period(f"{year:04d}-{month:02d}", freq="M")
    ].copy()
    if "status" in month_frame.columns:
        month_frame = month_frame[
            month_frame["status"].isin(MEASURABLE_STATUSES)
        ]
    if month_frame.empty:
        return {
            "projected_revenue": 0.0,
            "observed_revenue": 0.0,
            "observed_days": 0,
            "daily_average": 0.0,
        }

    daily = (
        month_frame.groupby(["date", "weekday_number"], as_index=False)[
            "expected_revenue"
        ]
        .sum()
        .sort_values("date")
    )
    observed_days = int(daily["date"].nunique())
    observed_revenue = float(daily["expected_revenue"].sum())
    daily_average = float(daily["expected_revenue"].mean())
    weekday_average = daily.groupby("weekday_number")[
        "expected_revenue"
    ].mean()

    projected = 0.0
    _, days_in_month = calendar.monthrange(year, month)
    for day_number in range(1, days_in_month + 1):
        weekday_number = date(year, month, day_number).weekday()
        projected += float(
            weekday_average.get(weekday_number, daily_average)
        )

    return {
        "projected_revenue": round(projected, 2),
        "observed_revenue": round(observed_revenue, 2),
        "observed_days": observed_days,
        "daily_average": round(daily_average, 2),
    }


def store_monthly_projections(
    frame: pd.DataFrame, year: int, month: int
) -> pd.DataFrame:
    """Project each store from its own observed Monday-Sunday revenue pattern."""
    weekday_columns = [f"{WEEKDAY_NAMES[number]}_daily_revenue" for number in range(7)]
    columns = [
        "store_id",
        "store_name",
        "region",
        "observed_days",
        "observed_weekdays",
        "observed_weekday_names",
        "coverage",
        "confidence",
        "total_slots",
        "reserved_slots",
        "booking_rate",
        *weekday_columns,
        "monthly_revenue",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    month_frame = frame[
        pd.to_datetime(frame["date"]).dt.to_period("M")
        == pd.Period(f"{year:04d}-{month:02d}", freq="M")
    ].copy()
    month_frame = month_frame[
        month_frame["status"].isin(MEASURABLE_STATUSES)
    ]
    if month_frame.empty:
        return pd.DataFrame(columns=columns)

    daily = (
        month_frame.assign(
            reserved=month_frame["status"].eq("reserved").astype(int),
            measurable=month_frame["status"].isin(MEASURABLE_STATUSES).astype(int),
        )
        .groupby(
            [
                "store_id",
                "store_name",
                "region",
                "date",
                "weekday_number",
            ],
            as_index=False,
        )
        .agg(
            total_slots=("measurable", "sum"),
            reserved_slots=("reserved", "sum"),
            daily_revenue=("expected_revenue", "sum"),
        )
    )
    calendar_weekdays = [
        date(year, month, day_number).weekday()
        for day_number in range(1, calendar.monthrange(year, month)[1] + 1)
    ]
    weekday_counts = pd.Series(calendar_weekdays).value_counts().to_dict()
    results: list[dict[str, object]] = []

    for (store_id, store_name, region), store_daily in daily.groupby(
        ["store_id", "store_name", "region"], dropna=False
    ):
        weekday_revenue = store_daily.groupby("weekday_number")[
            "daily_revenue"
        ].mean()
        fallback_revenue = float(store_daily["daily_revenue"].mean())
        observed_weekdays = int(store_daily["weekday_number"].nunique())
        observed_weekday_numbers = sorted(
            int(number) for number in store_daily["weekday_number"].unique()
        )
        row: dict[str, object] = {
            "store_id": store_id,
            "store_name": store_name,
            "region": region,
            "observed_days": int(store_daily["date"].nunique()),
            "observed_weekdays": observed_weekdays,
            "observed_weekday_names": ", ".join(
                WEEKDAY_NAMES[number] for number in observed_weekday_numbers
            ),
            "coverage": round(observed_weekdays / 7 * 100, 1),
            "confidence": (
                "높음"
                if observed_weekdays == 7
                else "보통"
                if observed_weekdays >= 5
                else "낮음"
            ),
            "total_slots": int(store_daily["total_slots"].sum()),
            "reserved_slots": int(store_daily["reserved_slots"].sum()),
        }
        row["booking_rate"] = round(
            (
                float(row["reserved_slots"])
                / float(row["total_slots"])
                * 100
            )
            if row["total_slots"]
            else 0.0,
            1,
        )
        monthly_revenue = 0.0
        for weekday_number in range(7):
            revenue = float(
                weekday_revenue.get(weekday_number, fallback_revenue)
            )
            row[f"{WEEKDAY_NAMES[weekday_number]}_daily_revenue"] = round(
                revenue, 2
            )
            monthly_revenue += revenue * int(
                weekday_counts.get(weekday_number, 0)
            )
        row["monthly_revenue"] = round(monthly_revenue, 2)
        results.append(row)

    return pd.DataFrame(results, columns=columns).sort_values(
        ["monthly_revenue", "booking_rate"],
        ascending=[False, False],
    )


def genre_monthly_summary(
    frame: pd.DataFrame, year: int, month: int
) -> pd.DataFrame:
    """Summarize projected revenue per genre without mixing store sizes."""
    columns = [
        "genre",
        "store_count",
        "theme_count",
        "observed_days",
        "reserved_slots",
        "total_slots",
        "booking_rate",
        "average_daily_revenue",
        "average_monthly_revenue",
        "total_monthly_revenue",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    genre_frame = frame.copy()
    genre_frame["genre"] = (
        genre_frame["genre"].fillna("").astype(str).str.strip().replace("", "미분류")
    )
    # Project store by store inside each genre so a large branch does not
    # silently turn the "average" into a nationwide total.
    projections: list[pd.DataFrame] = []
    for genre, subset in genre_frame.groupby("genre", dropna=False):
        projected = store_monthly_projections(subset, year, month)
        if projected.empty:
            continue
        projected["genre"] = genre
        projected["theme_count"] = int(
            len(subset[["store_id", "theme_name"]].drop_duplicates())
        )
        projections.append(projected)
    if not projections:
        return pd.DataFrame(columns=columns)

    store_genres = pd.concat(projections, ignore_index=True)
    results = (
        store_genres.groupby("genre", as_index=False)
        .agg(
            store_count=("store_id", "nunique"),
            theme_count=("theme_count", "max"),
            observed_days=("observed_days", "sum"),
            reserved_slots=("reserved_slots", "sum"),
            total_slots=("total_slots", "sum"),
            average_monthly_revenue=("monthly_revenue", "mean"),
            total_monthly_revenue=("monthly_revenue", "sum"),
        )
    )
    results["booking_rate"] = (
        results["reserved_slots"]
        .div(results["total_slots"].where(results["total_slots"].gt(0)))
        .fillna(0)
        .mul(100)
        .round(1)
    )
    days_in_month = calendar.monthrange(year, month)[1]
    results["average_daily_revenue"] = (
        results["average_monthly_revenue"] / days_in_month
    ).round(2)
    for column in ["average_monthly_revenue", "total_monthly_revenue"]:
        results[column] = results[column].round(2)
    return results[columns].sort_values(
        ["average_monthly_revenue", "booking_rate"],
        ascending=[False, False],
    )


def top_themes(frame: pd.DataFrame, limit: int = 50) -> pd.DataFrame:
    return theme_rates(frame).head(limit)


def low_themes(frame: pd.DataFrame, limit: int = 50) -> pd.DataFrame:
    return theme_rates(frame).sort_values(
        ["booking_rate", "total_slots"], ascending=[True, False]
    ).head(limit)
