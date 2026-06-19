from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Locator, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-4]):([0-5]\d)(?!\d)")
DURATION_PATTERN = re.compile(r"(?:플레이)?시간\s*:\s*(\d+)")


def _with_query(url: str, **updates: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(updates)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


class SinbiAdapter(BaseAdapter):
    """Parse the public Sinbi-style timetable used by several brands."""

    ignore_https_errors = True

    def build_booking_url(
        self, store_config: StoreConfig, target_date: date
    ) -> str:
        return _with_query(
            store_config.booking_url,
            rev_days=target_date.isoformat(),
        )

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        cards = page.locator(".tm_box, .theme_box")
        collected: dict[tuple[str, str], ReservationSlot] = {}
        for index in range(cards.count()):
            card = cards.nth(index)
            theme_name = self._theme_name(card)
            if not theme_name:
                continue
            configured = store_config.theme_by_name(theme_name)
            metadata = " ".join((card.inner_text() or "").split())
            genre = configured.genre if configured else self._genre(theme_name)
            duration = (
                configured.duration_minutes
                if configured and configured.duration_minutes > 0
                else self._duration(metadata)
            )
            price = configured.price if configured else 0

            entries = card.locator(".time li, .reserve_Time li")
            for entry_index in range(entries.count()):
                entry = entries.nth(entry_index)
                text = " ".join((entry.inner_text() or "").split())
                time_match = TIME_PATTERN.search(text)
                if not time_match:
                    continue
                time_value = f"{time_match.group(1)}:{time_match.group(2)}"
                if time_value == "24:00":
                    time_value = "23:59"
                if "예약가능" in text:
                    status = "available"
                elif "예약마감" in text:
                    status = "reserved"
                else:
                    status = "unknown"
                collected[(theme_name, time_value)] = ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme_name,
                    date=target_date,
                    time=time_value,
                    status=status,
                    price=price,
                    avg_people=store_config.avg_people,
                    expected_revenue=(
                        configured.estimated_booking_value(
                            store_config.avg_people,
                            target_date,
                        )
                        if status == "reserved" and configured
                        else 0.0
                    ),
                    crawled_at=datetime.now(timezone.utc),
                    genre=genre,
                    duration_minutes=duration,
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
    def _theme_name(card: Locator) -> str:
        locator = card.locator(".tit .name, .h3_theme")
        if locator.count() == 0:
            return ""
        raw = " ".join((locator.first.inner_text() or "").split())
        return re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()

    @staticmethod
    def _genre(theme_heading: str) -> str:
        match = re.search(r"\(([^()]*)\)\s*$", theme_heading)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _duration(text: str) -> int:
        match = DURATION_PATTERN.search(text)
        return int(match.group(1)) if match else 0
