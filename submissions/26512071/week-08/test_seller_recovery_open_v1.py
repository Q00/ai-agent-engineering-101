"""recovery-open-v1: a NEW task's opening is capped by the median of this SKU's own exact paid prices of the last 12 h
(one-day-pilot-ledger.json settle events, >= 3 valid samples), floored at max(unit cost, valid Sol min_accept); the
opening never rises. Numbers from evidence.json (2026-11-15): oil 5 x 23760, brownie median ~7274 < cost, spray none.
SELLER_UNDER_TEST=<dir> pins which seller_r3.py is imported (candidate by default; baseline for the RED run)."""
import json
import os
import sys
import tempfile
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
sys.modules.pop("seller_r3", None)

import httpx
import seller_r3
import sol_policy

assert Path(seller_r3.__file__).resolve() == UNDER / "seller_r3.py", seller_r3.__file__

T = 1_794_749_079.0
H = 3600


def oil(uid="o1", cost=18275):
    return dict(unit=uid, sku="G63ef2f11", name="oil set", category="food>oil", grade="A", kind="food", cost=cost,
                list_price=36000, expires_at=T + 9 * 24 * H, supplied_at=1)


def lamp(uid="l1", grade="A", cost=500):
    return dict(unit=uid, sku="GLAMP0001", name="lamp", category="goods>lamp", grade=grade, kind="goods", cost=cost,
                list_price=3000, expires_at=None, supplied_at=1)


class Ledger:
    def __init__(self):
        self.units, self.events, self.seq = {}, {}, 100

    def sale(self, sku, price, age_h=1.0, grade="A", kind="food", unit_sku=None, ts=None):
        self.seq += 1
        uid = f"u-{self.seq}"
        self.units[uid] = {"sku": unit_sku or sku, "cost": 1, "grade": grade, "kind": kind}
        self.events[f"settle:{self.seq}"] = {"kind": "settle", "seq": self.seq, "ts": T - age_h * H if ts is None else ts,
                                             "unit": uid, "sku": sku, "price": price}
        return self

    def write(self, d):
        (d / "one-day-pilot-ledger.json").write_text(json.dumps({"pilot": "one-day-v1", "units": self.units,
                                                                 "events": self.events, "requests": {}}),
                                                     encoding="utf-8")


def sol_entry(start, minimum):
    return ({"start_ask": start, "target_settle": start, "min_accept": minimum},
            {"used": True, "policy_id": "fixturepolicy00", "returned_model": "fixture", "start_ask": start,
             "target_settle": start, "min_accept": minimum})


