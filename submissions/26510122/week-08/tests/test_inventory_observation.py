import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import inventory_observation as obs


class ObservationTests(unittest.TestCase):
    def test_missing_data_is_unknown_not_zero_demand(self):
        e=obs.evidence('F1',[],1000)
        self.assertIsNone(e['total_demand'])
        self.assertIsNone(e['observed_visit_rate_while_stocked'])
        self.assertTrue(e['demand_is_censored'])

    def test_snapshot_gaps_do_not_become_exposure(self):
        history=[{'event':'inventory_snapshot','round':3,'source_time':t,'stock':{'F1':{'qty':1}}} for t in (0,60,120,600)]
        e=obs.evidence('F1',history,600)
        self.assertAlmostEqual(e['in_stock_exposure_hours'],120/3600,places=4)
        self.assertAlmostEqual(e['snapshot_coverage_hours'],120/3600,places=4)

    def test_requests_deduplicate_and_only_covered_visits_enter_rate(self):
        history=[{'event':'inventory_snapshot','round':3,'source_time':t,'stock':{'F1':{'qty':1}}} for t in range(0,361,60)]
        history += [{'event':'request_seen','at':30,'task_id':'one','requested_skus':['F1']},
                    {'event':'opening','at':40,'task_id':'one','sku':'F1'},
                    {'event':'opening','at':330,'task_id':'two','sku':'F1'},
                    {'event':'opening','at':400,'task_id':'uncovered_tail','sku':'F1'},
                    {'event':'receipt_signed','at':50,'task_id':'one','sku':'F1'},
                    {'event':'settled','at':55,'ledger':1,'sku':'F1'},
                    {'event':'settled','at':55,'ledger':1,'sku':'F1'}]
        e=obs.evidence('F1',history,420)
        self.assertEqual(e['observed_visits'],3)
        self.assertEqual(e['visits_during_observed_stock'],2)
        self.assertEqual(e['observed_visit_rate_while_stocked'],20.)
        self.assertEqual(e['confirmed_settlements'],1)

    def test_ambiguous_no_stock_does_not_inflate_every_sku(self):
        e=obs.evidence('F1',[{'event':'no_stock','at':10,'task_id':'a'}],20)
        self.assertEqual(e['observed_visits'],0)
        self.assertEqual(e['unattributed_no_stock_visits'],1)

    def test_only_specific_sku_or_unique_full_name_is_attributed(self):
        catalog={'G1':{'name':'특별한 우유 24팩'},'G10':{'name':'다른 우유 12팩'}}
        self.assertEqual(obs.requested_skus('I want G10',catalog),['G10'])
        self.assertEqual(obs.requested_skus('우유 주세요',catalog),[])
        self.assertEqual(obs.requested_skus('특별한 우유 24팩 주세요',catalog),['G1'])

    def test_stale_snapshot_is_not_recorded_as_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'inventory.json';p.write_text('{}');os.utime(p,(100,100))
            with patch.object(obs,'record') as record:
                self.assertFalse(obs.snapshot({'round':3},p,[],131))
                record.assert_not_called()


if __name__=='__main__':unittest.main()
