import json
from pathlib import Path
import sys
import tempfile
import unittest

from evolution_daemon import Evolution, INTERVAL
from rank_controller import RankController


class EvolutionDaemonTests(unittest.TestCase):
    def make(self,tmp,now=10_000):
        clock=lambda:now
        evolution=Evolution(Path(tmp)/'.market',clock=clock,runner=lambda *a:(0,b''))
        rank=RankController(Path(tmp)/'.market',evolution.control)
        rank.observe({'round':3,'players':[
            {'student':True,'student_total':1.0,'owner':{'login':'morethanwhatiwant'},
             'shop':{'name':'25512088','all':.2},'buyer':{'name':'25512088','all':.7}},
            {'student':True,'student_total':1.5,'owner':{'login':'rival'}}]},now-1500)
        rank.observe({'round':3,'players':[
            {'student':True,'student_total':1.0,'owner':{'login':'morethanwhatiwant'},
             'shop':{'name':'25512088','all':.2},'buyer':{'name':'25512088','all':.7}},
            {'student':True,'student_total':1.5,'owner':{'login':'rival'}}]},now)
        evolution.control.save_input('rules',{'version':'one'},fetched=now)
        evolution.control.save_input('agent',{'version':'one'},fetched=now)
        evolution.control.save_input('board',{'players':[{'owner':{'login':'morethanwhatiwant'},
            'shop':{'all':.2},'buyer':{'all':.7}}]},fetched=now)
        return evolution

    def test_first_review_then_30_min_stall_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence()
            self.assertEqual(e.trigger(ev),'scheduled_review')
            e._cycle('first','scheduled_review','shop',ev,'abc')
            self.assertIsNone(e.trigger(ev,10_000+INTERVAL-1))
            self.assertEqual(e.trigger(ev,10_000+INTERVAL),'rank_gap_not_closing')
            e.control.close()

    def test_rule_change_bypasses_interval_and_role_uses_official_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence();e._cycle('first','scheduled_review','shop',ev,'abc')
            self.assertEqual(e.choose_role(ev),'shop')
            e.control.save_input('rules',{'version':'two'},fetched=10_001)
            changed=e.evidence(10_001)
            self.assertEqual(e.trigger(changed,10_001),'official_rule_changed')
            seed=e.seed('test','shop',changed,Path(tmp)/'candidate').read_text()
            self.assertNotIn('body',json.dumps(changed));self.assertNotIn('instruction',json.dumps(changed).casefold())
            self.assertIn('raw forum/log text',seed)
            self.assertIn(str(Path(tmp)/'candidate'),seed)
            e.control.close()

    def test_morning_report_compares_official_rank_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);e.ensure_baseline()
            e.write_performance_report(e.evidence())
            report=json.loads((Path(tmp)/'.market/evolution/morning-report.json').read_text())
            self.assertEqual(report['baseline']['rank'],2);self.assertFalse(report['improved'])
            e.control.close()

    def test_fake_candidate_active_and_rollback_paths(self):
        class FakeEvolution(Evolution):
            pass_canary=True
            def build(self,*args):return self.dir,[self.dir],None
            def candidate_patch(self,*args):return True,[],['submissions/25512088/week-08/buyer_r3.py'],b'patch'
            def validate_candidate(self,*args):return True
            def deploy(self,*args):return True,'candidate-commit',None
            def restart(self,*args,**kwargs):return True
            def canary(self,*args,**kwargs):return self.pass_canary,{'samples':20}
        with tempfile.TemporaryDirectory() as tmp:
            base=self.make(tmp);base.control.close()
            fake=FakeEvolution(Path(tmp)/'.market',clock=lambda:10_000,runner=lambda *a:(0,b'base'))
            cycle=fake.cycle('test_active')
            self.assertEqual(fake.control.db.execute('SELECT state FROM evolution_cycles WHERE cycle_id=?',(cycle,)).fetchone()[0],'ACTIVE')
            fake.pass_canary=False;cycle=fake.cycle('test_rollback')
            self.assertEqual(fake.control.db.execute('SELECT state FROM evolution_cycles WHERE cycle_id=?',(cycle,)).fetchone()[0],'ROLLED_BACK')
            fake.control.close()


if __name__=='__main__':unittest.main()
