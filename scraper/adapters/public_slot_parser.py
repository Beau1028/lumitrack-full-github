from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

from playwright.sync_api import Error as PlaywrightError, Frame, Locator, Page

from scraper.models import ReservationSlot, ReservationStatus, StoreConfig, ThemeConfig

TIME_PATTERN = re.compile(
    r"(?<!\d)([01]?\d|2[0-3])\s*(?::|시)\s*([0-5]\d)(?:\s*분)?(?!\d)"
)
REMAINING_ZERO_PATTERN = re.compile(r"(?:잔여|남은\s*수량)\s*[:：]?\s*0(?:\D|$)")
REMAINING_POSITIVE_PATTERN = re.compile(
    r"(?:잔여|남은\s*수량)\s*[:：]?\s*[1-9]\d*(?:\D|$)"
)
RESERVED_TERMS = (
    "예약완료",
    "예약 완료",
    "예약불가",
    "예약 불가",
    "매진",
    "sold out",
    "soldout",
    "reserved",
)
CLOSED_TERMS = ("마감", "closed", "종료", "전화문의")
AVAILABLE_TERMS = (
    "예약가능",
    "예약 가능",
    "available",
    "예약하기",
    "신청",
)
NON_SLOT_NOISE_TERMS = (
    "고객센터",
    "운영시간",
    "온라인 문의접수",
    "사업자정보",
    "통신판매업",
    "개인정보처리방침",
    "이용약관",
    "대표이사",
    "naver cloud",
    "예약/주문 스마트봇",
)


