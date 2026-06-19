from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scraper.analytics import (
    booking_rate,
    combine_store_revenue_estimates,
    genre_monthly_summary,
    load_manual_estimates,
    market_radius_summary,
    operations_by,
    price_strategy_matrix,
    project_monthly_revenue,
    rate_by,
    store_efficiency,
    store_growth_trends,
    store_monthly_projections,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]


class AnalyticsTest(unittest.TestCase):
    def test_manual_keyescape_estimates_are_kept_separate(self) -> None:
        stores, themes, metadata = load_manual_estimates(
            PROJECT_DIR / "manual_estimates.yaml"
        )

        self.assertEqual(len(stores), 8)
        self.assertEqual(len(themes), 19)
        self.assertEqual(
            metadata["source_type"],
            "user_provided_manual_observation",
        )
        self.assertEqual(stores["monthly_revenue_min"].sum(), 447_015_858)
        self.assertEqual(stores["monthly_revenue_max"].sum(), 470_286_000)

    def test_booking_rate_excludes_closed_and_unknown_slots(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "status": ["reserved", "available", "closed", "unknown"],
                "region": ["강남"] * 4,
            }
        )
        self.assertEqual(booking_rate(frame), 50.0)
        grouped = rate_by(frame, "region")
        self.assertEqual(grouped.iloc[0]["booking_rate"], 50.0)
        self.assertEqual(grouped.iloc[0]["total_slots"], 2)

    def test_industry_average_combines_auto_and_manual_stores(self) -> None:
        automatic = pd.DataFrame(
            [
                {
                    "store_id": "auto_a",
                    "store_name": "자동 A",
                    "region": "서울",
                    "booking_rate": 50,
                    "monthly_revenue": 10_000_000,
                    "observed_days": 7,
                    "observed_weekdays": 7,
                    "observed_weekday_names": "월, 화, 수, 목, 금, 토, 일",
                    "confidence": "높음",
                },
                {
                    "store_id": "manual_a",
                    "store_name": "중복 자동값",
                    "region": "서울",
                    "booking_rate": 10,
                    "monthly_revenue": 1_000_000,
                },
            ]
        )
        manual = pd.DataFrame(
            [
                {
                    "store_id": "manual_a",
                    "store_name": "수동 A",
                    "region": "서울",
                    "booking_rate_min": 60,
                    "booking_rate_max": 70,
                    "daily_revenue_min": 600_000,
                    "daily_revenue_max": 700_000,
                    "monthly_revenue_min": 18_000_000,
                    "monthly_revenue_max": 21_000_000,
                }
            ]
        )

        result = combine_store_revenue_estimates(automatic, manual, 30)

        self.assertEqual(len(result), 2)
        self.assertEqual(result["monthly_revenue_min"].sum(), 28_000_000)
        self.assertEqual(result["monthly_revenue_max"].sum(), 31_000_000)
        self.assertEqual(
            result["monthly_revenue_mid"].mean(),
            14_750_000,
        )
        self.assertEqual(
            set(result["estimate_source"]),
            {"자동 수집", "수동 관측"},
        )

    def test_operations_include_slots_revenue_and_price_coverage(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "store_name": ["A", "A", "A"],
                "status": ["reserved", "available", "reserved"],
                "price": [20_000, 20_000, 0],
                "expected_revenue": [60_000, 0, 0],
            }
        )
        result = operations_by(frame, "store_name").iloc[0]
        self.assertEqual(result["total_slots"], 3)
        self.assertEqual(result["reserved_slots"], 2)
        self.assertEqual(result["available_slots"], 1)
        self.assertEqual(result["estimated_revenue"], 60_000)
        self.assertAlmostEqual(result["price_coverage"], 66.7)

    def test_month_projection_uses_observed_daily_average(self) -> None:
        frame = pd.DataFrame(
            {
                "date": [date(2026, 6, 1), date(2026, 6, 8)],
                "weekday_number": [0, 0],
                "expected_revenue": [100_000, 200_000],
            }
        )
        projection = project_monthly_revenue(frame, 2026, 6)
        self.assertEqual(projection["observed_days"], 2)
        self.assertEqual(projection["observed_revenue"], 300_000)
        self.assertEqual(projection["daily_average"], 150_000)
        self.assertEqual(projection["projected_revenue"], 4_500_000)

    def test_store_month_projection_uses_each_weekday_pattern(self) -> None:
        frame = pd.DataFrame(
            {
                "id": list(range(1, 8)),
                "store_id": ["a"] * 7,
                "store_name": ["A"] * 7,
                "region": ["서울"] * 7,
                "date": [date(2026, 6, day) for day in range(1, 8)],
                "weekday_number": list(range(7)),
                "status": ["reserved"] * 7,
                "expected_revenue": [
                    100_000,
                    200_000,
                    300_000,
                    400_000,
                    500_000,
                    600_000,
                    700_000,
                ],
            }
        )

        result = store_monthly_projections(frame, 2026, 6).iloc[0]

        self.assertEqual(result["observed_weekdays"], 7)
        self.assertEqual(result["confidence"], "높음")
        self.assertEqual(result["월_daily_revenue"], 100_000)
        self.assertEqual(result["일_daily_revenue"], 700_000)
        self.assertEqual(result["monthly_revenue"], 11_500_000)

    def test_store_month_projection_marks_missing_weekdays_low_confidence(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1],
                "store_id": ["a"],
                "store_name": ["A"],
                "region": ["서울"],
                "date": [date(2026, 6, 1)],
                "weekday_number": [0],
                "status": ["reserved"],
                "expected_revenue": [100_000],
            }
        )

        result = store_monthly_projections(frame, 2026, 6).iloc[0]

        self.assertEqual(result["observed_weekdays"], 1)
        self.assertEqual(result["coverage"], 14.3)
        self.assertEqual(result["confidence"], "낮음")
        self.assertEqual(result["monthly_revenue"], 3_000_000)

    def test_genre_summary_reports_average_and_total_separately(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2],
                "store_id": ["a", "b"],
                "store_name": ["A", "B"],
                "region": ["서울", "서울"],
                "theme_name": ["테마 A", "테마 B"],
                "genre": ["미스터리", "미스터리"],
                "date": [date(2026, 6, 1), date(2026, 6, 1)],
                "weekday_number": [0, 0],
                "status": ["reserved", "reserved"],
                "expected_revenue": [100_000, 300_000],
            }
        )

        result = genre_monthly_summary(frame, 2026, 6).iloc[0]

        self.assertEqual(result["store_count"], 2)
        self.assertEqual(result["theme_count"], 2)
        self.assertEqual(result["average_monthly_revenue"], 6_000_000)
        self.assertEqual(result["total_monthly_revenue"], 12_000_000)

    def test_store_growth_trends_compare_recent_and_previous_windows(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "store_id": ["a", "a", "a"],
                "store_name": ["A"] * 3,
                "region": ["서울"] * 3,
                "date": [
                    date(2026, 6, 2),
                    date(2026, 6, 10),
                    date(2026, 6, 11),
                ],
                "status": ["reserved", "reserved", "available"],
                "expected_revenue": [100_000, 300_000, 0],
            }
        )

        result = store_growth_trends(frame, date(2026, 6, 14)).iloc[0]

        self.assertEqual(result["current_revenue"], 300_000)
        self.assertEqual(result["previous_revenue"], 100_000)
        self.assertEqual(result["revenue_delta_pct"], 200.0)
        self.assertEqual(result["trend_label"], "상승")

    def test_price_strategy_matrix_marks_low_price_high_demand(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "store_id": ["a", "a", "a", "a"],
                "store_name": ["A"] * 4,
                "region": ["서울"] * 4,
                "theme_name": ["저가인기", "저가인기", "고가부진", "고가부진"],
                "genre": ["공포"] * 4,
                "status": ["reserved", "reserved", "available", "available"],
                "expected_revenue": [50_000, 50_000, 0, 0],
                "booking_value_estimate": [50_000, 50_000, 90_000, 90_000],
            }
        )
        catalog = pd.DataFrame(
            {
                "store_id": ["a", "a"],
                "theme_name": ["저가인기", "고가부진"],
                "booking_value_estimate": [50_000, 90_000],
                "per_person_estimate": [20_000, 40_000],
            }
        )

        result = price_strategy_matrix(frame, catalog)
        labels = dict(zip(result["theme_name"], result["strategy"]))

        self.assertEqual(labels["저가인기"], "가격 인상 여지")
        self.assertEqual(labels["고가부진"], "가격 저항 가능성")

    def test_store_efficiency_reports_revenue_per_slot_and_hour(self) -> None:
        frame = pd.DataFrame(
            {
                "id": [1, 2],
                "store_id": ["a", "a"],
                "store_name": ["A", "A"],
                "region": ["서울", "서울"],
                "theme_name": ["테마1", "테마2"],
                "status": ["reserved", "available"],
                "duration_minutes": [60, 120],
                "expected_revenue": [120_000, 0],
            }
        )

        result = store_efficiency(frame).iloc[0]

        self.assertEqual(result["measured_slots"], 2)
        self.assertEqual(result["observed_hours"], 3)
        self.assertEqual(result["revenue_per_measured_slot"], 60_000)
        self.assertEqual(result["revenue_per_operating_hour"], 40_000)

    def test_market_radius_summary_groups_nearby_store_revenue(self) -> None:
        status = pd.DataFrame(
            {
                "store_id": ["a", "b", "c"],
                "store_name": ["A", "B", "C"],
                "region": ["서울", "서울", "서울"],
                "latitude": [37.55, 37.5508, 37.57],
                "longitude": [126.92, 126.9207, 126.95],
            }
        )
        projection = pd.DataFrame(
            {
                "store_id": ["a", "b", "c"],
                "monthly_revenue_mid": [10_000_000, 20_000_000, 30_000_000],
                "estimate_source": ["자동 수집"] * 3,
            }
        )

        result = market_radius_summary(projection, status, radius_meters=150)
        row = result[result["anchor_store_id"].eq("a")].iloc[0]

        self.assertEqual(row["nearby_store_count"], 2)
        self.assertEqual(row["monthly_revenue_sum"], 30_000_000)
        self.assertEqual(row["top_store_name"], "B")


if __name__ == "__main__":
    unittest.main()
