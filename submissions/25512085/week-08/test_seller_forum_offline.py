"""Notice execution contracts: synthetic temporary feeds and mocked enqueue only."""
import contextlib
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import restock
import seller_forum
import store
from test_unified_buying_offline import BuyingFixture, NOW
from verify_cash_refactor_offline import select_temp_parent


def notice(thread='thread-1'):
    return {'action':{'post':'synthetic availability', 'as':'shop'},
            'skus':['G00000001'], 'demand_ts':NOW-10, 'thread':thread}


class NoticeExecutionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='seller-forum-', dir=select_temp_parent())
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.hub = {}
        self.units = [{'sku':'G00000001', 'cert':True}]
        self.catalog = {'G00000001':{'kind':'goods'}}
        self.planner = Mock(return_value=notice())
        self.enqueue = Mock(return_value=True)

    def feed(self, value, age=0):
        path = self.path / 'forum.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        os.utime(path, (NOW-age, NOW-age))
        return path

    def run_notice(self):
        seller_forum.queue_availability_notice(
            self.path, self.units, self.hub, self.catalog, NOW+.75,
            availability_plan=self.planner, queue_action=self.enqueue)

    def test_latest_feed_is_read_on_each_call(self):
        for value in ([{'id':1}], {'posts':[{'id':2}]}):
            self.feed(value)
            self.run_notice()
            self.planner.assert_called_with(self.units, self.hub, self.catalog, NOW+.75, value)

    def test_missing_stale_future_and_invalid_feeds_fall_back_to_empty(self):
        self.run_notice()  # Missing file.
        self.assertEqual(self.planner.call_args.args[-1], [])
        for age in (121, -10):
            with self.subTest(age=age):
                self.feed([{'id':1}], age)
                with patch.object(Path, 'read_text', side_effect=AssertionError('must not read stale feed')):
                    self.run_notice()
                self.assertEqual(self.planner.call_args.args[-1], [])
        path = self.feed([])
        path.write_text('{invalid', encoding='utf-8')
        os.utime(path, (NOW, NOW))
        self.run_notice()
        self.assertEqual(self.planner.call_args.args[-1], [])

    def test_feed_read_oserror_falls_back_to_empty(self):
        self.feed([])
        with patch.object(Path, 'read_text', side_effect=OSError('synthetic unreadable feed')):
            self.run_notice()
        self.assertEqual(self.planner.call_args.args[-1], [])

    def test_feed_freshness_includes_zero_and_120_seconds_only(self):
        value = [{'id':1}]
        for age in (0, 120, 121, -1):
            with self.subTest(age=age):
                self.feed(value, age)
                seller_forum.queue_availability_notice(
                    self.path, self.units, self.hub, self.catalog, NOW,
                    availability_plan=self.planner, queue_action=self.enqueue)
                self.assertEqual(self.planner.call_args.args[-1], value if 0<=age<=120 else [])

    def test_none_plan_does_not_enqueue_or_create_state(self):
        self.planner.return_value = None
        self.run_notice()
        self.enqueue.assert_not_called()
        self.assertEqual(self.hub, {})

    def test_unsuccessful_enqueue_leaves_existing_or_absent_state_unchanged(self):
        for hub in ({}, {'forum_state':{'last_shop_post_ts':1, 'availability_announced':{'old':2},
                                      'availability_replied_threads':['old']}}):
            with self.subTest(hub=hub):
                self.hub = copy.deepcopy(hub)
                self.enqueue.return_value = False
                self.run_notice()
                self.assertEqual(self.hub, hub)

    def test_enqueue_exception_propagates_without_reserving_slot(self):
        self.enqueue.side_effect = OSError('synthetic enqueue failure')
        with self.assertRaisesRegex(OSError, 'synthetic enqueue failure'):
            self.run_notice()
        self.assertEqual(self.hub, {})

    def test_planner_exception_is_not_swallowed(self):
        self.planner.side_effect = ValueError('synthetic planner failure')
        with self.assertRaisesRegex(ValueError, 'synthetic planner failure'):
            self.run_notice()
        self.enqueue.assert_not_called()
        self.assertEqual(self.hub, {})

    def test_success_records_only_after_enqueue_and_keeps_history_bounds(self):
        self.hub['forum_state'] = {
            'last_shop_post_ts':1,
            'availability_announced':{str(i):i for i in range(501)},
            'availability_replied_threads':[str(i) for i in range(301)],
            'unrelated':'preserved'}
        before = copy.deepcopy(self.hub)

        def enqueue(path, action):
            self.assertEqual(self.hub, before)
            self.assertEqual((path, action), (self.path, notice()['action']))
            return True

        self.enqueue.side_effect = enqueue
        self.run_notice()
        state = self.hub['forum_state']
        self.assertEqual(state['last_shop_post_ts'], NOW)
        self.assertEqual(state['availability_announced'],
                         {**{str(i):i for i in range(2,501)}, 'G00000001':NOW-10})
        self.assertEqual(state['availability_replied_threads'],
                         [str(i) for i in range(2,301)]+['thread-1'])
        self.assertEqual(state['unrelated'], 'preserved')

    def test_non_reply_success_does_not_create_reply_history(self):
        self.planner.return_value = notice(None)
        self.run_notice()
        self.assertNotIn('availability_replied_threads', self.hub['forum_state'])

    def test_default_dependencies_are_resolved_at_call_time(self):
        with patch.object(seller_forum.cash_policy, 'availability_plan', self.planner), \
                patch.object(seller_forum, 'queue_forum_action', self.enqueue):
            seller_forum.queue_availability_notice(self.path, [], self.hub, {}, NOW)
        self.planner.assert_called_once_with([], self.hub, {}, NOW, [])
        self.enqueue.assert_called_once_with(self.path, notice()['action'])


