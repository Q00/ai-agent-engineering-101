import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

import strategy_controller as controller
from strategy_policy import BASE, effective, validate
from market_state import read, write
from r3_policy import selling_terms
from restock_policy import plan
from local_settlements import KST


SETTINGS = controller.config()
T = 100000


def observation(at=T, deals=0, revenue=0, operating=0, **changes):
    return {"at": at, "market_at": at, "valid": True, "deals": deals, "reviews": deals, "visits": deals*2,
            "revenue": revenue, "stars": 5, "operating_value": operating, "balance": 5000000,
            "reserve": 1000000, "stock_cost": 10000, "bankruptcies": 0, "rank": 10, "gap_to_third": .2,
            "buyer_surplus": 1000, "forum_status": "ok", "price_consumer_ready": True,
            "price_candidates": [{"sku": "G1", "grade": "A", "kind": "goods", "quote": 200, "floor": 105,
                                  "list_price": 200, "median": 150, "count": 10}], "own_sales": [], **changes}


def trade(seq, ts, experiment=None, amount=200):
    return {"ledger": seq, "ts": ts, "sku": "G1", "grade": "A", "unit": f"u{seq}",
            "warranty_hours": 0, "amount": amount, "cost": 100, "experiment": experiment}


def start():
    state, _ = controller.transition(None, observation(), SETTINGS)
    before = [trade(i, T+i) for i in range(1, 4)]
    obs = observation(T+7200, 10, 2000, 1000, own_sales=before)
    state, events = controller.transition(state, obs, SETTINGS)
    return state, obs, events


def close_window(state, amount=194):
    identity = state["active"]["id"]
    after = [trade(i, T+7200+i, identity, amount) for i in range(4, 8)]
    obs = observation(T+14400, 24, 4800, 2400, own_sales=after)
    return (*controller.transition(state, obs, SETTINGS), obs)


