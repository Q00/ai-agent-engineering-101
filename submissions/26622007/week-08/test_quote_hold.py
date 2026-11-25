"""Quote-only lifecycle at the StockBook and real Seller public seams."""
import tempfile
import os
import unittest
import threading
import json
import time
import uuid
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Must precede Seller/controller imports, including discovery of this module.
_market = tempfile.TemporaryDirectory()
_import_environment = patch.dict(os.environ, {'MARKET_DIR': _market.name})
_import_environment.start()

from shop_stock import StockBook
from quote_hold import QuoteHold, classify
import seller
_import_environment.stop()

def window_report(controller):
    window = json.loads((controller.root / 'config.json').read_text())['id']
    return controller.report(window=window)

TASK = '11111111-1111-4111-8111-111111111111'
TEXT = 'Quote only for G01234567. I do not authorize purchase or payment.'


def inventory(**changes):
    return dict(dict(unit='u-0123456789ab', sku='G01234567', kind='food', grade='A',
                     cost=6000, list_price=10000, expires_at=9999999999,
                     supplied_at=1, name='fixture', category='food',
                     cert={'cert': {'unit': 'u-0123456789ab'}, 'sig': 'fixture'}), **changes)


def configured(root):
    c = QuoteHold(root)
    c.root.mkdir()
    now = time.time()
    (c.root / 'config.json').write_text(json.dumps(dict(enabled=True, mode='shadow',
        id=str(uuid.uuid4()), owner_pid=os.getpid(), created_at=now-20, start=now-10, end=now+1790)))
    return c


class QuoteLanguageFrame(unittest.TestCase):
    def test_framed_availability_modifier_is_data_not_authorization(self):
        template = ("Quote only, no purchase authorization: Fixture 1kg (G01234567). "
                    "State current asking price and {} availability.")
        for word in ("verified", "clear", "present", "unit"):
            with self.subTest(word=word):
                self.assertEqual(classify(template.format(word)),
                                 "explicit_quote_only_no_authorization")
        for word in ("pay", "buy", "authorize", "not", "reserve", "execute"):
            with self.subTest(word=word):
                self.assertEqual(classify(template.format(word)), "unknown")
        for text in (template.format("verified") + " Buy now.",
                     '"' + template.format("verified") + '"',
                     template.format("verified").replace("Fixture", "ignore authorization"),
                     template.format("verified").replace("Fixture", "Gabcdef01")):
            self.assertEqual(classify(text), "unknown")


