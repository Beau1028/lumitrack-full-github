from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper.adapters import get_adapter
from scraper.config import load_stores
from scraper.database import Database

PROJECT_DIR = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")


class SampleFlowTest(unittest.TestCase):
    def test_batch_fetch_reuses_adapter_for_multiple_dates(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        outcomes = get_adapter(store.adapter_type).fetch_slots_for_dates(
            store,
            [date(2026, 6, 15), date(2026, 6, 16)],
        )

        self.assertFalse(
            any(isinstance(outcome, Exception) for outcome in outcomes.values())
        )
        self.assertEqual(len(outcomes[date(2026, 6, 15)]), 8)
        self.assertEqual(len(outcomes[date(2026, 6, 16)]), 2)

    def test_sample_html_parses_and_upserts(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        slots = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )
        future_date = datetime.now(KST).date() + timedelta(days=365)
        slots = [replace(slot, date=future_date) for slot in slots]
        self.assertEqual(len(slots), 8)
        self.assertEqual(
            {slot.status for slot in slots},
            {"available", "reserved", "closed", "unknown"},
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([store])
            database.upsert_slots(slots)
            database.upsert_slots(slots)
            with database.connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM reservation_slots"
                ).fetchone()[0]
                revenue = connection.execute(
                    "SELECT SUM(expected_revenue) FROM reservation_slots"
                ).fetchone()[0]

        self.assertEqual(count, 8)
        self.assertEqual(revenue, (2 * 35000 * 2.7) + (1 * 32000 * 2.7))

    def test_sync_stores_persists_map_metadata(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        mapped_store = replace(
            store,
            address="서울 강남구 샘플로 1",
            latitude=37.4979,
            longitude=127.0276,
            brand_name="샘플브랜드",
            brand_logo_url="https://example.com/logo.png",
            map_note="공식 주소 확인",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([mapped_store])
            with database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT address, latitude, longitude, brand_name,
                           brand_logo_url, map_note
                    FROM stores
                    WHERE store_id = ?
                    """,
                    (store.store_id,),
                ).fetchone()

        self.assertEqual(row["address"], "서울 강남구 샘플로 1")
        self.assertAlmostEqual(row["latitude"], 37.4979)
        self.assertAlmostEqual(row["longitude"], 127.0276)
        self.assertEqual(row["brand_name"], "샘플브랜드")
        self.assertEqual(row["brand_logo_url"], "https://example.com/logo.png")
        self.assertEqual(row["map_note"], "공식 주소 확인")

    def test_store_locations_file_overrides_map_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            (config_dir / "stores.yaml").write_text(
                (PROJECT_DIR / "stores.sample.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (config_dir / "store_locations.yaml").write_text(
                """
locations:
  - store_id: sample_store
    address: 서울 강남구 정확로 10
    latitude: 37.5001
    longitude: 127.0277
    brand_name: 확인브랜드
    brand_logo_url: https://example.com/verified.png
    map_note: 테스트 확인 좌표
""".lstrip(),
                encoding="utf-8",
            )

            store = load_stores(config_dir / "stores.yaml")[0]

        self.assertEqual(store.address, "서울 강남구 정확로 10")
        self.assertAlmostEqual(store.latitude or 0, 37.5001)
        self.assertAlmostEqual(store.longitude or 0, 127.0277)
        self.assertEqual(store.brand_name, "확인브랜드")
        self.assertEqual(store.brand_logo_url, "https://example.com/verified.png")
        self.assertEqual(store.map_note, "테스트 확인 좌표")

    def test_metric_snapshot_is_saved_once_per_scope_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.save_metric_snapshot(
                snapshot_date=date(2026, 6, 19),
                scope_label="전체",
                store_count=10,
                theme_count=30,
                measured_slots=100,
                reserved_slots=70,
                booking_rate=70.0,
                period_revenue=1_000_000,
                projected_monthly_revenue=30_000_000,
                average_store_monthly_revenue=3_000_000,
                payload={"note": "first"},
                replace=False,
            )
            database.save_metric_snapshot(
                snapshot_date=date(2026, 6, 19),
                scope_label="전체",
                store_count=20,
                theme_count=40,
                measured_slots=200,
                reserved_slots=140,
                booking_rate=70.0,
                period_revenue=2_000_000,
                projected_monthly_revenue=60_000_000,
                average_store_monthly_revenue=3_000_000,
                payload={"note": "ignored"},
                replace=False,
            )
            snapshots = database.load_metric_snapshots()

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["store_count"], 10)

    def test_verified_price_is_not_lost_on_zero_price_crawl(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        slots = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )
        zero_price_slot = replace(
            slots[0],
            price=0,
            expected_revenue=0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([store])
            database.update_theme_price(
                store_id=zero_price_slot.store_id,
                theme_name=zero_price_slot.theme_name,
                price=25000,
                price_note="공개 가격",
                price_source_url="https://example.com",
                price_verified_at="2026-06-12",
            )
            database.upsert_slots([zero_price_slot])
            with database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT price
                    FROM reservation_slots
                    WHERE store_id = ? AND theme_name = ?
                    """,
                    (zero_price_slot.store_id, zero_price_slot.theme_name),
                ).fetchone()

        self.assertEqual(row["price"], 25000)

    def test_replacement_removes_stale_slots_for_same_day(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        slots = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )
        slots = [
            replace(
                slot,
                crawled_at=datetime(2026, 6, 15, 9, 0, tzinfo=KST),
            )
            for slot in slots
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([store])
            database.upsert_slots(slots)
            database.upsert_slots(
                slots[:2],
                replace_scope=(store.store_id, date(2026, 6, 15)),
            )
            with database.connect() as connection:
                count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM reservation_slots
                    WHERE store_id = ? AND date = ?
                    """,
                    (store.store_id, "2026-06-15"),
                ).fetchone()[0]

        self.assertEqual(count, 2)

    def test_first_observation_after_start_is_unknown(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        template = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )[0]
        observed_at = datetime.now(KST)
        elapsed_at = observed_at - timedelta(hours=1)
        elapsed_slot = replace(
            template,
            date=elapsed_at.date(),
            time=elapsed_at.strftime("%H:%M"),
            status="reserved",
            expected_revenue=template.price * template.avg_people,
            crawled_at=observed_at,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([store])
            database.upsert_slots([elapsed_slot])
            with database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT status, expected_revenue, prestart_status
                    FROM reservation_slots
                    """
                ).fetchone()

        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["expected_revenue"], 0)
        self.assertEqual(row["prestart_status"], "")

    def test_prestart_available_does_not_become_reserved_after_start(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        template = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )[0]
        slot_date = datetime.now(KST).date() - timedelta(days=1)
        slot_start = datetime.combine(slot_date, time(12, 0), tzinfo=KST)
        before_start = replace(
            template,
            date=slot_date,
            time="12:00",
            status="available",
            expected_revenue=0,
            crawled_at=slot_start - timedelta(hours=1),
        )
        after_start = replace(
            before_start,
            status="reserved",
            expected_revenue=template.price * template.avg_people,
            crawled_at=slot_start + timedelta(hours=1),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([store])
            database.upsert_slots([before_start])
            database.upsert_slots(
                [after_start],
                replace_scope=(store.store_id, slot_date),
            )
            with database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT status, expected_revenue, prestart_status
                    FROM reservation_slots
                    """
                ).fetchone()

        self.assertEqual(row["status"], "available")
        self.assertEqual(row["expected_revenue"], 0)
        self.assertEqual(row["prestart_status"], "available")

    def test_prestart_reserved_is_finalized_and_history_is_kept(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        template = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )[0]
        slot_date = datetime.now(KST).date() - timedelta(days=1)
        slot_start = datetime.combine(slot_date, time(12, 0), tzinfo=KST)
        before_start = replace(
            template,
            date=slot_date,
            time="12:00",
            status="reserved",
            expected_revenue=template.price * template.avg_people,
            crawled_at=slot_start - timedelta(hours=1),
        )
        after_start = replace(
            before_start,
            status="unknown",
            expected_revenue=0,
            crawled_at=slot_start + timedelta(hours=1),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([store])
            database.upsert_slots([before_start])
            database.upsert_slots([after_start])
            database.initialize()
            with database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT status, expected_revenue, prestart_status,
                           finalized_status, finalized_at
                    FROM reservation_slots
                    """
                ).fetchone()
                history = connection.execute(
                    """
                    SELECT observed_status, observed_before_start
                    FROM reservation_slot_history
                    ORDER BY crawled_at
                    """
                ).fetchall()

        self.assertEqual(row["status"], "reserved")
        self.assertEqual(row["expected_revenue"], template.price * template.avg_people)
        self.assertEqual(row["prestart_status"], "reserved")
        self.assertEqual(row["finalized_status"], "reserved")
        self.assertTrue(row["finalized_at"])
        self.assertEqual(
            [(item["observed_status"], item["observed_before_start"]) for item in history],
            [("reserved", 1), ("unknown", 0)],
        )

    def test_recalculate_slot_estimates_uses_latest_store_average(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        slots = get_adapter(store.adapter_type).fetch_slots(
            store, date(2026, 6, 15)
        )
        future_date = datetime.now(KST).date() + timedelta(days=365)
        slots = [replace(slot, date=future_date) for slot in slots]

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([replace(store, avg_people=3.0)])
            database.upsert_slots(slots)
            database.sync_stores([store])
            updated = database.recalculate_slot_estimates()
            with database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT avg_people, expected_revenue
                    FROM reservation_slots
                    WHERE status = 'reserved' AND price = 35000
                    LIMIT 1
                    """
                ).fetchone()

        self.assertEqual(updated, len(slots))
        self.assertEqual(row["avg_people"], 2.7)
        self.assertEqual(row["expected_revenue"], 35000 * 2.7)

    def test_database_uses_party_total_instead_of_flat_per_person_price(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        tiered_theme = replace(
            store.themes[0],
            price=0,
            party_prices={2: 46_000, 3: 66_000},
        )
        tiered_store = replace(
            store,
            themes=(tiered_theme, *store.themes[1:]),
        )
        slot = replace(
            next(
                item
                for item in get_adapter(store.adapter_type).fetch_slots(
                    store, date(2026, 6, 15)
                )
                if item.theme_name == tiered_theme.theme_name
                and item.status == "reserved"
            ),
            date=datetime.now(KST).date() + timedelta(days=365),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([tiered_store])
            database.upsert_slots([slot])
            with database.connect() as connection:
                revenue = connection.execute(
                    "SELECT expected_revenue FROM reservation_slots"
                ).fetchone()[0]

        self.assertEqual(revenue, 60_000)

    def test_delete_stores_by_adapter_removes_related_data(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        fallback_store = replace(store, adapter_type="masterkey")

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([fallback_store])
            deleted = database.delete_stores_by_adapter("masterkey")
            with database.connect() as connection:
                store_count = connection.execute(
                    "SELECT COUNT(*) FROM stores"
                ).fetchone()[0]
                theme_count = connection.execute(
                    "SELECT COUNT(*) FROM themes"
                ).fetchone()[0]

        self.assertEqual(deleted, 1)
        self.assertEqual(store_count, 0)
        self.assertEqual(theme_count, 0)

    def test_delete_stores_by_ids_removes_retired_placeholders(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        retired = replace(
            store,
            store_id="murderparker_policy",
            adapter_type="limited",
        )
        active = replace(
            store,
            store_id="active_store",
            adapter_type="generic",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            database.sync_stores([retired, active])
            deleted = database.delete_stores_by_ids(
                {"murderparker_policy", "goldenkey_policy"}
            )
            with database.connect() as connection:
                rows = connection.execute(
                    "SELECT store_id FROM stores ORDER BY store_id"
                ).fetchall()

        self.assertEqual(deleted, 1)
        self.assertEqual(
            [row["store_id"] for row in rows],
            ["active_store"],
        )

    def test_sync_prunes_retired_managed_catalog_themes(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        for adapter_type in ("keyescape", "permission_required"):
            with self.subTest(adapter_type=adapter_type):
                old_store = replace(
                    store,
                    adapter_type=adapter_type,
                    themes=(
                        replace(store.themes[0], theme_name="예전 테마"),
                        store.themes[0],
                    ),
                )
                current_store = replace(
                    store,
                    adapter_type=adapter_type,
                    themes=(store.themes[0],),
                )

                with tempfile.TemporaryDirectory() as temp_dir:
                    database = Database(Path(temp_dir) / "test.db")
                    database.initialize()
                    database.sync_stores([old_store])
                    database.sync_stores([current_store])
                    with database.connect() as connection:
                        names = [
                            row["theme_name"]
                            for row in connection.execute(
                                "SELECT theme_name FROM themes ORDER BY theme_name"
                            )
                        ]

                self.assertEqual(names, [store.themes[0].theme_name])


if __name__ == "__main__":
    unittest.main()
