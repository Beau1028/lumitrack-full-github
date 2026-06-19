from __future__ import annotations

import argparse
import logging
import random
import re
import time
from datetime import date
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright

from scraper.adapters.base_adapter import USER_AGENT
from scraper.config import find_store, load_stores
from scraper.database import Database
from scraper.logging_utils import configure_logging

LOGGER = logging.getLogger(__name__)
TOTAL_PRICE_PATTERN = re.compile(r"요금\s*([\d,]+)\s*원")


def build_date_url(url: str, target_date: date) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["rev_days"] = target_date.isoformat()
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def refresh_xdungeon_prices(
    database: Database,
    stores_path: str,
    target_date: date,
    store_id: str | None = None,
) -> dict[str, int]:
    stores = [
        store
        for store in load_stores(stores_path)
        if store.adapter_type == "xdungeon"
    ]
    if store_id:
        selected = find_store(stores, store_id)
        stores = [selected]

    database.initialize()
    database.sync_stores(stores)
    result = {"stores": 0, "themes": 0, "failed": 0}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
        page = context.new_page()
        page.set_default_timeout(30_000)
        try:
            for store in stores:
                if result["stores"] > 0:
                    time.sleep(random.uniform(5, 8))
                page.goto(
                    build_date_url(store.booking_url, target_date),
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                result["stores"] += 1
                candidates: list[tuple[str, str]] = []
                for box in page.query_selector_all(".thm_box .box"):
                    title = box.query_selector(".img_box .tit")
                    link = box.query_selector(".time_box li:not(.dead) a[href]")
                    theme_name = " ".join(
                        (title.inner_text() if title else "").split()
                    )
                    href = link.get_attribute("href") if link else None
                    if theme_name and href:
                        candidates.append((theme_name, urljoin(page.url, href)))

                for theme_name, detail_url in candidates:
                    time.sleep(random.uniform(5, 8))
                    try:
                        page.goto(
                            detail_url,
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        person_select = page.query_selector("select[name=person]")
                        if person_select is None:
                            raise ValueError("Person selector was not found.")
                        people = max(2, round(store.avg_people))
                        person_select.select_option(str(people))
                        page.wait_for_timeout(200)
                        body_text = page.locator("body").inner_text()
                        match = TOTAL_PRICE_PATTERN.search(body_text)
                        if not match:
                            raise ValueError("Displayed price was not found.")
                        total = int(match.group(1).replace(",", ""))
                        per_person = round(total / people)
                        database.update_theme_price(
                            store_id=store.store_id,
                            theme_name=theme_name,
                            price=per_person,
                            price_note=(
                                f"{people}명 공개 표시 총액 "
                                f"{total:,}원 기준"
                            ),
                            price_source_url=store.booking_url,
                            price_verified_at=date.today().isoformat(),
                        )
                        result["themes"] += 1
                        LOGGER.info(
                            "%s / %s: %s원/인",
                            store.store_name,
                            theme_name,
                            per_person,
                        )
                    except Exception:
                        result["failed"] += 1
                        LOGGER.exception(
                            "Price refresh failed: %s / %s",
                            store.store_id,
                            theme_name,
                        )
        finally:
            context.close()
            browser.close()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh prices shown on public booking input pages."
    )
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--store_id")
    parser.add_argument("--config", default="stores.yaml")
    parser.add_argument("--db", default="data/escape_room.db")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging()
    result = refresh_xdungeon_prices(
        database=Database(args.db),
        stores_path=args.config,
        target_date=args.date,
        store_id=args.store_id,
    )
    LOGGER.info("Price refresh summary: %s", result)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
