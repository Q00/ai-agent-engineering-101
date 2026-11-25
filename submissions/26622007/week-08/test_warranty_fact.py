"""W-fact (11/10): the exact goods warranty question answered from the warranty the shop already put on this talk.
Real turns from the 11/10 06:20-07:00 ops log are in w_fact_real.json (text, ask, unit, the WARRANTY line the seller
logged at the opening, the harness answer, the end)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import warranty_fact

HERE = Path(__file__).parent
CASES = json.loads((HERE / "w_fact_real.json").read_text())
Q = ("Does this unit come with a warranty of at least {} hours written on the receipt? I only buy goods with one. "
     "If you have a unit with it, offer that one and its price.")


def config(**kw):
    c = dict(study="w-fact-test", salt="t1", version=1, epoch="2026-11-10T07:30", enabled=True, mode="ab",
             share=0.5, start_at=1794230000.0, stop_at=1794230000.0 + 20 * 3600)
    c.update(kw)
    return c


def deal_of(case, **kw):
    d = dict(id=case["sku"], name=case["sku"], kind=case["kind"], unit=case["unit"], warranty=case["warranty"],
             reserve=case["ask"], ask=case["ask"], open=case["ask"], rounds=1, history=[("you asked", case["ask"])])
    d.update(kw)
    return d


NOW = 1794235000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WARRANTY_FACT_CONFIG", None)
        self.trial = warranty_fact.Trial(root=self.tmp.name)

    def on(self, **kw):
        self.trial.root.mkdir(parents=True, exist_ok=True)
        (self.trial.root / "config.json").write_text(json.dumps(config(**kw)))

    def rows(self):
        p = self.trial.root / "events.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def arm_of(self, task, cfg=None):
        cfg = cfg or config()
        key = f"{cfg['salt']}|{cfg['study']}|{cfg['version']}|{task}".encode()
        return "B" if int.from_bytes(hashlib.sha256(key).digest(), "big") < cfg["share"] * (1 << 256) else "A"


class FixtureShape(Base):
    def test_real_turns(self):
        self.assertEqual(len(CASES), 14)
        for c in CASES:
            self.assertEqual(c["text"], Q.format(int(c["required"])), c["source"])
            self.assertEqual(c["kind"], "goods")
            self.assertGreaterEqual(c["warranty"], c["required"])      # the unit carried the warranty asked for
            raw = [c["raw_log"][k] for k in sorted(c["raw_log"], key=int)]
            self.assertIn(f"seller task {c['task'][:8]} -> INPUT_REQUIRED {c['sku']} ask", raw[0])
            self.assertIn(f"WARRANTY {c['warranty']:g} h for {c['sku']}:", raw[1])
            self.assertIn(f"PRICE {c['sku']} unit {c['unit']} ", raw[2])
            self.assertIn(f"seller task {c['task'][:8]} <- '{c['text']}'", raw[3])
            self.assertIn(json.dumps(c["harness_message"], ensure_ascii=False)[1:-1], raw[4])
            self.assertIn(f"seller task {c['task'][:8]} -> {c['end']}", raw[5])
        self.assertEqual(sum(c["harness_negative"] for c in CASES), 13)
        self.assertEqual({c["end"] for c in CASES}, {"CANCELED"})


class Answer(Base):
    def test_off_by_default(self):
        c = CASES[0]
        self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=NOW))
        self.assertEqual(self.rows(), [])

    def test_real_turns_get_the_true_warranty_in_b_and_nothing_in_a(self):
        self.on()
        for c in CASES:
            deal = deal_of(c)
            got = self.trial.answer(c["task"], c["text"], deal, now=NOW)
            arm = self.arm_of(c["task"])
            self.assertEqual(deal["warranty_fact"]["arm"], arm)
            if arm == "B":
                self.assertEqual(got, "Yes. This unit comes with a 72 h warranty written on the signed receipt: if it "
                                      f"breaks within 72 h, you get the full price back. My price: {c['ask']}.")
            else:
                self.assertIsNone(got)
        rows = self.rows()
        self.assertEqual(len(rows), 14)
        self.assertEqual({r["arm"] for r in rows}, {"A", "B"})
        for r, c in zip(rows, CASES):
            self.assertEqual((r["event"], r["task"], r["unit"], r["warranty_hours"], r["required_hours"]),
                             ("turn", c["task"], c["unit"], 72.0, 6.0))
            self.assertEqual(r["answered"], r["arm"] == "B")
            self.assertNotIn("text", r)
            self.assertEqual(r["input_sha256"], hashlib.sha256(c["text"].encode()).hexdigest())

    def test_the_arm_is_sticky_for_the_talk(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.answer(c["task"], c["text"], deal, now=NOW)
        first = dict(deal["warranty_fact"])
        self.trial.answer(c["task"], Q.format(24), deal, now=NOW + 60)
        self.assertEqual(deal["warranty_fact"], first)
        self.assertEqual([r["assigned_at"] for r in self.rows()], [NOW, NOW])

    def test_less_warranty_than_asked_or_none_is_the_original_path_in_both_arms(self):
        self.on(share=1.0)
        c = CASES[0]
        for deal in (deal_of(c, warranty=6), deal_of(c, warranty=0), deal_of(c, warranty=float("nan")),
                     deal_of(c, warranty="72"), {k: v for k, v in deal_of(c).items() if k != "warranty"},
                     deal_of(c, warranty_trial={"arm": "treatment"})):
            self.assertIsNone(self.trial.answer(c["task"], Q.format(24), deal, now=NOW))
            self.assertNotIn("warranty_fact", deal)
        self.assertEqual(self.rows(), [])

    def test_exactly_the_hours_asked_is_answered(self):
        self.on(share=1.0)
        c = CASES[0]
        self.assertIn("a 24 h warranty", self.trial.answer(c["task"], Q.format(24), deal_of(c, warranty=24), now=NOW))

    def test_other_messages_food_and_unknown_units_are_left_alone(self):
        self.on(share=1.0)
        c = CASES[0]
        for text, deal in ((c["text"] + " I can pay 9000.", deal_of(c)),
                           ("Does this unit come with a warranty?", deal_of(c)),
                           (c["text"].replace("written on the receipt", "on the receipt"), deal_of(c)),
                           (c["text"], deal_of(c, kind="food")),
                           (c["text"], deal_of(c, unit=None)),
                           (c["text"], deal_of(c, ask=0)),
                           (c["text"], deal_of(c, ask=12.5)),
                           (None, deal_of(c)),
                           (c["text"] + " " * 600, deal_of(c))):
            self.assertIsNone(self.trial.answer(c["task"], text, deal, now=NOW))
            self.assertNotIn("warranty_fact", deal)
        self.assertEqual(self.rows(), [])

    def test_surrounding_whitespace_is_the_same_question(self):
        self.on(share=1.0)
        c = CASES[0]
        self.assertIsNotNone(self.trial.answer(c["task"], "  " + c["text"] + "\n", deal_of(c), now=NOW))


class Window(Base):
    def test_outside_the_window_or_too_long_or_disabled_is_off(self):
        c = CASES[0]
        for kw, now in ((dict(), 1794230000.0 - 1), (dict(), 1794230000.0 + 20 * 3600),
                        (dict(stop_at=1794230000.0 + 20 * 3600 + 1), NOW), (dict(enabled=False), NOW),
                        (dict(mode="other"), NOW), (dict(share=1.5), NOW), (dict(salt=""), NOW)):
            self.on(**kw)
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=now), kw)
        self.assertEqual(self.rows(), [])

    def test_exactly_twenty_hours_is_allowed(self):
        self.on(share=1.0, stop_at=1794230000.0 + 20 * 3600)
        c = CASES[0]
        self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=NOW))

    def test_invalid_or_non_regular_config_is_off(self):
        c = CASES[0]
        self.trial.root.mkdir(parents=True, exist_ok=True)
        (self.trial.root / "config.json").write_text("{not json")
        self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=NOW))
        (self.trial.root / "config.json").unlink()
        (self.trial.root / "config.json").mkdir()
        self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=NOW))

    def test_the_env_switch_path_wins(self):
        other = Path(self.tmp.name) / "elsewhere.json"
        other.write_text(json.dumps(config(share=1.0)))
        os.environ["WARRANTY_FACT_CONFIG"] = str(other)
        c = CASES[0]
        self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=NOW))

    def test_adopted_answers_every_eligible_turn(self):
        self.on(mode="adopted", share=0.0)
        for c in CASES:
            deal = deal_of(c)
            self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal, now=NOW))
            self.assertEqual(deal["warranty_fact"]["arm"], "adopted")

    def test_a_new_epoch_or_salt_reassigns(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.answer(c["task"], c["text"], deal, now=NOW)
        self.on(epoch="2026-11-10T09:00", salt="t2")
        self.trial._cache = None
        self.trial.answer(c["task"], c["text"], deal, now=NOW + 1)
        self.assertEqual(deal["warranty_fact"]["epoch"], "2026-11-10T09:00")
        self.assertEqual(deal["warranty_fact"]["arm"], self.arm_of(c["task"], config(salt="t2")))


class Journal(Base):
    def test_a_failed_write_keeps_the_original_path(self):
        self.on(share=1.0)
        c = CASES[0]
        deal = deal_of(c)
        with mock.patch.object(self.trial, "_write", side_effect=OSError("disk")):
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal, now=NOW))
        self.assertNotIn("warranty_fact", deal)

    def test_outcome_rows_for_assigned_talks_only(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.event(c["task"], deal, "canceled", None, now=NOW + 5)
        self.assertEqual(self.rows(), [])
        self.trial.answer(c["task"], c["text"], deal, now=NOW)
        self.trial.event(c["task"], deal, "deal", c["ask"], now=NOW + 5)
        last = self.rows()[-1]
        self.assertEqual((last["event"], last["task"], last["price"], last["arm"]),
                         ("deal", c["task"], c["ask"], deal["warranty_fact"]["arm"]))

    def test_floor_margin_and_gbt_style_assignments_are_kept_for_the_cross_table(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c, floor_margin={"study": "floor-margin-2040", "arm": "B", "text": "x"},
                       a_open={"study": "a-open", "arm": "A"}, sol_margin={"study_id": "s", "cell": "c"})
        self.trial.answer(c["task"], c["text"], deal, now=NOW)
        others = self.rows()[0]["others"]
        self.assertEqual(others["floor_margin"], {"study": "floor-margin-2040", "arm": "B"})
        self.assertEqual(others["a_open"], {"study": "a-open", "arm": "A"})
        self.assertEqual(others["sol_margin"], {"study_id": "s", "cell": "c"})

    def test_event_never_raises(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.answer(c["task"], c["text"], deal, now=NOW)
        with mock.patch.object(self.trial, "_write", side_effect=OSError("disk")):
            self.trial.event(c["task"], deal, "deal", 1)

    def test_answer_never_raises(self):
        self.on()
        c = CASES[0]
        with mock.patch.object(warranty_fact, "factual_answer", side_effect=RuntimeError("x")):
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=NOW))


class SellerWiring(unittest.TestCase):
    def test_seller_asks_w_fact_only_when_no_shelf_answer_was_given(self):
        src = (HERE / "seller.py").read_text()
        i = src.index("shelf_text = SHELF_FACT.answer(task.id, text, deal, now=shelf_now)")
        self.assertIn("if shelf_text is None:\n            shelf_text = WARRANTY_FACT.answer(task.id, text, deal, now=shelf_now)",
                      src[i:i + 400])
        self.assertIn("WARRANTY_FACT = warranty_fact.Trial()", src)
        self.assertIn("WARRANTY_FACT.event(task_id, deal, result, price, observed_at)", src)

    def test_the_b_line_is_what_the_buyer_reads(self):
        # the answered turn is a counter at the ask with no number in the buyer's message: seller.py sends shelf_text
        src = (HERE / "seller.py").read_text()
        self.assertIn("await up.requires_input(say(shelf_text if shelf_text is not None else", src)


SEAM = r"""
import asyncio,json,sys,time,uuid
from types import SimpleNamespace
from unittest.mock import patch
import seller,fast_classify
from shop_stock import StockBook
cases=json.loads(open('w_fact_real.json').read())
mode=sys.argv[1]
root=seller.WARRANTY_FACT.root
if mode!='off':
 root.mkdir(parents=True,exist_ok=True)
 (root/'config.json').write_text(json.dumps(dict(study='w-fact',salt='s',version=1,epoch='t',enabled=True,mode='ab',
  share=1.0 if mode=='B' else 0.0,start_at=time.time()-5,stop_at=time.time()+3600)))
