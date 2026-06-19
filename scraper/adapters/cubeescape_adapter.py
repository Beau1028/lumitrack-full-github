from __future__ import annotations

import re
from datetime import date, datetime, timezone

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class CubeEscapeAdapter(BaseAdapter):
    """Read Cube Escape's public timetable without opening a booking form."""

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        date_input = page.locator("#r_date")
        if date_input.count() == 1:
            date_input.fill(target_date.isoformat())
            page.evaluate("loadThemeInfo('')")
            page.locator("#show_themeTimeArea tbody").wait_for(state="attached")
            page.wait_for_timeout(500)

        collected: dict[tuple[str, str], ReservationSlot] = {}
        rows = page.locator("#show_themeTimeArea tbody tr")
        for index in range(rows.count()):
            cells = rows.nth(index).locator("td")
            if cells.count() < 3:
                continue
            time_text = " ".join((cells.nth(0).inner_text() or "").split())
            theme_name = " ".join((cells.nth(1).inner_text() or "").split())
            status_text = " ".join((cells.nth(2).inner_text() or "").split())
            match = TIME_PATTERN.search(time_text)
            if not match or not theme_name:
                continue
            status = "reserved" if "매진" in status_text else "available"
            configured = store_config.theme_by_name(theme_name)
            price = configured.price if configured else 0
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
                    configured.duration_minutes if configured else 60
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
