import json
import sys
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock
from uuid import uuid4


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main
import retail_sources as retail
from fastapi import HTTPException
from scraper import SearchCancelled
from search_models import validate_search_payload


def make_product(source, description="", extra_html="", **fields):
    store = retail.RETAILERS[source]
    url = store.origin + store.product_prefix + "graphic-t-shirt"
    product = {
        "@type": "Product",
        "name": "Graphic T-shirt",
        "url": url + "/",
        "image": [{"@type": "ImageObject", "contentUrl": "https://example.com/shirt.jpg"}],
        "description": description,
        "offers": {"price": "12.99", "priceCurrency": "CAD", "availability": "https://schema.org/InStock"},
        **fields,
    }
    return url, f'<script type="application/ld+json">{json.dumps(product)}</script>{extra_html}'


class RetailParseTest(unittest.TestCase):
    def test_wildrose_shoulder_to_hem_and_fractional_width(self):
        url, body = make_product("wildrose", '<p>Pit to Pit - 20 3/4″</p><p>Length (Top of Shoulder to Bottom) - 27″</p>')
        item = retail.parse_product(retail.RETAILERS["wildrose"], url, body)
        self.assertEqual((item["p2p"], item["length"]), (20.75, 27))

    def test_la_penderie_uses_listed_metric_measurements(self):
        url, body = make_product("la-penderie", '<p>Aisselle à aisselle : 53 cm</p><p>Longueur : 68,5 cm</p>')
        item = retail.parse_product(retail.RETAILERS["la-penderie"], url, body)
        self.assertAlmostEqual(item["p2p"], 53 / 2.54, places=3)
        self.assertAlmostEqual(item["length"], 68.5 / 2.54, places=3)

    def test_peoples_thrift_uses_size_section_and_actual_sale_price(self):
        url, body = make_product(
            "peoples-thrift", "Graphic T-Shirt",
            '<div class="wixui-product-prices-wrapper"><div>Original price<p>$12.99</p></div>'
            '<div>Sale price<p>$10.40</p></div></div>'
            '<h2>SIZE &amp; FIT</h2><p>25.5" L, 19.5" W.</p><h2>SHIPPING POLICY</h2>',
        )
        item = retail.parse_product(retail.RETAILERS["peoples-thrift"], url, body)
        self.assertEqual((item["p2p"], item["length"], item["price"]), (19.5, 25.5, 10.40))

    def test_excludes_bundles_sold_items_foreign_prices_and_guessed_sizes(self):
        store = retail.RETAILERS["wildrose"]
        for changes in (
            {"name": "Bundle of T-shirts"},
            {"name": "3-pack T-Shirt"},
            {"name": "Black t-shirt dress"},
            {"offers": {"price": "15", "priceCurrency": "CAD", "availability": "https://schema.org/OutOfStock"}},
            {"offers": {"price": "15", "priceCurrency": "USD", "availability": "https://schema.org/InStock"}},
            {"description": 'Chest: 42" Length: 27"'},
            {"description": 'Size L, Length: 27"'},
            {"image": []},
        ):
            with self.subTest(changes=changes):
                url, body = make_product("wildrose", **{"description": 'Pit to pit 21" Length 27"', **changes})
                self.assertIsNone(retail.parse_product(store, url, body))

    def test_non_product_html_is_an_error_not_a_sold_listing(self):
        store = retail.RETAILERS["peoples-thrift"]
        with self.assertRaises(retail.RetailAccessError):
            retail.parse_product(store, store.origin + "/product-page/graphic-t-shirt", "<h1>Access denied</h1>")


class RetailCatalogueTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(retail, "_ORIGINS", {key: retail._OriginState() for key in retail.RETAILERS})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_shopify_collection_pagination_ignores_external_non_tees_and_duplicates(self):
        url, body = make_product("wildrose", 'Pit to pit 21" Length 27"')
        store = retail.RETAILERS["wildrose"]
        parent = store.catalogue
        routes = {
            "/robots.txt": "User-agent: *\nAllow: /\n",
            parent: (
                '<a href="https://example.com/products/outside-tee">Outside</a>'
                '<a href="/products/pants">Pants</a>'
                f'<a href="{url}/?variant=123">Tee</a><a href="{parent}?page=2">Next</a>'
            ),
            parent + "?page=2": (
                f'<a href="{url}">Duplicate</a>'
                '<a href="/collections/vintage/products/another-tee">Another tee</a>'
            ),
            "/products/graphic-t-shirt": body,
            "/products/another-tee": "",
        }
        with mock.patch.dict("os.environ", {"DEBOT_AUTHORIZED_RETAIL_SOURCES": "wildrose"}), \
                mock.patch.object(retail, "_read", side_effect=lambda _, __, path, *_args: routes[path]):
            count, first, second = list(retail.search_catalogue("wildrose", 4, None))
        self.assertEqual(count, 2)
        self.assertEqual(first[1]["p2p"], 21)
        self.assertEqual(second, (store.origin + "/products/another-tee", None))

    def test_wix_sitemap_matches_only_canonical_tee_links_and_honors_max_links(self):
        url, body = make_product("peoples-thrift", extra_html='<h2>SIZE &amp; FIT</h2>27" L, 21" W.')
        store = retail.RETAILERS["peoples-thrift"]
        sitemap = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f'<url><loc>{store.origin}/product-page/other-tee</loc><lastmod>2026-01-01</lastmod></url>'
            '<url><loc>https://example.com/product-page/other-tee</loc><lastmod>2026-05-01</lastmod></url>'
            f'<url><loc>{url}</loc><lastmod>2026-02-01</lastmod></url></urlset>'
        )
        with mock.patch.object(retail, "_read", side_effect=["User-agent: *\nAllow: /", sitemap, body]) as read:
            count, (candidate, listing) = list(retail.search_catalogue("peoples-thrift", 1, None))
        self.assertEqual((count, candidate, listing["p2p"]), (1, url, 21))
        self.assertEqual(read.call_count, 3)

    def test_quick_rerun_reuses_parsed_product(self):
        source = "peoples-thrift"
        url, body = make_product(source, extra_html='<h2>SIZE &amp; FIT</h2>27" L, 21" W.')
        robots = "User-agent: *\nAllow: /"
        sitemap = f"<urlset><url><loc>{url}</loc></url></urlset>"
        with mock.patch.object(retail, "_read", side_effect=[robots, sitemap, body, robots, sitemap]) as read:
            first = list(retail.search_catalogue(source, 1, None))
            second = list(retail.search_catalogue(source, 1, None))
        self.assertEqual(first, second)
        self.assertEqual(first[1][1]["p2p"], 21)
        self.assertEqual(read.call_count, 5)

    def test_permission_check_never_fetches_protected_catalogue(self):
        with mock.patch.dict("os.environ", {"DEBOT_AUTHORIZED_RETAIL_SOURCES": ""}), \
                mock.patch.object(retail, "_read") as read:
            for source_id in ("wildrose", "la-penderie"):
                with self.assertRaisesRegex(retail.RetailAccessError, "requires permission"):
                    next(retail.search_catalogue(source_id, 1, None))
        read.assert_not_called()

    def test_robots_wildcard_blocks_a_product_before_http(self):
        source = "peoples-thrift"
        store = retail.RETAILERS[source]
        product = store.origin + "/product-page/graphic-t-shirt"
        with mock.patch.object(retail, "_read", side_effect=[
            "User-agent: *\nAllow: /\nDisallow: /product-page/*t-shirt$",
            f"<urlset><url><loc>{product}</loc></url></urlset>",
        ]) as read:
            catalogue = retail.search_catalogue(source, 2, None)
            self.assertEqual(next(catalogue), 1)
            with self.assertRaisesRegex(retail.RetailAccessError, "robots.txt restricts"):
                next(catalogue)
        self.assertEqual(read.call_count, 2)

    def test_shared_cooldown_honors_retry_after_and_does_not_retry(self):
        source = "peoples-thrift"
        state = retail._ORIGINS[source]
        headers = Message()
        headers["Retry-After"] = "1800"
        with mock.patch.object(retail._OPENER, "open", side_effect=HTTPError("", 429, "", headers, None)) as request:
            for _ in range(2):
                with self.assertRaises(retail.RetailAccessError) as error:
                    next(retail.search_catalogue(source, 1, None))
                self.assertEqual(error.exception.code, "retailer_paused")
        self.assertGreater(state.blocked_until - time.monotonic(), 1799)
        request.assert_called_once()

    def test_cancelled_search_does_not_request_robots_and_releases_lock(self):
        source = "peoples-thrift"
        with mock.patch.object(retail._OPENER, "open") as request:
            with self.assertRaises(SearchCancelled):
                next(retail.search_catalogue(source, 1, lambda: True))
        request.assert_not_called()
        self.assertFalse(retail._ORIGINS[source].lock.locked())


class RetailSearchJobTest(unittest.TestCase):
    def test_retail_source_is_validated(self):
        for payload in (
            {"retailSource": "https://example.com"},
            {"retailSource": "peoples-thrift", "seller": "sample"},
            {"retailSource": "peoples-thrift", "category": "bottoms"},
        ):
            with self.subTest(payload=payload), self.assertRaises(HTTPException):
                validate_search_payload(payload)

    def test_retail_job_bypasses_depop_cooldown_and_applies_fit_and_price(self):
        base = {"title": "Graphic T-Shirt", "p2p": 21, "length": 27, "price": 15}
        rows = [
            ("matching", base), ("too-short", {**base, "length": 26}),
            ("too-expensive", {**base, "price": 35.01}), ("sold", None),
        ]

        def catalogue(*_):
            yield len(rows)
            for slug, product in rows:
                url = f"https://www.peoplesthrift.ca/product-page/{slug}"
                yield url, {**product, "url": url} if product else None

        payload = {
            "searchId": uuid4().hex, "retailSource": "peoples-thrift",
            "measurements": {"first": 20.875, "second": 26.875},
            "p2pTolerance": .375, "lengthTolerance": .375, "maxPriceCad": 35,
        }
        with mock.patch.object(main, "search_catalogue", catalogue), \
                mock.patch.object(main, "RATE_LIMIT_BLOCKED_UNTIL_TS", time.time() + 3600), \
                mock.patch.object(main, "SEARCH_JOB_SLOTS", threading.Semaphore(0)), \
                mock.patch.object(main, "sync_playwright") as playwright:
            job = main._ensure_search_job(payload)
            until = time.monotonic() + 2
            while not job.complete and time.monotonic() < until:
                time.sleep(.01)
            self.assertTrue(job.complete)
        playwright.assert_not_called()
        events = [json.loads(event.removeprefix(b"data: ")) for event in job.read_from(0)[0]]
        self.assertEqual(len([event for event in events if event["type"] == "remove"]), 3)
        matches = [event["item"] for event in events if event["type"] == "match"]
        self.assertEqual([(item["seller"], item["title"], item["price"]) for item in matches],
                         [("retail:peoples-thrift", "Graphic T-Shirt", "$15.00 CAD")])
        self.assertEqual((events[-1]["type"], events[-1]["processed"], events[-1]["matches"]), ("done", 4, 1))