class BoundedTrials(unittest.TestCase):
    def test_initial_baseline_and_single_three_percent_change(self):
        first, _ = controller.transition(None, observation(), SETTINGS)
        self.assertEqual(first["phase"], "collecting_baseline")
        state, _, events = start()
        self.assertEqual(state["phase"], "testing")
        self.assertEqual(state["controls"]["quote_multipliers"], {"G1": .97})
        self.assertEqual([e["event"] for e in events], ["diagnosis", "start"])

    def test_unchanged_market_snapshot_is_not_an_independent_baseline(self):
        state, _ = controller.transition(None, observation(), SETTINGS)
        obs = observation(T+7200, 0, 0, 0, market_at=T)
        state, _ = controller.transition(state, obs, SETTINGS)
        self.assertEqual(len(state["history"]), 1)
        self.assertIsNone(state["active"])

    def test_trial_closes_then_waits_six_hours_on_previous_policy(self):
        state, _, _ = start()
        state, events, _ = close_window(state)
        self.assertEqual(state["phase"], "maturing")
        self.assertEqual(state["controls"], BASE)
        self.assertTrue(any(e["event"] == "trial_window_closed" for e in events))
        state, _ = controller.transition(state, observation(T+14400+21599, 50, 10000, 5000), SETTINGS)
        self.assertIsNotNone(state["active"])

    def test_observed_gain_kept_after_maturity(self):
        state, _, _ = start(); state, _, _ = close_window(state)
        state, events = controller.transition(state, observation(T+36000, 60, 12000, 6000), SETTINGS)
        self.assertEqual(state["phase"], "kept")
        self.assertIsNone(state["active"])
        self.assertEqual(state["controls"]["quote_multipliers"], {"G1": .97})
        self.assertTrue(any(e["event"] == "keep" for e in events))

    def test_margin_deterioration_rolls_back(self):
        state, _, _ = start(); state, _, _ = close_window(state, amount=100)
        state, events = controller.transition(state, observation(T+36000, 60, 12000, 6000), SETTINGS)
        self.assertEqual(state["controls"], BASE)
        self.assertTrue(any(e.get("reason") == "no_gain_or_deterioration" for e in events))

    def test_no_sku_gain_does_not_pass_using_global_growth_alone(self):
        state, _, _ = start()
        identity = state["active"]["id"]
        after = [trade(i, T+7200+i, identity, 194) for i in range(4, 7)]
        state, _ = controller.transition(state, observation(T+14400, 24, 4800, 2400, own_sales=after), SETTINGS)
        state, events = controller.transition(state, observation(T+36000, 60, 12000, 6000), SETTINGS)
        self.assertEqual(state["controls"], BASE)
        self.assertEqual(state["phase"], "cooldown")

    def test_unlabeled_or_different_grade_sales_do_not_prove_trial_effect(self):
        state, _, _ = start()
        wrong = [trade(i, T+7200+i, None) for i in range(4, 8)]
        state, _ = controller.transition(state, observation(T+14400, 24, 4800, 2400, own_sales=wrong), SETTINGS)
        state, events = controller.transition(state, observation(T+36000, 60, 12000, 6000), SETTINGS)
        self.assertTrue(any(e.get("reason") == "inconclusive" for e in events))

    def test_cash_and_rating_guards_rollback_before_normal_window(self):
        state, _, _ = start()
        cash, _ = controller.transition(state, observation(T+7300, 11, 2200, 1100, balance=900000), SETTINGS)
        self.assertIsNone(cash["active"])
        self.assertTrue(cash["controls"]["restock_paused"])
        stars, events = controller.transition(state, observation(T+7300, 16, 3200, 1600, stars=4.7), SETTINGS)
        self.assertEqual(stars["controls"], BASE)
        self.assertTrue(any(e.get("reason") == "rating_guard" for e in events))

    def test_early_operating_loss_guard(self):
        state, _, _ = start()
        state, events = controller.transition(state, observation(T+14400, 24, 4800, 800), SETTINGS)
        self.assertTrue(any(e.get("reason") == "early_performance_guard" for e in events))
        self.assertIsNone(state["active"])

    def test_stale_data_operator_disable_and_counter_reset(self):
        state, _, _ = start()
        stale, events = controller.transition(state, {"at": T+7300, "valid": False}, SETTINGS)
        self.assertEqual(stale["controls"], BASE)
        self.assertIsNone(stale["active"])
        disabled, _ = controller.transition(state, {"at": T+7300}, {**SETTINGS, "enabled": False})
        self.assertEqual(disabled["phase"], "disabled")
        reset, events = controller.transition(state, observation(T+7300, 1, 100, 100), SETTINGS)
        self.assertTrue(any(e["event"] == "baseline_reset" for e in events))
        self.assertIsNone(reset["active"])

    def test_refund_blocks_keep_and_timeout_restores(self):
        state, _, _ = start(); state, _, _ = close_window(state)
        final, _ = controller.transition(state, observation(T+36000, 60, 12000, 6000, refunded_units=["u4"]), SETTINGS)
        self.assertEqual(final["controls"], BASE)
        timed, events = controller.transition(state, observation(T+7200+43200, 60, 12000, 6000), SETTINGS)
        self.assertTrue(any(e.get("reason") == "trial_timeout_or_insufficient_evidence" for e in events))

    def test_missing_price_consumer_and_daily_limit_block_trials(self):
        state, _ = controller.transition(None, observation(), SETTINGS)
        obs = observation(T+7200, 10, 2000, 1000, own_sales=[trade(i, T+i) for i in range(1, 4)], price_consumer_ready=False)
        missing, _ = controller.transition(state, obs, SETTINGS)
        self.assertIsNone(missing["active"])
        state.update(day=datetime.fromtimestamp(T+7200, timezone.utc).date().isoformat(),
                     experiments_today=SETTINGS["max_daily_experiments"])
        limited, _ = controller.transition(state, {**obs, "price_consumer_ready": True}, SETTINGS)
        self.assertEqual(limited["phase"], "daily_trial_limit")

    def test_expanded_daily_quota_allows_third_but_blocks_seventh_trial(self):
        state, _ = controller.transition(None, observation(), SETTINGS)
        state.update(day=datetime.fromtimestamp(T+7200, timezone.utc).date().isoformat(), experiments_today=2)
        obs = observation(T+7200, 10, 2000, 1000, own_sales=[trade(i, T+i) for i in range(1, 4)])
        third, _ = controller.transition(state, obs, SETTINGS)
        self.assertEqual(third["phase"], "testing")
        self.assertEqual(third["experiments_today"], 3)
        state["experiments_today"] = 6
        seventh, _ = controller.transition(state, obs, SETTINGS)
        self.assertEqual(seventh["phase"], "daily_trial_limit")
        self.assertIsNone(seventh["active"])

    def test_config_rejects_excessive_or_non_integer_daily_quota(self):
        for limit in (7, 6.5, True):
            with self.subTest(limit=limit), patch.object(controller, "read", return_value={**SETTINGS, "max_daily_experiments": limit}):
                with self.assertRaises(ValueError):
                    controller.config()

    def test_restock_throttle_is_one_percentage_point(self):
        choices = controller.choose(observation(price_candidates=[], aged_units=3, stock_cost=2000000), BASE, SETTINGS)
        self.assertEqual(choices[0]["candidate"]["restock_budget_rate"], .04)


