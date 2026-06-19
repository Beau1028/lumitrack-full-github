from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from scraper.config import load_stores
from scraper.models import (
    ThemeConfig,
    effective_party_size,
    estimate_booking_value,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]


class PricingTest(unittest.TestCase):
    def test_shortstories_seongsu_has_all_public_theme_prices(self) -> None:
        stores = load_stores(PROJECT_DIR / "stores.yaml")
        store = next(
            item for item in stores
            if item.store_id == "shortstories_seongsu"
        )
        self.assertEqual(
            {
                theme.theme_name: theme.price
                for theme in store.themes
            },
            {
                "쓰여진 문장 속에 구원이 없다면": 32_000,
                "존재할 자격": 32_000,
                "뱃사람의 별": 31_000,
                "쥐와 파시스트와 마지막 한 장": 25_000,
            },
        )

    def test_interpolates_party_total_for_average_party_size(self) -> None:
        value = estimate_booking_value(
            avg_people=2.7,
            party_prices={2: 54_000, 3: 75_000},
        )
        self.assertEqual(value, 68_700)

    def test_clamps_to_minimum_party_size(self) -> None:
        value = estimate_booking_value(
            avg_people=2.7,
            price=32_000,
            min_people=3,
        )
        self.assertEqual(value, 96_000)

    def test_tientang_city_uses_current_public_party_prices(self) -> None:
        value = estimate_booking_value(
            avg_people=2.7,
            min_people=2,
            party_prices={
                2: 58_000,
                3: 87_000,
                4: 116_000,
                5: 145_000,
                6: 174_000,
            },
        )
        self.assertEqual(value, 78_300)

    def test_effective_party_size_respects_minimum_people(self) -> None:
        self.assertEqual(effective_party_size(2.7, 3), 3.0)
        self.assertEqual(effective_party_size(2.7, 2), 2.7)

    def test_uses_weekend_party_prices(self) -> None:
        value = estimate_booking_value(
            avg_people=2.7,
            target_date=date(2026, 6, 20),
            weekday_party_prices={2: 120_000, 3: 132_000},
            weekend_party_prices={2: 110_000, 3: 147_000},
        )
        self.assertEqual(value, 135_900)

    def test_fixed_session_price_is_not_multiplied_by_people(self) -> None:
        theme = ThemeConfig.from_dict(
            {
                "theme_name": "우정",
                "genre": "이머시브",
                "price": 0,
                "duration_minutes": 100,
                "min_people": 3,
                "max_people": 5,
                "party_prices": {3: 240_000, 4: 240_000, 5: 240_000},
            }
        )
        self.assertEqual(theme.estimated_booking_value(2.7), 240_000)

    def test_parses_public_schedule_url(self) -> None:
        theme = ThemeConfig.from_dict(
            {
                "theme_name": "선택",
                "genre": "스릴러",
                "price": 31_000,
                "duration_minutes": 65,
                "public_schedule_url": "https://example.com/public-schedule",
            }
        )
        self.assertEqual(
            theme.public_schedule_url,
            "https://example.com/public-schedule",
        )

    def test_verified_representative_price_remains_billable(self) -> None:
        theme = ThemeConfig.from_dict(
            {
                "theme_name": "공식 가격 테마",
                "genre": "드라마",
                "price": 23_000,
                "duration_minutes": 70,
                "price_note": "공식 가격 안내 일반 테마 대표가",
            }
        )
        self.assertEqual(theme.price, 23_000)
        self.assertEqual(theme.estimated_booking_value(2.7), 62_100)

    def test_estimated_price_is_still_excluded(self) -> None:
        theme = ThemeConfig.from_dict(
            {
                "theme_name": "추정 가격 테마",
                "genre": "드라마",
                "price": 28_000,
                "duration_minutes": 70,
                "price_note": "공식 공개 예약가 대표 추정",
            }
        )
        self.assertEqual(theme.price, 0)


if __name__ == "__main__":
    unittest.main()
