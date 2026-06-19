from __future__ import annotations

import re
from datetime import date, datetime, timezone

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class CodeKAdapter(BaseAdapter):
    """Read Code K's public all-theme timetable."""

    navigation_timeout_ms = 45_000

    def build_booking_url(
        self, store_config: StoreConfig, target_date: date
    ) -> str:
        separator = "&" if "?" in store_config.booking_url else "?"
        return (
            f"{store_config.booking_url}{separator}"
            f"CHOIS_DATE={target_date.isoformat()}&DIS_T=A"
        )

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        collected: list[ReservationSlot] = []
        for index, theme in enumerate(store_config.themes, start=1):
            block = page.locator(f"#CQ{index}")
            if block.count() == 0:
                continue
            for item in block.locator("li.timeOn, li.timeOff").all():
                text = " ".join((item.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                status = (
                    "available"
                    if "timeOn" in (item.get_attribute("class") or "")
                    else "reserved"
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
        return sorted(
            collected, key=lambda slot: (slot.theme_name, slot.time)
        )