class RuntimeAndPersistence(unittest.TestCase):
    def test_rating_guard_bypasses_normal_thirty_minute_interval(self):
        state, _, _ = start()
        state["checked_at"] = T+7200
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            write(md/"strategy-state.json", state)
            bad = observation(T+7260, 16, 3200, 1600, stars=4.7)
            with patch.object(controller, "observation", return_value=bad):
                result = controller.run(md, now=T+7260)
            self.assertIn("rollback", result["events"])
            self.assertEqual(effective(md, T+7261)["controls"], BASE)

    def test_operator_disable_restores_base_even_when_market_files_are_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(controller, "config", return_value={**SETTINGS, "enabled": False}), \
                patch.object(controller, "observation", side_effect=ValueError("unreadable")) as observe:
            md = Path(tmp)/".market"
            self.assertEqual(controller.run(md, now=T, force=True)["status"], "disabled")
            self.assertEqual(effective(md, T+1)["controls"], BASE)
            observe.assert_not_called()

    def test_ambiguous_receipts_are_not_attributed_to_the_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            now = time.time()
            write(md/"inventory.json", {"round": 3, "balance": 5000000, "units": []})
            log = Path(tmp)/"logs/market-kit.log"
            log.parent.mkdir(parents=True)
            clock = datetime.fromtimestamp(now, KST).strftime("%H:%M:%S")
            log.write_text(f"{clock} [market] shop registered as kcwarni, round 3\n"
                           f"{clock} [acp] settled fixture at 100, ledger #1, unit u grade A\n", encoding="utf8")
            os.utime(log, (now, now))
            write(md/"market-intel.json", {"round": 3, "observed_at": now,
                  "competition": {"own": {"shops": {"alive": True, "deals": 10, "visits": 20,
                   "revenue": 2000, "stars": 5, "reviews": 10}}},
                  "rows": [{"round": 3, "kind": "settle", "seq": 1, "ts": now,
                            "data": {"unit": "u", "item": "G1", "grade": "A", "amount": 100}}]})
            for i, identity in enumerate((None, "trial")):
                write(md/"signed-offers"/f"{i}.json", {"unit": "u", "sku": "G1", "cost": 80, "price": 100,
                      "issued_at": now-10, "strategy_experiment": identity})
            result = controller.observation(md, now)
            self.assertEqual(result["own_sales"][0]["receipt_attribution"], "ambiguous_or_missing")
            self.assertIsNone(result["own_sales"][0]["experiment"])

    def test_unknown_keys_and_unbounded_values_are_rejected(self):
        for controls in ({**BASE, "grades": ["C"]}, {**BASE, "restock_budget_rate": .5},
                         {**BASE, "quote_multipliers": {"G1": .5}}):
            with self.assertRaises(ValueError):
                validate(controls)
        wrong_round = controller.initial(); wrong_round["round"] = 2
        with self.assertRaises(ValueError):
            controller.transition(wrong_round, observation(), SETTINGS)

    def test_lease_and_hard_floor_food_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp)/"strategy-policy.json", {"schema": 1, "round": 3, "version": 1,
                  "published_at": 100, "valid_until": 200, "controls": {**BASE, "quote_multipliers": {"G1": .94}}})
            runtime = effective(tmp, 150)
            u = {"unit": "u", "sku": "G1", "kind": "goods", "grade": "A", "cost": 100,
                 "list_price": 106, "supplied_at": 100}
            terms = selling_terms(u, now=150, runtime=runtime)
            self.assertGreaterEqual(terms["quote"], terms["floor"])
            self.assertEqual(effective(tmp, 200)["controls"], BASE)
            food = {**u, "kind": "food", "expires_at": 10000}
            self.assertEqual(selling_terms(food, now=150, runtime=runtime)["quote"], 106)

    def test_budget_controls_affect_real_restock_plan(self):
        inv = {"round": 3, "balance": 5000000, "units": []}
        cat = {sku: {"kind": "goods", "category": "g", "list_price": 100000} for sku in ("A", "B")}
        runtime = {"controls": {**BASE, "restock_budget_rate": .02}, "version": 1}
        self.assertEqual(len(plan(inv, cat, [{"category": "g"}], {}, runtime=runtime)[0]), 1)
        runtime["controls"]["restock_paused"] = True
        self.assertEqual(plan(inv, cat, [{"category": "g"}], {}, runtime=runtime)[0], [])

    def test_file_state_resumes_trial_and_publishes_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            with patch.object(controller, "observation", return_value=observation()):
                controller.run(md, now=T, force=True)
            before = [trade(i, T+i) for i in range(1, 4)]
            with patch.object(controller, "observation", return_value=observation(T+7200, 10, 2000, 1000, own_sales=before)):
                controller.run(md, now=T+7200, force=True)
            self.assertEqual(effective(md, T+7201)["controls"]["quote_multipliers"], {"G1": .97})
            with patch.object(controller, "observation", return_value=observation(T+7300, 11, 2200, 1100, balance=900000)):
                controller.run(md, now=T+7300, force=True)
            self.assertTrue(effective(md, T+7301)["controls"]["restock_paused"])
            self.assertIsNone(read(md/"strategy-state.json")["active"])
            self.assertEqual(len(list((Path(tmp)/"logs/strategy-controller").glob("*.json"))), 3)

    def test_writer_lock_is_exclusive_and_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"lock"
            first = controller.acquire(path)
            self.assertIsNotNone(first)
            self.assertIsNone(controller.acquire(path))
            controller.release(first)
            second = controller.acquire(path)
            self.assertIsNotNone(second)
            controller.release(second)


