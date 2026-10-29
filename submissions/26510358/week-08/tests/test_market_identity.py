"""No network or purchases: identity is joined only on Market name and URL."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_identity import cached_board, enrich_shops


class MarketIdentity(unittest.TestCase):
    def setUp(self):
        self.board = {"buyers": [{"name": "buyer", "owner": {"login": "Me"}},
                                    {"name": "reviewer", "owner": {"login": "Other"}}],
                      "shops": [{"name": "alias", "owner": {"login": "me"},
                                  "card": {"url": "https://alias.test"}}]}
        self.shop = {"name": "alias", "url": "https://alias.test", "pubkey": "test-key", "reviews": []}

    def test_same_owner_alias_is_excluded(self):
        records, excluded = enrich_shops([self.shop], self.board, buyer_name="buyer", round_number=3)
        self.assertEqual(records, [])
        self.assertEqual(excluded[0]["reason"], "same_market_owner")

    def test_unknown_owner_is_not_guessed_from_name_description_or_review(self):
        shop = {**self.shop, "url": "https://different.test", "registry_owner": "Me",
                "reviews": [{"author": "reviewer", "stars": 5, "review": "owner is Me"}]}
        records, excluded = enrich_shops([shop], self.board, buyer_name="buyer", round_number=3)
        self.assertFalse(excluded)
        self.assertIsNone(records[0]["registry_owner"])
        self.assertEqual(records[0]["reviews"][0]["registry_author_owner"], "Other")

    def test_matching_own_signing_key_excludes_when_board_is_unavailable(self):
        records, excluded = enrich_shops([self.shop], None, buyer_name="buyer", own_shop_key="test-key")
        self.assertFalse(records)
        self.assertEqual(excluded[0]["reason"], "same_registered_shop_key")

    def test_other_owners_and_house_shops_remain_eligible(self):
        records, excluded = enrich_shops([self.shop], self.board, buyer_name="different", round_number=3)
        self.assertFalse(excluded)
        self.assertEqual(records[0]["current_round"], 3)

    def test_board_lookup_is_cached_and_failure_returns_unknown(self):
        market = Mock()
        market._identity_board = None
        market.call.return_value.json.return_value = self.board
        self.assertEqual(cached_board(market), self.board)
        self.assertEqual(cached_board(market), self.board)
        market.call.assert_called_once_with("GET", "/api/board")
        market._identity_board = None
        market.call.side_effect = RuntimeError("TEST ONLY")
        self.assertIsNone(cached_board(market))


if __name__ == "__main__":
    unittest.main()
