from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, ReservationStatus, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class Play33Adapter(BaseAdapter):
    """Read Play33's server-rendered public reservation list."""

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
        page.wait_for_selector("section.reslist", timeout=self.navigation_timeout_ms)
        collected: dict[tuple[str, str], ReservationSlot] = {}

        for section in page.query_selector_all("section.reslist"):
            heading = section.query_selector(".reslist-text > strong")
            theme_name = (heading.inner_text() if heading else "").strip()
            theme = store_config.theme_by_name(theme_name)
            if theme is None:
                continue

            for item in section.query_selector_all(".restimes li"):
                text = " ".join((item.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                time_value = f"{match.group(1)}:{match.group(2)}"
                status = self._status(text)
                expected_revenue = (
                    round(theme.price * store_config.avg_people, 2)
                    if status == "reserved"
                    else 0.0
                )
                collected[(theme.theme_name, time_value)] = ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme.theme_name,
                    date=target_date,
                    time=time_value,
                    status=status,
                    price=theme.price,
                    avg_people=store_config.avg_people,
                    expected_revenue=expected_revenue,
                    crawled_at=datetime.now(timezone.utc),
                    genre=theme.genre,
                    duration_minutes=theme.duration_minutes,
                    price_note=theme.price_note,
                    price_source_url=theme.price_source_url,
                    price_verified_at=theme.price_verified_at,
                )

        return sorted(
            collected.values(), key=lambda slot: (slot.theme_name, slot.time)
        )

    @staticmethod
    def _status(text: str) -> ReservationStatus:
        normalized = text.casefold()
        if "예약 가능" in normalized:
            return "available"
        if "예약 불가" in normalized or "예약완료" in normalized:
            return "reserved"
        if "마감" in normalized or "closed" in normalized:
            return "closed"
        return "unknown"
