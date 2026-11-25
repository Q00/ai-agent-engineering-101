"""Price study (.97 M vs 1.00 M, SKU x grade) and grade warranty study (unit) ON together.

2x2 factorial through the real Seller quote, follow-up and receipt seams. Both
controllers, both journals, pricing, briefing and memory live in a temporary
root; run it in the isolated runner (no model, network or live state).
"""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest import mock

import sol_seller_margin as margin
import seller_warranty_trial
from test_sol_seller_margin import real_loop

CONTROLLERS = ('READER_TRIAL', 'CONCESSION_TRIAL', 'LATE_CONCESSION', 'OPENING_DEFAULT',
               'DRAIN', 'OPENING_CUE', 'LATE_SPEED', 'JEV_TRANSPORT', 'SHELF_REPLY',
               'EXACT_ACCEPT', 'EXACT_COUNTER', 'GRADE_PREFERENCE', 'FOOD_OPENING',
               'AT_FLOOR_EXACT', 'DEEP_LOWBALL', 'QUOTE_HOLD')
AS_IS = 'sold as is, without a warranty'
WITH_72 = 'comes with a 72 h warranty'


class Updater:
    quote = None
    rejected = None

    async def requires_input(self, message): self.quote = message
    async def reject(self, message): self.rejected = message


class Continue:
    failed_message = None
    completed = False
    receipt = None
    messages = ()

    def new_agent_message(self, **kw): return kw
    async def requires_input(self, message): self.messages = (*self.messages, message)
    async def failed(self, message): self.failed_message = message
    async def reject(self, message): self.failed_message = message
    async def add_artifact(self, parts, name): self.receipt = parts
    async def complete(self, message): self.completed = True


