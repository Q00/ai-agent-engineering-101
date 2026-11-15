import math
import tempfile
import unittest

from rank_controller import RankController


def board(our,others):
    players=[{'name':'25512088','all':our,'owner':{'login':'morethanwhatiwant'}}]
    players += [{'name':f'rival{i}','all':score,'owner':{'login':f'r{i}'}} for i,score in enumerate(others)]
    return {'round':3,'players':players}


class RankControllerTests(unittest.TestCase):
    def test_current_public_board_schema_uses_student_total_only(self):
        with tempfile.TemporaryDirectory() as state:
            controller=RankController(state)
            current={
                'round':3,
                'players':[
                    {'player':'house','house':True,'total':99.0,
                     'shop':{'name':'house'},'buyer':{'name':'house'}},
                    {'player':'nonstudent','house':False,'total':9.0,
                     'shop':{'name':'nonstudent'},'buyer':{'name':'nonstudent'}},
                    {'player':'leader','house':False,'student':True,
                     'total':1.5,'student_total':.9,
                     'shop':{'name':'leader'},'buyer':{'name':'leader'}},
                    {'player':'morethanwhatiwant','house':False,'student':True,
                     'total':1.165,'student_total':.8,
                     'shop':{'name':'25512088'},'buyer':{'name':'25512088'}},
                ],
            }
            result=controller.observe(current,observed_at=1000)
            self.assertEqual(result['rank'],2)
            self.assertEqual(result['our_score'],.8)
            row=controller.control.db.execute(
                'SELECT top_score FROM rank_observations ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(row[0],.9)
            controller.close()

    def test_rate_requires_80_percent_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=RankController(tmp);now=1_000_000
            r.observe(board(1,[2]),now-1400);r.observe(board(2,[2]),now)
            self.assertIsNone(r.rate(1800,now))
            r.observe(board(0,[2]),now-1700)
            self.assertGreater(r.rate(1800,now),0);r.close()

    def test_closing_rate_uses_moving_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=RankController(tmp);now=1_000_000
            r.observe(board(1,[5,4,3,2]),now-1700)
            result=r.observe(board(3,[6,5,4,3]),now)
            self.assertIsNotNone(result['cutoff_rate_30m'])
            self.assertAlmostEqual(result['closing_rate'],r.rate(1800,now)-r.cutoff_rate(10,1800,now))
            r.close()

    def test_attack_to_defend_is_three_snapshots_over_sixty_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=RankController(tmp);base=1_000_000
            self.assertEqual(r.observe(board(3,[2]),base)['mode'],'ATTACK')
            self.assertEqual(r.observe(board(3,[2]),base+30)['mode'],'ATTACK')
            self.assertEqual(r.observe(board(3,[2]),base+61)['mode'],'DEFEND')
            self.assertEqual(r.observe(board(1,[2]),base+100)['mode'],'DEFEND')
            rows=r.control.db.execute("SELECT COUNT(*) FROM recovery_checkpoints WHERE role='rank_controller'").fetchone()[0]
            self.assertEqual(rows,1);r.close()

    def test_gap_schedule_and_threat(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=RankController(tmp);now=1_000_000
            result=r.observe(board(1,[5,4,3,2]),now)
            self.assertGreater(result['gap'],0);self.assertTrue(math.isinf(result['eta']))
            self.assertTrue(result['behind_schedule']);r.close()

    def test_rejects_wrong_round_or_ambiguous_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=RankController(tmp)
            with self.assertRaises(ValueError):r.observe({'round':2,'players':[]},1)
            bad=board(1,[2]);bad['players'].append({'name':'25512088','all':3})
            with self.assertRaises(ValueError):r.observe(bad,1)
            r.close()

    def test_current_class_player_schema_uses_student_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=RankController(tmp)
            current={'round':3,'players':[
                {'player':'rival','student':True,'student_total':1.5,'owner':{'login':'rival'}},
                {'player':'morethanwhatiwant','student':True,'student_total':1.0,
                 'owner':{'login':'morethanwhatiwant'},'shop':{'name':'25512088'}},
                {'player':'house','student':False,'student_total':9.0,'owner':{'login':'house'}}]}
            result=r.observe(current,1_000_000)
            self.assertEqual(result['rank'],2);self.assertEqual(result['our_score'],1.0)
            r.close()


if __name__=='__main__':unittest.main()
