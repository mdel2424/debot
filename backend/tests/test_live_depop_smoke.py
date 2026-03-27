import os
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEPENDENCY_IMPORT_ERROR = None

try:
    from playwright.sync_api import sync_playwright
    from scraper import (  # noqa: E402
        BROWSE_URL,
        accept_cookies,
        build_seller_url,
        collect_listing_links,
        create_browser_context,
        dismiss_login_modal,
        extract_seller_sold_count,
        parse_listing,
    )
except Exception as exc:  # pragma: no cover - protects VS Code discovery on wrong interpreter
    DEPENDENCY_IMPORT_ERROR = exc


@unittest.skipIf(
    DEPENDENCY_IMPORT_ERROR is not None,
    f"Live smoke tests require backend dependencies: {DEPENDENCY_IMPORT_ERROR}",
)
@unittest.skipUnless(
    os.getenv("DEPOP_LIVE_SMOKE") == "1",
    "Set DEPOP_LIVE_SMOKE=1 to run live Depop smoke tests.",
)
class LiveDepopSmokeTest(unittest.TestCase):
    def setUp(self):
        self.playwright = sync_playwright().start()
        self.browser, self.ctx = create_browser_context(self.playwright, headless=True, slowmo=0)
        self.page = self.ctx.new_page()

    def tearDown(self):
        try:
            self.ctx.close()
        finally:
            try:
                self.browser.close()
            finally:
                self.playwright.stop()

    def _prepare_page(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        accept_cookies(self.page)
        try:
            self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        dismiss_login_modal(self.page)

    def test_live_browse_page_collects_beyond_initial_48_and_parses_listing(self):
        self._prepare_page(BROWSE_URL)

        links = collect_listing_links(
            self.page,
            max_scrolls=8,
            per_scroll_wait_ms=1200,
            max_links=120,
            aggressive_end_scroll=True,
        )
        if len(links) <= 48:
            self.page.wait_for_timeout(2_000)
            links = collect_listing_links(
                self.page,
                max_scrolls=8,
                per_scroll_wait_ms=1200,
                max_links=120,
                aggressive_end_scroll=True,
            )

        self.assertGreater(len(links), 48)

        item_page = self.ctx.new_page()
        try:
            item = parse_listing(item_page, links[0])
        finally:
            item_page.close()

        self.assertIsNotNone(item)
        self.assertTrue(item.get("description"))
        self.assertTrue(item.get("price"))
        self.assertTrue(item.get("seller"))

    def test_live_seller_page_exposes_sold_count(self):
        self._prepare_page(build_seller_url("heavyvintage"))

        sold_count = extract_seller_sold_count(self.page)

        self.assertIsNotNone(sold_count)
        self.assertGreater(sold_count, 50)


if __name__ == "__main__":
    unittest.main()
