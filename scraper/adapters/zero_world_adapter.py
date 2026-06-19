from __future__ import annotations

from datetime import date, datetime, timezone
import re

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig


class ZeroWorldAdapter(BaseAdapter):
    """Read Zero World's public date, theme and time controls."""

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        date_cell = page.locator(
            "#calendar .datepicker--cell-day"
            f'[data-year="{target_date.year}"]'
            f'[data-month="{target_date.month - 1}"]'
            f'[data-date="{target_date.day}"]'
        )
        if date_cell.count() != 1:
            return []
        date_cell.click()
        page.wait_for_timeout(250)

        collected: dict[tuple[str, str], ReservationSlot] = {}
        theme_inputs = page.locator('input[name="themePK"]')
        for index in range(theme_inputs.count()):
            theme_input = theme_inputs.nth(index)
            label = theme_input.locator("xpath=..")
            # Themes live in a horizontally scrollable control. Calling the
            # public radio control's click method avoids viewport-only errors
            # while still using the same page event handler as a user click.
            theme_input.evaluate("element => element.click()")
            page.wait_for_timeout(120)

            raw_name = " ".join((label.inner_text() or "").split())
            theme_name = re.sub(r"^\[[^\]]+\]\s*", "", raw_name).strip()
            if not theme_name:
                continue
            configured = store_config.theme_by_name(theme_name)
            price = configured.price if configured else 0

            times = page.locator('#themeTimeWrap input[name="reservationTime"]')
            for time_index in range(times.count()):
                time_input = times.nth(time_index)
                raw_time = time_input.get_attribute("value") or ""
                time_value = raw_time[:5]
                if len(time_value) != 5:
                    continue
                status = "reserved" if time_input.is_disabled() else "available"
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