class FactorialTest(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.wconfig = root/'warranty.json'
        stack.enter_context(mock.patch.dict(os.environ, MARKET_DIR=str(root), PYTHONTZPATH='',
                                            SELLER_WARRANTY_TRIAL_CONFIG=str(self.wconfig)))
        import seller
        import shop_memory
        import fast_classify
        import harness
        self.seller, self.shop_memory = seller, shop_memory
        # Model boundaries fail the test; flags cached at import are forced offline.
        for target, name in ((seller, 'ask'), (harness, 'ask'), (fast_classify, 'jev_offer')):
            blocked = stack.enter_context(mock.patch.object(
                target, name, side_effect=AssertionError(f'factorial tests must not call {name}')))
            self.addCleanup(blocked.assert_not_called)
        stack.enter_context(mock.patch.object(harness, 'BACKEND', 'offline-factorial'))
        stack.enter_context(mock.patch.object(fast_classify, 'JEV_ON', False))
        self.trial = margin.MarginStudy(root/'price-study')
        stack.enter_context(mock.patch.object(seller, 'MARGIN_STUDY', self.trial))
        for name in CONTROLLERS:
            stack.enter_context(mock.patch.object(getattr(seller, name), 'root', root/name))
        pricing = dict(price=dict(min_profit_share=0, goods_break=.04,
                       high_median_up=dict(min_list=300000, factor=.97),
                       high_price_trial=dict(share=0)), warranty=dict(max_break=.5))
        (root/'pricing.json').write_text(json.dumps(pricing))
        stack.enter_context(mock.patch.object(shop_memory, 'PRICING', str(root/'pricing.json')))
        stack.enter_context(mock.patch.object(shop_memory, 'MEMORY', str(root/'memory.json')))
        stack.enter_context(mock.patch.object(shop_memory, 'MEMORY_R3', str(root/'memory_r3.json')))
        failures = list(seller.MEMORY_FAILURES)
        self.addCleanup(lambda: self.assertEqual(seller.MEMORY_FAILURES, failures))
        self.addCleanup(lambda: seller.flush_memory(strict=True))
        self.pricing = shop_memory.load_pricing()
        self.now = now = time.time()
        peers = [getattr(seller, name) for name in CONTROLLERS if name != 'DRAIN']
        baseline = margin.baseline_snapshot(self.pricing, peers)
        self.trial.root.mkdir()
        self.trial.write_controller(dict(
            enabled=True, version=margin.VERSION, study_id='factorial-price-study-01',
            epoch=now-1, start=now-1, end=now+43199, salt='factorial-price-salt-01',
            disk_snapshot_sha256=margin.DISK_SNAPSHOT_SHA256, **margin.sol_source_load.proof()['claim'],
            baseline=baseline, baseline_sha256=margin.digest(baseline)))
        self.wcfg = dict(study='grade-warranty-v1', version=1, epoch='factorial-fixture-01',
                         salt='factorial-warranty-salt-01', enabled=True, share=0.5,
                         start_at=now-1, stop_at=now+43199, grades=['C'])
        self.write_warranty()
        self.cells = self.find_cells()

    def write_warranty(self, **changes):
        self.wcfg.update(changes)
        pending = self.wconfig.with_suffix('.pending')
        pending.write_text(json.dumps(self.wcfg))
        os.replace(pending, self.wconfig)  # atomic, like the runbook's OFF writer

    def unit(self, sku, unit):
        return dict(unit=unit, sku=sku, grade='C', kind='goods', cost=100000, list_price=500000,
                    name='anonymous goods', category='goods', supplied_at=self.now, expires_at=None)

    def price_arm(self, sku):
        return 'B' if margin.hashlib.sha256(json.dumps(
            ['factorial-price-salt-01', sku, 'C'], separators=(',', ':')).encode()).digest()[0] < 128 else 'A'

    def warranty_arm(self, sku, unit):
        u = self.unit(sku, unit)
        row = seller_warranty_trial.Trial()._assignment(
            'probe', dict(kind='goods', id=sku, unit=unit), u, False, self.now, self.wcfg)
        return row['arm']

    def find_cells(self):
        """One SKU per price arm, one unit per warranty arm inside each SKU (split plot)."""
        skus = {}
        for n in range(200):
            skus.setdefault(self.price_arm(f'G{n:08x}'), f'G{n:08x}')
            if len(skus) == 2:
                break
        cells = {}
        for parm, sku in skus.items():
            for n in range(200):
                unit = f'u-{sku}-{n:04d}'
                cells.setdefault((parm, self.warranty_arm(sku, unit)), (sku, unit))
                if (parm, 'control') in cells and (parm, 'treatment') in cells:
                    break
        self.assertEqual(set(cells), {(p, w) for p in 'AB' for w in ('control', 'treatment')})
        (self.root/'briefing.json').write_text(json.dumps(dict(
            skus={sku: dict(price_ratio=.6) for sku in skus.values()})))
        return cells

    def open(self, cell, text=None):
        sku, unit = self.cells[cell]
        u = self.unit(sku, unit)
        stock = self.seller.StockBook(lambda: dict(round=3, units=[u]))
        shop = self.seller.Seller(None, 'anonymous-shop', stock=stock)
        task = SimpleNamespace(id=str(uuid.uuid4()))
        up = Updater()
        real_loop(shop.open_from_stock(task, text or sku, up, lambda text, price=None: (text, price)))
        self.assertIsNone(up.rejected)
        return shop, task.id, up.quote

    def followup(self, shop, task, text):
        follow = Continue()
        context = SimpleNamespace(current_task=SimpleNamespace(id=task, context_id='fixture'), message=None)
        with mock.patch.object(self.seller, 'get_message_text', return_value=text), \
                mock.patch.object(self.seller, 'TaskUpdater', return_value=follow), \
                mock.patch.object(self.seller, 'load_shop_key', return_value=None), \
                mock.patch.object(self.seller, 'decide_turn', return_value=dict(
                    buyer_offer=0, action='counter', price=0, message='Fixture reply.')):
            real_loop(shop._execute(context, None))
        self.assertIsNone(follow.failed_message)
        return follow

    def receipt(self, follow):
        from google.protobuf.json_format import MessageToDict
        receipt = MessageToDict(follow.receipt[0].data)
        receipt['price'] = int(receipt['price'])
        return receipt

    def price_events(self, task, event=None):
        return [e for e in self.trial.events() if e['task'] == task and event in (None, e['event'])]

    def warranty_rows(self, task):
        path = self.root/'measure'/'seller_warranty_trial.jsonl'
        if not path.exists():
            return []
        return [r for r in map(json.loads, path.read_text().splitlines()) if r['task'] == task]

    def test_four_cells_attribute_both_factors_independently(self):
        asks = {}
        for cell in sorted(self.cells):
            parm, warm = cell
            with self.subTest(cell=cell):
                shop, task, (text, ask) = self.open(cell)
                deal = shop.open[task]
                asks[cell] = ask
                self.assertEqual(deal['sol_margin']['arm'], parm)
                self.assertTrue(deal['sol_margin']['eligible'])
                self.assertEqual(deal['warranty_trial']['arm'], warm)
                self.assertEqual(AS_IS in text, warm == 'treatment')
                self.assertEqual(WITH_72 in text, warm == 'control')
                ready, = self.price_events(task, 'quote_ready')
                self.assertEqual(ready['actual_treatment'], parm == 'B')
                self.assertEqual(ready['final_price'], ask)
                self.assertEqual(ready['warranty_cell'], dict(
                    study='grade-warranty-v1', version=1, epoch='factorial-fixture-01', eligible=True,
                    assigned_arm=warm, arm=warm, reason='grade_eligible',
                    offered_hours=0 if warm == 'treatment' else 72,
                    promised_hours=None if warm == 'treatment' else 72))
                self.assertEqual(len(self.price_events(task, 'quoted')), 1)
                row, = self.warranty_rows(task)
                self.assertEqual((row['arm'], row['unit'], row['sku']), (warm, *self.cells[cell][::-1]))
                receipt = shop.stock_receipt(task, deal, ask)
                self.assertEqual(receipt.get('warranty_hours'), None if warm == 'treatment' else 72)
        # Warranty never moves the price; the price arm alone does.
        for warm in ('control', 'treatment'):
            self.assertGreater(asks[('B', warm)], asks[('A', warm)])
        self.assertEqual(asks[('A', 'control')], asks[('A', 'treatment')])
        self.assertEqual(asks[('B', 'control')], asks[('B', 'treatment')])

    def test_price_on_warranty_off_keeps_72h_and_price_treatment(self):
        self.write_warranty(enabled=False)
        shop, task, (text, ask) = self.open(('B', 'treatment'))
        self.assertIn(WITH_72, text)
        self.assertNotIn('warranty_trial', shop.open[task])
        ready, = self.price_events(task, 'quote_ready')
        self.assertTrue(ready['actual_treatment'])
        self.assertIsNone(ready['warranty_cell'].get('arm'))
        self.assertEqual(ready['warranty_cell']['promised_hours'], 72)
        self.assertEqual(self.warranty_rows(task), [])

    def test_warranty_on_price_off_keeps_baseline_price_and_as_is(self):
        _, _, (_, ask_a) = self.open(('A', 'treatment'))
        _, _, (_, ask_b) = self.open(('B', 'treatment'))
        self.trial.write_controller({'enabled': False})
        shop, task, (text, ask) = self.open(('B', 'treatment'))
        self.assertIn(AS_IS, text)
        self.assertNotIn('sol_margin', shop.open[task])
        self.assertEqual(ask, ask_a)  # OFF = the .97 baseline, not the B price
        self.assertNotEqual(ask, ask_b)
        self.assertEqual(self.price_events(task), [])
        row, = self.warranty_rows(task)
        self.assertEqual(row['arm'], 'treatment')

    def test_first_quote_warranty_request_is_control_and_price_arm_unchanged(self):
        sku = self.cells[('B', 'treatment')][0]
        shop, task, (text, ask) = self.open(('B', 'treatment'), text=f'{sku} with a 6 hour refund')
        deal = shop.open[task]
        self.assertIn(WITH_72, text)
        self.assertEqual((deal['warranty_trial']['arm'], deal['warranty_trial']['reason']),
                         ('control', 'buyer_requested'))
        ready, = self.price_events(task, 'quote_ready')
        self.assertTrue(ready['actual_treatment'])
        self.assertEqual(ready['warranty_cell']['reason'], 'buyer_requested')

    def test_followup_request_restores_warranty_and_keeps_price_promise(self):
        shop, task, (text, ask) = self.open(('B', 'treatment'))
        self.assertIn(AS_IS, text)
        self.followup(shop, task, 'I need 6 hrs refund if broken')
        deal = shop.open[task]
        self.assertEqual(deal['warranty'], 72)
        self.assertEqual(self.warranty_rows(task)[-1]['reason'], 'followup_warranty_request')
        follow = self.followup(shop, task, str(deal['ask']))
        self.assertTrue(follow.completed)
        receipt = self.receipt(follow)
        self.assertEqual(receipt['warranty_hours'], 72)
        self.assertEqual(receipt['price'], deal['ask'])
        self.seller.flush_memory(strict=True)
        agreed, = self.price_events(task, 'local_agreed')
        self.assertEqual((agreed['warranty_cell']['assigned_arm'], agreed['warranty_cell']['arm'],
                          agreed['warranty_cell']['promised_hours']), ('treatment', 'control', 72))
        self.assertEqual(agreed['price'], receipt['price'])

    def test_both_off_after_quote_keep_price_promise_and_restore_warranty(self):
        shop, task, (text, ask) = self.open(('B', 'treatment'))
        self.trial.write_controller({'enabled': False})
        self.write_warranty(enabled=False)
        follow = self.followup(shop, task, str(ask))
        self.assertTrue(follow.completed)
        receipt = self.receipt(follow)
        self.assertEqual(receipt['price'], ask)  # the published B price stays binding
        self.assertEqual(receipt['warranty_hours'], 72)  # OFF restores the normal warranty path

    def test_treatment_cell_accept_ships_as_is_at_b_price(self):
        shop, task, (text, ask) = self.open(('B', 'treatment'))
        follow = self.followup(shop, task, str(ask))
        self.assertTrue(follow.completed)
        receipt = self.receipt(follow)
        self.assertEqual(receipt['price'], ask)
        self.assertNotIn('warranty_hours', receipt)
        self.seller.flush_memory(strict=True)
        agreed, = self.price_events(task, 'local_agreed')
        self.assertEqual((agreed['warranty_cell']['arm'], agreed['warranty_cell']['promised_hours']),
                         ('treatment', None))

    def test_source_attestation_covers_the_warranty_module(self):
        self.assertIn('seller_warranty_trial.py', margin.SOURCE_FILES)
        self.assertIn('seller_warranty_trial.py', margin.DISK_SNAPSHOT)
        self.assertIn(seller_warranty_trial.__name__, margin.sol_source_load._context.codes)


if __name__ == '__main__':
    unittest.main()
