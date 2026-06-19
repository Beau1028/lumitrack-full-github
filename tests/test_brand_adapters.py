from __future__ import annotations

import unittest
from datetime import date

from playwright.sync_api import sync_playwright

from scraper.adapters.page_today_adapter import PageTodayAdapter
from scraper.adapters.xdungeon_adapter import XdungeonAdapter
from scraper.adapters.zero_world_adapter import ZeroWorldAdapter
from scraper.models import StoreConfig


class BrandAdapterTest(unittest.TestCase):
    def test_xdungeon_statuses_are_read_from_public_classes(self) -> None:
        store = StoreConfig(
            store_id="x",
            store_name="X",
            region="서울",
            booking_url="https://example.com",
            adapter_type="xdungeon",
            avg_people=3,
        )
        html = """
        <div class="thm_box"><div class="box">
          <div class="img_box"><p class="tit">테마 A</p></div>
          <div class="time_box"><ul>
            <li class="sale"><a href="/book">10:00</a></li>
            <li class="dead sale"><a>11:00</a></li>
          </ul></div>
        </div></div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = XdungeonAdapter().parse_slots(
                page, store, date(2026, 6, 15)
            )
            browser.close()

        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("11:00", "reserved")],
        )

    def test_page_today_reads_each_theme_pane(self) -> None:
        store = StoreConfig(
            store_id="page",
            store_name="오늘의 한 페이지",
            region="서울",
            booking_url="https://example.com",
            adapter_type="page_today",
            avg_people=3,
        )
        html = """
        <button data-bs-target="#theme0-tab-pane">버디</button>
        <div id="theme0-tab-pane">
          <button>10:00<br>예약 가능</button>
          <button class="disabled">11:00<br>예약 불가</button>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = PageTodayAdapter().parse_slots(
                page, store, date(2026, 6, 15)
            )
            browser.close()
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("11:00", "reserved")],
        )

    def test_zero_world_reads_disabled_times(self) -> None:
        store = StoreConfig(
            store_id="zero",
            store_name="제로월드",
            region="서울",
            booking_url="https://example.com",
            adapter_type="zero_world",
            avg_people=3,
        )
        html = """
        <div id="calendar">
          <div class="datepicker--cell-day"
               data-year="2026" data-month="5" data-date="15">15</div>
        </div>
        <label><input type="radio" name="themePK" value="1">[강남] 링</label>
        <div id="themeTimeWrap">
          <input name="reservationTime" value="10:00:00">
          <input name="reservationTime" value="11:30:00" disabled>
        </div>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = ZeroWorldAdapter().parse_slots(
                page, store, date(2026, 6, 15)
            )
            browser.close()
        self.assertEqual(
            [(slot.theme_name, slot.time, slot.status) for slot in slots],
            [
                ("링", "10:00", "available"),
                ("링", "11:30", "reserved"),
            ],
        )

if __name__ == "__main__":
    unittest.main()
