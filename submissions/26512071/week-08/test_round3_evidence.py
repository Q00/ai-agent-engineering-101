"""Fixtures below are synthetic unit cases, NEVER market evidence."""
import unittest
try:
    import round3_evidence as e
except ModuleNotFoundError:
    e = None

class Reconciliation(unittest.TestCase):
    def test_exact_order_settle_not_round2_verified_or_nearby_result(self):
        self.assertIsNotNone(e, 'collector feature missing')
        order=lambda oid,b: dict(kind='order',round=3,data=dict(order=oid,buyer=b,item='lamp'))
        result=lambda oid: dict(kind='result',round=3,data=dict(order=oid,buyer='fair-furniture',verified=True,
            receipt=dict(task='t',item='lamp',price=103),shop='https://shop'))
        settle=lambda oid: dict(kind='settle',round=3,data=dict(order=oid,buyer='fair-furniture',item='lamp',amount=103,checkout_hash='h'))
        rows=[order('o1','fair-furniture'),order('o2','fair-furniture'),result('o1'),settle('o2')]
        self.assertEqual(e.reconcile(rows,[])['buyer_settled'],0)
        rows.append(settle('o1'))
        self.assertEqual(e.reconcile(rows,[])['buyer_settled'],1)
        self.assertEqual(e.reconcile(rows+[settle('o1')],[])['buyer_settled'],1)
        self.assertEqual(e.reconcile([dict(r,round=2) for r in rows],[])['buyer_settled'],0)
        self.assertEqual(e.reconcile([order('o1','someone-else'),result('o1'),settle('o1')],[])['buyer_settled'],0)

    def test_metrics_separate_negotiations_and_settlement(self):
        self.assertTrue(hasattr(e, 'metrics'), 'metrics feature missing')
        events=[dict(task='t',policy='adaptive',item='lamp',first_ask=103,reserve=100),
                dict(task='open',policy='adaptive',item='lamp',first_ask=105,reserve=100)]
        talks=[dict(task='t',policy='adaptive',outcome='deal')]
        settled=[dict(task='t',policy='adaptive',seller=True,amount=102,margin=2)]
        m=e.metrics(events,talks,settled)['adaptive']['lamp']
        self.assertEqual(m['started'],2)
        self.assertEqual(m['first_ask_mean'],104)
        self.assertEqual(m['negotiation_success'],.5)
        self.assertEqual(m['settlement_success'],.5)
        self.assertEqual(m['pending_or_abandoned'],1)
        self.assertEqual(m['settled_average_price'],102)
        self.assertEqual(m['settled_margin_sum'],2)

    def test_collector_paginates_saves_raw_and_keeps_pending_round2(self):
        self.assertTrue(hasattr(e, 'collect'), 'read-only collector missing')
        import tempfile
        from pathlib import Path
        import json
        first=dict(seq=40,kind='order',round=2,data=dict(order='o',buyer='fair-furniture',item='lamp'))
        second=dict(seq=41,kind='result',round=2,data=dict(order='o',buyer='fair-furniture',verified=True))
        calls=[]
        def fetch(path):
            calls.append(path)
            if path == '/api/board': return {'current':2,'round':2}
            return {'/api/ledger?after=-500':[first],'/api/ledger?after=40':[second],'/api/ledger?after=41':[]}[path]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); out=root/'output'
            summary=e.collect(root,out,fetch=fetch)
            self.assertEqual(summary['round3_status'],'not_active')
            self.assertEqual(summary['reconciliation']['buyer_settled'],0)
            self.assertEqual(summary['ledger']['last_seq'],41)
            self.assertTrue(summary['ledger']['caught_up'])
            self.assertEqual(json.loads((out/'ledger.json').read_text()),[first,second])
            self.assertIn('/api/ledger?after=41',calls)

    def test_seller_settlement_requires_exact_local_receipt_task_item_and_amount(self):
        ledger=[dict(kind='order',round=3,data=dict(order='o',buyer='other',item='lamp')),
                dict(kind='result',round=3,data=dict(order='o',buyer='other',verified=True,
                    receipt=dict(task='sold',item='lamp',price=103),shop='https://our-shop')),
                dict(kind='settle',round=3,data=dict(order='o',buyer='other',item='lamp',amount=103,checkout_hash='h'))]
        sale=dict(task='sold',item='lamp',price=103,reserve=100,policy='adaptive')
        summary=e.reconcile(ledger,[sale])
        self.assertEqual(summary['seller_settled'],1)
        self.assertEqual(summary['buyer_settled'],0)
        self.assertEqual(summary['matched'][0]['margin'],3)
        for changed in [dict(sale,task='nearby'),dict(sale,price=104),dict(sale,item='chair'),dict(sale,test=True)]:
            self.assertEqual(e.reconcile(ledger,[changed])['seller_settled'],0)

if __name__=='__main__': unittest.main()
