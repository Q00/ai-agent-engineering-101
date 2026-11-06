"""The shop keeps negotiating without a model, and no path sells below the floor or skips the existing checks."""

import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import harness
import seller_r3
from harness import HarnessError

COST, LIST_PRICE = 100, 200


class Fixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.unit = {"unit": "u1", "sku": "G1", "name": "fixture", "kind": "goods", "grade": "A", "cost": COST, "list_price": LIST_PRICE,
                     "supplied_at": time.time(), "expires_at": None}
        self.inventory(self.unit)
        (self.root / "catalog.json").write_text(json.dumps({"G1": {"kind": "goods", "list_price": LIST_PRICE}}), encoding="utf8")
        self.seller = seller_r3.Seller()
        self.updater = MagicMock()
        for name in ("reject", "requires_input", "complete", "add_artifact", "failed"):
            setattr(self.updater, name, AsyncMock())
        self.updater.new_agent_message = lambda parts: parts
        printing = patch("builtins.print")
        printing.start()
        self.addCleanup(printing.stop)

    def inventory(self, *units):
        (self.root / "inventory.json").write_text(json.dumps({"round": 3, "balance": 5000000, "units": list(units)}), encoding="utf8")

    async def send(self, *texts, mode="rules", ask=None, allowed=(True, "ok")):
        context = SimpleNamespace(current_task=SimpleNamespace(id="t1", context_id="c1"), message=None)
        ask = ask or MagicMock(side_effect=AssertionError("model must not be called"))
        with patch.object(seller_r3, "MARKET_DIR", self.root), patch.object(seller_r3, "__file__", str(self.root / "seller_r3.py")), \
                patch.object(seller_r3, "TaskUpdater", return_value=self.updater), \
                patch.object(seller_r3, "get_message_text", side_effect=list(texts)), \
                patch.object(seller_r3, "decision_mode", return_value=mode), patch.object(seller_r3, "model_allowed", return_value=allowed), \
                patch.object(seller_r3, "ask_json", ask), patch("warranty_trial.assignment", return_value={"hours": 0, "floor": 0, "enrolled": False, "group": None}), \
                patch.object(seller_r3, "sign", side_effect=lambda r: {**r, "sig_seller": "fixture"}) as self.signer:
            for _ in texts:
                await self.seller.execute(context, MagicMock())
        return ask

    def last_ask(self):
        return self.seller.open["t1"]["ask"]

    def floor(self):
        return seller_r3.selling_terms(self.unit, {"G1": {"kind": "goods", "list_price": LIST_PRICE}})["floor"]


class RulesMode(Fixture):
    async def test_opening_and_numeric_offers_need_no_model(self):
        await self.send("buy G1", "120")
        self.assertEqual(self.updater.requires_input.await_count, 2)
        self.updater.failed.assert_not_awaited()
        self.assertLess(self.last_ask(), LIST_PRICE)
        self.assertGreaterEqual(self.last_ask(), self.floor())

    async def test_opening_is_the_quote_and_discloses_condition(self):
        await self.send("buy G1")
        self.assertEqual(self.last_ask(), LIST_PRICE)
        parts = self.updater.requires_input.await_args.args[0]
        self.assertIn("Inventory condition: A", str(parts))

    async def test_accepting_the_ask_signs_a_receipt_with_the_checks(self):
        await self.send("buy G1", str(LIST_PRICE))
        self.signer.assert_called_once()
        self.assertEqual(self.signer.call_args.args[0]["price"], LIST_PRICE)
        self.updater.complete.assert_awaited_once()
        self.assertEqual(json.loads(next((self.root / "signed-offers").glob("*.json")).read_text(encoding="utf8"))["price"], LIST_PRICE)

    async def test_close_offer_is_accepted_above_the_floor(self):
        await self.send("buy G1", str(LIST_PRICE - 2))
        self.assertEqual(self.signer.call_args.args[0]["price"], LIST_PRICE - 2)

    async def test_no_offer_below_the_floor_is_ever_signed(self):
        for offer in (1, 50, COST, self.floor() - 1):
            with self.subTest(offer=offer):
                self.seller = seller_r3.Seller()
                self.signer = None
                await self.send("buy G1", str(offer), str(offer), str(offer), str(offer))
                self.signer.assert_not_called()
                self.assertGreaterEqual(self.last_ask(), self.floor())

    async def test_free_text_gets_a_request_for_a_number_without_the_model(self):
        await self.send("buy G1", "could you do better if I add a warranty?")
        self.signer.assert_not_called()
        self.assertIn("unconditional numeric offer", str(self.updater.requires_input.await_args))

    async def test_expired_stock_is_still_refused(self):
        await self.send("buy G1")
        self.inventory({**self.unit, "expires_at": 1})
        await self.send("150")
        self.updater.reject.assert_awaited_once()
        self.signer.assert_not_called()

    async def test_committed_unit_is_still_refused(self):
        await self.send("buy G1")
        self.seller.committed_units.add("u1")
        await self.send(str(LIST_PRICE))
        self.updater.reject.assert_awaited_once()
        self.signer.assert_not_called()

    async def test_a_changed_condition_is_disclosed_again_before_any_sale(self):
        await self.send("buy G1")
        self.inventory({**self.unit, "grade": "B"})
        await self.send(str(LIST_PRICE))
        self.signer.assert_not_called()
        self.assertEqual(self.updater.requires_input.await_count, 2)


