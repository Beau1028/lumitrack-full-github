from __future__ import annotations

import re
from datetime import date, datetime, timezone

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3])\s*시\s*([0-5]\d)\s*분")


class FrankAdapter(BaseAdapter):
    """Read each theme tab on Frank's public reservation screen."""

    ignore_https_errors = True

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        self._select_date(page, target_date)
        page.locator("#theme_area a").first.wait_for(state="attached")
        page.locator("#theme_time_area a").first.wait_for(state="attached")
        collected: dict[tuple[str, str], ReservationSlot] = {}
        theme_links = page.locator("#theme_area a")
        theme_count = theme_links.count()
        for index in range(theme_count):
            theme_links = page.locator("#theme_area a")
            link = theme_links.nth(index)
            theme_name = " ".join((link.inner_text() or "").split())
            if not theme_name:
                continue
            if index > 0:
                link.click()
                page.wait_for_function(
                    "() => document.querySelectorAll('#theme_time_area a').length > 0"
                )
                page.wait_for_timeout(500)
            configured = store_config.theme_by_name(theme_name)
            price = configured.price if configured else 0
            times = page.locator("#theme_time_area a")
            for time_index in range(times.count()):
                entry = times.nth(time_index)
                text = " ".join((entry.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                classes = entry.get_attribute("class") or ""
                status = "reserved" if "none" in classes.split() else "available"
                time_value = f"{match.group(1)}:{match.group(2)}"
                collected[(theme_name, time_value)] = ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme_name,
                    date=target_date,
                    time=time_value,
                    status=status,
                    price=price,
                    avg_people=store_config.avg_people,
                    expected_revenue=(
                        round(price * store_config.avg_people, 2)
                        if status == "reserved" and price > 0
                        else 0.0
                    ),
                    crawled_at=datetime.now(timezone.utc),
                    genre=configured.genre if configured else "",
                    duration_minutes=(
                        configured.duration_minutes if configured else 0
                    ),
                    price_note=configured.price_note if configured else "",
                    price_source_url=(
                        configured.price_source_url if configured else ""
                    ),
                    price_verified_at=(
                        configured.price_verified_at if configured else ""
                    ),
                )
        return sorted(
            collected.values(), key=lambda slot: (slot.theme_name, slot.time)
        )

    @staticmethod
    def _select_date(page: Page, target_date: date) -> None:
        current = page.locator('input[name="rev_days"]')
        if (
            current.count() == 0
            or current.first.input_value() == target_date.isoformat()
        ):
            return
        target = page.locator(f'a[href*="{target_date.isoformat()}"]')
        if target.count() == 1:
            target.click()
            page.wait_for_timeout(500)
