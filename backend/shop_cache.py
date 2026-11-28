"""Bounded shop snapshots, optionally persisted for the backend's local calendar day."""

from collections import OrderedDict
import copy
from dataclasses import dataclass
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time


@dataclass
class ShopSnapshot:
    links: list[str]
    items: dict[str, dict]
    complete: bool
    sold_count: int = 0
    fresh: bool = True


class ShopCache:
    def __init__(self, *, ttl_seconds=300, max_items=5000, max_entries=64,
                 clock=time.time, path=None, day_key=None):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.max_entries = max_entries
        self.clock = clock
        self.path = Path(path) if path else None
        self.day_key = day_key
        self._entries = OrderedDict()
        self._item_count = 0
        self._lock = threading.Lock()
        if self.path:
            self._load()

    def _day(self):
        return self.day_key() if self.day_key else None

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute('CREATE TABLE IF NOT EXISTS shops '
                           '(key TEXT PRIMARY KEY, created REAL, day TEXT, snapshot TEXT)')
        return connection

    def _load(self):
        try:
            with closing(self._connect()) as connection, connection:
                for key, created, day, encoded in connection.execute('SELECT * FROM shops ORDER BY created'):
                    if day != self._day() or self.clock() - created < 0:
                        continue
                    data = json.loads(encoded)
                    snapshot = ShopSnapshot(**data)
                    if any(url not in snapshot.items for url in snapshot.links):
                        continue
                    self._entries[key] = (created, day, snapshot)
                    self._item_count += len(snapshot.items)
            self._prune()
            self._persist()
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            logging.getLogger(__name__).warning('Could not load daily shop cache', exc_info=True)

    def _persist(self, key=None):
        if not self.path:
            return
        try:
            with closing(self._connect()) as connection, connection:
                keys = list(self._entries)
                if keys:
                    connection.execute(f'DELETE FROM shops WHERE key NOT IN ({",".join("?" for _ in keys)})', keys)
                else:
                    connection.execute('DELETE FROM shops')
                entries = self._entries.items() if key is None else [(key, self._entries[key])]
                connection.executemany('INSERT OR REPLACE INTO shops VALUES (?, ?, ?, ?)', [
                    (key, created, day, json.dumps({
                        'links': snapshot.links, 'items': snapshot.items,
                        'complete': snapshot.complete, 'sold_count': snapshot.sold_count,
                    })) for key, (created, day, snapshot) in entries
                ])
        except (OSError, sqlite3.Error, TypeError):
            logging.getLogger(__name__).warning('Could not persist daily shop cache', exc_info=True)

    def _remove(self, key):
        _, _, snapshot = self._entries.pop(key)
        self._item_count -= len(snapshot.items)

    def _prune(self):
        for key, (created, day, _) in list(self._entries.items()):
            if (day != self._day() or self.clock() < created
                    or (self.day_key is None and self.clock() - created >= self.ttl_seconds)):
                self._remove(key)
        while len(self._entries) > self.max_entries or self._item_count > self.max_items:
            self._remove(next(iter(self._entries)))

    def put(self, key, snapshot):
        if self.ttl_seconds <= 0 or self.max_entries <= 0 or self.max_items <= 0:
            return
        if len(snapshot.items) > self.max_items or any(url not in snapshot.items for url in snapshot.links):
            return
        with self._lock:
            self._prune()
            if key in self._entries:
                self._remove(key)
            self._entries[key] = (self.clock(), self._day(), copy.deepcopy(snapshot))
            self._item_count += len(snapshot.items)
            self._prune()
            self._persist(key)

    def get(self, key, *, max_links, excluded_urls=None, allow_partial=False, allow_stale=False):
        if self.ttl_seconds <= 0 or self.max_items <= 0 or self.max_entries <= 0:
            return None
        excluded = excluded_urls or set()
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None:
                return None
            created, _, snapshot = entry
            fresh = self.clock() - created < self.ttl_seconds
            if not fresh and not allow_stale:
                return None
            available_links = [url for url in snapshot.links if url not in excluded]
            if not snapshot.complete and len(available_links) < max_links and not allow_partial:
                return None
            links = available_links[:max_links]
            self._entries.move_to_end(key)
            return ShopSnapshot(
                links, {url: copy.deepcopy(snapshot.items[url]) for url in links},
                snapshot.complete and len(links) == len(available_links),
                snapshot.sold_count, fresh,
            )

    def find_listing(self, url):
        if self.ttl_seconds <= 0:
            return None
        with self._lock:
            self._prune()
            newest = max((entry for entry in self._entries.values() if url in entry[2].items),
                         key=lambda entry: entry[0], default=None)
            if newest is not None:
                return copy.deepcopy(newest[2].items[url])
        return None

    def discard(self, key):
        with self._lock:
            if key in self._entries:
                self._remove(key)
                self._persist()

    def invalidate_urls(self, urls):
        urls = set(urls)
        if not urls:
            return
        with self._lock:
            changed = False
            for _, _, snapshot in self._entries.values():
                for url in urls:
                    if snapshot.items.pop(url, None) is not None:
                        self._item_count -= 1
                        changed = True
                snapshot.links = [url for url in snapshot.links if url not in urls]
            if changed:
                self._persist()

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._item_count = 0
            self._persist()

    def list_snapshots(self):
        """Return copies of all currently retained snapshots (for today's daily cache)."""
        with self._lock:
            self._prune()
            results = []
            for key, (created, _, snapshot) in self._entries.items():
                fresh = self.clock() - created < self.ttl_seconds
                results.append((
                    key,
                    ShopSnapshot(
                        list(snapshot.links),
                        copy.deepcopy(snapshot.items),
                        snapshot.complete,
                        snapshot.sold_count,
                        fresh,
                    ),
                ))
            return results
