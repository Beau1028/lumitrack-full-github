from __future__ import annotations

from datetime import date

from playwright.sync_api import Page

from scraper.adapters.base_adapter import BaseAdapter
from scraper.models import ReservationSlot, StoreConfig


class CatalogAdapter(BaseAdapter):
    """A no-request adapter for stores that are registered as catalog data only."""

    def fetch_slots(
        self, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        del store_config, target_date
        return []

    def parse_slots(
        self, page: Page, store_config: StoreConfig, target_date: date
    ) -> list[ReservationSlot]:
        del page, store_config, target_date
        return []
