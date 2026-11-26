import io
import json
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def make_product(source, description="", extra_html="", *, slug="graphic-t-shirt", **fields):
    store = retail.RETAILERS[source]
    url = store.origin + store.product_prefix + slug
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


@contextmanager
def serve_catalogue(routes):
    requested = []

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            status, body = routes.get(self.path, (404, "Not found"))
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if status == 429:
                self.send_header("Retry-After", "1800")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requested
        finally:
            server.shutdown()
            thread.join()


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

    def search_events(self, source, timeout=2):
        job = main._ensure_search_job({
            "searchId": uuid4().hex, "retailSource": source,
            "measurements": {"first": 20.875, "second": 26.875},
            "p2pTolerance": .375, "lengthTolerance": .375, "maxPriceCad": 35,
        })
        until = time.monotonic() + timeout
        while not job.complete and time.monotonic() < until:
            time.sleep(.01)
        self.assertTrue(job.complete, f"{source} search did not finish")
        return [json.loads(event.removeprefix(b"data: ")) for event in job.read_from(0)[0]]

    def assert_last_listing_matched(self, events, url, total):
        matches = [event["item"] for event in events if event["type"] == "match"]
        self.assertEqual([(item["url"], item["p2p"], item["length"]) for item in matches], [(url, 21, 27)])
        self.assertEqual([event["links"] for event in events if event["type"] == "meta"], [total])
        self.assertEqual(
            {key: events[-1][key] for key in ("type", "processed", "total", "matches", "stopReason")},
            {"type": "done", "processed": total, "total": total, "matches": 1, "stopReason": "completed"},
        )

    def numbered_routes(self):
        source = "peoples-thrift"
        store = retail.RETAILERS[source]
        early, early_html = make_product(source, extra_html='<h2>SIZE &amp; FIT</h2>24" L, 21" W.', slug="early-tee")
        final, final_html = make_product(source, extra_html='<h2>SIZE &amp; FIT</h2>27" L, 21" W.', slug="final-tee")
        paths = [store.catalogue, *(f"{store.catalogue}?page={n}&sort=recent" for n in range(2, 7))]
        routes = {store.origin + "/robots.txt": "User-agent: *\nAllow: /"}
        for number, path in enumerate(paths, 1):
            product = final if number == len(paths) else early
            next_link = f'<link rel="next" href="?page={number + 1}&amp;sort=recent">' if number < len(paths) else ""
            routes[store.origin + path] = f'{next_link}<a href="{product}">T-shirt</a>'
        routes.update({early: early_html, final: final_html})
        return routes, [store.origin + path for path in paths], early, final

    def test_numbered_search_parses_last_product_after_following_all_pages(self):
        routes, pages, early, final = self.numbered_routes()
        with mock.patch.object(retail, "sleep_with_cancel"), mock.patch.object(
            retail._OPENER, "open", side_effect=lambda req, **_: io.BytesIO(routes[req.full_url].encode()),
        ) as request:
            events = self.search_events("peoples-thrift")
        self.assert_last_listing_matched(events, final, 2)
        self.assertEqual([call.args[0].full_url for call in request.call_args_list[1:]], [*pages, early, final])

    def test_numbered_search_reports_failed_final_page(self):
        routes, pages, _, _ = self.numbered_routes()

        def open_page(request, **_):
            if request.full_url == pages[-1]:
                raise HTTPError(request.full_url, 503, "Service Unavailable", Message(), None)
            return io.BytesIO(routes[request.full_url].encode())

        with mock.patch.object(retail, "sleep_with_cancel"), mock.patch.object(retail._OPENER, "open", side_effect=open_page):
            events = self.search_events("peoples-thrift")
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("503", events[-1]["message"])
        self.assertNotIn("done", [event["type"] for event in events])

    def test_all_three_retailers_are_enabled_by_default(self):
        with mock.patch.dict("os.environ", clear=True):
            self.assertTrue(all(retail.source_enabled(source_id) for source_id in retail.RETAILERS))

    def test_collection_scans_past_empty_pages_and_finds_tee_on_page_five(self):
        url, body = make_product("peoples-thrift", extra_html='<h2>SIZE &amp; FIT</h2>27" L, 21" W.')
        store = retail.RETAILERS["peoples-thrift"]
        pages = [
            f'<a href="?page={number}">Next</a><a href="/product-page/jeans">Jeans</a>'
            for number in range(2, 6)
        ]
        pages.append(f'<a href="https://example.com/product-page/outside-tee">Outside</a><a href="{url}">Tee</a>')
        with mock.patch.object(retail, "_read", side_effect=["User-agent: *\nAllow: /", *pages, body]) as read:
            count, (candidate, listing) = list(retail.search_catalogue("peoples-thrift", 1, None))
        self.assertEqual((count, candidate, listing["p2p"]), (1, url, 21))
        self.assertEqual([call.args[2] for call in read.call_args_list[1:-1]], [
            store.catalogue, *(f"{store.catalogue}?page={number}" for number in range(2, 6)),
        ])

    def test_quick_rerun_reuses_parsed_product(self):
        source = "peoples-thrift"
        url, body = make_product(source, extra_html='<h2>SIZE &amp; FIT</h2>27" L, 21" W.')
        robots = "User-agent: *\nAllow: /"
        collection = f'<a href="{url}">Tee</a>'
        with mock.patch.object(retail, "_read", side_effect=[robots, collection, body, robots, collection]) as read:
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
            f'<a href="{product}">Tee</a>',
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

    def test_english_shopify_product_urls_are_canonicalized(self):
        store = retail.RETAILERS["la-penderie"]
        sku = "graphic-t-shirt"
        for href in (f"{store.catalogue}/products/{sku}", f"/products/{sku}", f"/en/products/{sku}?variant=2"):
            self.assertEqual(retail._product_url(store, href), f"{store.origin}/en/products/{sku}")

    @unittest.skipUnless(os.getenv("DEBOT_PLAYWRIGHT_TESTS") == "1", "Enable Firefox fixture tests.")
    def test_shopify_search_parses_last_product_after_delayed_scroll_batches(self):
        for source in ("wildrose", "la-penderie"):
            with self.subTest(source=source):
                store, requested, events = self.search_scroll_fixture(source)
                final = store.origin + store.product_prefix + "final-tee"
                self.assert_last_listing_matched(events, final, 3)
                self.assertEqual(requested, [
                    "/robots.txt", store.catalogue, store.catalogue + "?page=2", store.catalogue + "?page=3",
                    store.product_prefix + "first-tee", store.product_prefix + "middle-tee", store.product_prefix + "final-tee",
                ])

    @unittest.skipUnless(os.getenv("DEBOT_PLAYWRIGHT_TESTS") == "1", "Enable Firefox fixture tests.")
    def test_shopify_search_reports_block_when_loading_final_batch(self):
        store, requested, events = self.search_scroll_fixture("wildrose", last_status=429)
        self.assertEqual((events[-1]["type"], events[-1]["code"]), ("error", "retailer_paused"))
        self.assertNotIn("done", [event["type"] for event in events])
        self.assertEqual(requested, [
            "/robots.txt", store.catalogue, store.catalogue + "?page=2", store.catalogue + "?page=3",
        ])

    def search_scroll_fixture(self, source, last_status=200):
        routes = {}
        with serve_catalogue(routes) as (origin, requested):
            store = replace(retail.RETAILERS[source], origin=origin)
            with mock.patch.dict(retail.RETAILERS, {source: store}), \
                    mock.patch.dict("os.environ", {"DEBOT_AUTHORIZED_RETAIL_SOURCES": "wildrose,la-penderie"}), \
                    mock.patch.object(retail, "REQUEST_INTERVAL", .25):
                routes.update(self._scroll_fixture_routes(store, source))
                if last_status != 200:
                    routes[f"{store.catalogue}?page=3"] = (last_status, "")
                events = self.search_events(source, timeout=15)
        return store, requested, events

    @staticmethod
    def _scroll_fixture_routes(store, source):
        routes = {"/robots.txt": (200, "User-agent: *\nAllow: /")}
        slugs = ["first-tee", "middle-tee", "final-tee"]
        for slug in slugs:
            _, html = make_product(source, f'Pit to pit 21" Length {27 if slug == "final-tee" else 24}"', slug=slug)
            routes[store.product_prefix + slug] = (200, html)

        def card(slug):
            return f'<div style="height:1800px"><a href="{store.product_prefix + slug}">T-shirt</a></div>'

        routes[store.catalogue] = (200, f"""<!doctype html><main>{card(slugs[0])}</main><script>
            const grid = document.querySelector('main');
            let batch = 2;
            window.addEventListener('scroll', () => {{
                const atBottom = scrollY + innerHeight >= document.documentElement.scrollHeight - 4;
                if (!atBottom || batch > 3 || grid.getAttribute('aria-busy') === 'true') return;
                grid.setAttribute('aria-busy', 'true');
                setTimeout(async () => {{
                    const response = await fetch('?page=' + batch++);
                    grid.insertAdjacentHTML('beforeend', await response.text());
                    grid.setAttribute('aria-busy', 'false');
                }}, batch === 3 ? 1600 : 0);
            }});
            </script>""")
        for number, slug in enumerate(slugs[1:], 2):
            routes[f"{store.catalogue}?page={number}"] = (200, card(slug))
        return routes

    @unittest.skipUnless(os.getenv("DEBOT_PLAYWRIGHT_TESTS") == "1", "Enable Firefox fixture tests.")
    def test_browser_scroll_collects_deferred_links_beyond_old_cap(self):
        requests = []
        blocked = False

        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if self.path.endswith("?page=2"):
                    self.send_response(429 if blocked else 200)
                    self.send_header("Retry-After", "1800")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""<!doctype html><main style="min-height:1800px">
                    <a href="/products/first-tee">First</a>
                    <a href="https://example.com/products/not-ours-tee">External</a>
                    </main><script>
                    window.addEventListener('scroll', () => {
                        if (window.scrollY < innerHeight) return;
                        if (document.querySelector('[data-loaded]')) return;
                        const main = document.querySelector('main');
                        main.dataset.loaded = 'yes';
                        fetch('?page=2').then(response => {
                            if (!response.ok) return;
                            for (let i = 0; i < 41; i++) {
                                const a = document.createElement('a');
                                a.href = '/products/scroll-tee-' + i;
                                a.textContent = 'Product';
                                main.append(a);
                            }
                        }).catch(() => {});
                    });
                    </script>""")

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Site) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            store = replace(retail.RETAILERS["wildrose"], origin=f"http://127.0.0.1:{server.server_port}")
            state = retail._OriginState()
            robots = retail.RobotFileParser()
            robots.parse(["User-agent: *", "Allow: /"])
            try:
                candidates = retail._browser_candidates(store, state, robots, 1000, None, .25)
                blocked = True
                state.scroll_cache = None
                with self.assertRaises(retail.RetailAccessError) as error:
                    retail._browser_candidates(store, state, robots, 1000, None, .25)
                self.assertEqual(error.exception.code, "retailer_paused")
                self.assertGreater(state.blocked_until - time.monotonic(), 1795)
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(len(candidates), 42)
        self.assertIn(store.origin + "/products/scroll-tee-40", candidates)
        self.assertEqual(requests, [store.catalogue, store.catalogue + "?page=2"] * 2)


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
