"""R1 (11/09): the exact shelf-life question without the Jev read. Real turns from the 11/09 ops log are in
r1_shelf_jev_real.json (text, ask, history, rounds, floor, the Jev ledger row and the harness answer of the turn)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import shelf_jev_skip
from shelf_jev_skip import Trial, eligible

HERE = Path(__file__).parent
CASES = json.loads((HERE / "r1_shelf_jev_real.json").read_text())
Q = "How many hours of shelf life does this unit have left? It must last at least {} hours."


def config(**kw):
    c = dict(study="r1-shelf-jev", salt="s1", version=1, epoch="2026-11-09T17:41", enabled=True, mode="ab",
             share=0.5, start_at=1000.0, stop_at=10 ** 12)
    c.update(kw)
    return c


def deal_of(case):
    return dict(id=case["sku"], name=case["sku"], kind="food", reserve=case["floor"], ask=case["ask"],
                open=case["history"][0][1], rounds=case["rounds"], history=[tuple(h) for h in case["history"]])


class FixtureShape(unittest.TestCase):
    def test_real_inputs(self):
        kits = {c["source"].split()[1] for c in CASES}
        for k in ("46217", "65441", "71092", "80747", "85575", "94308", "3657"):
            self.assertIn("market-kit.log:" + k, kits)
        for c in CASES:
            self.assertTrue(shelf_jev_skip.QUESTION.fullmatch(c["text"]), c["source"])
            self.assertEqual(c["jev_ledger"][0]["chars"], len(c["text"]))     # the ledger row is this text
            self.assertEqual(c["jev_ledger"][0]["ok"], not c.get("jev_timeout"))
            if c["harness_answer"]:
                self.assertEqual(c["harness_answer"]["buyer_offer"], 0)       # the harness never read an offer


class Eligibility(unittest.TestCase):
    def test_real_cases_eligible(self):
        for c in CASES:
            ok, why, hours, numbers = eligible(c["text"], deal_of(c), c["floor"])
            self.assertTrue(ok, (c["source"], why))
            self.assertEqual(why, "exact_shelf_question")
            self.assertTrue(all(n < c["floor"] for n in numbers))

    def test_negative_exceptions(self):
        d = dict(ask=26615, reserve=20000, rounds=1)
        for text, why in (
                ("I can offer 20000. " + Q.format(85), "not_question"),             # number + price
                (Q.format(85) + " I can pay 20000.", "not_question"),
                (Q.format(85) + " I need it by 18:00.", "not_question"),            # number + time
                (Q.format(85) + " Grade A only.", "not_question"),                  # grade mix
                ("Grade B? " + Q.format(85), "not_question"),
                ("85. " + Q.format(85), "not_question"),
                (Q.format(85).lower(), "not_question"),
                (Q.format(85).replace("hours.", "hours"), "not_question"),
                ("How many hours of shelf life? 85", "not_question"),
                (Q.format(26615), "number_at_or_above_floor"),                     # hours could be an offer
                (Q.format(30000), "number_at_or_above_floor"),
                (Q.format(25000), "number_at_or_above_floor"),                     # at the floor of 25000 below
                (None, "not_question"), (Q.format(85) + " " * 600, "not_question")):
            ok, got, _, _ = eligible(text, d, 25000 if "25000" in str(text) else 22000)
            self.assertFalse(ok, text)
            self.assertEqual(got, why, text)
        self.assertTrue(eligible(Q.format("85.5"), d, 22000)[0])
        self.assertTrue(eligible("  " + Q.format(85) + "\n", d, 22000)[0])
        for bad in (dict(d, ask=None), dict(d, reserve="1"), dict(d, ask=True)):
            self.assertEqual(eligible(Q.format(85), bad, 22000)[1], "deal_unknown")
        self.assertEqual(eligible(Q.format(85), d, 22000.0)[1], "deal_unknown")


class TrialUnit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.t = Trial(self.td.name)
        self.t.root.mkdir()
        os.environ.pop("SHELF_JEV_SKIP_CONFIG", None)

    def tearDown(self):
        self.td.cleanup()

    def write(self, **kw):
        (self.t.root / "config.json").write_text(json.dumps(config(**kw)))

    def rows(self):
        p = self.t.root / "events.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_off_without_config_or_with_invalid_config(self):
        c = CASES[0]
        d = deal_of(c)
        self.assertFalse(self.t.skip("t1", c["text"], d, c["floor"], now=2000))
        self.assertNotIn("shelf_jev", d)
        for bad in ([], "x", dict(config(), share=1.5), dict(config(), mode="all"), dict(config(), enabled=1),
                    dict(config(), version=0), dict(config(), start_at=5, stop_at=5), dict(config(), salt=""),
                    dict(config(), share=None), dict(config(), epoch=None), dict(config(), share=True)):
            (self.t.root / "config.json").write_text(json.dumps(bad))
            self.assertFalse(self.t.skip("t1", c["text"], d, c["floor"], now=2000), bad)
        (self.t.root / "config.json").write_text("{not json")
        self.assertFalse(self.t.skip("t1", c["text"], d, c["floor"], now=2000))
        self.write(enabled=False)
        self.assertFalse(self.t.skip("t1", c["text"], d, c["floor"], now=2000))
        self.write(start_at=3000)
        self.assertFalse(self.t.skip("t1", c["text"], d, c["floor"], now=2000))
        self.write(stop_at=2000)
        self.assertFalse(self.t.skip("t1", c["text"], d, c["floor"], now=2000))
        self.assertNotIn("shelf_jev", d)
        self.assertEqual(self.rows(), [])

    def test_hash_both_arms_sticky_and_journal(self):
        self.write()
        c = CASES[0]
        arms = {}
        for i in range(200):
            d = deal_of(c)
            task = f"task-{i}"
            got = self.t.skip(task, c["text"], d, c["floor"], now=2000)
            key = f"s1|r1-shelf-jev|1|{task}".encode()
            want = "B" if int.from_bytes(hashlib.sha256(key).digest(), "big") < 0.5 * (1 << 256) else "A"
            self.assertEqual(d["shelf_jev"]["arm"], want)
            self.assertEqual(got, want == "B")
            arms[task] = (d, want)
        self.assertEqual({w for _, w in arms.values()}, {"A", "B"})
        self.assertEqual(len(self.rows()), 200)
        # sticky: a later share does not move a talk already assigned
        d, want = arms["task-0"]
        self.write(share=0.0 if want == "B" else 1.0)
        self.assertEqual(self.t.skip("task-0", c["text"], d, c["floor"], now=2100), want == "B")
        self.assertEqual(d["shelf_jev"]["arm"], want)
        r = self.rows()[-1]
        for k in ("study", "version", "epoch", "arm", "eligible", "reason", "jev_skipped", "required_hours", "numbers",
                  "ask", "floor", "reserve", "least", "sku", "input_sha256", "others", "randomization_unit"):
            self.assertIn(k, r)
        self.assertEqual(r["input_sha256"], hashlib.sha256(c["text"].encode()).hexdigest())
        self.assertNotIn("text", r)
        self.assertEqual(oct((self.t.root / "events.jsonl").stat().st_mode & 0o777), "0o600")

    def test_new_study_reassigns(self):
        self.write(share=1.0)
        c = CASES[0]
        d = deal_of(c)
        self.assertTrue(self.t.skip("t", c["text"], d, c["floor"], now=2000))
        self.write(share=0.0, version=2)
        self.assertFalse(self.t.skip("t", c["text"], d, c["floor"], now=2000))
        self.assertEqual((d["shelf_jev"]["version"], d["shelf_jev"]["arm"]), (2, "A"))

    def test_ineligible_question_is_logged_but_never_skipped(self):
        self.write(share=1.0)
        d = dict(deal_of(CASES[0]), reserve=20000)
        self.assertFalse(self.t.skip("t", Q.format(30000), d, 22000, now=2000))
        self.assertEqual(self.rows()[-1]["reason"], "number_at_or_above_floor")
        self.assertFalse(self.rows()[-1]["jev_skipped"])
        n = len(self.rows())
        self.assertFalse(self.t.skip("t", "I can offer 20000.", d, 22000, now=2000))   # not the question: no row
        self.assertEqual(len(self.rows()), n)

    def test_adopted_mode_skips_every_eligible_turn(self):
        self.write(mode="adopted", share=0.0)
        for i, c in enumerate(CASES):
            d = deal_of(c)
            self.assertTrue(self.t.skip(f"t{i}", c["text"], d, c["floor"], now=2000))
            self.assertEqual(d["shelf_jev"]["arm"], "adopted")

    def test_journal_failure_keeps_control(self):
        self.write(share=1.0)
        c = CASES[0]
        d = deal_of(c)
        (self.t.root / "events.jsonl").mkdir()          # the append cannot open
        self.assertFalse(self.t.skip("t", c["text"], d, c["floor"], now=2000))
        self.assertNotIn("shelf_jev", d)
        self.assertEqual(d["shelf_jev_turn"], dict(skipped=False, error=True))
        self.t.decided("t", d, None, None)               # no assignment: nothing to write, no raise
        self.t.event("t", d, "deal", 1)

    def test_outcome_rows_and_cross_tab(self):
        self.write(share=1.0)
        c = CASES[1]
        d = deal_of(c)
        d["sol_margin"] = dict(study="sol-margin", arm="treatment", cell="P1W0", epoch="e1", secret_like="x" * 3)
        d["warranty_trial"] = dict(study="w", arm="control", version=3)
        self.t.skip("t", c["text"], d, c["floor"], now=2000)

        class R:
            jev_calls, source = 0, "none"
        self.t.decided("t", d, None, R())
        self.t.event("t", d, "deal", c["ask"], now=2500)
        turn, dec, out = self.rows()
        self.assertEqual(turn["others"]["sol_margin"], dict(study="sol-margin", arm="treatment", cell="P1W0", epoch="e1"))
        self.assertEqual(turn["others"]["warranty_trial"], dict(study="w", arm="control", version=3))
        self.assertEqual((dec["event"], dec["harness_used"], dec["jev_calls"], dec["jev_skipped"]), ("decision", True, 0, True))
        self.assertEqual((out["event"], out["price"], out["arm"]), ("deal", c["ask"], "B"))
        self.assertIn("sol_margin", out["others"])

    def test_fifo_config_or_journal_never_blocks(self):
        os.mkfifo(self.t.root / "config.json")
        c = CASES[0]
        self.assertIsNone(self.t.config(now=2000))
        os.unlink(self.t.root / "config.json")
        self.write(share=1.0)
        os.mkfifo(self.t.root / "events.jsonl")
        d = deal_of(c)
        self.assertFalse(self.t.skip("t", c["text"], d, c["floor"], now=2000))
        self.assertNotIn("shelf_jev", d)

    def test_one_decision_row_per_turn(self):
        self.write(share=1.0)
        c = CASES[0]
        d = deal_of(c)
        self.t.skip("t", c["text"], d, c["floor"], now=2000)
        self.t.decided("t", d, None, None)
        self.t.decided("t", d, None, None)                # a later turn without skip(): no stale row
        self.assertEqual([r["event"] for r in self.rows()], ["turn", "decision"])

    def test_env_config_path(self):
        p = Path(self.td.name) / "elsewhere.json"
        p.write_text(json.dumps(config(share=1.0)))
        os.environ["SHELF_JEV_SKIP_CONFIG"] = str(p)
        try:
            c = CASES[0]
            self.assertTrue(self.t.skip("t", c["text"], deal_of(c), c["floor"], now=2000))
        finally:
            os.environ.pop("SHELF_JEV_SKIP_CONFIG", None)


SEAM = r'''
import asyncio,json,sys,time,uuid
from types import SimpleNamespace
from unittest.mock import patch
import seller,fast_classify
from shop_stock import StockBook
cases=json.loads(open('r1_shelf_jev_real.json').read())
mode=sys.argv[1]
root=seller.SHELF_JEV.root
if mode!='off':
 root.mkdir(parents=True,exist_ok=True)
 (root/'config.json').write_text(json.dumps(dict(study='r1-shelf-jev',salt='s',version=1,epoch='t',enabled=True,
  mode='adopted' if mode=='adopted' else 'ab',share=1.0 if mode=='B' else 0.0,start_at=time.time()-5,stop_at=time.time()+3600)))
Q="How many hours of shelf life does this unit have left? It must last at least {} hours."
class Capture:
 def new_agent_message(self,**kw):return kw
 async def requires_input(self,message):self.message=message
 async def reject(self,message):raise AssertionError('reject')
 async def failed(self,message):raise AssertionError('failed')
shop=seller.Seller(None,'fixture',stock=StockBook(lambda:dict(round=3,units=[])))
out=[]
async def turn(c,text,timeout=False,shelf=None):
 cap=Capture();task=SimpleNamespace(id=str(uuid.uuid4()),context_id='ctx')
 d=dict(id=c['sku'],name=c['sku'],kind='food',unit=None,reserve=c['floor'],ask=c['ask'],open=c['history'][0][1],
        rounds=c['rounds']-1,history=[tuple(h) for h in c['history']])
 shop.open[task.id]=d
 jev=[];harness=[]
 def fake_jev(t,*a,**k):
  jev.append(t);return fast_classify.JEV_DOWN if timeout else (None,0.0)
 answer=c['harness_answer'] or dict(buyer_offer=0,action='counter',price=c['ask'],message='')
 def fake_harness(deal,t):
  harness.append(t);return dict(answer)
 with patch.object(seller,'TaskUpdater',return_value=cap),patch.object(seller,'get_message_text',return_value=text),\
      patch.object(seller,'new_text_part',side_effect=lambda t:t),patch.object(seller,'turn_floor',return_value=c['floor']),\
      patch.object(seller.fast_classify,'JEV_ON',True),patch.object(fast_classify,'jev_offer',side_effect=fake_jev),\
      patch.object(seller,'decide_turn',side_effect=fake_harness),patch.object(seller.SHELF_REPLY,'reply',return_value=shelf):
  await shop._execute(SimpleNamespace(current_task=task,message=None),None)
 out.append(dict(source=c['source'],text=text,timeout=timeout,jev=len(jev),harness=len(harness),
  harness_text=harness[0] if harness else None,reply=cap.message['parts'][0],ask_meta=(cap.message.get('metadata') or {}).get('ask'),
  ask_after=d['ask'],history_tail=list(d['history'][-1]),jev_held=bool(d.get('jev_held')),arm=(d.get('shelf_jev') or {}).get('arm')))
 shop.paid(dict(task=task.id,deal=d,price=c['ask']))
async def run():
 for c in cases:
  await turn(c,c['text'],timeout=bool(c.get('jev_timeout')))
 c=cases[0]
 await turn(c,c['text'],timeout=True)                                   # every real case also under a timeout
 for text in ('I can offer 20000. '+Q.format(85),Q.format(85)+' Grade A only.','85. '+Q.format(85),Q.format(26615)):
  await turn(c,text)
 await turn(c,c['text'],shelf='The unit has 120 hours of shelf life left. My price: %d.'%c['ask'])   # shelf_life_reply answered: no Jev either way
with patch.object(seller,'ask',side_effect=AssertionError('unexpected external harness')):
 asyncio.run(run())
seller.MEMORY_WRITER.shutdown(wait=True)
rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
print('SEAM_JSON '+json.dumps(dict(turns=out,rows=rows)))
'''


class Attestation(unittest.TestCase):
    def test_margin_study_attests_this_module(self):
        import sol_seller_margin as margin
        self.assertIn("shelf_jev_skip.py", margin.SOURCE_FILES)
        self.assertIn("shelf_jev_skip.py", margin.DISK_SNAPSHOT)


class SellerSeam(unittest.TestCase):
    """seller._execute on the real turns: off/A/B/adopted, the Jev timeout and the negative exceptions."""
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
                env.pop("SHELF_JEV_SKIP_CONFIG", None)
                r = subprocess.run([sys.executable, "-B", "-c", SEAM, mode], env=env, cwd=HERE,
                                   capture_output=True, text=True, timeout=60)
                line = [x for x in r.stdout.splitlines() if x.startswith("SEAM_JSON ")]
                assert r.returncode == 0 and line, r.stdout[-3000:] + r.stderr[-3000:]
                cls.runs[mode] = json.loads(line[0][len("SEAM_JSON "):])
        return cls.runs[mode]

    def real(self, mode):
        n = len(CASES)
        turns = self.run_mode(mode)["turns"]
        return turns[:n], turns[n], turns[n + 1:-1]

    def test_shelf_reply_turn_is_not_counted(self):
        for mode in ("off", "A", "B", "adopted"):
            t = self.run_mode(mode)["turns"][-1]
            self.assertEqual((t["jev"], t["harness"]), (0, 0), mode)
            self.assertTrue(t["reply"].startswith("The unit has 120 hours"), mode)

    def test_control_paths_unchanged(self):
        for mode in ("off", "A"):
            real, timeout, negatives = self.real(mode)
            for t, c in zip(real, CASES):
                self.assertEqual(t["jev"], 1, (mode, t["source"]))
                self.assertEqual(t["reply"], f"Please tell me the price you offer. My price: {c['ask']}.")
                self.assertEqual((t["ask_meta"], t["ask_after"]), (c["ask"], c["ask"]))
                if c.get("jev_timeout"):          # ops 09:45:36 f785d897: HOLD, no harness
                    self.assertEqual((t["harness"], t["jev_held"]), (0, True))
                else:
                    self.assertEqual((t["harness"], t["harness_text"], t["jev_held"]), (1, c["text"], False))
            self.assertEqual((timeout["jev"], timeout["harness"], timeout["jev_held"]), (1, 0, True))
            for t in negatives:
                self.assertEqual(t["jev"], 1, t["text"])
        self.assertEqual(self.run_mode("off")["rows"], [])
        self.assertTrue(all(r["arm"] == "A" and not r.get("jev_skipped") for r in self.run_mode("A")["rows"]))
        held = [r for r in self.run_mode("A")["rows"] if r["event"] == "decision" and r["held_this_turn"]]
        self.assertEqual(len(held), 2)                    # f785d897 and the extra timeout turn hold the ask in A

    def test_treatment_skips_jev_with_the_same_reply(self):
        for mode in ("B", "adopted"):
            ctrl, _, _ = self.real("A")
            real, timeout, negatives = self.real(mode)
            for t, a, c in zip(real, ctrl, CASES):
                self.assertEqual(t["jev"], 0, (mode, t["source"]))
                self.assertEqual((t["harness"], t["harness_text"]), (1, c["text"]))   # Jev down no longer holds
                self.assertFalse(t["jev_held"])
                self.assertEqual(t["reply"], a["reply"])                              # the buyer sees the same line
                self.assertEqual((t["ask_meta"], t["ask_after"], t["history_tail"]),
                                 (a["ask_meta"], a["ask_after"], a["history_tail"]))
                self.assertEqual(t["arm"], "B" if mode == "B" else "adopted")
            self.assertEqual((timeout["jev"], timeout["harness"], timeout["jev_held"]), (0, 1, False))
            for t in negatives:                    # number + price, grade mix, prefixed number, hours >= floor
                self.assertEqual(t["jev"], 1, (mode, t["text"]))
            rows = self.run_mode(mode)["rows"]
            turns = [r for r in rows if r["event"] == "turn"]
            self.assertEqual(sum(r["jev_skipped"] for r in turns), len(CASES) + 1)
            self.assertEqual([r["reason"] for r in turns if not r["eligible"]], ["number_at_or_above_floor"])
            decisions = [r for r in rows if r["event"] == "decision"]
            self.assertEqual(len(decisions), len(turns))                          # one decision row per turn row
            self.assertEqual(sum(r["jev_skipped"] for r in decisions), len(CASES) + 1)
            self.assertTrue(all(r["jev_calls"] == 0 and r["harness_used"] and not r["jev_held"] and not r["held_this_turn"]
                                for r in decisions if r["jev_skipped"]))
            self.assertEqual(sum(r["event"] == "deal" for r in rows), len(turns))


if __name__ == "__main__":
    unittest.main()
