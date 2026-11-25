"""Limited deterministic 1-won opener probe, no live market/model calls."""
import json
import concurrent.futures
import os
import tempfile
import time
import unittest
from unittest import mock
import buyer
import market_buyer as mb


class BalancedProbe(unittest.TestCase):
    def test_only_time_bounded_target_shops_are_enabled_and_off_switch_wins(self):
        self.assertIsNone(mb.opening_probe_arm("absent", "11im", "o1", {}, now=mb.ONE_WON_PROBE_START-1))
        self.assertIsNone(mb.opening_probe_arm("absent", "11im", "o1", {}, now=mb.ONE_WON_PROBE_END))
        self.assertIsNone(mb.opening_probe_arm("absent", "11im", "o1", {"BUYER_ONE_WON_PROBE": "0"}, now=mb.ONE_WON_PROBE_START))
        self.assertIsNone(mb.opening_probe_arm("absent", "other", "o1", {"BUYER_ONE_WON_PROBE": "1"}, now=mb.ONE_WON_PROBE_START))

    def test_within_shop_imbalance_is_corrected_and_order_retry_keeps_arm(self):
        now = mb.ONE_WON_PROBE_START+1
        with tempfile.TemporaryDirectory() as md:
            first = mb.opening_probe_arm(md,"11im","o-old",{},now=now)
            second = mb.opening_probe_arm(md,"11im","o-next",{},now=now)
            self.assertIn(first,("A","B"))
            self.assertNotEqual(first,second)
            self.assertEqual(mb.opening_probe_arm(md,"11im","o-old",{},now=mb.ONE_WON_PROBE_END+1),first)


    def test_same_sku_and_required_life_are_balanced_before_shop_total(self):
        now = mb.ONE_WON_PROBE_START+1
        with tempfile.TemporaryDirectory() as md:
            first=mb.opening_probe_arm(md,"11im","o1",{},now=now,sku="G1",use_within=72)
            mb.opening_probe_arm(md,"11im","o2",{},now=now,sku="G2",use_within=72)
            third=mb.opening_probe_arm(md,"11im","o3",{},now=now,sku="G1",use_within=72)
            self.assertNotEqual(first,third)

    def test_eight_concurrent_pending_assignments_are_reserved_and_balanced(self):
        now=mb.ONE_WON_PROBE_START+1
        with tempfile.TemporaryDirectory() as md:
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                arms=list(ex.map(lambda i:mb.opening_probe_arm(md,"11im",f"o-{i}",{},now=now),range(8)))
            self.assertEqual(arms.count("A"),4)
            self.assertEqual(arms.count("B"),4)
            with open(os.path.join(md,"one_won_probe_assignments.json")) as f:
                journal=json.load(f)
            self.assertEqual(len(journal["orders"]),8)
            for i,arm in enumerate(arms):
                self.assertEqual(mb.opening_probe_arm(md,"11im",f"o-{i}",{},now=now),arm)

    def test_corrupt_assignment_journal_fails_closed_without_resetting_it(self):
        with tempfile.TemporaryDirectory() as md:
            path=os.path.join(md,"one_won_probe_assignments.json")
            with open(path,"w") as f:f.write("{")
            self.assertIsNone(mb.opening_probe_arm(md,"11im","o1",{},now=mb.ONE_WON_PROBE_START+1))
            with open(path) as f:self.assertEqual(f.read(),"{")


class DefaultScope(unittest.TestCase):
    def test_order_started_before_default_is_not_promoted_when_quote_returns_late(self):
        self.assertFalse(mb.one_won_default("11im","food",(.9,),{},now=1794030001,started_at=1794029999))

    def test_default_only_11im_food_normal_steps_after_start_and_off_wins(self):
        t=1794030000
        self.assertTrue(mb.one_won_default("11im","food",(.9,),{},now=t,started_at=t))
        self.assertFalse(mb.one_won_default("11im","food",(.9,),{},now=t-1,started_at=t-1))
        self.assertFalse(mb.one_won_default("choo-chu","food",(.9,),{},now=t,started_at=t))
        self.assertFalse(mb.one_won_default("11im","goods",(.9,),{},now=t,started_at=t))
        self.assertFalse(mb.one_won_default("11im","food",(.8,),{},now=t,started_at=t))
        self.assertFalse(mb.one_won_default("11im","food",(.9,),{"BUYER_ONE_WON_11IM":"0"},now=t,started_at=t))


class ProbeTalk(unittest.TestCase):
    def test_only_first_valid_food_code_offer_is_one_then_normal_counter(self):
        h=[]
        self.assertEqual(mb.opening_probe_turn(10000, "", h, 10000, (.9,), None, False, "B")["price"], 1)
        h=[("shop asked",10000),("you offered",1)]
        self.assertEqual(mb.opening_probe_turn(9500, "", h, 10000, (.9,), None, False, "B")["price"], 9500)
        self.assertEqual(mb.opening_probe_turn(10000, "", [], 10000, (.9,), None, False, "A")["price"],9000)
        self.assertEqual(mb.opening_probe_turn(10000, "", [], 10000, (.9,), None, True, "B")["price"],9000)
        self.assertIsNone(mb.opening_probe_turn(None, "unclear price", [], 10000, (.9,), None, False, "B"))

    def test_real_talk_sends_one_then_counter_without_harness(self):
        def task(ask=None,done=False):
            return {"id":"t1","status":{"state":"TASK_STATE_COMPLETED" if done else "TASK_STATE_INPUT_REQUIRED",
                    "message":{"metadata":{"ask":ask},"parts":[{"text":str(ask)}]}},
                    **({"artifacts":[{"parts":[{"data":{"price":9500,"item":"G1"}}]}]} if done else {})}
        sent=[]
        replies=iter([task(9500),task(done=True)])
        def send(url,text,task_id=None):
            sent.append(text);return next(replies)
        ladder=lambda meta,text,h:mb.opening_probe_turn(meta,text,h,10000,(.9,),None,False,"B")
        with mock.patch.object(buyer,"send",send),mock.patch.object(buyer,"decide",side_effect=AssertionError("no LLM")):
            out=buyer.talk("https://example.invalid",task(10000),"food",10000,"",
                          {"history":[],"agreed":None,"holds":0},ladder=ladder)
        self.assertEqual(sent,["1","9500"])
        self.assertEqual(out["agreed"],9500)
        self.assertEqual(out["turns"],{"code":2})
