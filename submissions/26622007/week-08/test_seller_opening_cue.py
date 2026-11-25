"""Public message seam: wording changes while price/terms remain identical."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

class OpeningCueTests(unittest.TestCase):
    def test_new_adopted_quote_changes_only_the_text_not_prices_or_terms(self):
        from seller_opening_cue import OpeningCue
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "adopted").touch()
            deal = dict(id="sku", unit="u", kind="goods", cost=8000, reserve=9000,
                        open=11000, ask=10000, rounds=0, warranty=72,
                        history=[("you asked", 10000)])
            before = copy.deepcopy(deal)
            result = cue.message("00000000-0000-0000-0000-000000000001", deal,
                                 "Asking 10000.", now=101)
            self.assertEqual(result, "Asking 10000. Please send your offer as a number.")
            self.assertEqual({k: deal[k] for k in before}, before)

    def test_full_uuid_choice_is_durable_across_adoption_and_disable(self):
        from seller_opening_cue import OpeningCue
        def deal():
            return dict(id="sku", unit="u", kind="goods", cost=8000, reserve=9000,
                        open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
        first = "00000000-0000-0000-0000-000000000001"
        second = "00000000-0000-0000-0000-000000000002"
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            self.assertEqual(cue.message(first, deal(), "Asking 10000.", 101), "Asking 10000.")
            (cue.root / "adopted").touch()
            self.assertEqual(OpeningCue(root).message(first, deal(), "Asking 10000.", 102),
                             "Asking 10000.")
            treated = "Asking 10000. Please send your offer as a number."
            self.assertEqual(cue.message(second, deal(), "Asking 10000.", 103), treated)
            (cue.root / "disabled").touch()
            self.assertEqual(OpeningCue(root).message(second, deal(), "Asking 10000.", 104), treated)
            self.assertEqual(cue.message("new-control", deal(), "Asking 10000.", 105), "Asking 10000.")

    def test_active_pilot_balances_two_same_stratum_quotes_without_changing_ask(self):
        from seller_opening_cue import OpeningCue
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "seller_concession_trial.json").write_text(
                json.dumps(dict(enabled=True, id="cue-pilot", seed=7, start=100, end=1900)))
            shown = []
            for task in ("00000000-0000-0000-0000-000000000011",
                         "00000000-0000-0000-0000-000000000012"):
                d = dict(id="sku", unit=task, kind="goods", cost=8000, reserve=9000,
                         open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
                shown.append(cue.message(task, d, "Asking 10000.", now=101))
                self.assertEqual((d["ask"], d["open"], d["reserve"]), (10000, 11000, 9000))
            self.assertCountEqual(shown, ["Asking 10000.",
                "Asking 10000. Please send your offer as a number."])

    def test_paid_trace_stays_in_its_own_cohort_not_the_old_opening_trial(self):
        from seller_opening_cue import OpeningCue
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "adopted").touch()
            d = dict(id="sku", unit="u", kind="goods", cost=8000, reserve=9000,
                     open=11000, ask=10000, rounds=0, history=[("you asked", 10000)],
                     concession_trial=dict(id="historical-opening", arm="B", open=11000,
                                           reserve=9000, cost=8000, unit="u"))
            cue.message("new-cue-task", d, "Asking 10000.", 101)
            cue.event("new-cue-task", d, "deal", 10000, 201)
            rows = [json.loads(x) for x in
                    (cue.root / "seller_concession_trial.jsonl").read_text().splitlines()]
            self.assertEqual((rows[-1]["event"], rows[-1]["id"], rows[-1]["price"]),
                             ("deal", "adopted-v1", 10000))
            self.assertNotIn("Asking 10000.", json.dumps(rows))
            self.assertEqual(d["concession_trial"]["id"], "historical-opening")

    def test_pilot_cap_and_end_keep_new_quotes_in_control(self):
        from seller_opening_cue import OpeningCue, CUE
        def deal(i):
            return dict(id="sku", unit=f"u-{i}", kind="goods", cost=8000, reserve=9000,
                        open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "seller_concession_trial.json").write_text(
                json.dumps(dict(enabled=True, id="cue-pilot", seed=7, start=100, end=1900)))
            shown = [cue.message(f"00000000-0000-0000-0000-{i:012x}",
                                 deal(i), "Asking 10000.", 101) for i in range(205)]
            self.assertEqual(sum(x.endswith(CUE) for x in shown), 100)
            self.assertTrue(all(x == "Asking 10000." for x in shown[200:]))
            self.assertEqual(cue.message("after-end", deal(999), "Asking 10000.", 1900),
                             "Asking 10000.")

    def test_disabled_failure_and_nonopening_responses_keep_baseline(self):
        from seller_opening_cue import OpeningCue
        d = dict(id="sku", unit="u", kind="goods", cost=8000, reserve=9000,
                 open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "adopted").touch()
            (cue.root / "disabled").touch()
            self.assertEqual(cue.message("disabled", copy.deepcopy(d), "Asking 10000.", 101),
                             "Asking 10000.")
            (cue.root / "disabled").unlink()
            self.assertEqual(cue.message("round-1", dict(d, rounds=1), "Counter 10000.", 102),
                             "Counter 10000.")
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.write_text("not a directory")
            self.assertEqual(cue.message("storage-failed", copy.deepcopy(d), "Asking 10000.", 101),
                             "Asking 10000.")

    def test_reentry_with_other_unit_is_not_treated_or_repriced(self):
        from seller_opening_cue import OpeningCue
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "adopted").touch()
            d = dict(id="sku", unit="old-unit", kind="goods", cost=8000, reserve=9000,
                     open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
            cue.message("same-full-task", d, "Asking 10000.", 101)
            other = dict(d, unit="other-unit", ask=12000)
            other.pop("opening_cue")
            self.assertEqual(cue.message("same-full-task", other, "Asking 12000.", 102),
                             "Asking 12000.")
            self.assertEqual(other["ask"], 12000)
            rec = json.loads((cue.root / "seller_concession_trial.jsonl").read_text().splitlines()[-1])
            self.assertTrue(rec["contaminated"])

    def test_cue_keeps_existing_text_only_price_seam(self):
        from ask_read import read
        from seller_opening_cue import CUE
        self.assertEqual(read("Asking 10000.")[0], 10000)
        self.assertEqual(read("Asking 10000. " + CUE)[0], 10000)

class SellerCueBoundaryTests(unittest.TestCase):
    def quote(self, adopted):
        import os
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / "pricing.json").write_text("{}")
            (p / "memory.json").write_text('{"negotiations":[]}')
            (p / "late_concession").mkdir()
            (p / "late_concession" / "adopted").touch()
            if adopted:
                (p / "opening_cue").mkdir()
                (p / "opening_cue" / "adopted").touch()
            script = """