class Capture:
 def new_agent_message(self,**kw):return kw
 async def requires_input(self,message):self.message=message
 async def reject(self,message):raise AssertionError('reject')
 async def failed(self,message):raise AssertionError('failed')
shop=seller.Seller(None,'fixture',stock=StockBook(lambda:dict(round=3,units=[])))
out=[]
async def turn(c,text,**kw):
 cap=Capture();task=SimpleNamespace(id=str(uuid.uuid4()),context_id='ctx')
 d=dict(id=c['sku'],name=c['sku'],kind='goods',unit=c['unit'],warranty=c['warranty'],reserve=c['ask'],ask=c['ask'],
        open=c['ask'],rounds=1,history=[('you asked',c['ask'])])
 d.update(kw)
 shop.open[task.id]=d
 harness=[]
 def fake_harness(deal,t):
  harness.append(t);return dict(buyer_offer=0,action='counter',price=c['ask'],message=c['harness_message'])
 with patch.object(seller,'TaskUpdater',return_value=cap),patch.object(seller,'get_message_text',return_value=text),\
      patch.object(seller,'new_text_part',side_effect=lambda t:t),patch.object(seller,'turn_floor',return_value=c['ask']),\
      patch.object(seller.fast_classify,'JEV_ON',False),patch.object(seller,'decide_turn',side_effect=fake_harness),\
      patch.object(shop.stock,'available',return_value=True),patch.object(shop.stock,'hold'):
  await shop._execute(SimpleNamespace(current_task=task,message=None),None)
 out.append(dict(source=c['source'],harness=len(harness),reply=cap.message['parts'][0],
  ask_meta=(cap.message.get('metadata') or {}).get('ask'),ask_after=d['ask'],arm=(d.get('warranty_fact') or {}).get('arm')))
 shop.remember(task.id,d,'canceled')
