from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterator, Sequence
from zoneinfo import ZoneInfo

from scraper.models import ReservationSlot, StoreConfig, estimate_booking_value

KST = ZoneInfo("Asia/Seoul")
PRESTART_STATUSES = {"available", "reserved"}

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    store_name TEXT NOT NULL,
    region TEXT NOT NULL,
    booking_url TEXT NOT NULL,
    adapter_type TEXT NOT NULL,
    avg_people REAL NOT NULL,
    collection_note TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    latitude REAL,
    longitude REAL,
    brand_name TEXT NOT NULL DEFAULT '',
    brand_logo_url TEXT NOT NULL DEFAULT '',
    map_note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    theme_name TEXT NOT NULL,
    genre TEXT NOT NULL,
    price INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    price_note TEXT NOT NULL DEFAULT '',
    price_source_url TEXT NOT NULL DEFAULT '',
    price_verified_at TEXT NOT NULL DEFAULT '',
    min_people INTEGER NOT NULL DEFAULT 1,
    max_people INTEGER NOT NULL DEFAULT 0,
    party_prices_json TEXT NOT NULL DEFAULT '{}',
    weekday_party_prices_json TEXT NOT NULL DEFAULT '{}',
    weekend_party_prices_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(store_id, theme_name),
    FOREIGN KEY(store_id) REFERENCES stores(store_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reservation_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    theme_name TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available', 'reserved', 'closed', 'unknown')),
    price INTEGER NOT NULL,
    avg_people REAL NOT NULL,
    expected_revenue REAL NOT NULL DEFAULT 0,
    crawled_at TEXT NOT NULL,
    prestart_status TEXT NOT NULL DEFAULT '',
    prestart_crawled_at TEXT NOT NULL DEFAULT '',
    finalized_status TEXT NOT NULL DEFAULT '',
    finalized_at TEXT NOT NULL DEFAULT '',
    UNIQUE(store_id, theme_name, date, time),
    FOREIGN KEY(store_id) REFERENCES stores(store_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_slots_date ON reservation_slots(date);
CREATE INDEX IF NOT EXISTS idx_slots_store_date ON reservation_slots(store_id, date);
CREATE INDEX IF NOT EXISTS idx_slots_status ON reservation_slots(status);

CREATE TABLE IF NOT EXISTS reservation_slot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    theme_name TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    observed_status TEXT NOT NULL
        CHECK(observed_status IN ('available', 'reserved', 'closed', 'unknown')),
    observed_before_start INTEGER NOT NULL,
    price INTEGER NOT NULL,
    avg_people REAL NOT NULL,
    expected_revenue REAL NOT NULL DEFAULT 0,
    crawled_at TEXT NOT NULL,
    UNIQUE(store_id, theme_name, date, time, crawled_at),
    FOREIGN KEY(store_id) REFERENCES stores(store_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_slot_history_slot
ON reservation_slot_history(store_id, date, theme_name, time, crawled_at);

CREATE TABLE IF NOT EXISTS crawl_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    target_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    slots_found INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY(store_id) REFERENCES stores(store_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_crawl_logs_store_started
ON crawl_logs(store_id, started_at DESC);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    scope_label TEXT NOT NULL DEFAULT '전체',
    store_count INTEGER NOT NULL DEFAULT 0,
    theme_count INTEGER NOT NULL DEFAULT 0,
    measured_slots INTEGER NOT NULL DEFAULT 0,
    reserved_slots INTEGER NOT NULL DEFAULT 0,
    booking_rate REAL NOT NULL DEFAULT 0,
    period_revenue REAL NOT NULL DEFAULT 0,
    projected_monthly_revenue REAL NOT NULL DEFAULT 0,
    average_store_monthly_revenue REAL NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(snapshot_date, scope_label)
);

CREATE INDEX IF NOT EXISTS idx_metric_snapshots_date
ON metric_snapshots(snapshot_date DESC, created_at DESC);
"""


class Database:
    def __init__(self, path: str | Path = "data/escape_room.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._add_missing_columns(connection)
            self._repair_elapsed_slot_assumptions(connection)

    @staticmethod
    def _add_missing_columns(connection: sqlite3.Connection) -> None:
        theme_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(themes)").fetchall()
        }
        theme_migrations = {
            "price_note": "TEXT NOT NULL DEFAULT ''",
            "price_source_url": "TEXT NOT NULL DEFAULT ''",
            "price_verified_at": "TEXT NOT NULL DEFAULT ''",
            "min_people": "INTEGER NOT NULL DEFAULT 1",
            "max_people": "INTEGER NOT NULL DEFAULT 0",
            "party_prices_json": "TEXT NOT NULL DEFAULT '{}'",
            "weekday_party_prices_json": "TEXT NOT NULL DEFAULT '{}'",
            "weekend_party_prices_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, definition in theme_migrations.items():
            if name not in theme_columns:
                connection.execute(
                    f"ALTER TABLE themes ADD COLUMN {name} {definition}"
                )
        store_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(stores)").fetchall()
        }
        store_migrations = {
            "collection_note": "TEXT NOT NULL DEFAULT ''",
            "address": "TEXT NOT NULL DEFAULT ''",
            "latitude": "REAL",
            "longitude": "REAL",
            "brand_name": "TEXT NOT NULL DEFAULT ''",
            "brand_logo_url": "TEXT NOT NULL DEFAULT ''",
            "map_note": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in store_migrations.items():
            if name not in store_columns:
                connection.execute(
                    f"ALTER TABLE stores ADD COLUMN {name} {definition}"
                )
        slot_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(reservation_slots)"
            ).fetchall()
        }
        slot_migrations = {
            "prestart_status": "TEXT NOT NULL DEFAULT ''",
            "prestart_crawled_at": "TEXT NOT NULL DEFAULT ''",
            "finalized_status": "TEXT NOT NULL DEFAULT ''",
            "finalized_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in slot_migrations.items():
            if name not in slot_columns:
                connection.execute(
                    f"ALTER TABLE reservation_slots ADD COLUMN {name} {definition}"
                )

    @staticmethod
    def _slot_start(slot_date: date, slot_time: str) -> datetime:
        parsed_time = time.fromisoformat(slot_time)
        return datetime.combine(slot_date, parsed_time, tzinfo=KST)

    @staticmethod
    def _as_aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @classmethod
    def _repair_elapsed_slot_assumptions(
        cls, connection: sqlite3.Connection
    ) -> None:
        """
        Keep only statuses observed before a slot began.

        Booking pages commonly disable elapsed times. A first observation made
        after the start time is therefore unknown, not proof of a reservation.
        """
        rows = connection.execute(
            """
            SELECT id, store_id, theme_name, date, time, status, price,
                   avg_people, expected_revenue, crawled_at,
                   prestart_status, prestart_crawled_at,
                   finalized_status, finalized_at
            FROM reservation_slots
            """
        ).fetchall()
        now_kst = datetime.now(KST)
        updates: list[tuple[str, str, str, str, str, float, int]] = []
        history_rows: list[tuple[object, ...]] = []
        for row in rows:
            try:
                slot_start = cls._slot_start(
                    date.fromisoformat(str(row["date"])),
                    str(row["time"]),
                )
                crawled_at = cls._as_aware(
                    datetime.fromisoformat(str(row["crawled_at"]))
                ).astimezone(KST)
            except (TypeError, ValueError):
                continue

            evidence_status = str(row["prestart_status"] or "")
            evidence_at = str(row["prestart_crawled_at"] or "")
            status = str(row["status"])
            finalized_status = str(row["finalized_status"] or "")
            finalized_at = str(row["finalized_at"] or "")
            if (
                not evidence_status
                and crawled_at < slot_start
                and status in PRESTART_STATUSES
            ):
                evidence_status = status
                evidence_at = str(row["crawled_at"])

            final_status = status
            expected_revenue_multiplier = 1.0
            if slot_start <= now_kst:
                if finalized_status not in PRESTART_STATUSES | {"unknown"}:
                    finalized_status = (
                        evidence_status
                        if evidence_status in PRESTART_STATUSES
                        else "unknown"
                    )
                    finalized_at = now_kst.isoformat()
                final_status = finalized_status
                if final_status != "reserved":
                    expected_revenue_multiplier = 0.0

            history_rows.append(
                (
                    str(row["store_id"]),
                    str(row["theme_name"]),
                    str(row["date"]),
                    str(row["time"]),
                    status,
                    int(crawled_at < slot_start),
                    int(row["price"]),
                    float(row["avg_people"]),
                    float(row["expected_revenue"]),
                    str(row["crawled_at"]),
                )
            )
            updates.append(
                (
                    final_status,
                    evidence_status,
                    evidence_at,
                    finalized_status,
                    finalized_at,
                    expected_revenue_multiplier,
                    int(row["id"]),
                )
            )

        connection.executemany(
            """
            INSERT OR IGNORE INTO reservation_slot_history (
                store_id, theme_name, date, time, observed_status,
                observed_before_start, price, avg_people,
                expected_revenue, crawled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            history_rows,
        )
        connection.executemany(
            """
            UPDATE reservation_slots
            SET status = ?,
                prestart_status = ?,
                prestart_crawled_at = ?,
                finalized_status = ?,
                finalized_at = ?,
                expected_revenue = expected_revenue * ?
            WHERE id = ?
            """,
            updates,
        )

    def sync_stores(self, stores: Sequence[StoreConfig]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        configured_ids = {store.store_id for store in stores}
        with self.connect() as connection:
            # Remove only the built-in tutorial store when switching to the
            # real catalog. User-created stores are never pruned automatically.
            if "sample_store" not in configured_ids:
                connection.execute(
                    "DELETE FROM stores WHERE store_id = 'sample_store'"
                )
            for store in stores:
                connection.execute(
                    """
                    INSERT INTO stores (
                        store_id, store_name, region, booking_url,
                        adapter_type, avg_people, collection_note,
                        address, latitude, longitude, brand_name,
                        brand_logo_url, map_note, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id) DO UPDATE SET
                        store_name = excluded.store_name,
                        region = excluded.region,
                        booking_url = excluded.booking_url,
                        adapter_type = excluded.adapter_type,
                        avg_people = excluded.avg_people,
                        collection_note = excluded.collection_note,
                        address = excluded.address,
                        latitude = excluded.latitude,
                        longitude = excluded.longitude,
                        brand_name = excluded.brand_name,
                        brand_logo_url = excluded.brand_logo_url,
                        map_note = excluded.map_note,
                        updated_at = excluded.updated_at
                    """,
                    (
                        store.store_id,
                        store.store_name,
                        store.region,
                        store.booking_url,
                        store.adapter_type,
                        store.avg_people,
                        store.collection_note,
                        store.address,
                        store.latitude,
                        store.longitude,
                        store.brand_name,
                        store.brand_logo_url,
                        store.map_note,
                        now,
                    ),
                )
                for theme in store.themes:
                    connection.execute(
                        """
                        INSERT INTO themes (
                            store_id, theme_name, genre, price,
                            duration_minutes, price_note, price_source_url,
                            price_verified_at, min_people, max_people,
                            party_prices_json, weekday_party_prices_json,
                            weekend_party_prices_json, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(store_id, theme_name) DO UPDATE SET
                            genre = excluded.genre,
                            price = excluded.price,
                            duration_minutes = excluded.duration_minutes,
                            price_note = excluded.price_note,
                            price_source_url = excluded.price_source_url,
                            price_verified_at = excluded.price_verified_at,
                            min_people = excluded.min_people,
                            max_people = excluded.max_people,
                            party_prices_json = excluded.party_prices_json,
                            weekday_party_prices_json =
                                excluded.weekday_party_prices_json,
                            weekend_party_prices_json =
                                excluded.weekend_party_prices_json,
                            updated_at = excluded.updated_at
                        """,
                        (
                            store.store_id,
                            theme.theme_name,
                            theme.genre,
                            theme.price,
                            theme.duration_minutes,
                            theme.price_note,
                            theme.price_source_url,
                            theme.price_verified_at,
                            theme.min_people,
                            theme.max_people,
                            json.dumps(theme.party_prices, ensure_ascii=False),
                            json.dumps(
                                theme.weekday_party_prices, ensure_ascii=False
                            ),
                            json.dumps(
                                theme.weekend_party_prices, ensure_ascii=False
                            ),
                            now,
                        ),
                    )
                if store.adapter_type in {
                    "keyescape",
                    "naver_booking",
                    "shortstories",
                    "permission_required",
                }:
                    theme_names = [theme.theme_name for theme in store.themes]
                    if theme_names:
                        placeholders = ", ".join("?" for _ in theme_names)
                        connection.execute(
                            f"""
                            DELETE FROM themes
                            WHERE store_id = ?
                              AND theme_name NOT IN ({placeholders})
                            """,
                            (store.store_id, *theme_names),
                        )

    def recalculate_slot_estimates(self) -> int:
        """Apply each store's current average party size to all saved slots."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT rs.id, rs.date, rs.status, rs.price AS slot_price,
                       s.avg_people, t.price, t.min_people,
                       t.party_prices_json, t.weekday_party_prices_json,
                       t.weekend_party_prices_json
                FROM reservation_slots AS rs
                JOIN stores AS s ON s.store_id = rs.store_id
                LEFT JOIN themes AS t
                  ON t.store_id = rs.store_id
                 AND t.theme_name = rs.theme_name
                """
            ).fetchall()
            updates: list[tuple[float, int, float, int]] = []
            for row in rows:
                theme_price = int(row["price"] or 0)
                slot_price = int(row["slot_price"] or 0)
                effective_price = theme_price if theme_price > 0 else slot_price
                booking_value = estimate_booking_value(
                    avg_people=float(row["avg_people"]),
                    target_date=date.fromisoformat(str(row["date"])),
                    price=effective_price,
                    min_people=int(row["min_people"] or 1),
                    party_prices=self._load_price_map(row["party_prices_json"]),
                    weekday_party_prices=self._load_price_map(
                        row["weekday_party_prices_json"]
                    ),
                    weekend_party_prices=self._load_price_map(
                        row["weekend_party_prices_json"]
                    ),
                )
                updates.append(
                    (
                        float(row["avg_people"]),
                        effective_price,
                        booking_value if row["status"] == "reserved" else 0.0,
                        int(row["id"]),
                    )
                )
            connection.executemany(
                """
                UPDATE reservation_slots
                SET avg_people = ?, price = ?, expected_revenue = ?
                WHERE id = ?
                """,
                updates,
            )
            return len(updates)

    @staticmethod
    def _load_price_map(raw_value: object) -> dict[int, int]:
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

    def delete_stores_by_adapter(self, adapter_type: str) -> int:
        """Remove an excluded adapter and all related data via foreign keys."""
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM stores WHERE adapter_type = ?",
                (adapter_type,),
            )
            return int(cursor.rowcount)

    def delete_stores_by_ids(self, store_ids: set[str]) -> int:
        """Remove explicitly retired catalog entries and their related data."""
        if not store_ids:
            return 0
        placeholders = ",".join("?" for _ in store_ids)
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM stores WHERE store_id IN ({placeholders})",
                tuple(sorted(store_ids)),
            )
            return int(cursor.rowcount)

    def upsert_slots(
        self,
        slots: Sequence[ReservationSlot],
        replace_scope: tuple[str, date] | None = None,
    ) -> int:
        if not slots and replace_scope is None:
            return 0

        with self.connect() as connection:
            existing_state: dict[
                tuple[str, str, str, str],
                tuple[str, str, str, str],
            ] = {}
            if replace_scope is not None:
                scope_store_id, scope_date = replace_scope
                if any(
                    slot.store_id != scope_store_id or slot.date != scope_date
                    for slot in slots
                ):
                    raise ValueError(
                        "All replacement slots must match the store/date scope."
                    )
            scope_rows = connection.execute(
                """
                SELECT id, store_id, theme_name, date, time,
                       prestart_status, prestart_crawled_at,
                       finalized_status, finalized_at
                FROM reservation_slots
                WHERE store_id = ? AND date = ?
                """,
                (
                    replace_scope[0],
                    replace_scope[1].isoformat(),
                ),
            ).fetchall() if replace_scope is not None else []
            for row in scope_rows:
                existing_state[
                    (
                        str(row["store_id"]),
                        str(row["theme_name"]),
                        str(row["date"]),
                        str(row["time"]),
                    )
                ] = (
                    str(row["prestart_status"] or ""),
                    str(row["prestart_crawled_at"] or ""),
                    str(row["finalized_status"] or ""),
                    str(row["finalized_at"] or ""),
                )
            if replace_scope is None:
                for slot in slots:
                    row = connection.execute(
                        """
                        SELECT prestart_status, prestart_crawled_at,
                               finalized_status, finalized_at
                        FROM reservation_slots
                        WHERE store_id = ? AND theme_name = ?
                          AND date = ? AND time = ?
                        """,
                        (
                            slot.store_id,
                            slot.theme_name,
                            slot.date.isoformat(),
                            slot.time,
                        ),
                    ).fetchone()
                    if row is not None:
                        existing_state[
                            (
                                slot.store_id,
                                slot.theme_name,
                                slot.date.isoformat(),
                                slot.time,
                            )
                        ] = (
                            str(row["prestart_status"] or ""),
                            str(row["prestart_crawled_at"] or ""),
                            str(row["finalized_status"] or ""),
                            str(row["finalized_at"] or ""),
                        )
            if not slots:
                if replace_scope is not None:
                    now_kst = datetime.now(KST)
                    stale_ids = []
                    for row in scope_rows:
                        evidence_status = str(row["prestart_status"] or "")
                        finalized_status = str(row["finalized_status"] or "")
                        slot_start = self._slot_start(
                            date.fromisoformat(str(row["date"])),
                            str(row["time"]),
                        )
                        if not (
                            slot_start <= now_kst
                            and (
                                evidence_status in PRESTART_STATUSES
                                or finalized_status in PRESTART_STATUSES | {"unknown"}
                            )
                        ):
                            stale_ids.append((int(row["id"]),))
                    connection.executemany(
                        "DELETE FROM reservation_slots WHERE id = ?",
                        stale_ids,
                    )
                return 0

            theme_rows = [
                (
                    slot.store_id,
                    slot.theme_name,
                    slot.genre,
                    slot.price,
                    slot.duration_minutes,
                    slot.price_note,
                    slot.price_source_url,
                    slot.price_verified_at,
                    slot.crawled_at.isoformat(),
                )
                for slot in slots
            ]
            connection.executemany(
                """
                INSERT INTO themes (
                    store_id, theme_name, genre, price, duration_minutes,
                    price_note, price_source_url, price_verified_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, theme_name) DO UPDATE SET
                    genre = CASE
                        WHEN excluded.genre <> '' THEN excluded.genre
                        ELSE themes.genre
                    END,
                    price = CASE
                        WHEN excluded.price > 0 THEN excluded.price
                        ELSE themes.price
                    END,
                    duration_minutes = CASE
                        WHEN excluded.duration_minutes > 0
                        THEN excluded.duration_minutes
                        ELSE themes.duration_minutes
                    END,
                    price_note = CASE
                        WHEN excluded.price_note <> '' THEN excluded.price_note
                        ELSE themes.price_note
                    END,
                    price_source_url = CASE
                        WHEN excluded.price_source_url <> ''
                        THEN excluded.price_source_url
                        ELSE themes.price_source_url
                    END,
                    price_verified_at = CASE
                        WHEN excluded.price_verified_at <> ''
                        THEN excluded.price_verified_at
                        ELSE themes.price_verified_at
                    END,
                    updated_at = excluded.updated_at
                """,
                theme_rows,
            )
            theme_pricing: dict[tuple[str, str], sqlite3.Row] = {}
            for store_id, theme_name in {
                (slot.store_id, slot.theme_name) for slot in slots
            }:
                row = connection.execute(
                    """
                    SELECT price, min_people, party_prices_json,
                           weekday_party_prices_json,
                           weekend_party_prices_json
                    FROM themes
                    WHERE store_id = ? AND theme_name = ?
                    """,
                    (store_id, theme_name),
                ).fetchone()
                if row is not None:
                    theme_pricing[(store_id, theme_name)] = row
            rows = []
            history_rows = []
            for slot in slots:
                key = (
                    slot.store_id,
                    slot.theme_name,
                    slot.date.isoformat(),
                    slot.time,
                )
                (
                    evidence_status,
                    evidence_at,
                    finalized_status,
                    finalized_at,
                ) = existing_state.get(key, ("", "", "", ""))
                crawled_at = self._as_aware(slot.crawled_at)
                slot_start = self._slot_start(slot.date, slot.time)
                observed_before_start = crawled_at.astimezone(KST) < slot_start
                if observed_before_start:
                    final_status = slot.status
                    if slot.status in PRESTART_STATUSES:
                        evidence_status = slot.status
                        evidence_at = crawled_at.isoformat()
                else:
                    if finalized_status not in PRESTART_STATUSES | {"unknown"}:
                        finalized_status = (
                            evidence_status
                            if evidence_status in PRESTART_STATUSES
                            else "unknown"
                        )
                        finalized_at = crawled_at.astimezone(KST).isoformat()
                    final_status = finalized_status
                pricing = theme_pricing.get((slot.store_id, slot.theme_name))
                theme_price = int(pricing["price"] or 0) if pricing else 0
                slot_price = int(slot.price or 0)
                effective_price = theme_price if theme_price > 0 else slot_price
                booking_value = estimate_booking_value(
                    avg_people=slot.avg_people,
                    target_date=slot.date,
                    price=effective_price,
                    min_people=(
                        int(pricing["min_people"] or 1) if pricing else 1
                    ),
                    party_prices=(
                        self._load_price_map(pricing["party_prices_json"])
                        if pricing
                        else {}
                    ),
                    weekday_party_prices=(
                        self._load_price_map(
                            pricing["weekday_party_prices_json"]
                        )
                        if pricing
                        else {}
                    ),
                    weekend_party_prices=(
                        self._load_price_map(
                            pricing["weekend_party_prices_json"]
                        )
                        if pricing
                        else {}
                    ),
                )
                expected_revenue = (
                    booking_value
                    if final_status == "reserved" and booking_value > 0
                    else 0.0
                )
                rows.append(
                    (
                        slot.store_id,
                        slot.theme_name,
                        slot.date.isoformat(),
                        slot.time,
                        final_status,
                        effective_price,
                        slot.avg_people,
                        expected_revenue,
                        crawled_at.isoformat(),
                        evidence_status,
                        evidence_at,
                        finalized_status,
                        finalized_at,
                    )
                )
                history_rows.append(
                    (
                        slot.store_id,
                        slot.theme_name,
                        slot.date.isoformat(),
                        slot.time,
                        slot.status,
                        int(observed_before_start),
                        effective_price,
                        slot.avg_people,
                        (
                            booking_value
                            if slot.status == "reserved"
                            and observed_before_start
                            and booking_value > 0
                            else 0.0
                        ),
                        crawled_at.isoformat(),
                    )
                )
            if replace_scope is not None:
                incoming_keys = {
                    (row[0], row[1], row[2], row[3]) for row in rows
                }
                stale_ids = []
                latest_crawl_kst = max(
                    self._as_aware(slot.crawled_at).astimezone(KST)
                    for slot in slots
                )
                for old_row in connection.execute(
                    """
                    SELECT id, store_id, theme_name, date, time
                    FROM reservation_slots
                    WHERE store_id = ? AND date = ?
                    """,
                    (replace_scope[0], replace_scope[1].isoformat()),
                ).fetchall():
                    old_key = (
                        str(old_row["store_id"]),
                        str(old_row["theme_name"]),
                        str(old_row["date"]),
                        str(old_row["time"]),
                    )
                    if old_key not in incoming_keys:
                        old_state = existing_state.get(
                            old_key, ("", "", "", "")
                        )
                        evidence_status = old_state[0]
                        finalized_status = old_state[2]
                        slot_start = self._slot_start(
                            date.fromisoformat(str(old_row["date"])),
                            str(old_row["time"]),
                        )
                        if not (
                            slot_start <= latest_crawl_kst
                            and (
                                evidence_status in PRESTART_STATUSES
                                or finalized_status in PRESTART_STATUSES | {"unknown"}
                            )
                        ):
                            stale_ids.append((int(old_row["id"]),))
                connection.executemany(
                    "DELETE FROM reservation_slots WHERE id = ?",
                    stale_ids,
                )
            connection.executemany(
                """
                INSERT OR IGNORE INTO reservation_slot_history (
                    store_id, theme_name, date, time, observed_status,
                    observed_before_start, price, avg_people,
                    expected_revenue, crawled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                history_rows,
            )
            connection.executemany(
                """
                INSERT INTO reservation_slots (
                    store_id, theme_name, date, time, status,
                    price, avg_people, expected_revenue, crawled_at,
                    prestart_status, prestart_crawled_at,
                    finalized_status, finalized_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, theme_name, date, time) DO UPDATE SET
                    status = excluded.status,
                    price = excluded.price,
                    avg_people = excluded.avg_people,
                    expected_revenue = excluded.expected_revenue,
                    crawled_at = excluded.crawled_at,
                    prestart_status = excluded.prestart_status,
                    prestart_crawled_at = excluded.prestart_crawled_at,
                    finalized_status = excluded.finalized_status,
                    finalized_at = excluded.finalized_at
                """,
                rows,
            )
        return len(rows)

    def update_theme_price(
        self,
        store_id: str,
        theme_name: str,
        price: int,
        price_note: str,
        price_source_url: str,
        price_verified_at: str,
    ) -> None:
        if price <= 0:
            raise ValueError("A verified theme price must be positive.")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE themes
                SET price = ?,
                    price_note = ?,
                    price_source_url = ?,
                    price_verified_at = ?,
                    updated_at = ?
                WHERE store_id = ? AND theme_name = ?
                """,
                (
                    price,
                    price_note,
                    price_source_url,
                    price_verified_at,
                    now,
                    store_id,
                    theme_name,
                ),
            )
            connection.execute(
                """
                UPDATE reservation_slots
                SET price = ?,
                    expected_revenue = CASE
                        WHEN status = 'reserved' THEN ? * avg_people
                        ELSE 0
                    END
                WHERE store_id = ? AND theme_name = ?
                """,
                (price, price, store_id, theme_name),
            )

    def save_metric_snapshot(
        self,
        *,
        snapshot_date: date,
        scope_label: str,
        store_count: int,
        theme_count: int,
        measured_slots: int,
        reserved_slots: int,
        booking_rate: float,
        period_revenue: float,
        projected_monthly_revenue: float,
        average_store_monthly_revenue: float,
        payload: dict[str, object] | None = None,
        replace: bool = False,
    ) -> None:
        statement = (
            """
            INSERT INTO metric_snapshots (
                snapshot_date, created_at, scope_label, store_count,
                theme_count, measured_slots, reserved_slots, booking_rate,
                period_revenue, projected_monthly_revenue,
                average_store_monthly_revenue, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date, scope_label) DO UPDATE SET
                created_at = excluded.created_at,
                store_count = excluded.store_count,
                theme_count = excluded.theme_count,
                measured_slots = excluded.measured_slots,
                reserved_slots = excluded.reserved_slots,
                booking_rate = excluded.booking_rate,
                period_revenue = excluded.period_revenue,
                projected_monthly_revenue = excluded.projected_monthly_revenue,
                average_store_monthly_revenue =
                    excluded.average_store_monthly_revenue,
                payload_json = excluded.payload_json
            """
            if replace
            else """
            INSERT OR IGNORE INTO metric_snapshots (
                snapshot_date, created_at, scope_label, store_count,
                theme_count, measured_slots, reserved_slots, booking_rate,
                period_revenue, projected_monthly_revenue,
                average_store_monthly_revenue, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )
        with self.connect() as connection:
            connection.execute(
                statement,
                (
                    snapshot_date.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    scope_label.strip() or "전체",
                    int(store_count),
                    int(theme_count),
                    int(measured_slots),
                    int(reserved_slots),
                    float(booking_rate),
                    float(period_revenue),
                    float(projected_monthly_revenue),
                    float(average_store_monthly_revenue),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )

    def load_metric_snapshots(self, limit: int = 180) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT
                    id, snapshot_date, created_at, scope_label, store_count,
                    theme_count, measured_slots, reserved_slots, booking_rate,
                    period_revenue, projected_monthly_revenue,
                    average_store_monthly_revenue, payload_json
                FROM metric_snapshots
                ORDER BY snapshot_date DESC, created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

    def start_crawl_log(self, store_id: str, target_date: date) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crawl_logs (
                    store_id, target_date, started_at, status
                ) VALUES (?, ?, ?, 'running')
                """,
                (
                    store_id,
                    target_date.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def start_crawl_logs(
        self,
        store_id: str,
        target_dates: Sequence[date],
    ) -> dict[date, int]:
        """Create several date logs in one SQLite transaction."""
        started_at = datetime.now(timezone.utc).isoformat()
        log_ids: dict[date, int] = {}
        with self.connect() as connection:
            for target_date in target_dates:
                cursor = connection.execute(
                    """
                    INSERT INTO crawl_logs (
                        store_id, target_date, started_at, status
                    ) VALUES (?, ?, ?, 'running')
                    """,
                    (store_id, target_date.isoformat(), started_at),
                )
                log_ids[target_date] = int(cursor.lastrowid)
        return log_ids

    def finish_crawl_log(
        self,
        log_id: int,
        status: str,
        slots_found: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE crawl_logs
                SET finished_at = ?, status = ?, slots_found = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    slots_found,
                    error_message,
                    log_id,
                ),
            )

    def latest_crawl_started_at(
        self,
        store_id: str,
        target_date: date | None = None,
    ) -> datetime | None:
        date_filter = "AND target_date = ?" if target_date is not None else ""
        parameters: tuple[str, ...] = (
            (store_id, target_date.isoformat())
            if target_date is not None
            else (store_id,)
        )
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT started_at
                FROM crawl_logs
                WHERE store_id = ? AND status = 'success'
                {date_filter}
                ORDER BY started_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(str(row["started_at"]))
