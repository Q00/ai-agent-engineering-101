import json
from pathlib import Path
import sys
import tempfile
import time
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
        evolution.control.save_input('rules',{'latest_patch':{'version':'3.5','announced':'Nov 6, 2026'},
            'score_formulas':{'shop_all':'0.5 book + 0.3 revenue + 0.2 stars'}},fetched=now)
        evolution.control.save_input('agent',{'version':'one'},fetched=now)
        evolution.control.save_input('board',{'players':[{'owner':{'login':'morethanwhatiwant'},
            'student_total':1.0,'shop':{'all':.2},'buyer':{'all':.7}},
            {'owner':{'login':'rival'},'student_total':1.5,'shop':{'all':.8},'buyer':{'all':.7}}]},fetched=now)
        evolution.control.save_input('shops',{'shops':[{'name':'rival'}]},fetched=now)
        evolution.control.event('procurement','observation',{},'internal1')
        return evolution

    def test_first_review_then_hourly_stall_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence()
            self.assertEqual(e.trigger(ev),'scheduled_review')
            e._cycle('first','scheduled_review','shop',ev,'abc')
            self.assertIsNone(e.trigger(ev,10_000+INTERVAL-1))
            self.assertEqual(e.trigger(ev,10_000+INTERVAL),'rank_gap_not_closing')
            e.control.close()

    def test_hourly_review_runs_even_without_comparable_gap_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence();e._cycle('first','scheduled_review','shop',ev,'abc')
            ev['top10_gap_24m_ago']=None
            self.assertEqual(e.trigger(ev,10_000+INTERVAL),'hourly_review')
            e.control.close()

    def test_rule_change_bypasses_interval_and_role_uses_official_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence();e._cycle('first','scheduled_review','shop',ev,'abc')
            self.assertEqual(e.choose_role(ev),'shop')
            e.control.save_input('rules',{'latest_patch':{'version':'3.6','announced':'Nov 9, 2026'}},fetched=10_001)
            changed=e.evidence(10_001)
            self.assertEqual(e.trigger(changed,10_001),'official_rule_changed')
            prompt=e.codex_prompt('test','shop',changed)
            self.assertNotIn('body',json.dumps(changed));self.assertNotIn('instruction',json.dumps(changed).casefold())
            self.assertIn('raw forum text',prompt)
            self.assertIn('Target role: shop',prompt)
            e.control.close()

    def test_three_layers_are_mandatory_and_competitor_text_is_not_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence()
            self.assertTrue(ev['evidence_readiness']['ready'])
            self.assertEqual(set(ev['layers']),{'operator','competition','internal'})
            leaders=ev['layers']['competition']['tables']['players']['leaders']
            self.assertEqual(leaders[0]['id'],'rival')
            prompt=e.codex_prompt('test','shop',ev)
            self.assertIn('All three layers are mandatory',prompt)
            e.control.db.execute("DELETE FROM input_snapshots WHERE source='shops'")
            blocked=e.evidence()
            self.assertFalse(blocked['evidence_readiness']['ready'])
            self.assertIn('missing_shops',blocked['evidence_readiness']['reasons'])
            e.control.close()

    def test_buyer_core_files_and_dynamic_inventory_policy_are_evolvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);prompt=e.codex_prompt('test','buyer',e.evidence())
            for path in ('buyer_agent.py','buyer_memory.py','buyer_review.py','buyer_r3.py'):
                self.assertIn(path,prompt)
            shop_prompt=e.codex_prompt('test','shop',e.evidence())
            self.assertIn('deployable inventory capital must continue',shop_prompt)
            self.assertIn('scale from current wealth',shop_prompt)
            e.control.close()

    def test_live_evolution_invokes_codex_directly_without_ouroboros(self):
        command=Evolution.codex_command(Path('/tmp/candidate'))
        joined=' '.join(command)
        self.assertIn('/codex exec',joined)
        self.assertIn('--approve-for-me',joined)
        self.assertNotIn('--sandbox',joined)
        self.assertNotIn('ouroboros',joined.casefold())

    def test_interrupted_pre_live_cycle_retries_without_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);ev=e.evidence();e._cycle('interrupted','scheduled_review','shop',ev,'abc')
            e._state('interrupted','QUARANTINED','interrupted_before_live_completion')
            self.assertEqual(e.trigger(ev,10_001),'interrupted_retry')
            e.control.close()

    def test_morning_report_compares_official_rank_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp);e.ensure_baseline()
            e.write_performance_report(e.evidence())
            report=json.loads((Path(tmp)/'.market/evolution/morning-report.json').read_text())
            self.assertEqual(report['baseline']['rank'],2);self.assertFalse(report['improved'])
            e.control.close()

    def test_role_score_and_conversion_metric_are_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=self.make(tmp)
            self.assertEqual(e.current_role_score('shop'),.2)
            e.control.event('seller','quote_created',{},'q1');e.control.event('seller','sale_signed',{},'q1')
            now=time.time();metric=e.role_metric('shop',now-10,now+10)
            self.assertEqual(metric,{'attempts':1,'successes':1,'rate':1.0})
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
