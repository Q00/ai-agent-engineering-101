import copy,json,tempfile,unittest,uuid
from pathlib import Path
from unittest import mock
from food_opening_trial import FoodOpeningTrial

def quote():
    return dict(id="F1",unit="u-1",kind="food",cost=5000,reserve=6000,open=10000,ask=9700,
                rounds=0,history=[("you asked",9700)],expires_at=99999,
                opening_default={"policy":"opening-3pct-v1","price":9700},
                reader_trial_policy_sha="baseline")

class FoodOpeningTests(unittest.TestCase):
    def test_guarded_price_is_randomized_and_original_anchor_stays(self):
        with tempfile.TemporaryDirectory() as td:
            trial=FoodOpeningTrial(td);trial.root.mkdir()
            (trial.root/"seller_concession_trial.json").write_text(json.dumps(
                dict(enabled=True,id="food-trial",start=1000,end=2800,seed=11,stop=False)))
            arms=set()
            for i in range(10):
                deal=quote();baseline=copy.deepcopy(deal)
                trial.quote(str(uuid.uuid5(uuid.NAMESPACE_URL,"food-"+str(i))),deal,lambda d:9500,now=1100)
                assignment=deal["food_opening"]
                arms.add(assignment["arm"])
                self.assertEqual(deal["ask"],9600 if assignment["arm"]=="B" else 9700)
                for key in ("open","reserve","cost","unit","expires_at","opening_default"):
                    self.assertEqual(deal[key],baseline[key])
                self.assertGreaterEqual(deal["ask"],9500)
                self.assertEqual(deal["history"][0],("you asked",deal["ask"]))
            self.assertEqual(arms,{"A","B"})

    def configured(self,td):
        t=FoodOpeningTrial(td);t.root.mkdir()
        (t.root/"seller_concession_trial.json").write_text(json.dumps(
            dict(enabled=True,id="food-trial",start=1000,end=2800,seed=11,stop=False)))
        return t

    def test_off_is_noop_without_floor_or_database(self):
        with tempfile.TemporaryDirectory() as td:
            t=FoodOpeningTrial(td);d=quote();old=copy.deepcopy(d)
            floor=mock.Mock(side_effect=RuntimeError("unexpected floor"))
            t.quote(str(uuid.uuid4()),d,floor,now=1100)
            floor.assert_not_called()
            self.assertEqual(d,old)
            self.assertFalse(t.root.exists())

    def test_existing_first_floor_clamps_and_no_price_increase(self):
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td)
            for floor in (9650,9700,9800,True):
                d=quote();old=copy.deepcopy(d)
                t.quote(str(uuid.uuid4()),d,lambda _,f=floor:f,now=1100)
                self.assertGreaterEqual(d["ask"],min(9700,int(floor)))
                self.assertLessEqual(d["ask"],9700)
                if floor!=9650:self.assertEqual(d,old)

    def test_goods_round_and_bad_history_are_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td)
            variants=[dict(kind="goods"),dict(rounds=1),dict(history=[]),dict(cost=True)]
            for change in variants:
                d=quote();d.update(change);old=copy.deepcopy(d)
                t.quote(str(uuid.uuid4()),d,lambda _:9500,now=1100)
                self.assertEqual(d,old)

    def test_disable_new_A_and_old_B_sticky(self):
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td);chosen=None
            for i in range(4):
                task=str(uuid.uuid5(uuid.NAMESPACE_URL,"sticky-food-"+str(i)))
                d=quote();t.quote(task,d,lambda _:9500,now=1100)
                if d["food_opening"]["arm"]=="B":chosen=task
            self.assertIsNotNone(chosen)
            (t.root/"disabled").touch()
            floor=mock.Mock(side_effect=RuntimeError("unexpected floor"))
            d=quote();t.quote(str(uuid.uuid4()),d,floor,now=1100)
            floor.assert_not_called()
            self.assertEqual(d["ask"],9700);self.assertNotIn("food_opening",d)
            d=quote();t.quote(chosen,d,lambda _:9500,now=3000)
            self.assertEqual(d["ask"],9600)

    def test_snapshot_change_fails_A(self):
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td);chosen=None
            for i in range(4):
                task=str(uuid.uuid4());d=quote()
                t.quote(task,d,lambda _:9500,now=1100)
                if d["food_opening"]["arm"]=="B":chosen=task
            d=quote();d["cost"]=5001
            t.quote(chosen,d,lambda _:9500,now=1100)
            self.assertTrue(d["food_opening"]["contaminated"])
            self.assertEqual(d["ask"],9700)

    def test_other_active_experiment_blocks_but_adopted_is_common(self):
        from seller_concession_trial import Trial
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td);other=Trial(Path(td)/"other");other.root.mkdir()
            (other.root/"seller_concession_trial.json").write_text(json.dumps(
                dict(enabled=True,id="other",start=1000,end=2800,seed=1,stop=False)))
            d=quote();old=copy.deepcopy(d)
            floor=mock.Mock(side_effect=RuntimeError("unexpected floor"))
            t.quote(str(uuid.uuid4()),d,floor,now=1100,others=(other,))
            floor.assert_not_called()
            self.assertEqual(d,old)
            (t.root/"adopted").touch()
            d=quote();t.quote(str(uuid.uuid4()),d,lambda _:9500,now=1100,blocked=True,others=(other,))
            self.assertEqual(d["ask"],9600)

    def test_full_uuid_and_expired_window_are_noop(self):
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td)
            for task,now in (("short-id",1100),(str(uuid.uuid4()),2900)):
                d=quote();old=copy.deepcopy(d)
                floor=mock.Mock(side_effect=RuntimeError("unexpected floor"))
                t.quote(task,d,floor,now=now)
                floor.assert_not_called()
                self.assertEqual(d,old)

    def test_parallel_caps_and_balanced_assignments(self):
        import concurrent.futures,sqlite3
        with tempfile.TemporaryDirectory() as td:
            t=self.configured(td)
            def one(i):
                d=quote()
                t.quote(str(uuid.uuid5(uuid.NAMESPACE_URL,"cap-food-"+str(i))),d,lambda _:9500,now=1100)
            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
                list(pool.map(one,range(240)))
            with sqlite3.connect(t.root/"seller_concession_trial.sqlite3") as db:
                rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM assignments")]
            arms=[r["arm"] for r in rows if r.get("id")=="food-trial"]
            self.assertLessEqual(len(arms),200)
            self.assertLessEqual(arms.count("B"),100)
            self.assertLessEqual(abs(arms.count("A")-arms.count("B")),1)
