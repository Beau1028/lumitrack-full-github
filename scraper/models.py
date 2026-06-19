from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Mapping

ReservationStatus = Literal["available", "reserved", "closed", "unknown"]
VALID_STATUSES: set[str] = {"available", "reserved", "closed", "unknown"}
UNVERIFIED_PRICE_MARKERS = (
    "추정",
    "임시",
    "실제 결제액 확인 필요",
)


def normalize_party_prices(value: object) -> dict[int, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[int, int] = {}
    for raw_people, raw_price in value.items():
        try:
            people = int(raw_people)
            total_price = int(raw_price)
        except (TypeError, ValueError):
            continue
        if people > 0 and total_price > 0:
            result[people] = total_price
    return dict(sorted(result.items()))


def effective_party_size(avg_people: float, min_people: int = 1) -> float:
    """Return the party size used for revenue and per-person estimates."""
    return max(float(avg_people), float(max(min_people, 1)))


def estimate_booking_value(
    *,
    avg_people: float,
    target_date: date | None = None,
    price: int = 0,
    min_people: int = 1,
    party_prices: Mapping[int, int] | None = None,
    weekday_party_prices: Mapping[int, int] | None = None,
    weekend_party_prices: Mapping[int, int] | None = None,
) -> float:
    """Estimate one booking total from public party-size pricing.

    Party price mappings contain the total checkout amount, not a per-person
    price. A 2.7-person assumption is interpolated between the 2-person and
    3-person totals. This remains an estimate and is never actual sales.
    """
    target_people = effective_party_size(avg_people, min_people)
    selected_prices: Mapping[int, int] = party_prices or {}
    if target_date is not None:
        dated_prices = (
            weekend_party_prices
            if target_date.weekday() >= 5
            else weekday_party_prices
        )
        if dated_prices:
            selected_prices = dated_prices

    tiers = normalize_party_prices(selected_prices)
    if not tiers:
        return round(max(int(price), 0) * target_people, 2)

    people_counts = sorted(tiers)
    if target_people <= people_counts[0]:
        return float(tiers[people_counts[0]])
    if target_people >= people_counts[-1]:
        return float(tiers[people_counts[-1]])

    lower = max(count for count in people_counts if count <= target_people)
    upper = min(count for count in people_counts if count >= target_people)
    if lower == upper:
        return float(tiers[lower])
    ratio = (target_people - lower) / (upper - lower)
    return round(tiers[lower] + ((tiers[upper] - tiers[lower]) * ratio), 2)


@dataclass(frozen=True)
class ThemeConfig:
    theme_name: str
    genre: str
    price: int
    duration_minutes: int
    price_note: str = ""
    price_source_url: str = ""
    price_verified_at: str = ""
    min_people: int = 1
    max_people: int = 0
    party_prices: dict[int, int] = field(default_factory=dict)
    weekday_party_prices: dict[int, int] = field(default_factory=dict)
    weekend_party_prices: dict[int, int] = field(default_factory=dict)
    public_schedule_url: str = ""
    schedule_times: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThemeConfig":
        price_note = str(data.get("price_note", "")).strip()
        party_prices = normalize_party_prices(data.get("party_prices"))
        weekday_party_prices = normalize_party_prices(
            data.get("weekday_party_prices")
        )
        weekend_party_prices = normalize_party_prices(
            data.get("weekend_party_prices")
        )
        price = int(data.get("price", 0))
        has_exact_tiers = bool(
            party_prices or weekday_party_prices or weekend_party_prices
        )
        if not has_exact_tiers and any(
            marker in price_note for marker in UNVERIFIED_PRICE_MARKERS
        ):
            price = 0
        return cls(
            theme_name=str(data["theme_name"]).strip(),
            genre=str(data.get("genre", "")).strip(),
            price=price,
            duration_minutes=int(data.get("duration_minutes", 0)),
            price_note=price_note,
            price_source_url=str(data.get("price_source_url", "")).strip(),
            price_verified_at=str(data.get("price_verified_at", "")).strip(),
            min_people=int(data.get("min_people", 1)),
            max_people=int(data.get("max_people", 0)),
            party_prices=party_prices,
            weekday_party_prices=weekday_party_prices,
            weekend_party_prices=weekend_party_prices,
            public_schedule_url=str(
                data.get("public_schedule_url", "")
            ).strip(),
            schedule_times=tuple(
                str(value).strip()
                for value in data.get("schedule_times", [])
                if str(value).strip()
            ),
        )

    def estimated_booking_value(
        self, avg_people: float, target_date: date | None = None
    ) -> float:
        return estimate_booking_value(
            avg_people=avg_people,
            target_date=target_date,
            price=self.price,
            min_people=self.min_people,
            party_prices=self.party_prices,
            weekday_party_prices=self.weekday_party_prices,
            weekend_party_prices=self.weekend_party_prices,
        )

    def pricing_summary(self) -> str:
        def format_tiers(label: str, tiers: Mapping[int, int]) -> str:
            values = ", ".join(
                f"{people}인 {total_price:,}원"
                for people, total_price in sorted(tiers.items())
            )
            return f"{label}{values}" if values else ""

        sections = [
            format_tiers("", self.party_prices),
            format_tiers("평일 ", self.weekday_party_prices),
            format_tiers("주말 ", self.weekend_party_prices),
        ]
        sections = [section for section in sections if section]
        if sections:
            return " / ".join(sections)
        if self.price > 0:
            return f"1인 {self.price:,}원"
        return "공식 가격 미확인"


@dataclass(frozen=True)
class StoreConfig:
    store_id: str
    store_name: str
    region: str
    booking_url: str
    adapter_type: str
    avg_people: float
    collection_note: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    brand_name: str = ""
    brand_logo_url: str = ""
    map_note: str = ""
    themes: tuple[ThemeConfig, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoreConfig":
        def optional_float(value: object) -> float | None:
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return cls(
            store_id=str(data["store_id"]).strip(),
            store_name=str(data["store_name"]).strip(),
            region=str(data["region"]).strip(),
            booking_url=str(data["booking_url"]).strip(),
            adapter_type=str(data.get("adapter_type", "generic")).strip(),
            avg_people=float(data.get("avg_people", 1.0)),
            collection_note=str(data.get("collection_note", "")).strip(),
            address=str(data.get("address", "")).strip(),
            latitude=optional_float(data.get("latitude")),
            longitude=optional_float(data.get("longitude")),
            brand_name=str(data.get("brand_name", "")).strip(),
            brand_logo_url=str(data.get("brand_logo_url", "")).strip(),
            map_note=str(data.get("map_note", "")).strip(),
            themes=tuple(
                ThemeConfig.from_dict(theme) for theme in data.get("themes", [])
            ),
        )

    def theme_by_name(self, theme_name: str) -> ThemeConfig | None:
        normalized = " ".join(theme_name.split()).casefold()
        for theme in self.themes:
            if " ".join(theme.theme_name.split()).casefold() == normalized:
                return theme
        return None


@dataclass(frozen=True)
class ReservationSlot:
    store_id: str
    theme_name: str
    date: date
    time: str
    status: ReservationStatus
    price: int
    avg_people: float
    expected_revenue: float
    crawled_at: datetime
    genre: str = ""
    duration_minutes: int = 0
    price_note: str = ""
    price_source_url: str = ""
    price_verified_at: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Unsupported reservation status: {self.status}")
