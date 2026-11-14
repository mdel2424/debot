import sys
from pathlib import Path
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import benchmark_scraper
import main
from scraper import SearchCancelled


class BenchmarkScraperTest(unittest.TestCase):
    def test_first_blocked_response_stops_the_probe_and_further_runs(self):
        callbacks = {}
        context = mock.Mock()
        context.on.side_effect = lambda name, callback: callbacks.update({name: callback})
        browser = mock.Mock()
        request = mock.Mock(
            url='https://webapi.depop.com/presentation/api/v1/shops/123/products/',
            resource_type='fetch',
        )
        response = mock.Mock(url=request.url, request=request, status=429)
        response.header_value.return_value = '900'

        def blocked_search(*args, **kwargs):
            callbacks['request'](request)
            callbacks['response'](response)
            self.assertTrue(main._is_cancelled(args[-1]))
            raise SearchCancelled()

        with (
            mock.patch.object(benchmark_scraper, 'sync_playwright'),
            mock.patch.object(benchmark_scraper.scraper, 'create_browser_context', return_value=(browser, context)),
            mock.patch.object(main, '_search_seller', side_effect=blocked_search) as search,
        ):
            reports = benchmark_scraper.benchmark('fixture', runs=3)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['outcome'], 'rate_limited')
        self.assertEqual(reports[0]['blocked'][0]['retry_after_seconds'], 900)
        self.assertEqual(reports[0]['data_requests'], 1)
        self.assertNotIn('benchmark-fixture-1', main.CANCEL_FLAGS)
        search.assert_called_once()
        context.close.assert_called_once()
        browser.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
