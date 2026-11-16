"""Real browser tests with every request fulfilled by local fixtures."""

import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from playwright.sync_api import sync_playwright
import main
import scraper


@unittest.skipUnless(os.getenv('DEBOT_PLAYWRIGHT_TESTS') == '1', 'Set DEBOT_PLAYWRIGHT_TESTS=1 for browser fixtures.')
class ScraperPlaywrightTest(unittest.TestCase):
    api_url = 'https://webapi.depop.com/presentation/api/v1/shops/123/products/?limit=24'

    def setUp(self):
        from shop_cache import ShopCache
        cache = mock.patch.object(scraper, 'SHOP_CACHE', ShopCache(day_key=lambda: '2026-11-14'))
        cache.start()
        self.addCleanup(cache.stop)
        scraper._clear_listing_cache()
        self.pw = sync_playwright().start()
        self.browser, self.context = scraper.create_browser_context(self.pw)
        self.requests = []
        self.unexpected = []
        self.limit_once = False
        self.limited = False
        self.inventory = None
        self.changes = {}
        self.context.route('**/*', self.route)

    def tearDown(self):
        try:
            self.context.close()
        finally:
            self.browser.close()
            self.pw.stop()
            scraper._clear_listing_cache()

    def route(self, route):
        request = route.request
        url = urlparse(request.url)
        if url.hostname == 'www.depop.com' and url.path == '/fixture_shop/':
            self.requests.append('document')
            route.fulfill(content_type='text/html', body=f'''<!doctype html>
                <html><body><h1>Fixture Shop</h1><p>100 sold</p><div id="products"></div>
                <script>fetch({json.dumps(self.api_url)}).then(response => response.json()).then(data => {{
                    document.getElementById('products').innerHTML = data.objects.map(product =>
                        '<a href="/products/' + product.slug + '/">Product</a>').join('');
                }});</script></body></html>''')
        elif scraper._is_shop_product_api_url(request.url):
            after = parse_qs(url.query).get('after', [''])[0]
            self.requests.append(after or 'first-page')
            headers = {'Access-Control-Allow-Origin': '*'}
            if after and self.limit_once and not self.limited:
                self.limited = True
                route.fulfill(status=429, headers={**headers, 'Retry-After': '240'}, body='')
                return
            start = int(after.removeprefix('cursor-')) if after else 0
            indices = self.inventory[start:start + 24] if self.inventory is not None else range(start, start + 24)
            products = [{
                'slug': f'item-{index}', 'status': 'STATUS_ONSALE',
                'description': 'Pit2Pit: 21 inches. Length: 27 inches.',
                'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'sizes': [{'name': 'M'}],
                'preview': {'formats': {'P0': {'url': 'https://media-photos.depop.com/test.jpg'}}},
                'pricing': {'display_price': {'headline_price': {'amount': '25', 'currency': 'CAD'}}},
                **self.changes.get(index, {}),
            } for index in indices]
            route.fulfill(content_type='application/json', headers=headers, body=json.dumps({
                'objects': products, 'page_info': {
                    'has_more': self.inventory is None or start + 24 < len(self.inventory),
                    'last': f'cursor-{start + 24}',
                },
            }))
        else:
            self.unexpected.append(request.url)
            route.abort()

    def search(self, max_items, groups=None):
        emitted = []
        page = self.context.new_page()
        with (
            mock.patch('builtins.print'), mock.patch('main._sleep_request_jitter'),
            mock.patch('main._mark_recent_rate_limit') as mark,
            mock.patch('main.sleep_with_cancel') as sleep,
            mock.patch.object(scraper, 'REQUEST_INTERVAL_SECONDS', 0),
        ):
            chunks = list(main._search_seller(
                self.context, page, 'fixture_shop', groups or ['tops', 'coats-jackets'], 'male',
                21, 27, 0.75, 0.75, max_items, 1000, 1, 'fixture-search',
                emit_event=emitted.append,
            ))
        page.close()
        events = emitted + [json.loads(chunk.decode().removeprefix('data: ')) for chunk in chunks]
        return events, mark, sleep

    def test_early_matches_stop_pagination_and_repeat_uses_no_network(self):
        events, _, _ = self.search(5)
        self.assertEqual(len([event for event in events if event['type'] == 'match']), 5)
        self.assertEqual(events[-1]['stopReason'], 'match_limit')
        self.assertEqual(events[-1]['processed'], 5)
        self.assertEqual(self.requests, ['document', 'first-page'])
        self.requests.clear()
        repeated, _, _ = self.search(5)
        self.assertEqual(repeated[-1]['matches'], 5)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.unexpected, [])

    def test_429_retries_the_cursor_without_reloading_or_duplicate_matches(self):
        self.limit_once = True
        events, mark, sleep = self.search(40)
        matches = [event['item']['url'] for event in events if event['type'] == 'match']
        self.assertEqual(len(matches), 40)
        self.assertEqual(len(set(matches)), 40)
        self.assertEqual(events[-1]['processed'], 40)
        self.assertEqual(self.requests, ['document', 'first-page', 'cursor-24', 'cursor-24'])
        mark.assert_called_once_with(240)
        sleep.assert_called_once()
        self.assertEqual(sleep.call_args.args[0], 240)
        self.assertEqual(self.unexpected, [])

    def test_daily_refresh_fetches_only_new_prefix_and_reuses_the_old_tail(self):
        now = [time.time()]
        self.inventory = list(range(120))
        with mock.patch.object(scraper.SHOP_CACHE, 'clock', lambda: now[0]):
            initial, _, _ = self.search(1000, ['tops'])
            self.assertEqual(initial[-1]['matches'], 120)
            self.assertEqual(self.requests, ['document', 'first-page', 'cursor-24', 'cursor-48', 'cursor-72', 'cursor-96'])
            self.requests.clear()
            now[0] += 301
            self.inventory.insert(0, 2000)
            self.changes = {
                0: {'sold': True},
                1: {'pricing': {'display_price': {'headline_price': {'amount': '99', 'currency': 'CAD'}}}},
                2: {'description': 'Pit2Pit: 30 inches. Length: 35 inches.'},
            }
            refreshed, _, _ = self.search(1000, ['tops'])
            matches = {event['item']['url']: event['item'] for event in refreshed if event['type'] == 'match'}
            removed = {event['url'] for event in refreshed if event['type'] == 'remove'}
            url = lambda index: f'https://www.depop.com/products/item-{index}/'
            self.assertEqual(len(matches), 119)
            self.assertIn(url(2000), matches)
            self.assertIn(url(119), matches)
            self.assertEqual(matches[url(1)]['price'], '$99.00')
            self.assertNotIn(url(0), matches)
            self.assertNotIn(url(2), matches)
            self.assertTrue({url(0), url(2)} <= removed)
            self.assertEqual(self.requests, ['document', 'first-page', 'cursor-24', 'cursor-48'])
            self.requests.clear()
            repeated, _, _ = self.search(1000, ['tops'])
            self.assertEqual(repeated[-1]['matches'], 119)
            self.assertEqual(self.requests, [])
            self.assertEqual(self.unexpected, [])


if __name__ == '__main__':
    unittest.main()
