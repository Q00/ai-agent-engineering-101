import json
import tempfile
from pathlib import Path
import unittest
from market_analysis import summarize,report,queue_report,WINDOW

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.now=3*WINDOW
        self.catalog={'x':{'name':'X'},'y':{'name':'Y'}}
        self.events=[{'seq':i+1,'round':3,'kind':'settle','ts':self.now-100-i,
                      'data':{'sku':'x','item':'category','amount':price}}
                     for i,price in enumerate([100,120,140])]
        self.events += [{'seq':i+10,'round':3,'kind':'settle','ts':self.now-WINDOW-100-i,
                         'data':{'sku':'x','amount':100}} for i in range(3)]
    def test_actual_amount_field_and_dedup(self):
        rows=summarize(self.events+self.events,self.catalog,{},self.now)
        self.assertEqual(rows[0]['median'],120)
        self.assertEqual(rows[0]['change_pct'],20)
        self.assertEqual(rows[0]['count'],3)
        self.assertEqual(report(rows)['refs'],[1,2])
    def test_positions_do_not_change_stats(self):
        a=summarize(self.events,self.catalog,{},self.now)
        b=summarize(self.events,self.catalog,{'units':[{'sku':'x'}]},self.now)
        self.assertEqual(a[0]['median'],b[0]['median'])
        self.assertEqual(b[0]['held'],1)
        self.assertIn('내 재고 1개',report(b)['post'])
    def test_bad_and_old_rounds_excluded(self):
        events=[{**self.events[0],'round':2},
                {**self.events[0],'data':{'sku':'x','amount':float('nan')}},
                {**self.events[0],'ts':self.now+1}]
        self.assertEqual(summarize(events,self.catalog,{},self.now),[])
    def test_queue_interval_pending_restart(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d)
            self.assertFalse(queue_report(md,self.events,self.catalog,{},self.now))
            self.assertFalse((md/'outbox.jsonl').exists())
            self.assertTrue(queue_report(md,self.events,self.catalog,{},self.now,True))
            self.assertFalse(queue_report(md,self.events,self.catalog,{},self.now+301,True))
            (md/'outbox.jsonl').unlink()
            self.assertFalse(queue_report(md,self.events,self.catalog,{},self.now+299,True))
            self.assertTrue(queue_report(md,self.events,self.catalog,{},self.now+300,True))
            self.assertFalse(queue_report(md,self.events,self.catalog,{},self.now+600,True))
            self.assertEqual(len(json.loads((md/'analysis-preview.json').read_text(encoding='utf-8'))['rows']),1)
    def test_small_sample_no_trend(self):
        row=summarize(self.events[:1],self.catalog,{},self.now)[0]
        self.assertIsNone(row['change_pct'])
if __name__=='__main__':
    unittest.main()
