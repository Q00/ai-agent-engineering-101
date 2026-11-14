"""Buyer rules as pure functions: caps, surplus, chance of closing, candidates, accept, rating."""
import copy
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_TEST_HOME", tempfile.mkdtemp(prefix="r3-test-"))
os.environ["R3_HOME"] = os.environ["R3_TEST_HOME"]
sys.path.insert(0, str(W))

import policy  # noqa: E402
from tools import buyer_policy as bp  # noqa: E402

POL = json.loads(json.dumps(policy.DEFAULTS))
CATALOG = {
    "F1": {"name": "김치 A", "category": "식품>김치", "list_price": 30000, "kind": "food", "shelf_hours": 720},
    "F2": {"name": "김치 B", "category": "식품>김치", "list_price": 40000, "kind": "food", "shelf_hours": 720},
    "F3": {"name": "김치 C", "category": "식품>김치", "list_price": 20000, "kind": "food", "shelf_hours": 720},
    "G1": {"name": "침대", "category": "가구>침대", "list_price": 100000, "kind": "goods"},
}
FOOD = {"order": "o-f", "item": "김치", "category": "식품>김치", "budget": 50000, "acceptable": ["F1", "F2", "F3"],
        "kind": "food", "use_within_hours": 10, "round": 3}
GOODS = {"order": "o-g", "item": "침대", "category": "가구>침대", "budget": 120000, "acceptable": ["G1"],
         "kind": "goods", "round": 3}


def shop(url, *offers, name=None):
    return {"name": name or url, "url": url, "reviews": [],
            "offers": [{"sku": s, "name": CATALOG.get(s, {}).get("name", s), "qty": q} for s, q in offers]}


class CapTests(unittest.TestCase):
    def test_d6_shares(self):
        self.assertEqual(bp.cap(30000, "food", 0, None, 99999, POL), 28500)
        self.assertEqual(bp.cap(100000, "goods", 0, None, 999999, POL), 60000)  # break_top: 1 - (0.05 + 0.25) / 0.75
        self.assertEqual(bp.cap(100000, "goods", 6, None, 999999, POL), 94000)
        self.assertEqual(bp.cap(100000, "goods", 0, 0.10, 999999, POL), 83333)  # 1 - (0.05 + 0.10) / 0.90 < 0.85

    def test_break_penalty_prices_an_uncovered_break_into_the_goods_cap(self):
        """Saved counts a deal that served nothing as -1: the cap keeps the expected share kept at the floor."""
        pol = json.loads(json.dumps(POL)); pol["buyer"]["min_kept"] = 0.31; pol["buyer"]["cap_goods_no_warranty"] = 0.80
        self.assertEqual(bp.cap(30000, "food", 0, 0.5, 99999, pol), 20700)                 # food: no breaks
        self.assertEqual(bp.cap(100000, "goods", 6, 0.40, 999999, pol), 69000)             # full warranty: min_kept only
        self.assertEqual(bp.cap(100000, "goods", 0, 0.022, 999999, pol), 66053)            # A grade: 1 - 0.332 / 0.978
        self.assertEqual(bp.cap(100000, "goods", 0, 0.40, 999999, pol), 0)                 # C grade: 1 - 0.71 / 0.60 < 0
        self.assertEqual(bp.cap(100000, "goods", 0, None, 999999, pol), 45882)             # unmeasured: q_prior 0.15
        self.assertEqual(bp.cap(100000, "goods", 3, 0.40, 999999, pol), 36250)             # half covered: q 0.20
        pol["buyer"]["break_penalty"] = 0.5
        self.assertEqual(bp.cap(100000, "goods", 0, 0.022, 999999, pol), 67177)            # 1 - 0.321 / 0.978
        pol["buyer"]["break_penalty"] = 0.0
        self.assertEqual(bp.cap(100000, "goods", 0, 0.40, 999999, pol), 54999)             # off: the old 1 - q - min_surplus
        self.assertEqual(bp.cap(100000, "goods", 0, None, 999999, pol), 69000)             # off: min(0.80, 0.69)

    def test_food_cap_keeps_min_surplus(self):
        pol = json.loads(json.dumps(POL))
        pol["buyer"]["min_surplus_list"] = 0.07
        for cap_food, want in [(0.95, 27900), (0.98, 27900), (0.90, 27000)]:     # 0.93 unless cap_food is lower
            with self.subTest(cap_food=cap_food):
                pol["buyer"]["cap_food"] = cap_food
                self.assertEqual(bp.cap(30000, "food", 0, None, 99999, pol), want)

    def test_food_candidates_survive_a_raised_min_surplus(self):
        """Regression: cap_food 0.95 with min_surplus_list 0.07 dropped every food offer."""
        pol = json.loads(json.dumps(POL))
        pol["buyer"]["min_surplus_list"] = 0.07
        cs = bp.candidates(FOOD, [shop("http://a", ("F2", 1))], CATALOG, {}, pol)
        self.assertEqual([c["cap"] for c in cs], [37200])
        self.assertGreaterEqual(cs[0]["surplus_at_cap"], 0.07 * 40000 - 1)
        rec = {"item": "F2", "price": 37000, "unit": "u7", "sig_seller": "x"}
        self.assertAlmostEqual(bp.build_accept(FOOD, cs[0], rec, "u7", 0, pol)["bind"]["max_price_list"], 0.93)

    def test_budget_bounds_the_cap(self):
        self.assertEqual(bp.cap(100000, "food", 0, None, 40000, POL), 40000)

    def test_expected_surplus_matches_strategy_table(self):
        self.assertAlmostEqual(bp.expected_surplus(100000, 50000, "goods", 0.30, 0), 20000)
        self.assertAlmostEqual(bp.expected_surplus(100000, 60000, "goods", 0.05, 6), 38000)
        self.assertAlmostEqual(bp.expected_surplus(100000, 50000, "goods", 0.30, 6), 35000)
        self.assertEqual(bp.expected_surplus(30000, 25000, "food", 0.9, 0), 5000)

    def test_p_success(self):
        fresh = bp.p_success({"attempts": 0, "paid": 0}, None, 70000, 100000)
        self.assertEqual((fresh["p_price"], fresh["p_checkout"], fresh["p"]), (1.0, 0.9, 0.9))
        bad = bp.p_success({"attempts": 10, "paid": 1}, 77500, 70000, 100000)
        self.assertEqual((bad["p_price"], bad["p_checkout"], bad["p"]), (0.5, 0.5, 0.25))
        self.assertEqual(bp.p_success({}, 90000, 70000, 100000)["p"], 0.0)


