from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
import os
import random
import shutil
import time
from urllib.parse import urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    sync_playwright,
)

from scraper.models import ReservationSlot, StoreConfig

USER_AGENT = (
    "EscapeRoomMonitor/0.1 (+personal-public-page-monitor; "
    "contact: configure-your-email@example.com)"
)


class BaseAdapter(ABC):
    """Common interface for store-specific public booking page adapters."""

    navigation_timeout_ms = 30_000
    ignore_https_errors = False
    preserve_existing_on_empty = False
    inter_date_delay_min_seconds = 0.4
    inter_date_delay_max_seconds = 0.8

    def fetch_slots(
        self, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        outcome = self.fetch_slots_for_dates(store_config, [target_date])[
            target_date
        ]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def fetch_slots_for_dates(
        self,
        store_config: StoreConfig,
        target_dates: list[date],
    ) -> dict[date, list[ReservationSlot] | Exception]:
        """Fetch several dates while reusing one browser session per store."""
        with sync_playwright() as playwright:
            browser = self.launch_browser(playwright)
            try:
                return self.fetch_slots_for_dates_in_browser(
                    store_config,
                    target_dates,
                    browser,
                )
            finally:
                browser.close()

    def launch_browser(self, playwright: Playwright) -> Browser:
        """Launch the browser used by this adapter."""
        launch_args = []
        if os.name != "nt":
            launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]

        chromium_executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if not chromium_executable and os.name != "nt":
            for executable_name in (
                "chromium",
                "chromium-browser",
                "google-chrome",
                "google-chrome-stable",
            ):
                found = shutil.which(executable_name)
                if found:
                    chromium_executable = found
                    break

        try:
            if os.getenv("ESCAPE_ROOM_MONITOR_USE_EDGE") == "1":
                return playwright.chromium.launch(
                    channel="msedge",
                    headless=True,
                    args=launch_args,
                )
            if chromium_executable:
                return playwright.chromium.launch(
                    executable_path=chromium_executable,
                    headless=True,
                    args=launch_args,
                )
            return playwright.chromium.launch(headless=True, args=launch_args)
        except PlaywrightError:
            # The packaged Windows app uses the installed Microsoft Edge
            # when a Playwright-managed Chromium is not available.
            if os.name == "nt":
                return playwright.chromium.launch(channel="msedge", headless=True)
            raise

    def fetch_slots_for_dates_in_browser(
        self,
        store_config: StoreConfig,
        target_dates: list[date],
        browser: Browser,
    ) -> dict[date, list[ReservationSlot] | Exception]:
        """Fetch dates while reusing a browser owned by the origin worker."""
        booking_urls = {
            target_date: self.build_booking_url(store_config, target_date)
            for target_date in target_dates
        }
        for booking_url in booking_urls.values():
            self._validate_public_url(booking_url)

        # Before enabling a real store, the user must manually check robots.txt
        # and the site's terms. This tool never logs in, bypasses CAPTCHA, pays,
        # books, or calls private APIs.
        context: BrowserContext = browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
            ignore_https_errors=self.ignore_https_errors,
        )
        page = context.new_page()
        page.set_default_timeout(self.navigation_timeout_ms)
        self._prepare_page(page)
        outcomes: dict[date, list[ReservationSlot] | Exception] = {}
        try:
            dated_urls = list(booking_urls.items())
            for date_index, (target_date, booking_url) in enumerate(
                dated_urls
            ):
                try:
                    page.goto(
                        booking_url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )
                    outcomes[target_date] = self.parse_slots(
                        page,
                        store_config,
                        target_date,
                    )
                except Exception as exc:
                    outcomes[target_date] = exc
                if (
                    date_index < len(dated_urls) - 1
                    and booking_url.startswith(("http://", "https://"))
                ):
                    time.sleep(
                        random.uniform(
                            self.inter_date_delay_min_seconds,
                            self.inter_date_delay_max_seconds,
                        )
                    )
        finally:
            context.close()
        return outcomes

    def build_booking_url(
        self, store_config: StoreConfig, target_date: date
    ) -> str:
        """Return the public page URL to open for a target date."""
        del target_date
        return store_config.booking_url

    def _prepare_page(self, page: Page) -> None:
        """Keep public-page crawls light without changing page behavior."""
        try:
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_()
                ),
            )
        except PlaywrightError:
            pass

    @abstractmethod
    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        """Convert the current public page into normalized reservation slots."""

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https", "file"}:
            raise ValueError(f"Unsupported booking URL scheme: {parsed.scheme}")
        if parsed.username or parsed.password:
            raise ValueError("Credentials must not be embedded in booking_url.")
