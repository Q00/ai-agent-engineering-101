import json
import tempfile
import unittest
from pathlib import Path

import buyer_policy
import memory_sync


def rows():
    return [
        {"order": "o-1", "shop": "a", "item": "home>bed", "sku": "S1", "name": "bed", "outcome": "deal", "price": 100,
         "honest": True, "settled": False},
        {"order": "o-2", "shop": "b", "item": "home>bed", "sku": "S2", "name": "lamp", "outcome": "deal", "price": 50,
         "honest": True, "settled": False},
    ]


class SettlementTest(unittest.TestCase):
    def test_settlement_is_merged_by_order_id_and_a_false_claim_marks_the_row_dishonest(self):
        mem = rows()
        deal = {"order": "o-1", "receipt": {"item": "S1", "price": 100},
                "settled": {"shop": "https://x", "sku": "S1", "name": "bed", "grade": "B", "claim": "A",
                            "claim_honest": False, "value": 1000, "warranty_hours": 3, "ledger": 77}}
        row = memory_sync.record_settlement(mem, deal, now=5000)
        self.assertIs(row, mem[0])
        self.assertEqual((True, 5000, 77, "B", False, 3, False),
                         (row["settled"], row["settled_at"], row["ledger"], row["grade"], row["claim_honest"],
                          row["warranty_hours"], row["honest"]))

    def test_without_an_order_id_the_latest_unsettled_deal_of_that_sku_and_price_matches(self):
        mem = rows()
        deal = {"order": {"item": "home>bed"}, "receipt": {"item": "S2", "price": 50}, "settled": {"claim_honest": True}}
        self.assertIs(mem[1], memory_sync.record_settlement(mem, deal, now=1))
        self.assertTrue(mem[1]["honest"])
        self.assertIsNone(memory_sync.record_settlement(mem, {"order": "o-9", "receipt": {"item": "S9", "price": 1}}))


class ReconcileTest(unittest.TestCase):
    def test_refused_payment_turns_the_deal_into_unpaid_and_out_of_the_reference(self):
        mem = rows()
        n = memory_sync.reconcile_payments(mem, [{"order": "o-1", "paid": False, "reason": "no certificate"},
                                                 {"order": "o-2", "paid": True, "ledger": 5}])
        self.assertEqual(1, n)
        self.assertEqual(("unpaid", "no certificate"), (mem[0]["outcome"], mem[0]["unpaid_reason"]))
        self.assertEqual(50, buyer_policy.item_reference(mem, "home>bed"))   # o-1's 100 no longer counts

    def test_broken_goods_match_the_latest_settled_purchase_of_that_name_and_know_the_warranty(self):
        mem = rows()
        mem[0].update({"settled": True, "settled_at": 1000, "warranty_hours": 1})
        mem[1].update({"settled": True, "settled_at": 2000, "warranty_hours": 0, "name": "bed"})
        n = memory_sync.reconcile_broken(mem, [{"name": "bed", "broke_at": 2100}, {"name": "bed", "broke_at": 4700},
                                               {"name": "sofa", "broke_at": 2100}])
        self.assertEqual(2, n)
        self.assertEqual((True, False), (mem[1]["broken"], mem[1]["refunded"]))   # b's bed, no warranty, at 2100
        self.assertEqual((True, False), (mem[0]["broken"], mem[0]["refunded"]))   # a's bed at 4700: 3700 s > 1 h warranty
        mem[0].update({"broken": False, "refunded": False})
        memory_sync.reconcile_broken(mem, [{"name": "bed", "broke_at": 4000}])
        self.assertTrue(mem[0]["refunded"])                                       # 3000 s: inside the 1 h warranty

    def test_reconcile_reads_each_file_once_via_the_cursor(self):
        mem = rows()
        with tempfile.TemporaryDirectory() as d:
            Path(d, "payments.jsonl").write_text(json.dumps({"order": "o-1", "paid": False, "reason": "x"}) + "\n")
            self.assertEqual({"unpaid": 1, "broken": 0}, memory_sync.reconcile(mem, d))
            mem[0]["outcome"] = "deal"
            self.assertEqual({"unpaid": 0, "broken": 0}, memory_sync.reconcile(mem, d))
            self.assertEqual({"payments": 1}, json.loads(Path(d, "memory_sync.json").read_text()))   # no broken.jsonl yet

    def test_unrefunded_break_lowers_the_shop_integrity(self):
        shop = {"name": "a", "reviews": []}
        fine = [{"shop": "a", "item": "x", "outcome": "deal", "price": 100, "honest": True, "settled": True, "broken": True, "refunded": True}]
        lost = [{"shop": "a", "item": "x", "outcome": "deal", "price": 100, "honest": True, "settled": True, "broken": True, "refunded": False}]
        self.assertGreater(buyer_policy.evidence(shop, fine, "x", 100)["integrity"],
                           buyer_policy.evidence(shop, lost, "x", 100)["integrity"])
        self.assertEqual(1, buyer_policy.evidence(shop, lost, "x", 100)["bad"])


if __name__ == "__main__":
    unittest.main()
