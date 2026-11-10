"""Hourly research cadence, clocks, and unchanged spending protections."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from feedback import atomic_json, load_json
import hourly_demand as hourly
import restock
import test_adaptive_stock as fixtures


class HourlyDemandTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AdaptiveStockTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        self.rules = hourly.configuration(f.cfg)
        self.clock = {"status": "synced", "now": f.now, "uncertainty_seconds": 1}
        self.tmp = tempfile.TemporaryDirectory(dir=fixtures.ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / ".market"
        atomic_json(self.md / "catalog.json", f.catalog)
        atomic_json(self.md / "market-evidence.json", f.public)
        atomic_json(self.root / "shop-policy.json", f.policy)
        for target in ("harness.decide", "httpx.Client", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("Research is local only"))
            guard.start()
            self.addCleanup(guard.stop)

    def build(self, **changes):
        f = self.f
        kwargs = dict(public=f.public, catalog=f.catalog, rnd=3, now=f.now,
                      clock=self.clock, rules=self.rules)
        return hourly.build(**{**kwargs, **changes})

    def refresh(self, now=None):
        f = self.f
        with patch("hourly_demand.schedule_now", return_value=self.clock):
            return hourly.refresh(self.root, f.inv, f.cfg, f.now if now is None else now)

    def test_hourly_snapshot_survives_restarts_and_refreshes_at_boundary(self):
        f = self.f
        first = self.refresh()
        self.assertTrue(first["rows"])
        self.assertEqual(self.refresh(f.now+3599)["report_id"], first["report_id"])
        self.assertEqual(len((self.root / "logs/hourly-demand.jsonl").read_text().splitlines()), 1)
        f.public["updated_at"] += 3600
        f.public["market_stock"]["observed_at"] += 3600
        for e in f.public["events"]:
            e["ts"] += 3600
        atomic_json(self.md / "market-evidence.json", f.public)
        self.clock["now"] += 3600
        second = self.refresh(f.now+3600)
        self.assertNotEqual(second["report_id"], first["report_id"])
        self.assertEqual(len((self.root / "logs/hourly-demand.jsonl").read_text().splitlines()), 2)

    def test_market_hour_and_local_cache_clock_are_separate(self):
        f = self.f
        for offset in (-780, 780):
            public = deepcopy(f.public)
            public["updated_at"] += offset
            public["market_stock"]["observed_at"] += offset
            report = self.build(public=public, now=f.now+offset)
            self.assertEqual(report["market_window_end"], f.now)
            self.assertEqual(report["clock_offset_seconds"], -offset)
            self.assertEqual([r["sales"] for r in report["rows"]], [3]*len(f.catalog))
        public["events"] = [{**e, "ts": f.now-3601} for e in f.public["events"]]
        self.assertEqual(self.build(public=public, now=f.now+780)["rows"], [])

    def test_missing_stale_or_bad_clock_is_not_zero_demand_and_does_not_spam(self):
        f = self.f
        for changes in ({"status": "missing"}, {"now": float("nan")}, {"uncertainty_seconds": 10}):
            self.assertIsNone(self.build(clock={**self.clock, **changes}))
        for key in ("updated_at",):
            self.assertIsNone(self.build(public={**f.public, key: f.now-901}))
        self.assertIsNone(self.build(public={**f.public, "market_stock": None}))
        (self.md / "market-evidence.json").unlink()
        self.assertIsNone(self.refresh())
        self.assertIsNone(self.refresh(f.now+60))
        rows = (self.root / "logs/hourly-demand.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["status"], "unavailable")
        atomic_json(self.md / "market-evidence.json", f.public)
        self.assertIsNotNone(self.refresh(f.now+60))

    def test_diverse_buyers_and_known_shops_not_nominal_price_rank_demand(self):
        f = self.f
        for e in f.public["events"]:
            if e["sku"] == fixtures.ALT1:
                e["price"] = 1
            else:
                e["buyer"] = "one-buyer"
        report = self.build()
        self.assertEqual([r["sku"] for r in report["rows"]], [fixtures.ALT1])
        f.public["market_stock"]["shop_names_by_url"] = {}
        self.assertEqual(self.build()["rows"], [])

    def test_expired_wrong_round_malformed_reports_and_disabled_policy_have_no_priority(self):
        f = self.f
        report = self.build()
        for bad in ({**report, "round": 2}, {**report, "generated_at": f.now-3600},
                    {**report, "generated_at": f.now+1}, {**report, "rows": [{"sku": fixtures.ALT1, "rank": 2}]}):
            self.assertEqual(hourly.ranks(bad, f.cfg, 3, f.now), {})
        self.assertEqual(hourly.ranks(report, {**f.cfg, "hourly_demand": {"enabled": False}}, 3, f.now), {})

    def priority_report(self):
        report = self.build()
        report["rows"].sort(key=lambda r: r["sku"] != fixtures.ALT1)
        for i, row in enumerate(report["rows"], 1):
            row["rank"] = i
        return report

    def plan(self, report):
        f = self.f
        return restock.plan(f.inv, f.catalog, f.demand, f.memory, [], f.cfg, f.now,
                            f.public, research=report)

    def test_hourly_priority_changes_purchase_order_but_keeps_all_caps(self):
        f = self.f
        decision, evidence = self.plan(self.priority_report())
        self.assertEqual(decision["buy"][0]["sku"], fixtures.ALT1)
        self.assertEqual(evidence["choices"][0]["reason"], "hourly_market_demand")
        self.assertLessEqual(evidence["hourly_demand"]["priority_units_planned"], 2)
        self.assertLessEqual(sum(r["qty"] for r in decision["buy"]), f.cfg["max_units_per_run"])
        self.assertGreaterEqual(evidence["projected_min_balance"], 2000000)
        f.inv["balance"] = 2000000
        self.assertEqual(self.plan(self.priority_report())[0]["buy"], [])

    def test_current_surge_missing_demand_and_unprofitable_history_override_research(self):
        f = self.f
        report = self.priority_report()
        f.inv["surge"] = [{"sku": fixtures.ALT1, "factor": 1.5}]
        self.assertNotIn(fixtures.ALT1, [r["sku"] for r in self.plan(report)[0]["buy"]])
        f.inv["surge"] = []
        f.public["events"] = [e for e in f.public["events"] if e["sku"] != fixtures.ALT1]
        self.assertNotIn(fixtures.ALT1, [r["sku"] for r in self.plan(report)[0]["buy"]])
        from adaptive_stock import candidates
        self.assertNotIn(fixtures.ALT1, candidates(f.inv, f.catalog, {"furniture"},
            {fixtures.ALT1: {"mature": 3, "net_estimate": -1}}, f.cfg, f.now,
            load_json(self.md / "market-evidence.json"), research=report))

    def test_closed_supply_still_researches_without_purchase_or_network(self):
        f = self.f
        inv = {**f.inv, "restock": {"open": False, "opens_at": f.now+1000, "closes_at": f.now+1180}}
        atomic_json(self.md / "inventory.json", inv)
        with patch("hourly_demand.schedule_now", return_value=self.clock), \
                patch("restock.refresh_market", side_effect=AssertionError("No extra fetch")):
            self.assertEqual(restock.run_once(self.root, f.now), {"buy": []})
        self.assertEqual(load_json(self.md / "hourly-demand.json")["status"], "ready")
        self.assertNotIn("last_window", load_json(self.md / "restock-control.json"))


if __name__ == "__main__":
    unittest.main()