class BreakageTests(unittest.TestCase):
    SUMMARY = {"market": {"breakage": {"sku": {"G1": {"C": {"n": 10, "k": 4, "q": 0.4, "upper": 0.6}}},
                                       "cat": {"침대": {"B": {"n": 8, "k": 1, "q": 0.125, "upper": 0.3}}},
                                       "grade": {"A": {"n": 100, "k": 2, "q": 0.02, "upper": 0.05}}}}}

    def test_goods_q_prefers_the_sku_grade_row_then_category_then_grade(self):
        p = copy.deepcopy(policy.DEFAULTS)
        self.assertEqual(bp.goods_q(self.SUMMARY, "침대", p, "G1", "C"), (0.4, 0.6))
        self.assertEqual(bp.goods_q(self.SUMMARY, "침대", p, "G1", "B"), (0.125, 0.3))
        self.assertEqual(bp.goods_q(self.SUMMARY, "침대", p, "G1", "A"), (0.02, 0.05))
        self.assertEqual(bp.goods_q(self.SUMMARY, "침대", p, "G1", None), (bp.q_prior(p), None))   # no grade yet: the category q


class CandidateTests(unittest.TestCase):
    def test_offers_intersect_acceptable_one_per_shop(self):
        shops = [shop("http://a", ("F1", 2), ("F2", 1), ("X9", 3)), shop("http://b", ("F3", 1)),
                 shop("http://c"), shop("http://d", ("F2", 0))]
        cs = bp.candidates(FOOD, shops, CATALOG, {}, POL)
        self.assertEqual([(c["shop"], c["sku"]) for c in cs], [("http://a", "F2"), ("http://b", "F3")])
        self.assertEqual(cs[0]["cap"], 38000)
        self.assertIn("shop:http://a", cs[0]["memory_refs"])
        self.assertIn("cat:김치", cs[0]["memory_refs"])
        # F1 and F2 both keep 5 % of list at the cap: the tie goes to the higher list price. The score is the
        # Saved that kept scores (Patch 3.6, food grades A .25 / B .5 / C .25 at the middle cost): 0.1049 x 0.9
        self.assertEqual((cs[0]["ask_est"], cs[0]["kept"], cs[0]["saved_est"], cs[0]["score"]), (None, 0.05, 0.1049, 0.0945))

    def known(self, url, share, n=3, kind="food", w=0.0):
        return {bp.ask_key(url, kind): {"n": n, "share": share, "w": w, "ts": 1}}

    def test_known_cheap_shop_outranks_an_unknown_one(self):
        shops = [shop("http://a", ("F2", 1)), shop("http://b", ("F2", 1))]
        cs = bp.candidates(FOOD, shops, CATALOG, {}, POL, self.known("http://b", 0.80))
        self.assertEqual([c["shop"] for c in cs], ["http://b", "http://a"])
        self.assertEqual((cs[0]["ask_est"], cs[0]["kept"], cs[0]["p_reach"], cs[0]["score"]), (32000, 0.2, 1.0, 0.3778))  # saved_est 0.4198 x 0.9
        self.assertIn("ask est 32000", cs[0]["why"])

    def test_min_kept_caps_goods_without_dropping_them(self):
        pol = json.loads(json.dumps(POL)); pol["buyer"]["min_kept"] = 0.28
        self.assertEqual(bp.cap(100000, "goods", 0, None, 999999, pol), 29333)       # break_top 1 - (0.28 + 0.25) / 0.75
        pol["buyer"]["break_penalty"] = 0
        self.assertEqual(bp.cap(100000, "goods", 0, None, 999999, pol), 70000)       # min(0.70, 0.72)
        self.assertEqual(bp.cap(100000, "goods", 6, None, 999999, pol), 72000)       # min(0.94, 0.72)
        self.assertEqual(bp.cap(30000, "food", 0, None, 99999, pol), 21600)          # min(0.95, 0.72)
        cs = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, pol)
        self.assertEqual([(c["shop"], c["cap"]) for c in cs], [("http://a", 70000)])   # q-adjusted kept < 0.28, still a candidate

    def test_shop_grade_book_ranks_an_a_shop_over_a_cheaper_c_shop(self):
        # Patch 3.6: shop a ships A at 0.65 and B at 0.6 (scores 1.0 / 0.8), shop b ships C at 0.5 (0.77)
        pol = json.loads(json.dumps(POL)); pol["buyer"].update(min_kept=0.3, min_kept_C=0.5)
        book = {"https://a": {"grade": {"food:A": {"n": 5, "med": 0.65}, "food:B": {"n": 5, "med": 0.6}}},
                "https://b": {"grade": {"food:C": {"n": 10, "med": 0.5}}},
                "https://c": {"grade": {"food:C": {"n": 10, "med": 0.55}}}}       # C over its 0.5 cap: no deal
        summary = {"market": {"price_book": book}}
        cs = bp.candidates(FOOD, [shop("https://b", ("F1", 1)), shop("https://a", ("F1", 1)), shop("https://c", ("F1", 1))],
                           CATALOG, summary, pol)
        self.assertEqual([(c["shop"], c["saved_est"]) for c in cs], [("https://a", 0.9), ("https://b", 0.7692), ("https://c", 0.0)])
        self.assertEqual(cs[0]["grade_book"], {"A": {"share": 0.65, "n": 5}, "B": {"share": 0.6, "n": 5}})
        self.assertIn("grade book 10 sales", cs[0]["why"])
        gb = {"A": {"share": 0.65, "n": 1}, "mix": {"A": 1.0}, "n": 1}          # one sale: the shop's overall share
        self.assertEqual(bp.grade_expected(gb, "food", 0, None, pol, 0.6), 1.0)   # kept 0.4 on A

    def test_grade_floors_raise_the_cap_floor_once_the_grade_is_named(self):
        # Patch 3.6: an A (cost 0.55-0.75 of list) scores a full 1 from a kept of 0.45, a C (0.25-0.45) needs 0.75
        pol = json.loads(json.dumps(POL)); pol["buyer"].update(min_kept=0.30, min_kept_A=0.0, min_kept_B=0.40, min_kept_C=0.55)
        self.assertEqual((bp.kept_floor(pol), bp.kept_floor(pol, "A"), bp.kept_floor(pol, "B"), bp.kept_floor(pol, "C")),
                         (0.30, 0.30, 0.40, 0.55))
        self.assertEqual(bp.cap(30000, "food", 0, None, 99999, pol), 21000)                 # min(0.95, 1 - 0.30)
        self.assertEqual(bp.cap(30000, "food", 0, None, 99999, pol, grade="C"), 13500)      # 1 - 0.55
        self.assertEqual(bp.cap(30000, "food", 0, None, 99999, pol, grade="A"), 21000)      # the generic floor
        pol["buyer"]["break_penalty"] = 0
        self.assertEqual(bp.cap(100000, "goods", 6, None, 999999, pol, grade="B"), 60000)   # min(0.94, 1 - 0.40)
        self.assertEqual(bp.cap(100000, "goods", 6, None, 999999, pol, grade="X"), 70000)   # unknown grade: generic
        # the deal's Saved by grade and cost band (policy.margin_share) and over the kind's grade prior
        self.assertEqual(policy.margin_share(0.45, "A", 0), 1.0)                            # the widest A margin
        self.assertEqual(round(policy.margin_share(0.55, "C"), 4), 0.8462)                  # 0.55 / 0.65
        self.assertEqual(round(policy.margin_share(0.55, "C", 1), 4), 1.0)                  # at its high cost
        self.assertIsNone(policy.margin_share(0.5, "D"))
        self.assertEqual(round(policy.expected_margin_share(0.55, "food"), 4), 0.9615)      # .25 x 1 + .5 x 1 + .25 x .8462
        self.assertEqual(round(policy.expected_margin_share(0.30, "goods"), 4), 0.7662)     # .7 x .857 + .2 x .6 + .1 x .4615

    def test_settled_price_in_the_ledger_outranks_first_asks(self):
        shops = [shop("http://a", ("F2", 1)), shop("http://b", ("F2", 1))]
        book = {"market": {"price_book": {"http://a": {"sku": {"F2": {"n": 2, "med": 0.6}}},
                                           "http://b": {"cat": {"김치": {"n": 5, "med": 0.9}}}}}}
        cs = bp.candidates(FOOD, shops, CATALOG, book, POL, self.known("http://b", 0.70))
        self.assertEqual([c["shop"] for c in cs], ["http://a", "http://b"])
        self.assertEqual((cs[0]["ask_est"], cs[0]["price_src"], cs[0]["kept"]), (24000, "ledger", 0.4))
        self.assertIn("settled est 24000", cs[0]["why"])
        self.assertEqual((cs[1]["ask_est"], cs[1]["price_src"]), (36000, "ledger"))   # the book beats the ask
        cs = bp.candidates(FOOD, shops, CATALOG, {}, POL, self.known("http://b", 0.70))
        self.assertEqual((cs[0]["shop"], cs[0]["price_src"], cs[1]["price_src"]), ("http://b", "ask", None))

    def test_known_shop_asking_above_the_cap_ranks_below_an_unknown_one(self):
        shops = [shop("http://a", ("F2", 1)), shop("http://b", ("F2", 1))]
        cs = bp.candidates(FOOD, shops, CATALOG, {}, POL, self.known("http://a", 1.0))
        self.assertEqual([c["shop"] for c in cs], ["http://b", "http://a"])
        self.assertEqual((cs[1]["ask_est"], cs[1]["kept"], cs[1]["p_reach"]), (40000, 0.05, 0.6667))  # cap 38000

    def test_fewer_than_ask_min_n_first_asks_are_ignored(self):
        cs = bp.candidates(FOOD, [shop("http://a", ("F2", 1))], CATALOG, {}, POL,
                           self.known("http://a", 0.5, n=bp.ASK_MIN_N - 1))
        self.assertEqual((cs[0]["ask_est"], cs[0]["score"]), (None, 0.0945))

    def test_food_and_goods_asks_are_separate(self):
        cs = bp.candidates(FOOD, [shop("http://a/", ("F2", 1))], CATALOG, {}, POL,
                           self.known("http://a", 0.5, kind="goods"))
        self.assertIsNone(cs[0]["ask_est"])
        cs = bp.candidates(FOOD, [shop("http://a/", ("F2", 1))], CATALOG, {}, POL, self.known("http://a", 0.5))
        self.assertEqual(cs[0]["ask_est"], 20000)

    def test_known_goods_shop_with_warranty_is_judged_at_the_warranty_cap(self):
        """A goods shop that grants the 6 h warranty closes above the 0.70 cap (negotiate raises it to 0.94)."""
        shops = [shop("http://a", ("G1", 1)), shop("http://b", ("G1", 1))]
        cs = bp.candidates(GOODS, shops, CATALOG, {}, POL, self.known("http://b", 0.85, kind="goods", w=0.75))
        self.assertEqual([c["shop"] for c in cs], ["http://b", "http://a"])
        self.assertEqual((cs[0]["ask_est"], cs[0]["kept"], cs[0]["p_reach"], cs[0]["cap"]), (85000, 0.1125, 1.0, 60000))
        self.assertIn("6 h warranty cap 94000", cs[0]["why"])
        self.assertEqual((cs[1]["kept"], cs[1]["p_reach"]), (0.15, 0.2857))             # unknown: (1 - q) - 0.60; 0.60 - 0.50
        cs = bp.candidates(GOODS, shops, CATALOG, {}, POL, self.known("http://b", 0.85, kind="goods", w=0.25))
        self.assertEqual([c["shop"] for c in cs], ["http://a", "http://b"])             # no warranty: 0.85 > 0.70
        self.assertEqual(cs[1]["p_reach"], 0.0)

    def test_max_shops_and_shop_record_ranks(self):
        shops = [shop(f"http://s{i}", ("F1", 1)) for i in range(5)]

        def stats(summary, url):
            bad = url == "http://s0"
            return {"attempts": 10 if bad else 0, "paid": 0, "refused": {}, "bind_mismatch": 0,
                    "broken_confirmed": 0, "broken_inferred": 0}
        with patch.object(bp.query, "shop_stats", side_effect=stats):
            cs = bp.candidates(FOOD, shops, CATALOG, {}, POL)
        self.assertEqual(len(cs), 3)
        self.assertNotIn("http://s0", [c["shop"] for c in cs])

    def test_goods_use_measured_break_rate_from_enough_observations(self):
        cat = {"uwh": {"n": 0, "median": None}, "budget_ratio": {"n": 0, "p25": None, "p50": None},
               "q": {"n": 40, "upper": 0.10}}
        with patch.object(bp.query, "category_stats", return_value=cat):
            c = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, POL)[0]
        self.assertEqual(c["cap"], 83333)                                           # 1 - (0.05 + 0.10) / 0.90
        c = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, POL)[0]
        self.assertEqual((c["cap"], c["q"]), (60000, bp.q_prior(POL)))

    def test_q_prior_follows_the_policy_cap(self):
        pol = json.loads(json.dumps(POL))
        pol["buyer"]["cap_goods_no_warranty"] = 0.75     # a raised D6 cap must not drop every goods offer
        c = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, pol)
        self.assertEqual([x["cap"] for x in c], [68750])                            # q_prior 0.20: 1 - 0.25 / 0.80

    def test_reference_list(self):
        self.assertEqual(bp.reference_list(FOOD, CATALOG), 30000)


