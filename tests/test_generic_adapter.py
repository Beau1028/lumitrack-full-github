from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.adapters.generic_adapter import GenericAdapter
from scraper.config import load_stores

PROJECT_DIR = Path(__file__).resolve().parents[1]


class GenericAdapterTest(unittest.TestCase):
    def test_keyword_fallback_reads_public_button_text(self) -> None:
        store = load_stores(PROJECT_DIR / "stores.sample.yaml")[0]
        html = """
        <section>
          <h2>사라진 연구원</h2>
          <button>19:30 예약가능</button>
          <button>21:00 예약완료</button>
        </section>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = GenericAdapter().parse_slots(page, store, date(2026, 6, 15))
            browser.close()

        self.assertEqual(len(slots), 2)
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("19:30", "available"), ("21:00", "reserved")],
        )


if __name__ == "__main__":
    unittest.main()
