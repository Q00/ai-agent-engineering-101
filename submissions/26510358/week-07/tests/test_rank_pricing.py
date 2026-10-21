"""TEST ONLY board observations and model outputs; no market registrations or trades."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from a2a.types import Task, TaskState, TaskStatus
import seller
from rank_pricing import RankPricing
from test_seller_cancel import ITEM, RecordingQueue, RecordingTrace, StubPolicy, request_context

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "data/price-policy.json").read_text())


def board(rank):
    shops = [{"name": "peer-" + str(i), "deals": 100 - i, "stars": 4} for i in range(1, 6)]
    shops.insert(rank - 1, {"name": "myeonggi", "deals": 100 - rank, "stars": 5})
    return {"round": 2, "current": 2, "shops": shops}


class RankPricingTests(unittest.TestCase):
    def test_enter_top_three_then_fall_back_to_fourth(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [0]; observed = [board(4)]
            control = RankPricing(POLICY, RecordingTrace(), state_path=Path(directory) / "state.json",
                                  fetch_board=lambda: observed[0], clock=lambda: clock[0])
            old_quote = control.refresh()
            self.assertEqual(old_quote["phase"], "acquisition")
            self.assertEqual(old_quote["opening_margin"]["lamp"], 0)
            observed[0] = board(3); clock[0] = 61
            recovery = control.refresh()
            self.assertEqual(recovery["phase"], "recovery")
            self.assertEqual(recovery["opening_margin"], {"lamp": 1, "chair": 3, "bookshelf": 5})
            self.assertFalse(recovery["accept_floor_bids"])
            self.assertEqual(old_quote["phase"], "acquisition", "An issued quote's policy must stay frozen")
            observed[0] = board(4); clock[0] = 122
            self.assertEqual(control.refresh()["phase"], "acquisition")

    def test_failed_missing_or_wrong_round_observation_preserves_saved_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            control = RankPricing(POLICY, RecordingTrace(), state_path=path, fetch_board=lambda: board(2))
            self.assertEqual(control.refresh()["phase"], "recovery")
            invalid = [None, {"round": 1, "current": 2, "shops": []},
                       {"round": 2, "current": 2, "shops": []}]
            for response in invalid:
                restored = RankPricing(POLICY, RecordingTrace(), state_path=path,
                                       fetch_board=lambda: response)
                self.assertEqual(restored.refresh()["phase"], "recovery")
            def unavailable():
                raise OSError("TEST ONLY unavailable board")
            restored = RankPricing(POLICY, RecordingTrace(), state_path=path, fetch_board=unavailable)
            self.assertEqual(restored.refresh()["phase"], "recovery")

    def test_poll_cache_and_deals_then_stars_sort_match_board(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []; clock = [0]
            tied = board(3)
            tied["shops"] = list(reversed(tied["shops"]))
            def fetch():
                calls.append(1); return tied
            control = RankPricing(POLICY, RecordingTrace(), state_path=Path(directory) / "state.json",
                                  fetch_board=fetch, clock=lambda: clock[0])
            self.assertEqual(control.refresh()["phase"], "recovery")
            control.refresh(); clock[0] = 59; control.refresh()
            self.assertEqual(len(calls), 1)
            clock[0] = 60; control.refresh()
            self.assertEqual(len(calls), 2)


class ExistingQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_task_keeps_its_phase_across_both_transitions(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(seller, "Trace", RecordingTrace), \
                patch.object(seller, "load_catalog", return_value=[deepcopy(ITEM)]), \
                patch.object(seller, "StrategyPolicy", StubPolicy), \
                patch.object(seller, "load_shop_key", return_value=Ed25519PrivateKey.generate()):
            shop = seller.Seller({"name": "TEST ONLY rank-cycle seller",
                                  "pricing_state": str(Path(directory) / "state.json")})
            observed = [board(4)]; clock = [0]; contexts = []
            shop.rank_pricing.fetch_board = lambda: observed[0]
            shop.rank_pricing.clock = lambda: clock[0]
            def model(trusted, **kwargs):
                contexts.append(deepcopy(trusted))
                bid = trusted["offer"]
                if trusted["opening"]:
                    action, price = "open", trusted["candidates"][0]["max_opening_ask"]
                elif bid >= trusted["last_ask"] or trusted["accept_floor_bids"] and bid >= 100:
                    action, price = "accept", bid
                else:
                    action, price = "counter", trusted["last_ask"]
                return kwargs["validator"]({"action": action, "price": price, "offer": bid,
                                            "item_id": "lamp", "reason": "TEST ONLY model fixture"})
            shop.harness.decide = model
            queue = RecordingQueue()
            def task(name):
                return Task(id=name, context_id=name, status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED))
            original, higher, later = task("original"), task("higher"), task("later")
            await shop.execute(request_context("lamp", task=original), queue)
            self.assertEqual(shop.open[original.id]["ask"], 100)
            observed[0] = board(3); clock[0] = 61
            await shop.execute(request_context("lamp", task=higher), queue)
            self.assertEqual(shop.open[higher.id]["ask"], 101)
            await shop.execute(request_context("100", task=original), queue)
            self.assertEqual(shop.terminal[original.id], "completed")
            self.assertEqual(contexts[-1]["pricing_phase"], "acquisition")
            observed[0] = board(4); clock[0] = 122
            await shop.execute(request_context("lamp", task=later), queue)
            self.assertEqual(shop.open[later.id]["ask"], 100)
            await shop.execute(request_context("100", task=higher), queue)
            self.assertEqual(shop.open[higher.id]["ask"], 101)
            self.assertEqual(contexts[-1]["pricing_phase"], "recovery")
            await shop.execute(request_context("101", task=higher), queue)
            self.assertEqual(shop.terminal[higher.id], "completed")


if __name__ == "__main__":
    unittest.main()
