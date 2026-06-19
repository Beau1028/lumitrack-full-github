from __future__ import annotations

import re
from datetime import date, datetime, timezone

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class PageTodayAdapter(BaseAdapter):
    """Read the public timetable shown by 오늘의 한 페이지."""

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        self._select_date(page, target_date)
        page.wait_for_timeout(700)

        collected: dict[tuple[str, str], ReservationSlot] = {}
        tabs = page.locator('button[data-bs-target^="#theme"]')
        for index in range(tabs.count()):
            tab = tabs.nth(index)
            theme_name = " ".join((tab.inner_text() or "").split())
            target = tab.get_attribute("data-bs-target")
            if not theme_name or not target:
                continue

            configured = store_config.theme_by_name(theme_name)
            pane = page.locator(target)
            for button in pane.locator("button").all():
                text = " ".join((button.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                time_value = f"{match.group(1)}:{match.group(2)}"
                classes = (button.get_attribute("class") or "").casefold()
                status = (
                    "reserved"
                    if "예약 불가" in text or "disabled" in classes
                    else "available"
                )
                price = configured.price if configured else 0
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
        date_input = page.locator("#datepicker")
        if date_input.count() == 0:
            return
        current_value = date_input.input_value()
        if current_value == target_date.isoformat():
            return

        date_input.click()
        current = date.fromisoformat(current_value)
        month_delta = (
            (target_date.year - current.year) * 12
            + target_date.month
            - current.month
        )
        direction = "next" if month_delta > 0 else "prev"
        for _ in range(abs(month_delta)):
            page.locator(f".datepicker-days th.{direction}").click()

        class_filter = ":not(.old):not(.new)"
        day = page.locator(
            f".datepicker-days td.day{class_filter}",
            has_text=str(target_date.day),
        )
        if day.count() != 1:
            return
        day.click()