class RecoveryOpening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.patches = [patch.object(seller_r3, "MARKET_DIR", self.tmp)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def opening(self, unit, baseline_anchor=None, sol=None):
        """(first quote of a NEW task, snapshot) with a fixed baseline anchor and optional Sol entry."""
        with patch.object(seller_r3, "baseline_policy", return_value=(baseline_anchor, {"anchor": baseline_anchor,
                                                                                         "label": "fixture"})), \
                patch.object(sol_policy, "active", return_value=sol is not None), \
                patch.object(sol_policy, "policy_for", return_value=sol or (None, "none")):
            anchor, snap = seller_r3.price_policy(unit, T)
        return seller_r3.first_quote(unit, anchor), snap

    def test_oil_five_paid_23760_caps_sol_start(self):
        led = Ledger()
        for _ in range(5):
            led.sale("G63ef2f11", 23760)
        led.write(self.tmp)
        q, snap = self.opening(oil(), 30000, sol_entry(32400, 23000))
        self.assertEqual(q, 23760)   # max(cost 18275, Sol min 23000, min(32400, 23760))
        rec = snap.get("recovery", {})
        self.assertEqual((rec.get("used"), rec.get("sample_count"), rec.get("median")), (True, 5, 23760))
        self.assertEqual(rec.get("window_h"), 12)
        self.assertEqual(len(rec.get("source_ids", [])), 5)
        self.assertEqual(rec.get("previous_opening"), 32400)
        self.assertIn("own", rec.get("condition", ""))

    def test_sol_min_stays_binding_floor(self):
        led = Ledger()
        for _ in range(5):
            led.sale("G63ef2f11", 23760)
        led.write(self.tmp)
        q, snap = self.opening(oil(), None, sol_entry(32400, 25000))
        self.assertEqual(q, 25000)   # Sol min_accept is not reduced
        self.assertEqual(snap.get("recovery", {}).get("binding"), "sol_min")

    def test_cost_floor_when_median_below_cost_brownie(self):
        led = Ledger()
        for p in (7150, 7184, 7274, 7300, 7400):
            led.sale("G63ef2f11", p)
        led.write(self.tmp)
        q, snap = self.opening(oil(cost=8960), None)   # baseline: 90% of list = 32400
        self.assertEqual(q, 8960)
        self.assertEqual(snap.get("recovery", {}).get("binding"), "cost")

    def test_never_increases_the_opening(self):
        led = Ledger()
        for _ in range(4):
            led.sale("G63ef2f11", 35000)
        led.write(self.tmp)
        q, snap = self.opening(oil(), 30000)
        self.assertEqual(q, 30000)
        self.assertEqual(snap.get("recovery", {}).get("binding"), "previous_opening")

    def test_stale_future_other_sku_invalid_and_sparse_are_ignored(self):
        led = Ledger()
        led.sale("G63ef2f11", 20000, age_h=12.5)           # stale
        led.sale("G63ef2f11", 20000, age_h=-0.5)           # future
        led.sale("GOTHER001", 20000)                       # other SKU
        led.sale("G63ef2f11", 20000, unit_sku="GOTHER001")  # unit row disagrees: untrusted
        for bad in (True, 20000.0, float("nan"), "20000", -5, 0, None):
            led.sale("G63ef2f11", bad)
        led.sale("G63ef2f11", 20000, ts=True)              # bool timestamp
        led.sale("G63ef2f11", 21000)
        led.sale("G63ef2f11", 21000)                       # only 2 valid: sparse
        led.events["settle:999"] = "malformed"
        raw = json.dumps({"pilot": "x", "units": led.units, "events": led.events}).replace("NaN", "1e999")
        (self.tmp / "one-day-pilot-ledger.json").write_text(raw, encoding="utf-8")
        q, snap = self.opening(oil(), 30000)
        self.assertEqual(q, 30000)
        rec = snap.get("recovery", {})
        self.assertIs(rec.get("used"), False)
        self.assertEqual(rec.get("sample_count"), 2)

    def test_missing_or_corrupt_ledger_keeps_opening(self):
        q, snap = self.opening(oil(), 30000)
        self.assertEqual(q, 30000)
        self.assertIs(snap.get("recovery", {}).get("used"), False)
        (self.tmp / "one-day-pilot-ledger.json").write_text("{not json", encoding="utf-8")
        q, snap = self.opening(oil(), 30000)
        self.assertEqual(q, 30000)
        self.assertIs(snap.get("recovery", {}).get("used"), False)

    def test_goods_prefers_same_observed_grade_and_labels_warranty_unknown(self):
        led = Ledger()
        for p in (1000, 1100, 1200):
            led.sale("GLAMP0001", p, grade="A", kind="goods")
        for p in (600, 600, 600, 600):
            led.sale("GLAMP0001", p, grade="C", kind="goods")
        led.write(self.tmp)
        q, snap = self.opening(lamp(), None)   # baseline 2700
        self.assertEqual(q, 1100)
        rec = snap.get("recovery", {})
        self.assertEqual(rec.get("sample_count"), 3)
        self.assertIn("same observed grade", rec.get("condition", ""))
        self.assertTrue(any("warranty" in x for x in rec.get("limitations", [])))

    def test_goods_mixed_grades_labelled_heuristic(self):
        led = Ledger()
        for p, g in ((1000, "B"), (1100, "C"), (1200, "C")):
            led.sale("GLAMP0001", p, grade=g, kind="goods")
        led.write(self.tmp)
        q, snap = self.opening(lamp(), None)
        self.assertEqual(q, 1100)
        self.assertIn("mixed-condition own-history heuristic", snap.get("recovery", {}).get("condition", ""))


class RecoveryEndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_new_task_quotes_recovery_opening_and_later_turns_never_rise(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "inventory.json").write_text(json.dumps({"round": 3, "units": [oil()]}), encoding="utf-8")
            led = Ledger()
            for _ in range(5):
                led.sale("G63ef2f11", 23760)
            led.write(d)
            with patch.object(seller_r3, "MARKET_DIR", d), patch.object(seller_r3, "now", return_value=T), \
                    patch.object(seller_r3, "price_anchor", side_effect=RuntimeError("offline")), \
                    patch.object(sol_policy, "active", return_value=False), \
                    patch.object(sol_policy, "maybe_refresh_async", return_value=None):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999))) as c:
                    async def send(text, tid=None):
                        msg = {"role": "ROLE_USER", "messageId": f"m-{text}", "parts": [{"text": text}]}
                        if tid:
                            msg["taskId"] = tid
                        r = await c.post("http://fixture/", headers={"A2A-Version": "1.0"},
                                         json={"jsonrpc": "2.0", "id": "1", "method": "SendMessage",
                                               "params": {"message": msg}})
                        return r.json()["result"]["task"]
                    a = await send("I want to buy G63ef2f11")
                    self.assertTrue(a["status"]["message"]["parts"][0]["text"].endswith("asking 23760. Send your offer as a number."))
                    led.events.clear()
                    led.write(d)   # evidence gone mid-task: the open task keeps its snapshot
                    b = await send("20000", a["id"])
                    nums = [int(x) for x in b["status"]["message"]["parts"][0]["text"].replace(".", " ").split() if x.isdigit()]
                    self.assertLessEqual(nums[-1], 23760)
                    self.assertGreaterEqual(nums[-1], 18275)


if __name__ == "__main__":
    unittest.main()