class ModelMode(Fixture):
    async def test_every_model_failure_keeps_the_negotiation_going(self):
        for name, failure in (("limit", HarnessError("You have hit your usage limit")), ("timeout", HarnessError("Harness exceeded 90 seconds")),
                              ("bad json", ValueError("Invalid R3 seller action")), ("anything", RuntimeError("boom")), ("os", OSError("gone"))):
            with self.subTest(name=name):
                self.seller = seller_r3.Seller()
                self.updater.failed.reset_mock()
                self.updater.requires_input.reset_mock()
                await self.send("buy G1", "120", mode="model", ask=MagicMock(side_effect=failure))
                self.updater.failed.assert_not_awaited()
                self.assertEqual(self.updater.requires_input.await_count, 2)
                self.assertGreaterEqual(self.last_ask(), self.floor())
                self.assertLess(self.last_ask(), LIST_PRICE)

    async def test_failure_at_opening_still_quotes(self):
        await self.send("buy G1", mode="model", ask=MagicMock(side_effect=HarnessError("limit")))
        self.assertEqual(self.last_ask(), LIST_PRICE)

    async def test_breaker_off_does_not_call_the_model(self):
        await self.send("buy G1", "120", mode="model", allowed=(False, "weekly_usage_high"))
        self.assertEqual(self.updater.requires_input.await_count, 2)

    async def test_a_working_model_is_used_when_selected(self):
        ask = MagicMock(return_value=({"action": "counter", "price": 180}, "fixture"))
        await self.send("buy G1", mode="model", ask=ask)
        ask.assert_called_once()
        self.assertEqual(self.last_ask(), 180)

    async def test_the_floor_still_guards_a_model_answer(self):
        for move in ({"action": "accept"}, {"action": "counter", "price": 1}):
            with self.subTest(move=move):
                self.seller = seller_r3.Seller()
                await self.send("buy G1", "50", mode="model", ask=MagicMock(return_value=(move, "fixture")))
                self.signer.assert_not_called()
                self.assertGreaterEqual(self.last_ask(), self.floor())


class HarnessBreaker(unittest.TestCase):
    def run_with(self, *, returncode=0, stderr="", stdout='{"action": "offer", "price": 1, "reason": "x"}', prompt="prompt mentions quota and rate limit"):
        class Process:
            pid = 1

            def __init__(self, command, stdout, stderr, **_):
                self.returncode = returncode
                stdout.write(self.out)
                stderr.write(self.err)

            def communicate(self, _input=None, timeout=None):
                return None

            def poll(self):
                return self.returncode
        Process.out, Process.err = stdout.encode(), (f"user\n{prompt}\ncodex\n{stderr}").encode()
        with tempfile.TemporaryDirectory() as directory, patch.object(harness, "ROOT", Path(directory)), \
                patch.object(harness.shutil, "which", return_value="codex.exe"), patch.object(harness.subprocess, "Popen", side_effect=Process), \
                patch.object(harness.model_breaker, "trip") as trip, patch("sys.stderr"):
            try:
                result = harness.run_harness("codex", prompt)
            except HarnessError as error:
                result = error
            return result, trip

    def test_limit_failure_opens_the_breaker(self):
        for text in ("ERROR: You have hit your usage limit", "unexpected status 429 Too Many Requests"):
            with self.subTest(text=text):
                result, trip = self.run_with(returncode=1, stderr=text, stdout="")
                self.assertIsInstance(result, HarnessError)
                trip.assert_called_once()

    def test_prompt_words_and_other_failures_do_not(self):
        for options in ({}, {"returncode": 1, "stderr": "some other failure", "stdout": ""}, {"stdout": "not json"}):
            with self.subTest(options=options):
                _, trip = self.run_with(**options)
                trip.assert_not_called()

    def test_an_open_breaker_makes_no_process(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(harness, "ROOT", Path(directory)), \
                patch.object(harness.model_breaker, "is_tripped", return_value=True), patch.object(harness.subprocess, "Popen") as popen, patch("sys.stderr"):
            with self.assertRaises(HarnessError):
                harness.run_harness("codex", "prompt")
        popen.assert_not_called()


def setUpModule():
    # These tests fix their own prices: the shipped price cap setting must not move them.
    import price_cap
    global _cap_config
    _cap_config = price_cap.CONFIG
    price_cap.CONFIG = price_cap.CONFIG.with_name("no-such-price-cap.json")


def tearDownModule():
    import price_cap
    price_cap.CONFIG = _cap_config


if __name__ == "__main__":
    unittest.main()
