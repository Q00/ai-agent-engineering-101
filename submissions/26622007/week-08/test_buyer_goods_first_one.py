import concurrent.futures
import os
import tempfile
import unittest
from unittest import mock
import buyer_goods_first_one as probe

class QuotedPrice(unittest.TestCase):
    def test_only_existing_valid_code_first_offer_is_repriced(self):
        d={"shop_price":10000,"action":"offer","price":9000}
        self.assertEqual(probe.turn(d,[],10000,(.9,),True,"B")["price"],1)
        self.assertEqual(probe.turn(d,[],1,(.9,),True,"B")["price"],1)
        self.assertEqual(probe.turn(d,[],10000,(.9,),True,"A"),d)
        self.assertEqual(probe.turn(d,[],10000,(.9,),False,"B"),d)
        self.assertEqual(probe.turn(d,[("you offered",1)],10000,(.9,),True,"B"),d)
        self.assertIsNone(probe.turn(None,[],10000,(.9,),True,"B"))
        self.assertEqual(probe.turn(d,[],0,(.9,),True,"B")["action"],"walk_away")
    def test_quote_validation_keeps_unknown_and_no_warranty_out(self):
        self.assertIsNotNone(probe.first_decision({"ask":10000,"ask_src":"metadata"},10000,(.9,)))
        self.assertIsNotNone(probe.first_decision({"ask":10000,"ask_src":"text","text":"Asking 10000."},10000,(.9,)))
        self.assertIsNone(probe.first_decision({"ask":10000,"ask_src":"text","text":"unclear price"},10000,(.9,)))
        self.assertIsNone(probe.first_decision({"ask":10000,"ask_src":"metadata","text":"without warranty"},10000,(.9,)))
        self.assertIsNone(probe.first_decision({"ask":0,"ask_src":"metadata"},10000,(.9,)))

class Allocation(unittest.TestCase):
    def test_default_disabled_and_scope_start_off_pending_retry(self):
        order={"kind":"goods","order":"o1","_buyer_started_at":probe.START,"category":"c"}
        with tempfile.TemporaryDirectory() as md:
            with mock.patch.object(probe,"ENABLED",False):
                self.assertIsNone(probe.reserve(md,order,"25620027","G1",now=probe.START,env={}))
            with mock.patch.object(probe,"ENABLED",True):
                self.assertIsNone(probe.reserve(md,order,"other","G1",now=probe.START,env={}))
                self.assertIsNone(probe.reserve(md,{**order,"_buyer_started_at":probe.START-1},"25620027","G1",now=probe.START,env={}))
                self.assertIsNone(probe.reserve(md,{**order,"kind":"food"},"25620027","G1",now=probe.START,env={}))
                first=probe.reserve(md,order,"25620027","G1",now=probe.START,env={})
                second=probe.reserve(md,{**order,"order":"o2"},"25620027","G1",now=probe.START+1,env={})
                self.assertNotEqual(first,second)
                self.assertEqual(probe.reserve(md,order,"25620027","G1",now=probe.END+1,env={}),first)
                self.assertIsNone(probe.reserve(md,order,"25620027","G1",now=probe.START,env={"BUYER_GOODS_FIRST_ONE":"0"}))
