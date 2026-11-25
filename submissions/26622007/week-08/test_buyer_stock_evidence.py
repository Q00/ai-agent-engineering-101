"""B1 public observation/decision/durable evidence seams, without market calls."""
import tempfile
import unittest
from collections import Counter
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
import time
import json
import pathlib
import threading
import fcntl
import sys
import market_buyer

import buyer_stock_evidence as evidence


class LiveObservation(unittest.TestCase):
    def test_invalid_listing_is_unknown_without_breaking_quote_observation(self):
        with tempfile.TemporaryDirectory() as md:
            for offers in (None, "unexpected", [None], [{"sku": "G12345678", "qty": True}],
                           [{"sku": "G12345678", "qty": "2"}]):
                sh = {"name": "a", "url": "https://a.example", "offers": offers}
                self.assertFalse(evidence.observe(md, sh, "G12345678", text="sold out", at=100))
                self.assertEqual(evidence.availability(md, sh, "G12345678", now=101), "UNKNOWN")

    def test_explicit_sold_out_requires_same_live_inventory_snapshot(self):
        # Listing schema consumed by usable_offers; seller.py open_from_stock's
        # actual rejected response. No terminal-log reconstruction.
        shop = {"name": "shop-a", "url": "https://shop-a.example",
                "offers": [{"sku": "G12345678", "name": "vacuum", "qty": 2}]}
        with tempfile.TemporaryDirectory() as md:
            self.assertEqual(evidence.availability(md, shop, "G12345678", now=100), "UNKNOWN")
            self.assertTrue(evidence.observe(md, shop, "G12345678",
                                            text="Sorry, we have none of that in stock.",
                                            ask=None, at=100))
            self.assertEqual(evidence.availability(md, shop, "G12345678", now=101), "SOLD_OUT")
            changed = {**shop, "offers": [{**shop["offers"][0], "qty": 3}]}
            self.assertEqual(evidence.availability(md, changed, "G12345678", now=101), "UNKNOWN")

    def test_inventory_change_does_not_resurrect_old_evidence_when_quantity_returns(self):
        old = {"name": "a", "url": "https://a.example", "offers": [{"sku": "G12345678", "qty": 2}]}
        new = {**old, "offers": [{"sku": "G12345678", "qty": 3}]}
        with tempfile.TemporaryDirectory() as md:
            evidence.observe(md, old, "G12345678", text="sold out", at=100, snapshot_at=99)
            self.assertEqual(evidence.availability(md, new, "G12345678", now=101), "UNKNOWN")
            self.assertEqual(evidence.availability(md, old, "G12345678", now=102), "UNKNOWN")

    def test_positive_quote_fit_and_ttl_clear_the_same_pair_only(self):
        shop = {"name": "a", "url": "https://a.example",
                "offers": [{"sku": "G12345678", "qty": 2}]}
        with tempfile.TemporaryDirectory() as md:
            evidence.observe(md, shop, "G12345678", text="sold out", at=100)
            evidence.observe(md, shop, "G12345678", ask=9000, at=101)
            evidence.observe(md, shop, "G12345678", text="sold out", at=100.5)
            self.assertEqual(evidence.availability(md, shop, "G12345678", now=102), "AVAILABLE")
            evidence.observe(md, shop, "G12345678", fit=True, at=103)
            self.assertEqual(evidence.availability(md, shop, "G12345678", now=104), "AVAILABLE")
            self.assertEqual(evidence.availability(md, shop, "G12345678", now=403), "UNKNOWN")

    def test_stale_snapshot_reply_cannot_undo_newer_inventory_invalidation(self):
        old = {"name": "a", "url": "https://a.example", "offers": [{"sku": "G12345678", "qty": 2}]}
        new = {**old, "offers": [{"sku": "G12345678", "qty": 3}]}
        with tempfile.TemporaryDirectory() as md:
            evidence.observe(md, old, "G12345678", text="sold out", at=100, snapshot_at=99)
            evidence.availability(md, new, "G12345678", now=101)
            evidence.observe(md, old, "G12345678", text="sold out", at=102, snapshot_at=99)
            self.assertEqual(evidence.availability(md, old, "G12345678", now=103), "UNKNOWN")

    def test_actual_legacy_terminal_schema_stays_unknown_without_live_snapshot(self):
        fixture = json.loads((pathlib.Path(__file__).parent / "testdata/b1_legacy_order.json").read_text())
        row = fixture["legacy_order"]
        self.assertEqual(len(row["candidates"]), 20)
        with tempfile.TemporaryDirectory() as md:
            for q in row["quotes"]:
                legacy = {"name": q["shop"], "url": "https://legacy.example", "usable": [{"sku": q["sku"], "qty": 1}]}
                self.assertFalse(evidence.observe(md, legacy, q["sku"], text=q["text"], at=row["ts"]))
                self.assertEqual(evidence.availability(md, legacy, q["sku"], now=row["ts"]+1), "UNKNOWN")

    def test_generic_refusal_negation_or_other_sku_never_marks_sold_out(self):
        sh = {"name": "a", "url": "https://a.example", "offers": [{"sku": "G12345678", "qty": 2}]}
        with tempfile.TemporaryDirectory() as md:
            for text in ("not sold out", "If sold out, try later", "G87654321 is sold out", "no warranty", "", "no free unit"):
                evidence.observe(md, sh, "G12345678", text=text, at=100)
                self.assertEqual(evidence.availability(md, sh, "G12345678", now=101), "UNKNOWN")

    def test_fsync_never_owns_publication_lock(self):
        sh = {"name": "a", "url": "https://a.example", "offers": [{"sku": "G12345678", "qty": 2}]}
        held = threading.local()
        real_flock, real_fsync = fcntl.flock, evidence.os.fsync
        def flock(fd, operation):
            real_flock(fd, operation)
            held.value = operation != fcntl.LOCK_UN
        def fsync(fd):
            self.assertFalse(getattr(held, "value", False))
            real_fsync(fd)
        with tempfile.TemporaryDirectory() as md, mock.patch.object(evidence.fcntl, "flock", flock), \
                mock.patch.object(evidence.os, "fsync", fsync):
            self.assertTrue(evidence.observe(md, sh, "G12345678", text="sold out", at=100))


