import sys
from pathlib import Path
import unittest
import tempfile

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from shop_cache import ShopCache, ShopSnapshot


class ShopCacheTest(unittest.TestCase):
    @staticmethod
    def snapshot(count=3, *, complete=True):
        links = [f'https://www.depop.com/products/item-{index}/' for index in range(count)]
        return ShopSnapshot(links, {url: {'url': url, 'description': 'P2P 21, length 27'} for url in links}, complete, 100)

    def test_complete_snapshot_can_cover_larger_requests_and_deduplicate_groups(self):
        cache = ShopCache()
        snapshot = self.snapshot()
        cache.put('tops', snapshot)
        result = cache.get('tops', max_links=1000, excluded_urls={snapshot.links[0]})
        self.assertEqual(result.links, snapshot.links[1:])
        self.assertEqual(result.sold_count, 100)
        self.assertTrue(result.complete)

    def test_partial_snapshot_never_claims_a_larger_scan_is_complete(self):
        cache = ShopCache()
        snapshot = self.snapshot(24, complete=False)
        cache.put('tops', snapshot)
        self.assertIsNone(cache.get('tops', max_links=72))
        self.assertEqual(len(cache.get('tops', max_links=24).links), 24)
        prefix = cache.get('tops', max_links=72, allow_partial=True)
        self.assertEqual(len(prefix.links), 24)
        self.assertFalse(prefix.complete)
        self.assertIsNone(cache.get('tops', max_links=24, excluded_urls={snapshot.links[0]}))

    def test_expired_snapshot_is_not_served_or_refreshed_by_reads(self):
        now = [0]
        cache = ShopCache(ttl_seconds=10, clock=lambda: now[0])
        cache.put('tops', self.snapshot())
        now[0] = 9
        self.assertIsNotNone(cache.get('tops', max_links=3))
        now[0] = 10
        self.assertIsNone(cache.get('tops', max_links=3))

    def test_readers_and_writers_cannot_mutate_the_shared_snapshot(self):
        cache = ShopCache()
        snapshot = self.snapshot()
        cache.put('tops', snapshot)
        first_url = snapshot.links[0]
        snapshot.items[first_url]['description'] = 'changed'
        result = cache.get('tops', max_links=3)
        self.assertEqual(result.items[first_url]['description'], 'P2P 21, length 27')
        result.items[first_url]['description'] = 'changed again'
        self.assertEqual(cache.get('tops', max_links=3).items[first_url]['description'], 'P2P 21, length 27')

    def test_total_item_budget_evicts_oldest_collections(self):
        cache = ShopCache(max_items=5)
        cache.put('tops', self.snapshot())
        cache.put('bottoms', self.snapshot())
        self.assertIsNone(cache.get('tops', max_links=3))
        self.assertIsNotNone(cache.get('bottoms', max_links=3))
        cache.put('too-large', self.snapshot(6))
        self.assertIsNone(cache.get('too-large', max_links=6))

    def test_empty_collections_are_bounded_and_uncached_details_are_rejected(self):
        cache = ShopCache(max_entries=2)
        for key in ('one', 'two', 'three'):
            cache.put(key, self.snapshot(0))
        self.assertIsNone(cache.get('one', max_links=1))
        self.assertEqual(cache.get('three', max_links=1).links, [])
        cache.put('missing-description', ShopSnapshot(['missing'], {}, True))
        self.assertIsNone(cache.get('missing-description', max_links=1))

    def test_category_and_gender_scopes_do_not_mix(self):
        cache = ShopCache()
        cache.put('shop?groups=tops&gender=male', self.snapshot())
        self.assertIsNone(cache.get('shop?groups=tops&gender=female', max_links=3))
        self.assertIsNone(cache.get('shop?groups=bottoms&gender=male', max_links=3))

    def test_daily_inventory_survives_freshness_expiry_and_a_restart_but_not_midnight(self):
        now = [1000]
        day = ['2026-11-14']
        with tempfile.TemporaryDirectory() as directory:
            options = dict(path=Path(directory) / 'shops.sqlite3', ttl_seconds=300,
                           clock=lambda: now[0], day_key=lambda: day[0])
            cache = ShopCache(**options)
            snapshot = self.snapshot()
            cache.put('tops', snapshot)
            now[0] += 301
            self.assertIsNone(cache.get('tops', max_links=100))
            restored = ShopCache(**options)
            stale = restored.get('tops', max_links=100, allow_stale=True)
            self.assertFalse(stale.fresh)
            self.assertEqual(stale.links, snapshot.links)
            self.assertIsNotNone(restored.find_listing(snapshot.links[0]))
            self.assertIsNone(restored.get('tops', max_links=100))
            day[0] = '2026-11-15'
            self.assertIsNone(restored.get('tops', max_links=100, allow_stale=True))
            self.assertIsNone(restored.find_listing(snapshot.links[0]))
            self.assertIsNone(ShopCache(**options).get('tops', max_links=100, allow_stale=True))

    def test_truncating_complete_inventory_does_not_claim_exhaustion(self):
        cache = ShopCache()
        cache.put('tops', self.snapshot(10))
        self.assertFalse(cache.get('tops', max_links=2).complete)
        self.assertTrue(cache.get('tops', max_links=10).complete)

    def test_sold_items_are_invalidated_in_every_scope_and_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            options = dict(path=Path(directory) / 'shops.sqlite3', day_key=lambda: '2026-11-14')
            cache = ShopCache(**options)
            snapshot = self.snapshot()
            cache.put('tops', snapshot)
            cache.put('coats', snapshot)
            cache.invalidate_urls([snapshot.links[0]])
            restored = ShopCache(**options)
            self.assertIsNone(restored.find_listing(snapshot.links[0]))
            for group in ('tops', 'coats'):
                self.assertEqual(restored.get(group, max_links=100).links, snapshot.links[1:])

    def test_eviction_and_disabled_cache_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            options = dict(path=Path(directory) / 'shops.sqlite3', max_entries=1,
                           day_key=lambda: '2026-11-14')
            cache = ShopCache(**options)
            cache.put('old', self.snapshot())
            cache.put('new', self.snapshot())
            restored = ShopCache(**options)
            self.assertIsNone(restored.get('old', max_links=100))
            self.assertIsNotNone(restored.get('new', max_links=100))
            disabled = ShopCache(**{**options, 'ttl_seconds': 0})
            self.assertIsNone(disabled.get('new', max_links=100, allow_stale=True))
            self.assertIsNone(disabled.find_listing(self.snapshot().links[0]))

    def test_listing_fallback_uses_newest_data_not_last_accessed_scope(self):
        now = [1000]
        cache = ShopCache(clock=lambda: now[0], day_key=lambda: '2026-11-14')
        snapshot = self.snapshot()
        url = snapshot.links[0]
        snapshot.items[url]['price'] = '$25'
        cache.put('old-scope', snapshot)
        now[0] += 1
        snapshot.items[url]['price'] = '$35'
        cache.put('new-scope', snapshot)
        cache.get('old-scope', max_links=100)
        self.assertEqual(cache.find_listing(url)['price'], '$35')


if __name__ == '__main__':
    unittest.main()
