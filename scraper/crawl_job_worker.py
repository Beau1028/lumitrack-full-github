from __future__ import annotations

import argparse
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scraper.config import load_stores
from scraper.crawl_jobs import read_job_file, update_job_file, utc_now
from scraper.database import Database
from scraper.logging_utils import configure_logging
from scraper.runner import NON_CRAWLING_ADAPTERS, RETIRED_STORE_IDS, KST, run_crawl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a LumiTrack crawl as a background job."
    )
    parser.add_argument("--job-file", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--store-id", action="append", default=[])
    parser.add_argument("--delay-min", type=float, default=5.0)
    parser.add_argument("--delay-max", type=float, default=8.0)
    parser.add_argument("--parallel-origins", type=int, default=4)
    parser.add_argument("--max-navigation-timeout-ms", type=int, default=15_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job_file = Path(args.job_file)
    configure_logging(Path(args.db).parent.parent / "logs")
    selected_ids = set(args.store_id or [])

    def set_status(**updates: object) -> None:
        update_job_file(job_file, **updates)

    try:
        set_status(
            status="running",
            progress={
                "phase": "loading",
                "completed": 0,
                "total": 1,
                "stores_completed": 0,
                "stores_total": 0,
                "current_store": "매장 설정을 읽는 중",
                "current_date": "",
                "success": 0,
                "failed": 0,
                "slots": 0,
            },
        )
        stores = [
            store
            for store in load_stores(args.config)
            if store.store_id not in RETIRED_STORE_IDS
            and store.adapter_type not in NON_CRAWLING_ADAPTERS
            and (not selected_ids or store.store_id in selected_ids)
        ]
        today = datetime.now(KST).date()
        target_dates = [
            today + timedelta(days=offset)
            for offset in range(args.days)
        ]

        current = read_job_file(job_file) or {}
        current.update(
            {
                "status": "running",
                "updated_at": utc_now(),
                "store_count": len(stores),
                "target_dates": [target_date.isoformat() for target_date in target_dates],
                "progress": {
                    "phase": "prepared",
                    "completed": 0,
                    "total": max(len(stores) * len(target_dates), 1),
                    "stores_completed": 0,
                    "stores_total": len(stores),
                    "current_store": "수집 준비 완료",
                    "current_date": f"{args.days}일",
                    "success": 0,
                    "failed": 0,
                    "slots": 0,
                },
            }
        )
        update_job_file(job_file, **current)

        def progress_callback(event: dict[str, object]) -> None:
            update_job_file(
                job_file,
                status="running",
                progress=event,
            )

        summary = run_crawl(
            stores=stores,
            target_dates=target_dates,
            database=Database(args.db),
            delay_min_seconds=args.delay_min,
            delay_max_seconds=args.delay_max,
            minimum_recrawl_minutes=0,
            max_parallel_origins=args.parallel_origins,
            max_navigation_timeout_ms=args.max_navigation_timeout_ms,
            progress_callback=progress_callback,
        )
        final_status = "success" if not summary.get("failed") else "partial_success"
        finished_units = (
            int(summary.get("success", 0) or 0)
            + int(summary.get("failed", 0) or 0)
            + int(summary.get("skipped", 0) or 0)
        )
        set_status(
            status=final_status,
            summary=summary,
            progress={
                "phase": "complete",
                "completed": finished_units,
                "total": finished_units,
                "stores_completed": len(stores),
                "stores_total": len(stores),
                "current_store": "",
                "current_date": "",
                "success": int(summary.get("success", 0)),
                "failed": int(summary.get("failed", 0)),
                "slots": int(summary.get("slots", 0)),
            },
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return 0 if final_status == "success" else 1
    except Exception as exc:
        set_status(
            status="failed",
            error="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
