from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from scraper.adapters.keyescape_adapter import KeyescapeAdapter
from scraper.adapters.naver_booking_adapter import NaverBookingAdapter
from scraper.adapters.shortstories_adapter import ShortstoriesAdapter
from scraper.models import StoreConfig, ThemeConfig


def store(adapter_type: str, themes: tuple[ThemeConfig, ...]) -> StoreConfig:
    return StoreConfig(
        store_id="public-store",
        store_name="공개 예약 매장",
        region="서울",
        booking_url="https://example.com",
        adapter_type=adapter_type,
        avg_people=2.7,
        themes=themes,
    )


class PublicBookingAdapterTest(unittest.TestCase):
    def parse(self, adapter, config: StoreConfig, html: str):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = adapter.parse_slots(page, config, date(2026, 6, 15))
            browser.close()
        return slots

    def test_naver_adapter_reads_visible_theme_cards(self) -> None:
        themes = (
            ThemeConfig("선택", "스릴러", 31_000, 65),
            ThemeConfig("쿠키", "미스터리", 25_000, 70),
        )
        html = """
        <button data-date="2026-06-15">15</button>
        <section class="product"><h2>선택</h2>
          <button>10:00 예약가능</button>
          <button disabled>12:00</button>
        </section>
        <section class="product"><h2>쿠키</h2>
          <button>14:30 예약 완료</button>
        </section>
        """
        slots = self.parse(
            NaverBookingAdapter(),
            store("naver_booking", themes),
            html,
        )
        self.assertEqual(
            [(slot.theme_name, slot.time, slot.status) for slot in slots],
            [
                ("선택", "10:00", "available"),
                ("선택", "12:00", "reserved"),
                ("쿠키", "14:30", "reserved"),
            ],
        )

    def test_naver_adapter_opens_schedule_and_reads_korean_ampm(self) -> None:
        theme = ThemeConfig("선택", "스릴러", 31_000, 65)
        html = """
        <article class="product">
          <h2>선택</h2>
          <button onclick="
            document.querySelector('#schedule').style.display='block'
          ">예약하기</button>
        </article>
        <section id="schedule" style="display:none">
          <button data-date="2026-06-15">6월 15일</button>
          <div class="time-list">
            <button>오전 10:00 예약가능</button>
            <button disabled>오후 2:30</button>
          </div>
        </section>
        """
        slots = self.parse(
            NaverBookingAdapter(),
            store("naver_booking", (theme,)),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("14:30", "reserved")],
        )

    def test_naver_adapter_reads_public_schedule_inside_frame(self) -> None:
        theme = ThemeConfig("선택", "스릴러", 31_000, 65)
        html = """
        <button>로그인</button>
        <iframe srcdoc="
          <section><h2>선택</h2>
            <button data-date='2026-06-15'>6월 15일</button>
            <button>오전 10:00 예약가능</button>
            <button disabled>오후 2:30</button>
          </section>
        "></iframe>
        """
        slots = self.parse(
            NaverBookingAdapter(),
            store("naver_booking", (theme,)),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("14:30", "reserved")],
        )

    def test_naver_adapter_ignores_footer_customer_center_hours(self) -> None:
        theme = ThemeConfig("인세인 / INSANE", "공포", 69_000, 75)
        html = """
        <main>
          <h2>인세인 / INSANE</h2>
          <p>예약 가능한 시간이 없습니다. 다른 날짜를 선택해 주세요.</p>
        </main>
        <footer>
          <ul>
            <li>네이버 예약 고객센터</li>
            <li>운영시간 09:00~18:00</li>
            <li>개인정보처리방침 이용약관 사업자정보 확인</li>
          </ul>
        </footer>
        """
        slots = self.parse(
            NaverBookingAdapter(),
            store("naver_booking", (theme,)),
            html,
        )
        self.assertEqual(slots, [])

    def test_keyescape_adapter_reads_selected_public_theme(self) -> None:
        theme = ThemeConfig(
            "그카지말라캤자나",
            "코믹",
            25_000,
            60,
            min_people=2,
        )
        html = """
        <select id="theme">
          <option value="7" data-themenum="7" selected>
            그카지말라캤자나
          </option>
        </select>
        <div class="calendar">
          <button data-date="2026-06-15">15</button>
        </div>
        <section class="reservation">
          <button class="open">10:00 예약가능</button>
          <button class="disabled" disabled>11:30</button>
        </section>
        """
        config = store("keyescape", (theme,))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            slots = KeyescapeAdapter().parse_slots(
                page,
                config,
                date(2026, 6, 15),
            )
            browser.close()
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("11:30", "reserved")],
        )

    def test_keyescape_fetch_requires_written_permission(self) -> None:
        config = store(
            "keyescape",
            (ThemeConfig("테마", "", 25_000, 60),),
        )
        with patch.dict(
            os.environ,
            {"KEYESCAPE_PUBLIC_MONITORING_PERMISSION": ""},
            clear=False,
        ):
            with self.assertRaises(PermissionError):
                KeyescapeAdapter().fetch_slots(
                    config,
                    date(2026, 6, 15),
                )

    def test_shortstories_reads_public_theme_schedule(self) -> None:
        theme = ThemeConfig(
            "그림자 없는 상자",
            "드라마",
            27_000,
            75,
        )
        html = """
        <article class="product">
          <h2>그림자 없는 상자</h2>
          <button onclick="
            document.querySelector('#schedule').style.display='block'
          ">그림자 없는 상자</button>
        </article>
        <section id="schedule" style="display:none">
          <button data-date="2026-06-15">15</button>
          <button>10:00 예약가능</button>
          <button class="soldout" disabled>11:30 예약완료</button>
        </section>
        """
        slots = self.parse(
            ShortstoriesAdapter(),
            store("shortstories", (theme,)),
            html,
        )
        self.assertEqual(
            [
                (slot.time, slot.status, slot.expected_revenue)
                for slot in slots
            ],
            [
                ("10:00", "available", 0.0),
                ("11:30", "reserved", 72_900),
            ],
        )

    def test_shortstories_treats_non_actionable_time_link_as_reserved(self) -> None:
        theme = ThemeConfig(
            "그림자 없는 상자",
            "드라마",
            27_000,
            75,
        )
        html = """
        <section>
          <h2>그림자 없는 상자</h2>
          <a href="/public-time/1000">10:00</a>
          <a>11:30</a>
        </section>
        """
        slots = self.parse(
            ShortstoriesAdapter(),
            store("shortstories", (theme,)),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("11:30", "reserved")],
        )

    def test_shortstories_reads_public_calendar_frame_without_login(self) -> None:
        theme = ThemeConfig(
            "그림자 없는 상자",
            "드라마",
            27_000,
            75,
        )
        html = """
        <a href="/login">로그인(강남)</a>
        <iframe srcdoc="
          <section><h2>그림자 없는 상자</h2>
            <button data-date='2026-06-15'>15</button>
            <a href='/reserve/public/1000'>10:00</a>
            <a>11:30</a>
          </section>
        "></iframe>
        """
        slots = self.parse(
            ShortstoriesAdapter(),
            store("shortstories", (theme,)),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:00", "available"), ("11:30", "reserved")],
        )

    def test_shortstories_reads_imweb_shadow_booking_widget(self) -> None:
        theme = ThemeConfig(
            "쓰여진 문장 속에 구원이 없다면",
            "드라마",
            32_000,
            80,
        )
        html = """
        <booking-widget></booking-widget>
        <script>
          const root = document.querySelector('booking-widget').attachShadow({
            mode: 'open'
          });
          root.innerHTML = `
            <button aria-label="2026년 6월 15일 월요일">15</button>
            <div class="reservationItem_divider">
              <img alt="문장 / 10:30">
              <button>예약</button>
            </div>
            <div class="reservationItem_divider">
              <img alt="문장 / 12:00">
              <button disabled>예약</button>
            </div>
          `;
        </script>
        """
        slots = self.parse(
            ShortstoriesAdapter(),
            store("shortstories", (theme,)),
            html,
        )
        self.assertEqual(
            [(slot.time, slot.status) for slot in slots],
            [("10:30", "available"), ("12:00", "reserved")],
        )


if __name__ == "__main__":
    unittest.main()