def _clean(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _attribute(locator: Locator, name: str) -> str:
    try:
        return locator.get_attribute(name) or ""
    except PlaywrightError:
        return ""


def _candidate_text(locator: Locator) -> str:
    parts: list[str] = []
    try:
        parts.append(locator.inner_text() or "")
    except PlaywrightError:
        pass
    for name in (
        "value",
        "data-time",
        "data-start-time",
        "data-time-text",
        "data-status",
        "aria-label",
        "title",
        "class",
    ):
        parts.append(_attribute(locator, name))
    return " ".join(part for part in parts if part)


def _extract_time(raw_text: str) -> str | None:
    match = TIME_PATTERN.search(raw_text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = match.group(2)
    prefix = _clean(raw_text[max(0, match.start() - 8) : match.start()])
    if "오후" in prefix and hour < 12:
        hour += 12
    elif "오전" in prefix and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _is_non_slot_noise(raw_text: str, context: str) -> bool:
    normalized = _clean(f"{raw_text} {context}")
    return any(term in normalized for term in NON_SLOT_NOISE_TERMS)


def _context_text(locator: Locator) -> str:
    try:
        return str(
            locator.evaluate(
                """
                node => {
                    const parent = node.closest(
                        '[data-theme], [data-theme-name], article, section, tr, li, '
                        + '.theme, .product, .booking, .reservation, .item, .card'
                    );
                    return parent ? parent.innerText.slice(0, 1600) : '';
                }
                """
            )
            or ""
        )
    except PlaywrightError:
        return ""


def _match_theme(
    text: str,
    store_config: StoreConfig,
    forced_theme: ThemeConfig | None,
) -> ThemeConfig | None:
    if forced_theme is not None:
        return forced_theme
    normalized = _clean(text)
    for theme in sorted(
        store_config.themes,
        key=lambda item: len(item.theme_name),
        reverse=True,
    ):
        if _clean(theme.theme_name) in normalized:
            return theme
    if len(store_config.themes) == 1:
        return store_config.themes[0]
    return None


def _status(
    locator: Locator,
    raw_text: str,
    non_actionable_links_reserved: bool = False,
) -> ReservationStatus:
    normalized = _clean(raw_text)
    if REMAINING_ZERO_PATTERN.search(normalized):
        return "reserved"
    if any(term in normalized for term in RESERVED_TERMS):
        return "reserved"
    if any(term in normalized for term in CLOSED_TERMS):
        return "closed"

    try:
        disabled = locator.is_disabled()
    except PlaywrightError:
        disabled = False
    aria_disabled = _attribute(locator, "aria-disabled").casefold() == "true"
    class_text = _clean(_attribute(locator, "class"))
    classes = set(class_text.split())
    if disabled or aria_disabled or classes.intersection(
        {
            "disabled",
            "disable",
            "soldout",
            "sold-out",
            "sold_out",
            "booked",
            "end",
            "closed",
        }
    ) or any(
        marker in class_text
        for marker in (
            "disabled",
            "soldout",
            "sold-out",
            "sold out",
            "booked",
            "unavailable",
            "unselectable",
            "booking closed",
            "reservation closed",
        )
    ):
        return "reserved"
    if REMAINING_POSITIVE_PATTERN.search(normalized):
        return "available"
    if any(term in normalized for term in AVAILABLE_TERMS):
        return "available"

    try:
        tag_name = str(locator.evaluate("node => node.tagName.toLowerCase()"))
    except PlaywrightError:
        tag_name = ""
    if (
        non_actionable_links_reserved
        and tag_name == "a"
        and not _attribute(locator, "href")
        and not _attribute(locator, "onclick")
    ):
        return "reserved"
    if tag_name in {"button", "a", "input"}:
        return "available"
    return "unknown"


def _click_public_date(scope: Page | Frame, target_date: date) -> bool:
    tokens = {
        target_date.isoformat(),
        target_date.strftime("%Y%m%d"),
        target_date.strftime("%Y/%m/%d"),
        f"{target_date.month}/{target_date.day}",
        f"{target_date.month}.{target_date.day}",
        f"{target_date.month}월 {target_date.day}일",
        f"{target_date.month}월{target_date.day}일",
    }
    return bool(
        scope.evaluate(
        """
        ({tokens, day, month}) => {
            const attributes = [
                'data-date', 'data-day', 'data-value', 'data-date-time',
                'data-testid', 'value',
                'href', 'onclick', 'aria-label', 'title', 'datetime'
            ];
            const controls = [...document.querySelectorAll(
                'button, a, [role="button"], [role="gridcell"], td, li, label'
            )];
            const visible = node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
            };
            for (const node of controls) {
                if (!visible(node)) continue;
                const haystack = attributes
                    .map(name => node.getAttribute(name) || '')
                    .join(' ');
                if (tokens.some(token => haystack.includes(token))) {
                    node.click();
                    return true;
                }
                const text = (node.textContent || '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const monthDay = [
                    `${month}/${day}`,
                    `${month}.${day}`,
                    `${month}월 ${day}일`,
                    `${month}월${day}일`,
                ];
                if (monthDay.some(token => text.includes(token))) {
                    node.click();
                    return true;
                }
            }
            const calendars = [...document.querySelectorAll(
                '[class*="calendar"], [class*="datepicker"], '
                + '[class*="date-picker"], [role="grid"]'
            )];
            for (const calendar of calendars) {
                const nodes = [...calendar.querySelectorAll(
                    'button, a, [role="button"], [role="gridcell"], td'
                )];
                const exact = nodes.find(node =>
                    visible(node) && node.textContent.trim() === String(day)
                );
                if (exact) {
                    exact.click();
                    return true;
                }
            }
            return false;
        }
        """,
        {
            "tokens": sorted(tokens),
            "day": target_date.day,
            "month": target_date.month,
        },
        )
    )


def select_public_date(page: Page, target_date: date) -> bool:
    """Click a public calendar date in the page or an embedded public frame."""
    clicked = False
    scopes: list[Page | Frame] = [page]
    scopes.extend(
        frame for frame in page.frames if frame != page.main_frame
    )
    for scope in scopes:
        try:
            if _click_public_date(scope, target_date):
                clicked = True
        except PlaywrightError:
            continue
    if clicked:
        page.wait_for_timeout(700)
    return clicked


def wait_for_public_time_controls(
    page: Page,
    timeout_ms: int = 4_000,
) -> bool:
    """Wait briefly for a public schedule rendered in the page or a frame."""
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        scopes: list[Page | Frame] = [page]
        scopes.extend(
            frame for frame in page.frames if frame != page.main_frame
        )
        for scope in scopes:
            try:
                body_text = scope.locator("body").inner_text(timeout=750)
            except PlaywrightError:
                continue
            if TIME_PATTERN.search(body_text):
                return True
        page.wait_for_timeout(250)
    return False


def collect_public_slots(
    page: Page,
    store_config: StoreConfig,
    target_date: date,
    forced_theme: ThemeConfig | None = None,
    non_actionable_links_reserved: bool = False,
) -> list[ReservationSlot]:
    """Read public time controls in the page and embedded frames."""
    selector = (
        "[data-time], button, a, [role=button], [role=option], "
        "li, td, label, input[type=button], input[type=radio], .time, .slot"
    )
    collected: dict[tuple[str, str], ReservationSlot] = {}
    scopes: list[Page | Frame] = [page]
    scopes.extend(
        frame for frame in page.frames if frame != page.main_frame
    )
    for scope in scopes:
        try:
            candidates = scope.locator(selector)
            candidate_count = min(candidates.count(), 1200)
        except PlaywrightError:
            continue
        for index in range(candidate_count):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible():
                    continue
            except PlaywrightError:
                continue

            raw_text = _candidate_text(candidate)
            time_value = _extract_time(raw_text)
            if time_value is None:
                continue

            raw_date = (
                _attribute(candidate, "data-date")
                or _attribute(candidate, "data-day")
                or _attribute(candidate, "data-date-time")
                or _attribute(candidate, "datetime")
            )
            if raw_date and target_date.isoformat() not in raw_date:
                continue

            context = _context_text(candidate)
            if _is_non_slot_noise(raw_text, context):
                continue
            theme = _match_theme(
                f"{raw_text} {context}",
                store_config,
                forced_theme,
            )
            if theme is None:
                continue

            status = _status(
                candidate,
                raw_text,
                non_actionable_links_reserved=non_actionable_links_reserved,
            )
            expected_revenue = (
                theme.estimated_booking_value(
                    store_config.avg_people, target_date
                )
                if status == "reserved"
                else 0.0
            )
            slot = ReservationSlot(
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
            key = (theme.theme_name, time_value)
            existing = collected.get(key)
            status_rank = {
                "unknown": 0,
                "closed": 1,
                "available": 2,
                "reserved": 3,
            }
            if (
                existing is None
                or status_rank[slot.status] > status_rank[existing.status]
            ):
                collected[key] = slot
    return sorted(
        collected.values(),
        key=lambda slot: (slot.theme_name, slot.time),
    )


def complete_with_schedule_times(
    slots: list[ReservationSlot],
    store_config: StoreConfig,
    target_date: date,
    *,
    missing_status: ReservationStatus = "reserved",
) -> list[ReservationSlot]:
    """Fill configured operating times that public widgets hide when sold out.

    Some public booking widgets only render available times. When a theme has
    schedule_times in stores.yaml, missing configured times are treated as
    reserved by adapters that explicitly opt in to this behavior.
    """
    collected: dict[tuple[str, str], ReservationSlot] = {
        (slot.theme_name, slot.time): slot for slot in slots
    }
    crawled_at = datetime.now(timezone.utc)
    for theme in store_config.themes:
        for raw_time in theme.schedule_times:
            time_value = _extract_time(raw_time) or raw_time.strip()
            if not time_value:
                continue
            key = (theme.theme_name, time_value)
            if key in collected:
                continue
            expected_revenue = (
                theme.estimated_booking_value(store_config.avg_people, target_date)
                if missing_status == "reserved"
                else 0.0
            )
            collected[key] = ReservationSlot(
                store_id=store_config.store_id,
                theme_name=theme.theme_name,
                date=target_date,
                time=time_value,
                status=missing_status,
                price=theme.price,
                avg_people=store_config.avg_people,
                expected_revenue=expected_revenue,
                crawled_at=crawled_at,
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
