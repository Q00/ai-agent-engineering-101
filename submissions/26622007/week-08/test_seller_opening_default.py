"""Public first-quote contract for the adopted (not experimental) opening."""
import tempfile
import unittest
from pathlib import Path
from seller_concession_trial import Trial

class OpeningDefaultTests(unittest.TestCase):
    def test_default_survives_trial_absence_and_rollback_preserves_sticky(self):
        from seller_opening_default import OpeningDefault
        with tempfile.TemporaryDirectory() as root:
            trial = Trial(root)
            policy = OpeningDefault(root)
            def deal():
                return dict(id="sku",unit="unit",kind="goods",cost=50,reserve=80,
                            open=100,ask=100,rounds=0,history=[("you asked",100)])
            d=deal()
            policy.quote("full-task-1",d,trial,100)
            self.assertEqual(d["ask"],97)
            self.assertEqual(d["open"],100)
            Path(root,"seller_opening_default.disabled").touch()
            policy.quote("full-task-1",d,trial,101)
            self.assertEqual(d["ask"],97)
            later=deal()
            policy.quote("full-task-2",later,trial,101)
            self.assertEqual(later["ask"],100)

    def test_reserve_cap_restart_and_historical_denominators(self):
        from seller_opening_default import OpeningDefault
        import json
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)
            p.joinpath("seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="old",start=100,end=1900,seed=1)))
            trial=Trial(root)
            def deal(reserve=80):
                return dict(id="sku",unit="unit",kind="goods",cost=50,reserve=reserve,
                            open=100,ask=100,rounds=0,history=[("you asked",100)])
            old=deal()
            trial.quote("historic",old,100)
            expected=old["ask"]
            policy=OpeningDefault(root)
            repeat=deal()
            policy.quote("historic",repeat,trial,2000)
            self.assertEqual(repeat["ask"],expected)
            for i in range(205):
                d=deal(99)
                policy.quote("new-"+str(i),d,trial,2000+i)
                self.assertEqual(d["ask"],99)
            replay=deal(99)
            OpeningDefault(root).quote("new-0",replay,trial,3000)
            self.assertEqual(replay["ask"],99)
            with sqlite3.connect(p/"seller_concession_trial.sqlite3") as db:
                self.assertEqual(db.execute("select count(*) from assignments").fetchone()[0],1)
            self.assertEqual(len(p.joinpath("seller_opening_default.jsonl").read_text().splitlines()),206)
