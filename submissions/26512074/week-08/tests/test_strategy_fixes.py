import json
import tempfile
import unittest
from pathlib import Path
import buyer_r3
import recommendations as r


class StrategyFixes(unittest.TestCase):
 def test_stale_stock_cooldown_expires_without_rearming_old_failure(self):
  rows=[{'shop':'s','item':'x','order':str(i),'paid':False,'reason':'unit is not in stock'} for i in range(2)]
  memory={}
  buyer_r3.checkout_feedback(rows,memory,1000)
  self.assertEqual(memory['s']['stock_cooldowns']['x']['until'],1300)
  buyer_r3.checkout_feedback(rows,memory,1400)
  self.assertEqual(memory['s']['stock_cooldowns']['x']['until'],1300)
  buyer_r3.checkout_feedback(rows+[{'shop':'s','item':'x','order':'ok','paid':True}],memory,1401)
  self.assertNotIn('x',memory['s']['stock_cooldowns'])

 def test_post_reactions_not_duplicated_or_confounded_by_cash(self):
  with tempfile.TemporaryDirectory() as d:
   c=r.database(d)
   data=json.dumps({'score':20,'median':100,'price':70,'shop_profit_baseline':0})
   for sku in ['a','b']:
    c.execute('insert into publications values(?,?,?,?,?,?,null)',(sku,1000,sku,'style','post',data))
   sent=[{'sent':{'post':'post'},'status':200,'answer':'{"post":1}'}]
   forum=[{'id':1,'ts':1000,'likes':6,'dislikes':0}]
   r.measure_feedback(c,[],forum,sent,2000,{'balance':9000000,'start':5000000})
   feedback=[json.loads(x[0]) for x in c.execute('select feedback from publications')]
   self.assertEqual(sum(x['reward'] for x in feedback),6)
   r.measure_feedback(c,[],forum,sent,3000,{'balance':2000000,'start':5000000})
   self.assertEqual(sum(json.loads(x[0])['reward'] for x in c.execute('select feedback from publications')),6)
   c.close()

if __name__=='__main__':unittest.main()
