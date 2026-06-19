from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from playwright.sync_api import ElementHandle, Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, ReservationStatus, StoreConfig

TIME_PATTERN = re.compile(r"(?<!\d)([01]\d|2[0-3]):([0-5]\d)(?!\d)")

STATUS_KEYWORDS: tuple[tuple[ReservationStatus, tuple[str, ...]], ...] = (
    ("reserved", ("예약 완료", "예약완료", "sold out", "soldout", "reserved")),
    ("closed", ("마감", "closed", "전화문의")),
    ("available", ("예약가능", "available", "예약하기", "신청")),
)


class GenericAdapter(BaseAdapter):
    """
    Parse semantic HTML slots.

    Preferred markup:
    <div class="booking-slot" data-theme="..." data-date="YYYY-MM-DD"
         data-time="HH:MM" data-status="available|reserved|closed">
    """

    slot_selectors = (
        "[data-booking-slot]",
        ".booking-slot",
        ".reservation-slot",
    )

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        elements: list[ElementHandle] = []
        for selector in self.slot_selectors:
            elements = page.query_selector_all(selector)
            if elements:
                break

        if not elements:
            elements = self._find_keyword_candidates(page)

        slots: dict[tuple[str, str], ReservationSlot] = {}
        for element in elements:
            raw = self._extract_element_data(element)
            slot = self._to_slot(raw, store_config, target_date)
            if slot is not None:
                slots[(slot.theme_name, slot.time)] = slot

        return sorted(slots.values(), key=lambda item: (item.theme_name, item.time))

    @staticmethod
    def _find_keyword_candidates(page: Page) -> list[ElementHandle]:
        candidates = page.query_selector_all(
            "button, a, [role=button], li, td, .time, .slot"
        )
        matched: list[ElementHandle] = []
        keyword_pattern = re.compile(
            r"예약\s*가능|예약\s*완료|마감|sold\s*out|available|closed|예약하기|신청|전화문의",
            re.IGNORECASE,
        )
        for candidate in candidates[:300]:
            text = (candidate.inner_text() or "").strip()
            if TIME_PATTERN.search(text) and keyword_pattern.search(text):
                matched.append(candidate)
        return matched

    def _extract_element_data(self, element: ElementHandle) -> dict[str, Any]:
        text = (element.inner_text() or "").strip()
        ancestor_text = element.evaluate(
            """
            node => {
                const parent = node.closest(
                    '[data-theme], [data-theme-name], section, article, tr, li'
                );
                return parent ? parent.innerText.slice(0, 1000) : '';
            }
            """
        )
        return {
            "text": text,
            "ancestor_text": ancestor_text,
            "theme": element.get_attribute("data-theme")
            or self._nested_text(element, ".theme-name, [data-theme-name]"),
            "date": element.get_attribute("data-date"),
            "time": element.get_attribute("data-time"),
            "status": element.get_attribute("data-status"),
            "price": element.get_attribute("data-price"),
            "class": element.get_attribute("class") or "",
        }

    @staticmethod
    def _nested_text(element: ElementHandle, selector: str) -> str | None:
        nested = element.query_selector(selector)
        return (nested.inner_text() or "").strip() if nested else None

    def _to_slot(
        self,
        raw: dict[str, Any],
        store_config: StoreConfig,
        target_date: date,
    ) -> ReservationSlot | None:
        slot_date = self._parse_date(raw.get("date"), target_date)
        if slot_date != target_date:
            return None

        text = str(raw.get("text") or "")
        theme_name = str(raw.get("theme") or "").strip()
        if not theme_name:
            theme_name = self._match_theme_from_text(
                f"{text} {raw.get('ancestor_text') or ''}", store_config
            )
        if not theme_name:
            return None

        time_value = self._normalize_time(str(raw.get("time") or ""), text)
        if time_value is None:
            return None

        status_source = " ".join(
            [
                str(raw.get("status") or ""),
                str(raw.get("class") or ""),
                text,
            ]
        )
        status = self.normalize_status(status_source)
        theme = store_config.theme_by_name(theme_name)
        price = self._parse_price(raw.get("price"))
        if price is None:
            price = theme.price if theme else 0

        # This is an estimate, not actual sales. Revenue is counted only when
        # the normalized slot status is reserved.
        expected_revenue = (
            theme.estimated_booking_value(
                store_config.avg_people,
                target_date,
            )
            if status == "reserved" and theme
            else round(price * store_config.avg_people, 2)
            if status == "reserved"
            else 0.0
        )
        return ReservationSlot(
            store_id=store_config.store_id,
            theme_name=theme.theme_name if theme else theme_name,
            date=slot_date,
            time=time_value,
            status=status,
            price=price,
            avg_people=store_config.avg_people,
            expected_revenue=expected_revenue,
            crawled_at=datetime.now(timezone.utc),
            genre=theme.genre if theme else "",
            duration_minutes=theme.duration_minutes if theme else 0,
            price_note=theme.price_note if theme else "",
            price_source_url=theme.price_source_url if theme else "",
            price_verified_at=theme.price_verified_at if theme else "",
        )

    @staticmethod
    def _parse_date(raw_date: Any, fallback: date) -> date:
        if not raw_date:
            return fallback
        try:
            return date.fromisoformat(str(raw_date).strip())
        except ValueError:
            return fallback

    @staticmethod
    def _normalize_time(raw_time: str, text: str) -> str | None:
        match = TIME_PATTERN.search(raw_time) or TIME_PATTERN.search(text)
        if not match:
            return None
        return f"{match.group(1)}:{match.group(2)}"

    @staticmethod
    def _parse_price(raw_price: Any) -> int | None:
        if raw_price is None:
            return None
        digits = re.sub(r"[^\d]", "", str(raw_price))
        return int(digits) if digits else None

    @staticmethod
    def _match_theme_from_text(text: str, store_config: StoreConfig) -> str:
        normalized_text = " ".join(text.split()).casefold()
        for theme in sorted(
            store_config.themes, key=lambda item: len(item.theme_name), reverse=True
        ):
            if " ".join(theme.theme_name.split()).casefold() in normalized_text:
                return theme.theme_name
        return ""

    @staticmethod
    def normalize_status(raw_status: str) -> ReservationStatus:
        normalized = " ".join(raw_status.casefold().replace("_", " ").split())
        for status, keywords in STATUS_KEYWORDS:
            if any(keyword in normalized for keyword in keywords):
                return status
        return "unknown"
