import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEPENDENCY_IMPORT_ERROR = None

try:
    from listing_pool import ParallelListingParser  # noqa: E402
except Exception as exc:  # pragma: no cover
    DEPENDENCY_IMPORT_ERROR = exc


@unittest.skipIf(
    DEPENDENCY_IMPORT_ERROR is not None,
    f"Parallel parser tests require backend dependencies: {DEPENDENCY_IMPORT_ERROR}",
)
class ParallelListingParserTest(unittest.TestCase):
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
