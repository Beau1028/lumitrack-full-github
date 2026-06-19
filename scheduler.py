from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from scraper.config import load_stores
from scraper.database import Database
from scraper.logging_utils import configure_logging
from scraper.runner import build_target_dates, run_crawl

PROJECT_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)


def scheduled_crawl() -> None:
    stores = load_stores(PROJECT_DIR / "stores.yaml")
    database = Database(PROJECT_DIR / "data" / "escape_room.db")
    days = int(os.getenv("CRAWL_DAYS", "1"))
    target_dates = build_target_dates(datetime.now(ZoneInfo("Asia/Seoul")).date(), days)
    summary = run_crawl(
        stores=stores,
        target_dates=target_dates,
        database=database,
        delay_min_seconds=5,
        delay_max_seconds=8,
        minimum_recrawl_minutes=90,
    )
    LOGGER.info("Scheduled crawl summary: %s", summary)


def scheduled_week_crawl() -> None:
    stores = load_stores(PROJECT_DIR / "stores.yaml")
    database = Database(PROJECT_DIR / "data" / "escape_room.db")
    target_dates = build_target_dates(datetime.now(ZoneInfo("Asia/Seoul")).date(), 7)
    summary = run_crawl(
        stores=stores,
        target_dates=target_dates,
        database=database,
        delay_min_seconds=5,
        delay_max_seconds=8,
        minimum_recrawl_minutes=12 * 60,
        max_parallel_origins=8,
    )
    LOGGER.info("Scheduled seven-day crawl summary: %s", summary)


def main() -> None:
    configure_logging(PROJECT_DIR / "logs")
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        scheduled_crawl,
        trigger="interval",
        hours=2,
        id="public-booking-crawl",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=datetime.now(ZoneInfo("Asia/Seoul")),
    )
    scheduler.add_job(
        scheduled_week_crawl,
        trigger="cron",
        hour=3,
        minute=30,
        id="public-booking-seven-day-crawl",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
    LOGGER.info("Scheduler started: every 2 hours (Asia/Seoul)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
