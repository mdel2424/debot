"""Browser regressions against Vite with local, deterministic API fixtures."""

import datetime as dt
import json
import os
import unittest

from playwright.sync_api import sync_playwright


@unittest.skipUnless(os.getenv('DEBOT_FRONTEND_URL'), 'Set DEBOT_FRONTEND_URL to run browser regressions.')
class FrontendSmokeTest(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.firefox.launch(headless=True)
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
        self.context.set_default_timeout(10000)
        self.context.add_init_script("""
            if (!localStorage.getItem('debot.followingAccounts.v1')) {
                localStorage.setItem('debot.sellerAdditions.version', '1');
                localStorage.setItem('debot.followingAccounts.v1', JSON.stringify([
                    {username: 'fixture_one', name: 'Fixture One'},
                    {username: 'fixture_two', name: 'Fixture Two'}
                ]));
            }
        """)
        self.context.route('https://fonts.googleapis.com/**', lambda route: route.abort())
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.url = os.environ['DEBOT_FRONTEND_URL']

    def tearDown(self):
        self.context.close()
        self.browser.close()
        self.pw.stop()

    @staticmethod
    def event(value):
        return f'data: {json.dumps(value)}\n\n'

    def test_close_and_reopen_restores_matches_and_reconnects_same_job(self):
        resumed = False
        search_ids = []

        def stream(route):
            payload = route.request.post_data_json
            search_ids.append(payload['searchId'])
            events = [
                {'type': 'match', 'item': {'url': 'https://www.depop.com/products/fixture-one/', 'price': '$25'}},
                {'type': 'progress', 'processed': 1, 'total': 2, 'matches': 1},
            ]
            if resumed:
                events.extend([
                    {'type': 'match', 'item': {'url': 'https://www.depop.com/products/fixture-two/', 'price': '$30'}},
                    {'type': 'done', 'processed': 2, 'total': 2, 'matches': 2},
                ])
            route.fulfill(content_type='text/event-stream', body=': ready\n\n' + ''.join(self.event(e) for e in events))

        self.context.route('**/api/search/stream', stream)
        self.page.goto(self.url)
        self.page.get_by_role('button', name='Search', exact=True).first.click()
        self.page.wait_for_function("document.querySelectorAll('.result-card').length === 1")
        self.page.close()
        resumed = True
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.page.wait_for_function("document.querySelectorAll('.result-card').length === 2")
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].processed === true")
        self.assertEqual(len(set(search_ids)), 1)
        self.assertGreaterEqual(len(search_ids), 2)
        self.assertEqual(self.errors, [])

    def test_batch_ids_are_saved_before_registration_and_mobile_layout_fits(self):
        registered = []
        saved_before_request = []

        def batch(route):
            payloads = route.request.post_data_json['searches']
            saved = self.page.evaluate("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows")
            saved_before_request.extend(row['searchId'] for row in saved)
            registered.extend(payload['searchId'] for payload in payloads)
            route.fulfill(json={'ok': True, 'count': len(payloads), 'jobs': []})

        self.context.route('**/api/search/batch/start', batch)
        self.context.route('**/api/search/stream', lambda route: route.fulfill(
            content_type='text/event-stream', body=self.event({'type': 'done', 'processed': 24, 'total': 24, 'matches': 0}),
        ))
        self.page.goto(self.url)
        self.page.get_by_role('button', name='Search All Sellers').click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows.every(row => row.processed)")
        self.assertEqual(len(registered), 2)
        self.assertEqual(set(registered), set(saved_before_request))
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        self.assertEqual(self.errors, [])

    def test_new_sellers_are_added_once_without_resetting_a_custom_list(self):
        self.context.add_init_script("""
            if (!localStorage.getItem('fixture_migration_seeded')) {
                localStorage.setItem('debot.followingAccounts.v1', JSON.stringify([
                    {username: 'fixture_one', name: 'My Custom Name'},
                    {username: 'theloopmtl', name: 'My Loop Name'}
                ]));
                localStorage.removeItem('debot.sellerAdditions.version');
                localStorage.setItem('fixture_migration_seeded', '1');
            }
        """)
        self.page.goto(self.url)
        self.page.wait_for_function("localStorage.getItem('debot.sellerAdditions.version') === '1'")
        sellers = self.page.evaluate("JSON.parse(localStorage.getItem('debot.followingAccounts.v1'))")
        self.assertEqual(len(sellers), 4)
        self.assertEqual(sellers[0], {'username': 'fixture_one', 'name': 'My Custom Name'})
        self.assertEqual(sellers[1], {'username': 'theloopmtl', 'name': 'My Loop Name'})
        self.assertEqual({seller['username'] for seller in sellers}, {
            'fixture_one', 'theloopmtl', 'thriftsnspliffs', 'mountainvintagethrifts',
        })
        self.page.evaluate("""localStorage.setItem('debot.followingAccounts.v1', JSON.stringify(
            JSON.parse(localStorage.getItem('debot.followingAccounts.v1'))
                .filter(seller => seller.username !== 'thriftsnspliffs')
        ))""")
        self.page.reload()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.followingAccounts.v1')).length === 3")
        self.assertEqual(self.page.locator('.seller-row-card').count(), 3)
        self.assertEqual(self.errors, [])

    def test_queued_seller_shows_the_shared_rate_limit_countdown(self):
        retry_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=3593)).isoformat()
        self.context.route('**/api/search/stream', lambda route: route.fulfill(
            content_type='text/event-stream', body=self.event({
                'type': 'progress', 'phase': 'waiting_rate_limit',
                'processed': 0, 'total': 0, 'matches': 0,
                'retryAvailableAt': retry_at,
                'message': 'Depop has paused requests. This search will start after the shared cooldown.',
            }),
        ))
        self.page.goto(self.url)
        self.page.get_by_role('button', name='Search', exact=True).first.click()
        self.page.get_by_text('Waiting for Depop cooldown', exact=True).wait_for()
        self.page.get_by_text('Depop has paused requests. This search will start after the shared cooldown.', exact=True).wait_for()
        self.assertIn('Cooldown 59m', self.page.locator('.seller-progress-primary').first.inner_text())
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
