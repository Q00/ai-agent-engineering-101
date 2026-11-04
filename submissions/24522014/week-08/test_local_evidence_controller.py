from datetime import datetime
import os
from pathlib import Path
import tempfile
import time
import unittest

from local_settlements import KST
from market_state import write
import strategy_controller as controller
from test_strategy_controller import observation, trade, start, SETTINGS, T


class LocalEvidenceIntegration(unittest.TestCase):
    def fixture(self, root, confirmed_count, now):
        md = Path(root)/".market"
        log = Path(root)/"logs/market-kit.log"
        log.parent.mkdir(parents=True)
        clock = lambda ts: datetime.fromtimestamp(ts, KST).strftime("%H:%M:%S")
        lines = [f"{clock(now-100)} [market] shop registered as kcwarni, round 3\n"]
        for i in range(1, confirmed_count+1):
            lines.append(f"{clock(now-30+i)} [acp] settled fixture at 200, ledger #{i}, unit u{i} grade A\n")
            write(md/"signed-offers"/f"{i}.json", {"unit": f"u{i}", "sku": "G1", "cost": 100,
                  "price": 200, "issued_at": now-60, "strategy_experiment": None})
        log.write_text("".join(lines), encoding="utf8")
        os.utime(log, (now, now))
        write(md/"inventory.json", {"round": 3, "balance": 5000000, "units": []})
        # Deliberately incomplete public evidence: one transaction that is not in our local log.
        write(md/"signed-offers/999.json", {"unit": "unlogged", "sku": "G1", "cost": 100,
              "price": 200, "issued_at": now-60, "strategy_experiment": None})
        write(md/"market-intel.json", {"round": 3, "observed_at": now,
              "competition": {"own": {"shops": {"alive": True, "deals": 1, "visits": 20,
               "revenue": 200, "stars": 5, "reviews": 1}}},
              "rows": [{"round": 3, "kind": "settle", "seq": 999, "ts": now-10,
                        "data": {"unit": "unlogged", "item": "G1", "amount": 200, "grade": "A"}}]})
        return md

    def test_local_confirmations_survive_missing_public_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time(); md = self.fixture(tmp, 3, now)
            before = (md/"market-intel.json").read_bytes()
            result = controller.observation(md, now)
            again = controller.observation(md, now)
            self.assertTrue(result["valid"])
            self.assertEqual(result["deals"], 3)
            self.assertEqual(result["revenue"], 600)
            self.assertEqual(result["board_deals"], 1)
            self.assertEqual(len(result["own_sales"]), 3)
            self.assertEqual({s["source"] for s in result["own_sales"]}, {"local_acp_settled"})
            self.assertEqual(again["deals"], 3)
            self.assertEqual((md/"market-intel.json").read_bytes(), before)

    def test_public_settlement_without_local_confirmation_is_not_own_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time(); md = self.fixture(tmp, 0, now)
            result = controller.observation(md, now)
            self.assertTrue(result["valid"])
            self.assertEqual(result["deals"], 0)
            self.assertEqual(result["own_sales"], [])

    def test_public_price_comparison_stays_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time(); md = self.fixture(tmp, 3, now)
            unit = {"unit": "stock", "sku": "G1", "kind": "goods", "grade": "A", "cost": 100,
                    "list_price": 200, "supplied_at": now, "expires_at": None}
            write(md/"inventory.json", {"round": 3, "balance": 5000000, "units": [unit]})
            write(md/"catalog.json", {"G1": {"list_price": 200}})
            write(md/"purchase-intel.json", {"status": "ok", "source_observed_at": now,
                  "summary": {"own_stock_comparison": [{"unit": "stock",
                  "same_grade_zero_warranty_prices": {"count": 5, "median": 150}}]}})
            result = controller.observation(md, now)
            self.assertEqual(result["price_candidates"][0]["median"], 150)
            self.assertEqual(result["price_candidates"][0]["count"], 5)
            self.assertEqual(len(result["own_sales"]), 3)

    def test_existing_history_is_reconciled_without_lowering_minimum_samples(self):
        state = controller.initial()
        state["history"] = [controller.compact(observation(T, 99, 9999, 0))]
        counters = [{"ledger": i, "ts": T+i, "amount": 200} for i in range(1, 11)]
        current = observation(T+7200, 10, 2000, 1000, self_evidence="local_acp_v1", local_counters=counters,
                              own_sales=[trade(i, T+i) for i in range(1, 4)])
        result, events = controller.transition(state, current, SETTINGS)
        self.assertEqual(result["history"][0]["deals"], 0)
        self.assertEqual(result["self_evidence"], "local_acp_v1")
        self.assertEqual(result["phase"], "testing")
        self.assertEqual(len(result["active"]["baseline_cohort"]), 3)
        self.assertTrue(any(e["event"] == "self_evidence_migrated" for e in events))
        diagnosis = next(e for e in events if e["event"] == "diagnosis")
        self.assertEqual(diagnosis["candidates"][0]["own_baseline_settlements"], 3)
        self.assertEqual(diagnosis["candidates"][0]["own_evidence_source"], "local_acp_v1")
        with_two = {**current, "own_sales": current["own_sales"][:2]}
        waiting, _ = controller.transition(state, with_two, SETTINGS)
        self.assertIsNone(waiting["active"])
        self.assertEqual(waiting["wait_reason"], "insufficient_local_same_condition_settlements")

    def test_source_migration_bypasses_normal_diagnosis_interval(self):
        from unittest.mock import patch
        state = controller.initial()
        state["checked_at"] = T+7200
        state["history"] = [controller.compact(observation(T))]
        current = observation(T+7260, 10, 2000, 1000, self_evidence="local_acp_v1",
                              local_counters=[{"ledger": i, "ts": T+i, "amount": 200} for i in range(1, 11)],
                              own_sales=[trade(i, T+i) for i in range(1, 4)], price_consumer_ready=False)
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            write(md/"strategy-state.json", state)
            with patch.object(controller, "observation", return_value=current):
                result = controller.run(md, now=T+7260)
            self.assertIn("self_evidence_migrated", result["events"])

    def test_new_unit_uses_same_sku_grade_public_prices_without_relaxing_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time(); md = self.fixture(tmp, 3, now)
            unit = {"unit": "new-stock", "sku": "G1", "kind": "goods", "grade": "A", "cost": 100,
                    "list_price": 200, "supplied_at": now, "expires_at": None}
            write(md/"inventory.json", {"round": 3, "balance": 5000000, "units": [unit]})
            write(md/"catalog.json", {"G1": {"list_price": 200}})
            write(md/"purchase-intel.json", {"status": "ok", "source_observed_at": now,
                  "summary": {"products": [{"sku": "G1", "conditions": [
                  {"grade": "B", "warranty_hours": 0, "prices": {"count": 9, "median": 110}},
                  {"grade": "A", "warranty_hours": 6, "prices": {"count": 9, "median": 180}},
                  {"grade": "A", "warranty_hours": 0, "prices": {"count": 4, "median": 150}}]}]}})
            candidate = controller.observation(md, now)["price_candidates"][0]
            self.assertEqual(candidate["median"], 150)
            self.assertEqual(candidate["count"], 4)

    def test_source_migration_closes_an_old_trial_before_mixing_definitions(self):
        state, _, _ = start()
        counters = [{"ledger": i, "ts": T+i, "amount": 200} for i in range(1, 12)]
        current = observation(T+7300, 11, 2200, 1100, self_evidence="local_acp_v1", local_counters=counters)
        result, events = controller.transition(state, current, SETTINGS)
        self.assertIsNone(result["active"])
        self.assertEqual(result["phase"], "waiting_for_comparable_evidence")
        self.assertTrue(any(e.get("reason") == "self_evidence_source_migration" for e in events))


if __name__ == "__main__":
    unittest.main()