async def run():
 for c in cases:
  await turn(c,c['text'])
 c=cases[0]
 await turn(c,c['text'],warranty=3)                      # less than asked: the original path
 await turn(c,c['text'],kind='food',expires_at=time.time()+3600*100)    # food: never this module
with patch.object(seller,'ask',side_effect=AssertionError('unexpected external harness')):
 asyncio.run(run())
seller.MEMORY_WRITER.shutdown(wait=True)
rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
print('SEAM_JSON '+json.dumps(dict(turns=out,rows=rows)))
"""


class SellerSeam(unittest.TestCase):
    """seller._execute on the 14 real turns: what the buyer reads in off, A and B."""
    runs = {}

    @classmethod
    def run_mode(cls, mode):
        if mode not in cls.runs:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td)
                (p / "pricing.json").write_text("{}")
                (p / "memory.json").write_text('{"negotiations":[]}')
                env = dict(os.environ, MARKET_DIR=td, SHOP_PRICING=str(p / "pricing.json"),
                           SHOP_MEMORY=str(p / "memory.json"), NEGOTIATE_ENGINE="jevrule")
                env.pop("WARRANTY_FACT_CONFIG", None)
                r = subprocess.run([sys.executable, "-B", "-c", SEAM, mode], env=env, cwd=HERE,
                                   capture_output=True, text=True, timeout=60)
                line = [x for x in r.stdout.splitlines() if x.startswith("SEAM_JSON ")]
                assert r.returncode == 0 and line, r.stdout[-3000:] + r.stderr[-3000:]
                cls.runs[mode] = json.loads(line[0][len("SEAM_JSON "):])
        return cls.runs[mode]

    def test_control_never_answers_the_question(self):
        # ops 11/10: the harness text is dropped on a turn without an offer; the buyer reads only this line
        for mode in ("off", "A"):
            turns = self.run_mode(mode)["turns"]
            for t, c in zip(turns, CASES):
                self.assertEqual(t["harness"], 1, (mode, t["source"]))
                self.assertEqual(t["reply"], f"Please tell me the price you offer. My price: {c['ask']}.")
                self.assertEqual((t["ask_meta"], t["ask_after"]), (c["ask"], c["ask"]))
        self.assertEqual(self.run_mode("off")["rows"], [])
        rows = self.run_mode("A")["rows"]
        self.assertEqual(sum(r["event"] == "turn" and r["arm"] == "A" and not r["answered"] for r in rows), len(CASES))

    def test_treatment_states_the_warranty_at_the_same_ask_without_the_harness(self):
        turns = self.run_mode("B")["turns"]
        for t, c in zip(turns, CASES):
            self.assertEqual(t["harness"], 0, t["source"])
            self.assertEqual(t["reply"], "Yes. This unit comes with a 72 h warranty written on the signed receipt: if it "
                                         f"breaks within 72 h, you get the full price back. My price: {c['ask']}.")
            self.assertEqual((t["ask_meta"], t["ask_after"], t["arm"]), (c["ask"], c["ask"], "B"))
        short, food = turns[len(CASES)], turns[len(CASES) + 1]
        for t in (short, food):
            self.assertEqual((t["harness"], t["arm"]), (1, None))
            self.assertTrue(t["reply"].startswith("Please tell me the price you offer."))
        rows = self.run_mode("B")["rows"]
        self.assertEqual(sum(r["event"] == "turn" and r["answered"] for r in rows), len(CASES))
        self.assertEqual(sum(r["event"] == "canceled" for r in rows), len(CASES))       # outcome row per assigned talk


class Attestation(unittest.TestCase):
    def test_margin_study_attests_this_module(self):
        import sol_seller_margin as margin
        self.assertIn("warranty_fact.py", margin.SOURCE_FILES)
        self.assertIn("warranty_fact.py", margin.DISK_SNAPSHOT)

    def test_meta_harness_watches_the_switch(self):
        import meta_harness
        src = Path(warranty_fact.__file__).read_text(encoding="utf-8")
        self.assertIn("\nSwitch: .market/warranty_fact/config.json", src)
        self.assertIn(".market/warranty_fact/config.json", meta_harness.SWITCHES)


if __name__ == "__main__":
    unittest.main()
