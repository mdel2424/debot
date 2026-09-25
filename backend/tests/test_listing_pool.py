import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from listing_pool import ParallelListingParser  # noqa: E402


class ParallelListingParserTest(unittest.TestCase):
    def test_slow_first_result_bounds_prefetch_and_closing_cancels_inflight_waits(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_cancelled = threading.Event()
        parsed = []
        lock = threading.Lock()

        def parse_job(page_getter, url, should_cancel, reset_session):
            with lock:
                parsed.append(url)
            if url == 'first':
                first_started.set()
                self.assertTrue(release_first.wait(2))
            else:
                second_started.set()
                deadline = time.monotonic() + 2
                while not should_cancel() and time.monotonic() < deadline:
                    time.sleep(0.005)
                if should_cancel():
                    second_cancelled.set()
            return {'url': url}

        with (
            mock.patch('listing_pool.sync_playwright'),
            mock.patch('listing_pool.create_browser_context', side_effect=lambda *a, **k: (mock.Mock(), mock.Mock())),
        ):
            with ParallelListingParser(2, parse_job, lambda: False) as pool:
                iterator = pool.map_ordered(['first', 'second', 'must-not-start', 'nor-this'])
                output = []
                consumer = threading.Thread(target=lambda: output.append(next(iterator)))
                consumer.start()
                try:
                    self.assertTrue(first_started.wait(2))
                    self.assertTrue(second_started.wait(2))
                    self.assertEqual(set(parsed), {'first', 'second'})
                    release_first.set()
                    consumer.join(2)
                    self.assertFalse(consumer.is_alive())
                    iterator.close()
                    self.assertTrue(second_cancelled.is_set())
                    self.assertEqual(set(parsed), {'first', 'second'})
                finally:
                    release_first.set()
                    consumer.join(2)

    def test_fast_later_results_do_not_submit_the_whole_shop(self):
        first_started = threading.Event()
        release_first = threading.Event()
        later_finished = threading.Event()
        parsed = []

        def parse_job(page_getter, url, should_cancel, reset_session):
            parsed.append(url)
            if url == 'first':
                first_started.set()
                release_first.wait(2)
            else:
                later_finished.set()
            return {'url': url}

        with (
            mock.patch('listing_pool.sync_playwright'),
            mock.patch('listing_pool.create_browser_context', side_effect=lambda *a, **k: (mock.Mock(), mock.Mock())),
        ):
            with ParallelListingParser(2, parse_job, lambda: False) as pool:
                iterator = pool.map_ordered(['first', 'second', 'third', 'fourth'])
                consumer = threading.Thread(target=lambda: next(iterator))
                consumer.start()
                try:
                    self.assertTrue(first_started.wait(2))
                    self.assertTrue(later_finished.wait(2))
                    time.sleep(0.03)
                    self.assertEqual(set(parsed), {'first', 'second'})
                finally:
                    release_first.set()
                    consumer.join(2)
                    iterator.close()

    def test_worker_startup_failure_closes_successful_workers(self):
        browser, context = mock.Mock(), mock.Mock()
        with (
            mock.patch('listing_pool.sync_playwright'),
            mock.patch('listing_pool.create_browser_context', side_effect=[(browser, context), RuntimeError('launch failed')]),
        ):
            pool = ParallelListingParser(2, mock.Mock(), lambda: False)
            with self.assertRaisesRegex(RuntimeError, 'Unable to start'):
                pool.start()
        browser.close.assert_called_once()
        self.assertFalse(pool._threads)

    def test_runs_workers_concurrently_and_yields_results_in_input_order(self):
        barrier = threading.Barrier(3)
        activity_lock = threading.Lock()
        activity = {"current": 0, "maximum": 0}
        playwright_handles = []

        def manager_factory():
            manager = mock.Mock()
            playwright = mock.Mock()
            manager.start.return_value = playwright
            playwright_handles.append(playwright)
            return manager

        def context_factory(*args, **kwargs):
            del args, kwargs
            browser = mock.Mock()
            context = mock.Mock()
            context.new_page.return_value = mock.Mock()
            return browser, context

        def parse_job(page_getter, url, should_cancel, reset_session):
            del page_getter, should_cancel, reset_session
            with activity_lock:
                activity["current"] += 1
                activity["maximum"] = max(
                    activity["maximum"],
                    activity["current"],
                )
            try:
                barrier.wait(timeout=2)
                if url == "first":
                    time.sleep(0.03)
                elif url == "second":
                    time.sleep(0.01)
                return {"url": url}
            finally:
                with activity_lock:
                    activity["current"] -= 1

        with (
            mock.patch(
                "listing_pool.sync_playwright",
                side_effect=manager_factory,
            ),
            mock.patch(
                "listing_pool.create_browser_context",
                side_effect=context_factory,
            ) as create_context,
        ):
            with ParallelListingParser(
                3,
                parse_job,
                lambda: False,
                storage_state={"cookies": [{"name": "session"}]},
            ) as parser_pool:
                results = list(
                    parser_pool.map_ordered(["first", "second", "third"])
                )

        self.assertEqual(
            results,
            [
                ("first", {"url": "first"}),
                ("second", {"url": "second"}),
                ("third", {"url": "third"}),
            ],
        )
        self.assertEqual(activity["maximum"], 3)
        self.assertEqual(create_context.call_count, 3)
        self.assertTrue(
            all(
                call.kwargs["storage_state"] == {
                    "cookies": [{"name": "session"}],
                }
                for call in create_context.call_args_list
            )
        )
        self.assertEqual(len(playwright_handles), 3)
        self.assertTrue(
            all(handle.stop.called for handle in playwright_handles)
        )


if __name__ == "__main__":
    unittest.main()
