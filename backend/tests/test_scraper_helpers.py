import sys
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEPENDENCY_IMPORT_ERROR = None

try:
    from scraper import (  # noqa: E402
        LOGIN_MODAL_MAX_ATTEMPTS,
        LOGIN_MODAL_WAIT_MS,
        RateLimitError,
        SearchCancelled,
        collect_listing_links,
        dismiss_login_modal,
        extract_created_at_from_html,
        extract_rate_limit_message,
        extract_seller_sold_count_from_text,
        extract_seller_username_from_href,
        extract_size_label_from_text,
        flush_debug_logs,
        log_debug,
        parse_listing,
    )
except Exception as exc:  # pragma: no cover - protects VS Code discovery on wrong interpreter
    DEPENDENCY_IMPORT_ERROR = exc


class FakeLocator:
    def __init__(self, count=0, text="", texts=None, attrs=None):
        self._count = count
        self._text = text
        self._texts = texts or []
        self._attrs = attrs or {}

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def all_inner_texts(self):
        return list(self._texts)

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def is_visible(self, timeout=None):
        return self._count > 0

    def click(self, timeout=None):
        if self._count <= 0:
            raise RuntimeError("locator not available")


class FakeKeyboard:
    def __init__(self):
        self.presses = []

    def press(self, key):
        self.presses.append(key)
        return key


class FakeCollectPage:
    def __init__(self, href_sequences):
        self.url = "https://www.depop.com/ca/category/mens/tops/"
        self._href_sequences = href_sequences
        self._eval_calls = 0
        self.scroll_amounts = []
        self.scroll_to_bottom_calls = 0
        self.waits = []
        self.keyboard = FakeKeyboard()

    def eval_on_selector_all(self, selector, script):
        idx = min(self._eval_calls, len(self._href_sequences) - 1)
        self._eval_calls += 1
        return list(self._href_sequences[idx])

    def evaluate(self, script, arg=None):
        if "window.innerHeight" in script:
            return 1000
        if "window.scrollBy" in script:
            self.scroll_amounts.append(arg)
        if "window.scrollTo" in script:
            self.scroll_to_bottom_calls += 1
        return None

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def title(self):
        return "Depop"

    def inner_text(self, selector, timeout=None):
        return "Active listings"

    def locator(self, selector):
        count = 1 if selector == 'a[href^="/products/"]' else 0
        return FakeLocator(count=count)


class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeRateLimitedListingPage:
    url = "https://www.depop.com/products/example/"

    def goto(self, url, wait_until=None, timeout=None):
        return FakeResponse(429)

    def wait_for_load_state(self, state, timeout=None):
        return None

    def content(self):
        return "<html><body>Too many requests</body></html>"

    def title(self):
        return "Too Many Requests"

    def inner_text(self, selector, timeout=None):
        return "Too many requests. Try again later."

    def locator(self, selector):
        return FakeLocator()

    def wait_for_timeout(self, ms):
        return ms


class FakeNoModalPage:
    def __init__(self):
        self.keyboard = FakeKeyboard()
        self.waits = []

    def locator(self, selector):
        return FakeLocator()

    def evaluate(self, script):
        return False

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


