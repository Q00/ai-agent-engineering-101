"""Purchase memory remains fresh and identity-safe with a lagging read-only index."""
import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import buyer_logdb
import buyer_memory as memory
import buyer_checkout_memory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'logs').mkdir()
        self.path=self.root/'logs/buyer-events.jsonl'
        self.dbpath=self.root/'state/buyer.sqlite3'
        self.now=1800000000

    def index(self):
        with closing(buyer_logdb.connect(self.dbpath)) as db:
            buyer_logdb.index_file(db,self.root,'logs/buyer-events.jsonl',None)

    def test_fresh_tail_and_all_identity_observations_survive_projection(self):
        memory.record('decision_executed',path=self.path,order='old',shop='same',shop_name='A')
        memory.record('payment',path=self.path,order='old',shop='same',sku='G',paid=True,
                      price=700,ledger=1)
        self.index()
        memory.record('shop_identity',path=self.path,shop='same',shop_name='B')
        memory.record('payment',path=self.path,order='new',shop='same',shop_name='B',
                      sku='G',paid=False,reason='bad receipt')
        raw=self.path.read_bytes()
        history=memory.read_history(self.path,now=self.now)
        self.assertEqual(history.reader_source,'sqlite_prefix_and_raw_tail')
        prepared=memory.PreparedHistory(history)
        self.assertEqual(memory.context('new-url','G',shop_name='A',history=prepared)
                         ['same_sku_payment_outcomes'],{'paid':1})
        self.assertEqual(memory.context('same','G',shop_name='B',history=prepared)
                         ['same_sku_payment_outcomes'],{'refused':1})
        self.assertEqual(self.path.read_bytes(),raw)

    def test_rotated_log_never_reuses_old_index_payments(self):
        memory.record('settled',path=self.path,order='old',shop='old',sku='G',ledger=1,price=1)
        self.index()
        self.path.rename(self.path.with_suffix('.old'))
        memory.record('settled',path=self.path,order='new',shop='new',sku='G',ledger=2,price=2)
        history=memory.read_history(self.path,now=self.now)
        self.assertEqual(history.reader_source,'raw_log')
        self.assertEqual([e['ledger'] for e in history if e['event']=='settled'],[2])

    def test_truncation_and_partial_append_are_not_confirmed_purchases(self):
        memory.record('settled',path=self.path,order='old',sku='G',ledger=1,price=1)
        self.index()
        self.path.write_bytes(b'{"event":"settled","ledger":999')
        self.assertEqual(memory.read_history(self.path,now=self.now),[])

    def test_prepared_context_matches_legacy_identity_and_checkout_guard(self):
        from datetime import datetime,timezone
        at=datetime.fromtimestamp(self.now,timezone.utc).isoformat()
        rows=[{'event':'shop_identity','shop':'old','shop_name':'A'},
              {'event':'payment','at':at,'order':'o','shop':'old','sku':'F',
               'paid':False,'reason':'not paying: 2.0 h of shelf life left'},
              {'event':'selection_opportunities','at':at,'order':'o',
               'eligible_candidates':[{'shop':'A','sku':'F','stock':1}]},
              {'event':'negotiation','order':'other','shop':'old','shop_name':'A',
               'sku':'F','result':'receipt_pending'}]
        prepared=memory.PreparedHistory(rows)
        self.assertEqual(memory.context('new','F',shop_name='A',history=rows),
                         memory.context('new','F',shop_name='A',history=prepared))
        choice={'shop':'A','url':'new','sku':'F','stock':1}
        order={'kind':'food','use_within_hours':24}
        self.assertEqual(buyer_checkout_memory.constraint(choice,order,rows,self.now),
                         buyer_checkout_memory.constraint(choice,order,prepared,self.now))
        changed={**choice,'stock':2}
        self.assertIsNone(buyer_checkout_memory.constraint(changed,order,prepared,self.now))

    def test_imported_tail_payment_is_reused_without_a_second_log_read(self):
        memory.record('negotiation',path=self.path,order='o',shop='url',shop_name='A',
                      sku='G',result='receipt_pending',receipt_price=700,attempt_id='exact')
        payments=self.root/'payments.jsonl'
        payments.write_text(json.dumps({'order':'o','shop':'url','item':'G',
                                       'paid':True,'price':700,'ledger':1})+chr(10))
        history=memory.read_history(self.path,now=self.now)
        with patch.object(memory,'read_history',side_effect=AssertionError('unnecessary second read')):
            self.assertEqual(memory.import_payments(payments,event_path=self.path,history=history),1)
        self.assertEqual(history[-1]['outcome_link_status'],'unique_receipt_attempt')
        self.assertEqual(memory.context('url','G',shop_name='A',
                         history=memory.PreparedHistory(history))['same_sku_payment_outcomes'],{'paid':1})
