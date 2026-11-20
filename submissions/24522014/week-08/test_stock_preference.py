"""More listed stock breaks near-ties only: it never overrides a clearly better surplus, a fault, a known price or the refusal skip."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import buyer_support as support
import buyer_funnel
from r3_policy import load_policy
import replay_stock_preference as replay

T = 1793760000.0
RULE = {"enabled": True, "surplus_tolerance_ratio": .05}
POLICY = load_policy()["buying"]
SETTINGS = {**support.config(), "mode": "balanced", "listed_stock_preference": RULE,
            "refusal_skip": {"enabled": True, "window": 10, "min_share": .9, "retry_seconds": 1800}}
CATALOG = {"G1": {"kind": "goods", "list_price": 100000}, "G2": {"kind": "goods", "list_price": 104000}, "G3": {"kind": "goods", "list_price": 300000}}
CTX = {"targets": {}, "rival_names": set(), "rival_urls": set()}


def entry(shop, surplus=1000, qty=1, sku="G1", known=None, faults=0):
    return {"shop": shop, "sku": sku, "estimated_surplus": surplus, "listed_qty": qty, "past_settled_price": known, "attributed_faults": faults}


def shop(name, qty, sku="G1"):
    return {"name": name, "url": "https://shops/" + name, "reviews": [], "offers": [{"sku": sku, "qty": qty}]}


def refusals(name):
    at = datetime.fromtimestamp(T - 60, timezone.utc).isoformat()
    return [{"round": 3, "shop_name": name, "timestamp": at, "refused_at_open": True, "decision_trail": []} for _ in range(10)]


class Bands(unittest.TestCase):
    def test_bands_match_the_refusal_table(self):
        for qty, band in ((1, 1), (2, 2), (3, 2), (4, 3), (40, 3), (0, 0), (None, 0), ("9", 0), (True, 0), (float("nan"), 0), (float("inf"), 0)):
            with self.subTest(qty=qty):
                self.assertEqual(support.stock_band(qty), band)


class Preference(unittest.TestCase):
    def pick(self, best, *others, rule=RULE):
        return support.prefer_listed_stock([best, *others], best, rule)

    def test_near_tie_goes_to_more_stock(self):
        best = entry("a", 1000, 1)
        better = entry("b", 960, 5)
        self.assertIs(self.pick(best, better), better)

    def test_clearly_better_surplus_is_kept(self):
        best = entry("a", 1000, 1)
        self.assertIs(self.pick(best, entry("b", 949, 50)), best)

    def test_exact_tolerance_edge_counts_as_near(self):
        best = entry("a", 1000, 1)
        edge = entry("b", 950, 4)
        self.assertIs(self.pick(best, edge), edge)

    def test_same_band_keeps_the_better_surplus(self):
        best = entry("a", 1000, 4)
        self.assertIs(self.pick(best, entry("b", 990, 40)), best)

    def test_highest_band_then_surplus_wins(self):
        best = entry("a", 1000, 1)
        two = entry("b", 999, 3)
        four_low = entry("c", 960, 5)
        four_high = entry("d", 970, 4)
        self.assertIs(self.pick(best, two, four_low, four_high), four_high)

    def test_a_fault_never_replaces_a_clean_candidate(self):
        best = entry("a", 1000, 1)
        self.assertIs(self.pick(best, entry("b", 1000, 9, faults=1)), best)

    def test_an_unknown_price_never_replaces_a_known_one(self):
        best = entry("a", 1000, 1, known=5000)
        self.assertIs(self.pick(best, entry("b", 1000, 9)), best)
        known = entry("c", 1000, 9, known=5100)
        self.assertIs(self.pick(best, entry("b", 1000, 9), known), known)

    def test_negative_surplus_is_not_widened_the_wrong_way(self):
        best = entry("a", -1000, 1)
        self.assertIs(self.pick(best, entry("b", -1100, 9)), best)
        near = entry("c", -1040, 9)
        self.assertIs(self.pick(best, near), near)

    def test_off_or_invalid_rule_changes_nothing(self):
        best = entry("a", 1000, 1)
        other = entry("b", 1000, 9)
        for rule in (None, {}, {"enabled": False, "surplus_tolerance_ratio": .05}, {"enabled": "yes", "surplus_tolerance_ratio": .05},
                     {"enabled": True}, {"enabled": True, "surplus_tolerance_ratio": .5}, {"enabled": True, "surplus_tolerance_ratio": -.1},
                     {"enabled": True, "surplus_tolerance_ratio": "x"}):
            with self.subTest(rule=rule):
                self.assertIs(self.pick(best, other, rule=rule), best)

    def test_live_setting_stays_within_the_bound(self):
        self.assertEqual(support.config()["listed_stock_preference"], {"enabled": True, "surplus_tolerance_ratio": .05})


class InChoose(unittest.TestCase):
    def choose(self, shops, memory=(), catalog=CATALOG, settings=SETTINGS):
        return support.choose(shops, list(memory), catalog, 10**7, POLICY, [], [], CTX, support.initial(), settings, T)

    def test_stock_decides_a_tie_and_leaves_a_record(self):
        chosen, sku, cap, _, evidence = self.choose([shop("a", 1), shop("b", 6)])
        self.assertEqual((chosen["name"], sku), ("b", "G1"))
        row = next(e for e in evidence if e["shop"] == "b")
        self.assertEqual(row["listed_stock_preferred_over"], {"shop": "a", "sku": "G1", "listed_qty": 1, "surplus_given_up": 0})
        self.assertEqual(row["listed_qty"], 6)

    def test_each_candidate_keeps_its_own_cap(self):
        alone = self.choose([shop("a", 6)])[2]
        self.assertEqual(self.choose([shop("a", 1), shop("b", 6)])[2], alone)

    def test_the_rule_off_gives_the_old_pick(self):
        off = {**SETTINGS, "listed_stock_preference": {"enabled": False}}
        self.assertEqual(self.choose([shop("a", 1), shop("b", 6)], settings=off)[0]["name"], "a")

    def test_a_higher_list_price_alone_no_longer_beats_more_stock(self):
        # Saved counts the share off list. With no known price both sit at the same cap share, so the amount is no reason to prefer one.
        chosen, sku, *_ = self.choose([shop("a", 1, "G3"), shop("b", 50, "G1")])
        self.assertEqual((chosen["name"], sku), ("b", "G1"))

    def test_a_skipped_shop_with_plenty_of_stock_is_not_preferred(self):
        chosen, _, _, _, evidence = self.choose([shop("dragonash", 500), shop("a", 1)], refusals("dragonash"))
        self.assertEqual(chosen["name"], "a")
        self.assertEqual(next(e for e in evidence if e["shop"] == "dragonash")["support_skip_reason"], "recent_open_refusals")

    def test_when_a_skipped_shop_comes_due_for_a_retry_stock_may_pick_it(self):
        old = [dict(r, timestamp=datetime.fromtimestamp(T - 1801, timezone.utc).isoformat()) for r in refusals("dragonash")]
        self.assertEqual(self.choose([shop("dragonash", 500), shop("a", 1)], old)[0]["name"], "dragonash")

    def test_only_candidate_is_still_tried_when_skipped(self):
        self.assertEqual(self.choose([shop("dragonash", 500)], refusals("dragonash"))[0]["name"], "dragonash")


class Reporting(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "logs/market-buyer").mkdir(parents=True)
        (self.root / ".market").mkdir()
        (self.root / ".market/payments.jsonl").write_text("", encoding="utf-8")

    def add(self, name, record, evidence=()):
        (self.root / f"logs/market-buyer/{name}-abcd.json").write_text(json.dumps({"record": record, "selection_evidence": list(evidence)}), encoding="utf-8")

    def test_funnel_counts_applied_preferences(self):
        base = {"order": "o", "outcome": "no_deal", "item": "G1", "url_at_time": "u", "decision_trail": [], "failure_reason": "seller_rejected", "selected_offer_qty": 5}
        self.add("20261105T010000000000Z", {**base, "refused_at_open": True, "buyer_support": {"listed_stock_preferred_over": {"surplus_given_up": 30}}})
        self.add("20261105T010001000000Z", {**base, "refused_at_open": False, "buyer_support": {"listed_stock_preferred_over": None}})
        report = buyer_funnel.funnel(self.root, "20261105T000000000000Z")
        self.assertEqual(report["listed_stock_preference"], {"applied": 1, "refused_at_open": 1, "paid": 0, "surplus_given_up_estimate": 30})
        self.assertEqual(report["open_refusals_by_listed_qty"]["4+"], {"attempts": 2, "refused_at_open": 1})

    def test_replay_uses_a_saved_figure_then_a_nearby_observation_and_reports_the_trade_off(self):
        evidence = [dict(entry("a", 1000, None), support_eligible=True), dict(entry("b", 960, None), support_eligible=True),
                    dict(entry("c", 500, 9), support_eligible=True)]
        record = {"outcome": "no_deal", "shop_name": "a", "item": "G1", "selected_offer_qty": 1, "refused_at_open": True}
        self.add("20261105T010000000000Z", record, evidence)
        self.add("20261105T010100000000Z", {**record, "shop_name": "b", "selected_offer_qty": 6, "refused_at_open": False}, evidence)
        runs = replay.load(self.root, "20261105T000000000000Z")
        self.assertEqual(len(runs), 2)
        report = replay.replay(runs, .05, 1800)
        self.assertEqual(report["changed"], 1)
        self.assertEqual(report["band_moves"], {"1->4+": 1})
        self.assertEqual(report["refused_at_open_among_changed"], 1)
        self.assertEqual(report["refusals_avoided_estimate"], 1.0)
        self.assertEqual(report["estimated_surplus_given_up"], 40)
        self.assertEqual(report["top_shops_after"][0], {"shop": "b", "share": 1.0})


if __name__ == "__main__":
    unittest.main()
