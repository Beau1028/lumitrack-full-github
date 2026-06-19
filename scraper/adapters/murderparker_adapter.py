from __future__ import annotations

import random
import re
import time
from datetime import date, datetime, timezone

from playwright.sync_api import Browser, BrowserContext, Page

from scraper.adapters.base_adapter import BaseAdapter, USER_AGENT
from scraper.models import ReservationSlot, StoreConfig

TIME_PATTERN = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
SUFFIX_PATTERN = re.compile(r"_[^_]+점$")
DETAIL_PATTERN = re.compile(r"\([^)]*(?:분|프리미엄|혼방)[^)]*\)")


class MurderParkerAdapter(BaseAdapter):
    """Read Murder Parker's public branch timetable without opening booking."""

    navigation_timeout_ms = 45_000
    preserve_existing_on_empty = True

    def fetch_slots_for_dates_in_browser(
        self,
        store_config: StoreConfig,
        target_dates: list[date],
        browser: Browser,
    ) -> dict[date, list[ReservationSlot] | Exception]:
        self._validate_public_url(store_config.booking_url)
        context: BrowserContext = browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
        )
        page = context.new_page()
        page.set_default_timeout(self.navigation_timeout_ms)
        outcomes: dict[date, list[ReservationSlot] | Exception] = {}
        try:
            page.goto(
                store_config.booking_url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            for index, target_date in enumerate(target_dates):
                try:
                    with page.expect_navigation(
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    ):
                        page.evaluate(
                            """
                            value => {
                                const input = document.querySelector('[name=H_Date]');
                                if (!input || !document.forms.TIN) {
                                    throw new Error('Public date form not found');
                                }
                                input.value = value;
                                document.forms.TIN.submit();
                            }
                            """,
                            target_date.isoformat(),
                        )
                    outcomes[target_date] = self.parse_slots(
                        page, store_config, target_date
                    )
                except Exception as exc:
                    outcomes[target_date] = exc
                if index < len(target_dates) - 1:
                    time.sleep(
                        random.uniform(
                            self.inter_date_delay_min_seconds,
                            self.inter_date_delay_max_seconds,
                        )
                    )
        finally:
            context.close()
        return outcomes

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        collected: list[ReservationSlot] = []
        blocks = page.locator(".reservTime")
        for index in range(blocks.count()):
            block = blocks.nth(index)
            heading = block.locator("h3")
            if heading.count() == 0:
                continue
            raw_name = " ".join((heading.first.inner_text() or "").split())
            theme_name = SUFFIX_PATTERN.sub("", raw_name).strip()
            theme_name = DETAIL_PATTERN.sub("", theme_name).strip()
            theme = store_config.theme_by_name(theme_name)
            if theme is None:
                continue
            for item in block.locator("li").all():
                text = " ".join((item.inner_text() or "").split())
                match = TIME_PATTERN.search(text)
                if not match:
                    continue
                if "예약가능" in text:
                    status = "available"
                elif "예약완료" in text:
                    status = "reserved"
                else:
                    status = "unknown"
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
