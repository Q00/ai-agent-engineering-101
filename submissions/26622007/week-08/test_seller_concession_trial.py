import json
from pathlib import Path
import tempfile
import unittest

class ConcessionTests(unittest.TestCase):
    def test_quote_changes_only_first_ask_not_schedule_anchor(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / "seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="price",start=100,end=1900,seed=1)))
            t = Trial(p)
            deals = []
            for i in range(2):
                d = dict(id="sku",unit="u",kind="goods",cost=50000,reserve=80000,open=100000,ask=100000,history=[("you asked",100000)])
                t.quote(str(i),d,100)
                deals.append(d)
            self.assertEqual(sorted(d["ask"] for d in deals),[97000,100000])
            self.assertTrue(all(d["open"] == 100000 for d in deals))
            self.assertEqual(sorted(d["history"][0][1] for d in deals),[97000,100000])
            self.assertFalse((p/"seller_reader_trial.sqlite3").exists())

    def test_bound_sticky_stop_and_600_second_itt(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            cfg = dict(enabled=True,id='p',start=100,end=1900,seed=2)
            config = p/'seller_concession_trial.json'
            config.write_text(json.dumps(cfg))
            t=Trial(p)
            def deal():
                return dict(id='sku',unit='u',kind='goods',cost=50000,reserve=99000,open=100000,ask=100000,rounds=0,history=[('you asked',100000)])
            ds=[]
            for i in range(205):
                d=deal(); t.quote('full-uuid-'+str(i),d,100); ds.append(d)
            b=next(d for d in ds if d['concession_trial']['arm']=='B')
            self.assertEqual(b['ask'],99000)
            self.assertEqual(sum(d['concession_trial']['arm']=='B' for d in ds),100)
            a=ds[0]['concession_trial']
            self.assertEqual(Trial(p).assign('full-uuid-0',deal(),1901),a)
            self.assertIsNone(t.assign('new',deal(),1900)['id'])
            t.event('full-uuid-0',ds[0],'deal',100000,699)
            early=t.readout('p',699)
            self.assertEqual(sum(x['mature'] for x in early.values()),0)
            mature=t.readout('p',700)
            self.assertEqual(sum(x['deals'] for x in mature.values()),1)
            self.assertEqual(sum(x['gross_margin'] for x in mature.values()),50000)
            self.assertGreaterEqual(sum(x['mature'] for x in mature.values()),199)
            config.write_text(json.dumps(dict(cfg,stop=True)))
            t.restore(b)
            self.assertEqual(b['ask'],100000)
            config.write_text(json.dumps(dict(cfg,end=1901)))
            self.assertIsNone(t.assign('invalid',deal(),100)['id'])

    def test_default_off_and_invalid_restore(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            t=Trial(root)
            d=dict(open=100000,ask=100000,reserve=50000,history=[('you asked',100000)])
            t.quote('off',d,100)
            self.assertEqual(d['ask'],100000)
            self.assertFalse((Path(root)/'seller_concession_trial.sqlite3').exists())

    def test_reentry_keeps_current_control_quote_and_marks_contamination(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p/'seller_concession_trial.json').write_text(json.dumps(dict(enabled=True,id='p',start=100,end=1900,seed=1)))
            for task in ('one', 'two'):
                old = dict(id='sku',unit='old',kind='goods',cost=50000,reserve=80000,open=100000,ask=100000,history=[('you asked',100000)])
                original = Trial(p).quote(task, old, 100)
                current = dict(old,unit='new',reserve=150000,open=200000,ask=200000,history=[('you asked',200000)])
                t = Trial(p)
                t.quote(task,current,101)
                self.assertEqual(current['ask'],200000)
                t.restore(current)
                self.assertEqual(current['ask'],200000)
                self.assertEqual(t.arm(current['concession_trial']),'A')
                self.assertEqual(t.assign(task,old,102),original)
                event=json.loads((p/'seller_concession_trial.jsonl').read_text().splitlines()[-1])
                self.assertTrue(event['contaminated'])
                self.assertEqual(event['original_open'],100000)
                self.assertEqual(event['current_open'],200000)

    def test_turn_trace_records_guarded_decision_without_reader_enrollment(self):
        from seller_concession_trial import Trial
        from types import SimpleNamespace
        import time
        import seller
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)
            (p/'seller_concession_trial.json').write_text(json.dumps(dict(enabled=True,id='p',start=100,end=1900,seed=1)))
            t=Trial(p)
            d=dict(id='sku',unit='u',kind='goods',cost=50000,reserve=80000,open=100000,ask=100000,rounds=1,history=[('you asked',100000)],reader_trial_floor=90000,reader_trial_harness_used=True)
            t.quote('uuid',d,100)
            action,price=seller.price_guards(d,'accept',70000,70000,90000)
            t.trace('uuid',d,SimpleNamespace(source='jev',jev_calls=1),time.monotonic(),{},action,price,70000)
            r=json.loads((p/'seller_concession_trial.jsonl').read_text().splitlines()[-1])
            self.assertEqual(r['event'],'turn')
            self.assertEqual((r['action'],r['price'],r['offer']),('counter',90000,70000))
            self.assertEqual((r['ask'],r['floor'],r['source'],r['jev_calls'],r['harness_used']),(d['ask'],90000,'jev',1,True))
            self.assertGreaterEqual(r['seconds'],0)
            self.assertFalse((p/'seller_reader_trial.jsonl').exists())

    def test_all_reader_trace_sites_also_emit_concession_turn(self):
        import ast
        tree=ast.parse((Path(__file__).parent/'seller.py').read_text())
        sites=0
        for node in ast.walk(tree):
            for value in ast.iter_fields(node):
                body=value[1]
                if not isinstance(body,list):
                    continue
                for i,stmt in enumerate(body):
                    if not isinstance(stmt,ast.Expr) or not isinstance(stmt.value,ast.Call):
                        continue
                    call=stmt.value
                    if isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id=='READER_TRIAL' and call.func.attr=='trace':
                        sites+=1
                        next_call=body[i+1].value
                        self.assertEqual(ast.unparse(next_call.func),'CONCESSION_TRIAL.trace')
                        self.assertEqual([ast.dump(a) for a in call.args],[ast.dump(a) for a in next_call.args])
        self.assertEqual(sites,5)
