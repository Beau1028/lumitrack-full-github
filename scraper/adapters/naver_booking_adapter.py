from __future__ import annotations

from datetime import date
from urllib.parse import quote, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.adapters.public_slot_parser import (
    collect_public_slots,
    select_public_date,
    wait_for_public_time_controls,
)
from scraper.models import ReservationSlot, StoreConfig, ThemeConfig


class NaverBookingAdapter(BaseAdapter):
    """Read visible public Naver products, dates and times without booking."""

    navigation_timeout_ms = 45_000
    preserve_existing_on_empty = True

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        direct_themes = [
            theme
            for theme in store_config.themes
            if "booking.naver.com" in theme.public_schedule_url
        ]
        if direct_themes:
            collected: dict[tuple[str, str], ReservationSlot] = {}
            for theme in direct_themes:
                page.goto(
                    self._dated_url(theme.public_schedule_url, target_date),
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                page.wait_for_timeout(900)
                for slot in collect_public_slots(
                    page,
                    store_config,
                    target_date,
                    forced_theme=theme,
                ):
                    collected[(slot.theme_name, slot.time)] = slot
            return sorted(
                collected.values(),
                key=lambda slot: (slot.theme_name, slot.time),
            )

        page.wait_for_timeout(1_500)
        wait_for_public_time_controls(page)
        select_public_date(page, target_date)
        slots = collect_public_slots(page, store_config, target_date)
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
            page.wait_for_timeout(1_200)
            if "/items/" in page.url:
                page.goto(
                    self._dated_url(page.url, target_date),
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                page.wait_for_timeout(900)
            wait_for_public_time_controls(page, timeout_ms=2_500)
            select_public_date(page, target_date)
            for slot in collect_public_slots(
                page,
                store_config,
                target_date,
                forced_theme=theme,
            ):
                collected[(slot.theme_name, slot.time)] = slot
        if collected:
            return sorted(
                collected.values(),
                key=lambda slot: (slot.theme_name, slot.time),
            )
        return []

    @staticmethod
    def _dated_url(url: str, target_date: date) -> str:
        parts = urlsplit(url)
        query = quote(
            f"{target_date.isoformat()}T00:00:00+09:00",
            safe="",
        )
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                f"startDateTime={query}",
                "",
            )
        )

    @staticmethod
    def _select_theme(page: Page, theme: ThemeConfig) -> bool:
        try:
            clicked = page.evaluate(
                """
                themeName => {
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
                    const blocked = node => {
                        const text = clean(node.textContent);
                        const href = clean(node.getAttribute('href'));
                        return ['결제', '구매', '주문', '예약완료', '예약 완료']
                            .some(term => text.includes(term))
                            || ['/payment', '/checkout', '/orders', '/login']
                                .some(term => href.includes(term));
                    };
                    const target = clean(themeName);
                    const controls = [...document.querySelectorAll(
                        'a[href*="/items/"], button, a, [role="button"], '
                        + '[role="tab"], label'
                    )];

                    const productLink = controls.find(node =>
                        visible(node)
                        && !blocked(node)
                        && clean(node.textContent).includes(target)
                        && (node.getAttribute('href') || '').includes('/items/')
                    );
                    if (productLink) {
                        productLink.click();
                        return true;
                    }

                    const exactThemeControl = controls.find(node =>
                        visible(node)
                        && !blocked(node)
                        && clean(node.textContent) === target
                    );
                    if (exactThemeControl) {
                        exactThemeControl.click();
                        return true;
                    }

                    const containers = [...document.querySelectorAll(
                        'article, section, li, [data-testid*="product"], '
                        + '[class*="product"], [class*="item"], [class*="card"]'
                    )].filter(node =>
                        visible(node) && clean(node.textContent).includes(target)
                    );
                    containers.sort(
                        (left, right) =>
                            clean(left.textContent).length
                            - clean(right.textContent).length
                    );
                    for (const container of containers.slice(0, 20)) {
                        const action = [...container.querySelectorAll(
                            'a[href*="/items/"], button, a, [role="button"]'
                        )].find(node => {
                            const text = clean(node.textContent);
                            return visible(node)
                                && !blocked(node)
                                && (
                                    text.includes(target)
                                    || text.includes('예약')
                                    || text.includes('일정')
                                    || text.includes('시간')
                                );
                        });
                        if (action) {
                            action.click();
                            return true;
                        }
                    }
                    return false;
                }
                """,
                theme.theme_name,
            )
        except PlaywrightError:
            return False
        if clicked:
            page.wait_for_timeout(1_000)
        return bool(clicked)
