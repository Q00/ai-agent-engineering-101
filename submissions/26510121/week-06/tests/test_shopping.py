"""Simulated multi-shop transport: concurrency, replanning and uncertainty guards."""
import asyncio
import unittest

import httpx

from negotiation.cart_contracts import CartDecision
from negotiation.contracts import BuyerDecision
from negotiation.shopping import Shopper
from test_cart import MemoryLog


def shopping_line(category):
    return {"line_id": category, "item_id": category + "-white-wood", "category": category,
            "description": "White wooden " + category}


class FakeHarness:
    def __init__(self):
        self.plans = []

    async def cart(self, context, observation):
        self.plans.append((context, observation))
        return CartDecision(action="plan", assignments=[{"line_id": line["line_id"],
                             "shop_id": line["shops"][0]["shop_id"], "budget": 50}
                            for line in context.lines], reason="Simulated plan.", summary="Simulated remaining plan.")

    async def buyer(self, context, observation):
        await asyncio.sleep(.01)
        blocked = observation["agent_card_untrusted"]["name"].startswith("blocked")
        return BuyerDecision(action="walk_away" if blocked else "accept", item_id=context.item_id,
                             price=None if blocked else context.seller_ask, reason="Simulated decision.",
                             summary="Negotiating " + context.item_id)


class FakeNetwork:
    def __init__(self, shops, uncertain=False, wrong_selected=False):
        self.shops, self.uncertain = shops, uncertain
        self.wrong_selected = wrong_selected
        self.opens = []
        self.offers = []

    def client(self):
        return FakeClient(self)


class FakeClient:
    def __init__(self, network):
        self.network = network

    async def discover(self, url):
        self.shop_id = url.split("//", 1)[1]
        category = self.network.shops[self.shop_id]
        self.item = category + "-white-wood"
        self.task_id = "task-" + self.shop_id
        return {"name": self.shop_id, "skills": [{"id": "sell", "tags": [category],
                                                  "description": "Sells " + self.item}]}

    def task(self, state="TASK_STATE_INPUT_REQUIRED", price=None):
        task = {"id": self.task_id, "contextId": "context-" + self.shop_id, "status": {
            "state": state, "message": {"parts": [{"text": "Asking 40"},
                {"data": {"selected_item": {"item_id": "other-product" if self.network.wrong_selected else self.item}}}]}}}
        if price is not None:
            task["artifacts"] = [{"parts": [{"data": {"item": self.item, "price": price}}]}]
        return task

    async def send(self, text, task_id=None):
        if task_id is None:
            self.network.opens.append(self.shop_id)
            await asyncio.sleep(.01)
            return self.task()
        self.network.offers.append((self.shop_id, task_id, text))
        if self.network.uncertain:
            raise httpx.ReadTimeout("simulated response lost")
        return self.task("TASK_STATE_COMPLETED", int(text))

    async def get_task(self, task_id):
        return self.task()

    async def cancel(self, task_id):
        return self.task("TASK_STATE_CANCELED")

    async def close(self):
        pass


class ShoppingTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_skills_route_different_items_concurrently_without_overspending(self):
        categories = ("lamp", "chair", "bookshelf", "desk", "cabinet")
        network = FakeNetwork({category + str(i): category for category in categories for i in range(4)})
        directory = {"shops": [{"shop_id": key, "url": "http://" + key} for key in network.shops]}
        log, harness = MemoryLog(), FakeHarness()
        shopper = Shopper([shopping_line(c) for c in categories], 250, harness, log,
                          client_factory=network.client, concurrency=4)
        result = await shopper.run(directory)
        self.assertEqual((result["outcome"], result["spent"], result["held"]), ("purchased", 200, 0))
        self.assertEqual(len(network.opens), 5)
        self.assertEqual(len(set(network.opens)), 5)
        self.assertEqual(len(shopper.shops), 20)
        self.assertTrue(all(len(line["shops"]) == 4 for line in harness.plans[0][0].lines))
        peak = max(sum(line["state"] == "active" for line in e["ledger"]["lines"])
                   for e in log.events if "ledger" in e)
        self.assertEqual(peak, 4)
        for line in result["lines"]:
            self.assertEqual(line["summary"], "Negotiating " + line["item_id"])
            self.assertEqual(line["task_id"], "task-" + line["shop_id"])
            self.assertLessEqual(len(line["recent_turns"]), 4)

    async def test_confirmed_cancel_allows_model_to_select_a_different_shop(self):
        network = FakeNetwork({"blocked-first": "lamp", "second": "lamp"})
        harness = FakeHarness()
        shopper = Shopper([shopping_line("lamp")], 50, harness, MemoryLog(), client_factory=network.client)
        result = await shopper.run({"shops": [{"shop_id": key, "url": "http://" + key} for key in network.shops]})
        self.assertEqual(result["outcome"], "purchased")
        self.assertEqual(network.opens, ["blocked-first", "second"])
        self.assertEqual(len(harness.plans), 2)
        self.assertEqual(result["lines"][0]["attempted_shops"], ["blocked-first", "second"])

    async def test_lost_offer_with_unknown_outcome_does_not_start_another_shop(self):
        network = FakeNetwork({"first": "lamp", "second": "lamp"}, uncertain=True)
        harness = FakeHarness()
        shopper = Shopper([shopping_line("lamp")], 50, harness, MemoryLog(), client_factory=network.client)
        result = await shopper.run({"shops": [{"shop_id": key, "url": "http://" + key} for key in network.shops]})
        self.assertEqual((result["outcome"], result["spent"], result["held"]), ("incomplete", 0, 50))
        self.assertEqual(network.opens, ["first"])
        self.assertEqual(len(network.offers), 1)
        self.assertEqual(len(harness.plans), 1)
        self.assertEqual(result["lines"][0]["state"], "uncertain")

    async def test_wrong_selected_product_never_receives_a_binding_price_offer(self):
        network = FakeNetwork({"first": "lamp"}, wrong_selected=True)
        shopper = Shopper([shopping_line("lamp")], 50, FakeHarness(), MemoryLog(), client_factory=network.client)
        result = await shopper.run({"shops": [{"shop_id": "first", "url": "http://first"}]})
        self.assertEqual(result["outcome"], "incomplete")
        self.assertEqual(network.offers, [])
        self.assertEqual(result["spent"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
