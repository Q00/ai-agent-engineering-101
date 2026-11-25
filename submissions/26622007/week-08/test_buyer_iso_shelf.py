"""Food expiry eligibility follows the existing checkout policy, not price reading."""
import unittest
from unittest import mock
import market_buyer as m


class Expiry(unittest.TestCase):
    def test_real_short_food_quote_is_refused_before_any_offer(self):
        # Actual 11/10 18:31 quote, later refused by checkout at 152.14 < 180.1 hours.
        text = ('Inventory condition: C. Expires at 2026-11-16T17:39:25+00:00. '
                'Confirm the certificate at checkout; condition can change while negotiating. Warranty: 0 hours.\n'
                '2026년 햅쌀 임금님표 이천쌀 4kg 상등급 소포장쌀 단일품종: asking 18550. Send your offer as a number.')
        order = {'kind': 'food', 'use_within_hours': 180.0}
        with mock.patch.object(m.time, 'time', return_value=1794303059.0):
            self.assertIsNotNone(m.quote_refused(text, order))

    @staticmethod
    def quote(expiry='2026-11-10T11:00:00+00:00', warranty=0):
        return (f'Inventory condition: A. Expires at {expiry}. Confirm the certificate at checkout; '
                f'condition can change while negotiating. Warranty: {warranty} hours.\nCounteroffer: 12000.')

    def test_checkout_boundary_and_timezone_offsets(self):
        # 19:00 KST -> expiry20:00 KST, exactly 0.9 + 0.1h is eligible.
        order = {'kind': 'food', 'use_within_hours': 0.9}
        for expiry in ('2026-11-10T11:00:00+00:00', '2026-11-10T20:00:00+09:00', '2026-11-10T11:00:00Z'):
            with self.subTest(expiry=expiry), mock.patch.object(m.time, 'time', return_value=1794304800.0):
                self.assertIsNone(m.quote_refused(self.quote(expiry), order))
            with mock.patch.object(m.time, 'time', return_value=1794304800.01):
                self.assertIsNotNone(m.quote_refused(self.quote(expiry), order))

    def test_ambiguous_truncated_or_unbound_dates_keep_the_existing_path(self):
        order = {'kind': 'food', 'use_within_hours': 48.0}
        good = self.quote()
        cases = [self.quote('2026-11-10T11:00:00'), self.quote('2026-02-30T11:00:00+00:00'),
                 self.quote('2026-11-10T11:00:00+99:00'),
                 self.quote('2026-11-10T11:00:00+00:60'), self.quote('2026-11-10T11:00:00+00:99'),
                 self.quote('2026-11-10T11:00:00-00:60'), self.quote('2026-11-10T11:00:00-00:99'), good[:120],
                 good + '\nExpires at 2026-12-10T11:00:00+00:00.', 'Example: ' + good]
        with mock.patch.object(m.time, 'time', return_value=1794304800.0):
            for text in cases:
                with self.subTest(text=text):
                    self.assertIsNone(m.quote_refused(text, order))

    def test_bad_clocks_do_not_reclassify_unknown_quotes(self):
        order = {'kind': 'food', 'use_within_hours': 48.0}
        for now in (True, '0', float('nan'), float('inf'), -float('inf'), 10**400):
            with self.subTest(now=now), mock.patch.object(m.time, 'time', return_value=now):
                self.assertIsNone(m.quote_refused(self.quote(), order))

    def test_existing_relative_hours_and_goods_guards_are_unchanged(self):
        with mock.patch.object(m.time, 'time', return_value=1794304800.0):
            self.assertIsNotNone(m.quote_refused('12 h of shelf life left', {'kind': 'food', 'use_within_hours': 48.0}))
            # Contradictory/updated freshness stays on the old conservative max-hours policy.
            self.assertIsNone(m.quote_refused(self.quote() + ' 72 h of shelf life left', {'kind': 'food', 'use_within_hours': 48.0}))
            self.assertIsNone(m.shelf_refused(self.quote(), {'kind': 'goods', 'use_within_hours': 48.0}))
            self.assertIsNotNone(m.quote_refused(self.quote(), {'kind': 'goods'}))
            self.assertIsNone(m.quote_refused(self.quote(warranty=6), {'kind': 'goods'}))
            self.assertIsNone(m.quote_refused(self.quote(), {'kind': 'food', 'use_within_hours': 0}))


