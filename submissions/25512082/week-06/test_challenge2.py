"""Model-free tests for Challenge 2 multi-shop planning and state."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import challenge2_buyer as challenge2
import challenge2_seller
from buyer import BuyerOutcome


class DirectoryTests(unittest.TestCase):
    def test_example_directory_has_twenty_unique_shops(self):
        path = Path(__file__).with_name("challenge2_directory.example.json")
        urls = challenge2.read_directory(path)
        self.assertEqual(len(urls), 20)
        self.assertEqual(len(set(urls)), 20)

    def test_reads_twenty_agent_cards_and_narrows_candidates(self):
        urls = tuple(f"http://shop-{index}.test" for index in range(20))

        def respond(request):
            index = int(request.url.host.split("-")[1].split(".")[0])
            item = ("lamp", "chair", "bookshelf")[index % 3]
            return httpx.Response(200, json={
                "name": f"Shop {index}",
                "skills": [{"name": f"Sell {item}", "description": f"Negotiates {item}",
                            "tags": [item]}],
            })

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            cards = challenge2.fetch_cards(urls, client)
        self.assertEqual(len(cards), 20)
        candidates = challenge2.candidates_for("lamp", cards)
        self.assertEqual(len(candidates), challenge2.MAX_SHOP_CANDIDATES)
        self.assertTrue(all("lamp" in card.skills_text for card in candidates))

    def test_shop_model_sees_only_candidates_and_cannot_invent_url(self):
        cards = tuple(
            challenge2.ShopCard(f"http://shop-{index}.test", f"Shop {index}", "lamp lighting")
            for index in range(8)
        )
        candidates = challenge2.candidates_for("lamp", cards)
        with patch("challenge2_buyer.run_decision", return_value={
            "selected_url": candidates[0].url, "reason": "match",
        }) as run:
            selected = challenge2.decide_shop("lamp", 120, candidates)
        self.assertEqual(selected, candidates[0].url)
        prompt = run.call_args.args[0]
        self.assertEqual(prompt.count('"url"'), challenge2.MAX_SHOP_CANDIDATES)
        self.assertNotIn("shop-7.test", prompt)

        with patch("challenge2_buyer.run_decision", return_value={
            "selected_url": "http://invented.test", "reason": "invalid",
        }):
            with self.assertRaisesRegex(challenge2.Challenge2Error, "outside"):
                challenge2.decide_shop("lamp", 120, candidates)

    def test_real_shop_config_has_twenty_distinct_cards_and_exact_matches_rank_first(self):
        path = Path(__file__).with_name("challenge2_shops.json")
        raw = json.loads(path.read_text(encoding="utf-8"))["shops"]
        self.assertEqual(len(raw), 20)
        self.assertEqual(len({shop["port"] for shop in raw}), 20)
        cards = tuple(
            challenge2.ShopCard(
                f"http://127.0.0.1:{shop['port']}", shop["name"],
                " ".join(shop["inventory"]).lower(),
            )
            for shop in raw
        )
        candidates = challenge2.candidates_for("desk lamp", cards)
        self.assertIn("desk lamp", candidates[0].skills_text)
        for shop in raw:
            loaded = challenge2_seller.load_shop(path, shop["port"])
            card = challenge2_seller.build_card(loaded)
            self.assertEqual(card.name, shop["name"])


class MultiShopTests(unittest.TestCase):
    def setUp(self):
        self.cards = (
            challenge2.ShopCard("http://lamp.test", "Lamp Shop", "lamp lighting"),
            challenge2.ShopCard("http://chair.test", "Chair Shop", "chair seating"),
            challenge2.ShopCard("http://books.test", "Book Shop", "bookshelf books storage"),
        )

    def test_parallel_shopping_buys_every_item_once_within_total_budget(self):
        barrier = threading.Barrier(3, timeout=2)
        created = []

        class FakeBuyer:
            def __init__(self, url):
                self.url = url
                self.history = []
                created.append(self)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def buy(self, item, budget):
                self.history.append((item, budget, f"task-{item}"))
                barrier.wait()
                return BuyerOutcome("deal", f"task-{item}", budget - 10)

        chosen = {"lamp": "http://lamp.test", "chair": "http://chair.test",
                  "bookshelf": "http://books.test"}
        buyer = challenge2.MultiShopBuyer(
            self.cards, buyer_factory=FakeBuyer,
            shop_decider=lambda item, budget, candidates: chosen[item],
        )
        result = buyer.shop(("lamp", "chair", "bookshelf"), 900)
        self.assertTrue(result.success)
        self.assertEqual(result.total_spent, 870)
        self.assertEqual([p.item for p in result.purchases], ["lamp", "chair", "bookshelf"])
        self.assertEqual(len({p.task_id for p in result.purchases}), 3)
        self.assertEqual(len(created), 3)
        self.assertTrue(all(len(client.history) == 1 for client in created))

    def test_duplicate_items_are_rejected_before_planning(self):
        buyer = challenge2.MultiShopBuyer(self.cards)
        with self.assertRaisesRegex(challenge2.Challenge2Error, "unique"):
            buyer.shop(("lamp", "lamp"), 300)

    def test_over_allocation_receipt_cannot_produce_success(self):
        class FakeBuyer:
            def __init__(self, url): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def buy(self, item, budget): return BuyerOutcome("deal", "task", budget + 1)

        buyer = challenge2.MultiShopBuyer(
            self.cards, buyer_factory=FakeBuyer,
            shop_decider=lambda item, budget, candidates: candidates[0].url,
        )
        result = buyer.shop(("lamp",), 120)
        self.assertFalse(result.success)
        self.assertEqual(result.total_spent, 121)

    def test_non_deal_leaves_item_unbought_and_fails_whole_list(self):
        class FakeBuyer:
            def __init__(self, url): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def buy(self, item, budget): return BuyerOutcome("walked_away", f"task-{item}")

        buyer = challenge2.MultiShopBuyer(
            self.cards, buyer_factory=FakeBuyer,
            shop_decider=lambda item, budget, candidates: candidates[0].url,
        )
        result = buyer.shop(("chair",), 200)
        self.assertFalse(result.success)
        self.assertEqual(result.total_spent, 0)


if __name__ == "__main__":
    unittest.main()
