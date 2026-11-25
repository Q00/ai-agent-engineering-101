import json
import os
from pathlib import Path
import tempfile
import unittest

from market_state import read, write
import warranty_trial as wt

NOW = 1793900000
MATURE = NOW - 30000
OLD = NOW - 3 * 86400


class Evaluate(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.md = Path(temp.name) / ".market"
        self.md.mkdir()
        self.assignments, self.records, self.rows = {}, {}, []
        self.official = 0
        write(self.md / "catalog.json", {"G1": {"category": "가구/침구>가구>소파"}, "G2": {"category": "가구/침구>침구>이불"}, "G3": {"category": "가전>주방>토스터"}})

    def sale(self, name, group, sku="G1", amount=1000, cost=700, ts=MATURE):
        self.assignments[f"t-{name}"] = dict(group=group, enrolled=True, hours=2 if group == "warranty" else 0, sku=sku, cost=cost, unit=name, at=ts, expires_at=ts + 600, quote=amount, day="d", floor=1)
        write(self.md / "signed-offers" / f"t-{name}.json", dict(task=f"t-{name}", unit=name, sku=sku, price=amount, cost=cost, warranty_hours=2 if group == "warranty" else 0, issued_at=ts))
        self.records[name] = dict(unit=name, amount=amount, ts=ts, source="local_acp_settled", round=3)

    def run_it(self, now=NOW):
        write(self.md / "warranty-trial.json", {"assignments": self.assignments, "paused": False})
        write(self.md / "local-settlements.json", dict(active_round=3, r3_anchor_found=True, conflicting_ledgers=[], collected_at=now, records=self.records))
        write(self.md / "market-intel.json", dict(round=3, observed_at=now, rows=self.rows, competition={"own": {"shops": {"refunds": self.official}}}))
        return wt.evaluate(self.md, now)

    def broken(self, unit, refunded, warranty=2):
        self.rows.append(dict(seq=len(self.rows) + 1, ts=NOW - 100, round=3, kind="broken", data=dict(unit=unit, refunded=refunded, warranty_hours=warranty, shop="us")))

    def paused(self):
        return read(self.md / "warranty-trial.json").get("paused")

    def test_broken_row_refund_pauses_and_uses_the_real_amount(self):
        self.sale("w1", "warranty")
        self.sale("c1", "control")
        self.broken("w1", 400)
        result = self.run_it()
        self.assertTrue(self.paused())
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["refunds_seen"], {"w1": 400})
        self.assertTrue(read(self.md / "warranty-trial.json")["pause_evidence"]["refund_seen"])

    def test_margin_counts_only_what_was_refunded(self):
        for index in range(3):
            self.sale(f"w{index}", "warranty")
            self.sale(f"c{index}", "control")
        self.official = 0
        self.broken("c0", 400, warranty=0)
        result = self.run_it()
        self.assertEqual((result["warranty_mature_count"], result["control_mature_count"]), (3, 3))
        self.assertFalse(self.paused())

    def test_another_shops_refund_changes_nothing(self):
        self.sale("w1", "warranty")
        self.sale("c1", "control")
        self.broken("someone-elses-unit", 900)
        result = self.run_it()
        self.assertEqual(result["status"], "collecting_mature_comparisons")
        self.assertEqual(result["refunds_seen"], {})
        self.assertFalse(self.paused())

    def test_breakage_without_a_refund_does_not_pause(self):
        self.sale("w1", "warranty")
        self.broken("w1", 0)
        self.run_it()
        self.assertFalse(self.paused())

    def test_a_refund_outside_the_row_sample_is_caught_by_the_official_count(self):
        self.sale("w1", "warranty")
        self.official = 1
        result = self.run_it()
        self.assertTrue(self.paused())
        self.assertEqual(result["refunds_without_a_row"], 1)
        self.assertTrue(read(self.md / "warranty-trial.json")["pause_evidence"]["refund_counted"])

    def test_official_count_alone_does_not_pause_with_nothing_warranted(self):
        self.sale("c1", "control")
        self.official = 1
        self.run_it()
        self.assertFalse(self.paused())

    def test_stale_market_data_cannot_pause(self):
        self.sale("w1", "warranty")
        self.official = 5
        write(self.md / "warranty-trial.json", {"assignments": self.assignments, "paused": False})
        write(self.md / "local-settlements.json", dict(active_round=3, r3_anchor_found=True, conflicting_ledgers=[], collected_at=NOW, records=self.records))
        write(self.md / "market-intel.json", dict(round=3, observed_at=NOW - 4000, rows=[], competition={"own": {"shops": {"refunds": 5}}}))
        result = wt.evaluate(self.md, NOW)
        self.assertFalse(result["valid_sources"])
        self.assertFalse(self.paused())

    def test_a_refund_seen_once_is_remembered(self):
        self.sale("w1", "warranty")
        self.broken("w1", 300)
        self.run_it()
        self.rows.clear()
        self.assertEqual(self.run_it(NOW + 3600)["refunds_seen"], {"w1": 300})

    def test_refund_row_without_an_amount_counts_the_whole_price(self):
        self.assertEqual(wt.refunds_in([dict(round=3, kind="refund", data=dict(unit="u"))]), {"u": None})
        self.assertEqual(wt.refunds_in([dict(round=2, kind="broken", data=dict(unit="u", refunded=5))]), {})

    def test_sales_past_24_hours_stay_in_the_comparison(self):
        self.sale("w1", "warranty", ts=OLD)
        self.sale("c1", "control", ts=OLD)
        result = self.run_it()
        self.assertEqual((result["warranty_mature_total"], result["control_mature_total"]), (1, 1))

    def test_maturity_comes_from_the_setting(self):
        self.sale("w1", "warranty", ts=NOW - 21000)
        self.assertEqual(self.run_it()["warranty_mature_total"], 0)
        self.sale("w2", "warranty", ts=NOW - 22000)
        self.assertEqual(self.run_it(NOW + 1800)["warranty_mature_total"], 2)
        self.assertIn("maturity_seconds", self.run_it(NOW + 3600))

    def test_compares_inside_the_top_category(self):
        self.sale("w1", "warranty", sku="G1")
        self.sale("c1", "control", sku="G2")
        self.sale("c2", "control", sku="G3")
        result = self.run_it()
        self.assertEqual(result["matched_strata"], ["가구/침구"])
        self.assertEqual((result["warranty_mature_count"], result["control_mature_count"]), (1, 1))

    def test_fewer_than_three_a_side_cannot_pause_on_margin(self):
        self.sale("w1", "warranty", cost=990)
        self.sale("w2", "warranty", cost=990)
        for index in range(3):
            self.sale(f"c{index}", "control", cost=100)
        self.run_it()
        self.assertFalse(self.paused())

    def test_three_a_side_with_worse_margin_pauses(self):
        for index in range(3):
            self.sale(f"w{index}", "warranty", cost=900)
            self.sale(f"c{index}", "control", cost=100)
        self.run_it()
        self.assertTrue(self.paused())
        self.assertTrue(read(self.md / "warranty-trial.json")["pause_evidence"]["margin_worse"])

    def test_it_only_ever_pauses(self):
        for index in range(5):
            self.sale(f"w{index}", "warranty", cost=100)
            self.sale(f"c{index}", "control", cost=900)
        before = wt.config()
        self.run_it()
        self.assertEqual(wt.config(), before)
        state = read(self.md / "warranty-trial.json")
        self.assertEqual(set(state), {"assignments", "paused"})
        self.assertFalse(state["paused"])

    def test_assignment_and_liability_are_unchanged(self):
        import inspect
        self.assertIn('controls.get("active")', inspect.getsource(wt.assignment))
        self.assertNotIn("refunds_in", inspect.getsource(wt.assignment) + inspect.getsource(wt.liability))


if __name__ == "__main__":
    unittest.main()