class FoldAskTests(unittest.TestCase):
    def test_first_observation_sets_the_share_then_ewma(self):
        a = bp.fold_ask({}, "http://a/", "food", 0.8, 1000.4)
        self.assertEqual(a, {"http://a|food": {"n": 1, "share": 0.8, "w": 0.0, "ts": 1000}})
        b = bp.fold_ask(a, "http://a", "food", 0.6, 2000)
        self.assertEqual(b, {"http://a|food": {"n": 2, "share": 0.75, "w": 0.0, "ts": 2000}})  # 0.8 + 0.25 x -0.2
        self.assertEqual(a["http://a|food"]["n"], 1)                                    # pure: the input is unchanged

    def test_warranty_share_is_folded_the_same_way(self):
        a = bp.fold_ask({}, "http://a", "goods", 0.85, 0, warranty=True)
        self.assertEqual(a["http://a|goods"]["w"], 1.0)
        self.assertEqual(bp.fold_ask(a, "http://a", "goods", 0.85, 0)["http://a|goods"]["w"], 0.75)

    def test_a_misread_share_is_not_folded(self):
        a = bp.fold_ask({}, "http://a", "food", 0.9, 0)
        for share in (5 / 40000, 0.19, 2.01):
            with self.subTest(share=share):
                self.assertEqual(bp.fold_ask(a, "http://a", "food", share, 0), a)
                self.assertEqual(bp.fold_ask({}, "http://a", "food", share, 0), {})

    def test_kinds_are_separate(self):
        a = bp.fold_ask(bp.fold_ask({}, "http://a", "food", 0.8, 0), "http://a", "goods", 0.6, 0)
        self.assertEqual({k: (e["n"], e["share"]) for k, e in a.items()},
                         {"http://a|food": (1, 0.8), "http://a|goods": (1, 0.6)})

    def test_entries_older_than_48_h_are_dropped(self):
        keep_s = bp.ASK_KEEP_H * 3600
        a = {"http://old|food": {"n": 5, "share": 0.7, "ts": 0}, "http://edge|food": {"n": 5, "share": 0.7, "ts": 1}}
        out = bp.fold_ask(a, "http://new", "food", 0.9, keep_s + 1)
        self.assertEqual(sorted(out), ["http://edge|food", "http://new|food"])
        self.assertEqual(sorted(bp.fresh_asks(a, keep_s + 1)), ["http://edge|food"])   # the read side skips them too

    def test_malformed_record_counts_as_empty(self):
        for asks in (None, [], "x", {"http://a|food": {"n": "3", "share": 0.7, "ts": 0}},
                     {"http://a|food": {"n": 3, "share": 0.7, "w": "1", "ts": 0}},
                     {"http://a|food": {"n": 3, "share": None, "ts": 0}}, {"http://a|food": 7}):
            with self.subTest(asks=asks):
                self.assertEqual(bp.fold_ask(asks, "http://a", "food", 0.9, 10),
                                 {"http://a|food": {"n": 1, "share": 0.9, "w": 0.0, "ts": 10}})
                cs = bp.candidates(FOOD, [shop("http://a", ("F2", 1))], CATALOG, {}, POL,
                                   asks if isinstance(asks, dict) else None)
                self.assertIsNone(cs[0]["ask_est"])