class DatedCatalogueQuote(unittest.TestCase):
    LABEL = '풀무원 워터루틴 무라벨 생수 500mL X 60병(무라벨/유라벨 랜덤발송)'
    SKU = 'Gdf79cd07'
    # Complete logged response, uniquely joined by SKU/prefix/ask to the selected 20:55:31 quote.
    TEXT = (LABEL + ' (' + SKU + '). Food expiry: 2026-11-15T11:26:51+00:00; '
            'remaining shelf life is confirmed at checkout. At quote: 119.52 h remaining (conservative estimate). '
            'Catalog list price: 15200. No breakage warranty offered. One unit at the quoted price; '
            'check the certificate and terms at checkout. Market certificate included. Asking price: 7904. ')

    def quote(self, text=None, label=None, sku=None, order=None, now=1794311731.0):
        task = {'id': 'fixture-task', 'status': {'state': 'TASK_STATE_INPUT_REQUIRED',
                'message': {'parts': [{'text': self.TEXT if text is None else text}], 'metadata': {'ask': 7904}}}}
        card = {'supportedInterfaces': [{'protocolBinding': 'JSONRPC', 'protocolVersion': '1.0',
                                        'url': 'https://fixture.invalid/a2a'}]}
        out = {}
        with mock.patch.object(m, 'serves_acp', return_value=True), \
                mock.patch.object(m.httpx, 'get') as get, \
                mock.patch.object(m, 'buyer_send', return_value=task), \
                mock.patch.object(m.time, 'time', return_value=now):
            get.return_value.json.return_value = card
            m.quote({'name': 'fixture-shop', 'url': 'https://fixture.invalid'},
                    self.LABEL if label is None else label, self.SKU if sku is None else sku,
                    {'kind': 'food', 'use_within_hours': 360.0} if order is None else order, out)
        return out['fixture-shop']

    def test_real_dated_catalogue_quote_is_ineligible_before_selection(self):
        quote = self.quote()
        self.assertIsNotNone(quote['refused'])
        self.assertEqual(quote['ask'], 7904)
        self.assertEqual(quote['ask_src'], 'metadata')

    def test_a_conflicting_longer_estimate_keeps_the_existing_path(self):
        text = self.TEXT.replace('119.52 h remaining', '500 h remaining')
        self.assertIsNone(self.quote(text)['refused'])

    def test_sufficient_and_exact_boundary_quotes_keep_their_metadata_price(self):
        expiry = 1794742011.0
        for now in (expiry - 500 * 3600, expiry - 360.1 * 3600):
            with self.subTest(now=now):
                quote = self.quote(now=now)
                self.assertIsNone(quote['refused'])
                self.assertEqual((quote['ask'], quote['ask_src']), (7904, 'metadata'))
        self.assertIsNotNone(self.quote(now=expiry - 360.1 * 3600 + .01)['refused'])

    def test_incomplete_unbound_or_ambiguous_templates_keep_the_existing_path(self):
        for text in (self.TEXT[:120], self.TEXT[:300], 'Example: ' + self.TEXT,
                     self.TEXT + ' Food expiry: 2026-12-15T11:26:51+00:00',
                     self.TEXT.replace('+00:00', ''),
                     self.TEXT.replace('+00:00', '+00:60'), self.TEXT.replace('+00:00', '-00:99'),
                     self.TEXT.replace('2026-11-15', '2026-02-30'),
                     self.TEXT.replace('2026-11-15T11:26:51', '2026-11-15T24:26:51')):
            with self.subTest(text=text):
                self.assertIsNone(self.quote(text)['refused'])
        self.assertIsNone(self.quote(sku='G00000000')['refused'])
        self.assertIsNone(self.quote(label='another product')['refused'])
        self.assertIsNone(m.quote_refused(self.TEXT, {'kind': 'food', 'use_within_hours': 360.0}))

    def test_conservative_estimate_alone_and_bad_clocks_are_not_eligibility_evidence(self):
        no_date = self.TEXT.replace('2026-11-15T11:26:51+00:00', 'unknown')
        self.assertIsNone(self.quote(no_date)['refused'])
        for now in (None, True, '0', float('nan'), float('inf'), 10**400):
            with self.subTest(now=now):
                self.assertIsNone(self.quote(now=now)['refused'])

    def test_direct_talk_without_quotes_refuses_before_first_offer(self):
        import contextlib
        import io
        import json
        import os
        import sys
        import tempfile
        import buyer
        sent = []
        now = 1794311731.0
        task = {'id': 'fixture-task', 'status': {'state': 'TASK_STATE_INPUT_REQUIRED',
                'message': {'parts': [{'text': self.TEXT}], 'metadata': {'ask': 7904}}}}
        cancelled = {'id': 'fixture-task', 'status': {'state': 'TASK_STATE_CANCELED'}}
        def negotiate(url, label, budget, note, request, **kwargs):
            return buyer.talk(url, task, label, budget, note,
                    {'history': [], 'agreed': None, 'holds': 0, 'receipt': None},
                    stop_if=kwargs['stop_if'], take_at=kwargs.get('take_at'))
        with tempfile.TemporaryDirectory() as d:
            shops, order_path = os.path.join(d, 'shops.json'), os.path.join(d, 'order.json')
            with open(shops, 'w') as f:
                json.dump([{'name': 'fixture-shop', 'url': 'https://fixture.invalid', 'reviews': [],
                            'offers': [{'sku': self.SKU, 'name': self.LABEL, 'qty': 1}]}], f)
            with open(order_path, 'w') as f:
                json.dump({'order': 'fixture-order', 'kind': 'food', 'category': '식품>음료', 'budget': 28630,
                           'acceptable': [self.SKU], 'use_within_hours': 360.0}, f)
            with open(os.path.join(d, 'catalog.json'), 'w') as f:
                json.dump({self.SKU: {'kind': 'food', 'category': '식품>음료', 'name': self.LABEL,
                                     'list_price': 15200}}, f)
            with mock.patch.object(m, 'MEMORY', os.path.join(d, 'memory.json')), \
                    mock.patch.object(m, 'SETTLED', os.path.join(d, 'settled.jsonl')), \
                    mock.patch.object(m, 'AGENT_DIR', d), mock.patch.object(m, 'SAVED_SHARE', .3), \
                    mock.patch.dict(m._SW_CTX, clear=True), mock.patch.object(m, 'STARTED', now-.01), \
                    mock.patch.object(m.time, 'time', return_value=now), \
                    mock.patch.object(m, 'market_round', return_value=3), \
                    mock.patch.object(m, 'serves_acp', return_value=True), \
                    mock.patch.object(m, 'buyer_cfg', return_value={'quote_shops': 1}), \
                    mock.patch.object(m, 'choose', side_effect=lambda shops, *a, **k: (shops[0], 'fixture')), \
                    mock.patch.object(m, 'negotiate', side_effect=negotiate), \
                    mock.patch.object(buyer, 'send', side_effect=lambda url, text, *a: sent.append(text) or cancelled), \
                    mock.patch.object(buyer, 'rpc', return_value=cancelled), \
                    mock.patch.object(buyer, 'decide', return_value={'shop_price': 7904, 'action': 'walk_away', 'price': 0}), \
                    mock.patch.object(sys, 'argv', ['fixture', '--shops', shops, '--item', '음료',
                                                    '--budget', '28630', '--order', order_path]), \
                    contextlib.redirect_stdout(io.StringIO()):
                m.main()
        self.assertEqual(sent, [])

    def test_goods_and_existing_relative_shelf_checks_are_unchanged(self):
        item = (self.LABEL, self.SKU)
        self.assertIsNone(m.shelf_refused(self.TEXT, {'kind': 'goods', 'use_within_hours': 360.0}, item))
        self.assertIsNotNone(m.quote_refused(self.TEXT, {'kind': 'goods'}, item))
        self.assertIsNone(self.quote(order={'kind': 'food', 'use_within_hours': 0})['refused'])
        self.assertIsNotNone(m.quote_refused('12 h shelf life left', {'kind': 'food', 'use_within_hours': 48}, item))
        self.assertIsNone(m.quote_refused('500 h shelf life left', {'kind': 'food', 'use_within_hours': 48}, item))



if __name__ == '__main__':
    unittest.main()
