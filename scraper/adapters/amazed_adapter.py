from __future__ import annotations

import re
from datetime import date, datetime, timezone

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class AmazedAdapter(BaseAdapter):
    """Read the public WordPress Booked timetable used by AMAZED."""

    navigation_timeout_ms = 60_000
    preserve_existing_on_empty = True

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        target = target_date.isoformat()
        for _ in range(35):
            schedule = page.locator(
                f'.booked-appt-list[data-list-date="{target}"]'
            )
            if schedule.count():
                break
            next_button = page.locator(
                f'.booked-list-view-date-next[data-date="{target}"]'
            )
            if next_button.count():
                next_button.first.click()
                page.wait_for_timeout(700)
                continue
            current_next = page.locator(
                ".booked-list-view-date-next[data-date]"
            )
            if current_next.count() == 0:
                return []
            current_next.first.click()
            page.wait_for_timeout(700)
        else:
            return []

        theme = store_config.themes[0]
        collected: list[ReservationSlot] = []
        for item in schedule.first.locator(".timeslot").all():
            text = " ".join((item.inner_text() or "").split())
            match = TIME_PATTERN.search(text)
            if not match:
                continue
            button = item.locator("button")
            disabled = button.count() > 0 and button.first.is_disabled()
            status = (
                "reserved"
                if disabled or "매진되었습니다" in text
                else "available"
                if "예매하기" in text
                else "unknown"
            )
            collected.append(
                ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme.theme_name,
                    date=target_date,
                    time=match.group(0),
                    status=status,
                    price=theme.price,
                    avg_people=store_config.avg_people,
                    expected_revenue=(
                        theme.estimated_booking_value(
                            store_config.avg_people, target_date
                        )
                        if status == "reserved"
                        else 0.0
                    ),
                    crawled_at=datetime.now(timezone.utc),
                    genre=theme.genre,
                    duration_minutes=theme.duration_minutes,
                    price_note=theme.price_note,
                    price_source_url=theme.price_source_url,
                    price_verified_at=theme.price_verified_at,
                )
            )
        return collected
