from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.adapters.amazed_adapter import AmazedAdapter
from scraper.adapters.codek_adapter import CodeKAdapter
from scraper.adapters.murderparker_adapter import MurderParkerAdapter
from scraper.config import load_stores
from scraper.models import StoreConfig, ThemeConfig
from scraper.runner import RETIRED_STORE_IDS

PROJECT_DIR = Path(__file__).resolve().parents[1]
TARGET_DATE = date(2026, 6, 16)


def make_store(
    adapter_type: str,
    themes: tuple[ThemeConfig, ...],
) -> StoreConfig:
    return StoreConfig(
        store_id="requested-store",
        store_name="요청 매장",
        region="서울",
        booking_url="https://example.com",
        adapter_type=adapter_type,
        avg_people=2.7,
        themes=themes,
    )


class RequestedStoreAdapterTest(unittest.TestCase):
    def parse(self, adapter, store: StoreConfig, html: str):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = adapter.parse_slots(page, store, TARGET_DATE)
            browser.close()
        return slots

    def test_codek_reads_on_and_off_slots(self) -> None:
        themes = (
            ThemeConfig("꼬레아 우라 A", "드라마", 28_000, 75),
            ThemeConfig("꼬레아 우라 B", "드라마", 28_000, 75),
        )
        html = """
        <div id="CQ1"><ul>
          <li class="timeOn">☆ 10:20</li>
          <li class="timeOff">★ 15:20</li>
        </ul></div>
        <div id="CQ2"><ul>
          <li class="timeOn">☆ 11:10</li>
        </ul></div>
        """
        slots = self.parse(
            CodeKAdapter(),
            make_store("codek", themes),
            html,
        )
        self.assertEqual(
            [(slot.theme_name, slot.time, slot.status) for slot in slots],
            [
                ("꼬레아 우라 A", "10:20", "available"),
                ("꼬레아 우라 A", "15:20", "reserved"),
                ("꼬레아 우라 B", "11:10", "available"),
            ],
        )

    def test_murderparker_normalizes_branch_and_detail_suffixes(self) -> None:
        themes = (
            ThemeConfig.from_dict(
                {
                    "theme_name": "칠칠77",
                    "genre": "미스터리",
                    "price": 0,
                    "duration_minutes": 70,
                    "party_prices": {2: 50_000, 3: 72_000},
                }
            ),
        )
        html = """
        <div class="reservTime">
          <h3>칠칠77(70분 프리미엄)_홍대1호점</h3>
          <ul>
            <li><span>11:20</span><span>예약완료</span></li>
            <li><span>12:45</span><span>예약가능</span></li>
          </ul>
        </div>
        """
        slots = self.parse(
            MurderParkerAdapter(),
            make_store("murderparker", themes),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("11:20", "reserved"), ("12:45", "available")],
        )
        self.assertEqual(slots[0].expected_revenue, 65_400)

    def test_amazed_reads_disabled_public_times(self) -> None:
        theme = ThemeConfig("SHIFT:고쿄의 하루", "SF", 38_000, 100)
        html = """
        <div class="booked-appt-list" data-list-date="2026-06-16">
          <div class="timeslot">
            <button disabled><span>10:00</span><span>매진되었습니다</span></button>
          </div>
          <div class="timeslot">
            <button><span>10:55</span><span>바로 예매하기</span></button>
          </div>
        </div>
        """
        slots = self.parse(
            AmazedAdapter(),
            make_store("amazed", (theme,)),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "reserved"), ("10:55", "available")],
        )

    def test_requested_catalog_entries_and_pricing_are_present(self) -> None:
        stores = {store.store_id: store for store in load_stores(
            PROJECT_DIR / "stores.yaml"
        )}
        requested = {
            "codek_hongdae",
            "goldenkey_gangnam_timesquare",
            "murderparker_hongdae1",
            "sowoojoo_suwon",
            "questionmark_hongdae",
            "weyol_hongdae",
            "hotelleto_seongsu",
            "exodus_gangnam",
            "dreamescape_konkuk",
            "amazed_bupyeong3",
            "decoder_hongdae",
            "masterkey_prime_sinchon_public",
            "episode_gangnam",
        }
        self.assertTrue(requested.issubset(stores))
        hotel = stores["hotelleto_seongsu"].themes[0]
        self.assertEqual(hotel.estimated_booking_value(2.7), 200_000)

    def test_ticket_to_escape_is_retired_from_active_catalog(self) -> None:
        stores = {store.store_id: store for store in load_stores(
            PROJECT_DIR / "stores.yaml"
        )}
        self.assertNotIn("tickettoescape_hongdae", stores)
        self.assertIn("tickettoescape_hongdae", RETIRED_STORE_IDS)


if __name__ == "__main__":
    unittest.main()
