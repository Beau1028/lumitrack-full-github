from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class OasisAdapter(BaseAdapter):
    """Read the public 오아시스 뮤지엄 ticket timetable."""

    navigation_timeout_ms = 45_000

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
        page.wait_for_function(
            """
            () => {
                const buttons = [...document.querySelectorAll('.room_btn')];
                return buttons.length > 0 && buttons.every(button =>
                    button.classList.contains('btn-opened') || button.disabled
                );
            }
            """,
            timeout=self.navigation_timeout_ms,
        )
        page.wait_for_timeout(500)

        slots: list[ReservationSlot] = []
        buttons = page.locator(".room_btn")
        for index in range(buttons.count()):
            button = buttons.nth(index)
            theme_id = button.get_attribute("data-tm") or ""
            time_text = (
                button.get_attribute("data-time")
                or button.get_attribute("value")
                or button.inner_text()
            )
            match = TIME_PATTERN.search(time_text or "")
            if not theme_id or not match:
                continue
            heading = page.locator(f"#tm_name{theme_id}")
            if heading.count() == 0:
                continue
            raw_theme_name = " ".join((heading.first.inner_text() or "").split())
            theme_name = re.sub(r"^\[[^\]]+\]\s*", "", raw_theme_name).strip()
            configured = store_config.theme_by_name(theme_name)
            price = configured.price if configured else 0
            classes = set((button.get_attribute("class") or "").split())
            if "btn-opened" in classes and not button.is_disabled():
                status = "available"
            elif "btn-closed" in classes or button.is_disabled():
                status = "reserved"
            else:
                status = "unknown"
            slots.append(
                ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme_name,
                    date=target_date,
                    time=f"{match.group(1)}:{match.group(2)}",
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
            )
        return sorted(slots, key=lambda slot: (slot.theme_name, slot.time))
