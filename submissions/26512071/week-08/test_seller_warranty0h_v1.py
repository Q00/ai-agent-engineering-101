"""warranty-0h-new-v1: every NEW quote is 0 h (no warranty.quote intent, no reserve); a buyer's positive warranty
requirement is declined unsigned on any turn, whatever the model says; a negated/0 h mention is not a requirement;
a deal that already holds a 72 h intent (opened before this policy) still goes through warranty.issue unchanged.
SELLER_UNDER_TEST=<dir> pins which seller_r3.py is imported (candidate by default; baseline for the RED run)."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]   # week-08: warranty, sol_policy, harness ... (production, unchanged)
UNDER = Path(os.environ.get("SELLER_UNDER_TEST", HERE)).resolve()
for p in (ROOT, UNDER):
    if str(p) in sys.path:
        sys.path.remove(str(p))
    sys.path.insert(0, str(p))
sys.modules.pop("seller_r3", None)

import httpx
import seller_r3
import warranty

assert Path(seller_r3.__file__).resolve() == UNDER / "seller_r3.py", seller_r3.__file__


def goods(uid="w1", grade="A"):
    return dict(unit=uid, sku="GWARR01", name="Warranty fixture lamp", category="goods>lamp", grade=grade, kind="goods",
                cost=50, list_price=100, expires_at=None, supplied_at=1)


class Warranty0h(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.signed, self.sellers = [], []
        fake_sign = lambda rec: self.signed.append(dict(rec)) or dict(rec, sig_seller="offline-signature-placeholder")
        orig = seller_r3.Seller
        test = self

        class Captured(orig):
            def __init__(s):
                super().__init__()
                test.sellers.append(s)
        self.patches = [patch.object(seller_r3, "MARKET_DIR", self.tmp),
                        patch.object(seller_r3, "ask_json", return_value=({"action": "accept"}, "fixture")),
                        patch.object(seller_r3, "sign", side_effect=fake_sign),
                        patch.object(seller_r3, "now", return_value=1000),
                        patch.object(seller_r3, "Seller", Captured),
                        patch.object(warranty, "quote", return_value=warranty.HOURS),   # room for 72 h if asked
                        patch.object(warranty, "issue", return_value="issued"),
                        patch.object(warranty, "release", return_value="released")]
        started = [p.start() for p in self.patches]
        self.model, self.quote, self.issue, self.release = started[1], started[5], started[6], started[7]
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)))
        units = [goods(f"w{i}") for i in range(1, 5)]   # one per concurrent open task (a reserved unit is taken)
        Path(self.tmp, "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
        # An old issued authority file: no new-quote path may touch it.
        self.ledger = self.tmp / "warranty-ledger.json"
        self.ledger.write_bytes(b'{"authorities":{"old":{"reserve":973081}}}')

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    async def send(self, text, tid=None):
        msg = {"role": "ROLE_USER", "messageId": f"m-{text}-{tid}", "parts": [{"text": text}]}
        if tid:
            msg["taskId"] = tid
        r = await self.client.post("http://fixture/", headers={"A2A-Version": "1.0"},
                                   json={"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}})
        return r.json()["result"]["task"]

    def text(self, task):
        return task["status"]["message"]["parts"][0]["text"]

    def rows(self, event):
        p = self.tmp / "decisions.jsonl"
        return [r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines())
                if r.get("event") == event] if p.exists() else []

    def assert_rejected_unsigned(self, task):
        self.assertEqual(task["status"]["state"], "TASK_STATE_REJECTED")
        self.assertEqual(self.signed, [])
        self.assertFalse(task.get("artifacts"))
        self.assertIn("no new warranty", self.text(task).lower())
        self.issue.assert_not_called()

    async def test_new_goods_quote_is_0h_without_reserve_and_receipt_0h(self):
        a = await self.send("I want to buy GWARR01")
        self.assertEqual(a["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertIn("no warranty (0 h)", self.text(a))
        self.quote.assert_not_called()   # no intent, no reserve, even though warranty.quote would grant 72 h
        first = self.rows("first_quote")[-1]
        self.assertEqual(first["warranty_hours"], 0)
        self.assertEqual(first["warranty_policy"]["policy"], "warranty-0h-new-v1")
        self.assertEqual(first["warranty_policy"]["reason"], "new_quote_default_0h")
        done = await self.send("90", a["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(done["artifacts"][0]["parts"][0]["data"]["warranty_hours"], 0)
        self.assertEqual(done["artifacts"][0]["parts"][0]["data"]["claim"], "A")   # grade claim and cert kept
        self.assertIs(done["artifacts"][0]["parts"][0]["data"]["cert"], True)
        self.issue.assert_not_called()
        self.release.assert_not_called()
        self.assertEqual(self.ledger.read_bytes(), b'{"authorities":{"old":{"reserve":973081}}}')

    @patch.object(seller_r3, "FASTPATH", False)
    async def test_explicit_72h_first_turn_rejected_unsigned_even_if_model_accepts(self):
        for ask in ("I want GWARR01 with a 72h warranty", "GWARR01, only with warranty please", "GWARR01 보증 72시간 필요해요"):
            self.signed.clear()
            t = await self.send(ask)
            self.assert_rejected_unsigned(t)
        self.quote.assert_not_called()
        rows = self.rows("warranty_demand_rejected")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["warranty_policy"]["policy"], "warranty-0h-new-v1")
        self.assertEqual(rows[0]["warranty_policy"]["turn"], "first")

    async def test_later_turn_warranty_demand_on_0h_deal_rejected_unsigned(self):
        a = await self.send("I want to buy GWARR01")
        t = await self.send("90 if you add a 72 hour warranty", a["id"])
        self.assert_rejected_unsigned(t)
        self.assertEqual(self.rows("warranty_demand_rejected")[-1]["warranty_policy"]["turn"], "later")
        self.assertEqual(self.sellers[0].open, {})   # task closed: never re-quoted without the requirement

    @patch.object(seller_r3, "FASTPATH", False)
    async def test_negated_or_zero_warranty_is_not_a_requirement(self):
        for ask in ("I want GWARR01, no warranty needed", "GWARR01 without warranty is fine",
                    "GWARR01 warranty 0h is ok", "GWARR01 보증 없이 살게요"):
            t = await self.send(ask)
            self.assertEqual(t["status"]["state"], "TASK_STATE_INPUT_REQUIRED", ask)
        self.assertEqual(self.rows("warranty_demand_rejected"), [])
        self.quote.assert_not_called()

    async def test_preexisting_72h_deal_still_issues_unchanged(self):
        a = await self.send("I want to buy GWARR01")
        self.sellers[0].open[a["id"]]["warranty"] = warranty.HOURS   # in-memory 72 h deal from before the policy
        t = await self.send("ok and the 72 h warranty as quoted", a["id"])   # not declined: already promised
        self.assertNotEqual(t["status"]["state"], "TASK_STATE_REJECTED")
        done = await self.send("90", a["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(done["artifacts"][0]["parts"][0]["data"]["warranty_hours"], warranty.HOURS)
        self.issue.assert_called_once()

    async def test_card_says_0h_for_new_quotes(self):
        r = await self.client.get("http://fixture/.well-known/agent-card.json")
        d = r.json()["description"]
        self.assertIn("new quotes carry no warranty (0 h)", d)
        self.assertNotIn("while the approved pilot has room", d)
        self.assertIn("market certificate kept", d)


if __name__ == "__main__":
    unittest.main()
