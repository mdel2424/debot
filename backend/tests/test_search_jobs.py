import sys
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEPENDENCY_IMPORT_ERROR = None

try:
    import main  # noqa: E402
    from search_jobs import SearchJob, SearchJobRegistry  # noqa: E402
except Exception as exc:  # pragma: no cover
    DEPENDENCY_IMPORT_ERROR = exc


class FakeDisconnectedRequest:
    def __init__(self):
        self.disconnect_checks = 0

    async def is_disconnected(self):
        self.disconnect_checks += 1
        return True


class FakeJsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


@unittest.skipIf(
    DEPENDENCY_IMPORT_ERROR is not None,
    f"Search job tests require backend dependencies: {DEPENDENCY_IMPORT_ERROR}",
)
class SearchJobTest(unittest.TestCase):
    def test_registry_reuses_job_and_keeps_payload_snapshot(self):
        registry = SearchJobRegistry(max_jobs=2)
        payload = {"searchId": "job-1", "seller": "first"}

        job, created = registry.get_or_create("job-1", payload)
        payload["seller"] = "changed"
        same_job, created_again = registry.get_or_create("job-1", payload)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertIs(same_job, job)
        self.assertEqual(job.payload["seller"], "first")

    def test_job_replays_events_after_completion(self):
        job = SearchJob("job-1", {})
        job.publish(b"first")
        job.publish(b"second")
        job.finish()

        events, complete = job.read_from(0)
        tail, tail_complete = job.read_from(1)

        self.assertEqual(events, [b"first", b"second"])
        self.assertTrue(complete)
        self.assertEqual(tail, [b"second"])
        self.assertTrue(tail_complete)

    def test_registry_prunes_old_completed_jobs(self):
        registry = SearchJobRegistry(max_jobs=1)
        old_job, _ = registry.get_or_create("old", {})
        old_job.finish()

        new_job, created = registry.get_or_create("new", {})

        self.assertTrue(created)
        self.assertIsNone(registry.get("old"))
        self.assertIs(registry.get("new"), new_job)

    def test_ensure_search_job_starts_only_once_for_reconnections(self):
        registry = SearchJobRegistry(max_jobs=2)
        payload = {
            "searchId": "persistent-1",
            "category": "tops",
            "seller": "seller-one",
        }

        try:
            with (
                mock.patch.object(main, "SEARCH_JOBS", registry),
                mock.patch.object(main.threading, "Thread") as thread_mock,
                mock.patch("builtins.print"),
            ):
                first = main._ensure_search_job(payload)
                second = main._ensure_search_job(payload)

            self.assertIs(first, second)
            self.assertEqual(thread_mock.call_count, 1)
            thread_mock.return_value.start.assert_called_once_with()
        finally:
            main.CANCEL_FLAGS.pop("persistent-1", None)


@unittest.skipIf(
    DEPENDENCY_IMPORT_ERROR is not None,
    f"Search stream tests require backend dependencies: {DEPENDENCY_IMPORT_ERROR}",
)
class SearchStreamReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_subscriber_disconnect_does_not_cancel_or_clear_job(self):
        request = FakeDisconnectedRequest()
        job = SearchJob("job-1", {})
        event = main._sse({"type": "match", "item": {"url": "one"}})
        job.publish(event)

        chunks = []
        async for chunk in main._stream_search_job(request, job):
            chunks.append(chunk)

        self.assertEqual(chunks, [main.SSE_PREAMBLE, event])
        self.assertEqual(request.disconnect_checks, 1)
        self.assertFalse(job.complete)
        self.assertEqual(job.read_from(0)[0], [event])

    async def test_batch_endpoint_registers_every_search(self):
        first = SearchJob("first", {})
        second = SearchJob("second", {})
        second.finish()
        request = FakeJsonRequest({
            "searches": [
                {"searchId": "first"},
                {"searchId": "second"},
            ],
        })

        with mock.patch(
            "main._ensure_search_job",
            side_effect=[first, second],
        ) as ensure_mock:
            result = await main.start_search_batch(request)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["jobs"],
            [
                {"searchId": "first", "complete": False},
                {"searchId": "second", "complete": True},
            ],
        )
        self.assertEqual(ensure_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