class AcceptAndReceiptTests(unittest.TestCase):
    def setUp(self):
        self.food = bp.candidates(FOOD, [shop("http://a", ("F1", 1))], CATALOG, {}, POL)[0]
        self.goods = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, POL)[0]

    def test_food_accept(self):
        rec = {"item": "F1", "price": 25000, "unit": "u7", "sig_seller": "x"}
        a = bp.build_accept(FOOD, self.food, rec, "u7", 0, POL)
        self.assertEqual(a, {"cert": True, "min_hours_left": 10.2,
                             "bind": {"max_price_list": 0.95, "sku": "F1", "unit": "u7"}})

    def test_goods_accept_with_warranty_and_no_binding(self):
        p = json.loads(json.dumps(POL))
        p["buyer"]["binding"] = False
        a = bp.build_accept(GOODS, self.goods, {"item": "G1", "price": 90000}, None, 6, p)
        self.assertEqual(a, {"cert": True, "warranty_hours": 6})

    def test_category_receipt_binds_by_list_share_only(self):
        a = bp.build_accept(GOODS, self.goods, {"item": "침대", "price": 60000}, None, 0, POL)
        self.assertEqual(a["bind"], {"max_price_list": 0.6})                        # break_top under the defaults

    def test_verify_receipt(self):
        ok = {"item": "F1", "price": 25000, "unit": "u7", "sig_seller": "x"}
        self.assertIsNone(bp.verify_receipt(FOOD, ok, self.food, CATALOG, POL, "u7"))
        cases = [({}, "no signed receipt"), (dict(ok, item="Z1"), "neither"), (dict(ok, price=25000.5), "whole"),
                 (dict(ok, price=60000), "limit"), (dict(ok, price=29000), "cap"),
                 ({k: v for k, v in ok.items() if k != "unit"}, "promised unit")]
        for rec, why in cases:
            with self.subTest(why=why):
                self.assertIn(why, bp.verify_receipt(FOOD, rec, self.food, CATALOG, POL, "u7"))

    def test_receipt_saved_check(self):
        # F1 list 30000; at the bands' middle cost (A 0.65, B 0.50, C 0.35) and RECEIPT_MIN_SAVED 0.70 a C pays at
        # most 0.545 of list (16350), a B 0.65, an A 0.755
        ok = {"item": "F1", "price": 17520, "unit": "u7", "sig_seller": "x"}       # 0.584 of list
        why = bp.verify_receipt(FOOD, dict(ok, claim="C"), self.food, CATALOG, POL, "u7")
        self.assertTrue(why.startswith(bp.SAVED_CHECK), why)
        self.assertIn("claim C at 0.584 of list scores 0.640", why)
        for rec in (dict(ok, claim="B"), dict(ok, claim="A"), ok, dict(ok, claim="X"),   # B 0.83, A 1, no claim
                    dict(ok, claim="C", price=16200)):                                  # C at 0.54 scores 0.71
            with self.subTest(rec=rec):
                self.assertIsNone(bp.verify_receipt(FOOD, rec, self.food, CATALOG, POL, "u7"))
        self.assertTrue(bp.verify_receipt(FOOD, dict(ok, claim="B", price=19800), self.food, CATALOG, POL,
                                          "u7").startswith(bp.SAVED_CHECK))             # B at 0.66 scores 0.68

    def test_receipt_cert_false(self):
        ok = {"item": "F1", "price": 25000, "unit": "u7", "sig_seller": "x"}
        self.assertIn("cert: false", bp.verify_receipt(FOOD, dict(ok, cert=False), self.food, CATALOG, POL))
        for rec in (ok, dict(ok, cert=True)):
            self.assertIsNone(bp.verify_receipt(FOOD, rec, self.food, CATALOG, POL))
        p = json.loads(json.dumps(POL))
        p["buyer"]["accept_cert"] = False
        goods = {"item": "G1", "price": 90000, "sig_seller": "x", "cert": False}
        self.assertIsNone(bp.verify_receipt(GOODS, goods, self.goods, CATALOG, p, warranty_h=6))
        no_hours = {k: v for k, v in FOOD.items() if k != "use_within_hours"}
        self.assertIsNone(bp.verify_receipt(no_hours, dict(ok, cert=False), self.food, CATALOG, p))
        # food min_hours_left needs the certificate's expiry: the kit refuses even without accept_cert
        self.assertIn("cert: false", bp.verify_receipt(FOOD, dict(ok, cert=False), self.food, CATALOG, p))

    def test_food_claim_must_cover_use_within(self):
        ok = {"item": "F1", "price": 16000, "unit": "u7", "sig_seller": "x"}     # F1 shelf 720 h: C < 324, B < 576
        long = dict(FOOD, use_within_hours=400)                                  # (0.533 of list: passes the Saved check)
        self.assertIn("claim C", bp.verify_receipt(long, dict(ok, claim="C"), self.food, CATALOG, POL))
        self.assertIsNone(bp.verify_receipt(long, dict(ok, claim="B"), self.food, CATALOG, POL))
        self.assertIsNone(bp.verify_receipt(FOOD, dict(ok, claim="C"), self.food, CATALOG, POL))
        edge = dict(FOOD, use_within_hours=323.9)                                # 324 < 323.9 + food_margin_h 0.2
        self.assertIn("claim C", bp.verify_receipt(edge, dict(ok, claim="C"), self.food, CATALOG, POL))
        equal = dict(FOOD, use_within_hours=323.8)                               # C means under 324 h, the kit wants 324
        self.assertIn("claim C", bp.verify_receipt(equal, dict(ok, claim="C"), self.food, CATALOG, POL))
        self.assertIsNone(bp.verify_receipt(FOOD, dict(ok, claim=["C"]), self.food, CATALOG, POL))
        goods = {"item": "G1", "price": 50000, "sig_seller": "x", "claim": "C"}   # goods have no shelf life (0.5 of
                                                                                    # list: passes the Saved check)
        order = dict(GOODS, use_within_hours=400)
        self.assertIsNone(bp.verify_receipt(order, goods, self.goods, CATALOG, POL, warranty_h=6))

    def test_grade_top_matches_the_kit_cuts(self):
        """A sync check between our two local copies of the cuts, not a market patch detector:
        if agent.md's 'Supply' grade cuts change, update pricing.FOOD_GRADE_CUTS and the kit by hand."""
        src = (W / "market_kit.py").read_text(encoding="utf-8")
        kit = tuple(float(x) for x in re.search(r"^FOOD_GRADE_CUTS = \(([\d.]+), ([\d.]+)\)", src, re.M).groups())
        self.assertEqual((bp.GRADE_TOP["B"], bp.GRADE_TOP["C"]), kit)

    def test_cap_of_the_shipped_sku_not_the_asked_one(self):
        rec = {"item": "F3", "price": 25000, "sig_seller": "x"}     # F3 lists at 20000
        self.assertIn("above the cap 19000", bp.verify_receipt(FOOD, rec, self.food, CATALOG, POL))

    def test_goods_warranty_cap_only_with_warranty(self):
        rec = {"item": "G1", "price": 90000, "sig_seller": "x"}
        self.assertIsNotNone(bp.verify_receipt(GOODS, rec, self.goods, CATALOG, POL))
        self.assertIsNone(bp.verify_receipt(GOODS, rec, self.goods, CATALOG, POL, warranty_h=6))


