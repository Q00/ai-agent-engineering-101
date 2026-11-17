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

    def test_buyer_rolling_limit_is_aggressive_but_below_official_maximum(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Control(tmp)
            for n in range(287):
                ok,_,reason=c.allow_publication('buyer',f'고유 시장 정보 {n}',[n],now=1000+n*301)
                self.assertTrue(ok,reason)
            ok,_,reason=c.allow_publication('buyer','한도 초과 정보',[999],now=1000+287*301)
            self.assertFalse(ok);self.assertEqual(reason,'daily_limit')
            c.close()


if __name__=='__main__':unittest.main()
