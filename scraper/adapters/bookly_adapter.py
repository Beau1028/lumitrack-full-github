from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from playwright.sync_api import Error as PlaywrightError, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.adapters.public_slot_parser import (
    collect_public_slots,
    complete_with_schedule_times,
)
from scraper.models import ReservationSlot, StoreConfig, ThemeConfig


TIME_PATTERN = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:시]\s*([0-5]\d)(?!\d)")


class BooklyAdapter(BaseAdapter):
    """Read public WordPress Bookly widgets without logging in or booking."""

    navigation_timeout_ms = 45_000
    ignore_https_errors = True
    preserve_existing_on_empty = True

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        direct_themes = [
            theme
            for theme in store_config.themes
            if theme.public_schedule_url.startswith(("http://", "https://"))
        ]
        themes = direct_themes or list(store_config.themes)
        collected: dict[tuple[str, str], ReservationSlot] = {}

        for index, theme in enumerate(themes):
            if theme.public_schedule_url:
                if index or page.url != theme.public_schedule_url:
                    page.goto(
                        theme.public_schedule_url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )
            page.wait_for_timeout(1_500)
            slots = self._fetch_widget_slots(page, store_config, target_date, theme)
            if not slots:
                slots = collect_public_slots(
                    page,
                    store_config,
                    target_date,
                    forced_theme=theme,
                )
            for slot in complete_with_schedule_times(
                slots,
                StoreConfig(
                    store_id=store_config.store_id,
                    store_name=store_config.store_name,
                    region=store_config.region,
                    booking_url=store_config.booking_url,
                    adapter_type=store_config.adapter_type,
                    avg_people=store_config.avg_people,
                    collection_note=store_config.collection_note,
                    address=store_config.address,
                    latitude=store_config.latitude,
                    longitude=store_config.longitude,
                    brand_name=store_config.brand_name,
                    brand_logo_url=store_config.brand_logo_url,
                    map_note=store_config.map_note,
                    themes=(theme,),
                ),
                target_date,
            ):
                collected[(slot.theme_name, slot.time)] = slot

        return sorted(collected.values(), key=lambda slot: (slot.theme_name, slot.time))

    def _fetch_widget_slots(
        self,
        page: Page,
        store_config: StoreConfig,
        target_date: date,
        theme: ThemeConfig,
    ) -> list[ReservationSlot]:
        config = self._bookly_config(page)
        if not config:
            return []
        try:
            payload = page.evaluate(
                """
                async ({ajaxurl, formId, selectedDate}) => {
                    const body = new URLSearchParams({
                        action: 'ab_render_time',
                        form_id: formId,
                        selected_date: selectedDate,
                        time_zone_offset: String(new Date().getTimezoneOffset()),
                    });
                    const response = await fetch(ajaxurl, {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body,
                    });
                    return await response.json();
                }
                """,
                {
                    "ajaxurl": config["ajaxurl"],
                    "formId": config["form_id"],
                    "selectedDate": target_date.isoformat(),
                },
            )
        except PlaywrightError:
            return []

        slots: list[ReservationSlot] = []
        raw_slots = payload.get("slots", []) if isinstance(payload, dict) else []
        for raw in raw_slots or []:
            time_value = self._slot_time(raw)
            if not time_value:
                continue
            slots.append(
                ReservationSlot(
                    store_id=store_config.store_id,
                    theme_name=theme.theme_name,
                    date=target_date,
                    time=time_value,
                    status="available",
                    price=theme.price,
                    avg_people=store_config.avg_people,
                    expected_revenue=0.0,
                    crawled_at=datetime.now(timezone.utc),
                    genre=theme.genre,
                    duration_minutes=theme.duration_minutes,
                    price_note=theme.price_note,
                    price_source_url=theme.price_source_url,
                    price_verified_at=theme.price_verified_at,
                )
            )
        return slots

    @staticmethod
    def _bookly_config(page: Page) -> dict[str, str]:
        try:
            script_text = str(
                page.evaluate(
                    "() => [...document.scripts].map(s => s.textContent || '').join('\\n')"
                )
            )
        except PlaywrightError:
            return {}
        ajax_match = re.search(r'ajaxurl\s*:\s*"([^"]+)"', script_text)
        form_match = re.search(r'form_id\s*:\s*"([^"]+)"', script_text)
        if not ajax_match or not form_match:
            return {}
        return {
            "ajaxurl": ajax_match.group(1).replace("\\/", "/"),
            "form_id": form_match.group(1),
        }

    @staticmethod
    def _slot_time(raw: Any) -> str:
        text = ""
        if isinstance(raw, dict):
            text = " ".join(str(value) for value in raw.values())
        else:
            text = str(raw)
        match = TIME_PATTERN.search(text)
        if not match:
            return ""
        return f"{int(match.group(1)):02d}:{match.group(2)}"
