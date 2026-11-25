"""Grade warranty experiment through Seller's quote/receipt and measurement seams.

Run with python -m unittest test_seller_warranty_trial in an offline source/state copy.
"""
import copy
import asyncio
import concurrent.futures
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx
import fast_classify
import seller
import harness
import shop_memory
from shop_stock import StockBook


ORIGINAL_MEMORY_WRITER = seller.MEMORY_WRITER


class DiscardingMemoryWriter:
    """Complete submissions without running callbacks or creating a worker."""

    def __init__(self):
        self.futures = []

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        future.set_result(None)
        self.futures.append(future)
        return future

    def shutdown(self, wait=True):
        if wait and self.futures:
            concurrent.futures.wait(self.futures)


@contextmanager
def measured_reads(target):
    """Count real file reads, so corrupt data cannot pass by failing eventually."""
    reads = []
    original_open = Path.open

    def tracked_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path != target:
            return stream
        tracked = mock.MagicMock(wraps=stream)
        tracked.__enter__.return_value = tracked
        tracked.__exit__.side_effect = stream.__exit__

        def read(size=-1):
            data = stream.read(size)
            reads.append((size, len(data)))
            return data

        tracked.read.side_effect = read
        return tracked

    with mock.patch.object(Path, "open", tracked_open):
        yield reads


# Inventory contract: unit/sku/grade strings, integer list_price/cost,
# nullable expires_at, cert object. No live inventory is consulted.
UNIT = dict(unit="unit-fixture-c", sku="G00000001", name="fixture box",
            category="goods > box", kind="goods", grade="C", cost=7000,
            list_price=10000, expires_at=None, supplied_at=100.0,
            cert={"cert": {"unit": "unit-fixture-c", "grade": "C"}, "sig": "fixture"})


# Literal buyer variants: each must preserve a 72h receipt on either turn.
WARRANTY_REQUESTS = (
    "I require 6-h money-back coverage", "warrant", "warranties", "WARRANTY",
    "guaranty", "guaranteed", "cover", "covered", "coverage", "refund", "refunded",
    "money back", "money-back", "MONEY‐BACK", "money‑back", "money – back",
    "return", "return policy", "replacement", "replace", "repair", "repairable",
    "break", "breakage", "broke", "broken", "defect", "defective", "faulty",
    "insurance", "insured", "protection", "protect", "assurance", "assured",
    "보증", "보장", "환불", "반품", "교환", "고장", "파손", "불량",
    "A/S", "AS", "a / s", "Ａ／Ｓ", "ＡＳ", "6h", "6-h", "6 h", "6hr",
    "6-hour", "6시간", "72h", "6.h", "6 . h", "6-hr", "6.hr", "6 hrs.",
    "6-hours", "6.hour", "6-시간", "6.시간", "６－Ｈ", "７２ｈ", "6‑h",
    "6–hour", "6—hr", "6−h", "6\t h", "6.5-h", "w a r r a n t y",
    "guar-anty", "c o v e r", "money\tback", "보 증", "파 손", "re-placement",
)


def protected_state():
    """Hash only: tests must leave the imported seller's real state untouched."""
    base = Path(seller.__file__).parent
    paths = {base / ".market", base / "memory", Path(shop_memory.MEMORY), Path(shop_memory.MEMORY_R3)}
    paths.update(controller.root for controller in vars(seller).values()
                 if hasattr(controller, "root") and isinstance(controller.root, Path))
    result = []
    for root in sorted(paths):
        entries = sorted(root.rglob("*")) if root.is_dir() else [root]
        result.append((str(root), root.exists()))
        for path in entries:
            if path.is_file():
                data = path.read_bytes()
                result.append((str(path), hashlib.sha256(data).hexdigest(), data.count(b"\n")))
    return result


