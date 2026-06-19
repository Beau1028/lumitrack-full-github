from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scraper.database import Database
from scraper.models import StoreConfig
from scraper.runner import RETIRED_STORE_IDS, run_crawl

KST = ZoneInfo("Asia/Seoul")


def store(store_id: str, booking_url: str) -> StoreConfig:
    return StoreConfig(
        store_id=store_id,
        store_name=store_id,
        region="서울",
        booking_url=booking_url,
        adapter_type="fake",
        avg_people=2.7,
    )


class RunnerTest(unittest.TestCase):
    def test_retired_store_ids_are_not_synced_or_fetched(self) -> None:
        called = False
        retired_id = next(iter(RETIRED_STORE_IDS))

        class Adapter:
            preserve_existing_on_empty = False

            def fetch_slots_for_dates(self, store_config, target_dates):
                nonlocal called
                called = True
                return {target_date: [] for target_date in target_dates}

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            with patch("scraper.runner.get_adapter", return_value=Adapter()):
                summary = run_crawl(
                    stores=[store(retired_id, "https://retired.example")],
                    target_dates=[datetime.now(KST).date()],
                    database=database,
                    delay_min_seconds=5,
                    delay_max_seconds=5,
                )
            with database.connect() as connection:
                saved = connection.execute(
                    "SELECT COUNT(*) FROM stores WHERE store_id = ?",
                    (retired_id,),
                ).fetchone()[0]

        self.assertFalse(called)
        self.assertEqual(saved, 0)
        self.assertEqual(summary["skipped"], 1)

    def test_different_origins_fetch_in_parallel(self) -> None:
        barrier = threading.Barrier(2)

        class ParallelAdapter:
            preserve_existing_on_empty = False

            def fetch_slots_for_dates(self, store_config, target_dates):
                del store_config
                barrier.wait(timeout=2)
                return {target_date: [] for target_date in target_dates}

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            with patch(
                "scraper.runner.get_adapter",
                return_value=ParallelAdapter(),
            ):
                summary = run_crawl(
                    stores=[
                        store("a", "https://one.example/reservation"),
                        store("b", "https://two.example/reservation"),
                    ],
                    target_dates=[datetime.now(KST).date()],
                    database=database,
                    delay_min_seconds=5,
                    delay_max_seconds=5,
                    max_parallel_origins=2,
                )

        self.assertEqual(summary["success"], 2)
        self.assertEqual(summary["failed"], 0)

    def test_same_origin_is_never_fetched_concurrently(self) -> None:
        lock = threading.Lock()
        active = 0
        maximum_active = 0

        class SerialAdapter:
            preserve_existing_on_empty = False

            def fetch_slots_for_dates(self, store_config, target_dates):
                nonlocal active, maximum_active
                del store_config
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return {target_date: [] for target_date in target_dates}

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            with (
                patch(
                    "scraper.runner.get_adapter",
                    return_value=SerialAdapter(),
                ),
                patch("scraper.runner.time.sleep", return_value=None),
            ):
                summary = run_crawl(
                    stores=[
                        store("a", "https://same.example/branch/1"),
                        store("b", "https://same.example/branch/2"),
                    ],
                    target_dates=[datetime.now(KST).date()],
                    database=database,
                    delay_min_seconds=5,
                    delay_max_seconds=5,
                    max_parallel_origins=4,
                )

        self.assertEqual(summary["success"], 2)
        self.assertEqual(maximum_active, 1)

    def test_progress_reports_each_store_date(self) -> None:
        events: list[dict[str, object]] = []

        class ProgressAdapter:
            preserve_existing_on_empty = False

            def fetch_slots_for_dates(self, store_config, target_dates):
                del store_config
                return {target_date: [] for target_date in target_dates}

        today = datetime.now(KST).date()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            with patch(
                "scraper.runner.get_adapter",
                return_value=ProgressAdapter(),
            ):
                run_crawl(
                    stores=[store("a", "https://one.example/reservation")],
                    target_dates=[today, today + timedelta(days=1)],
                    database=database,
                    delay_min_seconds=5,
                    delay_max_seconds=5,
                    progress_callback=events.append,
                )

        self.assertEqual(events[0]["phase"], "start")
        self.assertEqual(events[-1]["phase"], "complete")
        running = [event for event in events if event["phase"] == "running"]
        self.assertEqual(len(running), 2)
        self.assertEqual(running[-1]["completed"], 2)
        self.assertEqual(running[-1]["total"], 2)

    def test_same_origin_reuses_one_browser(self) -> None:
        launches = 0
        fetches = 0
        browser = object()

        class ReusedAdapter:
            preserve_existing_on_empty = False

            def launch_browser(self, playwright):
                nonlocal launches
                del playwright
                launches += 1
                return browser

            def fetch_slots_for_dates_in_browser(
                self, store_config, target_dates, shared_browser
            ):
                nonlocal fetches
                del store_config
                self.assert_browser(shared_browser)
                fetches += 1
                return {target_date: [] for target_date in target_dates}

            @staticmethod
            def assert_browser(shared_browser):
                if shared_browser is not browser:
                    raise AssertionError("browser was not reused")

        class PlaywrightContext:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, traceback):
                return False

        class BrowserWithClose:
            def close(self):
                return None

        shared = BrowserWithClose()
        adapter = ReusedAdapter()
        browser = shared
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            with (
                patch("scraper.runner.get_adapter", return_value=adapter),
                patch(
                    "scraper.runner.sync_playwright",
                    return_value=PlaywrightContext(),
                ),
                patch("scraper.runner.time.sleep", return_value=None),
            ):
                summary = run_crawl(
                    stores=[
                        store("a", "https://same.example/branch/1"),
                        store("b", "https://same.example/branch/2"),
                    ],
                    target_dates=[datetime.now(KST).date()],
                    database=database,
                    delay_min_seconds=5,
                    delay_max_seconds=5,
                )

        self.assertEqual(summary["success"], 2)
        self.assertEqual(launches, 1)
        self.assertEqual(fetches, 2)


if __name__ == "__main__":
    unittest.main()
