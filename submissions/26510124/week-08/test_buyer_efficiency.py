"""Price/quality constraints stay intact while repeat work is avoided."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from buyer_context import compact_dialogue
from buyer_policy import buying_terms, load_policy, negotiation_policy
from buyer_selection import offer_fingerprint, reuse_failures
from market_buyer import Buyer, last_asking_price, trade_and_record
from payment_memory import recent_jsonl
from test_buyer_selection import shop

ROOT = Path(__file__).resolve().parent


class FailureReuseTests(unittest.TestCase):
    def setUp(self):
        self.now = 10000
        self.config = load_policy()
        self.order = {"order": "o1", "round": 3, "kind": "goods"}
        self.catalog = {"G00001": {"list_price": 100000}, "G00002": {"list_price": 100000}}
        self.shops = [shop("failed"), shop("alternative")]
        self.terms = buying_terms(100000, product=self.catalog["G00001"], order=self.order, config=self.config)
        self.record = {"name": "failed", "item": "G00001", "round": 3, "result": "no_deal",
                       "time": datetime.fromtimestamp(self.now - 10, timezone.utc).isoformat(),
                       "selection_fingerprint": offer_fingerprint(self.shops[0], "G00001", self.terms, 3)}

    def apply(self, *, shops=None, records=None, budget=100000, config=None):
        return reuse_failures(self.shops if shops is None else shops,
                              [self.record] if records is None else records,
                              item="chair", budget=budget, config=config or self.config,
                              order=self.order, catalog=self.catalog, now=self.now)

    def test_recent_same_conditions_prefer_alternative_without_mutating_sources(self):
        before = deepcopy(self.shops)
        result, metrics = self.apply()
        self.assertEqual([s["name"] for s in result], ["alternative"])
        self.assertEqual(metrics["cooldown_skipped_offers"], 1)
        self.assertEqual(self.shops, before)

    def test_other_sku_at_same_shop_remains_eligible(self):
        self.shops[0]["offers"].append({"sku": "G00002", "qty": 1})
        result, _ = self.apply()
        self.assertEqual([o["sku"] for o in result[0]["offers"]], ["G00002"])

    def test_observable_stock_review_or_effective_price_changes_allow_retry(self):
        for field in ("stock", "review", "budget", "warranty"):
            with self.subTest(field=field):
                shops, config, budget = deepcopy(self.shops), dict(self.config), 100000
                if field == "stock":
                    shops[0]["offers"][0]["qty"] = 2
                elif field == "review":
                    shops[0]["reviews"] = [{"stars": 4, "review": "new evidence"}]
                elif field == "budget":
                    budget = 90000
                else:
                    config["goods_warranty_hours"] = 5
                result, _ = self.apply(shops=shops, config=config, budget=budget)
                self.assertEqual(len(result), 2)

    def test_changed_url_does_not_erase_same_shop_failure(self):
        self.shops[0]["url"] = "https://replacement-tunnel.invalid"
        result, _ = self.apply()
        self.assertEqual([s["name"] for s in result], ["alternative"])

    def test_expired_future_unknown_timestamps_and_other_round_do_not_block(self):
        for changes in (
            {"time": datetime.fromtimestamp(self.now - 901, timezone.utc).isoformat()},
            {"time": datetime.fromtimestamp(self.now + 10, timezone.utc).isoformat()},
            {"time": "not-a-time"}, {"time": "2026-11-03T00:00:00"}, {"round": 2},
            {"selection_fingerprint": None},
        ):
            with self.subTest(changes=changes):
                result, _ = self.apply(records=[{**self.record, **changes}])
                self.assertEqual(len(result), 2)

    def test_network_errors_pending_payments_and_latest_success_are_not_no_deals(self):
        for status in ("error", "receipt_issued", "payment_refused", "settled"):
            latest = {**self.record, "result": status,
                      "time": datetime.fromtimestamp(self.now - 1, timezone.utc).isoformat()}
            result, _ = self.apply(records=[self.record, latest])
            self.assertEqual(len(result), 2, status)

    def test_all_cooling_reconsiders_instead_of_locking_out_every_shop(self):
        result, metrics = self.apply(shops=self.shops[:1])
        self.assertEqual([s["name"] for s in result], ["failed"])
        self.assertTrue(metrics["all_cooling_reconsidered"])
        self.assertEqual(metrics["cooldown_skipped_offers"], 0)

    def test_disabling_cooldown_keeps_candidates(self):
        result, _ = self.apply(config={**self.config, "failure_cooldown_seconds": 0})
        self.assertEqual(len(result), 2)

    def test_runtime_records_comparison_data_and_next_order_reuses_it(self):
        fake = SimpleNamespace(run=lambda: "no_deal", no_deal_reason="Seller would not meet my price",
                               receipt=None, authorized_price=None, observed_receipt=None,
                               diagnostics=lambda: {"version": 1, "reason": "unknown"})
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            memory = Path(directory) / "memory.jsonl"
            with patch("market_buyer.Buyer", return_value=fake):
                trade_and_record(self.shops[0], item="G00001", budget=100000, memory_path=memory,
                                 reason="fixture", seconds=10, client=Mock(), order=self.order, policy=self.terms)
            records = recent_jsonl(memory)
        self.assertEqual(records[0]["selection_fingerprint"], self.record["selection_fingerprint"])
        result, _ = reuse_failures(self.shops, records, item="chair", budget=100000,
                                   config=self.config, order=self.order, catalog=self.catalog)
        self.assertEqual([s["name"] for s in result], ["alternative"])


class DialogueCompressionTests(unittest.TestCase):
    def test_short_quote_prompt_fits_small_context_budget(self):
        terms = buying_terms(100000, product={"list_price": 100000}, order={"round": 3, "kind": "goods"})
        history = [{"role": "buyer", "text": "I want G00001"}]
        for ask, bid in ((90000, 55000), (80000, 57000), (70000, 58000), (60000, 58000)):
            message = f"Grade A. Warranty 6 hours. Asking price: {ask}"
            history.append({"role": "seller", "text": message})
            context = {"item": "G00001", "budget": 100000, "purchase_policy": negotiation_policy(terms), "current_ask": ask,
                       **compact_dialogue(history, message, last_asking_price)}
            prompt = (ROOT / "prompts/buyer.txt").read_text() + "\n" + json.dumps(context)
            # Application input characters, not the CLI's full input or tokens.
            self.assertLess(len(prompt), 2100)
            history.append({"role": "buyer", "text": str(bid)})

    def test_price_trail_keeps_exact_bids_and_asks_without_duplicate_current_message(self):
        history = [{"role": "buyer", "text": "I want G00001"}]
        for ask, bid in ((90000, 55000), (80000, 57000), (70000, 58000)):
            history += [{"role": "seller", "text": f"Grade A. Warranty 6 hours. Asking price: {ask}"},
                        {"role": "buyer", "text": str(bid)}]
        current = "Grade A. Warranty 6 hours. Asking price: 59000"
        history.append({"role": "seller", "text": current})
        result = compact_dialogue(history, current, last_asking_price)
        self.assertEqual(result["negotiation"]["buyer_offers"], [55000, 57000, 58000])
        self.assertEqual(result["negotiation"]["seller_asks"], [90000, 80000, 70000, 59000])
        self.assertNotIn(current, [r["text"] for r in result["history"]])
        self.assertLessEqual(len(result["history"]), 2)
        self.assertIn("Warranty 6 hours", result["seller_message"])

    def test_long_messages_keep_head_and_final_quote_and_mark_omissions(self):
        text = "No warranty. " + "long explanation " * 1000 + " Asking price: 60000"
        history = [{"role": "seller", "text": text}, {"role": "buyer", "text": "50000"},
                   {"role": "seller", "text": text}]
        result = compact_dialogue(history, text, last_asking_price)
        self.assertLessEqual(len(result["seller_message"]), 1600)
        self.assertTrue(result["seller_message"].startswith("No warranty."))
        self.assertTrue(result["seller_message"].endswith("Asking price: 60000"))
        self.assertLessEqual(len(result["history"][0]["text"]), 240)
        self.assertTrue(result["negotiation"]["excerpts_omitted_text"])
        self.assertLess(len(json.dumps(result)), 2300)

    def test_ambiguous_price_is_preserved_as_unknown(self):
        text = "List price: 100000; asking price: 60000"
        result = compact_dialogue([{"role": "seller", "text": text}], text, last_asking_price)
        self.assertEqual(result["negotiation"]["seller_asks"], [None])

    def test_working_poll_messages_do_not_bloat_decision_history(self):
        def task(state, text):
            return {"status": {"state": state, "message": {"parts": [{"text": text}]}}}
        buyer = Buyer(Mock(), url="https://fixture.invalid", item="G00001", budget=100000)
        buyer.task_id = "t1"
        with patch.object(buyer, "discover"), patch.object(buyer, "send", return_value=task("TASK_STATE_WORKING", "waiting")), \
                patch.object(buyer, "rpc", side_effect=[task("TASK_STATE_WORKING", "waiting")] * 3 + [
                    task("TASK_STATE_INPUT_REQUIRED", "Asking price: 90000")]), \
                patch.object(buyer, "cancel"), patch("market_buyer.time.sleep"), \
                patch("market_buyer.buyer_decision", return_value={"action": "walk_away", "reason": "price"}) as model:
            self.assertEqual(buyer.run(), "no_deal")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(buyer.history, [{"role": "seller", "text": "Asking price: 90000"}])
