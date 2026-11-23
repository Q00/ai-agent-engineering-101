import hashlib
from tests.test_integrated_strategy import Case
from strategy_feedback import quote,market_price,evaluate,sampling_stats
from strategy_policy import database


class AdaptiveSamplingTests(Case):
    def test_warranty_horizon_excludes_recent_failures_and_successes_together(self):
        with database(self.md,True) as c:
            for task,age,paid in [('recent-failed',1000,None),('recent-paid',1000,80),('mature-failed',23000,None),('mature-paid',23000,80)]:
                c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry,warranty,settled_at,paid) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (task,task,'x','A',self.now-age,1.0,80,80,50,0,6,self.now-age+60 if paid else None,paid))
        result=evaluate(self.md,self.now,True)
        arm=result['metrics'][0]['arms']['1.0']
        self.assertEqual(arm['opportunities'],2)
        self.assertEqual(arm['known_contribution'],30)

    def test_unwarranted_failure_does_not_wait_six_hours(self):
        with database(self.md,True) as c:
            c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry,warranty) values(?,?,?,?,?,?,?,?,?,?,?)',
                      ('t','u','x','A',self.now-1000,1.0,80,80,50,0,0))
        result=evaluate(self.md,self.now,True)
        self.assertEqual(result['metrics'][0]['arms']['1.0']['opportunities'],1)

    def unit(self):return dict(unit='u',sku='x',grade='A',kind='goods',cost=50000,list_price=100000)

    def test_supported_challenger_gets_exploration_without_replacing_champion(self):
        self.policy['price_exploration_fraction']=1;self.save_policy();self.market()
        self.write('strategy-feedback.json',dict(at=self.now,challengers={'x:A':dict(arm=1.2)}))
        task=next('t'+str(i) for i in range(100) if int(hashlib.sha256(('t'+str(i)+':challenger').encode()).hexdigest(),16)%2==0)
        q=quote(self.md,task,self.unit(),self.inv,market_price(self.md,self.unit(),self.inv,self.now),self.now)
        self.assertEqual(q['pricing_evidence']['price_arm'],1.2);self.assertEqual(q['pricing_evidence']['champion'],1.0)

    def test_non_exploration_keeps_champion(self):
        self.policy['price_exploration_fraction']=0;self.save_policy();self.market()
        self.write('strategy-feedback.json',dict(at=self.now,challengers={'x:A':dict(arm=1.2)}))
        q=quote(self.md,'bound',self.unit(),self.inv,market_price(self.md,self.unit(),self.inv,self.now),self.now)
        self.assertEqual(q['pricing_evidence']['price_arm'],1.0)

    def test_two_supported_sales_increase_sampling_not_promote_policy(self):
        with database(self.md,True) as c:
            c.execute('insert into price_state values(?,?,?,?,?)',('x','A',1.0,self.now,'verified'))
            for arm,n,paid in [(1.0,20,60),(1.1,2,80)]:
                for i in range(n):
                    identity=f'{arm}-{i}'
                    c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry,settled_at,paid) values(?,?,?,?,?,?,?,?,?,?,?,?)',
                              (identity,identity,'x','A',self.now-1000,arm,paid,paid,50,0,self.now-900,paid))
        r=evaluate(self.md,self.now,True)
        self.assertEqual(r['changes'],[]);self.assertEqual(r['challengers']['x:A']['arm'],1.1)

    def test_identical_successes_still_have_conversion_uncertainty(self):
        self.assertGreater(sampling_stats([10,10],[True,True])[1],0)
