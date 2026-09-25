"""Thread-owned Playwright workers for ordered parallel listing parsing."""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from playwright.sync_api import sync_playwright

from scraper import SearchCancelled, create_browser_context


CancelCheck = Callable[[], bool]
ResetSession = Callable[[], None]
PageGetter = Callable[[], Any]
ParseJob = Callable[
    [PageGetter, str, CancelCheck, ResetSession],
    Optional[Dict[str, Any]],
]
ParseResult = Tuple[int, int, str, Optional[Dict[str, Any]], Optional[Exception]]


class ParallelListingParser:
    """Parse listing URLs concurrently while yielding results in input order."""

    _STOP = object()

    def __init__(
        self,
        worker_count: int,
        parse_job: ParseJob,
        should_cancel: CancelCheck,
        *,
        headless: bool = True,
        slowmo: int = 0,
        storage_state: Optional[Dict[str, Any]] = None,
        prefetch_per_worker: int = 1,
    ):
        self.worker_count = max(int(worker_count or 1), 1)
        self.parse_job = parse_job
        self.should_cancel = should_cancel
        self.headless = headless
        self.slowmo = slowmo
        self.storage_state = storage_state
        self.prefetch_per_worker = max(int(prefetch_per_worker or 1), 1)

        self._jobs: queue.Queue[Any] = queue.Queue()
        self._results: queue.Queue[ParseResult] = queue.Queue()
        self._ready: queue.Queue[Tuple[bool, Optional[Exception]]] = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._stop_event = threading.Event()
        self._batch_lock = threading.Lock()
        self._cancelled_batches: set[int] = set()
        self._result_backlog: Dict[int, List[ParseResult]] = {}
        self._next_batch_id = 0
        self._started = False

    def start(self) -> "ParallelListingParser":
        if self._started:
            return self

        if self._stop_event.is_set():
            raise RuntimeError("A closed listing parser cannot be restarted.")
        self._started = True
        for worker_index in range(self.worker_count):
            thread = threading.Thread(
                target=self._worker,
                args=(worker_index,),
                name=f"debot-listing-parser-{worker_index + 1}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

        startup_errors: List[Exception] = []
        for _ in self._threads:
            ready, error = self._ready.get()
            if not ready and error is not None:
                startup_errors.append(error)

        if startup_errors:
            self.close()
            raise RuntimeError("Unable to start parallel listing parser workers.") from startup_errors[0]

        return self

    def _worker_cancelled(self) -> bool:
        return self._stop_event.is_set() or bool(self.should_cancel())

    def _batch_cancelled(self, batch_id: int) -> bool:
        with self._batch_lock:
            return batch_id in self._cancelled_batches

    def _worker(self, worker_index: int) -> None:
        del worker_index
        pw = None
        browser = None
        context = None
        page = None
        ready_sent = False

        def close_session() -> None:
            nonlocal browser, context, page
            for resource in (page, context, browser):
                if resource is None:
                    continue
                try:
                    resource.close()
                except Exception:
                    pass
            page = None
            context = None
            browser = None

        def start_session() -> None:
            nonlocal browser, context, page
            browser, context = create_browser_context(
                pw,
                headless=self.headless,
                slowmo=self.slowmo,
                storage_state=self.storage_state,
            )
            page = context.new_page()

        def reset_session() -> None:
            close_session()
            start_session()

        try:
            pw = sync_playwright().start()
            start_session()
            self._ready.put((True, None))
            ready_sent = True

            while True:
                job = self._jobs.get()
                if job is self._STOP:
                    break

                batch_id, index, url = job
                item = None
                error: Optional[Exception] = None

                try:
                    if self._worker_cancelled() or self._batch_cancelled(batch_id):
                        raise SearchCancelled("Listing parse batch cancelled.")
                    item = self.parse_job(
                        lambda: page,
                        url,
                        lambda: self._worker_cancelled() or self._batch_cancelled(batch_id),
                        reset_session,
                    )
                except Exception as exc:
                    error = exc

                self._results.put((batch_id, index, url, item, error))
        except Exception as exc:
            if not ready_sent:
                self._ready.put((False, exc))
        finally:
            close_session()
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass

    def _next_result_for_batch(self, batch_id: int) -> ParseResult:
        backlog = self._result_backlog.get(batch_id)
        if backlog:
            return backlog.pop(0)

        while True:
            try:
                result = self._results.get(timeout=0.1)
            except queue.Empty:
                if not any(thread.is_alive() for thread in self._threads):
                    raise RuntimeError("Listing workers exited before completing the batch.")
                continue
            result_batch_id = result[0]
            if result_batch_id == batch_id:
                return result
            self._result_backlog.setdefault(result_batch_id, []).append(result)

    def map_ordered(
        self,
        urls: List[str],
    ) -> Iterator[Tuple[str, Optional[Dict[str, Any]]]]:
        """Submit a bounded batch and yield parsed items in URL order."""
        if not self._started:
            self.start()

        ordered_urls = list(urls)
        if not ordered_urls:
            return

        with self._batch_lock:
            batch_id = self._next_batch_id
            self._next_batch_id += 1

        total = len(ordered_urls)
        prefetch = min(
            total,
            self.worker_count * self.prefetch_per_worker,
        )
        submitted = 0
        received = 0
        next_index = 0
        buffered: Dict[int, Tuple[str, Optional[Dict[str, Any]], Optional[Exception]]] = {}

        def submit_one(index: int) -> None:
            self._jobs.put((batch_id, index, ordered_urls[index]))

        for index in range(prefetch):
            submit_one(index)
            submitted += 1

        try:
            while next_index < total:
                while next_index not in buffered:
                    _, index, url, item, error = self._next_result_for_batch(batch_id)
                    received += 1
                    buffered[index] = (url, item, error)

                url, item, error = buffered.pop(next_index)
                if error is not None:
                    raise error

                yield url, item
                next_index += 1
                # Bound work ahead of the consumer, including out-of-order
                # results buffered behind a slow or rate-limited first item.
                if submitted < total:
                    submit_one(submitted)
                    submitted += 1

        finally:
            if received < submitted:
                with self._batch_lock:
                    self._cancelled_batches.add(batch_id)
                try:
                    while received < submitted:
                        self._next_result_for_batch(batch_id)
                        received += 1
                finally:
                    with self._batch_lock:
                        self._cancelled_batches.discard(batch_id)

    def close(self) -> None:
        if not self._started:
            return

        self._stop_event.set()
        for _ in self._threads:
            self._jobs.put(self._STOP)
        for thread in self._threads:
            thread.join()

        self._threads.clear()
        self._started = False

    def __enter__(self) -> "ParallelListingParser":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()
