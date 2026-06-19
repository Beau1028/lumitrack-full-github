from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class XdungeonAdapter(BaseAdapter):
    """Read the public Bitphobia/Xdungeon reservation timetable."""

    def build_booking_url(
        self, store_config: StoreConfig, target_date: date
    ) -> str:
        parts = urlsplit(store_config.booking_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["rev_days"] = target_date.isoformat()
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        page.wait_for_selector(".thm_box .box", timeout=self.navigation_timeout_ms)
        collected: dict[tuple[str, str], ReservationSlot] = {}

        for box in page.query_selector_all(".thm_box .box"):
            heading = box.query_selector(".img_box .tit")
            theme_name = " ".join((heading.inner_text() if heading else "").split())
            if not theme_name:
                continue
            configured = store_config.theme_by_name(theme_name)
            price = configured.price if configured else 0

            for item in box.query_selector_all(".time_box li"):
                text = " ".join((item.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                time_value = f"{match.group(1)}:{match.group(2)}"
                classes = (item.get_attribute("class") or "").casefold().split()
                anchor = item.query_selector("a")
                href = anchor.get_attribute("href") if anchor else None
                status = "reserved" if "dead" in classes or not href else "available"
                expected_revenue = (
                    configured.estimated_booking_value(
                        store_config.avg_people,
                        target_date,
                    )
                    if status == "reserved" and configured
                    else 0.0
                )
                collected[(theme_name, time_value)] = ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme_name,
                    date=target_date,
                    time=time_value,
                    status=status,
                    price=price,
                    avg_people=store_config.avg_people,
                    expected_revenue=expected_revenue,
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
