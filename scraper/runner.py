from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import logging
from queue import Empty, Queue
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from scraper.adapters import get_adapter
from scraper.config import load_stores
from scraper.database import Database
from scraper.logging_utils import configure_logging
from scraper.models import StoreConfig

LOGGER = logging.getLogger(__name__)
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
CRAWL_SKIP_STORE_IDS = {
    # These public pages repeatedly time out from the Hetzner server. Keep them
    # registered for catalog/revenue context, but do not let them stall 7-day
    # collection jobs.
    "imaginary_door_daehangno",
    "imaginary_door_seohyeon",
    "imaginary_door_gwangju",
    "imaginary_door_suwon",
    "imaginary_door_bupyeong",
    "imaginary_door_suwon2",
    "frank_gangnam",
}
KST = ZoneInfo("Asia/Seoul")
MAX_PARALLEL_ORIGINS = 8
ProgressCallback = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class CrawlTask:
    store: StoreConfig
    pending_dates: tuple[date, ...]
    log_ids: dict[date, int]


def store_origin(store: StoreConfig) -> str:
    """Return the request origin used to serialize stores on the same site."""
    parsed = urlparse(store.booking_url)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return f"{parsed.scheme}://{parsed.hostname.casefold()}"
    return store.booking_url


def _fetch_origin_group(
    tasks: Sequence[CrawlTask],
    delay_min_seconds: float,
    delay_max_seconds: float,
    result_queue: Queue[tuple[CrawlTask, bool, dict[date, object]]],
    max_navigation_timeout_ms: int | None = None,
) -> None:
    """Fetch one origin sequentially; separate origins may run in parallel."""
    task_adapters = []
    for task in tasks:
        try:
            adapter = get_adapter(task.store.adapter_type)
            if max_navigation_timeout_ms:
                adapter.navigation_timeout_ms = min(
                    int(adapter.navigation_timeout_ms),
                    int(max_navigation_timeout_ms),
                )
            task_adapters.append((task, adapter))
        except Exception as exc:
            result_queue.put(
                (
                    task,
                    False,
                    {
                        target_date: exc
                        for target_date in task.pending_dates
                    },
                )
            )

    can_share_browser = bool(task_adapters) and all(
        hasattr(adapter, "launch_browser")
        and hasattr(adapter, "fetch_slots_for_dates_in_browser")
        for _, adapter in task_adapters
    )

    def fetch_all(shared_browser: object | None = None) -> None:
        for task_index, (task, adapter) in enumerate(task_adapters):
            LOGGER.info(
                "Crawling %s for %s dates in a reused origin browser",
                task.store.store_id,
                len(task.pending_dates),
            )
            try:
                if shared_browser is not None:
                    outcomes = adapter.fetch_slots_for_dates_in_browser(
                        task.store,
                        list(task.pending_dates),
                        shared_browser,
                    )
                else:
                    outcomes = adapter.fetch_slots_for_dates(
                        task.store,
                        list(task.pending_dates),
                    )
            except Exception as exc:
                outcomes = {
                    target_date: exc for target_date in task.pending_dates
                }
            result_queue.put(
                (task, adapter.preserve_existing_on_empty, outcomes)
            )

            if (
                task_index < len(task_adapters) - 1
                and task.store.booking_url.startswith(("http://", "https://"))
            ):
                delay = random.uniform(delay_min_seconds, delay_max_seconds)
                LOGGER.info(
                    "Waiting %.1f seconds before the next store on %s",
                    delay,
                    store_origin(task.store),
                )
                time.sleep(delay)

    if not can_share_browser:
        fetch_all()
        return

    with sync_playwright() as playwright:
        try:
            browser = task_adapters[0][1].launch_browser(playwright)
        except Exception:
            LOGGER.exception(
                "Shared browser launch failed for %s; using store sessions",
                store_origin(tasks[0].store),
            )
            fetch_all()
            return
        try:
            fetch_all(browser)
        finally:
            browser.close()


def _emit_progress(
    callback: ProgressCallback | None,
    event: dict[str, object],
) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        LOGGER.exception("Progress callback failed")


def build_target_dates(start_date: date, days: int) -> list[date]:
    if days <= 0:
        raise ValueError("--days must be at least 1.")
    return [start_date + timedelta(days=offset) for offset in range(days)]


def recently_crawled(
    database: Database,
    store_id: str,
    target_date: date,
    minimum_minutes: int,
) -> bool:
    if minimum_minutes <= 0:
        return False
    latest = database.latest_crawl_started_at(store_id, target_date)
    if latest is None:
        return False
    elapsed = datetime.now(timezone.utc) - latest.astimezone(timezone.utc)
    return elapsed < timedelta(minutes=minimum_minutes)


