"""A buyer is graded only against its own reviews elsewhere, with enough samples and no recorded cause for the low stars."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import investigate_low_stars as il

US, OTHER = "https://us", "https://other"
CATALOG = {"g": {"list_price": 1000}}


class Builder:
    def __init__(self):
        self.rows, self.seq = [], 0

    def add(self, kind, **data):
        self.seq += 1
        self.rows.append({"seq": self.seq, "ts": 1000.0 + self.seq, "kind": kind, "data": data})

    def deal(self, buyer, shop, stars, grade="A", amount=500, verified=True, review="ok", broke=False):
        order = f"o{self.seq}"
        self.add("settle", order=order, buyer=buyer, shop=shop, item="g", amount=amount, grade=grade, claim=grade, claim_honest=True)
        self.add("result", order=order, buyer=buyer, shop=shop, stars=stars, review=review, verified=verified)
        if broke:
            self.add("broken", order=order, buyer=buyer, shop=shop)


def row_for(report, buyer):
    return next(r for r in report["table"] if r["buyer"] == buyer)


class Grades(unittest.TestCase):
    def build(self, ours, others, **deal):
        b = Builder()
        for stars in ours:
            b.deal("x", US, stars, **deal)
        for stars in others:
            b.deal("x", OTHER, stars, amount=deal.get("amount", 500))
        return il.analyze(b.rows, {US}, CATALOG)

    def test_low_only_on_us_with_clean_deals_is_strong(self):
        row = row_for(self.build([1] * 6, [5] * 20), "x")
        self.assertEqual((row["grade"], row["low_without_recorded_problem"]), ("strong", 6))

    def test_a_buyer_harsh_everywhere_is_not_flagged(self):
        self.assertEqual(row_for(self.build([1] * 6, [1] * 20), "x")["grade"], "none")

    def test_small_samples_are_not_graded(self):
        for ours, others in (([1] * 4, [5] * 20), ([1] * 6, [5] * 9)):
            row = row_for(self.build(ours, others), "x")
            self.assertEqual((row["grade"], row["note"]), ("none", "sample_too_small"))

    def test_low_stars_with_recorded_causes_are_weak_at_most(self):
        for deal in ({"broke": True}, {"grade": "C"}, {"amount": 950}):
            with self.subTest(deal=deal):
                self.assertEqual(row_for(self.build([1] * 6, [5] * 20, **deal), "x")["grade"], "weak")

    def test_grade_b_alone_is_not_a_cause_but_is_shown(self):
        report = self.build([1] * 6, [5] * 20, grade="B")
        self.assertEqual(row_for(report, "x")["grade"], "strong")
        self.assertIn("grade_b", report["low_reviews"][0]["record"])

    def test_unverified_reviews_never_count(self):
        b = Builder()
        for _ in range(6):
            b.deal("x", US, 1, verified=False)
        report = il.analyze(b.rows, {US}, CATALOG)
        self.assertEqual((report["verified_reviews_of_us"], report["unverified_results_naming_us"], report["table"]), (0, 6, []))

    def test_repeated_review_text_is_grouped_without_numbers(self):
        b = Builder()
        b.deal("x", US, 1, review="Paid 500 too much")
        b.deal("y", US, 1, review="Paid 700 too much")
        self.assertEqual(il.analyze(b.rows, {US}, CATALOG)["low_review_templates"], [("Paid # too much", 2)])

    def test_a_rule_applied_to_every_shop_at_once_is_not_flagged(self):
        b = Builder()
        for _ in range(30):
            b.rows.append({"seq": len(b.rows), "ts": 100.0, "kind": "result", "data": {"order": "n", "buyer": "x", "shop": OTHER, "stars": 5, "verified": True}})
        for _ in range(30):
            b.rows.append({"seq": len(b.rows), "ts": 1e6, "kind": "result", "data": {"order": "n", "buyer": "x", "shop": OTHER, "stars": 1, "verified": True}})
        for _ in range(10):
            b.rows.append({"seq": len(b.rows), "ts": 1e6, "kind": "result", "data": {"order": "n", "buyer": "x", "shop": US, "stars": 1, "verified": True}})
        report = il.analyze(b.rows, {US}, CATALOG)
        row = row_for(report, "x")
        self.assertEqual(row["time_matched"], {"expected_low": 10.0, "observed_low": 10, "z": None})
        self.assertEqual(row["grade"], "none")
        self.assertEqual(report["low_runs_across_all_shops"]["x"]["since_low"], 40)

    def test_like_deals_explain_a_buyer_who_rates_by_warranty(self):
        b = Builder()
        for _ in range(6):
            b.deal("x", US, 3)
        for _ in range(20):
            b.deal("x", OTHER, 3)
        for _ in range(60):
            b.add("settle", order=f"w{b.seq}", buyer="x", shop=OTHER, item="g", amount=500, warranty_hours=6)
            b.add("result", order=f"w{b.seq - 1}", buyer="x", shop=OTHER, stars=5, review="ok", verified=True)
        row = row_for(il.analyze(b.rows, {US}, CATALOG), "x")
        self.assertGreater(row["gap"], 1)
        self.assertEqual((row["like_for_like_gap"], row["grade"]), (0.0, "none"))

    def test_a_buyer_split_by_receipt_format_is_listed(self):
        b = Builder()
        for shop in (US, OTHER):
            for i in range(5):
                b.add("result", order=f"s{b.seq}", buyer="x", shop=shop, stars=1, verified=True, receipt={"item": "G0123abcd"})
        for i in range(5):
            b.add("result", order=f"n{b.seq}", buyer="x", shop=OTHER, stars=5, verified=True, receipt={"item": "김치"})
        found = il.analyze(b.rows, {US}, CATALOG)["by_receipt_format"]
        self.assertEqual(found[0]["sku"], {"n": 10, "mean": 1.0, "low": 10, "shops": 2, "on_us": 5})
        self.assertEqual(found[0]["name"]["mean"], 5.0)

    def test_fisher_matches_a_hand_value(self):
        self.assertAlmostEqual(il.fisher_greater(3, 0, 0, 3), .05)
        self.assertEqual(il.fisher_greater(0, 3, 0, 3), 1.0)


class Fetching(unittest.TestCase):
    def test_pages_skip_covered_sales_keep_only_used_rows_and_stop_on_refusal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".market").mkdir()
            records = {str(s): {"ledger": s, "source": "local_acp_settled", "round": 3, "unit": "u"} for s in (10, 11, 30, 50)}
            (root / ".market/local-settlements.json").write_text(json.dumps({"records": records}), encoding="utf-8")
            asked = []

            def get(path, params=None):
                asked.append(params)
                if params and params.get("after") == 49:
                    return SimpleNamespace(status_code=429, json=list)
                after = params["after"]
                rows = [{"seq": after + 1, "kind": "settle", "data": {}}, {"seq": after + 5, "kind": "order", "data": {}}]
                return SimpleNamespace(status_code=200, json=lambda: rows, text="{}")
            meta = il.fetch(root, root / "out", client=SimpleNamespace(get=get), sleep=lambda _s: None)
            self.assertEqual([a["after"] for a in asked], [9, 29, 49])
            self.assertEqual((meta["calls"], meta["stopped"]), (3, "http_429"))
            kept = [json.loads(line)["kind"] for line in (root / "out/rows.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(kept, ["settle", "settle"])


if __name__ == "__main__":
    unittest.main()
