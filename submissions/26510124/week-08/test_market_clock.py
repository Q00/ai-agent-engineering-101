"""Observe existing responses without adding HTTP or changing host timestamps."""
from datetime import datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import time

import httpx

from inventory import Inventory
from market_clock import capture_response, install_market_clock, schedule_now
from restock import supply_window

ROOT = Path(__file__).resolve().parent
EPOCH = 1793898000


def response(epoch=EPOCH, *, headers=None, body=None, status=200):
    return httpx.Response(status, headers={
        "Date": format_datetime(datetime.fromtimestamp(epoch, timezone.utc), usegmt=True),
        **(headers or {})}, json=body or {"round": 3, "restock": {
            "open": True, "opens_at": epoch - 10, "closes_at": epoch + 180}})


class MarketClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        boot = patch("market_clock._boot_id", return_value="fixture-boot")
        boot.start()
        self.addCleanup(boot.stop)

    def sample(self, offset=600, **kwargs):
        return capture_response(self.md, response(**kwargs), round_trip=0.2,
                                received_wall=EPOCH + offset, received_monotonic=1000)

    def test_ahead_and_behind_host_use_market_window_and_keep_offset_direction(self):
        for offset in (600, -600, 776):
            with self.subTest(offset=offset):
                self.assertEqual(self.sample(offset), "recorded")
                clock = schedule_now(self.md, 3, local_now=EPOCH + offset + 10, monotonic_now=1010)
                self.assertEqual(clock["status"], "synced")
                self.assertAlmostEqual(clock["now"], EPOCH + 11.2)
                self.assertAlmostEqual(clock["offset_seconds"], 0.6 - offset, delta=1e-6)
                self.assertAlmostEqual(clock["uncertainty_seconds"], 1.2)
                self.assertEqual(supply_window(response().json(), clock["now"])["status"], "window_open")
                self.assertEqual(supply_window(response().json(), EPOCH + offset)["status"], "window_closed")

    def test_elapsed_monotonic_time_closes_window_conservatively(self):
        body = {"round": 3, "restock": {"open": True, "opens_at": EPOCH - 10, "closes_at": EPOCH + 30}}
        self.sample(body=body)
        clock = schedule_now(self.md, 3, local_now=EPOCH + 620, monotonic_now=1020)
        self.assertEqual(supply_window(body, clock["now"])["status"], "window_closing")
        body["restock"]["open"] = False
        self.assertEqual(supply_window(body, clock["now"])["status"], "window_closed")

    def test_missing_stale_and_wrong_round_return_labelled_local_fallback(self):
        local = EPOCH + 600
        self.assertEqual(schedule_now(self.md, 3, local_now=local, monotonic_now=1000), {
            "now": local, "status": "missing", "offset_seconds": None, "uncertainty_seconds": None})
        self.sample()
        for rnd, elapsed, expected in ((3, 46, "stale"), (4, 1, "round_mismatch")):
            clock = schedule_now(self.md, rnd, local_now=local + elapsed, monotonic_now=1000 + elapsed)
            self.assertEqual(clock["status"], expected)
            self.assertEqual(clock["now"], local + elapsed)

    def test_clock_jump_monotonic_reset_and_changed_boot_invalidate_sample(self):
        self.sample()
        for wall, mono in ((EPOCH + 640, 1010), (EPOCH + 601, 5)):
            self.assertEqual(schedule_now(self.md, 3, local_now=wall, monotonic_now=mono)["status"], "clock_changed")
        with patch("market_clock._boot_id", return_value="next-boot"):
            self.assertEqual(schedule_now(self.md, 3, local_now=EPOCH + 601, monotonic_now=1001)["status"], "clock_changed")

    def test_unreliable_responses_do_not_replace_last_trusted_sample(self):
        self.sample()
        before = (self.md / "market-clock.json").read_bytes()
        cases = [
            (response(headers={"Age": "1"}), 0.2, "cached_response"),
            (response(headers={"Age": "bad"}), 0.2, "cached_response"),
            (response(headers={"CF-Cache-Status": "HIT"}), 0.2, "cached_response"),
            (response(headers={"Date": "not a date"}), 0.2, "invalid_date"),
            (response(), 5.01, "invalid_timing"),
            (response(status=401), 0.2, "http_error"),
            (response(body={"round": True}), 0.2, "invalid_response"),
            (response(body={"round": 3, "restock": {"open": True,
                "opens_at": EPOCH + 600, "closes_at": EPOCH + 780}}), 0.2, "date_schedule_mismatch"),
        ]
        for item, rtt, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(capture_response(self.md, item, round_trip=rtt,
                    received_wall=EPOCH + 600, received_monotonic=1000), expected)
                self.assertEqual((self.md / "market-clock.json").read_bytes(), before)
        self.assertEqual(self.sample(headers={"Age": "0"}), "recorded")

    def test_bounded_corrupt_clock_file_does_not_change_local_fallback(self):
        for raw in ("{broken", "[]", "x" * 4097):
            (self.md / "market-clock.json").write_text(raw)
            clock = schedule_now(self.md, 3, local_now=EPOCH, monotonic_now=1000)
            self.assertEqual((clock["status"], clock["now"]), ("invalid", EPOCH))

    def test_clock_sample_does_not_change_inventory_file_age_or_global_clock(self):
        inventory = self.md / "inventory.json"
        inventory.write_text('{"round":3,"units":[]}')
        before = inventory.stat().st_mtime_ns
        local, mono = time.time(), time.monotonic()
        function = time.time
        capture_response(self.md, response(epoch=int(local) - 600), round_trip=0.2,
                         received_wall=local, received_monotonic=mono)
        clock = schedule_now(self.md, 3, local_now=local, monotonic_now=mono)
        self.assertEqual(clock["status"], "synced")
        self.assertLess(clock["now"], local - 598)
        self.assertIs(time.time, function)
        self.assertEqual(inventory.stat().st_mtime_ns, before)
        self.assertEqual(Inventory(self.md).snapshot()["round"], 3)
        self.assertEqual(set(json.loads((self.md / "market-clock.json").read_text())), {
            "version", "round", "market_epoch", "received_wall", "received_monotonic", "round_trip", "boot_id"})

    def test_wrapper_is_idempotent_and_only_observes_existing_inventory_call(self):
        calls, returned = [], response()
        class Market:
            def call(self, method, path, role="buyer", **kwargs):
                calls.append((method, path, role, kwargs))
                return returned
        kit = SimpleNamespace(Market=Market)
        install_market_clock(kit, self.md)
        install_market_clock(kit, self.md)
        with patch("market_clock.capture_response") as capture:
            market = Market()
            self.assertIs(market.call("GET", "/api/inventory", "shop"), returned)
            market.call("POST", "/api/supply", "shop", json={"sku": "fixture"})
            market.call("GET", "/api/orders", "buyer")
        self.assertEqual(len(calls), 3)
        capture.assert_called_once()
        self.assertIs(capture.call_args.args[1], returned)

    def test_capture_failure_preserves_response_and_original_errors_still_propagate(self):
        returned = response()
        class Market:
            def call(self, method, path, role="buyer", **kwargs):
                if kwargs.get("fail"):
                    raise RuntimeError("request failed")
                return returned
        install_market_clock(SimpleNamespace(Market=Market), self.md)
        with patch("market_clock.capture_response", side_effect=OSError("disk full")):
            self.assertIs(Market().call("GET", "/api/inventory", "shop"), returned)
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            Market().call("GET", "/api/inventory", "shop", fail=True)

    def test_wall_clock_step_during_http_call_does_not_record_sample(self):
        class Market:
            def call(self, *args, **kwargs):
                return response()
        install_market_clock(SimpleNamespace(Market=Market), self.md)
        with patch("market_clock.time.monotonic", side_effect=[1000, 1001]), \
                patch("market_clock.time.time", side_effect=[EPOCH, EPOCH + 601]), \
                patch("market_clock.capture_response") as capture:
            Market().call("GET", "/api/inventory", "shop")
        capture.assert_not_called()