class Decision(unittest.TestCase):
    def test_post_journal_io_revalidation_uses_latest_clock_and_never_confirms_a_stale_proposal(self):
        for change in ("end", "expired", "numeric", "inventory"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                    evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200 if change == "end" else 900):
                shops = [{"name": n, "url": "https://"+n+".example", "reviews": [],
                          "offers": [{"sku": "G12345678", "qty": 2}]} for n in "abc"]
                selected = {n: "G12345678" for n in "abc"}
                order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100}
                ctx = evidence.reserve(md, order, now=101)
                evidence.observe(md, shops[0], "G12345678", text="sold out", at=100)
                planned = evidence.plan(md, shops, Counter(), set(), selected, k=2, now=101)
                clock, fired = [101.0], [False]
                real_fsync = evidence.os.fsync
                def fsync(fd):
                    real_fsync(fd)
                    if not fired[0]:
                        fired[0] = True
                        clock[0] = 201 if change == "end" else 401 if change == "expired" else 103
                        if change == "numeric":
                            self.assertTrue(evidence.observe(md, shops[0], "G12345678", ask=9000, at=102))
                        elif change == "inventory":
                            shops[0]["offers"][0]["qty"] = 3
                current = lambda now: evidence.plan(md, shops, Counter(), set(), selected, k=2, now=now)
                with mock.patch.object(evidence.time, "time", side_effect=lambda: clock[0]), \
                        mock.patch.object(evidence.os, "fsync", fsync):
                    chosen = evidence.decide(ctx, planned, current=current)
                self.assertEqual(chosen, list("abc"))
                rows = evidence.read_order(md, order["order"], "b1-local-test")
                self.assertIsNotNone(rows["assignment"])
                # Disk contains a proposal, never a forged confirmed dispatch.
                self.assertIsNone(rows["decision"]["intended_reorder"])
                self.assertTrue(rows["decision"]["proposed_reorder"])
                self.assertEqual(rows["attempts"], [])

    def test_expired_cleared_or_changed_evidence_between_plan_and_decision_keeps_baseline(self):
        for change in ("expired", "numeric", "fit", "inventory"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                    evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=900):
                shops = [{"name": n, "url": "https://"+n+".example", "reviews": [],
                          "offers": [{"sku": "G12345678", "qty": 2}]} for n in "abc"]
                selected = {n: "G12345678" for n in "abc"}
                order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100}
                ctx = evidence.reserve(md, order, now=101)
                evidence.observe(md, shops[0], "G12345678", text="sold out", at=100)
                planned = evidence.plan(md, shops, Counter(), set(), selected, k=2, now=101)
                self.assertTrue(planned["eligible"])
                at = 401 if change == "expired" else 103
                if change == "numeric":
                    evidence.observe(md, shops[0], "G12345678", ask=9000, at=102)
                elif change == "fit":
                    evidence.observe(md, shops[0], "G12345678", fit=True, at=102)
                elif change == "inventory":
                    shops[0]["offers"][0]["qty"] = 3
                with mock.patch.object(evidence.time, "time", return_value=at):
                    # Public supplier always rebuilds from live caller inputs and real disk evidence.
                    current = lambda now: evidence.plan(md, shops, Counter(), set(), selected, k=2, now=now)
                    chosen = evidence.decide(ctx, planned, current=current)
                self.assertEqual(chosen, list("abc"))
                rows = evidence.read_order(md, order["order"], "b1-local-test")
                self.assertIsNotNone(rows["assignment"])
                self.assertFalse(rows["decision"]["intended_reorder"])
                self.assertEqual(rows["attempts"], [])

    def test_only_moves_within_fit_fresh_and_rival_slots_and_keeps_every_shop(self):
        shops = [{"name": n, "url": "https://" + n + ".example", "reviews": [1] * (8 - i),
                  "offers": [{"sku": "G12345678", "qty": 2}]} for i, n in enumerate("afbrcsdt")]
        fits = Counter({n: 1 for n in "abcd"})
        rivals = {"r", "s", "t"}
        selected = {s["name"]: "G12345678" for s in shops}
        with tempfile.TemporaryDirectory() as md:
            evidence.observe(md, shops[0], "G12345678", text="sold out", at=100)
            plan = evidence.plan(md, shops, fits, rivals, selected, k=4, now=101)
            self.assertEqual(plan["baseline"], list("afbrcsdt"))
            self.assertEqual(plan["treatment"], list("bfcrdsat"))
            self.assertEqual(plan["first_baseline"], list("afb"))
            self.assertEqual(plan["first_treatment"], list("bfc"))
            self.assertTrue(plan["eligible"])
            self.assertEqual(len(plan["inputs"]), 8)
            # k-1 covers every candidate: no exposure difference means ineligible.
            self.assertFalse(evidence.plan(md, shops, fits, rivals, selected, k=10, now=101)["eligible"])

    def test_unknown_sold_out_of_another_sku_and_expired_evidence_preserve_baseline(self):
        sh = [{"name": n, "url": "https://"+n+".example", "offers": [{"sku": "G12345678", "qty": 2}]}
              for n in "abc"]
        selected = {n: "G12345678" for n in "abc"}
        with tempfile.TemporaryDirectory() as md:
            first = evidence.plan(md, sh, Counter(), set(), selected, k=2, now=100)
            self.assertFalse(first["eligible"])
            evidence.observe(md, sh[0], "G12345678", text="sold out", at=100)
            expired = evidence.plan(md, sh, Counter(), set(), selected, k=2, now=400)
            self.assertFalse(expired["eligible"])
            other = evidence.plan(md, sh, Counter(), set(), {n: "G87654321" for n in "abc"}, k=2, now=101)
            self.assertFalse(other["eligible"])

    def test_full_snapshot_is_not_truncated_to_legacy_twenty_candidates(self):
        shops = [{"name": str(n), "url": "https://fixture.example", "offers": [{"sku": "G12345678", "qty": 1}]}
                 for n in range(32)]
        with tempfile.TemporaryDirectory() as md:
            decision = evidence.plan(md, shops, Counter(), set(), {s["name"]: "G12345678" for s in shops}, k=10, now=100)
            self.assertEqual(len(decision["inputs"]), 32)
            self.assertEqual(len(decision["treatment"]), 32)