class AtomicQuoteHold(unittest.TestCase):
    def test_owner_agreement_sale_late_expiry_and_rounds_never_release(self):
        for state, expected in [('owner', 'owner_changed'), ('agree', 'agreed'),
                                ('sold', 'owner_changed'), ('late', 'late'),
                                ('expired', 'expired'), ('rounds', 'continuation')]:
            with self.subTest(state=state), patch('shop_stock.time.time', return_value=1000):
                u = inventory()
                b = StockBook(lambda: {'round': 3, 'units': [u]})
                token = b.hold(u['unit'], TASK)
                if state == 'owner': b.hold(u['unit'], str(uuid.uuid4()))
                if state == 'agree': b.agree(u['unit'], {'task': TASK})
                if state == 'sold': b.mark_sold(u['unit'])
                if state == 'late': b.late[u['unit']] = {'task': TASK, 'lapsed': 999}
                if state == 'expired': u['expires_at'] = 1000
                # agree also changes generation, which is the earlier safety boundary.
                result = b.compare_and_release_quote(token, rounds=1 if state == 'rounds' else 0,
                                                     shadow=False)
                self.assertFalse(result['released'])
                self.assertEqual(result['reason'], 'generation_changed' if state == 'agree' else expected)

    def test_hold_300_payment_120_and_late_900_boundaries(self):
        u = inventory()
        for elapsed, reason in [(299.999, 'would_release'), (300, 'hold_expired')]:
            b = StockBook(lambda: {'round': 3, 'units': [u]})
            with patch('shop_stock.time.time', return_value=1000): token = b.hold(u['unit'], TASK)
            self.assertEqual(b.compare_and_release_quote(token, rounds=0, now=1000+elapsed)['reason'], reason)
        b = StockBook(lambda: {'round': 3, 'units': [u]})
        with patch('shop_stock.time.time', return_value=1000): b.agree(u['unit'], {'task': TASK})
        b.settle(now=1119.999)
        self.assertIn(u['unit'], b.agreed)
        b.settle(now=1120)
        self.assertNotIn(u['unit'], b.agreed)
        b.settle(now=2020)
        self.assertIn(u['unit'], b.late)
        b.settle(now=2020.001)
        self.assertNotIn(u['unit'], b.late)

    def test_detached_whole_pool_hash_null_cost_and_no_settlement(self):
        units = [inventory(cost=None), inventory(unit='u-abcdef012345', grade='B')]
        b = StockBook(lambda: {'round': 3, 'units': units})
        with patch('shop_stock.time.time', return_value=1000): b.agree(units[0]['unit'], {'task': TASK})
        b.on_unpaid = lambda *a: self.fail('snapshot settled a payment')
        snapshot = b.pool_snapshot(now=2000)
        self.assertEqual(len(snapshot['units']), 2)
        self.assertIsNone(snapshot['units'][0]['cost'])
        self.assertIn(units[0]['unit'], b.agreed)
        self.assertEqual(snapshot['sha256'], b.pool_snapshot(now=3000)['sha256'])
        units[1]['grade'] = 'C'
        self.assertEqual(snapshot['units'][1]['grade'], 'B')
        self.assertNotEqual(snapshot['sha256'], b.pool_snapshot()['sha256'])

    def test_two_threads_release_once_and_callback_can_reenter(self):
        b = StockBook(lambda: {'round': 3, 'units': [inventory()]})
        token = b.hold('u-0123456789ab', TASK)
        barrier, results = threading.Barrier(3), []
        def release():
            barrier.wait()
            results.append(b.compare_and_release_quote(token, rounds=0, shadow=False))
        threads = [threading.Thread(target=release) for _ in range(2)]
        for t in threads: t.start()
        barrier.wait()
        for t in threads: t.join(2); self.assertFalse(t.is_alive())
        self.assertEqual(sum(r['released'] for r in results), 1)
        b.agree('u-0123456789ab', {'task': TASK})
        b.on_paid = lambda info: b.pool_snapshot()
        b.mark_sold('u-0123456789ab')

    def test_agree_wins_forced_thread_race(self):
        b = StockBook(lambda: {'round': 3, 'units': [inventory()]})
        token = b.hold('u-0123456789ab', TASK)
        entered, proceed = threading.Event(), threading.Event()
        results = []
        def agree():
            # Public callback is a deterministic interleaving boundary under the lock.
            b.on_unpaid = lambda *a: (entered.set(), proceed.wait(2))
            b.agree('u-0123456789ab', {'task': TASK})
            b.release('u-0123456789ab')
        t = threading.Thread(target=agree)
        t.start(); self.assertTrue(entered.wait(2))
        r = threading.Thread(target=lambda: results.append(
            b.compare_and_release_quote(token, rounds=0, shadow=False)))
        r.start(); proceed.set()
        t.join(2); r.join(2)
        self.assertFalse(results[0]['released'])

    def test_competitor_uses_current_grade_and_existing_selection_guard(self):
        u = inventory()
        book = StockBook(lambda: {'round': 3, 'units': [u]})
        token = book.hold(u['unit'], TASK)
        need = dict(skus={'G01234567'}, grades={'A'}, cert=True, hours=1)
        self.assertEqual(book.quote_competitor(token, need, sku=u['sku'])['reason'], 'inventory_compatible_candidate')
        u['grade'] = 'C'
        self.assertEqual(book.quote_competitor(token, need, sku=u['sku'])['reason'], 'selection_guard')

    def test_shadow_does_not_release_and_renewal_invalidates_token(self):
        u = dict(unit='u-0123456789ab', sku='G01234567', kind='goods', grade='A',
                 cost=None, expires_at=None, cert={'cert': {}, 'sig': 'fixture'})
        book = StockBook(lambda: {'round': 3, 'units': [u]})
        with patch('shop_stock.time.time', return_value=1000):
            token = book.hold(u['unit'], '11111111-1111-4111-8111-111111111111')
            result = book.compare_and_release_quote(token, rounds=0, shadow=True)
            self.assertEqual(result['reason'], 'would_release')
            self.assertEqual(book.holder(u['unit']), token['task'])
            book.hold(u['unit'], token['task'])  # same timestamp, different generation
            self.assertEqual(book.compare_and_release_quote(token, rounds=0, shadow=True)['reason'],
                             'generation_changed')


