"""Replacements for the 10 existing assertions that pinned the old automatic-72 h policy (test_warranty.SellerFlow x7,
test_seller_fastpath x2, test_seller_discovery card x1). Each subclass reuses the original fixtures (real signed,
armed warranty state with room) and overrides only the superseded test; load_tests runs only the overrides.
A 72 h deal here is a PRE-EXISTING one: the real warranty.quote reserves the intent and the open deal is marked 72,
as an in-memory deal opened before warranty-0h-new-v1 would be. The full 72 h lifecycle (issue before sign, refusal
unsigned, cancel/expiry release, real signature) is re-checked on that path."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
UNDER = Path(os.environ.get("SELLER_UNDER_TEST", HERE)).resolve()
for p in (ROOT, UNDER):
    if str(p) in sys.path:
        sys.path.remove(str(p))
    sys.path.insert(0, str(p))

import seller_r3
import test_seller_discovery
import test_seller_fastpath
import test_warranty
import warranty
from test_seller_fastpath import NAME, SKU, unit
from test_warranty import GOODS_A, pilot_state

assert Path(seller_r3.__file__).resolve() == UNDER / "seller_r3.py", seller_r3.__file__
CARD_0H = "Warranty: new quotes carry no warranty (0 h)"


class SellerFlow0h(test_warranty.SellerFlow):
    async def asyncSetUp(self):
        self.sellers = []
        orig, test = seller_r3.Seller, self

        class Captured(orig):
            def __init__(s):
                super().__init__()
                test.sellers.append(s)
        self.cap = patch.object(seller_r3, "Seller", Captured)
        self.cap.start()
        await super().asyncSetUp()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.cap.stop()

    async def open72(self):
        """An open deal holding a real 72 h intent at its opening ask (opened before the 0 h policy)."""
        a = await self.send("I want to buy G00010. Please quote a price in credits")
        self.assertIn("no warranty (0 h)", self.reply(a))
        d = self.sellers[-1].open[a["id"]]
        self.assertEqual(warranty.quote(self.d, a["id"], d["unit"], d["ask"], self.t, key=self.key), warranty.HOURS)
        d["warranty"] = warranty.HOURS
        return a

    async def test_quote_72_then_signed_receipt_72_with_authority_first(self):
        # New policy: an armed state WITH room still quotes 0 h and reserves nothing.
        a = await self.send("I want to buy G00010. Please quote a price in credits")
        self.assertIn("no warranty (0 h)", self.reply(a))
        row = [r for r in self.rows() if r["event"] == "first_quote"][-1]
        self.assertEqual((row["decided_by"], row["warranty_hours"]), ("deterministic_fastpath", 0))
        self.assertEqual(row["warranty_policy"]["reason"], "new_quote_default_0h")
        self.assertEqual(warranty.liability(self.d, key=self.key), 0)
        b = await self.send(str(row["quote"]), a["id"])
        self.assertEqual(b["artifacts"][0]["parts"][0]["data"]["warranty_hours"], 0)
        self.assertEqual(warranty.liability(self.d, key=self.key), 0)
        self.assertEqual(warranty._load(self.d, self.key)["authorities"], {})
        # Pre-existing 72 h deal: authority durable BEFORE sign, receipt 72 h (unchanged lifecycle).
        a = await self.open72()
        b = await self.send(str(self.ask()), a["id"])
        self.assertEqual(b["status"]["state"], "TASK_STATE_COMPLETED")
        rec = b["artifacts"][0]["parts"][0]["data"]
        self.assertEqual((rec["warranty_hours"], rec["claim"]), (72, "A"))
        self.assertEqual(self.signed[-1][1:], (self.ask(), {a["id"]}))

    async def test_real_signature_covers_core4_and_metadata_binds_terms(self):
        a = await self.open72()
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            with patch.object(seller_r3, "sign", side_effect=self.real_sign):
                b = await self.send(str(self.ask()), a["id"])
        finally:
            os.chdir(cwd)
        rec = b["artifacts"][0]["parts"][0]["data"]
        core = {k: rec[k] for k in ("shop", "item", "price", "task")}
        core["price"] = int(core["price"])
        import base64
        from cryptography.hazmat.primitives import serialization
        pub = serialization.load_pem_private_key(self.key.read_bytes(), None).public_key()
        pub.verify(base64.b64decode(rec["sig_seller"]), seller_r3.canon(core))
        self.assertEqual((rec["claim"], rec["warranty_hours"], rec["cert"]), ("A", 72, True))
        auth = warranty._load(self.d, self.key)["authorities"][a["id"]]
        self.assertEqual(auth["receipt_core"], core)
        self.assertEqual(auth["terms"], {k: rec[k] for k in ("cert", "warranty_hours", "claim")})

    async def test_signer_failure_keeps_committed_authority(self):
        a = await self.open72()
        ask = self.ask()
        with patch.object(seller_r3, "sign", side_effect=OSError("signer down")):
            try:
                await self.send(str(ask), a["id"])
            except Exception:
                pass
        self.assertEqual(set(warranty._load(self.d, self.key)["authorities"]), {a["id"]})
        self.assertEqual(warranty.liability(self.d, key=self.key), ask)

    async def test_grade_drop_between_quote_and_accept_refuses_unsigned(self):
        a = await self.open72()
        ask = self.ask()
        self.stock([dict(GOODS_A, grade="C"), dict(GOODS_A, unit="u-2")])
        b = await self.send(str(ask), a["id"])
        self.assertEqual((b["status"]["state"], self.signed), ("TASK_STATE_REJECTED", []))
        self.assertEqual(warranty.liability(self.d, key=self.key), 0)

    async def test_pilot_expired_before_accept_refuses_unsigned(self):
        a = await self.open72()
        ask = self.ask()
        pilot_state(self.d, self.t, active_until=self.t + 1, units=[GOODS_A, dict(GOODS_A, unit="u-2")])
        self.t += 5
        b = await self.send(str(ask), a["id"])
        self.assertEqual((b["status"]["state"], self.signed), ("TASK_STATE_REJECTED", []))
        self.assertIn("72 h warranty", self.reply(b))
        self.assertEqual(warranty.liability(self.d, key=self.key), 0)
        c = await self.send("I want to buy G00010. Please quote a price in credits")
        self.assertIn("no warranty (0 h)", self.reply(c))

    async def test_cancel_frees_unsigned_intent(self):
        a = await self.open72()
        self.assertGreater(warranty.liability(self.d, key=self.key), 0)
        await self.client.post("http://fixture/", json={"jsonrpc": "2.0", "id": "c", "method": "CancelTask",
                                                         "params": {"id": a["id"]}}, headers={"A2A-Version": "1.0"})
        self.assertEqual(warranty.liability(self.d, key=self.key), 0)

    async def test_grade_c_and_card(self):
        self.stock([dict(GOODS_A, grade="C")])
        a = await self.send("I want to buy G00010. Please quote a price in credits")
        self.assertIn("no warranty (0 h)", self.reply(a))
        self.assertEqual(warranty.liability(self.d, key=self.key), 0)
        card = (await self.client.get("http://fixture/.well-known/agent-card.json")).json()
        self.assertIn(CARD_0H, card["description"])
        self.assertNotIn("72 h warranty", card["description"])


class FastPath0h(test_seller_fastpath.FastPathTests):
    async def test_complex_openings_still_go_to_terra(self):
        self.stock([unit('u-a'), unit('u-b', sku='G999', name='other lamp')])
        for text in (f'I want {SKU} if it is grade A', f'{SKU} grade A or B',
                     f'{SKU} for 3000', f'{SKU} REVIEW-COUPON 12', f'{SKU} with a discount',
                     f'{SKU} with at least 30 days of shelf life', f'two units of {SKU}', f'{SKU} not grade C',
                     f'{SKU}, can you do better?'):
            n = len(self.prompts)
            self.moves = [None]
            a = await self.send(text)
            self.assertEqual(len(self.prompts), n + 1, text)
            self.assertNotEqual(self.last()['decided_by'], 'deterministic_fastpath', text)
            await self.client.post('http://fixture/', json={'jsonrpc':'2.0', 'id':'c', 'method':'CancelTask',
                                                            'params':{'id':a['id']}}, headers={'A2A-Version':'1.0'})
        # Superseded item: a warranty requirement is declined unsigned before any harness call.
        n = len(self.prompts)
        a = await self.send(f'{SKU} with a 72h warranty?')
        self.assertEqual((a['status']['state'], len(self.prompts), self.last()['event']),
                         ('TASK_STATE_REJECTED', n, 'warranty_demand_rejected'))

    async def test_non_plain_followups_go_to_terra_without_stale_or_sku_offers(self):
        a = await self.send(OPEN)
        a = await self.send('3000', a['id'])
        self.assertEqual(self.last()['offer'], 3000)
        for text in ('not 3000, maybe later', f'is {SKU} grade A?', '3000.5', f'{NAME} is it new?'):
            n = len(self.prompts)
            self.moves = [{'action':'accept'}]
            a = await self.send(text, a['id'])
            self.assertEqual(len(self.prompts), n + 1, text)
            self.assertIsNone(self.last()['offer'], text)
            self.assertEqual(self.signed, [], text)
        # Superseded item: 'ok if you add a warranty' now closes the 0 h task unsigned, no harness call.
        n = len(self.prompts)
        self.moves = [{'action':'accept'}]
        b = await self.send('ok if you add a warranty', a['id'])
        self.assertEqual((b['status']['state'], len(self.prompts), self.signed), ('TASK_STATE_REJECTED', n, []))


OPEN = test_seller_fastpath.OPEN


class Discovery0h(test_seller_discovery.DiscoveryRouteTests):
    async def test_card_mentions_unique_partial_name_discovery_without_new_claims(self):
        import re
        card = await self.card()
        desc = card['description']
        self.assertRegex(desc, r'(?i)part(?:ial)?\s+(?:of\s+(?:its|the|a)\s+)?(?:product\s+)?name')
        self.assertRegex(desc, r'(?i)\bunique|only one\b')
        self.assertNotRegex(desc, r'(?i)\bfast(?:er)?\b|\bspeed|\bms\b|latency|all goods|every (?:item|product)')
        self.assertIn(CARD_0H, desc)                       # superseded: 72 h wording -> 0 h for new quotes
        self.assertNotIn('72 h warranty', desc)
        examples = [e for s in card['skills'] for e in s.get('examples', [])]
        self.assertIn('I want to buy 종가 알뜰맘 포기김치 10kg (G00001)', examples)
        self.assertFalse([e for e in examples if re.search(r'(?i)discount|warrant|coupon', e)], examples)


SUPERSEDED = {
    SellerFlow0h: ["test_quote_72_then_signed_receipt_72_with_authority_first",
                   "test_real_signature_covers_core4_and_metadata_binds_terms",
                   "test_signer_failure_keeps_committed_authority",
                   "test_grade_drop_between_quote_and_accept_refuses_unsigned",
                   "test_pilot_expired_before_accept_refuses_unsigned", "test_cancel_frees_unsigned_intent",
                   "test_grade_c_and_card"],
    FastPath0h: ["test_complex_openings_still_go_to_terra",
                 "test_non_plain_followups_go_to_terra_without_stale_or_sku_offers"],
    Discovery0h: ["test_card_mentions_unique_partial_name_discovery_without_new_claims"],
}


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls(name) for cls, names in SUPERSEDED.items() for name in names)


if __name__ == "__main__":
    unittest.main()
