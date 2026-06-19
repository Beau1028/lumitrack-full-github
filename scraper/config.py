from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import yaml

from scraper.models import StoreConfig


class ConfigError(ValueError):
    """Raised when stores.yaml is missing required or valid data."""


def _resolve_booking_url(raw_url: str, config_dir: Path) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme in {"http", "https", "file"}:
        return raw_url
    if parsed.scheme:
        raise ConfigError(
            f"booking_url only supports public http(s) URLs or local sample files: {raw_url}"
        )
    return (config_dir / raw_url).resolve().as_uri()


def _load_location_overrides(config_dir: Path) -> dict[str, dict[str, object]]:
    path = config_dir / "store_locations.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    raw_locations = raw.get("locations", [])
    if not isinstance(raw_locations, list):
        raise ConfigError("store_locations.yaml must contain a 'locations' list.")

    allowed_fields = {
        "address",
        "latitude",
        "longitude",
        "brand_name",
        "brand_logo_url",
        "map_note",
    }
    overrides: dict[str, dict[str, object]] = {}
    for index, item in enumerate(raw_locations, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"Location #{index} must be a YAML mapping.")
        store_id = str(item.get("store_id", "")).strip()
        if not store_id:
            raise ConfigError(f"Location #{index} has an empty store_id.")
        overrides[store_id] = {
            field: item[field]
            for field in allowed_fields
            if field in item
        }
    return overrides


def load_stores(config_path: str | Path = "stores.yaml") -> list[StoreConfig]:
    path = Path(config_path).resolve()
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    raw_stores = raw.get("stores")
    if not isinstance(raw_stores, list):
        raise ConfigError("stores.yaml must contain a top-level 'stores' list.")

    stores: list[StoreConfig] = []
    seen_ids: set[str] = set()
    required = {"store_id", "store_name", "region", "booking_url"}
    location_overrides = _load_location_overrides(path.parent)

    for index, item in enumerate(raw_stores, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"Store #{index} must be a YAML mapping.")
        missing = required - item.keys()
        if missing:
            raise ConfigError(f"Store #{index} is missing fields: {sorted(missing)}")

        store_data = dict(item)
        store_data["booking_url"] = _resolve_booking_url(
            str(item["booking_url"]), path.parent
        )
        store = StoreConfig.from_dict(store_data)
        if store.store_id in location_overrides:
            store = replace(store, **location_overrides[store.store_id])

        if not store.store_id:
            raise ConfigError(f"Store #{index} has an empty store_id.")
        if store.store_id in seen_ids:
            raise ConfigError(f"Duplicate store_id: {store.store_id}")
        if store.avg_people <= 0:
            raise ConfigError(f"avg_people must be positive: {store.store_id}")
        if not store.themes and store.adapter_type not in {
            "xdungeon",
            "catalog",
            "blocked",
            "limited",
            "permission_required",
            "cubeescape",
            "earthstar",
            "frank",
            "horror_switch",
            "sinbi",
        }:
            raise ConfigError(f"At least one theme is required: {store.store_id}")

        theme_names: set[str] = set()
        for theme in store.themes:
            if theme.theme_name in theme_names:
                raise ConfigError(
                    f"Duplicate theme '{theme.theme_name}' in {store.store_id}"
                )
            if theme.price < 0 or theme.duration_minutes < 0:
                raise ConfigError(
                    f"Theme price/duration is invalid: {store.store_id}/{theme.theme_name}"
                )
            if theme.min_people <= 0 or theme.max_people < 0:
                raise ConfigError(
                    f"Theme party size is invalid: {store.store_id}/{theme.theme_name}"
                )
            if theme.max_people and theme.max_people < theme.min_people:
                raise ConfigError(
                    f"Theme maximum party size is invalid: "
                    f"{store.store_id}/{theme.theme_name}"
                )
            theme_names.add(theme.theme_name)

        seen_ids.add(store.store_id)
        stores.append(store)

    return stores


def find_store(stores: list[StoreConfig], store_id: str) -> StoreConfig:
    for store in stores:
        if store.store_id == store_id:
            return store
    raise ConfigError(f"Unknown store_id: {store_id}")
