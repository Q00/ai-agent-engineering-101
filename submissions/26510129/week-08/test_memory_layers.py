import json
import tempfile
import time
import unittest
from pathlib import Path

import objective
import profiles
import rulebook

NOW = time.mktime(time.strptime("2026-11-17 20:00", "%Y-%m-%d %H:%M"))
RULES_34 = "Saved, the part that scores, is the sum over deals of\nsurplus / list price (a deal that served nothing counts -1)"
RULES_36 = ("Saved, the part that scores, is the sum over deals of\n(list price - price) / (list price - unit cost), "
            "the share of the shop's margin kept, at most 1")


def row(shop, at, outcome="deal", price=700, ask=700, first=700, grade="A", **more):
    base = {"shop": shop, "item": "가구>소파", "outcome": outcome, "price": price if outcome in ("deal", "unpaid") else None,
            "list_price": 1000, "ask": ask, "first_offer": first, "last_offer": price or first, "at": at,
            "honest": True, "manipulative": False}
    if outcome == "deal":
        base.update({"settled": True, "grade": grade, "value": 1000, "warranty_hours": 6})
    base.update(more)
    return base


class ObjectiveTest(unittest.TestCase):
    def test_the_same_price_scores_by_the_grade_under_the_margin_rule_and_not_under_the_list_rule(self):
        a, c = objective.unit_cost(1000, "A"), objective.unit_cost(1000, "C")
        self.assertEqual((650, 350), (round(a), round(c)))
        self.assertAlmostEqual(0.857, objective.score(700, 1000, a), places=3)    # grade A at 0.70 of list
        self.assertAlmostEqual(0.462, objective.score(700, 1000, c), places=3)    # grade C at the same price
        self.assertEqual(1.0, objective.score(600, 1000, a))                      # under cost: capped at 1
        self.assertAlmostEqual(0.30, objective.score(700, 1000, c, "list_discount"))
        self.assertAlmostEqual(0.30, objective.score(700, 1000, a, "list_discount"))

    def test_price_for_is_the_inverse_of_score(self):
        for grade in "ABC":
            cost = objective.unit_cost(1000, grade)
            cap = objective.price_for(0.8, 1000, cost)
            self.assertGreaterEqual(objective.score(cap, 1000, cost), 0.8)
            self.assertLess(objective.score(cap + 2, 1000, cost), 0.8)
        self.assertEqual({"A": 720, "B": 600, "C": 480},
                         {g: objective.price_for(0.8, 1000, objective.unit_cost(1000, g)) for g in "ABC"})
        self.assertEqual(200, objective.price_for(0.8, 1000, None, "list_discount"))

    def test_an_unstated_grade_uses_the_kind_and_a_useless_unit_scores_minus_one(self):
        self.assertEqual(0.52, objective.cost_share(None, "food"))
        self.assertEqual(0.61, objective.cost_share(None, "goods"))
        self.assertEqual("food", objective.kind_of({"item": "식품>김치"}))
        deal = row("s", "2026-11-17 10:00")
        self.assertAlmostEqual(0.857, objective.outcome_score(deal), places=3)
        self.assertEqual(-1.0, objective.outcome_score({**deal, "broken": True, "refunded": False}))
        self.assertEqual(0.0, objective.outcome_score({**deal, "broken": True, "refunded": True}))
        self.assertIsNone(objective.outcome_score(row("s", "2026-11-17 10:00", outcome="walked_away")))


