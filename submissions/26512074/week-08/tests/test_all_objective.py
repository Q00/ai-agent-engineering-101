import tempfile
import unittest
from score_coordination import all_value,snapshot_targets


class AllObjectiveTests(unittest.TestCase):
    def targets(self):
        return dict(at=1000,components={
            'shop':{'book':dict(current=100,values=[0,1000]),'revenue':dict(current=100,values=[0,200]),'stars_rank':dict(current=4,values=[1,5])},
            'buyer':{'saved':dict(current=.5,values=[0,1]),'forum':dict(current=.8,values=[]),'deals':dict(current=10,values=[0,100]),'posts':dict(current=10,values=[0,100])}})

    def test_all_components_use_official_weights(self):
        t=self.targets()
        self.assertAlmostEqual(all_value(t,'shop',{'book':100,'revenue':10,'stars_rank':.4},1000),.05+.015+.02)
        self.assertAlmostEqual(all_value(t,'buyer',{'saved':.1,'forum':.1,'deals':10,'posts':10},1000),.05+.03+.01+.01)

    def test_lower_profit_can_be_better_for_all(self):
        t=self.targets()
        a=all_value(t,'shop',{'book':10,'revenue':1},1000)
        b=all_value(t,'shop',{'book':5,'revenue':30},1000)
        self.assertGreater(b,a)

    def test_stale_data_has_no_fake_points(self):
        self.assertIsNone(all_value(self.targets(),'shop',{'book':100},3000))

    def test_student_buyer_and_stars_distributions_are_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            players=[dict(student=True,shop=dict(name=n),buyer=dict(name=n)) for n in ['totato2203-r3','peer']]
            board=dict(players=players,shops=[dict(name=n,book=100,revenue=100,stars_rank=4) for n in ['totato2203-r3','peer']],buyers=[dict(name=n,saved=.5,forum=.8,deals=10,posts=10) for n in ['totato2203-r3','peer']])
            r=snapshot_targets(d,board,1000)
            self.assertEqual(set(r['components']['buyer']),{'saved','forum','deals','posts'})
            self.assertIn('stars_rank',r['components']['shop'])


if __name__=='__main__':unittest.main()
