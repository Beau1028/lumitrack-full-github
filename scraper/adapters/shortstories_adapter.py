from __future__ import annotations

from datetime import date, datetime, timezone
import logging
import random
import re
import time

from playwright.sync_api import Browser, Error as PlaywrightError, Page

from scraper.adapters.base_adapter import BaseAdapter, USER_AGENT
from scraper.adapters.public_slot_parser import (
    collect_public_slots,
    select_public_date,
    wait_for_public_time_controls,
)
from scraper.models import ReservationSlot, StoreConfig, ThemeConfig

TIME_PATTERN = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3])\s*:\s*[0-5]\d(?!\d)")
THEME_ALIASES = {
    "그림자 없는 상자": ("그림자 없는 상자", "상자"),
    "사람들은 그것을 행복이라 부르기로 했다": (
        "사람들은 그것을 행복이라 부르기로 했다",
        "행복",
    ),
    "쓰여진 문장 속에 구원이 없다면": (
        "쓰여진 문장 속에 구원이 없다면",
        "문장",
    ),
    "존재할 자격": ("존재할 자격", "자격"),
    "쥐와 파시스트와 마지막 한 장": (
        "쥐와 파시스트와 마지막 한 장",
        "쥐",
    ),
    "뱃사람의 별": ("뱃사람의 별", "별"),
}
LOGGER = logging.getLogger(__name__)


