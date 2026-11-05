"""Synthetic buyer forum reports; all HTTP is mocked and no LLM is invoked."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

import buyer_forum as forum
from receipt_leases import durable_json

ROOT = Path(__file__).resolve().parent
NOW = 1793898000.0


class FakeMarket:
    def __init__(self):
        self.token = {"buyer": "private-buyer-token", "shop": "private-shop-token"}
        self.calls = []
        self.response = httpx.Response(200, json={"id": 77})
        self.callback = None

    def call(self, method, path, role="buyer", **kwargs):
        self.calls.append((method, path, role, kwargs))
        if self.callback:
            self.callback()
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class BuyerForumTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = self.root / ".market"
        self.market = FakeMarket()
        self.assessment = {"reviewed_at": NOW, "stars": 4,
                           "review": "NEVER-PUBLISH-RAW-REVIEW", "reasons": ["NEVER-PUBLISH-RAW-REASON"]}
        self.deal = {"order": {"order": "private-order", "round": 3, "kind": "goods", "budget": 987654,
                               "mandates": {"secret": "NEVER-PUBLISH-MANDATE"}},
                     "receipt": {"shop": "fixture-shop", "item": "G12345678", "price": 70000,
                                 "task": "private-task", "sig_seller": "NEVER-PUBLISH-SIGNATURE"},
                     "settled": {"ledger": 100, "sku": "G12345678", "grade": "A", "warranty_hours": 6,
                                 "shop": "https://fixture.invalid", "name": "NEVER-PUBLISH-RAW-NAME",
                                 "claim": "NEVER-PUBLISH-CLAIM", "value": 100000}}

    def queue(self, ledger=100, *, now=NOW, price=None, sku=None):
        deal = deepcopy(self.deal)
        deal["settled"]["ledger"] = ledger
        if price is not None:
            deal["receipt"]["price"] = price
        if sku is not None:
            deal["settled"]["sku"] = deal["receipt"]["item"] = sku
        return forum.queue_confirmed_deal(self.root, deal, {**self.assessment, "reviewed_at": now}, now=now)

    def state(self):
        return forum.Spool(self.md).load()

    def drain(self, now=NOW):
        return forum.drain_buyer_forum(self.market, self.md, now=now)

    def test_reports_only_public_verified_terms_and_buyer_role(self):
        self.assertEqual(self.queue()["status"], "queued")
        self.assertEqual(self.drain()["status"], "sent")
        method, path, role, kwargs = self.market.calls[0]
        self.assertEqual((method, path, role), ("POST", "/api/forum", "buyer"))
        self.assertEqual(kwargs["timeout"], 5)
        body = kwargs["json"]
        self.assertEqual(body["refs"], [100])
        self.assertEqual(set(body), {"text", "refs", "thread"})
        self.assertIn("70,000원", body["text"])
        self.assertIn("정산 등급 A", body["text"])
        self.assertIn("보증 6시간", body["text"])
        self.assertIn("미래 품질을 보장하지", body["text"])
        raw = (self.md / "buyer-forum-spool.json").read_text() + json.dumps(body)
        for secret in ("NEVER-PUBLISH", "987654", "private-order", "private-task", "fixture.invalid", "private-buyer-token"):
            self.assertNotIn(secret, raw)
        self.assertFalse((self.md / "outbox.jsonl").exists())

    def test_unconfirmed_mismatched_or_stale_evidence_never_queues(self):
        cases = [lambda d: d.update(settled=None),
                 lambda d: d["settled"].update(ok=False),
                 lambda d: d["settled"].update(ok="true"),
                 lambda d: d["settled"].update(ledger=True),
                 lambda d: d["settled"].update(ledger=0),
                 lambda d: d["settled"].update(sku="OTHER"),
                 lambda d: d["settled"].update(grade="<script>"),
                 lambda d: d["receipt"].update(price=True),
                 lambda d: d["receipt"].update(price=-1),
                 lambda d: d["settled"].update(amount=1),
                 lambda d: d["settled"].update(order="another-order"),
                 lambda d: d["settled"].update(shop_name="another-shop"),
                 lambda d: d["order"].pop("round"),
                 lambda d: d["order"].update(round=True),
                 lambda d: d["settled"].update(round=4),
                 lambda d: d["settled"].update(ts=NOW - 21601)]
        for change in cases:
            deal = deepcopy(self.deal)
            change(deal)
            with self.subTest(change=change):
                self.assertEqual(forum.queue_confirmed_deal(self.root, deal, self.assessment, now=NOW)["status"], "invalid")
        for timestamp in (NOW - 21601, NOW + 1, None, float("nan")):
            self.assertEqual(forum.queue_confirmed_deal(self.root, self.deal,
                {**self.assessment, "reviewed_at": timestamp}, now=NOW)["status"], "invalid")
        self.assertFalse((self.md / "buyer-forum-spool.json").exists())

    def test_matching_url_identities_and_disagreeing_urls(self):
        deal = deepcopy(self.deal)
        deal["receipt"]["shop"] = "https://fixture.invalid/"
        self.assertEqual(forum.queue_confirmed_deal(self.root, deal, self.assessment, now=NOW)["status"], "queued")
        deal["receipt"]["shop"] = "https://wrong.invalid"
        self.assertEqual(forum.queue_confirmed_deal(self.root, deal, self.assessment, now=NOW)["status"], "invalid")

    def test_exact_ledger_dedup_allows_out_of_order_completions(self):
        self.assertEqual(self.queue(1000)["status"], "queued")
        self.assertEqual(self.queue(999)["status"], "queued")
        before = (self.md / "buyer-forum-spool.json").read_bytes()
        self.assertEqual(self.queue(1000)["status"], "duplicate")
        self.assertEqual((self.md / "buyer-forum-spool.json").read_bytes(), before)
        self.assertEqual(set(self.state()["seen"]), {999, 1000})

    def test_pending_and_post_size_are_bounded_without_losing_dedup(self):
        for ledger in range(1, 101):
            self.queue(ledger, price=60000 + ledger)
        self.assertEqual(len(self.state()["pending"]), forum.MAX_PENDING)
        self.assertEqual(len(self.state()["seen"]), 100)
        self.assertEqual(self.queue(1)["status"], "duplicate")
        self.assertEqual(self.drain()["status"], "sent")
        body = self.market.calls[0][3]["json"]
        self.assertEqual(len(body["refs"]), 3)
        self.assertLess(len(body["text"]), 1000)

    def test_fifteen_minute_cadence_and_no_repeated_identical_summary(self):
        self.queue(100)
        self.assertEqual(self.drain()["status"], "sent")
        self.queue(101, now=NOW + 1)
        snapshot = (self.md / "buyer-forum-spool.json").read_bytes()
        self.assertEqual(self.drain(NOW + 899)["status"], "throttled")
        self.assertEqual((self.md / "buyer-forum-spool.json").read_bytes(), snapshot)
        self.assertEqual(self.drain(NOW + 900)["status"], "unchanged")
        self.assertEqual(len(self.market.calls), 1)
        self.queue(102, now=NOW + 900, price=65000)
        self.assertEqual(self.drain(NOW + 900)["status"], "sent")
        self.assertEqual(len(self.market.calls), 2)

    def test_shop_token_never_substitutes_for_missing_buyer_token(self):
        self.queue()
        self.market.token.pop("buyer")
        self.assertEqual(self.drain()["status"], "no_buyer_token")
        self.assertFalse(self.market.calls)
        self.assertEqual(len(self.state()["pending"]), 1)

    def test_rate_limit_retains_exact_batch_and_obeys_backoff(self):
        self.queue()
        self.market.response = httpx.Response(429, json={"wait": 1200}, headers={"Retry-After": "1800"})
        self.assertEqual(self.drain(), {"status": "rate_limited", "retry_at": NOW + 1800})
        before = deepcopy(self.market.calls[0][3]["json"])
        self.queue(101, now=NOW + 10, price=61000)
        self.assertEqual(self.drain(NOW + 1799)["status"], "throttled")
        self.market.response = httpx.Response(200, json={"id": 78})
        self.assertEqual(self.drain(NOW + 1800)["status"], "sent")
        self.assertEqual(self.market.calls[-1][3]["json"], before)
        self.assertEqual([entry["ledger"] for entry in self.state()["pending"]], [101])

    def test_transport_unknown_is_quarantined_and_never_replayed(self):
        self.queue()
        self.market.response = httpx.ReadTimeout("NEVER-PUBLISH-SECRET-ERROR")
        self.assertEqual(self.drain()["status"], "uncertain")
        self.assertEqual(self.state()["history"][-1]["status"], "uncertain")
        self.assertEqual(self.queue()["status"], "duplicate")
        self.market.response = httpx.Response(200, json={"id": 79})
        self.assertEqual(self.drain(NOW + 900)["status"], "empty")
        self.assertEqual(len(self.market.calls), 1)
        self.assertNotIn("SECRET-ERROR", (self.md / "buyer-forum-spool.json").read_text())

    def test_existing_inflight_after_restart_is_not_sent_again(self):
        self.queue()
        spool = forum.Spool(self.md)
        state = spool.load()
        state["attempt"] = {"id": "a" * 32, "state": "inflight", "started_at": NOW - 1,
                            "entries": state["pending"], "fingerprint": forum.fingerprint(state["pending"])}
        spool.save(state)
        self.assertEqual(self.drain()["status"], "uncertain")
        self.assertFalse(self.market.calls)
        self.assertFalse(self.state()["pending"])
        self.assertEqual(self.queue()["status"], "duplicate")

    def test_durable_inflight_precedes_network_and_write_failure_prevents_send(self):
        self.queue()
        self.market.callback = lambda: self.assertEqual(self.state()["attempt"]["state"], "inflight")
        self.assertEqual(self.drain()["status"], "sent")
        self.queue(101, now=NOW + 900, price=61000)
        with patch("buyer_forum.durable_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.drain(NOW + 900)
        self.assertEqual(len(self.market.calls), 1)

    def test_queue_remains_available_during_send_and_concurrent_drainer_does_not_post(self):
        self.queue()
        entered, release = threading.Event(), threading.Event()
        def callback():
            entered.set()
            if not release.wait(3):
                raise AssertionError("test sender blocked")
        self.market.callback = callback
        results = []
        worker = threading.Thread(target=lambda: results.append(self.drain()))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.queue(101, price=61000)["status"], "queued")
            self.assertEqual(self.drain()["status"], "busy")
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["status"], "sent")
        self.assertEqual(len(self.market.calls), 1)
        self.assertEqual([entry["ledger"] for entry in self.state()["pending"]], [101])

    def test_no_old_backlog_after_six_hours_or_market_round_change(self):
        self.queue()
        self.assertEqual(self.drain(NOW + 21601)["status"], "empty")
        self.assertFalse(self.market.calls)
        self.queue(101, now=NOW + 21601)
        durable_json(self.md / "inventory.json", {"round": 4})
        self.assertEqual(self.drain(NOW + 21601)["status"], "wrong_round")
        self.assertEqual(self.queue(102, now=NOW + 21601)["status"], "wrong_round")
        self.assertFalse(self.market.calls)

    def test_empty_poll_does_not_create_spool_or_lock_files(self):
        self.assertEqual(self.drain()["status"], "empty")
        self.assertFalse(self.md.exists())

    def test_auth_failure_and_server_error_never_fall_back_or_retry(self):
        for status, outcome in ((403, "rejected"), (500, "uncertain")):
            with self.subTest(status=status):
                self.queue(status, now=NOW + status * 1000, price=status * 100)
                self.market.response = httpx.Response(status, json={"error": "fixture"})
                self.assertEqual(self.drain(NOW + status * 1000)["status"], outcome)
                self.assertEqual(self.drain(NOW + status * 1000 + 900)["status"], "empty")
        self.assertEqual([call[2] for call in self.market.calls], ["buyer", "buyer"])

    def test_dedup_capacity_fails_closed_instead_of_forgetting_old_ledger(self):
        with patch("buyer_forum.MAX_SEEN", 2):
            self.assertEqual(self.queue(100)["status"], "queued")
            self.assertEqual(self.queue(99)["status"], "queued")
            self.assertEqual(self.queue(101)["status"], "capacity")
            self.assertEqual(self.queue(100)["status"], "duplicate")

    def test_invalid_spool_is_not_reset_and_symlinks_are_not_followed(self):
        self.queue()
        path = self.md / "buyer-forum-spool.json"
        path.write_text("{broken")
        with self.assertRaises(forum.BuyerForumError):
            self.drain()
        self.assertEqual(path.read_text(), "{broken")
        path.unlink()
        target = self.root / "private.txt"
        target.write_text("private data")
        path.symlink_to(target)
        with self.assertRaises(forum.BuyerForumError):
            self.drain()
        self.assertEqual(target.read_text(), "private data")
        self.assertFalse(self.market.calls)


if __name__ == "__main__":
    unittest.main()
