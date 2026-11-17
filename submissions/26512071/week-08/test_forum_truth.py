"""Do not call a local outbox append a published external post."""
import json,tempfile,unittest
from pathlib import Path
import forum_r3
class ForumTruthTests(unittest.TestCase):
    def test_float_warranty_is_observed_not_fabricated_missing(self):
        text=forum_r3.render({'item':'G1','price':100},{'grade':'A','warranty_hours':6.0})
        self.assertIn('warranty 6',text)
        self.assertNotIn('warranty unobserved',text)
    def test_local_append_is_queued_not_posted(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'forum-policy.json').write_text('{"enabled":true}')
            payment={'order':'o1','shop':'https://other.example','item':'G1','price':100,'paid':True,'ledger':123}
            (p/'payments.jsonl').write_text(json.dumps(payment)+'\n')
            row={'seq':123,'kind':'settle','round':3,'data':{'order':'o1','shop':'https://other.example','item':'G1','amount':100,'buyer':'fair-furniture','grade':'A'}}
            result=forum_r3.maybe_report(p,now=100000,rows=[row])
            self.assertTrue(result.get('queued'))
            self.assertFalse(result.get('posted',False))