class ShortstoriesAdapter(BaseAdapter):
    """Read Danpyeonseon public theme, date and time controls only."""

    navigation_timeout_ms = 20_000
    theme_schedule_timeout_ms = 10_000
    preserve_existing_on_empty = True
    inter_date_delay_min_seconds = 0.2
    inter_date_delay_max_seconds = 0.4

    def fetch_slots_for_dates_in_browser(
        self,
        store_config: StoreConfig,
        target_dates: list[date],
        browser: Browser,
    ) -> dict[date, list[ReservationSlot] | Exception]:
        booking_url = self.build_booking_url(store_config, target_dates[0])
        self._validate_public_url(booking_url)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
            ignore_https_errors=self.ignore_https_errors,
        )
        page = context.new_page()
        page.set_default_timeout(self.navigation_timeout_ms)
        self._prepare_page(page)
        outcomes: dict[date, list[ReservationSlot] | Exception] = {}
        try:
            page.goto(
                booking_url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            page.wait_for_timeout(1_800)
            for date_index, target_date in enumerate(target_dates):
                try:
                    outcomes[target_date] = self.parse_slots(
                        page,
                        store_config,
                        target_date,
                    )
                except Exception as exc:
                    outcomes[target_date] = exc
                if date_index < len(target_dates) - 1:
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
        page.wait_for_timeout(700)
        self._select_shadow_date(page, target_date)
        shadow_slots = self._collect_shadow_widget_slots(
            page,
            store_config,
            target_date,
        )
        if shadow_slots:
            return shadow_slots
        wait_for_public_time_controls(page, timeout_ms=2_500)
        select_public_date(page, target_date)
        slots = collect_public_slots(
            page,
            store_config,
            target_date,
            non_actionable_links_reserved=True,
        )
        if slots:
            return slots

        collected: dict[tuple[str, str], ReservationSlot] = {}
        landing_url = page.url
        for index, theme in enumerate(store_config.themes):
            if index and landing_url.startswith(("http://", "https://", "file://")):
                page.goto(
                    landing_url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                page.wait_for_timeout(1_000)
            if not self._select_theme(page, theme):
                continue
            page.wait_for_timeout(1_000)
            wait_for_public_time_controls(page, timeout_ms=2_500)
            select_public_date(page, target_date)
            for slot in collect_public_slots(
                page,
                store_config,
                target_date,
                forced_theme=theme,
                non_actionable_links_reserved=True,
            ):
                collected[(slot.theme_name, slot.time)] = slot
        if collected:
            return sorted(
                collected.values(),
                key=lambda slot: (slot.theme_name, slot.time),
            )
        return []

    @staticmethod
    def _select_shadow_date(page: Page, target_date: date) -> bool:
        try:
            clicked = page.evaluate(
                """
                ({year, month, day}) => {
                    const root = document.querySelector('booking-widget')?.shadowRoot;
                    if (!root) return false;
                    const visible = node => {
                        const style = getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const expected = `${year}년 ${month}월 ${day}일`;
                    const buttons = [...root.querySelectorAll('button')];
                    const exact = buttons.find(node => {
                        const aria = node.getAttribute('aria-label') || '';
                        return visible(node) && aria.includes(expected);
                    });
                    if (exact) {
                        exact.click();
                        return true;
                    }
                    const dayOnly = buttons.find(node =>
                        visible(node)
                        && (node.textContent || '').trim() === String(day)
                    );
                    if (dayOnly) {
                        dayOnly.click();
                        return true;
                    }
                    return false;
                }
                """,
                {
                    "year": target_date.year,
                    "month": target_date.month,
                    "day": target_date.day,
                },
            )
        except PlaywrightError:
            return False
        if clicked:
            page.wait_for_timeout(650)
        return bool(clicked)

    def _collect_shadow_widget_slots(
        self,
        page: Page,
        store_config: StoreConfig,
        target_date: date,
    ) -> list[ReservationSlot]:
        try:
            rows = page.evaluate(
                """
                () => {
                    const root = document.querySelector('booking-widget')?.shadowRoot;
                    if (!root) return [];
                    const visible = node => {
                        const style = getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    return [...root.querySelectorAll(
                        '[class*="reservationItem_divider"]'
                    )].filter(visible).map(node => {
                        const button = node.querySelector('button');
                        const title =
                            node.querySelector('img[alt]')?.getAttribute('alt')
                            || node.innerText
                            || node.textContent
                            || '';
                        const text = (node.innerText || node.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        const classText = String(node.className || '');
                        const buttonClass = String(button?.className || '');
                        const buttonText = (button?.innerText || button?.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        return {
                            title,
                            text,
                            classText,
                            buttonClass,
                            buttonText,
                            disabled: Boolean(button?.disabled)
                                || button?.getAttribute('aria-disabled') === 'true'
                        };
                    });
                }
                """
            )
        except PlaywrightError:
            return []

        collected: dict[tuple[str, str], ReservationSlot] = {}
        for row in rows:
            raw_text = " ".join(
                str(row.get(key, "") or "")
                for key in ("title", "text", "classText", "buttonClass", "buttonText")
            )
            time_match = TIME_PATTERN.search(raw_text)
            if not time_match:
                continue
            hour_text, minute_text = time_match.group(0).split(":", 1)
            time_value = f"{int(hour_text.strip()):02d}:{minute_text.strip()}"
            theme = self._match_widget_theme(raw_text, store_config)
            if theme is None:
                continue
            normalized = raw_text.casefold()
            disabled = bool(row.get("disabled"))
            if (
                disabled
                or "예약불가" in normalized
                or "예약 불가" in normalized
                or "예약완료" in normalized
                or "예약 완료" in normalized
                or "complete" in normalized
                or "disabled" in normalized
            ):
                status = "reserved"
            elif "예약" in normalized:
                status = "available"
            else:
                status = "unknown"
            expected_revenue = (
                theme.estimated_booking_value(
                    store_config.avg_people,
                    target_date,
                )
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
            collected.values(),
            key=lambda slot: (slot.theme_name, slot.time),
        )

    @staticmethod
    def _match_widget_theme(
        raw_text: str,
        store_config: StoreConfig,
    ) -> ThemeConfig | None:
        normalized = " ".join(raw_text.casefold().split())
        for theme in store_config.themes:
            candidates = THEME_ALIASES.get(theme.theme_name, (theme.theme_name,))
            if any(candidate.casefold() in normalized for candidate in candidates):
                return theme
        if len(store_config.themes) == 1:
            return store_config.themes[0]
        return None

    @staticmethod
    def _select_theme(page: Page, theme: ThemeConfig) -> bool:
        """Open a visible theme/schedule control, never a booking time."""
        try:
            clicked = page.evaluate(
                """
                ({themeNames, timePattern}) => {
                    const clean = value => (value || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .toLocaleLowerCase();
                    const visible = node => {
                        const style = getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const targets = themeNames.map(clean);
                    const timeRegex = new RegExp(timePattern);
                    const controls = [...document.querySelectorAll(
                        'button, a, [role="button"], [role="tab"], label'
                    )];
                    const safe = node => {
                        const text = clean(node.textContent);
                        const href = clean(node.getAttribute('href'));
                        return visible(node)
                            && !timeRegex.test(text)
                            && !['로그인', '결제', '구매', '신청 완료']
                                .some(term => text.includes(term))
                            && !['/login', '/payment', '/checkout']
                                .some(term => href.includes(term));
                    };
                    const exact = controls.find(node =>
                        safe(node)
                        && targets.some(
                            target => clean(node.textContent) === target
                        )
                    );
                    if (exact) {
                        exact.click();
                        return true;
                    }
                    const containing = controls.find(node =>
                        safe(node)
                        && targets.some(
                            target => clean(node.textContent).includes(target)
                        )
                    );
                    if (containing) {
                        containing.click();
                        return true;
                    }
                    return false;
                }
                """,
                {
                    "themeNames": THEME_ALIASES.get(
                        theme.theme_name, (theme.theme_name,)
                    ),
                    "timePattern": TIME_PATTERN.pattern,
                },
            )
        except PlaywrightError:
            return False
        if clicked:
            page.wait_for_timeout(900)
        return bool(clicked)