class RestockNoticeBoundaryTests(BuyingFixture, unittest.TestCase):
    def setUp(self):
        BuyingFixture.setUp(self)
        self.hub.pop('forum_state')

    def test_finish_branches_keep_monkeypatch_boundary_and_log_order(self):
        for branch in ('outside_window', 'round_end', 'first_window', 'normal'):
            with self.subTest(branch=branch):
                self.hub.clear()
                self.hub.update(store.default_hub())
                self.hub.pop('forum_state')
                self.stderr.seek(0)
                self.stderr.truncate()
                self.inv['restock'] = {'opens_at':NOW-20, 'closes_at':NOW+160}
                if branch == 'outside_window':
                    self.inv['restock'] = {}
                if branch == 'first_window':
                    window = str(restock.supply_window(self.inv))
                    self.hub['restock_windows'] = {window:{'blocked':True}}

                def enqueue(path, action):
                    self.assertEqual(self.stderr.getvalue(), '')
                    self.assertNotIn('forum_state', self.hub)
                    return True

                with patch.object(restock, 'ROUND_END_TS', NOW if branch == 'round_end' else NOW+10_000_000), \
                        patch.object(restock.cash_policy, 'availability_plan', return_value=notice()) as planner, \
                        patch.object(restock, 'queue_forum_action', side_effect=enqueue) as queue:
                    result = restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [],
                                                   inventory_fresh=True)
                planner.assert_called_once_with([], self.hub, self.catalog, NOW, [])
                queue.assert_called_once_with(self.path, notice()['action'])
                self.assertEqual(self.hub['forum_state']['last_shop_post_ts'], NOW)
                self.assertEqual(result, {'buy':[]})
                self.assertEqual(self.diag().get('stop'), None if branch == 'normal' else branch)

    def test_inventory_not_fresh_skips_all_notice_work(self):
        with patch.object(restock, 'queue_availability_notice') as helper, \
                patch.object(restock.cash_policy, 'availability_plan') as planner:
            restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [], inventory_fresh=False)
        helper.assert_not_called()
        planner.assert_not_called()
        self.assertNotIn('forum_state', self.hub)

    def test_real_policy_replies_outside_window_then_suppresses_duplicate(self):
        sku = self.item('G00000001')
        self.stock(sku)
        self.inv['units'][0]['cert'] = True
        self.inv['restock'] = {}
        feed = self.path / 'forum.json'
        feed.write_text(json.dumps([{'id':123, 'ts':NOW, 'role':'buyer',
                                     'owner':'outside', 'text':'want to buy '+sku}]), encoding='utf-8')
        os.utime(feed, (NOW, NOW))
        with patch.object(restock, 'queue_forum_action', return_value=True) as queue:
            for _ in range(2):
                self.assertEqual(restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [],
                                                      inventory_fresh=True), {'buy':[]})
        queue.assert_called_once()
        self.assertEqual(queue.call_args.args[1]['thread'], 123)
        self.assertEqual(self.hub['forum_state']['availability_announced'], {sku:NOW})
        self.assertEqual(self.hub['forum_state']['availability_replied_threads'], ['123'])
        self.assertEqual(self.diag()['stop'], 'outside_window')

    def test_enqueue_failure_still_logs_and_returns_but_exception_prevents_both(self):
        with patch.object(restock.cash_policy, 'availability_plan', return_value=notice()), \
                patch.object(restock, 'queue_forum_action', return_value=False):
            self.assertEqual(restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [],
                                                  inventory_fresh=True), {'buy':[]})
        self.assertNotIn('forum_state', self.hub)
        self.assertTrue(self.stderr.getvalue())
        self.stderr.seek(0)
        self.stderr.truncate()
        with patch.object(restock.cash_policy, 'availability_plan', return_value=notice()), \
                patch.object(restock, 'queue_forum_action', side_effect=OSError('synthetic failure')):
            with self.assertRaisesRegex(OSError, 'synthetic failure'):
                restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [], inventory_fresh=True)
        self.assertEqual(self.stderr.getvalue(), '')
        self.assertNotIn('forum_state', self.hub)

    def test_build_keeps_notice_within_hub_lock_outside_supply_window(self):
        self.item()
        self.inv['restock'] = {}
        inventory = self.path / 'inventory.json'
        inventory.write_text('{}', encoding='utf-8')
        os.utime(inventory, (NOW, NOW))
        locked = []

        @contextlib.contextmanager
        def mutate():
            locked.append(True)
            try:
                yield self.hub
            finally:
                locked.pop()

        def enqueue(path, action):
            self.assertEqual(locked, [True])
            self.assertEqual(self.stderr.getvalue(), '')
            return True

        with patch.object(restock, 'load_catalog', return_value=self.catalog), \
                patch.object(restock, 'load_inventory', return_value=self.inv), \
                patch.object(restock, 'load_hub', return_value=self.hub), \
                patch.object(restock, 'mutate_hub', side_effect=mutate), \
                patch.object(restock, 'fetch_ledger_rows', return_value=[]), \
                patch.object(restock, 'fetch_own_shop_urls', return_value=[]), \
                patch.object(restock, 'fetch_public_buying_data', return_value=None), \
                patch.object(restock.cash_policy, 'availability_plan', return_value=notice()), \
                patch.object(restock, 'queue_forum_action', side_effect=enqueue) as queue:
            self.assertEqual(restock.build_restock_plan(self.path), {'buy':[]})
        queue.assert_called_once()
        self.assertFalse(locked)
        self.assertEqual(self.diag()['stop'], 'outside_window')

    def test_shop_availability_notice_attaches_ledger_refs(self):
        sku = self.item('G00000001')
        self.stock(sku)
        self.inv['units'][0]['cert'] = True
        self.hub['seller_cash'] = {
            'sales': {
                '101': {'ts': NOW - 50, 'sku': sku, 'amount': 18000, 'own': True},
                '102': {'ts': NOW - 30, 'sku': 'G99999999', 'amount': 20000, 'own': True},
            },
            'market_sales': {
                sku: {'201': NOW - 40, '101': NOW - 50},
            },
        }
        self.hub['demand_radar'] = {
            'sku_inquiries': {sku.lower(): [{'ts': NOW - 10}]},
            'recent_refs': [301, 302, 303],
        }
        plan = restock.cash_policy.availability_plan(self.inv['units'], self.hub, self.catalog, NOW)
        self.assertIsNotNone(plan)
        self.assertEqual(plan['action']['refs'], [101, 201, 102, 303, 302])
        self.assertIn('인증 A등급', plan['action']['post'])
        self.assertIn('refs', plan['action']['post'])


if __name__ == '__main__':
    unittest.main()