class DurableITT(unittest.TestCase):
    def test_live_reservation_crossing_end_during_fsync_preserves_itt_but_returns_baseline(self):
        order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 199}
        clock = [199.0]
        real_fsync = evidence.os.fsync
        def fsync(fd):
            real_fsync(fd)
            clock[0] = 201.0  # elapsed wallclock crosses END while reservation IO runs
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200), \
                mock.patch.object(evidence.time, "time", side_effect=lambda: clock[0]):
            with mock.patch.object(evidence.os, "fsync", fsync):
                self.assertIsNone(evidence.reserve(md, order))
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual(rows["assignment"]["arm"], "B")
            self.assertIsNone(rows["decision"])
            self.assertEqual(rows["attempts"], [])

    def test_genuine_order_identifier_schema_gets_a_full_uuid_allocation(self):
        fixture = json.loads((pathlib.Path(__file__).parent / "testdata/b1_legacy_order.json").read_text())
        order = {**fixture["legacy_order"], "_buyer_started_at": 100}
        # Actual kit schema is a prefix plus twelve hex digits, not a bare UUID.
        self.assertEqual(order["order"], "o-000000000001")
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            ctx = evidence.reserve(md, order, now=101)
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx["order"], "o-000000000001")
            self.assertEqual(len(ctx["allocation_id"]), 36)
            self.assertEqual(evidence.reserve(md, order, now=102)["allocation_id"], ctx["allocation_id"])
            self.assertEqual(evidence.read_order(md, order["order"], "b1-local-test")["assignment"]["order"], order["order"])

    def test_slow_disk_reservation_times_out_to_baseline_even_if_intent_finishes_later(self):
        order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100}
        entered, release = threading.Event(), threading.Event()
        real_fsync, real_start = evidence.os.fsync, threading.Thread.start
        workers = []
        def fsync(fd):
            entered.set()
            if not release.wait(2):
                raise OSError("test disk deadline")
            return real_fsync(fd)
        def start(thread):
            workers.append(thread)
            return real_start(thread)
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            with mock.patch.object(evidence.os, "fsync", fsync), mock.patch.object(threading.Thread, "start", start):
                try:
                    self.assertIsNone(evidence.reserve(md, order, now=101))
                    self.assertTrue(entered.is_set())
                finally:
                    release.set()
                    for worker in workers:
                        worker.join(2)
                        self.assertFalse(worker.is_alive())
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual(rows["assignment"]["arm"], "B")
            self.assertIsNone(rows["decision"])
            self.assertEqual(rows["attempts"], [])

    def test_witness_and_terminal_io_failure_leave_the_allocation_denominator(self):
        order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100}
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            ctx = evidence.reserve(md, order, now=101)
            with mock.patch.object(evidence.os, "link", side_effect=OSError("disk unavailable")):
                self.assertFalse(evidence.attempt(ctx, "a", "G12345678", phase="prefetch"))
                self.assertFalse(evidence.terminal(ctx, state="ERROR", passed_to_kit=False))
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual(rows["assignment"]["arm"], "B")
            self.assertEqual(rows["attempts"], [])
            self.assertIsNone(rows["terminal"])

    def test_concurrent_retries_reserve_once_without_fabricating_attempt_or_terminal(self):
        order = {"order": "12345678-1234-4234-8234-123456789abc", "kind": "goods", "_buyer_started_at": 100}
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            with ThreadPoolExecutor(max_workers=4) as pool:
                contexts = list(pool.map(lambda _: evidence.reserve(md, order, now=101), range(4)))
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual(rows["assignment"]["order"], order["order"])
            self.assertTrue(all(c and c["arm"] == rows["assignment"]["arm"] for c in contexts))
            self.assertEqual(rows["attempts"], [])
            self.assertIsNone(rows["terminal"])
            with mock.patch.object(evidence, "ENABLED", False):
                self.assertIsNone(evidence.reserve(md, order, now=102))
            self.assertEqual(evidence.read_order(md, order["order"], "b1-local-test"), rows)

    def test_full_uuid_hash_has_known_arms_and_retry_cannot_reassign(self):
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            for suffix, expected in (("c", "A"), ("d", "B")):
                order = {"order": "12345678-1234-4234-8234-123456789ab"+suffix,
                         "kind": "goods", "_buyer_started_at": 100}
                self.assertEqual(evidence.reserve(md, order, now=101)["arm"], expected)
                self.assertEqual(evidence.reserve(md, order, now=102)["arm"], expected)
            self.assertIsNone(evidence.reserve(md, {**order, "order": "short-id"}, now=101))
            self.assertIsNone(evidence.reserve(md, {**order, "kind": "food"}, now=101))
            self.assertIsNone(evidence.reserve(md, order, now=200))


