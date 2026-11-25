"""Regression: synthetic Seller E2E must never write ambient trial namespaces."""
import hashlib
import builtins
import io
import json
import pathlib
import tempfile
import sqlite3
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import os
_import_market = tempfile.TemporaryDirectory()
_import_environment = patch.dict(os.environ, {'MARKET_DIR': _import_market.name})
_import_environment.start()
import seller
import test_round3 as round3
_import_environment.stop()

NAMES = ("READER_TRIAL", "CONCESSION_TRIAL", "LATE_CONCESSION",
         "OPENING_DEFAULT", "DRAIN", "OPENING_CUE", "LATE_SPEED",
         "JEV_TRANSPORT", "SHELF_REPLY", "EXACT_ACCEPT", "EXACT_COUNTER",
         "GRADE_PREFERENCE", "FOOD_OPENING", "AT_FLOOR_EXACT", "DEEP_LOWBALL", "QUOTE_HOLD",
         "MARGIN_STUDY", "SHELF_JEV", "SHELF_FACT", "WARRANTY_FACT", "GRADE_READ")
NAMES += ("MARKET_REF",)   # market_ref_read.Switch (지시23 11/10)


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class FixtureIsolation(unittest.TestCase):
    def test_active_ambient_trial_is_untouched_by_real_round3_fixture(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            outside = pathlib.Path(td) / "ambient"
            controls = {name: type(getattr(seller, name))(root=outside)
                        for name in NAMES}
            deep = controls["DEEP_LOWBALL"]
            deep.root.mkdir(parents=True)
            now = time.time()
            (deep.root / "seller_concession_trial.json").write_text(json.dumps(
                dict(enabled=True, id="isolated-regression", start=now-10,
                     end=now+1790, seed=7)))
            margin = controls['MARGIN_STUDY']
            margin.root.mkdir(parents=True, exist_ok=True)
            pricing = dict(price=dict(high_median_up=dict(min_list=300000, factor=.97),
                                      high_price_trial=dict(share=0)))
            baseline = seller.sol_seller_margin.baseline_snapshot(pricing)
            (margin.root/'controller.json').write_text(json.dumps(dict(
                enabled=True, version=seller.sol_seller_margin.VERSION, study_id='ambient-margin-regression',
                epoch=now-10, start=now-10, end=now+43190,
                salt='ambient-margin-regression-salt',
                disk_snapshot_sha256=seller.sol_seller_margin.DISK_SNAPSHOT_SHA256,
                **seller.sol_source_load.proof()['claim'],
                baseline=baseline, baseline_sha256=seller.sol_seller_margin.digest(baseline))))
            margin.quote('ambient-fixture-task', dict(unit='u-anonymous', sku='G00000000',
                         grade='A', kind='goods', cost=100000, list_price=500000),
                         300000, .04, (), pricing, now=now)
            for name, value in controls.items():
                stack.enter_context(patch.object(seller, name, value))
            before = manifest(outside)
            ambient_access = []
            def guard_open(original):
                def guarded(path, *args, **kwargs):
                    if isinstance(path, (str, bytes, os.PathLike)) and pathlib.Path(os.fsdecode(path)).resolve().is_relative_to(outside):
                        ambient_access.append('file')
                        raise AssertionError('real fixture accessed ambient controller/state')
                    return original(path, *args, **kwargs)
                return guarded
            original_connect = sqlite3.connect
            def guarded_connect(database, *args, **kwargs):
                if str(outside) in os.fsdecode(database):
                    ambient_access.append('sqlite')
                    raise AssertionError('real fixture accessed ambient database')
                return original_connect(database, *args, **kwargs)
            case = round3.EndToEnd(methodName="test_food_deal_goes_all_the_way_with_a_checkable_certificate")
            with patch('builtins.open', guard_open(builtins.open)), \
                    patch('io.open', guard_open(io.open)), patch('sqlite3.connect', guarded_connect):
                try:
                    case.setUp()
                    result = case.deal("G1", 10000)
                    self.assertEqual(result["receipt"]["unit"], "u-food")
                    for name in NAMES:
                        self.assertTrue(pathlib.Path(getattr(seller, name).root).is_relative_to(case.tmp.name),
                                        name)
                finally:
                    case.doCleanups()
            self.assertEqual(ambient_access, [])
            self.assertEqual(manifest(outside), before)
            for name in NAMES:
                self.assertIs(getattr(seller, name), controls[name])


if __name__ == "__main__":
    unittest.main()
