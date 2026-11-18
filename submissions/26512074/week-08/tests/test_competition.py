import base64
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from competition import Competition, TERMS


class CompetitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = Competition(Path(self.temp.name) / "game.sqlite3")

    def agree(self, name):
        key = Ed25519PrivateKey.generate()
        pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        agreement = {"partner": name, "beneficiary": "totato2203", "pubkey": pub,
                     "expires": time.time() + 3600, "terms": TERMS, "nonce": name}
        signature = base64.b64encode(key.sign(json.dumps(agreement, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())).decode()
        self.game.accept(agreement, signature, pub)
        return agreement, signature, pub

    def test_unsigned_or_changed_commitment_cannot_enable_spending(self):
        self.assertEqual(self.game.allowance("peer"), 0)
        agreement, signature, pub = self.agree("peer")
        agreement["beneficiary"] = "other"
        with self.assertRaises(ValueError):
            self.game.accept(agreement, signature, pub)

    def test_nonreciprocating_partner_is_cut_off_after_three_units(self):
        self.agree("peer")
        for i in range(3):
            self.assertTrue(self.game.reserve_extra(str(i), "peer", 1))
            self.game.resolve_extra(str(i), True)
        self.assertEqual(self.game.allowance("peer"), 0)
        self.agree("peer")  # Renewing the agreement cannot reset the loss cap.
        self.assertFalse(self.game.reserve_extra("renewal", "peer", 1))

    def test_only_real_round3_return_shape_earns_credit_and_replay_does_not(self):
        self.agree("peer")
        for i in range(3):
            self.game.reserve_extra(str(i), "peer", 1)
        entry = {"id": 7, "kind": "settle", "data": {"round": 2, "shop": "totato2203", "buyer": "peer", "item": "lamp", "amount": 110}}
        self.assertFalse(self.game.record_public_return(entry))
        self.assertEqual(self.game.allowance("peer"), 0)
        entry["data"]["round"] = 3
        self.assertTrue(self.game.record_public_return(entry))
        self.game.record_public_return(entry)
        with self.game.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM returns").fetchone()[0], 1)
        self.assertEqual(self.game.allowance("peer"), 1)

    def test_global_cap_blocks_many_partner_identities(self):
        for partner in range(10):
            name = f"peer-{partner}"
            self.agree(name)
            for i in range(3):
                self.assertTrue(self.game.reserve_extra(f"{name}-{i}", name, 1))
        self.agree("eleventh")
        self.assertFalse(self.game.reserve_extra("31", "eleventh", 1))

    def test_price_policy_does_not_give_away_floor_on_first_offer(self):
        base = {"opening_markup": 15, "minimum_markup": 0, "concession_step": 5, "floor_after": 3, "max_rounds": 5}
        quote = self.game.quote("task", "lamp", base)
        self.assertGreater(quote["minimum_markup"], 0)
        self.assertGreaterEqual(quote["opening_markup"], quote["minimum_markup"])
        self.game.settle_quote("task", 115)
        with self.game.connect() as db:
            self.assertEqual(db.execute("SELECT profit,settled FROM quotes").fetchone()[0], 15)


if __name__ == "__main__":
    unittest.main()
