from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Locator, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class EarthstarAdapter(BaseAdapter):
    """Read the public reservation cards from 지구별방탈출."""

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
        collected: dict[tuple[str, str], ReservationSlot] = {}
        cards = page.locator("section.res-item")
        for index in range(cards.count()):
            card = cards.nth(index)
            heading = card.locator("h2")
            if heading.count() == 0:
                continue
            theme_name = " ".join((heading.first.inner_text() or "").split())
            configured = store_config.theme_by_name(theme_name)
            metadata = self._metadata(card)
            price = configured.price if configured else 0
            buttons = card.locator(".res-times-btn button")
            for button_index in range(buttons.count()):
                button = buttons.nth(button_index)
                text = " ".join((button.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                status = "available" if "예약가능" in text else "reserved"
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
                    genre=(
                        configured.genre
                        if configured and configured.genre
                        else metadata.get("장르", "")
                    ),
                    duration_minutes=(
                        configured.duration_minutes
                        if configured and configured.duration_minutes > 0
                        else self._number(metadata.get("시간", ""))
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
    def _metadata(card: Locator) -> dict[str, str]:
        result: dict[str, str] = {}
        rows = card.locator("table tr")
        for index in range(rows.count()):
            row = rows.nth(index)
            cells = row.locator("th, td")
            if cells.count() >= 2:
                result[
                    " ".join((cells.nth(0).inner_text() or "").split())
                ] = " ".join((cells.nth(1).inner_text() or "").split())
        return result

    @staticmethod
    def _number(value: str) -> int:
        match = re.search(r"\d+", value)
        return int(match.group()) if match else 0