import asyncio,json,time
from types import SimpleNamespace
import seller
from shop_stock import StockBook
u=dict(unit="u",sku="G1",name="test item",category="food",kind="food",
       cost=6000,list_price=10000,grade="A",supplied_at=time.time()-3600,
       expires_at=time.time()+72*3600)
book=StockBook(lambda:dict(round=3,units=[u]))
shop=seller.Seller(catalog=None,name="fixture",stock=book)
class Capture:
 async def requires_input(self,message):self.message=message
 async def reject(self,message):raise AssertionError("unexpected rejected stock quote")
cap=Capture()
say=lambda text,ask=None:dict(text=text,metadata=dict(ask=ask))
task=SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
asyncio.run(shop.open_from_stock(task,"G1",cap,say))
d=shop.open[task.id]
print(json.dumps(dict(text=cap.message["text"],metadata=cap.message["metadata"],
 protected={k:d.get(k) for k in ("id","unit","kind","cost","reserve","ask","open","warranty","history")})))
"""
            env = dict(os.environ, MARKET_DIR=root, SHOP_PRICING=str(p / "pricing.json"),
                       SHOP_MEMORY=str(p / "memory.json"))
            r = subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).parent,
                               env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, "isolated first-stock-response fixture failed")
            return json.loads(r.stdout.splitlines()[-1])

    def test_real_stock_first_response_keeps_metadata_and_terms(self):
        control = self.quote(False)
        treated = self.quote(True)
        self.assertEqual(treated["metadata"], control["metadata"])
        self.assertEqual(treated["protected"], control["protected"])
        self.assertEqual(treated["text"],
                         control["text"] + " Please send your offer as a number.")

if __name__ == "__main__":
    unittest.main()
