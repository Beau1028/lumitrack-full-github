from __future__ import annotations

from datetime import date
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.adapters.public_slot_parser import (
    collect_public_slots,
    select_public_date,
)
from scraper.models import ReservationSlot, StoreConfig, ThemeConfig


class KeyescapeAdapter(BaseAdapter):
    """Read Keyescape only after the operator grants written permission."""

    navigation_timeout_ms = 45_000
    preserve_existing_on_empty = True

    def fetch_slots(
        self, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        if os.getenv("KEYESCAPE_PUBLIC_MONITORING_PERMISSION") != "1":
            raise PermissionError(
                "키이스케이프 자동 수집은 운영사의 서면 허가가 필요합니다. "
                "허가를 받은 뒤에만 "
                "KEYESCAPE_PUBLIC_MONITORING_PERMISSION=1을 설정하세요."
            )
        return super().fetch_slots(store_config, target_date)

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        self._raise_if_automation_blocked(page)
        theme_urls = self._theme_urls(page, store_config)
        collected: dict[tuple[str, str], ReservationSlot] = {}

        for index, theme in enumerate(store_config.themes):
            target_url = theme_urls.get(self._normalize(theme.theme_name))
            if not target_url:
                continue
            if index > 0 or page.url != target_url:
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                page.wait_for_timeout(900)
            self._raise_if_automation_blocked(page)
            select_public_date(page, target_date)
            for slot in collect_public_slots(
                page,
                store_config,
                target_date,
                forced_theme=theme,
            ):
                collected[(slot.theme_name, slot.time)] = slot

        if not collected and len(store_config.themes) == 1:
            theme = store_config.themes[0]
            select_public_date(page, target_date)
            return collect_public_slots(
                page,
                store_config,
                target_date,
                forced_theme=theme,
            )
        return sorted(
            collected.values(),
            key=lambda slot: (slot.theme_name, slot.time),
        )

    def _theme_urls(
        self,
        page: Page,
        store_config: StoreConfig,
    ) -> dict[str, str]:
        parts = urlsplit(page.url or store_config.booking_url)
        current_query = dict(parse_qsl(parts.query, keep_blank_values=True))
        branch = current_query.get("zizum_num", "")
        result: dict[str, str] = {}
        options = page.locator("#theme option, select[name=theme] option")
        for index in range(options.count()):
            option = options.nth(index)
            try:
                name = " ".join((option.inner_text() or "").split())
                info_num = option.get_attribute("value") or ""
                theme_num = option.get_attribute("data-themenum") or ""
            except PlaywrightError:
                continue
            if not name or not info_num or not theme_num or not branch:
                continue
            query = {
                "zizum_num": branch,
                "theme_num": theme_num,
                "theme_info_num": info_num,
            }
            result[self._normalize(name)] = urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    "/reservation1.php",
                    urlencode(query),
                    "",
                )
            )

        selected = page.locator(
            "#theme option:checked, select[name=theme] option:checked"
        )
        if selected.count() and page.url:
            name = " ".join((selected.first.inner_text() or "").split())
            if name:
                result.setdefault(self._normalize(name), page.url)
        return result

    @staticmethod
    def _raise_if_automation_blocked(page: Page) -> None:
        notice = page.get_by_text(
            "개발자 도구 사용이 금지되어 있습니다.",
            exact=True,
        )
        try:
            if notice.count() and notice.first.is_visible():
                raise RuntimeError(
                    "키이스케이프가 자동 브라우저 접근 금지 안내를 표시했습니다. "
                    "안내 화면을 제거하거나 우회하지 않습니다."
                )
        except PlaywrightError:
            return

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(value.casefold().split()).replace("-", "")
