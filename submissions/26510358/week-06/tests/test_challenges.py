"""Challenge validation regressions; all counterpart results here are TEST ONLY.

No actual harness or HTTP seller is called. Temporary fixtures never become
submission challenge evidence. These tests protect the scope of real runs.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import load_catalog, search_catalog
from policy import StrategyPolicy
from run_challenges import LocalShops, run_catalog_challenge


class ChallengeScopeTests(unittest.TestCase):
    @staticmethod
    def fixture(root: Path, events: list[dict]):
        shops = LocalShops(root)
        # Only startup-evidence reading is exercised; these are not processes.
        shops.processes = [object()]
        shops.configs = [{"config": {"name": "test-only-shop"}}]
        path = root / "shop-01/trace/seller-test-only.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        return shops

    def test_effective_301_items_cannot_pass_using_3003_committed_items(self):
        product = search_catalog(
            "wooden shelf for 40 books, not taller than 85cm", load_catalog()
        )[0]
        fake_result = {"status": "completed", "receipt": {"item": product["id"], "price": product["reserve"]}}
        for effective_count, expected_pass in ((301, False), (3003, True)):
            with self.subTest(effective_count=effective_count), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shops = self.fixture(root / "shops", [
                    {"event": "seller_started", "name": "test-only-shop", "catalog_size": effective_count}
                ])
                actual_count = shops.active_catalog_size(0)
                with patch("run_challenges.negotiate", return_value=fake_result) as counterpart, contextlib.redirect_stdout(io.StringIO()):
                    summary = run_catalog_challenge(
                        "http://test-only.invalid", root / "challenge", StrategyPolicy(), 240,
                        server_catalog_size=actual_count,
                    )
                counterpart.assert_called_once()
                self.assertEqual(summary["passed"], expected_pass)
                self.assertEqual(summary["checks"]["catalog_has_thousands"], expected_pass)
                self.assertEqual(summary["catalog_size"], effective_count)
                self.assertEqual(summary["committed_catalog_size"], 3003)
                self.assertTrue(all(value for key, value in summary["checks"].items() if key != "catalog_has_thousands"))

    def test_duplicate_startup_evidence_is_rejected(self):
        event = {"event": "seller_started", "name": "test-only-shop", "catalog_size": 3003}
        with tempfile.TemporaryDirectory() as temporary:
            shops = self.fixture(Path(temporary), [event, event])
            with self.assertRaisesRegex(RuntimeError, "one actual seller_started"):
                shops.active_catalog_size(0)

    def test_startup_evidence_must_identify_the_requested_shop(self):
        with tempfile.TemporaryDirectory() as temporary:
            shops = self.fixture(Path(temporary), [
                {"event": "seller_started", "name": "different-shop", "catalog_size": 3003}
            ])
            with self.assertRaisesRegex(RuntimeError, "identify a valid effective catalog"):
                shops.active_catalog_size(0)


if __name__ == "__main__":
    unittest.main()
