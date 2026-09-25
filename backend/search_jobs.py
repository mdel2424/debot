"""In-memory search jobs that outlive individual SSE subscribers."""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple


class SearchJob:
    """Store a search's replayable event history and terminal state."""

    def __init__(self, search_id: str, payload: Dict[str, Any]):
        self.search_id = search_id
        self.payload = copy.deepcopy(payload)
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._events: List[bytes] = []
        self._complete = False
        self._lock = threading.Lock()

    def publish(self, event: bytes) -> None:
        if not isinstance(event, bytes):
            raise TypeError("Search job events must be encoded bytes.")
        with self._lock:
            if self._complete:
                return
            self._events.append(event)
            self.updated_at = time.time()

    def finish(self) -> None:
        with self._lock:
            self._complete = True
            self.updated_at = time.time()

    def read_from(self, index: int) -> Tuple[List[bytes], bool]:
        with self._lock:
            start = max(int(index or 0), 0)
            return list(self._events[start:]), self._complete

    @property
    def complete(self) -> bool:
        with self._lock:
            return self._complete


class SearchJobRegistry:
    """Keep bounded process-local search history for reconnecting clients."""

    def __init__(self, max_jobs: int = 500):
        self.max_jobs = max(int(max_jobs or 1), 1)
        self._jobs: OrderedDict[str, SearchJob] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, search_id: str) -> Optional[SearchJob]:
        with self._lock:
            job = self._jobs.get(search_id)
            if job is not None:
                self._jobs.move_to_end(search_id)
            return job

    def get_or_create(
        self,
        search_id: str,
        payload: Dict[str, Any],
    ) -> Tuple[SearchJob, bool]:
        with self._lock:
            existing = self._jobs.get(search_id)
            if existing is not None:
                self._jobs.move_to_end(search_id)
                return existing, False

            self._prune_completed_locked()
            job = SearchJob(search_id, payload)
            self._jobs[search_id] = job
            return job, True

    def _prune_completed_locked(self) -> None:
        while len(self._jobs) >= self.max_jobs:
            completed_id = next(
                (search_id for search_id, job in self._jobs.items() if job.complete),
                None,
            )
            if completed_id is None:
                return
            self._jobs.pop(completed_id, None)
