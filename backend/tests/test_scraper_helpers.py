import json
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import scraper  # noqa: E402
from scraper import (  # noqa: E402
    LOGIN_MODAL_MAX_ATTEMPTS,
    LOGIN_MODAL_WAIT_MS,
    RateLimitError,
    SearchCancelled,
    collect_listing_links,
    dismiss_login_modal,
    extract_captured_shop_product_hrefs,
    extract_created_at_from_html,
    extract_rate_limit_message,
    extract_seller_sold_count_from_text,
    extract_seller_username_from_href,
    extract_size_label_from_text,
    flush_debug_logs,
    install_shop_products_capture,
    log_debug,
    normalize_product_listing_href,
    parse_listing,
)


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
        self.captured_shop_pages = []
        self.scroll_amounts = []
        self.scroll_to_bottom_calls = 0
        self.waits = []
        self.keyboard = FakeKeyboard()
        self.api_responses = []
        self.api_requests = []

    def eval_on_selector_all(self, selector, script):
        idx = min(self._eval_calls, len(self._href_sequences) - 1)
        self._eval_calls += 1
        return list(self._href_sequences[idx])

    def evaluate(self, script, arg=None):
        if 'async (url)' in script:
            self.api_requests.append(arg)
            return self.api_responses.pop(0)
        if 'window.__debotShopProductPages = []' in script:
            self.captured_shop_pages.clear()
            return None
        if "__debotShopProductPages" in script:
            return list(self.captured_shop_pages)
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
        count = 1 if selector == 'a[href*="/products/"]' else 0
        return FakeLocator(count=count)


class FakeCapturePage:
    def __init__(self):
        self.url = "https://www.depop.com/heavyvintage/"
        self.handlers = {}
        self.init_scripts = []

    def on(self, event, handler):
        self.handlers[event] = handler

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def evaluate(self, script, arg=None):
        return []


class FakeProductApiResponse:
    def __init__(self, url, status, text):
        self.url = url
        self.status = status
        self._text = text

    def text(self):
        return self._text


class FakeResponse:
    def __init__(self, status, retry_after=None):
        self.status = status
        self.retry_after = retry_after

    def header_value(self, name):
        return self.retry_after if name == 'retry-after' else None


class FakeGotoPage:
    def __init__(self):
        self.gotos = []

    def goto(self, url, wait_until=None, timeout=None):
        self.gotos.append((url, wait_until, timeout))
        return FakeResponse(200)


class FakeFastListingPage:
    def __init__(self):
        self.wait_functions = []
        self.gotos = []

    def goto(self, url, wait_until=None, timeout=None):
        self.gotos.append((url, wait_until, timeout))
        return FakeResponse(200)

    def wait_for_function(self, expression, timeout=None):
        self.wait_functions.append((expression, timeout))

    def evaluate(self, expression):
        return {
            "description": "Vintage tee\nPit to pit 21\nLength 28",
            "price": "$22.00",
            "image": "https://media-photos.depop.com/example/P0.jpg",
            "seller": "seller_one",
            "bodyText": "Condition\nGood\nSize M",
            "datetime": "2026-03-20T21:16:13.033766Z",
            "timeText": "",
        }


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


