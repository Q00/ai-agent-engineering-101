"""Buyer report plumbing must preserve reviews and the original kit behavior."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import buyer_policy
import market_runtime
import review_settled


class ForumRuntimeTests(unittest.TestCase):
    def test_real_review_hook_reaches_buyer_transport_once(self):
        deal = {"order": {"order": "o1", "round": 3, "kind": "goods", "budget": 987654},
                "receipt": {"task": "t1", "item": "G00001", "price": 62000, "shop": "fixture"},
                "settled": {"ledger": 17, "sku": "G00001", "grade": "A", "warranty_hours": 6,
                            "shop": "https://fixture.invalid", "value": 100000}}
        market = SimpleNamespace(token={"buyer": "private-token"},
                                 call=Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"id": 7})))
        kit = SimpleNamespace(outbox=Mock(), log=Mock())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "deal.json"
            path.write_text(json.dumps(deal))
            with patch.object(review_settled, "ROOT", root), \
                    patch.object(review_settled.sys, "argv", ["review", "--deal", str(path)]), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                review_settled.main()
                review_settled.main()  # Replayed hook must not create another post.
            market_runtime.install_buyer_forum(kit)
            kit.outbox(market, root / ".market")
            kit.outbox(market, root / ".market")
            market.call.assert_called_once()
            self.assertEqual(market.call.call_args.args, ("POST", "/api/forum", "buyer"))
            body = market.call.call_args.kwargs["json"]
            self.assertEqual(body["refs"], [17])
            self.assertIn("62,000원", body["text"])
            self.assertNotIn("987654", body["text"])
            self.assertNotIn("private-token", json.dumps(body))

    def test_runtime_drains_once_and_preserves_original_outbox(self):
        original = Mock(return_value="original-result")
        kit = SimpleNamespace(outbox=original, log=Mock())
        market, md = object(), Path("fixture/.market")
        with patch.object(market_runtime, "drain_buyer_forum", return_value={"status": "sent"}) as drain:
            market_runtime.install_buyer_forum(kit)
            market_runtime.install_buyer_forum(kit)
            self.assertEqual(kit.outbox(market, md), "original-result")
        original.assert_called_once_with(market, md)
        drain.assert_called_once_with(market, md)
        kit.log.assert_called_once_with("[buyer-forum] send=sent")

    def test_forum_error_does_not_stop_original_sync_or_expose_details(self):
        kit = SimpleNamespace(outbox=Mock(return_value=7), log=Mock())
        with patch.object(market_runtime, "drain_buyer_forum", side_effect=OSError("private-token")):
            market_runtime.install_buyer_forum(kit)
            self.assertEqual(kit.outbox(object(), Path("fixture")), 7)
        kit.log.assert_called_once_with("[buyer-forum] send_error=OSError")

    def test_original_error_does_not_starve_independent_buyer_spool(self):
        kit = SimpleNamespace(outbox=Mock(side_effect=OSError("original")), log=Mock())
        with patch.object(market_runtime, "drain_buyer_forum", return_value={"status": "empty"}) as drain:
            market_runtime.install_buyer_forum(kit)
            with self.assertRaisesRegex(OSError, "original"):
                kit.outbox(object(), Path("fixture"))
        drain.assert_called_once()
        kit.log.assert_not_called()

    def test_queue_failure_keeps_review_output_and_evidence(self):
        deal = {"order": {"order": "o1", "round": 3, "kind": "goods", "budget": 987654},
                "receipt": {"task": "t1", "item": "G00001", "price": 62000},
                "settled": {"ledger": 17, "sku": "G00001", "grade": "A",
                            "warranty_hours": 6, "shop": "https://fixture.invalid", "value": 100000}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "deal.json"
            path.write_text(json.dumps(deal))
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(review_settled, "ROOT", root), \
                    patch.object(review_settled.sys, "argv", ["review", "--deal", str(path)]), \
                    patch.object(review_settled, "queue_confirmed_deal", side_effect=OSError("private-token")), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                review_settled.main()
            output = json.loads(stdout.getvalue())
            self.assertIn("Ledger 17", output["review"])
            self.assertNotIn("987654", output["review"])
            self.assertNotIn("private-token", stderr.getvalue())
            evidence = json.loads((root / "logs/settled-deals.jsonl").read_text())
            self.assertEqual(evidence["settled"]["ledger"], 17)


class SelectionSettingsTests(unittest.TestCase):
    def test_invalid_selection_controls_fail_before_trading(self):
        baseline = buyer_policy.load_policy()
        for field, value in (("selection_timeout_seconds", True), ("selection_timeout_seconds", 0),
                             ("selection_timeout_seconds", 46), ("selection_cache_seconds", -1),
                             ("selection_cache_seconds", 3601), ("selection_cache_seconds", 1.5)):
            with self.subTest(field=field, value=value), \
                    patch.object(buyer_policy, "read_json", return_value={**baseline, field: value}):
                with self.assertRaises(ValueError):
                    buyer_policy.load_policy()

    def test_local_controls_never_enter_negotiation_context(self):
        policy = buyer_policy.load_policy()
        view = buyer_policy.negotiation_policy(policy)
        for field in ("selection_timeout_seconds", "selection_cache_seconds", "failure_cooldown_seconds",
                      "selection_max_candidates", "review_preferences"):
            self.assertNotIn(field, view)
        self.assertEqual(view["grades"], ["A", "B"])
        self.assertEqual(view["goods_warranty_hours"], 3)
        self.assertEqual(view["budget_cap_percent"], 90)
        self.assertEqual(view["list_price_cap_percent"], 85)


if __name__ == "__main__":
    unittest.main()