class RateDealTests(unittest.TestCase):
    ROW = {"id": "d-buyer-1", "choice": {"sku": "F1", "unit": "u7", "V": 30000, "name": "김치 A"}}
    RECEIPT = {"shop": "s", "item": "F1", "price": 22000, "task": "t", "unit": "u7", "sig_seller": "x"}

    def deal(self, **settled):
        s = {"shop": "http://a", "unit": "u7", "sku": "F1", "name": "김치 A", "grade": "A", "claim": None,
             "claim_honest": None, "value": 30000, "ledger": 5}
        s.update(settled)
        return {"order": dict(FOOD), "receipt": self.RECEIPT, "settled": s}

    def test_settled_as_agreed(self):
        stars, review = bp.rate_deal(self.deal(), self.ROW)
        self.assertEqual(stars, 5)
        self.assertIn("paid 22000 for list 30000", review)
        self.assertIn("unit u7 as named", review)
        self.assertNotIn(str(FOOD["budget"]), review)

    def test_price_bands(self):
        for price, stars in [(28000, 4), (29500, 3)]:
            with self.subTest(price=price):
                d = self.deal()
                d["receipt"] = dict(self.RECEIPT, price=price)
                self.assertEqual(bp.rate_deal(d, self.ROW)[0], stars)

    def test_problems(self):
        self.assertEqual(bp.rate_deal(self.deal(unit="u8"), self.ROW)[0], 1)
        self.assertEqual(bp.rate_deal(self.deal(sku="F2"), self.ROW)[0], 1)
        stars, review = bp.rate_deal(self.deal(claim="A", claim_honest=False, grade="B"), self.ROW)
        self.assertEqual(stars, 2)
        self.assertIn("claimed grade A", review)
        self.assertEqual(bp.rate_deal(self.deal(value=0), self.ROW)[0], 2)

    def test_without_decision_row_uses_settled_value(self):
        self.assertEqual(bp.rate_deal(self.deal(), None)[0], 5)

    def test_provisional(self):
        stars, review = bp.rate_deal(self.RECEIPT, self.ROW, provisional=True)
        self.assertEqual(stars, 5)
        self.assertIn("pending settlement", review)


if __name__ == "__main__":
    unittest.main()