class RulebookTest(unittest.TestCase):
    def test_a_changed_rules_page_is_noticed_and_names_the_new_formula(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual("margin_share", rulebook.load(d)["formula"])        # nothing remembered: the default
            book, changed = rulebook.refresh(d, now=1000, fetch=lambda: RULES_34)
            self.assertEqual(("list_discount", False), (book["formula"], changed))  # the first sight is not a change
            book, changed = rulebook.refresh(d, now=1100, fetch=lambda: RULES_36)
            self.assertFalse(changed)                                              # checked 100 s ago: no fetch
            book, changed = rulebook.refresh(d, now=1000 + rulebook.CHECK_EVERY, fetch=lambda: RULES_36)
            self.assertEqual(("margin_share", True, "list_discount"), (book["formula"], changed, book["previous_formula"]))
            self.assertIn("-surplus / list price (a deal that served nothing counts -1)", book["diff"])
            self.assertEqual(RULES_34, Path(d, rulebook.PREVIOUS).read_text())
            self.assertEqual("margin_share", rulebook.load(d)["formula"])

    def test_an_unreadable_or_unknown_page_keeps_the_last_known_formula(self):
        def down():
            raise OSError("down")
        with tempfile.TemporaryDirectory() as d:
            rulebook.refresh(d, now=1000, fetch=lambda: RULES_34)
            book, changed = rulebook.refresh(d, now=9000, fetch=down)
            self.assertEqual(("list_discount", False), (book["formula"], changed))
            book, changed = rulebook.refresh(d, now=9000, fetch=lambda: "Saved is now the buyer's horoscope.")
            self.assertEqual(("list_discount", "unknown", True), (book["formula"], book["named_formula"], changed))


class ProfilesTest(unittest.TestCase):
    def test_old_rows_move_to_the_archive_and_stay_in_the_counters(self):
        rows = ([row("old", "2026-11-10 09:00")] * 6 + [row("old", "2026-11-10 09:30", outcome="walked_away", first=600)]
                + [row("new", "2026-11-17 19:00", grade="C")] * 5)
        with tempfile.TemporaryDirectory() as d:
            prof, moved = profiles.consolidate(rows, d, "margin_share", now=NOW)
            self.assertEqual((7, 5), (moved, len(rows)))                           # the hot file keeps 48 h
            self.assertEqual(7, sum(1 for _ in profiles.archived_rows(d)))
            self.assertEqual((7, 6), (prof["shops"]["old"]["attempts"], prof["shops"]["old"]["deals"]))
            self.assertAlmostEqual(0.857, prof["shops"]["old"]["kept"], places=3)  # known from the counters alone
            self.assertAlmostEqual(0.462, prof["shops"]["new"]["kept"], places=3)
            self.assertAlmostEqual((6 * 0.857143 + 5 * 0.461538) / 111, prof["saved"], places=3)
            # a stale copy of an archived row comes back (another process saved what it had loaded): not counted twice
            rows.insert(0, row("old", "2026-11-10 09:00"))
            prof, moved = profiles.consolidate(rows, d, "margin_share", now=NOW + 60)
            self.assertEqual((1, 5, 7), (moved, len(rows), prof["archived_rows"]))
            self.assertEqual(6, prof["shops"]["old"]["deals"])

    def test_a_new_scoring_rule_rebuilds_the_counters_from_the_archive(self):
        rows = [row("old", "2026-11-10 09:00")] * 6
        with tempfile.TemporaryDirectory() as d:
            prof, _ = profiles.consolidate(rows, d, "margin_share", now=NOW)
            self.assertAlmostEqual(0.857, prof["shops"]["old"]["kept"], places=3)
            prof, _ = profiles.consolidate(rows, d, "list_discount", now=NOW)
            self.assertAlmostEqual(0.30, prof["shops"]["old"]["kept"], places=3)
            self.assertEqual(("list_discount", "margin_share", 6), (prof["formula"], prof["rebuilt_from"], prof["archived_rows"]))

    def test_a_shop_that_never_comes_down_is_fixed_and_a_haggler_has_a_threshold(self):
        at = "2026-11-17 19:00"
        fixed = [row("fixed", at, price=700, ask=700, first=560)] * 9 + [row("fixed", at, outcome="closed", first=560)] * 6
        haggler = [row("haggler", at, price=581, ask=700, first=560)] * 9
        with tempfile.TemporaryDirectory() as d:
            prof, _ = profiles.consolidate(fixed + haggler, d, "margin_share", now=NOW)
            self.assertEqual((1.0, True), (prof["shops"]["fixed"]["theta"], prof["shops"]["fixed"]["fixed"]))
            self.assertEqual((0.83, False), (prof["shops"]["haggler"]["theta"], prof["shops"]["haggler"]["fixed"]))
            self.assertEqual(0.6, prof["shops"]["fixed"]["p_deal"])

    def test_refused_probes_keep_a_shop_fixed_and_accepted_probes_move_its_threshold(self):
        at = "2026-11-17 19:00"
        paid_ask = [row("s", at, price=700, ask=700, first=700)] * 12
        refused = [row("s", at, price=700, ask=700, first=665)] * 4            # 0.95 of the ask, countered, ask paid
        taken = [row("s", at, price=665, ask=700, first=665)] * 4              # the same probe, accepted
        with tempfile.TemporaryDirectory() as d:
            prof, _ = profiles.consolidate(paid_ask + refused, d, "margin_share", now=NOW)
            self.assertEqual((1.0, True), (prof["shops"]["s"]["theta"], prof["shops"]["s"]["fixed"]))
            prof, _ = profiles.consolidate(paid_ask + refused[:1] + taken, d, "margin_share", now=NOW)
            self.assertEqual((0.95, False), (prof["shops"]["s"]["theta"], prof["shops"]["s"]["fixed"]))   # 4 of 5 at 0.95

    def test_refused_payments_mark_what_not_to_bargain_for_at_a_shop(self):
        at = "2026-11-17 19:00"
        unpaid = lambda shop, reason, **more: row(shop, at, outcome="unpaid", unpaid_reason=reason, **more)
        rows = ([unpaid("nograde", "not paying: grade C, the policy accepts ['A', 'B']")] * 6 + [row("nograde", at)] * 4
                + [unpaid("oldfood", "not paying: 110.49 h of shelf life left, the policy asks for 540", item="식품>육류")] * 5
                + [row("oldfood", at, item="식품>육류")] * 5
                + [unpaid("bare-c", "not paying: warranty 0 h, the policy asks for 1")] * 3 + [row("bare-c", at)] * 9
                + [unpaid("unlucky", "checkout refused: 409 unit is not in stock for this order")] * 6 + [row("unlucky", at)] * 4)
        with tempfile.TemporaryDirectory() as d:
            shops = profiles.consolidate(rows, d, "margin_share", now=NOW)[0]["shops"]
        flags = lambda name: tuple(shops[name][k] for k in ("assume_c", "shelf_risk", "c_blocked"))
        self.assertEqual((True, False, False), flags("nograde"))      # 6 of 10 agreed deals were unnamed grade C units
        self.assertEqual((False, True, False), flags("oldfood"))      # 5 refused for shelf life against 5 paid food deals
        self.assertEqual((False, False, True), flags("bare-c"))       # its C units came without the warranty, none paid
        self.assertEqual((False, False, False), flags("unlucky"))     # a unit gone at checkout says nothing of its stock
        self.assertEqual(({"grade_c": 6}, 0.6, 0.4), (shops["nograde"]["refused"], shops["nograde"]["p_unpaid"], shops["nograde"]["p_deal"]))

    def test_steering_bad_deals_and_warranties_survive_compaction(self):
        rows = [row("pushy", "2026-11-09 10:00", manipulative=True),
                row("breaks", "2026-11-09 10:00", broken=True, refunded=False), row("breaks", "2026-11-09 11:00", honest=False),
                row("breaks", "2026-11-09 12:00")]
        rows += [row("covered", "2026-11-09 10:00", list_price=100_000, price=70_000, ask=70_000, first=70_000)] * 3
        with tempfile.TemporaryDirectory() as d:
            prof, _ = profiles.consolidate(rows, d, "margin_share", now=NOW)
            self.assertEqual([], rows)
            self.assertEqual(1, prof["shops"]["pushy"]["steer"])
            self.assertEqual((2, 1), (prof["shops"]["breaks"]["bad"], prof["shops"]["breaks"]["clean_deals"]))
            self.assertEqual(1.0, prof["shops"]["covered"]["warranty"])
            self.assertEqual(json.loads(Path(d, profiles.FILE).read_text())["shops"], prof["shops"])


class PlaybookTest(unittest.TestCase):
    def test_the_seed_is_the_starting_memory_and_an_outage_is_one_alert_not_a_flood(self):
        import playbook
        with tempfile.TemporaryDirectory() as d:
            seed = playbook.load(d)
            self.assertGreaterEqual(len(seed), 10)                       # nothing recorded here yet: the seed
            self.assertFalse(Path(d, playbook.FILE).exists())
            first = playbook.alert_once("model-call-failed", "no answer", d, now=NOW, evidence="exit 1")
            again = playbook.alert_once("model-call-failed", "no answer", d, now=NOW + 3600)
            later = playbook.alert_once("model-call-failed", "no answer", d, now=time.time() + 7 * 3600)
            self.assertEqual(("alert", None), (first["source"], again))
            self.assertIsNotNone(later)                                   # six hours on, it is said again
            stored = playbook.load(d)
            self.assertEqual(len(seed) + 2, len(stored))                  # the file now holds the seed and the alerts
            self.assertIn("[alert/", playbook.digest(stored, 1)[0])


if __name__ == "__main__":
    unittest.main()
