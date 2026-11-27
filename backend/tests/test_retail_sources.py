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

    def test_wildrose_omitted_quote_inherits_sibling_unit(self):
        store = retail.RETAILERS["wildrose"]
        url, body = make_product(
            "wildrose",
            "Measurements:\nPit to Pit - 20”\nLength (Top of Shoulder to Bottom) - 25.25\nFits like: M",
        )
        item = retail.parse_product(store, url, body)
        self.assertEqual((item["p2p"], item["length"]), (20, 25.25))

        url2, body2 = make_product(
            "wildrose",
            "Measurements:\nPit to Pit - 17\nLength (Top of Shoulder to Bottom) - 26.50”\nFits like: S",
        )
        item2 = retail.parse_product(store, url2, body2)
        self.assertEqual((item2["p2p"], item2["length"]), (17, 26.5))

        url3, body3 = make_product(
            "wildrose",
            'Full length 100% cotton. Pit to Pit - 21". Length (Top of Shoulder to Bottom) - 27".',
        )
        item3 = retail.parse_product(store, url3, body3)
        self.assertEqual((item3["p2p"], item3["length"]), (21, 27))

        url4, body4 = make_product("wildrose", "Pit to Pit - 21\nLength - 27")
        self.assertIsNone(retail.parse_product(store, url4, body4))

    def test_la_penderie_uses_listed_metric_measurements(self):
        url, body = make_product("la-penderie", '<p>Aisselle à aisselle : 53 cm</p><p>Longueur : 68,5 cm</p>')
        item = retail.parse_product(retail.RETAILERS["la-penderie"], url, body)
        self.assertAlmostEqual(item["p2p"], 53 / 2.54, places=3)
        self.assertAlmostEqual(item["length"], 68.5 / 2.54, places=3)

    def test_la_penderie_live_chest_width_and_submit_button_availability(self):
        store = retail.RETAILERS["la-penderie"]
        active_btn = (
            '<div class="mb20 product-single__description rte">'
            '<p><strong>Size</strong>: Large</p><p>Chest: 21"<br>Sleeves: 8"<br>Length: 27"</p></div>'
            '<button id="ProductSubmitButton-main" type="submit" name="add" class="btn pr_btn product-form__submit">'
            '<span class="txt">Add to cart</span></button>'
        )
        url, body = make_product(
            "la-penderie",
            'Size: LargeChest: 21&quot;Sleeves: 8&quot;Length: 27&quot;100% Cotton',
            extra_html=active_btn,
            offers=[{"price": "6.3", "priceCurrency": "CAD", "availability": "http://schema.org/OutOfStock"}],
        )
        item = retail.parse_product(store, url, body)
        self.assertEqual((item["p2p"], item["length"], item["price"]), (21, 27, 6.3))

        # Truly sold-out La Penderie item with active upsell buttons below must still be rejected.
        sold_html = (
            '<button id="ProductSubmitButton-main" type="submit" name="add" class="btn pr_btn product-form__submit" disabled>'
            '<span class="txt">Sold out</span></button>'
            '<button type="submit" name="add" id="AddToCart-upsell" class="cartBtn">Add to cart</button>'
        )
        url_sold, body_sold = make_product(
            "la-penderie",
            'Size: LargeChest: 21&quot;Length: 27&quot;',
            extra_html=sold_html,
            offers=[{"price": "6.3", "priceCurrency": "CAD", "availability": "http://schema.org/OutOfStock"}],
        )
        self.assertIsNone(retail.parse_product(store, url_sold, body_sold))

        # Full-chest circumference values (> 32") must still be rejected on La Penderie.
        btn_only = (
            '<button id="ProductSubmitButton-main" type="submit" name="add" class="btn pr_btn product-form__submit">'
            '<span class="txt">Add to cart</span></button>'
        )
        url_circ, body_circ = make_product("la-penderie", 'Chest: 42" Length: 27"', extra_html=btn_only)
        self.assertIsNone(retail.parse_product(store, url_circ, body_circ))

    def test_underscore_slug_and_anchor_title_discovery(self):
        store = retail.RETAILERS["wildrose"]
        url = store.origin + "/products/untitled-feb1_12-56"
        hoodie_url = store.origin + "/products/varsity-women-s-lacrosse-russell"
        polo_url = store.origin + "/products/sunice-navy-polo-shirt"
        self.assertEqual(retail._product_url(store, "/products/untitled-feb1_12-56"), url)
        candidates = {}
        retail._add_candidates(
            store,
            [
                ("/products/untitled-feb1_12-56", "Vintage 90s Disney Blue Tee Shirt", 15.0),
                ("/products/varsity-women-s-lacrosse-russell", "Vintage Pullover Hoodie", 32.0),
                ("/products/sunice-navy-polo-shirt", "Navy Polo Shirt - Men", 22.5),
                ("/products/vintage-1983-levi-s-orange-tab-dark-wash-jeans", "Dark Wash Jeans", 25.0),
                ("/products/graphic-t-shirt", "Bundle of T-shirts", 10.0),
                ("/products/rw-co-men-s-slim-fit-miles-2-piece-black-suit-34-m", "2-Piece Black Suit", 30.0),
            ],
            candidates,
            10,
        )
        self.assertEqual(candidates, {url: 15.0, hoodie_url: 32.0, polo_url: 22.5})
        parsed_url, html = make_product(
            "wildrose",
            'Pit to Pit - 21”\nLength (Top of Shoulder to Bottom) - 27”',
            slug="untitled-feb1_12-56",
            name="Vintage 90s Disney Blue Tee Shirt",
            offers={"price": "15.00", "priceCurrency": "CAD", "availability": "http://schema.org/InStock"},
        )
        item = retail.parse_product(store, parsed_url, html)
        self.assertEqual((item["url"], item["p2p"], item["length"], item["price"]), (url, 21, 27, 15.0))

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

    def test_bottoms_parsers_for_all_three_stores(self):
        wr = retail.RETAILERS["wildrose"]
        wr_url, wr_body = make_product(
            "wildrose",
            "\nTag: Made In Canada 32 x 34\nMeasurments:\nWaist (Unstretched) -\xa0 30”\nInseam - 32.25",
            slug="vintage-1983-levi-s-orange-tab-dark-wash-jeans",
            name="Vintage 1983 Levi’s Orange Tab Dark Wash Jeans",
            offers={"price": "42.00", "priceCurrency": "CAD", "availability": "http://schema.org/InStock"},
        )
        wr_item = retail.parse_product(wr, wr_url, wr_body, category="bottoms")
        self.assertEqual(
            (wr_item["waist"], wr_item["inseam"], wr_item["rise"], wr_item["legOpening"], wr_item["price"]),
            (30.0, 32.25, None, None, 42.0),
        )

        lp = retail.RETAILERS["la-penderie"]
        active_btn = (
            '<div class="mb20 product-single__description rte">'
            '<p>Size: 36 x 32</p><p>Size: 18.5"<br>Rise: 10"<br>Length: 42"</p></div>'
            '<button id="ProductSubmitButton-p" type="submit" name="add" class="btn pr_btn product-form__submit">'
            'Add to cart</button>'
        )
        lp_url, lp_body = make_product(
            "la-penderie",
            'Size: 36 x 32Condition: Like newSize: 18.5&quot;Rise: 10&quot;Length: 42&quot;',
            extra_html=active_btn,
            slug="simons-burgundy-straight-leg-pants",
            name="Burgundy straight leg pants (xl) - Men",
            offers=[{"price": "18.0", "priceCurrency": "CAD", "availability": "http://schema.org/OutOfStock"}],
        )
        lp_item = retail.parse_product(lp, lp_url, lp_body, category="bottoms")
        self.assertEqual(
            (lp_item["waist"], lp_item["inseam"], lp_item["rise"], lp_item["price"]),
            (37.0, 32.0, 10.0, 18.0),
        )

        # Tag-only bottoms without explicit unit-bearing measurements must be rejected.
        tag_only_btn = (
            '<button id="ProductSubmitButton-p" type="submit" name="add" class="btn pr_btn product-form__submit">'
            'Add to cart</button>'
        )
        tag_url, tag_body = make_product(
            "la-penderie", "Size: 36 x 32 Condition: Like new", extra_html=tag_only_btn,
            slug="tag-only-jeans", name="Blue straight leg jeans",
        )
        self.assertIsNone(retail.parse_product(lp, tag_url, tag_body, category="bottoms"))

        pt = retail.RETAILERS["peoples-thrift"]
        pt_url, pt_body = make_product(
            "peoples-thrift", "RW & Co. Men's Grey Pants",
            '<div class="wixui-product-prices-wrapper"><div>Original price<p>$29.99</p></div>'
            '<div>Sale price<p>$24.00</p></div></div>'
            '<h2>SIZE &amp; FIT</h2><p>42" L, 32" inseam, 10" H rise, 17.5" W waist. Tagged 33 x 32.</p>',
            slug="rw-co-men-s-grey-pants-34",
            name="RW & Co. Men's Grey Pants (34)",
            offers={"price": "29.99", "priceCurrency": "CAD", "availability": "https://schema.org/InStock"},
        )
        pt_item = retail.parse_product(pt, pt_url, pt_body, category="bottoms")
        self.assertEqual(
            (pt_item["waist"], pt_item["inseam"], pt_item["rise"], pt_item["price"]),
            (35.0, 32.0, 10.0, 24.0),
        )

        pt_short_url, pt_short_body = make_product(
            "peoples-thrift", "Shorts",
            '<h2>SIZE &amp; FIT</h2><p>16" W, 19.5" L, 9" inseam. 98% Cotton.</p>',
            slug="abercrombie-men-s-shorts-m",
            name="Abercrombie Men's Brown Shorts (M)",
        )
        pt_short = retail.parse_product(pt, pt_short_url, pt_short_body, category="bottoms")
        self.assertEqual((pt_short["waist"], pt_short["inseam"], pt_short["rise"]), (32.0, 9.0, None))

        # Denim jackets and bundles must not be treated as bottoms.
        dj_url, dj_body = make_product(
            "wildrose", 'Waist - 34” Inseam - 32”', slug="vintage-denim-jacket", name="Vintage 90s Denim Jacket",
        )
        self.assertIsNone(retail.parse_product(wr, dj_url, dj_body, category="bottoms"))

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
    def test_shopify_scrollerpro_deduplicates_fetches_and_skips_recrawling_next_link(self):
        routes = {}
        with serve_catalogue(routes) as (origin, requested):
            store = replace(retail.RETAILERS["wildrose"], origin=origin)
            with mock.patch.dict(retail.RETAILERS, {"wildrose": store}), \
                    mock.patch.dict("os.environ", {"DEBOT_AUTHORIZED_RETAIL_SOURCES": "wildrose,la-penderie"}), \
                    mock.patch.object(retail, "REQUEST_INTERVAL", .15):
                routes["/robots.txt"] = (200, "User-agent: *\nAllow: /")
                _, expensive_html = make_product(
                    "wildrose", 'Pit to Pit - 21”\nLength (Top of Shoulder to Bottom) - 27”',
                    slug="expensive-tee",
                    offers={"price": "45.00", "priceCurrency": "CAD", "availability": "http://schema.org/InStock"},
                )
                _, sale_html = make_product(
                    "wildrose", 'Pit to Pit - 21”\nLength (Top of Shoulder to Bottom) - 27',
                    slug="untitled-feb1_12-56",
                    name="Vintage 90s Blue Tee Shirt",
                    offers={"price": "15.00", "priceCurrency": "CAD", "availability": "http://schema.org/InStock"},
                )
                routes["/products/expensive-tee"] = (200, expensive_html)
                routes["/products/untitled-feb1_12-56"] = (200, sale_html)

                def card(slug, title, price_html):
                    return (
                        f'<li class="grid__item" style="height:1200px"><div class="card-wrapper">'
                        f'<a href="/products/{slug}">{title}</a>'
                        f'<div class="price__container">{price_html}</div></div></li>'
                    )

                routes[store.catalogue] = (200, f"""<!doctype html>
                    <head><link rel="next" href="/collections/all?page=2"></head>
                    <main>
                      <ul class="product-grid">{card("expensive-tee", "Expensive Tee Shirt", "$45.00 CAD")}</ul>
                      <div class="pagination-wrapper" style="display:none"><a href="/collections/all?page=2">2</a></div>
                    </main>
                    <script>
                      const grid = document.querySelector('.product-grid');
                      const pag = document.querySelector('.pagination-wrapper');
                      let page = 1, loading = false, done = false;
                      async function probe(n) {{
                        const r = await fetch('/collections/all?page=' + n);
                        const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
                        return doc.querySelectorAll('.product-grid > .grid__item').length;
                      }}
                      async function append(n) {{
                        const r = await fetch('/collections/all?page=' + n);
                        const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
                        doc.querySelectorAll('.product-grid > .grid__item').forEach(el => grid.appendChild(el.cloneNode(true)));
                      }}
                      window.addEventListener('scroll', async () => {{
                        if (loading || done) return;
                        if (grid.getBoundingClientRect().bottom - window.innerHeight > 30) return;
                        loading = true;
                        const loader = document.createElement('div');
                        loader.className = 'custom-loader';
                        pag.appendChild(loader);
                        try {{
                          const count = await probe(page + 1);
                          if (count > 0) {{
                            page += 1;
                            await append(page);
                          }} else {{
                            done = true;
                          }}
                        }} finally {{
                          loader.remove();
                          loading = false;
                        }}
                      }});
                    </script>""")
                routes[store.catalogue + "?page=2"] = (
                    200,
                    f'<ul class="product-grid">{card("vintage-dark-wash-jeans", "Dark Wash Jeans", "$40.00 CAD")}</ul>',
                )
                routes[store.catalogue + "?page=3"] = (
                    200,
                    f'<ul class="product-grid">{card("untitled-feb1_12-56", "Vintage 90s Blue Tee Shirt", "<s>$38.00 CAD</s><span>$15.00 CAD</span>")}</ul>',
                )
                routes[store.catalogue + "?page=4"] = (200, '<ul class="product-grid"></ul>')
                events = self.search_events("wildrose", timeout=20)
        self.assert_last_listing_matched(events, store.origin + "/products/untitled-feb1_12-56", 2)
        self.assertEqual(requested, [
            "/robots.txt",
            store.catalogue,
            store.catalogue + "?page=2",
            store.catalogue + "?page=3",
            store.catalogue + "?page=4",
            "/products/untitled-feb1_12-56",
        ])

    @unittest.skipUnless(os.getenv("DEBOT_PLAYWRIGHT_TESTS") == "1", "Enable Firefox fixture tests.")
    def test_shopify_numbered_pagination_follows_next_when_scroll_does_not_fetch(self):
        routes = {}
        with serve_catalogue(routes) as (origin, requested):
            store = replace(retail.RETAILERS["la-penderie"], origin=origin)
            with mock.patch.dict(retail.RETAILERS, {"la-penderie": store}), \
                    mock.patch.dict("os.environ", {"DEBOT_AUTHORIZED_RETAIL_SOURCES": "wildrose,la-penderie"}), \
                    mock.patch.object(retail, "REQUEST_INTERVAL", .15):
                routes["/robots.txt"] = (200, "User-agent: *\nAllow: /")
                active_btn = (
                    '<button id="ProductSubmitButton-p" type="submit" name="add" class="btn product-form__submit">'
                    'Add to cart</button>'
                )
                _, html = make_product(
                    "la-penderie",
                    'Size: LargeChest: 21&quot;Length: 27&quot;',
                    extra_html=active_btn,
                    slug="page-two-t-shirt",
                    offers=[{"price": "9.0", "priceCurrency": "CAD", "availability": "http://schema.org/OutOfStock"}],
                )
                routes[store.product_prefix + "page-two-t-shirt"] = (200, html)
                routes[store.catalogue] = (
                    200,
                    f'<!doctype html><main><a href="{store.product_prefix}blue-jeans">Blue Jeans</a>'
                    f'<div class="pagination"><a href="{store.catalogue}?page=2">2</a></div></main>',
                )
                routes[store.catalogue + "?page=2"] = (
                    200,
                    f'<!doctype html><main><a href="{store.product_prefix}page-two-t-shirt">Graphic T-shirt</a></main>',
                )
                events = self.search_events("la-penderie", timeout=20)
        self.assert_last_listing_matched(events, store.origin + store.product_prefix + "page-two-t-shirt", 1)
        self.assertEqual(requested, [
            "/robots.txt",
            store.catalogue,
            store.catalogue + "?page=2",
            store.product_prefix + "page-two-t-shirt",
        ])

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
    def test_peoples_thrift_bottoms_walks_pants_and_shorts_collections(self):
        store = retail.RETAILERS["peoples-thrift"]
        pants_url, pants_body = make_product(
            "peoples-thrift", extra_html='<h2>SIZE &amp; FIT</h2><p>41" L, 32" inseam, 11.5" H rise, 17" W waist.</p>',
            slug="men-s-cotton-pants-34", name="Men's Cotton Pants (34)",
        )
        shorts_url, shorts_body = make_product(
            "peoples-thrift", extra_html='<h2>SIZE &amp; FIT</h2><p>16.5" W, 19.5" L, 9" inseam.</p>',
            slug="men-s-khaki-shorts-33", name="Men's Khaki Shorts (33)",
        )
        pants_base, shorts_base = store.bottoms_catalogues
        with mock.patch.object(retail, "_read", side_effect=[
            "User-agent: *\nAllow: /",
            f'<a href="{pants_base}?page=2">2</a><a href="/product-page/blue-t-shirt">T-Shirt</a>',
            f'<a href="{pants_url}">Cotton Pants</a>',
            f'<a href="{shorts_url}">Khaki Shorts</a>',
            pants_body,
            shorts_body,
        ]) as read:
            count, first, second = list(retail.search_catalogue("peoples-thrift", 10, None, category="bottoms"))
        self.assertEqual(count, 2)
        self.assertEqual((first[0], first[1]["waist"], first[1]["inseam"], first[1]["rise"]), (pants_url, 34.0, 32.0, 11.5))
        self.assertEqual((second[0], second[1]["waist"], second[1]["inseam"]), (shorts_url, 33.0, 9.0))
        self.assertEqual(
            [call.args[2] for call in read.call_args_list[1:4]],
            [pants_base, f"{pants_base}?page=2", shorts_base],
        )

    def test_retail_source_is_validated(self):
        validated = validate_search_payload({
            "retailSource": "peoples-thrift",
            "category": "bottoms",
            "bottomsMeasurements": {"waist": {"min": 32, "max": 36}, "inseam": {"min": 31, "max": 33}},
        })
        self.assertEqual((validated["retailSource"], validated["category"]), ("peoples-thrift", "bottoms"))
        for payload in (
            {"retailSource": "https://example.com"},
            {"retailSource": "peoples-thrift", "seller": "sample"},
            {"retailSource": "peoples-thrift", "category": "footwear"},
        ):
            with self.subTest(payload=payload), self.assertRaises(HTTPException):
                validate_search_payload(payload)

    def test_retail_bottoms_job_filters_by_measurements_and_price(self):
        rows = [
            ("wildrose-fit", {"title": "Vintage Jeans", "waist": 34.0, "inseam": 32.0, "rise": None, "legOpening": None, "price": 35.0}),
            ("full-fit", {"title": "Cotton Pants", "waist": 34.0, "inseam": 32.0, "rise": 11.5, "legOpening": None, "price": 24.0}),
            ("low-rise", {"title": "Low Rise Pants", "waist": 34.0, "inseam": 32.0, "rise": 10.0, "legOpening": None, "price": 24.0}),
            ("too-expensive", {"title": "Pricey Jeans", "waist": 34.0, "inseam": 32.0, "rise": 11.5, "legOpening": None, "price": 46.0}),
            ("unmeasured", None),
        ]

        def catalogue(*_):
            yield len(rows)
            for slug, product in rows:
                url = f"https://www.peoplesthrift.ca/product-page/{slug}"
                yield url, {**product, "url": url} if product else None

        payload = {
            "searchId": uuid4().hex, "retailSource": "peoples-thrift", "category": "bottoms",
            "bottomsMeasurements": {
                "waist": {"min": 32, "max": 36},
                "inseam": {"min": 31, "max": 33},
                "rise": {"min": 11, "max": 13},
                "legOpening": {"min": 9.5, "max": 10.5},
            },
            "maxPriceCad": 45,
        }
        with mock.patch.object(main, "search_catalogue", catalogue):
            job = main._ensure_search_job(payload)
            until = time.monotonic() + 2
            while not job.complete and time.monotonic() < until:
                time.sleep(.01)
            self.assertTrue(job.complete)
        events = [json.loads(event.removeprefix(b"data: ")) for event in job.read_from(0)[0]]
        matches = [event["item"] for event in events if event["type"] == "match"]
        self.assertEqual(
            [(item["title"], item["waist"], item["inseam"], item["rise"], item["inseamRise"], item["price"]) for item in matches],
            [
                ("Vintage Jeans", 34.0, 32.0, None, None, "$35.00 CAD"),
                ("Cotton Pants", 34.0, 32.0, 11.5, 43.5, "$24.00 CAD"),
            ],
        )
        self.assertEqual((events[-1]["type"], events[-1]["processed"], events[-1]["matches"]), ("done", 5, 2))

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