class Runtime(unittest.TestCase):
    def test_failed_thread_start_does_not_fabricate_an_attempt_witness(self):
        sh = {"name": "a", "url": "https://a.example", "usable": [
            {"sku": "G12345678", "name": "vacuum", "qty": 2, "list_price": 10000}]}
        order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100}
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            order["_stock_evidence"] = evidence.reserve(md, order, now=101)
            with mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("test thread unavailable")):
                with self.assertRaisesRegex(RuntimeError, "test thread unavailable"):
                    market_buyer.start_quotes([sh], order, 20000, lambda sku: 0, {}, market_buyer.quote_ctl())
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertIsNotNone(rows["assignment"])
            self.assertEqual(rows["attempts"], [])
            self.assertIsNone(rows["terminal"])

    def test_final_dispatch_rereads_listing_file_without_mutating_original_input(self):
        shops = [{"name": n, "url": "https://"+n+".example", "reviews": [],
                  "offers": [{"sku": "G12345678", "name": "vacuum", "qty": 2}],
                  "usable": [{"sku": "G12345678", "name": "vacuum", "qty": 2, "list_price": 10000}]}
                 for n in "abc"]
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=900):
            path = pathlib.Path(md)/"shops.json"
            raw = [{k: s[k] for k in ("name", "url", "reviews", "offers")} for s in shops]
            path.write_text(json.dumps(raw))
            order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100,
                     "_stock_phase": "prefetch", "_stock_shops_path": str(path), "_stock_all_shop_names": list("abc")}
            order["_stock_evidence"] = evidence.reserve(md, order, now=101)
            evidence.observe(md, shops[0], "G12345678", text="sold out", at=100)
            with mock.patch.object(evidence.time, "time", return_value=101):
                early, full = market_buyer.prefetch_pool(shops, {"trades": []}, md, "appliances", 2,
                                                       kind="goods", order=order, budget=20000)
            self.assertEqual([s["name"] for s in early], ["b"])
            updated = json.loads(path.read_text())
            updated[0]["offers"][0]["qty"] = 3
            path.write_text(json.dumps(updated))
            self.assertEqual(shops[0]["offers"][0]["qty"], 2)
            with mock.patch.object(evidence.time, "time", return_value=103), \
                    mock.patch.object(market_buyer, "serves_acp", return_value=False):
                threads = market_buyer.start_quotes(early, order, 20000, lambda sku: 0, {}, market_buyer.quote_ctl())
                for thread in threads.values():
                    thread.join(1)
            self.assertEqual(set(threads), {"a"})
            self.assertEqual([s["name"] for s in full], list("abc"))
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual([(a["shop"], a["intended_reorder"]) for a in rows["attempts"]], [("a", False)])

    def test_actual_prefetch_dispatch_falls_back_after_evidence_clears(self):
        shops = [{"name": n, "url": "https://"+n+".example", "reviews": [],
                  "offers": [{"sku": "G12345678", "name": "vacuum", "qty": 2}],
                  "usable": [{"sku": "G12345678", "name": "vacuum", "qty": 2, "list_price": 10000}]}
                 for n in "abc"]
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=900):
            order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100,
                     "_stock_phase": "prefetch"}
            order["_stock_evidence"] = evidence.reserve(md, order, now=101)
            evidence.observe(md, shops[0], "G12345678", text="sold out", at=100)
            with mock.patch.object(evidence.time, "time", return_value=101):
                early, full = market_buyer.prefetch_pool(shops, {"trades": []}, md, "appliances", 2,
                                                       kind="goods", order=order, budget=20000)
            self.assertEqual([s["name"] for s in early], ["b"])
            evidence.observe(md, shops[0], "G12345678", ask=9000, at=102)
            with mock.patch.object(evidence.time, "time", return_value=103), \
                    mock.patch.object(market_buyer, "serves_acp", return_value=False):
                threads = market_buyer.start_quotes(early, order, 20000, lambda sku: 0, {}, market_buyer.quote_ctl())
                for thread in threads.values():
                    thread.join(1)
            self.assertEqual(set(threads), {"a"})
            self.assertEqual([s["name"] for s in full], list("abc"))
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual([(a["shop"], a["intended_reorder"]) for a in rows["attempts"]], [("a", False)])
            self.assertIsNone(rows["decision"]["intended_reorder"])

    def test_main_no_shop_still_reserves_denominator_and_writes_terminal_without_attempts(self):
        now = time.time()
        order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "acceptable": ["G12345678"]}
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=now-1, END=now+120):
            base = pathlib.Path(md)
            (base / "shops.json").write_text("[]")
            (base / "catalog.json").write_text("{}")
            (base / "order.json").write_text(json.dumps(order))
            with mock.patch.object(sys, "argv", ["buyer", "--shops", str(base/"shops.json"), "--order", str(base/"order.json"),
                                                "--item", "appliances", "--budget", "20000"]), \
                    mock.patch.object(market_buyer, "MEMORY", str(base/"memory.json")), mock.patch("builtins.print"):
                self.assertEqual(market_buyer.main(), 0)
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual(rows["assignment"]["arm"], "B")
            self.assertEqual(rows["terminal"]["state"], "NO_SHOP_IN_STOCK")
            self.assertFalse(rows["terminal"]["passed_to_kit"])
            self.assertEqual(rows["attempts"], [])

    def test_live_quote_observation_changes_real_prefetch_and_start_quotes_has_separate_witness(self):
        shops = [{"name": n, "url": "https://" + n + ".example", "reviews": [],
                  "offers": [{"sku": "G12345678", "name": "vacuum", "qty": 2}],
                  "usable": [{"sku": "G12345678", "name": "vacuum", "qty": 2, "list_price": 10000}]}
                 for n in "abc"]
        task = {"id": "task-sold-out", "status": {"state": "TASK_STATE_REJECTED", "message": {
            "role": "agent", "parts": [{"kind": "text", "text": "Sorry, we have none of that in stock."}]}}}
        card = mock.Mock()
        card.json.return_value = {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                                           "url": "https://a.example/a2a"}]}
        now = time.time()
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=now-1, END=now+120):
            prior = {"kind": "goods", "_stock_evidence_md": md}
            with mock.patch.object(market_buyer, "serves_acp", return_value=True), \
                    mock.patch.object(market_buyer.httpx, "get", return_value=card), \
                    mock.patch.object(market_buyer, "buyer_send", return_value=task):
                market_buyer.quote(shops[0], "vacuum", "G12345678", prior, {})
            order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods",
                     "_buyer_started_at": now, "_stock_evidence_md": md, "_stock_phase": "prefetch"}
            order["_stock_evidence"] = evidence.reserve(md, order)
            early, full = market_buyer.prefetch_pool(shops, {"trades": []}, md, "appliances", 2,
                                                   kind="goods", order=order, budget=20000)
            self.assertEqual([s["name"] for s in early], ["b"])
            self.assertEqual([s["name"] for s in full], list("bca"))
            before = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual(before["attempts"], [])
            # Real start_quotes/thread/quote; external transport only is replaced.
            with mock.patch.object(market_buyer, "serves_acp", return_value=False):
                threads = market_buyer.start_quotes(early, order, 20000, lambda sku: 0, {}, market_buyer.quote_ctl())
                for thread in threads.values():
                    thread.join(1)
            after = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertEqual([(r["shop"], r["sku"]) for r in after["attempts"]], [("b", "G12345678")])
            self.assertTrue(after["attempts"][0]["intended_reorder"])
            self.assertEqual(after["attempts"][0]["phase"], "prefetch")
            self.assertIsNone(after["decision"]["intended_reorder"])
            self.assertIsNone(after["terminal"])

    def test_decision_failure_keeps_baseline_and_durable_itt_without_an_attempt(self):
        order = {"order": "12345678-1234-4234-8234-123456789abd", "kind": "goods", "_buyer_started_at": 100}
        shops = [{"name": n, "url": "https://" + n + ".example", "offers": [{"sku": "G12345678", "qty": 2}]}
                 for n in "abc"]
        with tempfile.TemporaryDirectory() as md, mock.patch.multiple(
                evidence, ENABLED=True, STUDY="b1-local-test", START=90, END=200):
            ctx = evidence.reserve(md, order, now=101)
            evidence.observe(md, shops[0], "G12345678", text="sold out", at=100)
            plan = evidence.plan(md, shops, Counter(), set(), {n: "G12345678" for n in "abc"}, k=2, now=101)
            # Filesystem boundary failure, after a durable allocation.
            with mock.patch.object(evidence.os, "link", side_effect=OSError("disk unavailable")):
                current = lambda now: evidence.plan(md, shops, Counter(), set(), {n: "G12345678" for n in "abc"}, k=2, now=now)
                chosen = evidence.decide(ctx, plan, current=current, now=101)
            self.assertEqual(chosen, list("abc"))
            rows = evidence.read_order(md, order["order"], "b1-local-test")
            self.assertIsNotNone(rows["assignment"])
            self.assertIsNone(rows["decision"])
            self.assertEqual(rows["attempts"], [])