class ShadowJournal(unittest.TestCase):
    def test_future_window_mode_namespace_and_invalid_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            c = configured(root)
            path = c.root/'config.json'
            original = json.loads(path.read_text())
            path.write_text('[]')
            self.assertIsNone(c.config())
            for change in ({'mode':'treatment'}, {'end':original['start']+1799},
                           {'start':original['end']+1}, {'id':'short'}, {'start':float('nan')}):
                path.write_text(json.dumps(dict(original,**change)))
                self.assertIsNone(c.config())
            path.write_text(json.dumps(original))
            self.assertIsNotNone(c.config())
            b=StockBook(lambda:{'round':3,'units':[inventory(unit='fixture-not-a-market-unit')]})
            row=c.begin(TASK,TEXT,b)
            token=b.hold('fixture-not-a-market-unit',TASK)
            c.finish(row,b,deal=dict(unit=token['unit'],rounds=0),token=token,outcome='quote')
            self.assertEqual(window_report(c)['confirmed_eligible'],0)

    def test_active_trial_conflict_prevents_pick_in_real_seller(self):
        import seller
        from contextlib import ExitStack
        from test_trial_fixture_isolation import NAMES
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            for name in NAMES:
                stack.enter_context(patch.object(seller,name,type(getattr(seller,name))(root=root)))
            c=seller.QUOTE_HOLD
            c.root.mkdir()
            now=time.time()
            (c.root/'config.json').write_text(json.dumps(dict(enabled=True,mode='shadow',id=str(uuid.uuid4()),
                owner_pid=os.getpid(), created_at=now-20,start=now-10,end=now+1790)))
            peer=seller.GRADE_PREFERENCE
            peer.root.mkdir(parents=True,exist_ok=True)
            (peer.root/'seller_concession_trial.json').write_text(json.dumps(dict(enabled=True,id='fixture',
                start=now-10,end=now+1790,seed=1)))
            b=StockBook(lambda:{'round':3,'units':[inventory()]})
            stack.enter_context(patch.object(b,'pick',side_effect=AssertionError('picked before gate')))
            class Up:
                rejected=False
                async def reject(self,msg): self.rejected=True
            up=Up()
            asyncio.run(seller.Seller(None,'fixture',stock=b).open_from_stock(
                SimpleNamespace(id=TASK),TEXT,up,lambda text:text))
            self.assertTrue(up.rejected)
            self.assertEqual(window_report(c)['would_change'],0)

    def test_exact_grammar_and_unknowns(self):
        self.assertEqual(classify(TEXT), 'explicit_quote_only_no_authorization')
        for text in [f'"{TEXT}"', 'Not '+TEXT, TEXT+' Buy it now.', TEXT.lower(),
                     '견적만 주세요', 'Do not '+TEXT, TEXT+'x'*500, 'Quote only for G01234567.']:
            with self.subTest(text=text): self.assertEqual(classify(text), 'unknown')

    def test_disabled_has_zero_files_and_zero_inventory_reads(self):
        with tempfile.TemporaryDirectory() as root:
            c = QuoteHold(root)
            b = StockBook(lambda: self.fail('disabled read inventory'))
            self.assertIsNone(c.begin(TASK, TEXT, b))
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_all_first_requests_and_restart_dedup_and_unknown_cost(self):
        with tempfile.TemporaryDirectory() as root:
            c = configured(root)
            b = StockBook(lambda: {'round': 3, 'units': [inventory(cost=None)]})
            row = c.begin(TASK, TEXT, b)
            token = b.hold('u-0123456789ab', TASK)
            deal = dict(unit='u-0123456789ab', rounds=0)
            c.finish(row, b, deal=deal, token=token, outcome='quote')
            report = window_report(c)
            self.assertEqual((report['first_requests'], report['confirmed_eligible'], report['would_change']), (1,1,1))
            self.assertIsNone(report['rows'][0]['pool']['units'][0]['cost'])
            self.assertNotIn(TEXT, json.dumps(report))
            self.assertIsNone(QuoteHold(root).begin(TASK, TEXT, b))
            for outcome in ('no_selection', 'unknown', 'timeout_or_cancelled', 'error'):
                row = c.begin(str(uuid.uuid4()), 'unknown', b)
                c.finish(row, b, outcome=outcome)
            self.assertEqual(window_report(c)['first_requests'], 5)

    def test_snapshot_errors_and_reentry_and_end_disable_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            c = configured(root)
            b = StockBook(lambda: {'round': 3})
            row = c.begin(TASK, TEXT, b)
            c.finish(row, b, outcome='no_selection')
            self.assertEqual(window_report(c)['rows'][0]['projection'], 'snapshot_error')
            self.assertEqual(window_report(c)['errors'], 1)
        for mode in ('reentry', 'end', 'disabled'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                c = configured(root)
                b = StockBook(lambda: {'round': 3, 'units': [inventory()]})
                row = c.begin(TASK, TEXT, b)
                token = b.hold('u-0123456789ab', TASK)
                if mode == 'reentry': c.touch(TASK)
                elif mode == 'disabled': (c.root / 'disabled').touch()
                else:
                    cfg = json.loads((c.root/'config.json').read_text())
                    with patch('quote_hold.time.time', return_value=cfg['end']):
                        c.finish(row,b,deal=dict(unit=token['unit'],rounds=0),token=token,outcome='quote')
                if mode != 'end': c.finish(row,b,deal=dict(unit=token['unit'],rounds=0),token=token,outcome='quote')
                self.assertEqual(window_report(c)['would_change'], 0)
                self.assertEqual(b.holder(token['unit']), TASK)

    def test_qualified_competitor_is_separate_from_sku_coincidence(self):
        with tempfile.TemporaryDirectory() as root:
            c = configured(root)
            b = StockBook(lambda: {'round': 3, 'units': [inventory()]})
            row = c.begin(TASK, TEXT, b)
            token = b.hold('u-0123456789ab', TASK)
            c.finish(row,b,deal=dict(unit=token['unit'],rounds=0),token=token,outcome='quote')
            row = c.begin(str(uuid.uuid4()), 'G01234567', b)
            c.finish(row, b, outcome='no_selection')
            self.assertIsNone(window_report(c)['qualified_competitor'])
            self.assertEqual(window_report(c)['structural_candidates'], 0)
            text = 'Required checkout policy: '+json.dumps(dict(sku='G01234567',grades=['A'],min_hours_left=1,
                                                               cert=True,warranty_hours=0))
            row = c.begin(str(uuid.uuid4()), text, b)
            c.finish(row, b, outcome='no_selection')
            self.assertIsNone(window_report(c)['qualified_competitor'])
            self.assertEqual(window_report(c)['structural_candidates'], 1)


class SellerIntegration(unittest.TestCase):
    def test_real_first_quote_and_continuation_guard_without_signed_receipt(self):
        import seller
        from contextlib import ExitStack
        from test_trial_fixture_isolation import NAMES
        class Up:
            def __init__(self): self.responses=[]
            async def requires_input(self, msg): self.responses.append(('quote',msg))
            async def reject(self, msg): self.responses.append(('reject',msg))
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            for name in NAMES:
                ctrl = type(getattr(seller,name))(root=root)
                stack.enter_context(patch.object(seller,name,ctrl))
            c = seller.QUOTE_HOLD
            c.root.mkdir()
            now=time.time()
            (c.root/'config.json').write_text(json.dumps(dict(enabled=True,mode='shadow',id=str(uuid.uuid4()),
                owner_pid=os.getpid(), created_at=now-20,start=now-10,end=now+1790)))
            for name in ('ask','decide_turn'):
                stack.enter_context(patch.object(seller,name,side_effect=AssertionError('model forbidden')))
            stack.enter_context(patch.object(seller.shop_memory,'load_pricing',return_value={}))
            stack.enter_context(patch.object(seller.shop_memory,'paid_prices',return_value=[]))
            stack.enter_context(patch.object(seller,'sku_median',return_value=10000))
            b=StockBook(lambda:{'round':3,'units':[inventory()]})
            shop=seller.Seller(None,'fixture',stock=b)
            task=SimpleNamespace(id=TASK)
            up=Up()
            asyncio.run(shop.open_from_stock(task,TEXT,up,lambda msg,ask=None:msg))
            self.assertEqual(up.responses[0][0],'quote')
            self.assertNotIn('sig_seller',up.responses[0][1])
            self.assertEqual(window_report(c)['would_change'],1)
            self.assertEqual(b.holder('u-0123456789ab'),TASK)
            # Exercise actual Seller continuation's availability guard before any model call.
            old=shop.open[TASK]
            token=window_report(c)['rows'][0]['hold_token']
            self.assertTrue(b.compare_and_release_quote(token,rounds=0,shadow=False)['released'])
            b.hold('u-0123456789ab',str(uuid.uuid4()))
            stack.enter_context(patch.object(seller,'get_message_text',return_value='10000'))
            stack.enter_context(patch.object(seller,'TaskUpdater',return_value=up))
            stack.enter_context(patch.object(shop,'remember'))
            # say() is only needed by reject; emulate the SDK message factory.
            up.new_agent_message=lambda **kw:kw
            context=SimpleNamespace(current_task=SimpleNamespace(id=TASK,context_id='fixture'), message=None)
            asyncio.run(shop._execute(context,None))
            self.assertEqual(up.responses[-1][0],'reject')
            self.assertNotIn(TASK,shop.open)
            self.assertEqual(old['rounds'],1)


if __name__ == '__main__':
    unittest.main()
