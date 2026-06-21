from __future__ import annotations

from datetime import date

from playwright.sync_api import Error as PlaywrightError, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.adapters.public_slot_parser import (
    collect_public_slots,
    complete_with_schedule_times,
)
from scraper.models import ReservationSlot, StoreConfig


class WordPressBookedAdapter(BaseAdapter):
    """Read public WordPress Booked calendars by opening the visible day row."""

    navigation_timeout_ms = 45_000
    ignore_https_errors = True
    preserve_existing_on_empty = True

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        page.wait_for_timeout(1_500)
        self._open_target_date(page, target_date)
        page.wait_for_timeout(1_500)
        forced_theme = store_config.themes[0] if len(store_config.themes) == 1 else None
        slots = collect_public_slots(
            page,
            store_config,
            target_date,
            forced_theme=forced_theme,
            non_actionable_links_reserved=True,
        )
        return complete_with_schedule_times(slots, store_config, target_date)

    def _open_target_date(self, page: Page, target_date: date) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    ({isoDate, day, monthIndex}) => {
                        const visible = node => {
                            const style = getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && rect.width > 0
                                && rect.height > 0;
                        };
                        const calendars = [...document.querySelectorAll(
                            'table.booked-calendar, .booked-calendar-wrap table'
                        )];
                        for (const calendar of calendars) {
                            const month = calendar.getAttribute('data-month');
                            if (month && Number(month) !== monthIndex) continue;
                            const cells = [...calendar.querySelectorAll(
                                'td, a, button, [role="button"]'
                            )].filter(visible);
                            const exact = cells.find(node => {
                                const text = (node.textContent || '')
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                const attrs = [
                                    'data-date', 'data-day', 'href', 'title',
                                    'aria-label', 'onclick'
                                ].map(name => node.getAttribute(name) || '').join(' ');
                                return attrs.includes(isoDate)
                                    || text === String(day)
                                    || text.includes(`${monthIndex + 1}/${day}`)
                                    || text.includes(`${monthIndex + 1}월 ${day}일`);
                            });
                            if (exact) {
                                exact.click();
                                return true;
                            }
                        }
                        return false;
                    }
                    """,
                    {
                        "isoDate": target_date.isoformat(),
                        "day": target_date.day,
                        "monthIndex": target_date.month - 1,
                    },
                )
            )
        except PlaywrightError:
            return False