class ScraperHelpersTest(unittest.TestCase):
    def setUp(self):
        scraper._clear_listing_cache()
        self.addCleanup(scraper._clear_listing_cache)

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

    def test_normalize_product_listing_href_accepts_current_product_variants(self):
        origin = "https://www.depop.com"

        self.assertEqual(
            normalize_product_listing_href("/products/item-one/", origin),
            "https://www.depop.com/products/item-one/",
        )
        self.assertEqual(
            normalize_product_listing_href("/ca/products/item-two/", origin),
            "https://www.depop.com/products/item-two/",
        )
        self.assertEqual(
            normalize_product_listing_href("https://www.depop.com/products/item-three/", origin),
            "https://www.depop.com/products/item-three/",
        )
        self.assertIsNone(normalize_product_listing_href("/seller/?productId=123", origin))
        self.assertIsNone(normalize_product_listing_href("/products/create/", origin))
        self.assertIsNone(normalize_product_listing_href("https://example.com/products/item/", origin))

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
            "Depop returned HTTP 403 Forbidden.",
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
        self.assertGreaterEqual(page.scroll_to_bottom_calls, 3)
        self.assertTrue(all(wait == 2500 for wait in page.waits))

    def test_aggressive_collection_does_not_treat_one_scroll_as_a_24_link_cap(self):
        page = FakeCollectPage([
            [f"/products/item-{i}/" for i in range(1, 25)],
            [f"/products/item-{i}/" for i in range(1, 49)],
            [f"/products/item-{i}/" for i in range(1, 73)],
        ])

        links = collect_listing_links(
            page,
            max_scrolls=1,
            per_scroll_wait_ms=25,
            max_links=72,
            aggressive_end_scroll=True,
        )

        self.assertEqual(len(links), 72)
        self.assertEqual(page.keyboard.presses, ["End", "End"])

    def test_browser_context_hides_webdriver_before_depop_scripts_run(self):
        pw = mock.Mock()
        browser = pw.firefox.launch.return_value
        ctx = browser.new_context.return_value

        with mock.patch.object(scraper, "install_resource_blocking"):
            result = scraper.create_browser_context(pw, headless=True, slowmo=0)

        self.assertEqual(result, (browser, ctx))
        kwargs = browser.new_context.call_args.kwargs
        self.assertNotIn("user_agent", kwargs)
        ctx.add_init_script.assert_called_once_with(scraper.BROWSER_INIT_SCRIPT)
        self.assertIn("webdriver", ctx.add_init_script.call_args.args[0])

    def test_extract_captured_shop_product_hrefs_uses_seller_api_products(self):
        page = FakeCollectPage([[]])
        page.captured_shop_pages = [
            {
                "status": 200,
                "text": json.dumps({
                    "products": [
                        {"slug": "api-active-one", "sold": False, "status": "ONSALE"},
                        {"slug": "api-sold", "sold": True, "status": "SOLD"},
                        {"slug": "api-removed", "status": "REMOVED"},
                        {"slug": "api-active-two"},
                    ]
                }),
            },
        ]

        self.assertEqual(
            extract_captured_shop_product_hrefs(page),
            ["/products/api-active-one/", "/products/api-active-two/"],
        )

    def test_collect_listing_links_merges_captured_api_products_with_dom_links(self):
        page = FakeCollectPage([["/products/dom-one/"]])
        page.captured_shop_pages = [
            {
                "status": 200,
                "text": json.dumps({
                    "products": [
                        {"slug": "api-one", "sold": False},
                        {"slug": "api-two", "sold": False},
                    ]
                }),
            }
        ]

        links = collect_listing_links(page, max_scrolls=0, per_scroll_wait_ms=25)

        self.assertEqual(
            links,
            [
                "https://www.depop.com/products/api-one/",
                "https://www.depop.com/products/api-two/",
                "https://www.depop.com/products/dom-one/",
            ],
        )

    def test_install_shop_products_capture_records_playwright_response_bodies(self):
        page = FakeCapturePage()
        api_response = FakeProductApiResponse(
            "https://webapi.depop.com/presentation/api/v1/shops/123/products/?limit=24",
            200,
            json.dumps({"products": [{"slug": "response-captured", "sold": False}]}),
        )

        try:
            install_shop_products_capture(page)
            page.handlers["response"](api_response)

            self.assertEqual(
                extract_captured_shop_product_hrefs(page),
                ["/products/response-captured/"],
            )
            self.assertEqual(len(page.init_scripts), 1)
        finally:
            scraper._CAPTURED_SHOP_PRODUCT_PAGES.pop(page, None)
            scraper._SHOP_PRODUCTS_CAPTURE_INSTALLED_PAGES.discard(page)

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

    def test_parse_listing_uses_fast_dom_path_without_cookie_or_modal_waits(self):
        page = FakeFastListingPage()

        with (
            mock.patch("scraper.accept_cookies") as accept_mock,
            mock.patch("scraper.dismiss_login_modal") as dismiss_mock,
        ):
            item = parse_listing(page, "https://www.depop.com/products/example-fast/")

        accept_mock.assert_not_called()
        dismiss_mock.assert_not_called()
        self.assertEqual(page.wait_functions[0][1], scraper.FAST_LISTING_READY_TIMEOUT_MS)
        self.assertEqual(item["description"], "Vintage tee\nPit to pit 21\nLength 28")
        self.assertEqual(item["price"], "$22.00")
        self.assertEqual(item["seller"], "seller_one")
        self.assertEqual(item["sizeLabel"], "M")
        self.assertIsNotNone(item["ageDays"])

    def test_parse_listing_reuses_recent_cached_result_without_navigation(self):
        url = "https://www.depop.com/products/example-cached/"

        with (
            mock.patch.object(scraper, "LISTING_CACHE_TTL_SECONDS", 300.0),
            mock.patch.object(scraper, "LISTING_CACHE_MAX_ITEMS", 10),
        ):
            item = parse_listing(FakeFastListingPage(), url)
            item["seller"] = "mutated-after-parse"

            with mock.patch("scraper.guarded_goto") as goto_mock:
                cached_item = parse_listing(mock.Mock(), url)

        goto_mock.assert_not_called()
        self.assertEqual(cached_item["url"], url)
        self.assertEqual(cached_item["seller"], "seller_one")
        self.assertIsNot(cached_item, item)

    def test_guarded_goto_has_no_default_navigation_delay(self):
        page = FakeGotoPage()

        with (
            mock.patch.object(scraper, "MIN_NAV_INTERVAL_SECONDS", 0.0),
            mock.patch.object(scraper, "RATE_LIMIT_NAV_INTERVAL_SECONDS", 1.0),
            mock.patch.object(scraper, "_LAST_NAVIGATION_STARTED_AT", 0.0),
            mock.patch.object(scraper, "_NAVIGATION_PACING_UNTIL_TS", 0.0),
            mock.patch("scraper.sleep_with_cancel") as sleep_mock,
        ):
            scraper.guarded_goto(page, "https://www.depop.com/products/one/")
            scraper.guarded_goto(page, "https://www.depop.com/products/two/")

        sleep_mock.assert_not_called()
        self.assertEqual(
            page.gotos,
            [
                ("https://www.depop.com/products/one/", "domcontentloaded", 60_000),
                ("https://www.depop.com/products/two/", "domcontentloaded", 60_000),
            ],
        )

    def test_guarded_goto_uses_temporary_rate_limit_navigation_pacing(self):
        page = FakeGotoPage()
        cancel_check = lambda: False

        with (
            mock.patch.object(scraper, "MIN_NAV_INTERVAL_SECONDS", 0.0),
            mock.patch.object(scraper, "RATE_LIMIT_NAV_INTERVAL_SECONDS", 1.0),
            mock.patch.object(scraper, "_LAST_NAVIGATION_STARTED_AT", 100.0),
            mock.patch.object(scraper, "_NAVIGATION_PACING_UNTIL_TS", 200.0),
            mock.patch("scraper.time.monotonic", return_value=100.25),
            mock.patch("scraper.sleep_with_cancel") as sleep_mock,
        ):
            scraper.guarded_goto(
                page,
                "https://www.depop.com/products/paced/",
                should_cancel=cancel_check,
            )

        sleep_mock.assert_called_once()
        delay, should_cancel = sleep_mock.call_args.args
        self.assertAlmostEqual(delay, 0.75)
        self.assertIs(should_cancel, cancel_check)

    def test_dismiss_login_modal_exits_quickly_when_absent(self):
        page = FakeNoModalPage()

        with mock.patch("builtins.print"):
            dismiss_login_modal(page)
            flush_debug_logs()

        self.assertEqual(page.keyboard.presses, [])
        self.assertEqual(page.waits, [])

    @staticmethod
    def shop_response(start, count=24, has_more=True):
        return {
            'status': 200,
            'text': json.dumps({
                'objects': [
                    {'slug': f'item-{index}', 'status': 'STATUS_ONSALE'}
                    for index in range(start, start + count)
                ],
                'page_info': {'has_more': has_more, 'last': f'cursor-{start + count}'},
            }),
        }

    def test_current_shop_cursor_collects_360_items_with_a_static_24_item_dom(self):
        page = FakeCollectPage([[f'/products/item-{i}/' for i in range(24)]])
        first = self.shop_response(0)
        first['url'] = 'https://webapi.depop.com/presentation/api/v1/shops/123/products/?limit=24&groups=tops'
        page.captured_shop_pages = [first]
        page.api_responses = [self.shop_response(i, has_more=i < 336) for i in range(24, 360, 24)]

        links = collect_listing_links(page, max_scrolls=1, max_links=1000, aggressive_end_scroll=True)

        self.assertEqual(len(links), 360)
        self.assertEqual(len(set(links)), 360)
        self.assertEqual(len(page.api_requests), 14)
        self.assertTrue(all('groups=tops' in url for url in page.api_requests))
        self.assertEqual(page.waits, [])

    def test_partial_shop_rate_limit_is_not_reported_as_a_complete_24_item_scan(self):
        page = FakeCollectPage([['/products/item-0/']])
        first = self.shop_response(0)
        first['url'] = 'https://webapi.depop.com/presentation/api/v1/shops/123/products/?limit=24'
        page.captured_shop_pages = [first]
        page.api_responses = [{'status': 429, 'text': '', 'retryAfter': '240'}]

        with self.assertRaises(RateLimitError) as caught:
            collect_listing_links(page, max_links=100, aggressive_end_scroll=True)

        self.assertEqual(caught.exception.retry_after_seconds, 240)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(page.api_requests), 1)

    def test_dom_collection_continues_past_eight_batches_while_growing(self):
        page = FakeCollectPage([
            [f'/products/item-{i}/' for i in range(end)]
            for end in range(24, 361, 24)
        ])
        links = collect_listing_links(page, max_scrolls=1, max_links=360, aggressive_end_scroll=True)
        self.assertEqual(len(links), 360)

    def test_old_urls_do_not_use_up_new_link_capacity(self):
        page = FakeCollectPage([
            ['/products/a/', '/products/b/'],
            ['/products/a/', '/products/b/', '/products/c/', '/products/d/'],
        ])
        links = collect_listing_links(
            page, max_links=2, aggressive_end_scroll=True,
            excluded_urls={'https://www.depop.com/products/a/', 'https://www.depop.com/products/b/'},
        )
        self.assertEqual(links, ['https://www.depop.com/products/c/', 'https://www.depop.com/products/d/'])

    def test_canonical_variants_do_not_duplicate_listings(self):
        page = FakeCollectPage([['/products/a/?ref=shop', '/ca/products/a/#photo', '/products/b/']])
        self.assertEqual(len(collect_listing_links(page, max_scrolls=0)), 2)

    def test_repeated_api_cursor_is_an_incomplete_scan_error(self):
        page = FakeCollectPage([[]])
        first = self.shop_response(0)
        first['url'] = 'https://webapi.depop.com/presentation/api/v1/shops/123/products/?after=cursor-24'
        page.captured_shop_pages = [first]
        with self.assertRaises(scraper.CollectionIncompleteError):
            collect_listing_links(page, max_links=100, aggressive_end_scroll=True)

    def test_capture_is_reset_on_navigation_and_installed_only_once(self):
        page = FakeCapturePage()
        page.goto = mock.Mock(return_value=FakeResponse(200))
        install_shop_products_capture(page)
        install_shop_products_capture(page)
        page.handlers['response'](FakeProductApiResponse(
            'https://webapi.depop.com/presentation/api/v1/shops/123/products/', 200,
            json.dumps({'objects': [{'slug': 'old-category'}]}),
        ))
        self.assertEqual(extract_captured_shop_product_hrefs(page), ['/products/old-category/'])
        scraper.guarded_goto(page, 'https://www.depop.com/seller/?groups=bottoms')
        self.assertEqual(extract_captured_shop_product_hrefs(page), [])
        self.assertEqual(len(page.init_scripts), 1)

    def test_captured_sold_feed_and_purchased_items_are_excluded(self):
        page = FakeCollectPage([[]])
        page.captured_shop_pages = [
            {'url': 'https://webapi.depop.com/presentation/api/v1/shops/123/products/by-status/sold/',
             'status': 200, 'text': json.dumps({'objects': [{'slug': 'sold-feed'}]})},
            {'status': 200, 'text': json.dumps({'objects': [
                {'slug': 'purchased', 'status': 'STATUS_PURCHASED'},
                {'slug': 'inactive', 'active_status': 'inactive'},
                {'slug': 'available', 'status': 'STATUS_ONSALE'},
            ]})},
        ]
        self.assertEqual(extract_captured_shop_product_hrefs(page), ['/products/available/'])

    def test_shop_product_details_satisfy_listing_parse_without_another_request(self):
        page = FakeCollectPage([[]])
        page.url = 'https://www.depop.com/example_shop/'
        page.captured_shop_pages = [{'status': 200, 'text': json.dumps({'objects': [{
            'slug': 'cached-current-product', 'status': 'STATUS_ONSALE',
            'description': 'Pit to pit 21 inches. Length 27 inches.',
            'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'sizes': [{'name': 'M'}],
            'preview': {'formats': {'P0': {'url': 'https://media-photos.depop.com/test.jpg'}}},
            'pricing': {'display_price': {'headline_price': {'amount': '25', 'currency': 'CAD'}}},
        }]})}]
        links = collect_listing_links(page, max_scrolls=0)
        with mock.patch('scraper.guarded_goto') as goto:
            item = parse_listing(mock.Mock(), links[0])
        goto.assert_not_called()
        self.assertEqual(item['seller'], 'example_shop')
        self.assertEqual(item['sizeLabel'], 'M')
        self.assertEqual(item['price'], '$25.00')

    def test_listing_retry_after_is_preserved_and_errors_are_not_silently_dropped(self):
        page = FakeRateLimitedListingPage()
        page.goto = mock.Mock(return_value=FakeResponse(429, '240'))
        with self.assertRaises(RateLimitError) as caught:
            parse_listing(page, 'https://www.depop.com/products/limited/')
        self.assertEqual(caught.exception.retry_after_seconds, 240)

        page.goto.side_effect = RuntimeError('Page.goto: NS_BINDING_ABORTED')
        with self.assertRaisesRegex(RuntimeError, 'NS_BINDING_ABORTED'):
            parse_listing(page, 'https://www.depop.com/products/failed/')

    def test_missing_listing_is_skipped_but_forbidden_listing_is_not(self):
        page = FakeRateLimitedListingPage()
        page.goto = mock.Mock(return_value=FakeResponse(404))
        self.assertIsNone(parse_listing(page, 'https://www.depop.com/products/deleted/'))
        page.goto.return_value = FakeResponse(403)
        with self.assertRaises(RateLimitError):
            parse_listing(page, 'https://www.depop.com/products/blocked/')

    def test_normal_listing_text_does_not_trigger_rate_limit(self):
        self.assertIsNone(extract_rate_limit_message(
            'Slow down vintage graphic tee. Try again later slogan.',
            expected_content_missing=False,
        ))

    def test_cancelled_navigation_does_not_start_even_without_pacing(self):
        page = FakeGotoPage()
        with self.assertRaises(SearchCancelled):
            scraper.guarded_goto(page, 'https://www.depop.com/', should_cancel=lambda: True)
        self.assertEqual(page.gotos, [])

    def test_json_ld_fills_missing_description_even_when_listing_time_exists(self):
        page = FakeFastListingPage()
        original = page.evaluate
        page.evaluate = lambda expression: {**original(expression), 'description': ''}
        page.locator = lambda selector: FakeLocator(texts=[json.dumps({'@graph': [{
            '@type': 'Product', 'description': 'Size US 10.5. Leather shoes.',
            'offers': {'price': 25, 'priceCurrency': 'CAD'},
        }]})])
        item = parse_listing(page, 'https://www.depop.com/products/json-ld/')
        self.assertEqual(item['description'], 'Size US 10.5. Leather shoes.')

    def test_server_error_is_not_cached_as_an_empty_listing(self):
        page = FakeRateLimitedListingPage()
        page.goto = mock.Mock(return_value=FakeResponse(500))
        page.inner_text = lambda *a, **k: 'Internal server error'
        page.title = lambda: 'Server error'
        url = 'https://www.depop.com/products/server-error/'
        with self.assertRaises(scraper.FetchError):
            parse_listing(page, url)
        self.assertIsNone(scraper.get_cached_listing(url))

    def test_log_debug_collapses_repeated_escape_messages(self):
        with mock.patch("builtins.print") as print_mock:
            flush_debug_logs()
            log_debug("[login-modal] Pressed Escape to dismiss modal", aggregate_key="login_modal_escape")
            log_debug("[login-modal] Pressed Escape to dismiss modal", aggregate_key="login_modal_escape")
            flush_debug_logs()

        print_mock.assert_called_once_with("[login-modal] Pressed Escape to dismiss modal x2")


if __name__ == "__main__":
    unittest.main()
