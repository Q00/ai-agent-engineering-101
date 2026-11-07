from pathlib import Path
import tempfile
import unittest

from deployment_pipeline import DeploymentPipeline, WorktreeGuard


class DeploymentPipelineTests(unittest.TestCase):
    def test_guard_rejects_overlap_escape_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard=WorktreeGuard(tmp)
            self.assertEqual(guard.check(['buyer_r3.py'],[]),(True,[]))
            self.assertIn('dirty_overlap',guard.check(['buyer_r3.py'],['buyer_r3.py'])[1])
            self.assertIn('path_escape',guard.check(['../x.py'],[])[1])
            self.assertIn('forbidden',guard.check(['.market/owner.env'],[])[1])

    def test_pre_live_failure_quarantines(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=DeploymentPipeline(tmp,runner=lambda *a:(1,.1,False,'bad'))
            dep=p.create('candidate','buyer')
            self.assertFalse(p.validate(dep,['buyer_r3.py'],checks=[('syntax',['python','-V'])]))
            self.assertEqual(p.control.deployment_state(dep),'QUARANTINED');p.close()

    def test_truthful_activation_and_live_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=DeploymentPipeline(tmp,runner=lambda *a:(0,.1,False,'ok'))
            dep=p.create('candidate','buyer',rollback_target='active-1')
            self.assertTrue(p.validate(dep,['buyer_r3.py'],checks=[('syntax',['python','-V'])]))
            self.assertTrue(p.shadow(dep,0,300,0,'buyer'))
            self.assertTrue(p.canary(dep,'buyer',20,600))
            self.assertFalse(p.activate(dep,supervisor_owned=True,board_alive=False,
                commit_matches=True,checkpoint_valid=True,alive_seconds=90))
            self.assertEqual(p.control.deployment_state(dep),'ROLLED_BACK')
            success=p.control.db.execute("SELECT COUNT(*) FROM runtime_events WHERE kind='deployment_succeeded'").fetchone()[0]
            self.assertEqual(success,0);p.close()

    def test_success_only_after_all_runtime_proofs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=DeploymentPipeline(tmp,runner=lambda *a:(0,.1,False,'ok'))
            dep=p.create('candidate','shop')
            p.validate(dep,['referral_seller.py']);p.shadow(dep,0,0,30,'shop');p.canary(dep,'shop',10,0,1)
            self.assertTrue(p.activate(dep,supervisor_owned=True,board_alive=True,
                commit_matches=True,checkpoint_valid=True,alive_seconds=90))
            self.assertEqual(p.control.deployment_state(dep),'ACTIVE');p.close()


if __name__=='__main__':unittest.main()
