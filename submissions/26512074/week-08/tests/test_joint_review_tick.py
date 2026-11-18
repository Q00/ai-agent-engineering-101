import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_strategy_review as review


class JointReviewTickTests(unittest.TestCase):
    def test_existing_worker_runs_whole_cohort_and_matures_before_review(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);md=root/'.market';md.mkdir();(root/'logs').mkdir()
            with patch.multiple(review,ROOT=root,MD=md,STATE=md/'strategy-batch-review.json'),patch.object(review,'hashes',return_value={}),patch.object(review,'board_snapshot',return_value={'rank':1}),patch.object(review,'review',return_value={'changes':[]}) as run:
                self.assertEqual(review.tick(1000)['status'],'new_joint_cohort')
                self.assertEqual(review.tick(2000)['status'],'scheduled');run.assert_not_called()
                self.assertEqual(review.tick(22600)['status'],'maturing');run.assert_not_called()
                self.assertEqual(review.tick(44200)['status'],'joint_review_complete');run.assert_called_once()

    def test_standalone_job_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json';path.write_text(json.dumps({'status':'scheduled'}))
            with patch.multiple(review,STATE=path,MD=Path(d)):self.assertEqual(review.tick(1000)['status'],'standalone_review_owns_cohort')
