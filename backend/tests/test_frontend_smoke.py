"""Browser regressions against Vite with local, deterministic API fixtures."""

import datetime as dt
import json
import os
import re
import unittest

from playwright.sync_api import expect, sync_playwright


@unittest.skipUnless(os.getenv('DEBOT_FRONTEND_URL'), 'Set DEBOT_FRONTEND_URL to run browser regressions.')
class FrontendSmokeTest(unittest.TestCase):
    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.firefox.launch(headless=True)
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
        self.context.set_default_timeout(10000)
        self.context.add_init_script("""
            if (!localStorage.getItem('debot.followingAccounts.v1')) {
                localStorage.setItem('debot.sellerAdditions.version', '3');
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

    def test_retailers_respect_availability_send_fit_and_restore_results(self):
        searches = []
        registered = []
        depop_routes = []

        def batch(route):
            registered.extend(route.request.post_data_json["searches"])
            route.fulfill(json={"ok": True, "count": 1, "jobs": []})

        def stream(route):
            payload = route.request.post_data_json
            if payload.get("seller"):
                depop_routes.append(route)
                return
            searches.append(payload)
            route.fulfill(content_type="text/event-stream", body="".join(map(self.event, [
                {"type": "match", "item": {
                    "url": "https://www.peoplesthrift.ca/product-page/fixture",
                    "seller": "retail:peoples-thrift", "title": "Measured graphic T-shirt",
                    "p2p": 21, "length": 27, "price": "$15.00 CAD",
                }},
                {"type": "done", "processed": 1, "total": 1, "matches": 1},
            ])))

        self.context.route("**/api/search/retailers", lambda route: route.fulfill(json=[
            {"id": "wildrose", "enabled": False}, {"id": "la-penderie", "enabled": False},
            {"id": "peoples-thrift", "enabled": True},
        ]))
        self.context.route("**/api/search/batch/start", batch)
        self.context.route("**/api/search/stream", stream)
        self.page.goto(self.url + "#tops")
        with self.page.expect_request("**/api/search/stream"):
            self.page.get_by_role("button", name="Search", exact=True).first.click()
        self.page.get_by_role("button", name="Retailers", exact=True).click()
        for name in ("Wildrose Vintage", "La Penderie du Paradis"):
            row = self.page.locator(".seller-row-card").filter(has_text=name)
            expect(row.get_by_role("button", name="Search", exact=True)).to_be_disabled()
            expect(row.get_by_text("Permission required", exact=True)).to_be_visible()
        expect(self.page.get_by_role("link", name="People's Thrift")).to_be_visible()
        self.page.get_by_role("button", name="Search retailers", exact=True).click()
        expect(self.page.get_by_text("Measured graphic T-shirt", exact=True)).to_be_visible()
        self.page.wait_for_function("""
            JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1'))?.tops.retailerRows
                ?.some(row => row.seller === 'retail:peoples-thrift' && row.processed && row.results.length === 1)
        """)
        self.assertEqual(registered, searches)
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]["retailSource"], "peoples-thrift")
        self.assertNotIn("seller", searches[0])
        self.assertEqual(searches[0]["measurements"], {"first": 20.875, "second": 26.875})
        self.assertEqual(searches[0]["maxPriceCad"], 35)
        depop_routes[0].fulfill(content_type="text/event-stream", body=self.event({"type": "done"}))
        self.page.wait_for_function("""
            JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1'))?.tops.sellerRows[0].processed
        """)
        self.page.reload()
        self.page.get_by_role("button", name="Retailers", exact=True).click()
        expect(self.page.get_by_text("Measured graphic T-shirt", exact=True)).to_be_visible()
        self.page.set_viewport_size({"width": 320, "height": 840})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        self.page.get_by_role("button", name="With matches").click()
        expect(self.page.get_by_text("Measured graphic T-shirt", exact=True)).to_be_visible()
        expect(self.page.get_by_role("link", name="Wildrose Vintage")).to_have_count(0)
        self.page.get_by_role("button", name="All sellers", exact=True).click()
        expect(self.page.get_by_role("link", name="Fixture One")).to_be_visible()
        expect(self.page.get_by_text("Measured graphic T-shirt", exact=True)).to_have_count(0)
        self.assertEqual(self.errors, [])

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

    def test_tops_search_sends_exact_default_measurement_ranges(self):
        searches = []

        def stream(route):
            searches.append(route.request.post_data_json)
            route.fulfill(content_type='text/event-stream', body=self.event({'type': 'done'}))

        self.context.route('**/api/search/stream', stream)
        self.page.goto(self.url + '#tops')
        expect(self.page.get_by_role('textbox', name='Pit to pit minimum')).to_have_value('20.5')
        expect(self.page.get_by_role('textbox', name='Pit to pit maximum')).to_have_value('21.25')
        expect(self.page.get_by_role('textbox', name='Length minimum')).to_have_value('26.5')
        expect(self.page.get_by_role('textbox', name='Length maximum')).to_have_value('27.25')
        with self.page.expect_request('**/api/search/stream'):
            self.page.get_by_role('button', name='Search', exact=True).first.click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].processed")
        self.assertEqual(len(searches), 1)
        payload = searches[0]
        first, second = (payload['measurements'][key] for key in ('first', 'second'))
        p2p_tolerance, length_tolerance = payload['p2pTolerance'], payload['lengthTolerance']
        self.assertEqual((first - p2p_tolerance, first + p2p_tolerance), (20.5, 21.25))
        self.assertEqual((second - length_tolerance, second + length_tolerance), (26.5, 27.25))

    def test_custom_tops_range_migrates_validates_and_resets(self):
        self.context.add_init_script("""
            if (!localStorage.getItem('debot.categoryFilters.v1')) {
                localStorage.setItem('debot.categoryFilters.defaults.v1', JSON.stringify([
                    {id: 'tops', defaults: {first: '20.875', second: '26.875', firstTolerance: '0.375', secondTolerance: '0.375'}}
                ]));
                localStorage.setItem('debot.categoryFilters.v1', JSON.stringify({
                    tops: {first: '22', second: '28', firstTolerance: '0.5', secondTolerance: '1'},
                    footwear: {min: '8', max: '9'}
                }));
            }
        """)
        self.page.goto(self.url + '#tops')
        minimum = self.page.get_by_role('textbox', name='Pit to pit minimum')
        maximum = self.page.get_by_role('textbox', name='Pit to pit maximum')
        expect(minimum).to_have_value('21.5')
        expect(maximum).to_have_value('22.5')
        expect(self.page.get_by_role('textbox', name='Length minimum')).to_have_value('27')
        minimum.fill('23')
        expect(self.page.get_by_role('alert')).to_have_text('Enter a valid min and max, in order.')
        expect(self.page.get_by_role('button', name='Search All Sellers')).to_be_disabled()
        self.page.get_by_role('button', name='Reset filters').click()
        expect(minimum).to_have_value('20.5')
        expect(maximum).to_have_value('21.25')
        expect(self.page.get_by_role('button', name='Search All Sellers')).to_be_enabled()
        self.page.get_by_role('button', name='Footwear', exact=True).click()
        expect(self.page.get_by_role('combobox', name='US min')).to_have_value('8')
        self.page.get_by_role('link', name='Skip to search').focus()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('main')).to_be_focused()
        self.assertTrue(self.page.url.endswith('#footwear'))

    def test_price_cap_defaults_editing_and_removal(self):
        searches = []

        def stream(route):
            searches.append(route.request.post_data_json)
            route.fulfill(content_type='text/event-stream', body=self.event({'type': 'done'}))

        self.context.route('**/api/search/stream', stream)
        self.page.goto(self.url + '#tops')
        price = self.page.get_by_role('textbox', name='Max price (CAD)')
        expect(price).to_have_value('35')
        self.page.get_by_role('button', name='Bottoms', exact=True).click()
        expect(price).to_have_value('45')
        search = self.page.get_by_role('button', name='Search', exact=True).first
        price.fill('-1')
        expect(search).to_be_disabled()
        expect(self.page.get_by_role('button', name='Search All Sellers')).to_be_disabled()
        for value in ('39.50', ''):
            price.fill(value)
            search.click()
            self.page.wait_for_function(
                "JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).bottoms.sellerRows[0].processed"
            )
        self.assertEqual(searches[0]['maxPriceCad'], 39.5)
        self.assertNotIn('maxPriceCad', searches[1])
        self.page.reload()
        expect(price).to_have_value('')
        self.page.get_by_role('button', name='Reset filters').click()
        expect(price).to_have_value('45')

    def test_bottoms_guide_tracks_measurement_focus(self):
        self.page.goto(self.url + '#bottoms')
        guide = self.page.get_by_role('img', name='Bottoms measurement guide')
        expect(guide).to_be_visible()
        for label, key in (
            ('Waist', 'waistMin'),
            ('Inseam', 'inseamMin'),
            ('Rise', 'riseMin'),
            ('Leg opening', 'legOpeningMin'),
        ):
            self.page.get_by_role('textbox', name=f'{label} minimum').focus()
            expect(guide.locator(f'[data-fit-mark="{key}"]')).to_have_css('opacity', '1')
            for other in guide.locator(f'[data-fit-mark]:not([data-fit-mark="{key}"])').all():
                expect(other).to_have_css('opacity', '0.38')

        for width in (320, 390, 768, 1024, 1440):
            self.page.set_viewport_size({'width': width, 'height': 900})
            box = guide.bounding_box()
            self.assertGreater(box['width'], 0)
            self.assertGreater(box['height'], 0)
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x'] + box['width'], width)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)

        self.page.get_by_role('button', name='Tops', exact=True).click()
        expect(self.page.get_by_role('img', name='Tops measurement guide')).to_be_visible()
        expect(guide).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_bottoms_sends_separate_ranges_and_displays_measurements_in_cards(self):
        searches = []
        self.context.add_init_script("""
            if (!localStorage.getItem('debot.pageWorkspaces.v1')) {
                localStorage.setItem('debot.pageWorkspaces.v1', JSON.stringify({bottoms: {sellerRows: [{
                    seller: 'fixture_one', searchId: 'legacy-bottoms',
                    searchTask: {filters: {waistMin: '32', waistMax: '36', inseamRiseMin: '42',
                        inseamRiseMax: '44', legOpeningMin: '9.5', legOpeningMax: '10.5'}}
                }]}}));
            }
        """)

        def stream(route):
            payload = route.request.post_data_json
            searches.append(payload)
            if payload['searchId'] == 'legacy-bottoms':
                events = [
                    {'type': 'match', 'item': {
                        'url': 'https://www.depop.com/products/legacy-bottoms/', 'price': '$25',
                        'inseam': 30, 'rise': 13,
                    }},
                    {'type': 'done', 'processed': 1, 'total': 1, 'matches': 1},
                ]
                route.fulfill(content_type='text/event-stream', body=''.join(self.event(e) for e in events))
                return
            events = [
                {'type': 'match', 'item': {
                    'url': 'https://www.depop.com/products/full-measurements/',
                    'price': '$45', 'sizeLabel': '34', 'ageDays': 4, 'waist': 34,
                    'inseam': 32.25, 'rise': 12, 'legOpening': 9.75,
                }},
                {'type': 'match', 'item': {
                    'url': 'https://www.depop.com/products/missing-rise/', 'price': '$30', 'inseam': 31,
                }},
                {'type': 'done', 'processed': 2, 'total': 2, 'matches': 2},
            ]
            route.fulfill(content_type='text/event-stream', body=''.join(self.event(e) for e in events))

        self.context.route('**/api/search/stream', stream)
        self.page.goto(self.url + '#bottoms')
        for field, value in (('Inseam minimum', '31'), ('Inseam maximum', '33'),
                             ('Rise minimum', '11'), ('Rise maximum', '13')):
            expect(self.page.get_by_role('textbox', name=field)).to_have_value(value)
        expect(self.page.get_by_text('$25', exact=True)).to_be_visible()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).bottoms.sellerRows[0].processed")
        self.page.get_by_role('button', name='Search', exact=True).first.click()
        cards = self.page.locator('.result-card')
        expect(cards).to_have_count(2)
        self.assertEqual(len(searches), 2)
        self.assertEqual(searches[0]['bottomsMeasurements']['inseamRise'], {'min': 42, 'max': 44})
        self.assertNotIn('inseam', searches[0]['bottomsMeasurements'])
        self.assertEqual(searches[1]['bottomsMeasurements'], {
            'waist': {'min': 32, 'max': 36},
            'inseam': {'min': 31, 'max': 33},
            'rise': {'min': 11, 'max': 13},
            'legOpening': {'min': 9.5, 'max': 10.5},
        })
        full = cards.first.locator('.result-measurements')
        expect(full.locator('dt')).to_have_text(['Waist', 'Inseam', 'Rise', 'Leg opening', 'Inseam + rise'])
        expect(full.locator('dd')).to_have_text(['34\u2033', '32.25\u2033', '12\u2033', '9.75\u2033', '44.25\u2033'])
        partial = cards.nth(1).locator('.result-measurements')
        expect(partial.locator('dt')).to_have_text(['Inseam'])
        expect(partial.locator('dd')).to_have_text(['31\u2033'])
        self.page.set_viewport_size({'width': 320, 'height': 800})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 320)
        self.assertEqual(self.errors, [])

    def test_workspace_filters_and_seller_dialog_work_on_phone(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto(self.url + '#tops')
        self.page.get_by_role('textbox', name='Filter sellers').fill('fixture_two')
        expect(self.page.locator('.seller-row-card')).to_have_count(1)
        expect(self.page.get_by_role('link', name='Fixture Two')).to_be_visible()
        self.page.get_by_role('button', name='With matches').click()
        expect(self.page.get_by_role('heading', name='No sellers found')).to_be_visible()
        self.page.get_by_role('button', name='Manage sellers').click()
        dialog = self.page.get_by_role('dialog', name='Manage sellers')
        expect(dialog).to_be_visible()
        self.page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        for width in (320, 390, 768, 1024, 1440):
            self.page.set_viewport_size({'width': width, 'height': 900})
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
        self.page.wait_for_function("localStorage.getItem('debot.sellerAdditions.version') === '3'")
        sellers = self.page.evaluate("JSON.parse(localStorage.getItem('debot.followingAccounts.v1'))")
        self.assertEqual(len(sellers), 6)
        self.assertEqual(sellers[0], {'username': 'fixture_one', 'name': 'My Custom Name'})
        self.assertEqual(sellers[1], {'username': 'theloopmtl', 'name': 'My Loop Name'})
        self.assertEqual({seller['username'] for seller in sellers}, {
            'fixture_one', 'theloopmtl', 'thriftsnspliffs', 'mountainvintagethrifts',
            'secondlifeinc', 'vintagebysherman',
        })
        self.page.evaluate("""localStorage.setItem('debot.followingAccounts.v1', JSON.stringify(
            JSON.parse(localStorage.getItem('debot.followingAccounts.v1'))
                .filter(seller => !['thriftsnspliffs', 'secondlifeinc'].includes(seller.username))
        ))""")
        self.page.reload()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.followingAccounts.v1')).length === 4")
        self.assertEqual(self.page.locator('.seller-row-card').count(), 4)
        self.assertEqual(self.errors, [])

    def test_second_seller_update_preserves_removals_and_existing_custom_names(self):
        self.context.add_init_script("""
            if (!localStorage.getItem('fixture_migration_seeded')) {
                localStorage.setItem('debot.followingAccounts.v1', JSON.stringify([
                    {username: 'fixture_one', name: 'My Custom Seller'},
                    {username: 'secondlifeinc', name: 'My Second Life Name'}
                ]));
                localStorage.setItem('debot.sellerAdditions.version', '1');
                localStorage.setItem('fixture_migration_seeded', '1');
            }
        """)
        self.page.goto(self.url)
        self.page.wait_for_function("localStorage.getItem('debot.sellerAdditions.version') === '3'")
        sellers = self.page.evaluate("JSON.parse(localStorage.getItem('debot.followingAccounts.v1'))")
        self.assertEqual(sellers, [
            {'username': 'fixture_one', 'name': 'My Custom Seller'},
            {'username': 'secondlifeinc', 'name': 'My Second Life Name'},
            {'username': 'vintagebysherman', 'name': 'Vintage by Sherman'},
        ])
        self.assertEqual(self.page.locator('.seller-row-card').count(), 3)
        self.assertEqual(self.errors, [])

    def test_removed_sellers_leave_saved_lists_and_workspaces_without_resetting_other_results(self):
        requests = []
        self.context.route('**/api/search/stream', lambda route: (
            requests.append(route.request.post_data_json),
            route.fulfill(content_type='text/event-stream', body=self.event({'type': 'done'})),
        ))
        self.context.add_init_script("""
            if (!localStorage.getItem('fixture_removal_seeded')) {
                localStorage.setItem('debot.followingAccounts.v1', JSON.stringify([
                    {username: 'fixture_one', name: 'My Custom Seller'},
                    {username: 'former_vintage', name: 'Former'},
                    {username: 'gwoodsgear', name: 'Gwoods'},
                    {username: 'jizzypantthrifts', name: 'Jizzy'},
                    {username: 'secondlifeinc', name: 'My Second Life Name'}
                ]));
                localStorage.setItem('debot.sellerAdditions.version', '2');
                localStorage.setItem('debot.pageWorkspaces.v1', JSON.stringify({tops: {sellerRows: [
                    {seller: 'fixture_one', results: [{url: 'https://www.depop.com/products/keep/', price: '$25'}]},
                    {seller: 'former_vintage', searchId: 'removed-job', loading: true, results: []}
                ]}}));
                localStorage.setItem('fixture_removal_seeded', '1');
            }
        """)
        self.page.goto(self.url)
        self.page.wait_for_function("localStorage.getItem('debot.sellerAdditions.version') === '3'")
        self.page.wait_for_function("""Object.values(JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')))
            .every(workspace => JSON.stringify(workspace.sellerRows.map(row => row.seller))
                === JSON.stringify(['fixture_one', 'secondlifeinc']))""")
        self.page.reload()
        sellers = self.page.evaluate("JSON.parse(localStorage.getItem('debot.followingAccounts.v1'))")
        self.assertEqual(sellers, [
            {'username': 'fixture_one', 'name': 'My Custom Seller'},
            {'username': 'secondlifeinc', 'name': 'My Second Life Name'},
        ])
        workspaces = self.page.evaluate("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1'))")
        for workspace in workspaces.values():
            self.assertEqual([row['seller'] for row in workspace['sellerRows']], ['fixture_one', 'secondlifeinc'])
        self.assertEqual(workspaces['tops']['sellerRows'][0]['results'][0]['url'], 'https://www.depop.com/products/keep/')
        self.assertEqual(requests, [])
        self.assertEqual(self.page.locator('.seller-row-card').count(), 2)
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

    def test_collection_metadata_keeps_already_streamed_progress(self):
        self.context.route('**/api/search/stream', lambda route: route.fulfill(
            content_type='text/event-stream', body=''.join(self.event(event) for event in [
                {'type': 'match', 'item': {'url': 'https://www.depop.com/products/streamed/', 'price': '$25'}},
                {'type': 'progress', 'processed': 12, 'total': 24, 'matches': 1},
                {'type': 'meta', 'links': 24, 'processed': 12, 'matches': 1},
            ]),
        ))
        self.page.goto(self.url)
        self.page.get_by_role('button', name='Search', exact=True).first.click()
        self.page.get_by_text(re.compile(r'1 matches.*12 parsed / 24 collected')).wait_for()
        self.assertEqual(self.errors, [])

    def test_same_day_rerun_merges_updates_and_preserves_unseen_matches(self):
        calls = []

        def stream(route):
            calls.append(route.request.post_data_json)
            if len(calls) == 1:
                events = [{'type': 'match', 'item': {'url': url, 'price': '$25'}}
                          for url in ('https://www.depop.com/products/old/',
                                      'https://www.depop.com/products/kept/',
                                      'https://www.depop.com/products/sold/')]
            else:
                events = [
                    {'type': 'match', 'item': {'url': 'https://www.depop.com/products/old/', 'price': '$45'}},
                    {'type': 'match', 'item': {'url': 'https://www.depop.com/products/new/', 'price': '$30'}},
                    {'type': 'remove', 'url': 'https://www.depop.com/products/sold/'},
                ]
            events.append({'type': 'done', 'processed': 3, 'total': 3, 'matches': 3})
            route.fulfill(content_type='text/event-stream', body=''.join(self.event(e) for e in events))

        self.context.route('**/api/search/stream', stream)
        self.page.goto(self.url)
        search = self.page.get_by_role('button', name='Search', exact=True).first
        search.click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].processed")
        self.page.evaluate("""() => {
            const key = 'debot.pageWorkspaces.v1';
            const state = JSON.parse(localStorage.getItem(key));
            state.tops.sellerRows[0].resultsFilters = {
                first: '20.875', firstTolerance: '0.375', second: '26.875', secondTolerance: '0.375',
                maxPriceCad: '35'
            };
            localStorage.setItem(key, JSON.stringify(state));
        }""")
        self.page.reload()
        search.click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].results.some(item => item.price === '$45')")
        self.page.reload()
        saved = self.page.evaluate("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0]")
        self.assertEqual({item['url'] for item in saved['results']}, {
            'https://www.depop.com/products/old/', 'https://www.depop.com/products/kept/',
            'https://www.depop.com/products/new/',
        })
        self.assertTrue(saved['resultsDay'])
        self.assertIsNotNone(saved['resultsFilters'])
        self.assertEqual(self.errors, [])

    def test_saved_recommendations_from_other_sellers_are_not_restored(self):
        self.context.add_init_script("""
            if (!localStorage.getItem('fixture_ownership_seeded')) {
                localStorage.setItem('debot.pageWorkspaces.v1', JSON.stringify({tops: {sellerRows: [
                    {seller: 'fixture_one', results: [
                        {url: 'https://www.depop.com/products/correct/', seller: 'fixture_one', price: '$25'},
                        {url: 'https://www.depop.com/products/wrong/', seller: 'another_shop', price: '$30'}
                    ]}
                ]}}));
                localStorage.setItem('fixture_ownership_seeded', '1');
            }
        """)
        self.page.goto(self.url)
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].results.length === 1")
        self.assertEqual(self.page.locator('.result-card').count(), 1)
        results = self.page.evaluate("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].results")
        self.assertEqual(results[0]['seller'], 'fixture_one')
        self.assertEqual(self.errors, [])

    def test_previous_day_or_changed_filter_results_are_not_merged(self):
        self.context.route('**/api/search/stream', lambda route: route.fulfill(
            content_type='text/event-stream', body=''.join(self.event(e) for e in [
                {'type': 'match', 'item': {'url': 'https://www.depop.com/products/current/', 'price': '$30'}},
                {'type': 'done', 'processed': 1, 'total': 1, 'matches': 1},
            ]),
        ))
        self.page.goto(self.url)
        search = self.page.get_by_role('button', name='Search', exact=True).first
        search.click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].processed")
        for change in ('date', 'fit', 'price', 'legacy-price'):
            self.page.evaluate("""(change) => {
                const key = 'debot.pageWorkspaces.v1';
                const state = JSON.parse(localStorage.getItem(key));
                const row = state.tops.sellerRows[0];
                row.results.push({url: 'https://www.depop.com/products/stale/', price: '$10'});
                if (change === 'date') row.resultsDay = '1990-1-1';
                if (change === 'fit') row.resultsFilters.p2pMin = '999';
                if (change === 'price') row.resultsFilters.maxPriceCad = '50';
                if (change === 'legacy-price') delete row.resultsFilters.maxPriceCad;
                localStorage.setItem(key, JSON.stringify(state));
            }""", change)
            self.page.reload()
            search.click()
            self.page.wait_for_function("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].processed")
            results = self.page.evaluate("JSON.parse(localStorage.getItem('debot.pageWorkspaces.v1')).tops.sellerRows[0].results")
            self.assertEqual([item['url'] for item in results], ['https://www.depop.com/products/current/'])
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