class ActualSellerConsumer(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_quote_and_signed_offer_carry_experiment_identity(self):
        import seller_r3
        from market_state import task_file
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); md = root/".market"
            now = time.time()
            unit = {"unit": "u", "sku": "G1", "name": "fixture", "category": "g", "grade": "A",
                    "cost": 100, "kind": "goods", "list_price": 200, "supplied_at": now, "expires_at": None}
            write(md/"inventory.json", {"round": 3, "units": [unit]})
            write(md/"catalog.json", {"G1": {"kind": "goods", "list_price": 200}})
            write(md/"strategy-policy.json", {"schema": 1, "round": 3, "published_at": now-1,
                  "valid_until": now+3600, "version": 42, "experiment": "trial",
                  "controls": {**BASE, "quote_multipliers": {"G1": .97}}})
            updater = MagicMock()
            updater.requires_input = AsyncMock(); updater.complete = AsyncMock(); updater.add_artifact = AsyncMock()
            context = SimpleNamespace(current_task=SimpleNamespace(id="t", context_id="c"), message=None)
            seller = seller_r3.Seller()
            with patch.object(seller_r3, "MARKET_DIR", md), patch.object(seller_r3, "__file__", str(root/"seller_r3.py")), \
                    patch.object(seller_r3, "TaskUpdater", return_value=updater), \
                    patch.object(seller_r3, "get_message_text", side_effect=["buy G1", "194"]), \
                    patch.object(seller_r3, "ask_json", return_value=({"action": "counter", "price": 200}, "fixture")), \
                    patch.object(seller_r3, "sign", side_effect=lambda r: {**r, "sig_seller": "fixture"}):
                await seller.execute(context, MagicMock())
                self.assertEqual(seller.open["t"]["ask"], 194)
                await seller.execute(context, MagicMock())
            signed = read(task_file(md, "signed-offers", "t"))
            self.assertEqual(signed["price"], 194)
            self.assertEqual(signed["strategy_experiment"], "trial")
            self.assertEqual(signed["cost"], 100)
            self.assertEqual(signed["strategy_version"], 42)


if __name__ == "__main__":
    unittest.main()
