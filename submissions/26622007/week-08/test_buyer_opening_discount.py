import concurrent.futures
import json
import os
import tempfile
import unittest
import buyer_opening_discount as probe

class DiscountPilot(unittest.TestCase):
    def test_b_offer_85_percent_then_original_counter_with_cap(self):
        self.assertEqual(probe.turn(10000,"",[],10000,(.9,),None,False,"B")["price"],8500)
        self.assertEqual(probe.turn(10000,"",[],8000,(.9,),None,False,"B")["price"],8000)
        history=[("shop asked",10000),("you offered",8500)]
        self.assertEqual(probe.turn(9500,"",history,10000,(.9,),None,False,"B")["price"],9500)
        self.assertEqual(probe.turn(10000,"",[],10000,(.9,),None,False,"A")["price"],9000)
    def test_one_won_ask_and_positive_cap_never_make_zero_offer(self):
        self.assertEqual(probe.turn(1,"",[],1,(.9,),None,False,"B")["price"],1)
        self.assertEqual(probe.turn(10000,"",[],1,(.9,),None,False,"B")["price"],1)
        self.assertNotEqual(probe.turn(10000,"",[],0,(.9,),None,False,"B")["action"],"offer")

    def test_goods_unknown_and_other_steps_keep_original_behavior(self):
        self.assertEqual(probe.turn(10000,"",[],10000,(.9,),None,True,"B")["price"],9000)
        self.assertIsNone(probe.turn(None,"unclear",[],10000,(.9,),None,False,"B"))
        self.assertEqual(probe.turn(10000,"",[],10000,(.8,),None,False,"B")["price"],8000)
    def test_only_bounded_window_and_non_one_won_shops(self):
        with tempfile.TemporaryDirectory() as md:
            for shop in ("11im","choo-chu"):
                self.assertIsNone(probe.reserve(md,shop,"o1","G1",72,now=probe.START))
            self.assertIsNone(probe.reserve(md,"other","o1","G1",72,now=probe.START-1))
            self.assertIsNone(probe.reserve(md,"other","o1","G1",72,now=probe.END))
            self.assertIsNone(probe.reserve(md,"other","o1","G1",72,now=probe.START,env={"BUYER_OPENING_DISCOUNT":"0"}))
    def test_pending_allocations_balanced_retry_stable_and_corrupt_fails_closed(self):
        with tempfile.TemporaryDirectory() as md:
            def reserve(i):return probe.reserve(md,"other",str(i),"G1",72,now=probe.START+1)
            with concurrent.futures.ThreadPoolExecutor(8) as pool:arms=list(pool.map(reserve,range(8)))
            self.assertEqual(arms.count("A"),4)
            self.assertEqual(arms.count("B"),4)
            for i,arm in enumerate(arms):
                self.assertEqual(probe.reserve(md,"other",str(i),"G1",72,now=probe.END+1),arm)
            path=os.path.join(md,probe.JOURNAL)
            with open(path,"w") as f:f.write("{")
            self.assertIsNone(reserve(99))
            with open(path) as f:self.assertEqual(f.read(),"{")
