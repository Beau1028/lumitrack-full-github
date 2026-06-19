from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.adapters.base_adapter import USER_AGENT
from scraper.config import find_store, load_stores
from scraper.logging_utils import configure_logging

KEYWORDS = (
    "예약가능",
    "예약 완료",
    "예약완료",
    "마감",
    "sold out",
    "available",
    "closed",
    "예약하기",
    "신청",
    "전화문의",
)
PROJECT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect a public booking page without writing to the database."
    )
    parser.add_argument("--store_id", required=True)
    parser.add_argument("--config", default=str(PROJECT_DIR / "stores.yaml"))
    parser.add_argument(
        "--output-dir", default=str(PROJECT_DIR / "artifacts" / "dry_run")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(PROJECT_DIR / "logs")
    store = find_store(load_stores(args.config), args.store_id)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = output_dir / f"{store.store_id}_{stamp}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
        page = context.new_page()
        page.goto(store.booking_url, wait_until="domcontentloaded", timeout=30_000)

        html = page.content()
        body_text = page.locator("body").inner_text()
        html_path = prefix.with_suffix(".html")
        screenshot_path = prefix.with_suffix(".png")
        html_path.write_text(html, encoding="utf-8")
        page.screenshot(path=str(screenshot_path), full_page=True)

        lowered = body_text.casefold()
        print(f"URL: {store.booking_url}")
        print(f"HTML: {html_path}")
        print(f"Screenshot: {screenshot_path}")
        print("\n[예약 관련 키워드]")
        for keyword in KEYWORDS:
            count = lowered.count(keyword.casefold())
            if count:
                print(f"- {keyword}: {count}")

        print("\n[예약 관련 텍스트 후보]")
        text_candidates: list[str] = []
        for line in body_text.splitlines():
            normalized = re.sub(r"\s+", " ", line).strip()
            if normalized and (
                any(keyword.casefold() in normalized.casefold() for keyword in KEYWORDS)
                or re.search(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", normalized)
            ):
                text_candidates.append(normalized[:200])
        for text in dict.fromkeys(text_candidates[:200]):
            print(f"- {text}")

        print("\n[가능한 버튼/링크/컨트롤 텍스트]")
        candidates = page.locator(
            "button, a, input[type=button], input[type=submit], [role=button]"
        )
        seen: set[str] = set()
        for index in range(min(candidates.count(), 200)):
            candidate = candidates.nth(index)
            text = (
                candidate.inner_text()
                or candidate.get_attribute("value")
                or candidate.get_attribute("aria-label")
                or ""
            )
            text = re.sub(r"\s+", " ", text).strip()
            if text and text not in seen:
                print(f"- {text[:160]}")
                seen.add(text)

        context.close()
        browser.close()

    print("\nDry-run only: no database writes, clicks, login, payment, or booking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
