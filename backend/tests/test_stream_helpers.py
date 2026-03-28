import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEPENDENCY_IMPORT_ERROR = None

try:
    from main import (  # noqa: E402
        _browse_all,
        _error_payload_for_exception,
        _process_item,
        _run_with_rate_limit_retries,
        _search_seller,
        _sse,
    )
    from scraper import RateLimitError, SearchCancelled, sleep_with_cancel  # noqa: E402
except Exception as exc:  # pragma: no cover - protects VS Code discovery on wrong interpreter
    DEPENDENCY_IMPORT_ERROR = exc


class FakePage:
    def __init__(self):
        self.waits = []
        self.closed = False

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.new_page_calls = 0
        self.pages = []

    def new_page(self):
        self.new_page_calls += 1
        page = FakePage()
        self.pages.append(page)
        return page


@unittest.skipIf(
    DEPENDENCY_IMPORT_ERROR is not None,
    f"Stream helper tests require backend dependencies: {DEPENDENCY_IMPORT_ERROR}",
)
class StreamHelpersTest(unittest.TestCase):
    @staticmethod
    def _decode_events(events):
        return [json.loads(chunk.decode("utf-8").split("data: ", 1)[1]) for chunk in events]

    def test_sleep_with_cancel_raises_search_cancelled(self):
        checks = {"count": 0}

        def should_cancel():
            checks["count"] += 1
            return checks["count"] >= 2

        with self.assertRaises(SearchCancelled):
            sleep_with_cancel(0.02, should_cancel=should_cancel, interval_seconds=0.01)

    def test_run_with_rate_limit_retries_uses_expected_backoff(self):
        attempts = []
        delays = []
        retry_events = []

        def action():
            attempts.append("try")
            raise RateLimitError("Depop returned HTTP 429 Too Many Requests.")

        with patch("builtins.print"), patch("main.sleep_with_cancel", side_effect=lambda delay, should_cancel=None: delays.append(delay)):
            with self.assertRaises(RateLimitError) as ctx:
                _run_with_rate_limit_retries(
                    action,
                    lambda: False,
                    "listing page",
                    on_rate_limit=lambda attempt, total_attempts, delay, exc, label: retry_events.append(
                        (attempt, total_attempts, delay, label)
                    ),
                )

        self.assertEqual(len(attempts), 4)
        self.assertEqual(delays, [15, 30, 60])
        self.assertEqual(
            retry_events,
            [(1, 3, 15, "listing page"), (2, 3, 30, "listing page"), (3, 3, 60, "listing page")],
        )
        self.assertEqual(
            str(ctx.exception),
            "Rate limited after 3 cooldown attempts while listing page.",
        )

    def test_rate_limit_error_payload_is_encoded_as_sse_error(self):
        payload = _error_payload_for_exception(
            RateLimitError("Depop appears to be rate limiting requests right now."),
            "search-123",
        )

        encoded = _sse(payload).decode("utf-8").strip()
        event = json.loads(encoded.split("data: ", 1)[1])

        self.assertEqual(event["type"], "error")
        self.assertEqual(event["code"], "rate_limited")
        self.assertEqual(event["searchId"], "search-123")

    def test_browse_all_reuses_single_item_page(self):
        ctx = FakeContext()
        browse_page = FakePage()
        first_batch = [f"u{i}" for i in range(1, 31)]
        second_batch = [f"u{i}" for i in range(1, 61)]
        third_batch = [f"u{i}" for i in range(1, 91)]
        fourth_batch = [f"u{i}" for i in range(1, 121)]
        parse_calls = []

        def fake_parse(page, url, should_cancel=None):
            parse_calls.append(url)
            return {"seller": f"seller-{url}", "url": url}

        with (
            patch("builtins.print"),
            patch("main._load_page_with_retries"),
            patch(
                "main.collect_listing_links",
                side_effect=[first_batch, second_batch, third_batch, fourth_batch],
            ) as collect_mock,
            patch("main.parse_listing", side_effect=fake_parse),
            patch("main._process_item", side_effect=lambda item, *args: dict(item)),
            patch("main._resolve_seller_sold_count", return_value=99),
        ):
            events = list(
                _browse_all(
                    ctx,
                    browse_page,
                    "tops",
                    "male",
                    21.5,
                    27.0,
                    0.5,
                    0.75,
                    max_items=100,
                    max_links=100,
                    max_scrolls=4,
                    search_id="search-123",
                )
            )

        decoded = self._decode_events(events)
        self.assertEqual(ctx.new_page_calls, 1)
        self.assertTrue(ctx.pages[0].closed)
        self.assertEqual(browse_page.waits, [500])
        self.assertEqual(collect_mock.call_count, 4)
        self.assertEqual(decoded[-1]["type"], "done")
        self.assertEqual(sum(evt["type"] == "match" for evt in decoded), 100)
        self.assertEqual(len(parse_calls), 100)
        self.assertEqual(parse_calls[-1], "u100")

        progress_events = [evt for evt in decoded if evt["type"] == "progress"]
        self.assertEqual(progress_events[0]["total"], 0)
        self.assertEqual(progress_events[-1]["processed"], 99)
        self.assertEqual(progress_events[-1]["total"], 120)

    def test_browse_all_respects_requested_max_links_when_no_matches(self):
        ctx = FakeContext()
        browse_page = FakePage()
        first_batch = [f"u{i}" for i in range(1, 601)]
        second_batch = [f"u{i}" for i in range(1, 1201)]
        third_batch = [f"u{i}" for i in range(1, 1801)]
        parse_calls = []

        def fake_parse(page, url, should_cancel=None):
            parse_calls.append(url)
            return {"seller": f"seller-{url}", "url": url}

        with (
            patch("builtins.print"),
            patch("main._load_page_with_retries"),
            patch(
                "main.collect_listing_links",
                side_effect=[first_batch, second_batch, third_batch],
            ) as collect_mock,
            patch("main.parse_listing", side_effect=fake_parse),
            patch("main._process_item", return_value=None),
            patch("main._resolve_seller_sold_count", return_value=99),
        ):
            events = list(
                _browse_all(
                    ctx,
                    browse_page,
                    "tops",
                    "male",
                    21.5,
                    27.0,
                    0.5,
                    0.75,
                    max_items=100,
                    max_links=1_500,
                    max_scrolls=4,
                    search_id="search-456",
                )
            )

        decoded = self._decode_events(events)
        progress_events = [evt for evt in decoded if evt["type"] == "progress"]

        self.assertEqual(collect_mock.call_count, 3)
        self.assertEqual(len(parse_calls), 1_500)
        self.assertEqual(decoded[-1]["type"], "done")
        self.assertEqual(progress_events[-1]["processed"], 1_500)
        self.assertEqual(progress_events[-1]["total"], 1_800)
        self.assertEqual(sum(evt["type"] == "match" for evt in decoded), 0)

    def test_browse_all_stops_after_repeated_no_growth_batches(self):
        ctx = FakeContext()
        browse_page = FakePage()
        repeated_batch = [f"u{i}" for i in range(1, 49)]
        parse_calls = []

        def fake_parse(page, url, should_cancel=None):
            parse_calls.append(url)
            return {"seller": f"seller-{url}", "url": url}

        with (
            patch("builtins.print"),
            patch("main._load_page_with_retries"),
            patch(
                "main.collect_listing_links",
                side_effect=[repeated_batch, repeated_batch, repeated_batch, repeated_batch],
            ) as collect_mock,
            patch("main.parse_listing", side_effect=fake_parse),
            patch("main._process_item", return_value=None),
            patch("main._resolve_seller_sold_count", return_value=99),
        ):
            events = list(
                _browse_all(
                    ctx,
                    browse_page,
                    "tops",
                    "male",
                    21.5,
                    27.0,
                    0.5,
                    0.75,
                    max_items=100,
                    max_links=200,
                    max_scrolls=4,
                    search_id="search-789",
                )
            )

        decoded = self._decode_events(events)
        progress_events = [evt for evt in decoded if evt["type"] == "progress"]

        self.assertEqual(collect_mock.call_count, 4)
        self.assertEqual(len(parse_calls), 48)
        self.assertEqual(decoded[-1]["type"], "done")
        self.assertEqual(progress_events[-1]["processed"], 48)
        self.assertEqual(progress_events[-1]["total"], 48)

    def test_search_seller_aggregates_multiple_groups_into_one_stream(self):
        ctx = FakeContext()
        page = FakePage()
        tops_links = ['tops-1', 'shared-2']
        coats_links = ['shared-2', 'coats-3']
        parse_calls = []

        def fake_parse(current_page, url, should_cancel=None):
            parse_calls.append(url)
            return {'seller': 'drewzal', 'url': url}

        with (
            patch('builtins.print'),
            patch('main._load_page_with_retries') as load_mock,
            patch('main.extract_seller_sold_count', return_value=88),
            patch('main.remove_sold_sections'),
            patch('main.collect_listing_links', side_effect=[tops_links, coats_links]) as collect_mock,
            patch('main.parse_listing', side_effect=fake_parse),
            patch(
                'main._process_item',
                side_effect=lambda item, *args: {**item, 'p2p': 23.0, 'length': 28.0},
            ),
        ):
            events = list(
                _search_seller(
                    ctx,
                    page,
                    'drewzal',
                    ['tops', 'coats-jackets'],
                    'male',
                    21.5,
                    27.25,
                    0.5,
                    1.25,
                    max_items=40,
                    max_links=100,
                    max_scrolls=4,
                    search_id='search-multi-group',
                )
            )

        decoded = self._decode_events(events)
        match_events = [evt for evt in decoded if evt['type'] == 'match']
        progress_events = [evt for evt in decoded if evt['type'] == 'progress']
        meta_event = next(evt for evt in decoded if evt['type'] == 'meta')

        self.assertEqual(collect_mock.call_count, 2)
        self.assertEqual(meta_event['links'], 3)
        self.assertEqual(len(match_events), 3)
        self.assertEqual(parse_calls, ['tops-1', 'shared-2', 'coats-3'])
        self.assertTrue(all(evt['item']['soldCount'] == 88 for evt in match_events))
        self.assertEqual(progress_events[-1]['processed'], 3)
        self.assertEqual(progress_events[-1]['total'], 3)
        self.assertEqual(load_mock.call_count, 2)
        self.assertTrue(all(call.kwargs.get('aggressive_end_scroll') for call in collect_mock.call_args_list))

    def test_search_seller_collect_listing_rate_limit_emits_cooldown_and_recovers(self):
        ctx = FakeContext()
        page = FakePage()
        emitted_progress = []

        with (
            patch('builtins.print'),
            patch('main._load_page_with_retries'),
            patch('main.extract_seller_sold_count', return_value=88),
            patch('main.remove_sold_sections'),
            patch(
                'main.collect_listing_links',
                side_effect=[RateLimitError("Depop appears to be rate limiting requests right now."), ['tops-1']],
            ),
            patch('main.sleep_with_cancel'),
            patch('main.parse_listing', return_value={'seller': 'drewzal', 'url': 'tops-1'}),
            patch(
                'main._process_item',
                return_value={'seller': 'drewzal', 'url': 'tops-1', 'p2p': 22.0, 'length': 27.0},
            ),
        ):
            events = list(
                _search_seller(
                    ctx,
                    page,
                    'drewzal',
                    ['tops'],
                    'male',
                    21.5,
                    27.25,
                    0.5,
                    1.25,
                    max_items=40,
                    max_links=100,
                    max_scrolls=4,
                    search_id='search-rate-limit',
                    emit_event=emitted_progress.append,
                )
            )

        decoded = self._decode_events(events)
        rate_limited_event = next(evt for evt in emitted_progress if evt['phase'] == 'rate_limited')
        match_events = [evt for evt in decoded if evt['type'] == 'match']

        self.assertEqual(rate_limited_event['retryAttempt'], 1)
        self.assertEqual(rate_limited_event['retryTotalAttempts'], 3)
        self.assertEqual(rate_limited_event['retryDelaySeconds'], 15)
        self.assertEqual(rate_limited_event['message'], 'Paused while collecting listings.')
        self.assertEqual(len(match_events), 1)
        self.assertEqual(match_events[0]['item']['url'], 'tops-1')
        self.assertEqual(decoded[-1]['type'], 'done')

    def test_browse_all_collect_listing_rate_limit_emits_cooldown_and_recovers(self):
        ctx = FakeContext()
        browse_page = FakePage()
        emitted_progress = []

        with (
            patch('builtins.print'),
            patch('main._load_page_with_retries'),
            patch(
                'main.collect_listing_links',
                side_effect=[RateLimitError("Depop appears to be rate limiting requests right now."), ['u1']],
            ),
            patch('main.sleep_with_cancel'),
            patch('main.parse_listing', return_value={'seller': 'seller-u1', 'url': 'u1'}),
            patch('main._process_item', return_value={'seller': 'seller-u1', 'url': 'u1'}),
            patch('main._resolve_seller_sold_count', return_value=99),
        ):
            events = list(
                _browse_all(
                    ctx,
                    browse_page,
                    'tops',
                    'male',
                    21.5,
                    27.0,
                    0.5,
                    0.75,
                    max_items=1,
                    max_links=10,
                    max_scrolls=4,
                    search_id='browse-rate-limit',
                    emit_event=emitted_progress.append,
                )
            )

        decoded = self._decode_events(events)
        rate_limited_event = next(evt for evt in emitted_progress if evt['phase'] == 'rate_limited')
        match_events = [evt for evt in decoded if evt['type'] == 'match']

        self.assertEqual(rate_limited_event['retryAttempt'], 1)
        self.assertEqual(rate_limited_event['retryDelaySeconds'], 15)
        self.assertEqual(rate_limited_event['message'], 'Paused while collecting listings.')
        self.assertEqual(len(match_events), 1)
        self.assertEqual(match_events[0]['item']['soldCount'], 99)
        self.assertEqual(decoded[-1]['type'], 'done')

    def test_process_item_matches_bottoms_by_size_range(self):
        item = {
            "url": "https://www.depop.com/products/example-bottoms/",
            "image": "https://example.com/image.jpg",
            "price": "$60.00",
            "description": "Vintage jeans",
            "sizeLabel": '34"',
            "seller": "seller-one",
            "ageDays": 2.5,
            "listedAt": "2026-03-28T00:00:00+00:00",
        }

        match = _process_item(
            item,
            None,
            None,
            0.5,
            1.25,
            "bottoms",
            {"min": 30, "max": 34, "system": "WAIST"},
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["sizeLabel"], '34"')
        self.assertIsNone(match["p2p"])
        self.assertIsNone(match["length"])

    def test_process_item_matches_footwear_by_size_range(self):
        item = {
            "url": "https://www.depop.com/products/example-shoes/",
            "image": "https://example.com/shoe.jpg",
            "price": "$110.00",
            "description": "Leather loafers",
            "sizeLabel": "US 10.5",
            "seller": "seller-two",
        }

        match = _process_item(
            item,
            None,
            None,
            0.5,
            1.25,
            "footwear",
            {"min": 10, "max": 11, "system": "US"},
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["sizeLabel"], "US 10.5")

    def test_process_item_allows_all_accessories(self):
        item = {
            "url": "https://www.depop.com/products/example-bag/",
            "image": None,
            "price": "$48.00",
            "description": "Vintage bag",
            "seller": "seller-three",
        }

        match = _process_item(
            item,
            None,
            None,
            0.5,
            1.25,
            "accessories",
            None,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["url"], item["url"])


if __name__ == "__main__":
    unittest.main()
