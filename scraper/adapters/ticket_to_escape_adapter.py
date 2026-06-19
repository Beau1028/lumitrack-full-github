from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import Browser, BrowserContext, Page

from scraper.adapters.base_adapter import BaseAdapter, USER_AGENT
from scraper.models import ReservationSlot, StoreConfig, ThemeConfig

TIME_PATTERN = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def _dated_naver_url(url: str, target_date: date) -> str:
    parts = urlsplit(url)
    value = quote(
        f"{target_date.isoformat()}T00:00:00+09:00",
        safe="",
    )
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, f"startDateTime={value}", "")
    )


class TicketToEscapeAdapter(BaseAdapter):
    """Read the public TTE page today and public Naver item pages ahead."""

    navigation_timeout_ms = 45_000
    preserve_existing_on_empty = True

    def fetch_slots_for_dates_in_browser(
        self,
        store_config: StoreConfig,
        target_dates: list[date],
        browser: Browser,
    ) -> dict[date, list[ReservationSlot] | Exception]:
        context: BrowserContext = browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
        )
        page = context.new_page()
        page.set_default_timeout(self.navigation_timeout_ms)
        outcomes: dict[date, list[ReservationSlot] | Exception] = {}
        today = datetime.now(ZoneInfo("Asia/Seoul")).date()
        try:
            page.goto(
                store_config.booking_url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            page.wait_for_selector(
                "#view-byTheme .chip",
                state="attached",
                timeout=self.navigation_timeout_ms,
            )
            page.wait_for_timeout(1_500)
            official_slots = self.parse_slots(page, store_config, today)
            schedule_by_theme: dict[str, list[str]] = {}
            for slot in official_slots:
                schedule_by_theme.setdefault(slot.theme_name, []).append(
                    slot.time
                )

            for target_date in target_dates:
                try:
                    if target_date == today:
                        outcomes[target_date] = official_slots
                        continue

                    slots: list[ReservationSlot] = []
                    for theme in store_config.themes:
                        if "booking.naver.com" not in theme.public_schedule_url:
                            continue
                        page.goto(
                            _dated_naver_url(
                                theme.public_schedule_url, target_date
                            ),
                            wait_until="domcontentloaded",
                            timeout=self.navigation_timeout_ms,
                        )
                        try:
                            page.wait_for_function(
                                """
                                () => document.querySelector('button.btn_time')
                                    || document.body.innerText.includes(
                                        '예약 가능한 시간이 없습니다'
                                    )
                                    || document.body.innerText.includes(
                                        '예약이 종료'
                                    )
                                """,
                                timeout=4_000,
                            )
                        except Exception:
                            pass
                        theme_slots = self._parse_naver_theme(
                            page, store_config, theme, target_date
                        )
                        if (
                            not theme_slots
                            and "예약 가능한 시간이 없습니다"
                            in page.locator("body").inner_text()
                        ):
                            theme_slots = [
                                self._slot(
                                    store_config,
                                    theme,
                                    target_date,
                                    time_value,
                                    "reserved",
                                )
                                for time_value in schedule_by_theme.get(
                                    theme.theme_name, []
                                )
                            ]
                        slots.extend(theme_slots)
                    outcomes[target_date] = slots
                except Exception as exc:
                    outcomes[target_date] = exc
        finally:
            context.close()
        return outcomes

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        collected: list[ReservationSlot] = []
        cards = page.locator("#view-byTheme .card")
        for index in range(cards.count()):
            card = cards.nth(index)
            title = card.locator(".title")
            if title.count() == 0:
                continue
            theme = store_config.theme_by_name(
                " ".join((title.first.inner_text() or "").split())
            )
            if theme is None:
                continue
            for chip in card.locator(".chip").all():
                match = TIME_PATTERN.search(chip.inner_text() or "")
                if not match:
                    continue
                status = (
                    "reserved"
                    if "is-unavailable" in (chip.get_attribute("class") or "")
                    else "available"
                )
                collected.append(
                    self._slot(
                        store_config,
                        theme,
                        target_date,
                        f"{int(match.group(1)):02d}:{match.group(2)}",
                        status,
                    )
                )
        return sorted(
            collected, key=lambda slot: (slot.theme_name, slot.time)
        )

    def _parse_naver_theme(
        self,
        page: Page,
        store_config: StoreConfig,
        theme: ThemeConfig,
        target_date: date,
    ) -> list[ReservationSlot]:
        found: dict[str, ReservationSlot] = {}
        for button in page.locator("button.btn_time").all():
            text = " ".join((button.inner_text() or "").split())
            match = TIME_PATTERN.search(text)
            if not match:
                continue
            hour = int(match.group(1))
            if "오후" in text and hour < 12:
                hour += 12
            elif "오전" in text and hour == 12:
                hour = 0
            time_value = f"{hour:02d}:{match.group(2)}"
            class_name = button.get_attribute("class") or ""
            status = (
                "reserved" if "unselectable" in class_name else "available"
            )
            found[time_value] = self._slot(
                store_config,
                theme,
                target_date,
                time_value,
                status,
            )
        return list(found.values())

    @staticmethod
    def _slot(
        store: StoreConfig,
        theme: ThemeConfig,
        target_date: date,
        time_value: str,
        status: str,
    ) -> ReservationSlot:
        return ReservationSlot(
            store_id=store.store_id,
            theme_name=theme.theme_name,
            date=target_date,
            time=time_value,
            status=status,  # type: ignore[arg-type]
            price=theme.price,
            avg_people=store.avg_people,
            expected_revenue=(
                theme.estimated_booking_value(store.avg_people, target_date)
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
