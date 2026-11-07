import tempfile
import unittest

from adaptive_control import Control, classify_forum_claim


class ForumPolicyTests(unittest.TestCase):
    def test_claim_truth_table(self):
        self.assertEqual(classify_forum_claim(True,0),'VERIFIED')
        self.assertEqual(classify_forum_claim(False,3),'CORROBORATED')
        self.assertEqual(classify_forum_claim(False,2),'UNVERIFIED')

    def test_duplicate_interval_and_role_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Control(tmp)
            ok,_,reason=c.allow_publication('buyer','검증된 가격 10,000원',[1],now=1000)
            self.assertTrue(ok);self.assertIsNone(reason)
            ok,_,reason=c.allow_publication('buyer','검증된 가격 10000원',[1],now=1301)
            self.assertFalse(ok);self.assertEqual(reason,'duplicate')
            ok,_,reason=c.allow_publication('buyer','다른 정보',[2],now=1100)
            self.assertFalse(ok);self.assertEqual(reason,'interval')
            self.assertNotIn('검증된 가격',c.db.execute('SELECT tokens FROM forum_publications').fetchone()[0])
            c.close()


if __name__=='__main__':unittest.main()