class WarrantyTrialTest(unittest.TestCase):
    def setUp(self):
        # Guard both the imported alias and the harness entry point: a swallowed
        # exception must still fail cleanup's zero-call assertion.
        self.external_guards = []
        http_methods = ("request", "send", "stream", "get", "post", "put", "patch", "delete", "head", "options")
        # Patch methods as well as constructors: Jev pools may have captured a
        # Client factory before setUp, or may already own a client instance.
        boundaries = [(client, name) for client in (httpx.Client, httpx.AsyncClient)
                      for name in http_methods]
        boundaries += [(httpx, name) for name in http_methods if name != "send"]
        boundaries += [(httpx, "Client"), (httpx, "AsyncClient"),
                       (httpx.HTTPTransport, "handle_request"),
                       (httpx.AsyncHTTPTransport, "handle_async_request"),
                       (fast_classify, "jev_offer"),
                       (seller, "ask"), (harness, "ask"), (subprocess, "Popen")]
        for target, name in boundaries:
            blocked = mock.Mock(side_effect=AssertionError(
                f"warranty tests must not call {target.__name__}.{name}"))
            patch = mock.patch.object(target, name, blocked)
            patch.start()
            self.addCleanup(patch.stop)
            self.addCleanup(blocked.assert_not_called)
            self.external_guards.append(blocked)
        # Environment changes alone cannot undo flags cached during import.
        for target, name, value in ((harness, "BACKEND", "offline-warranty"),
                                    (fast_classify, "JEV_ON", False)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        before = protected_state()
        self.addCleanup(lambda: self.assertEqual(protected_state(), before))
        failures = list(seller.MEMORY_FAILURES)
        self.addCleanup(lambda: self.assertEqual(seller.MEMORY_FAILURES, failures))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.memory_writer = DiscardingMemoryWriter()
        writer = mock.patch.object(seller, "MEMORY_WRITER", self.memory_writer)
        writer.start()
        self.addCleanup(writer.stop)
        for name in ("READER_TRIAL", "CONCESSION_TRIAL", "LATE_CONCESSION",
                     "OPENING_DEFAULT", "DRAIN", "OPENING_CUE", "LATE_SPEED",
                     "JEV_TRANSPORT", "SHELF_REPLY", "EXACT_ACCEPT", "EXACT_COUNTER",
                     "GRADE_PREFERENCE", "FOOD_OPENING", "AT_FLOOR_EXACT", "DEEP_LOWBALL", "QUOTE_HOLD", "SHELF_JEV", "SHELF_FACT", "WARRANTY_FACT", "GRADE_READ"):
            patch = mock.patch.object(seller, name, type(getattr(seller, name))(root=self.root / "controllers"))
            patch.start()
            self.addCleanup(patch.stop)
        for name in ("MEMORY", "MEMORY_R3"):
            patch = mock.patch.object(shop_memory, name, str(self.root / (name + ".json")))
            patch.start()
            self.addCleanup(patch.stop)
        self.config = self.root / "seller_warranty_trial.json"
        self.market = self.root / "market"
        self.pricing = self.root / "pricing.json"
        self.pricing.write_text(json.dumps({"warranty_hours": 72}))
        env = mock.patch.dict(os.environ, MARKET_DIR=str(self.market),
                              SELLER_WARRANTY_TRIAL_CONFIG=str(self.config),
                              HARNESS="offline-warranty", FAST_JEV="0",
                              OPENROUTER_API_KEY="fixture-no-network",
                              OPENROUTER_ENV_FILE=str(self.root / "missing-credentials"))
        env.start()
        self.addCleanup(env.stop)
        pricing = mock.patch.object(shop_memory, "PRICING", str(self.pricing))
        pricing.start()
        self.addCleanup(pricing.stop)
        clock = mock.patch("time.time", return_value=150.0)
        clock.start()
        self.addCleanup(clock.stop)
        # Cleanups run in reverse order: finish submissions before restoring
        # any memory/controller path, the original executor, or protected hashes.
        self.addCleanup(self.finish_memory)
        self.unit = copy.deepcopy(UNIT)
        self.stock = StockBook(load=lambda: {"round": 3, "units": [self.unit]})
        self.shop = seller.Seller(None, "fixture shop", self.stock)
        self.cfg = dict(study="grade-warranty-v1", version=1, epoch="fixture-1",
                        salt="fixture-salt", enabled=True, share=1.0,
                        start_at=100, stop_at=200, grades=["C"])
        self.write_config()

    def finish_memory(self):
        self.memory_writer.shutdown(wait=True)
        self.assertTrue(all(future.done() for future in self.memory_writer.futures))

    def write_config(self, **changes):
        self.cfg.update(changes)
        self.config.write_text(json.dumps(self.cfg))

    def deal(self, **changes):
        return dict(dict(id=self.unit["sku"], unit=self.unit["unit"], kind="goods",
                         name="fixture box", cost=7000, reserve=8000, ask=9000,
                         open=9000, rounds=0, history=[("you asked", 9000)]), **changes)

    def quote(self, deal, text="Please quote G00000001", task="task-fixture"):
        return self.shop.warranty_terms(deal, text, task_id=task, unit=self.unit)

    def rows(self):
        path = self.market / "measure" / "seller_warranty_trial.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_all_controller_roots_are_temporary_before_opening(self):
        for name in ("READER_TRIAL", "CONCESSION_TRIAL", "LATE_CONCESSION",
                     "OPENING_DEFAULT", "DRAIN", "OPENING_CUE", "LATE_SPEED",
                     "JEV_TRANSPORT", "SHELF_REPLY", "EXACT_ACCEPT", "EXACT_COUNTER",
                     "GRADE_PREFERENCE", "FOOD_OPENING", "AT_FLOOR_EXACT", "DEEP_LOWBALL", "QUOTE_HOLD", "SHELF_JEV", "SHELF_FACT", "WARRANTY_FACT", "GRADE_READ"):
            with self.subTest(controller=name):
                self.assertTrue(getattr(seller, name).root.is_relative_to(self.root))

    def test_jev_followup_is_offline_even_with_hostile_ambient_model_settings(self):
        # The isolated runner imports with HARNESS=openrouter, FAST_JEV=1 and
        # a synthetic key. Keep the real Reader/fast-classify followup path.
        deal, _ = self.opening("G00000001")
        with mock.patch.object(seller, "decide_turn", return_value=dict(
                buyer_offer=0, action="counter", price=0, message="Fixture reply.")):
            self.followup("I need 6 hrs refund if broken")
        self.assertEqual(deal["warranty"], 72)
        self.assertEqual(os.environ["HARNESS"], "offline-warranty")
        self.assertEqual(os.environ["FAST_JEV"], "0")
        self.assertEqual(harness.BACKEND, "offline-warranty")
        self.assertFalse(fast_classify.JEV_ON)
        for blocked in self.external_guards:
            blocked.assert_not_called()

    def test_accept_never_submits_to_original_writer_or_records_memory(self):
        from google.protobuf.json_format import MessageToDict

        with mock.patch.object(ORIGINAL_MEMORY_WRITER, "submit",
                               side_effect=AssertionError("real memory executor must not receive test work")), \
                mock.patch.object(shop_memory, "record",
                                  side_effect=AssertionError("warranty tests must not record memory")):
            deal, _ = self.opening("G00000001")
            up = self.followup(str(deal["ask"]))
        up.complete.assert_awaited_once()
        receipt = MessageToDict(up.add_artifact.call_args.kwargs["parts"][0].data)
        self.assertEqual(receipt["unit"], self.unit["unit"])
        self.assertTrue(self.memory_writer.futures)
        self.assertTrue(all(future.done() for future in self.memory_writer.futures))

    def test_treatment_discloses_as_is_and_receipt_omits_warranty_even_with_pricing_fallback(self):
        deal = self.deal()
        prices = {key: copy.deepcopy(deal[key]) for key in deal}
        note = self.quote(deal)
        self.assertEqual(note, "This grade C unit is sold as is, without a warranty.")
        self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, 9000))
        self.assertEqual({key: deal[key] for key in prices}, prices)
        self.assertEqual(self.rows()[-1]["arm"], "treatment")

    def test_later_turn_keeps_terms_then_warranty_request_permanently_restores_control(self):
        deal = self.deal()
        self.quote(deal)
        self.shop.warranty_terms(deal, "I offer 9000", task_id="task-fixture")
        self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, 9000))
        note = self.shop.warranty_terms(deal, "Please give a 6 hours warranty", task_id="task-fixture")
        self.assertIn("72 h warranty", note)
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
        row = self.rows()[-1]
        self.assertEqual((row["assigned_arm"], row["arm"], row["reason"], row["offered_hours"]),
                         ("treatment", "control", "followup_warranty_request", 72))
        self.write_config(enabled=True)
        self.shop.warranty_terms(deal, "9000", task_id="task-fixture")
        self.assertEqual(self.rows()[-1]["arm"], "control")

    def test_negative_followup_restores_initial_control_hours_and_stays_control(self):
        self.pricing.write_text(json.dumps({"warranty_hours": 0}))
        for request in ("I cannot accept this without a warranty", "보증 없이 거래할 수 없습니다"):
            with self.subTest(request=request):
                deal = self.deal()
                self.assertIn("without a warranty", self.quote(deal))
                note = self.quote(deal, request)
                self.assertIn("72 h warranty", note or "")
                self.assertEqual(deal["warranty"], 72)
                self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                self.quote(deal, "9000")
                self.assertEqual(self.rows()[-1]["arm"], "control")

    def test_negative_numeric_followup_accepts_with_restored_receipt(self):
        from google.protobuf.json_format import MessageToDict
        self.pricing.write_text(json.dumps({"warranty_hours": 0}))
        deal, _ = self.opening("G00000001")
        up = self.followup(f"{deal['ask']}. I cannot accept this without a warranty")
        up.complete.assert_awaited_once()
        receipt = MessageToDict(up.add_artifact.call_args.kwargs["parts"][0].data)
        self.assertEqual(receipt["warranty_hours"], 72)
        self.assertIn("72 h warranty", up.complete.call_args.args[0]["parts"][0].text)
        self.assertEqual(self.rows()[-1]["arm"], "control")

    def test_off_missing_invalid_and_outside_window_keep_baseline(self):
        configurations = [None, "{", "[]", {**self.cfg, "enabled": False},
                          {**self.cfg, "start_at": 151}, {**self.cfg, "stop_at": 150}]
        for field, value in (("enabled", 1), ("version", True), ("version", 0),
                             ("share", -0.1), ("share", 1.1), ("share", float("nan")),
                             ("start_at", None), ("stop_at", None), ("start_at", 201),
                             ("epoch", ""), ("epoch", True), ("salt", ""),
                             ("study", ""), ("grades", []), ("grades", ["Q"]),
                             ("grades", "C")):
            configurations.append({**self.cfg, field: value})
        for cfg in configurations:
            with self.subTest(config=cfg):
                if cfg is None:
                    self.config.unlink(missing_ok=True)
                else:
                    self.config.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg))
                deal = self.deal()
                self.assertIn("72 h warranty", self.quote(deal))
                self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                self.assertNotIn("warranty_trial", deal)
                self.assertFalse(self.market.exists())

    def test_other_grades_requests_existing_terms_and_unknowns_keep_baseline(self):
        for grade in ("A", "B", None, "Q"):
            self.unit["grade"] = grade
            deal = self.deal()
            self.quote(deal)
            self.assertEqual(deal["warranty"], 72)
            self.assertEqual(self.rows()[-1]["reason"], "grade_not_selected")
        self.unit["grade"] = "C"
        for text in ("Warranty please", "guarantee", "보증 부탁", "보장 부탁", "I need 6h", "3 hours", "2시간"):
            deal = self.deal()
            self.quote(deal, text)
            self.assertEqual(deal["warranty"], 72)
            self.assertEqual(self.rows()[-1]["reason"], "buyer_requested")
        deal = self.deal(warranty=96)
        self.quote(deal)
        self.assertEqual(deal["warranty"], 96)
        self.assertEqual(self.rows()[-1]["reason"], "existing_warranty")
        for field in ("unit", "sku", "list_price"):
            old = self.unit[field]
            self.unit[field] = None
            deal = self.deal()
            self.quote(deal)
            self.assertEqual(deal["warranty"], 72)
            self.unit[field] = old
        deal = self.deal(kind="food")
        self.assertIsNone(self.quote(deal))
        self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, 9000))

    def test_config_read_is_byte_bounded_and_oversized_config_is_off(self):
        limit = 64 * 1024
        valid = json.dumps(self.cfg).encode("utf-8")
        cases = (valid + b" " * (limit - len(valid)),
                 valid + b" " * (limit + 1 - len(valid)),
                 valid + b" " * (4 * 1024 * 1024),
                 b"x" * (4 * 1024 * 1024),
                 json.dumps({**self.cfg, "salt": "가" * 22000}, ensure_ascii=False).encode("utf-8"))
        for payload in cases:
            for followup in (False, True):
                with self.subTest(bytes=len(payload), followup=followup):
                    self.write_config()
                    deal = self.deal()
                    if followup:
                        self.quote(deal)
                    self.config.write_bytes(payload)
                    before = self.rows() if self.market.exists() else []
                    with measured_reads(self.config) as reads:
                        note = self.quote(deal)
                    self.assertTrue(reads)
                    self.assertTrue(all(0 < requested <= limit + 1 for requested, _ in reads), reads)
                    self.assertLessEqual(sum(size for _, size in reads), limit + 1)
                    self.assertEqual(self.config.read_bytes(), payload)
                    if len(payload) <= limit:
                        self.assertIn("without a warranty", note)
                        self.assertEqual(self.rows()[-1]["arm"], "treatment")
                    else:
                        self.assertIn("72 h warranty", note)
                        self.assertNotIn("warranty_trial", deal)
                        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                        self.assertEqual(self.rows() if self.market.exists() else [], before)

    def test_refund_breakage_and_hour_variants_exclude_first_and_followup_quotes(self):
        requests = ("full refund if it breaks within 6 hrs.", "warrant", "guarantee", "refund",
                    "break", "broke", "broken", "defect", "6 hr", "6 hour", "6 h", "6H",
                    "보증", "보장", "환불", "고장", "교환", "2시간")
        for request in requests:
            for followup in (False, True):
                with self.subTest(request=request, followup=followup):
                    deal = self.deal()
                    if followup:
                        self.quote(deal)
                    # Both first and later quotes inspect text past the prompt limit.
                    note = self.quote(deal, "G00000001 " + "x" * 510 + " " + request)
                    self.assertIn("72 h warranty", note)
                    self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                    self.assertEqual(self.rows()[-1]["arm"], "control")

    def test_request_variants_keep_72h_through_first_or_followup_acceptance(self):
        from google.protobuf.json_format import MessageToDict

        # A free-text request has no price offer. Stub only the model decision;
        # keep request detection, the numeric acceptance and receipt creation real.
        decision = mock.patch.object(seller, "decide_turn", return_value=dict(
            buyer_offer=0, action="counter", price=0, message="Fixture reply."))
        decision.start()
        self.addCleanup(decision.stop)
        self.pricing.write_text(json.dumps({"warranty_hours": 0}))
        for request in WARRANTY_REQUESTS:
            for followup in (False, True):
                with self.subTest(request=request, followup=followup):
                    # Recreate only the synthetic stock/shop; every case is a
                    # fresh eligible grade C unit in the active share=1 window.
                    self.stock = StockBook(load=lambda: {"round": 3, "units": [self.unit]})
                    self.shop = seller.Seller(None, "fixture shop", self.stock)
                    text = "G00000001 " + "x" * 510 + " " + request
                    deal, note = self.opening("G00000001" if followup else text)
                    if followup:
                        self.assertEqual(deal["warranty_trial"]["arm"], "treatment")
                        self.followup(text)
                    else:
                        self.assertIn("72 h warranty", note)
                    row = self.rows()[-1]
                    self.assertEqual(row["arm"], "control")
                    self.assertEqual(row["reason"], "followup_warranty_request" if followup else "buyer_requested")
                    # Accept by price only, after the request: the artifact must
                    # retain control even though this message has no keywords.
                    up = self.followup(str(deal["ask"]))
                    up.complete.assert_awaited_once()
                    receipt = MessageToDict(up.add_artifact.call_args.kwargs["parts"][0].data)
                    self.assertEqual(receipt["warranty_hours"], 72)
                    self.assertEqual(self.rows()[-1]["arm"], "control")
                    for blocked in self.external_guards:
                        blocked.assert_not_called()

    def test_unit_assignment_is_deterministic_across_tasks_and_share_endpoints(self):
        self.write_config(share=0.5)
        arms = []
        for task in ("first-task", "second-task", "first-task"):
            deal = self.deal()
            self.quote(deal, task=task)
            arms.append(self.rows()[-1]["arm"])
        self.assertEqual(len(set(arms)), 1)
        # Known SHA-256 vector starts 394efccc: below the 0.5 threshold.
        self.assertEqual(arms[0], "treatment")
        self.assertEqual(self.rows()[-1]["randomization_unit"], "unit")
        for share, expected in ((0, "control"), (1, "treatment")):
            self.write_config(share=share)
            deal = self.deal()
            self.quote(deal)
            self.assertEqual(self.rows()[-1]["arm"], expected)
            receipt = self.shop.stock_receipt("task-fixture", deal, 9000)
            self.assertEqual(receipt.get("warranty_hours", 0), 72 if share == 0 else 0)

    def test_measurement_exists_before_receipt_and_preserves_join_and_assignment_fields(self):
        deal = self.deal()
        self.quote(deal)
        row = self.rows()[-1]  # already on disk, before constructing a receipt
        self.assertEqual({key: row[key] for key in ("study", "version", "epoch", "ts", "task", "sku", "unit", "grade", "list_price", "offered_hours")},
                         dict(study="grade-warranty-v1", version=1, epoch="fixture-1", ts=150.0,
                              task="task-fixture", sku="G00000001", unit="unit-fixture-c",
                              grade="C", list_price=10000, offered_hours=0))
        receipt = self.shop.stock_receipt("task-fixture", deal, 9000)
        self.assertEqual((receipt["unit"], receipt["claim"], receipt["price"]), (row["unit"], "C", 9000))

    def test_measurement_failure_forces_control_and_logs(self):
        self.market.mkdir()
        (self.market / "measure").write_text("not a directory")
        deal = self.deal()
        with mock.patch.object(seller, "log") as logged:
            self.assertIn("72 h warranty", self.quote(deal))
        self.assertTrue(any("measurement failed" in call.args[0] for call in logged.call_args_list))
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
        self.assertEqual(deal["warranty_trial"]["arm"], "control")

    def test_partial_measurement_tail_forces_control_without_appending(self):
        path = self.market / "measure" / "seller_warranty_trial.jsonl"
        for followup in (False, True):
            with self.subTest(followup=followup):
                path.unlink(missing_ok=True)
                deal = self.deal()
                if followup:
                    self.quote(deal)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'{"partial":')
                with mock.patch.object(seller, "log") as logged:
                    self.assertIn("72 h warranty", self.quote(deal))
                self.assertEqual(path.read_bytes(), b'{"partial":')
                self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                self.assertTrue(any("measurement failed" in c.args[0] for c in logged.call_args_list))

    def test_complete_measurement_tail_accepts_parseable_jsonl(self):
        self.quote(self.deal(), task="first-task")
        self.quote(self.deal(), task="second-task")
        self.assertEqual([row["task"] for row in self.rows()], ["first-task", "second-task"])
        self.assertTrue(all(row["arm"] == "treatment" for row in self.rows()))

    def test_invalid_newline_terminated_tail_forbids_treatment_without_appending(self):
        path = self.market / "measure" / "seller_warranty_trial.jsonl"
        for tail in (b"not-json\n", b'{"partial":\n', b"\n", b"\xff\n", b"x" * 5000 + b"\n"):
            for followup in (False, True):
                with self.subTest(tail=tail[:16], followup=followup):
                    path.unlink(missing_ok=True)
                    deal = self.deal()
                    if followup:
                        self.quote(deal)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("ab") as stream:
                        stream.write(tail)
                    before = path.read_bytes()
                    self.assertIn("72 h warranty", self.quote(deal))
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)

    def test_last_measurement_record_has_a_byte_budget_and_accepts_boundary(self):
        limit = 64 * 1024
        path = self.market / "measure" / "seller_warranty_trial.jsonl"
        cases = ((b'"' + b"x" * (limit - 2) + b'"', True),
                 (b'"' + b"x" * (limit - 1) + b'"', False),
                 (b'"' + b"x" * (4 * 1024 * 1024) + b'"', False),
                 (b"x" * (4 * 1024 * 1024), False),
                 (b'{"ok":true}', True))
        for record, allowed in cases:
            for prefix in (b"", b'{"old":true}\n' * 10000):
                for followup in (False, True):
                    with self.subTest(bytes=len(record), prefix=bool(prefix), followup=followup):
                        path.unlink(missing_ok=True)
                        deal = self.deal()
                        if followup:
                            self.quote(deal)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        before = prefix + record + b"\n"
                        path.write_bytes(before)
                        with measured_reads(path) as reads:
                            note = self.quote(deal)
                        self.assertTrue(reads)
                        self.assertTrue(all(0 < requested <= limit + 1 for requested, _ in reads), reads)
                        # One terminal-newline byte plus a bounded tail window.
                        self.assertLessEqual(sum(size for _, size in reads), limit + 2)
                        if allowed:
                            self.assertIn("without a warranty", note)
                            self.assertTrue(path.read_bytes().startswith(before))
                            self.assertEqual(self.rows()[-1]["arm"], "treatment")
                        else:
                            self.assertIn("72 h warranty", note)
                            self.assertEqual(path.read_bytes(), before)
                            self.assertEqual(deal["warranty_trial"]["reason"], "measurement_failed")
                            self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)

    def test_dynamic_toggle_applies_to_new_talks(self):
        self.write_config(enabled=False)
        first = self.deal()
        self.quote(first)
        self.assertEqual(first["warranty"], 72)
        self.write_config(enabled=True)
        second = self.deal()
        self.assertIn("without a warranty", self.quote(second))
        self.write_config(enabled=False)
        third = self.deal()
        self.quote(third)
        self.assertEqual(third["warranty"], 72)

    def test_disabling_an_open_treatment_restores_baseline_without_scan_or_journal(self):
        deal = self.deal()
        self.quote(deal)
        path = self.market / "measure" / "seller_warranty_trial.jsonl"
        before = path.read_bytes()
        self.write_config(enabled=False)
        with mock.patch.object(seller.seller_warranty_trial, "requested", side_effect=AssertionError("OFF detection")):
            self.assertIn("72 h warranty", self.quote(deal, "refund please"))
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("warranty_trial", deal)
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)

    def followup(self, text, task="00000000-0000-0000-0000-000000000043"):
        from a2a.types import Message
        up = SimpleNamespace(new_agent_message=lambda **kwargs: kwargs,
                             requires_input=mock.AsyncMock(), reject=mock.AsyncMock(), failed=mock.AsyncMock(),
                             add_artifact=mock.AsyncMock(), complete=mock.AsyncMock())
        context = SimpleNamespace(current_task=SimpleNamespace(id=task, context_id="fixture-context"),
                                  message=Message(role="ROLE_USER", parts=[seller.new_text_part(text)], message_id="fixture-message"))
        with mock.patch.object(seller, "TaskUpdater", return_value=up), \
                mock.patch.object(seller, "load_shop_key", return_value=None):
            asyncio.run(self.shop._execute(context, None))
        self.assertFalse(up.reject.called)
        self.assertFalse(up.failed.called)
        return up

    def test_cancel_during_warranty_append_keeps_next_accept_terms_consistent(self):
        from a2a.types import Message
        from google.protobuf.json_format import MessageToDict

        task_id = "00000000-0000-0000-0000-000000000043"
        self.pricing.write_text(json.dumps({"warranty_hours": 0}))
        deal, _ = self.opening("G00000001")
        entered, release, next_done = threading.Event(), threading.Event(), threading.Event()
        path_open = Path.open
        failures = []
        updates = []

        class DelayedAppend:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def write(self, data):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("test did not release warranty append")
                return self.stream.write(data)

        def delayed_open(path, *args, **kwargs):
            stream = path_open(path, *args, **kwargs)
            if path.name == "seller_warranty_trial.jsonl" and args and args[0] == "a+b":
                return DelayedAppend(stream)
            return stream

        def updater(**kwargs):
            async def publish(message):
                # Model the transport yielding while publishing the response.
                await asyncio.sleep(0)

            up = SimpleNamespace(new_agent_message=lambda **kw: kw,
                                 requires_input=mock.AsyncMock(side_effect=publish), reject=mock.AsyncMock(), failed=mock.AsyncMock(),
                                 add_artifact=mock.AsyncMock(), complete=mock.AsyncMock())
            updates.append(up)
            return up

        def context(text):
            return SimpleNamespace(current_task=SimpleNamespace(id=task_id, context_id="fixture-context"),
                                   message=Message(role="ROLE_USER", parts=[seller.new_text_part(text)],
                                                   message_id="fixture-message"))

        async def run():
            loop = asyncio.get_running_loop()
            gaps, running = [], True

            async def heartbeat():
                previous = loop.time()
                while running:
                    await asyncio.sleep(0.01)
                    now = loop.time()
                    gaps.append(now - previous)
                    previous = now

            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.03)
            first = asyncio.create_task(self.shop.execute(context("I cannot accept this without a warranty"), None))

            def cancel_during_append():
                try:
                    if not entered.wait(2):
                        failures.append("warranty append was not reached")
                        return
                    loop.call_soon_threadsafe(first.cancel)
                    loop.call_soon_threadsafe(lambda: loop.call_later(0.02, first.cancel))
                    # Hold the write open through repeated cancellation. The next
                    # turn may start only after the worker has committed control.
                    next_done.wait(0.2)
                finally:
                    release.set()

            canceller = threading.Thread(target=cancel_during_append)
            canceller.start()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await first
                await self.shop.execute(context(str(deal["ask"])), None)
            finally:
                next_done.set()
                release.set()
                await asyncio.to_thread(canceller.join, 2)
                self.assertFalse(canceller.is_alive())
                running = False
                await beat
            gap_ms = max(gaps) * 1000
            print(f"CANCEL_HEARTBEAT repeated_cancel=True injected_ms=200 max_gap_ms={gap_ms:.3f}", flush=True)
            self.assertLess(gap_ms, 50)

        with mock.patch.object(Path, "open", delayed_open), \
                mock.patch.object(seller, "TaskUpdater", side_effect=updater), \
                mock.patch.object(seller, "load_shop_key", return_value=None):
            asyncio.run(run())
        self.assertEqual(failures, [])
        self.assertEqual(len(updates), 2)
        up = updates[-1]
        up.complete.assert_awaited_once()
        self.assertFalse(up.reject.called)
        self.assertFalse(up.failed.called)
        receipt = MessageToDict(up.add_artifact.call_args.kwargs["parts"][0].data)
        self.assertEqual(receipt.get("warranty_hours"), 72)
        self.assertNotIn("without a warranty", up.complete.call_args.args[0]["parts"][0].text)
        self.assertEqual((deal["warranty_trial"]["arm"], deal["warranty"]), ("control", 72))
        self.assertEqual([row["arm"] for row in self.rows()], ["treatment", "control", "control"])

    def test_quote_and_followup_warranty_io_finish_in_worker(self):
        main_thread = threading.get_ident()
        path_open = Path.open
        calls = []

        def checked_open(path, *args, **kwargs):
            if path == self.config and args and args[0] == "rb":
                calls.append(("config", threading.get_ident()))
            if path.name == "seller_warranty_trial.jsonl" and args and args[0] == "a+b":
                calls.append(("journal", threading.get_ident()))
            return path_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", checked_open):
            deal, _ = self.opening("G00000001")
            self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, deal["ask"]))
            self.followup("x" * 510 + " full refund if broken within 6 hrs")
            self.assertEqual(self.rows()[-1]["arm"], "control")
            self.assertEqual(self.shop.stock_receipt("task-fixture", deal, deal["ask"])["warranty_hours"], 72)
        self.assertEqual([name for name, _ in calls], ["config", "journal", "journal"])
        self.assertTrue(all(thread != main_thread for _, thread in calls), calls)

    def test_off_quotes_count_config_io_and_never_reread_unchanged_config(self):
        path_open, path_stat = Path.open, Path.stat
        for mode in ("missing", "disabled", "outside_window"):
            with self.subTest(mode=mode):
                if mode == "missing":
                    self.config.unlink(missing_ok=True)
                else:
                    self.write_config(enabled=mode != "disabled", stop_at=150 if mode == "outside_window" else 200)
                counts = dict(stat=0, open=0, journal=0)

                def opened(path, *args, **kwargs):
                    if path == self.config:
                        counts["open"] += 1
                    if path.name == "seller_warranty_trial.jsonl":
                        counts["journal"] += 1
                    return path_open(path, *args, **kwargs)

                def stated(path, *args, **kwargs):
                    if path == self.config:
                        counts["stat"] += 1
                    return path_stat(path, *args, **kwargs)

                with mock.patch.object(Path, "open", opened), mock.patch.object(Path, "stat", stated), \
                        mock.patch.object(seller.seller_warranty_trial, "requested",
                                          side_effect=AssertionError("OFF must not scan")):
                    self.quote(self.deal())  # cold observation, reported separately
                    cold = counts.copy()
                    counts.update(stat=0, open=0, journal=0)
                    for index in range(5):
                        deal = self.deal()
                        self.assertIn("72 h warranty", self.quote(deal, task=f"off-{index}"))
                        self.assertNotIn("warranty_trial", deal)
                    print(f"OFF_IO mode={mode} cold={cold} repeated_total={counts} quotes=5 "
                          f"per_quote_stat={counts['stat']/5} per_quote_open={counts['open']/5}", flush=True)
                    self.assertEqual(counts, dict(stat=5, open=0, journal=0))
                self.assertFalse(self.market.exists())

    def test_delayed_warranty_io_keeps_heartbeat_under_50ms(self):
        from a2a.types import Message

        task_id = "00000000-0000-0000-0000-000000000043"
        path_open, path_stat = Path.open, Path.stat
        for mode in ("on_config", "on_append", "off_missing_stat", "off_disabled", "off_window"):
            for delay in (0, 0.2):
                with self.subTest(mode=mode, delay=delay):
                    self.stock = StockBook(load=lambda: {"round": 3, "units": [self.unit]})
                    self.shop = seller.Seller(None, "fixture shop", self.stock)
                    # A different path for each measurement prevents cache warming
                    # from hiding the real delayed read, including disabled config.
                    self.config = self.root / f"config-{mode}-{delay}.json"
                    self.write_config(enabled=mode != "off_disabled", stop_at=150 if mode == "off_window" else 200)
                    if mode == "off_missing_stat":
                        self.config.unlink()
                    calls = []

                    class SlowAppend:
                        def __init__(self, stream):
                            self.stream = stream

                        def __enter__(self):
                            self.stream.__enter__()
                            return self

                        def __exit__(self, *args):
                            return self.stream.__exit__(*args)

                        def __getattr__(self, name):
                            return getattr(self.stream, name)

                        def write(self, data):
                            calls.append("write")
                            time.sleep(delay)
                            return self.stream.write(data)

                    def opened(path, *args, **kwargs):
                        if path == self.config and mode != "on_append":
                            calls.append("open")
                            time.sleep(delay)
                        stream = path_open(path, *args, **kwargs)
                        if path.name == "seller_warranty_trial.jsonl" and args and args[0] == "a+b" and mode == "on_append":
                            return SlowAppend(stream)
                        return stream

                    def stated(path, *args, **kwargs):
                        if path == self.config and mode == "off_missing_stat":
                            calls.append("stat")
                            time.sleep(delay)
                        return path_stat(path, *args, **kwargs)

                    up = SimpleNamespace(new_agent_message=lambda **kwargs: kwargs,
                                         requires_input=mock.AsyncMock(), reject=mock.AsyncMock(), failed=mock.AsyncMock())

                    def context(text):
                        return SimpleNamespace(current_task=SimpleNamespace(id=task_id, context_id="fixture-context"),
                                               message=Message(role="ROLE_USER", parts=[seller.new_text_part(text)],
                                                               message_id="fixture-message"))

                    async def run():
                        loop = asyncio.get_running_loop()
                        gaps = []
                        running = True

                        async def heartbeat():
                            previous = loop.time()
                            while running:
                                await asyncio.sleep(0.01)
                                now = loop.time()
                                gaps.append(now - previous)
                                previous = now

                        beat = asyncio.create_task(heartbeat())
                        try:
                            await asyncio.sleep(0.03)  # heartbeat is armed before I/O
                            await self.shop._execute(context("G00000001"), None)
                            await self.shop._execute(context("Please include a warranty"), None)
                            await asyncio.sleep(0.03)
                        finally:
                            running = False
                            await beat
                        return max(gaps) * 1000

                    with mock.patch.dict(os.environ, SELLER_WARRANTY_TRIAL_CONFIG=str(self.config)), \
                            mock.patch.object(Path, "open", opened), mock.patch.object(Path, "stat", stated), \
                            mock.patch.object(seller, "TaskUpdater", return_value=up), \
                            mock.patch.object(seller, "decide_turn", return_value=dict(
                                buyer_offer=0, action="counter", price=0, message="Fixture reply.")):
                        gap_ms = asyncio.run(run())
                    print(f"HEARTBEAT mode={mode} injected_ms={delay*1000:.0f} max_gap_ms={gap_ms:.3f} calls={calls}", flush=True)
                    self.assertTrue(calls, "delay must reach actual I/O")
                    up.reject.assert_not_awaited()
                    up.failed.assert_not_awaited()
                    self.assertEqual(up.requires_input.await_count, 2)
                    first = up.requires_input.call_args_list[0].args[0]["parts"][0].text
                    last = up.requires_input.call_args_list[-1].args[0]["parts"][0].text
                    deal = self.shop.open[task_id]
                    if mode.startswith("on_"):
                        self.assertIn("without a warranty", first)
                        self.assertIn("72 h warranty", last)
                        self.assertEqual(self.rows()[-1]["arm"], "control")
                    else:
                        self.assertIn("72 h warranty", first)
                        self.assertNotIn("warranty_trial", deal)
                    self.assertEqual(self.shop.stock_receipt(task_id, deal, deal["ask"])["warranty_hours"], 72)
                    self.assertLess(gap_ms, 50, f"loop blocked by {mode}: {gap_ms:.3f}ms")

    def test_journal_contention_keeps_heartbeat_and_public_quotes_consistent(self):
        from a2a.types import Message

        entered, competing = threading.Event(), threading.Event()
        units = [self.unit, {**self.unit, "unit": "unit-second-c"}]
        self.stock = StockBook(load=lambda: {"round": 3, "units": units})
        self.shop = seller.Seller(None, "fixture shop", self.stock)
        path_open = Path.open
        assignment = seller.seller_warranty_trial.Trial._assignment
        published = []
        testcase = self

        def assigned(trial, task, *args, **kwargs):
            row = assignment(trial, task, *args, **kwargs)
            if task == "contending-task":
                competing.set()
            return row

        class SlowAppend:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def write(self, data):
                if not entered.is_set():
                    entered.set()
                    if not competing.wait(2):
                        raise AssertionError("second assignment must reach the held journal lock")
                    time.sleep(0.2)
                return self.stream.write(data)

        def opened(path, *args, **kwargs):
            stream = path_open(path, *args, **kwargs)
            if path.name == "seller_warranty_trial.jsonl" and args and args[0] == "a+b":
                return SlowAppend(stream)
            return stream

        class Updates:
            def __init__(self, task_id, **kwargs):
                self.task_id = task_id

            def new_agent_message(self, parts, **kwargs):
                return parts[0].text

            async def requires_input(self, text):
                deal = testcase.shop.open[self.task_id]
                row = next(row for row in testcase.rows() if row["task"] == self.task_id)
                testcase.assertEqual((row["arm"], deal["warranty_trial"]["arm"]), ("treatment", "treatment"))
                testcase.assertNotIn("warranty_hours", testcase.shop.stock_receipt(self.task_id, deal, deal["ask"]))
                testcase.assertIn("without a warranty", text)
                published.append(self.task_id)

            async def reject(self, text):
                testcase.fail(f"Unexpected rejection: {text}")

        def context(task):
            return SimpleNamespace(current_task=SimpleNamespace(id=task, context_id="fixture-context"),
                                   message=Message(role="ROLE_USER", parts=[seller.new_text_part("G00000001")],
                                                   message_id="fixture-message"))

        async def run():
            loop = asyncio.get_running_loop()
            gaps, running = [], True

            async def heartbeat():
                previous = loop.time()
                while running:
                    await asyncio.sleep(0.01)
                    now = loop.time()
                    gaps.append(now - previous)
                    previous = now

            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.03)
            first = asyncio.create_task(self.shop._execute(context("holding-task"), None))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                second = asyncio.create_task(self.shop._execute(context("contending-task"), None))
                await asyncio.gather(first, second)
            finally:
                competing.set()
                await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
                running = False
                await beat
            gap_ms = max(gaps) * 1000
            print(f"CONTENTION_HEARTBEAT injected_ms=200 max_gap_ms={gap_ms:.3f} published={len(published)}", flush=True)
            self.assertLess(gap_ms, 50)

        with mock.patch.object(Path, "open", opened), \
                mock.patch.object(seller.seller_warranty_trial.Trial, "_assignment", assigned), \
                mock.patch.object(seller, "TaskUpdater", Updates):
            asyncio.run(run())
        self.assertCountEqual(published, ["holding-task", "contending-task"])
        self.assertEqual(len({row["unit"] for row in self.rows()}), 2)

    def test_cached_config_checks_clock_and_retries_transient_open_failures(self):
        trial = seller.seller_warranty_trial
        with measured_reads(self.config) as reads:
            self.assertIsNone(trial._config(150)[1])
            self.assertEqual(trial._config(200)[1], "outside_window")
            self.assertEqual(trial._config(99)[1], "outside_window")
            self.assertIsNone(trial._config(100)[1])
        self.assertEqual(len(reads), 1)
        self.write_config(enabled=False)
        with mock.patch.object(Path, "open", side_effect=OSError("transient read failure")) as opened:
            self.assertEqual(trial._config(150)[1], "config_unavailable")
            opened.assert_called_once_with("rb")
        with measured_reads(self.config) as reads:
            self.assertEqual(trial._config(150)[1], "disabled")
        self.assertEqual(len(reads), 1)

    def test_concurrent_followups_publish_control_after_delayed_treatment(self):
        from a2a.types import Message
        task_id = "00000000-0000-0000-0000-000000000043"
        deal, _ = self.opening("G00000001")
        entered, release = threading.Event(), threading.Event()
        dumps = json.dumps
        messages = []

        def delayed_encode(value, *args, **kwargs):
            if isinstance(value, dict) and value.get("task") == task_id and value.get("arm") == "treatment":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release treatment serialization")
            return dumps(value, *args, **kwargs)

        class Updates:
            def __init__(self, **kwargs):
                pass

            def new_agent_message(self, parts, **kwargs):
                return parts[0].text

            async def requires_input(self, message):
                messages.append((message, deal["warranty_trial"]["arm"],
                                 self_shop.stock_receipt(task_id, deal, deal["ask"]).get("warranty_hours", 0)))

        self_shop = self.shop

        def context(text):
            return SimpleNamespace(current_task=SimpleNamespace(id=task_id, context_id="fixture-context"),
                                   message=Message(role="ROLE_USER", parts=[seller.new_text_part(text)],
                                                   message_id="fixture-message"))

        async def run():
            loop = asyncio.get_running_loop()
            first = asyncio.create_task(self.shop._execute(context("Please tell me more"), None))
            second, failures = [], []

            def queue_control():
                try:
                    if not entered.wait(2):
                        failures.append("treatment serialization was not reached")
                        return
                    second.append(asyncio.run_coroutine_threadsafe(
                        self.shop._execute(context("I cannot accept this without a warranty"), None), loop))
                finally:
                    release.set()

            competing = threading.Thread(target=queue_control)
            competing.start()
            try:
                await first
            finally:
                release.set()
                await asyncio.to_thread(competing.join, 2)
                self.assertFalse(competing.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(second), 1)
            await asyncio.wrap_future(second[0])

        with mock.patch.object(json, "dumps", delayed_encode), mock.patch.object(seller, "TaskUpdater", Updates):
            asyncio.run(run())
        self.assertEqual(deal["warranty_trial"]["arm"], "control")
        self.assertEqual(messages[-1][1:], ("control", 72))
        self.assertIn("72 h warranty", messages[-1][0])
        seen_control = False
        for message, arm, hours in messages:
            if arm == "control":
                seen_control = True
            if seen_control:
                self.assertNotIn("without a warranty", message)
                self.assertEqual((arm, hours), ("control", 72))
        self.assertEqual([row["arm"] for row in self.rows()], ["treatment", "treatment", "control"])

    def test_slow_warranty_io_keeps_unit_held_and_journal_precedes_public_quote(self):
        entered, release = threading.Event(), threading.Event()
        path_open = Path.open
        messages = []
        testcase = self
        first_id, second_id = "first-task", "second-task"

        def slow_open(path, *args, **kwargs):
            if path == self.config and args and args[0] == "rb" and not entered.is_set():
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release measurement worker")
            return path_open(path, *args, **kwargs)

        class Updates:
            def __init__(self, task):
                self.task = task

            async def requires_input(self, text):
                testcase.assertEqual(testcase.rows()[-1]["task"], self.task)
                messages.append((self.task, "quote"))

            async def reject(self, text):
                messages.append((self.task, "reject"))

        async def run():
            loop = asyncio.get_running_loop()
            first = asyncio.create_task(self.shop.open_from_stock(SimpleNamespace(id=first_id), "G00000001",
                                        Updates(first_id), lambda text, ask=None: text))
            second, failures = [], []

            def queue_competing_quote():
                try:
                    if not entered.wait(2):
                        raise AssertionError("warranty config read was not reached")
                    self.assertEqual(self.stock.holder(self.unit["unit"]), first_id)
                    self.assertEqual(messages, [])
                    self.assertFalse((self.market / "measure" / "seller_warranty_trial.jsonl").exists())
                    second.append(asyncio.run_coroutine_threadsafe(
                        self.shop.open_from_stock(SimpleNamespace(id=second_id), "G00000001",
                                                  Updates(second_id), lambda text, ask=None: text), loop))
                except Exception as error:
                    failures.append(str(error))
                finally:
                    release.set()

            competing = threading.Thread(target=queue_competing_quote)
            competing.start()
            try:
                await first
            finally:
                release.set()
                await asyncio.to_thread(competing.join, 2)
                self.assertFalse(competing.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(second), 1)
            await asyncio.wrap_future(second[0])
            self.assertCountEqual(messages, [(second_id, "reject"), (first_id, "quote")])
            self.assertNotIn(second_id, self.shop.open)
            self.assertEqual(self.rows()[-1]["arm"], "treatment")

        with mock.patch.object(Path, "open", slow_open):
            asyncio.run(run())

    def test_followup_control_terms_and_receipts_equal_bounded_3de0399(self):
        from testdata import seller_warranty_baseline_3de0399 as baseline
        task = "00000000-0000-0000-0000-000000000043"
        self.pricing.write_text(json.dumps({"warranty_hours": 0}))
        for mode in ("off", "off_missing", "off_window", "control", "ineligible"):
            with self.subTest(mode=mode):
                self.write_config(enabled=mode != "off", share=0 if mode == "control" else 1,
                                  stop_at=150 if mode == "off_window" else 200)
                if mode == "off_missing":
                    self.config.unlink()
                self.unit["grade"] = "A" if mode == "ineligible" else "C"
                original = self.deal()
                expected = copy.deepcopy(original)
                text = "x" * 510 + " without warranty"
                baseline.warranty_terms(self.shop, expected, text[:seller.MAX_TEXT])
                self.shop.open[task] = original
                self.stock.hold(self.unit["unit"], task)
                self.followup(text)
                self.assertEqual(original["warranty"], expected["warranty"])
                self.assertEqual(self.shop.stock_receipt(task, original, 9000),
                                 baseline.stock_receipt(self.shop, task, expected, 9000))
                self.shop.free(task, original)
                self.shop.open.clear()

    def test_off_is_inert_and_oversized_numeric_input_keeps_control_quickly(self):
        variants = (None, "{", {**self.cfg, "enabled": False}, {**self.cfg, "start_at": 151},
                    {**self.cfg, "stop_at": 150})
        for cfg in variants:
            with self.subTest(config=cfg):
                if cfg is None:
                    self.config.unlink(missing_ok=True)
                else:
                    self.config.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg))
                # Detector and journal are forbidden work when the switch is OFF.
                with mock.patch.object(seller.seller_warranty_trial, "requested", side_effect=AssertionError("OFF detection")):
                    deal = self.deal()
                    self.assertIn("72 h warranty", self.quote(deal, "9" * 100_000))
                self.assertNotIn("warranty_trial", deal)
                self.assertFalse(self.market.exists())
        self.write_config(enabled=True)
        for size in (8001, 100_000, 1_000_000):
            started = time.perf_counter()
            deal = self.deal()
            self.assertIn("72 h warranty", self.quote(deal, "9" * size))
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.5, f"numeric input size={size}, seconds={elapsed}")
            self.assertEqual(self.rows()[-1]["reason"], "buyer_requested")

    def test_deep_or_unexpected_config_failure_is_inert_baseline(self):
        self.config.write_text("[" * 2000 + "0" + "]" * 2000)
        deal, text = self.opening("G00000001", measured=False)
        self.assertIn("72 h warranty", text)
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, deal["ask"])["warranty_hours"], 72)
        self.assertNotIn("warranty_trial", deal)
        path_open = Path.open
        for error in (RuntimeError("unexpected read failure"), RecursionError("nested config"), UnicodeError("bad encoding")):
            self.write_config()  # Valid bytes: failure must come from the injected read.
            calls = []

            def broken(path, *args, **kwargs):
                if path == self.config:
                    calls.append((path, args))
                    raise error
                return path_open(path, *args, **kwargs)
            with self.subTest(error=type(error).__name__), mock.patch.object(Path, "open", broken):
                deal = self.deal()
                self.assertIn("72 h warranty", self.quote(deal))
                self.assertNotIn("warranty_trial", deal)
                self.assertEqual(calls, [(self.config, ("rb",))])
        self.assertFalse(self.market.exists())

    def test_unencodable_config_strings_keep_opening_and_receipt_control(self):
        for field in ("salt", "study", "epoch"):
            with self.subTest(field=field):
                valid = self.cfg[field]
                self.write_config(**{field: "\ud800"})
                try:
                    deal, text = self.opening("G00000001", measured=False)
                    self.assertIn("72 h warranty", text)
                    self.assertEqual(deal["warranty"], 72)
                    self.assertEqual(self.shop.stock_receipt("task-fixture", deal, deal["ask"])["warranty_hours"], 72)
                    self.assertNotIn("warranty_trial", deal)
                    self.assertFalse(self.market.exists())
                finally:
                    self.stock.release(self.unit["unit"])
                    self.shop.open.clear()
                    self.write_config(**{field: valid})

    def test_unexpected_assignment_detection_and_journal_errors_restore_control(self):
        failures = (("_assignment", seller.seller_warranty_trial.Trial),
                    ("requested", seller.seller_warranty_trial),
                    ("sha256", seller.seller_warranty_trial.hashlib),
                    ("dumps", json))
        for name, target in failures:
            for followup in (False, True):
                if followup and name in ("_assignment", "sha256"):
                    continue  # Existing assignments do not hash again.
                with self.subTest(boundary=name, followup=followup):
                    deal = self.deal()
                    if followup:
                        self.quote(deal)
                    with mock.patch.object(target, name, side_effect=RuntimeError("injected trial failure")):
                        note = self.quote(deal, "Please quote G00000001" if name in ("_assignment", "sha256")
                                          else "I cannot accept this without a warranty")
                    self.assertIn("72 h warranty", note or "")
                    self.assertEqual(deal["warranty"], 72)
                    self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                    self.quote(deal, "9000")
                    self.assertEqual(deal["warranty_trial"]["arm"], "control")

    def opening(self, text, task="00000000-0000-0000-0000-000000000043", *, measured=True):
        messages = []
        testcase = self

        class Updates:
            async def requires_input(self, message):
                if measured:
                    testcase.assertTrue(testcase.rows())
                messages.append(message)

            async def reject(self, message):
                testcase.fail(f"Unexpected rejection: {message}")

        asyncio.run(self.shop.open_from_stock(SimpleNamespace(id=task), text, Updates(), lambda text, ask=None: text))
        return self.shop.open[task], messages[0]

    def test_off_control_ineligible_opening_and_receipt_equal_3de0399(self):
        from testdata import seller_warranty_baseline_3de0399 as baseline
        task = "00000000-0000-0000-0000-000000000043"
        self.pricing.write_text(json.dumps({"warranty_hours": 0}))
        for mode in ("off", "off_missing", "off_window", "control", "ineligible"):
            for text in ("G00000001 " + "x" * 510 + " without warranty",
                         "G00000001 without warranty", "G00000001 6 hrs refund if broken"):
                with self.subTest(mode=mode, text=text[-32:]):
                    self.write_config(enabled=mode != "off", share=0 if mode == "control" else 1,
                                      stop_at=150 if mode == "off_window" else 200)
                    if mode == "off_missing":
                        self.config.unlink()
                    self.unit["grade"] = "A" if mode == "ineligible" else "C"
                    with mock.patch.object(seller.Seller, "warranty_terms",
                                           lambda shop, deal, text, **kw: baseline.warranty_terms(shop, deal, text[:seller.MAX_TEXT])):
                        expected, expected_text = self.opening(text, measured=False)
                    expected_receipt = baseline.stock_receipt(self.shop, task, expected, expected["ask"])
                    self.shop.free(task, expected)
                    self.shop.open.clear()
                    actual, actual_text = self.opening(text, measured=False)
                    self.assertEqual({k: v for k, v in actual.items() if k != "warranty_trial"}, expected)
                    self.assertEqual(actual_text, expected_text)
                    self.assertEqual(self.shop.stock_receipt(task, actual, actual["ask"]), expected_receipt)
                    self.shop.free(task, actual)
                    self.shop.open.clear()

    def test_full_opening_keeps_price_claim_and_cert_and_measurement_precedes_quote(self):
        control, control_text = None, None
        for share in (0, 1):
            self.write_config(share=share)
            deal, text = self.opening("Please quote G00000001")
            receipt = self.shop.stock_receipt("task-fixture", deal, deal["ask"])
            if share == 0:
                control = {key: copy.deepcopy(deal[key]) for key in ("id", "unit", "cost", "reserve", "ask", "open", "break", "expires_at", "history")}
                control_text = text
                self.assertEqual(receipt["warranty_hours"], 72)
                self.shop.free("00000000-0000-0000-0000-000000000043", deal)
                self.shop.open.clear()
            else:
                self.assertEqual({key: deal[key] for key in control}, control)
                self.assertEqual(receipt["claim"], "C")
                self.assertNotIn("warranty_hours", receipt)
                self.assertIn("without a warranty", text)
                self.assertEqual(text.replace("This grade C unit is sold as is, without a warranty.", "TERMS"),
                                 control_text.replace("This unit comes with a 72 h warranty: if it breaks within 72 h, you get the full price back.", "TERMS"))
        self.assertEqual(self.unit["cert"], UNIT["cert"])

    def test_warranty_request_after_message_limit_still_keeps_baseline(self):
        deal, text = self.opening("G00000001 " + "x" * 510 + " Please include a warranty.")
        self.assertIn("72 h warranty", text)
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, deal["ask"])["warranty_hours"], 72)

    def test_collected_settle_fixture_and_no_warranty_receipt_join_by_string_unit(self):
        # Actual captured settle schema, anonymized without changing field types.
        # This is a join-contract check, not a claim that a live settlement ran.
        fixture = json.loads((Path(__file__).parent / "testdata" / "seller_warranty_trial_settle.json").read_text())
        compact = seller.market_model.compact(fixture)
        deal = self.deal()
        self.quote(deal)
        receipt = self.shop.stock_receipt("task-fixture", deal, 9000)
        self.assertEqual((compact["unit"], receipt["unit"], self.rows()[-1]["unit"]),
                         ("unit-fixture-c", "unit-fixture-c", "unit-fixture-c"))
        self.assertIsInstance(compact["warranty"], float)
        self.assertNotIn("warranty_hours", receipt)


if __name__ == "__main__":
    unittest.main()
