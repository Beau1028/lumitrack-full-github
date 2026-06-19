from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")
CAPACITY_PATTERN = re.compile(r"Available\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)


class HorrorSwitchAdapter(BaseAdapter):
    """Normalize the public remaining-capacity timetable into slot status."""

    def build_booking_url(
        self, store_config: StoreConfig, target_date: date
    ) -> str:
        parts = urlsplit(store_config.booking_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["date"] = target_date.isoformat()
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        theme = page.locator(".restheme figcaption")
        if theme.count() == 0:
            return []
        theme_name = " ".join((theme.first.inner_text() or "").split())
        configured = store_config.theme_by_name(theme_name)
        price = configured.price if configured else 0
        slots: list[ReservationSlot] = []
        buttons = page.locator(".restimes-button")
        for index in range(buttons.count()):
            text = " ".join((buttons.nth(index).inner_text() or "").split())
            time_match = TIME_PATTERN.search(text)
            capacity_match = CAPACITY_PATTERN.search(text)
            if not time_match or not capacity_match:
                continue
            remaining = int(capacity_match.group(1))
            status = "available" if remaining > 0 else "reserved"
            slots.append(
                ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme_name,
                    date=target_date,
                    time=f"{time_match.group(1)}:{time_match.group(2)}",
                    status=status,
                    price=price,
                    avg_people=store_config.avg_people,
                    expected_revenue=(
                        round(price * store_config.avg_people, 2)
                        if status == "reserved" and price > 0
                        else 0.0
                    ),
                    crawled_at=datetime.now(timezone.utc),
                    genre=configured.genre if configured else "호러",
                    duration_minutes=(
                        configured.duration_minutes if configured else 30
                    ),
                    price_note=configured.price_note if configured else "",
                    price_source_url=(
                        configured.price_source_url if configured else ""
                    ),
                    price_verified_at=(
                        configured.price_verified_at if configured else ""
                    ),
                )
            )
        return sorted(slots, key=lambda slot: slot.time)
