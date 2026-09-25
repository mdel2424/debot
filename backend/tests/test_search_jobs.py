import sys
import unittest
import threading
import json
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
from search_jobs import SearchJob, SearchJobRegistry  # noqa: E402


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


class SearchJobTest(unittest.TestCase):
    def test_invalid_request_does_not_leave_an_unfinishable_job(self):
        registry = SearchJobRegistry()
        with mock.patch.object(main, 'SEARCH_JOBS', registry):
            with self.assertRaises(main.HTTPException) as caught:
                main._ensure_search_job({'searchId': 'invalid', 'maxLinks': -1})
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIsNone(registry.get('invalid'))

    def test_reconnect_uses_saved_payload_without_reparsing_changed_filters(self):
        registry = SearchJobRegistry()
        job, _ = registry.get_or_create('existing', {'seller': 'original'})
        with mock.patch.object(main, 'SEARCH_JOBS', registry), mock.patch.object(main.threading, 'Thread') as thread:
            result = main._ensure_search_job({'searchId': 'existing', 'maxLinks': 'invalid'})
        self.assertIs(result, job)
        thread.assert_not_called()

    def test_queued_job_cancels_without_waiting_for_running_search(self):
        registry = SearchJobRegistry()
        slots = threading.Semaphore(0)
        threads = []
        real_thread = threading.Thread

        def thread_factory(**kwargs):
            thread = real_thread(**kwargs)
            threads.append(thread)
            return thread

        with (
            mock.patch.object(main, 'SEARCH_JOBS', registry),
            mock.patch.object(main, 'SEARCH_JOB_SLOTS', slots),
            mock.patch.object(main.threading, 'Thread', side_effect=thread_factory),
            mock.patch.object(main, 'sync_playwright') as playwright,
            mock.patch('builtins.print'),
        ):
            job = main._ensure_search_job({'searchId': 'queued-cancel'})
            main.CANCEL_FLAGS['queued-cancel'] = True
            threads[0].join(2)
            self.assertFalse(threads[0].is_alive())
            self.assertTrue(job.complete)
            events = [json.loads(chunk.decode().split('data: ', 1)[1]) for chunk in job.read_from(0)[0]]
            self.assertEqual(events[-1]['type'], 'cancelled')
            playwright.assert_not_called()

    def test_zero_tolerance_and_finite_request_bounds(self):
        from search_models import validate_search_payload
        payload = validate_search_payload({'p2pTolerance': 0, 'lengthTolerance': 0})
        self.assertEqual(payload['p2pTolerance'], 0)
        for invalid in ({'p2pTolerance': float('nan')}, {'measurements': {'first': float('inf')}}, {'seller': 'x/../../products'}):
            with self.subTest(invalid=invalid), self.assertRaises(main.HTTPException):
                validate_search_payload(invalid)

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


class SearchStreamReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_batch_validation_is_atomic(self):
        request = FakeJsonRequest({'searches': [{'seller': 'valid'}, {'maxLinks': -1}]})
        with mock.patch.object(main, '_ensure_search_job') as ensure:
            with self.assertRaises(main.HTTPException):
                await main.start_search_batch(request)
        ensure.assert_not_called()

    async def test_completed_job_replays_after_disconnect_with_separate_sse_preamble(self):
        job = SearchJob('replay', {})
        first = main._sse({'type': 'match', 'item': {'url': 'one'}})
        job.publish(first)
        detached = [chunk async for chunk in main._stream_search_job(FakeDisconnectedRequest(), job)]
        self.assertEqual(detached[1:], [first])
        self.assertFalse(job.complete)
        second = main._sse({'type': 'match', 'item': {'url': 'two'}})
        done = main._sse({'type': 'done'})
        job.publish(second)
        job.publish(done)
        job.finish()
        chunks = [chunk async for chunk in main._stream_search_job(FakeDisconnectedRequest(), job)]
        self.assertTrue(main.SSE_PREAMBLE.endswith(b'\n\n'))
        self.assertEqual(chunks[1:], [first, second, done])

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
