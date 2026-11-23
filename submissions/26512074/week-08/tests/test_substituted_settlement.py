import json
from tests.test_integrated_strategy import Case
from strategy_policy import database
from strategy_feedback import reconcile_paid
from optimizer import db


class SubstitutedSettlementTests(Case):
    def seed(self,grade='A',cost=40000,verified=True):
        with database(self.md,True) as c:
            c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry) values(?,?,?,?,?,?,?,?,?,?)',
                      ('task','original','x','A',self.now-10,1,80000,80000,50000,0))
            events=[dict(seq=1,ts=self.now,kind='settle',data=dict(shop='totato2203-r3',order='o',unit='actual',sku='x',grade=grade,amount=80000)),
                    dict(seq=2,ts=self.now+1,kind='result',data=dict(shop='totato2203-r3',order='o',verified=verified,receipt=dict(task='task',price=80000)))]
            for e in events:c.execute('insert into market_events values(?,?,?,?)',(e['seq'],e['ts'],e['kind'],json.dumps(e)))
        if cost is not None:
            with db(self.md) as c:c.execute('insert into units values(?,?,?,?)',('actual','x',cost,self.now-86400))

    def test_verified_replacement_uses_actual_cost_storage_and_single_payment(self):
        self.seed();reconcile_paid(self.md,self.now+5);reconcile_paid(self.md,self.now+10)
        with database(self.md) as c:r=c.execute('select * from price_quotes').fetchone()
        self.assertEqual(r['unit'],'actual');self.assertEqual(r['cost'],40000)
        self.assertEqual(r['storage'],400);self.assertEqual(r['paid'],80000)

    def test_grade_change_is_not_attributed(self):
        self.seed(grade='B');reconcile_paid(self.md,self.now+5)
        with database(self.md) as c:self.assertIsNone(c.execute('select paid from price_quotes').fetchone()[0])

    def test_unknown_cost_or_unverified_receipt_is_not_attributed(self):
        self.seed(cost=None,verified=False);reconcile_paid(self.md,self.now+5)
        with database(self.md) as c:self.assertIsNone(c.execute('select paid from price_quotes').fetchone()[0])