def run_crawl(
    stores: Sequence[StoreConfig],
    target_dates: Sequence[date],
    database: Database,
    delay_min_seconds: float = 5.0,
    delay_max_seconds: float = 6.0,
    minimum_recrawl_minutes: int = 0,
    max_parallel_origins: int = 8,
    max_navigation_timeout_ms: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, int]:
    if delay_min_seconds < 0 or delay_max_seconds < delay_min_seconds:
        raise ValueError("Invalid delay range.")
    managed_stores = [
        store for store in stores
        if store.store_id not in RETIRED_STORE_IDS
    ]
    has_remote_store = any(
        store.adapter_type not in NON_CRAWLING_ADAPTERS
        and store.booking_url.startswith(("http://", "https://"))
        for store in managed_stores
    )
    if has_remote_store and delay_min_seconds < 5:
        raise ValueError("Public website requests require at least a 5-second delay.")
    if not 1 <= max_parallel_origins <= MAX_PARALLEL_ORIGINS:
        raise ValueError(
            f"max_parallel_origins must be between 1 and "
            f"{MAX_PARALLEL_ORIGINS}."
        )
    today_kst = datetime.now(KST).date()
    if any(target_date < today_kst for target_date in target_dates):
        raise ValueError(
            "Past dates cannot be crawled reliably because elapsed slots may be hidden."
        )

    database.initialize()
    database.delete_stores_by_adapter("masterkey")
    database.delete_stores_by_adapter("sherlock")
    database.delete_stores_by_ids(RETIRED_STORE_IDS)
    database.sync_stores(managed_stores)
    database.recalculate_slot_estimates()
    summary = {"success": 0, "failed": 0, "slots": 0, "skipped": 0}
    active_stores = [
        store for store in managed_stores
        if store.adapter_type not in NON_CRAWLING_ADAPTERS
        and store.store_id not in CRAWL_SKIP_STORE_IDS
    ]
    summary["skipped"] = (
        len(stores) - len(active_stores)
    ) * len(target_dates)
    total_units = len(active_stores) * len(target_dates)
    completed_units = 0
    stores_completed = 0
    tasks: list[CrawlTask] = []
    for store in active_stores:
        pending_dates: list[date] = []
        for target_date in target_dates:
            if recently_crawled(
                database,
                store.store_id,
                target_date,
                minimum_recrawl_minutes,
            ):
                LOGGER.info(
                    "Skipping %s / %s: crawled within %s minutes",
                    store.store_id,
                    target_date,
                    minimum_recrawl_minutes,
                )
                summary["skipped"] += 1
                completed_units += 1
                continue
            pending_dates.append(target_date)

        if not pending_dates:
            stores_completed += 1
            continue

        tasks.append(
            CrawlTask(
                store=store,
                pending_dates=tuple(pending_dates),
                log_ids=database.start_crawl_logs(
                    store.store_id,
                    pending_dates,
                ),
            )
        )

    origin_groups: dict[str, list[CrawlTask]] = {}
    for task in tasks:
        origin_groups.setdefault(store_origin(task.store), []).append(task)
    worker_count = min(max_parallel_origins, len(origin_groups))
    LOGGER.info(
        "Fetching %s stores across %s origins with %s parallel workers",
        len(tasks),
        len(origin_groups),
        worker_count,
    )
    _emit_progress(
        progress_callback,
        {
            "phase": "start",
            "completed": completed_units,
            "total": total_units,
            "stores_completed": stores_completed,
            "stores_total": len(active_stores),
            "current_store": "",
            "current_date": "",
            "success": summary["success"],
            "failed": summary["failed"],
            "slots": summary["slots"],
        },
    )

    result_queue: Queue[
        tuple[CrawlTask, bool, dict[date, object]]
    ] = Queue()

    def process_task_result(
        task: CrawlTask,
        preserve_existing_on_empty: bool,
        outcomes: dict[date, object],
    ) -> None:
        nonlocal completed_units, stores_completed
        for target_date in task.pending_dates:
            log_id = task.log_ids[target_date]
            outcome = outcomes.get(
                target_date,
                RuntimeError(
                    "Adapter returned no result for target date."
                ),
            )
            if isinstance(outcome, Exception):
                summary["failed"] += 1
                database.finish_crawl_log(
                    log_id,
                    "failed",
                    error_message=f"{type(outcome).__name__}: {outcome}",
                )
                LOGGER.error(
                    "Crawl failed for %s on %s: %s",
                    task.store.store_id,
                    target_date,
                    outcome,
                )
            else:
                try:
                    slots = outcome
                    replace_scope = (
                        task.store.store_id,
                        target_date,
                    )
                    if not slots and preserve_existing_on_empty:
                        replace_scope = None
                    saved = database.upsert_slots(
                        slots,
                        replace_scope=replace_scope,
                    )
                    database.finish_crawl_log(
                        log_id,
                        "success",
                        slots_found=saved,
                    )
                    summary["success"] += 1
                    summary["slots"] += saved
                    LOGGER.info(
                        "Saved %s slots for %s on %s",
                        saved,
                        task.store.store_id,
                        target_date,
                    )
                except Exception as exc:
                    summary["failed"] += 1
                    database.finish_crawl_log(
                        log_id,
                        "failed",
                        error_message=f"{type(exc).__name__}: {exc}",
                    )
                    LOGGER.exception(
                        "Saving crawl failed for %s on %s",
                        task.store.store_id,
                        target_date,
                    )
            completed_units += 1
            _emit_progress(
                progress_callback,
                {
                    "phase": "running",
                    "completed": completed_units,
                    "total": total_units,
                    "stores_completed": stores_completed,
                    "stores_total": len(active_stores),
                    "current_store": task.store.store_name,
                    "current_date": target_date.isoformat(),
                    "success": summary["success"],
                    "failed": summary["failed"],
                    "slots": summary["slots"],
                },
            )
        stores_completed += 1

    with ThreadPoolExecutor(
        max_workers=max(worker_count, 1),
        thread_name_prefix="crawl-origin",
    ) as executor:
        future_groups = {
            executor.submit(
                _fetch_origin_group,
                group,
                delay_min_seconds,
                delay_max_seconds,
                result_queue,
                max_navigation_timeout_ms,
            ): group
            for group in origin_groups.values()
        }

        processed_store_ids: set[str] = set()
        while len(processed_store_ids) < len(tasks):
            try:
                task, preserve_existing, outcomes = result_queue.get(
                    timeout=0.2
                )
            except Empty:
                if all(future.done() for future in future_groups):
                    break
                continue
            if task.store.store_id in processed_store_ids:
                continue
            process_task_result(task, preserve_existing, outcomes)
            processed_store_ids.add(task.store.store_id)

        for future in as_completed(future_groups):
            try:
                future.result()
            except Exception:
                LOGGER.exception("Origin worker failed")

        for task in tasks:
            if task.store.store_id in processed_store_ids:
                continue
            error = RuntimeError("Origin worker ended without a result.")
            process_task_result(
                task,
                False,
                {target_date: error for target_date in task.pending_dates},
            )

    _emit_progress(
        progress_callback,
        {
            "phase": "complete",
            "completed": total_units,
            "total": total_units,
            "stores_completed": len(active_stores),
            "stores_total": len(active_stores),
            "current_store": "",
            "current_date": "",
            "success": summary["success"],
            "failed": summary["failed"],
            "slots": summary["slots"],
        },
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl public escape-room booking slots."
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Single target date in YYYY-MM-DD format.",
    )
    date_group.add_argument(
        "--days",
        type=int,
        help="Number of days to crawl from today (default: 1).",
    )
    parser.add_argument("--config", default="stores.yaml")
    parser.add_argument("--db", default="data/escape_room.db")
    parser.add_argument(
        "--store-id",
        action="append",
        default=[],
        help="Crawl only this store_id. Repeat for multiple stores.",
    )
    parser.add_argument(
        "--delay-min", type=float, default=5.0, help="Minimum request delay."
    )
    parser.add_argument(
        "--delay-max", type=float, default=6.0, help="Maximum request delay."
    )
    parser.add_argument(
        "--minimum-recrawl-minutes",
        type=int,
        default=0,
        help="Skip a store/date crawled within this many minutes.",
    )
    parser.add_argument(
        "--parallel-origins",
        type=int,
        default=4,
        help="Parallel public-site origins (1-8, default: 4).",
    )
    parser.add_argument(
        "--max-navigation-timeout-ms",
        type=int,
        default=None,
        help="Optional cap for each adapter navigation timeout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging()
    stores = load_stores(Path(args.config))
    if args.store_id:
        selected_store_ids = set(args.store_id)
        stores = [
            store for store in stores
            if store.store_id in selected_store_ids
        ]
        missing = selected_store_ids - {store.store_id for store in stores}
        if missing:
            raise ValueError(
                "Unknown store_id: " + ", ".join(sorted(missing))
            )
    target_dates = (
        [args.date]
        if args.date
        else build_target_dates(datetime.now(KST).date(), args.days or 1)
    )
    database = Database(args.db)
    summary = run_crawl(
        stores=stores,
        target_dates=target_dates,
        database=database,
        delay_min_seconds=args.delay_min,
        delay_max_seconds=args.delay_max,
        minimum_recrawl_minutes=args.minimum_recrawl_minutes,
        max_parallel_origins=args.parallel_origins,
        max_navigation_timeout_ms=args.max_navigation_timeout_ms,
    )
    LOGGER.info("Crawl summary: %s", summary)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
