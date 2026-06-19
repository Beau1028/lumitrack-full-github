from __future__ import annotations

import unittest
from datetime import date

from playwright.sync_api import sync_playwright

from scraper.adapters.cubeescape_adapter import CubeEscapeAdapter
from scraper.adapters.earthstar_adapter import EarthstarAdapter
from scraper.adapters.frank_adapter import FrankAdapter
from scraper.adapters.horror_switch_adapter import HorrorSwitchAdapter
from scraper.adapters.oasis_adapter import OasisAdapter
from scraper.adapters.sinbi_adapter import SinbiAdapter
from scraper.models import StoreConfig, ThemeConfig


def store(adapter_type: str, theme_name: str, price: int = 28_000) -> StoreConfig:
    return StoreConfig(
        store_id="new-store",
        store_name="신규 매장",
        region="서울",
        booking_url="https://example.com",
        adapter_type=adapter_type,
        avg_people=2.7,
        themes=(
            ThemeConfig(
                theme_name=theme_name,
                genre="테스트",
                price=price,
                duration_minutes=70,
            ),
        ),
    )


class NewAdapterTest(unittest.TestCase):
    def parse(self, adapter, config: StoreConfig, html: str):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = adapter.parse_slots(page, config, date(2026, 6, 15))
            browser.close()
        return slots

    def test_sinbi_reads_available_and_closed_times(self) -> None:
        html = """
        <div class="theme_box">
          <h3 class="h3_theme">나이트워크 (판타지)</h3>
          <div class="theme_div">시간 : 100분</div>
          <ul class="reserve_Time">
            <li><a class="end"><span class="time">10:00</span>
              <span>예약마감</span></a></li>
            <li><a><span class="time">12:00</span>
              <span>예약가능</span></a></li>
          </ul>
        </div>
        """
        slots = self.parse(
            SinbiAdapter(), store("sinbi", "나이트워크", 35_000), html
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "reserved"), ("12:00", "available")],
        )

    def test_earthstar_reads_public_reservation_cards(self) -> None:
        html = """
        <section class="res-item">
          <h2>스텔라</h2>
          <table><tr><th>장르</th><td>드라마</td></tr>
          <tr><th>시간</th><td>70분</td></tr></table>
          <div class="res-times-btn"><button><label>예약불가</label>
            <span>10:00</span></button></div>
          <div class="res-times-btn"><button><label>예약가능</label>
            <span>11:30</span></button></div>
        </section>
        """
        slots = self.parse(
            EarthstarAdapter(), store("earthstar", "스텔라", 23_000), html
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "reserved"), ("11:30", "available")],
        )

    def test_cubeescape_reads_table_rows(self) -> None:
        html = """
        <table id="show_themeTimeArea"><tbody>
          <tr><td>10:00 ~ 11:00</td><td>피라미드</td><td>매진</td></tr>
          <tr><td>11:20 ~ 12:20</td><td>피라미드</td><td>예약하기</td></tr>
        </tbody></table>
        """
        slots = self.parse(
            CubeEscapeAdapter(), store("cubeescape", "피라미드", 20_000), html
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "reserved"), ("11:20", "available")],
        )

    def test_horror_switch_uses_remaining_capacity(self) -> None:
        html = """
        <div class="restheme"><figcaption>에덴병원</figcaption></div>
        <button class="restimes-button">14:00 Available 0/6</button>
        <button class="restimes-button">14:20 Available 3/6</button>
        """
        slots = self.parse(
            HorrorSwitchAdapter(),
            store("horror_switch", "에덴병원", 23_000),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("14:00", "reserved"), ("14:20", "available")],
        )

    def test_frank_reads_each_public_theme_tab(self) -> None:
        html = """
        <input name="rev_days" value="2026-06-15">
        <div id="theme_area"><a href="#">My Private Heaven</a></div>
        <div id="theme_time_area">
          <a class="none">10시 30분</a>
          <a>12시 00분</a>
        </div>
        """
        slots = self.parse(
            FrankAdapter(),
            store("frank", "My Private Heaven", 28_000),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:30", "reserved"), ("12:00", "available")],
        )

    def test_oasis_reads_loaded_ticket_buttons(self) -> None:
        html = """
        <div id="tm_name1">업사이드 다운</div>
        <button class="room_btn btn-closed" data-tm="1"
          data-time="10:00" disabled>10:00</button>
        <button class="room_btn btn-opened" data-tm="1"
          data-time="12:00">12:00</button>
        """
        slots = self.parse(
            OasisAdapter(),
            store("oasis", "업사이드 다운", 29_000),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "reserved"), ("12:00", "available")],
        )


if __name__ == "__main__":
    unittest.main()
