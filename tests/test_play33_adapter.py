from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.adapters.play33_adapter import Play33Adapter
from scraper.config import load_stores

PROJECT_DIR = Path(__file__).resolve().parents[1]


class Play33AdapterTest(unittest.TestCase):
    def test_public_reservation_list_is_normalized(self) -> None:
        store = next(
            item
            for item in load_stores(PROJECT_DIR / "stores.yaml")
            if item.store_id == "play33_konkuk"
        )
        html = """
        <section class="reslist">
          <div class="reslist-text">
            <strong>목격자 (5/7~)</strong>
            <div class="restimes"><ul>
              <li><button>예약 가능 <span>10:35</span></button></li>
              <li>예약 불가 12:00</li>
            </ul></div>
          </div>
        </section>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = Play33Adapter().parse_slots(
                page, store, date(2026, 6, 15)
            )
            browser.close()

        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:35", "available"), ("12:00", "reserved")],
        )

    def test_target_date_is_added_without_losing_branch(self) -> None:
        store = next(
            item
            for item in load_stores(PROJECT_DIR / "stores.yaml")
            if item.store_id == "play33_konkuk"
        )
        url = Play33Adapter().build_booking_url(store, date(2026, 6, 15))
        self.assertIn("branch=1", url)
        self.assertIn("date=2026-06-15", url)


if __name__ == "__main__":
    unittest.main()
