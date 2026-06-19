from __future__ import annotations

import re
from datetime import date, datetime, timezone

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")


class DeepthinkerAdapter(BaseAdapter):
    """Read the public 딥띵커 timetable after selecting a calendar date."""

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        page.wait_for_function("() => window.jQuery && jQuery('.datepicker').length")
        page.evaluate(
            """
            targetDate => {
                const input = jQuery('.datepicker');
                input.datepicker('setDate', targetDate);
                const onSelect = input.datepicker('option', 'onSelect');
                onSelect.call(input[0], targetDate, {input: [input[0]]});
            }
            """,
            target_date.isoformat(),
        )
        page.locator("#loader").wait_for(state="hidden")
        page.wait_for_timeout(500)

        slots: list[ReservationSlot] = []
        cards = page.locator(".theme1-detail-info")
        for card_index in range(cards.count()):
            card = cards.nth(card_index)
            heading = card.locator("h2")
            if heading.count() == 0:
                continue
            theme_name = " ".join((heading.first.inner_text() or "").split())
            configured = store_config.theme_by_name(theme_name)
            price = configured.price if configured else 0
            buttons = card.locator(".theme-time-list a")
            for button_index in range(buttons.count()):
                button = buttons.nth(button_index)
                raw_time = button.get_attribute("data-time") or button.inner_text()
                match = TIME_PATTERN.search(raw_time or "")
                if not match:
                    continue
                classes = set((button.get_attribute("class") or "").split())
                text = " ".join((button.inner_text() or "").split())
                if "submit" in classes or "예약가능" in text:
                    status = "available"
                elif "disable" in classes or "예약불가" in text:
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