@unittest.skipIf(
    DEPENDENCY_IMPORT_ERROR is not None,
    f"Scraper helper tests require backend dependencies: {DEPENDENCY_IMPORT_ERROR}",
)
class ScraperHelpersTest(unittest.TestCase):
    def test_extract_created_at_from_hydration_html(self):
        html = (
            '<script>self.__next_f.push([1,"...'
            '\\\"created_at\\\":\\\"2026-03-20T21:16:13.033766Z\\\"'
            '..."])</script>'
        )
        self.assertEqual(
            extract_created_at_from_html(html),
            "2026-03-20T21:16:13.033766Z",
        )

    def test_extract_seller_username_from_shop_href(self):
        self.assertEqual(
            extract_seller_username_from_href("/hycen88/?brandIds=697&productId=713321702"),
            "hycen88",
        )
        self.assertIsNone(extract_seller_username_from_href("/products/h1cen88-dime-mtl-sun-faded-teal-crewneck-f51d/"))

    def test_extract_seller_sold_count_from_text(self):
        self.assertEqual(extract_seller_sold_count_from_text("249 sold · Active today"), 249)
        self.assertEqual(extract_seller_sold_count_from_text("1,249 sold"), 1249)
        self.assertIsNone(extract_seller_sold_count_from_text("Sold items"))

    def test_extract_size_label_from_text(self):
        self.assertEqual(extract_size_label_from_text("Condition\nGood\nSize 34\""), '34"')
        self.assertEqual(extract_size_label_from_text("Size\nUS 10.5\nColor\nBlack"), 'US 10.5')
        self.assertIsNone(extract_size_label_from_text("No size line here"))

    def test_extract_rate_limit_message(self):
        self.assertEqual(
            extract_rate_limit_message("anything", status=429),
            "Depop returned HTTP 429 Too Many Requests.",
        )
        self.assertEqual(
            extract_rate_limit_message("Too many requests. Please try again later."),
            "Depop appears to be rate limiting requests right now.",
        )
        self.assertEqual(
            extract_rate_limit_message(
                "Checking your browser before accessing Depop",
                status=403,
                expected_content_missing=True,
            ),
            "Depop appears to be temporarily blocking requests right now.",
        )
        self.assertIsNone(extract_rate_limit_message("Vintage tee listed 2 hours ago"))

    def test_collect_listing_links_scrolls_slowly_until_plateau(self):
        page = FakeCollectPage([
            ["/products/a/"],
            ["/products/a/", "/products/b/"],
            ["/products/a/", "/products/b/"],
            ["/products/a/", "/products/b/"],
        ])

        links = collect_listing_links(page, max_scrolls=2, per_scroll_wait_ms=25)

        self.assertEqual(
            links,
            [
                "https://www.depop.com/products/a/",
                "https://www.depop.com/products/b/",
            ],
        )
        self.assertTrue(page.scroll_amounts)
        self.assertTrue(all(amount == 700 for amount in page.scroll_amounts))

    def test_collect_listing_links_waits_through_initial_plateau_before_stopping(self):
        page = FakeCollectPage([
            ["/products/a/"],
            ["/products/a/"],
            ["/products/a/"],
            ["/products/a/", "/products/b/", "/products/c/"],
        ])

        links = collect_listing_links(page, max_scrolls=1, per_scroll_wait_ms=25)

        self.assertEqual(
            links,
            [
                "https://www.depop.com/products/a/",
                "https://www.depop.com/products/b/",
                "https://www.depop.com/products/c/",
            ],
        )

    def test_collect_listing_links_uses_aggressive_end_scroll_for_browse_pages(self):
        page = FakeCollectPage([
            [f"/products/item-{i}/" for i in range(1, 49)],
            [f"/products/item-{i}/" for i in range(1, 73)],
            [f"/products/item-{i}/" for i in range(1, 97)],
            [f"/products/item-{i}/" for i in range(1, 97)],
        ])

        links = collect_listing_links(
            page,
            max_scrolls=4,
            per_scroll_wait_ms=25,
            aggressive_end_scroll=True,
        )

        self.assertEqual(len(links), 96)
        self.assertEqual(links[0], "https://www.depop.com/products/item-1/")
        self.assertEqual(links[-1], "https://www.depop.com/products/item-96/")
        self.assertEqual(page.keyboard.presses, ["End", "End", "End"])
        self.assertEqual(page.scroll_to_bottom_calls, 3)
        self.assertEqual(page.waits, [2500, 2500, 2500])

    def test_collect_listing_links_raises_when_cancelled(self):
        page = FakeCollectPage([["/products/a/"]] * 6)
        checks = {"count": 0}

        def should_cancel():
            checks["count"] += 1
            return checks["count"] >= 2

        with self.assertRaises(SearchCancelled):
            collect_listing_links(page, max_scrolls=2, per_scroll_wait_ms=1, should_cancel=should_cancel)

    def test_parse_listing_raises_rate_limit_error(self):
        with self.assertRaises(RateLimitError):
            parse_listing(FakeRateLimitedListingPage(), "https://www.depop.com/products/example/")

    def test_dismiss_login_modal_exits_quickly_when_absent(self):
        page = FakeNoModalPage()

        with mock.patch("builtins.print"):
            dismiss_login_modal(page)
            flush_debug_logs()

        self.assertEqual(page.keyboard.presses, ["Escape"])
        self.assertEqual(page.waits, [LOGIN_MODAL_WAIT_MS] * (LOGIN_MODAL_MAX_ATTEMPTS + 1))

    def test_log_debug_collapses_repeated_escape_messages(self):
        with mock.patch("builtins.print") as print_mock:
            flush_debug_logs()
            log_debug("[login-modal] Pressed Escape to dismiss modal", aggregate_key="login_modal_escape")
            log_debug("[login-modal] Pressed Escape to dismiss modal", aggregate_key="login_modal_escape")
            flush_debug_logs()

        print_mock.assert_called_once_with("[login-modal] Pressed Escape to dismiss modal x2")


if __name__ == "__main__":
    unittest.main()
