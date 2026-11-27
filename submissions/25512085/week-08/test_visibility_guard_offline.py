"""No live market, processes, keys or existing output files are used by these tests."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import httpx

import guarded_market_kit as adapter
import restock
import visibility_guard as vg
from test_unified_buying_offline import BuyingFixture, NOW

URL = "https://own.invalid"
NEW_URL = "https://new.invalid"


class FakeMarket:
    def __init__(self, base, owner=None):
        self.base, self.owner = base, owner
        self.token, self.joined = {}, {}
        self.joins, self.calls = [], []
        self.response = 200
        self.join_error = None
        self.join_hook = None
        self.call_hook = None

    def join(self, role, name, key, url=None, tries=6):
        if self.join_hook:
            self.join_hook()
        self.joins.append((role, name, key, url, tries))
        if self.join_error:
            raise self.join_error
        self.token[role] = f"test-token-{len(self.joins)}"
        self.joined[role] = name, key, url
        return {"name": name, "round": 3}

    def call(self, method, path, role="buyer", **kw):
        if self.call_hook:
            self.call_hook()
        self.calls.append((method, path, role, kw, self.token.get(role)))
        return httpx.Response(self.response, json={})


def fake_kit():
    def watchdog(a, agent, d, market, keys, procs, proxy, sport, pport, state, every=60):
        return proxy

    def cmd_join(a):
        return None

    return types.SimpleNamespace(KIT_VERSION="4", Market=FakeMarket, watchdog=watchdog, cmd_join=cmd_join)


class GuardFixture(unittest.TestCase):
    def setUp(self):
        approved = Path("C:/Users/clair/AppData/Local/Temp/opencode")
        self.tmp = tempfile.TemporaryDirectory(prefix="visibility-", dir=approved if approved.exists() else None)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.md = self.path / ".market"
        self.md.mkdir()
        self.now = NOW
        self.url = URL
        self.board = {"shops": [{"name": "mjforge", "alive": True, "card": {"url": URL}}]}
        self.shops = {"shops": [{"name": "mjforge", "url": URL, "stock": {}}]}
        self.card_status = 200
        self.api_status = 200
        self.requests = []
        self.failure = None
        self.observation_hook = None
        self.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("network forbidden")))

    def respond(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        if request.url.path == "/api/board":
            return httpx.Response(self.api_status, json=self.board)
        if request.url.path == "/api/shops":
            return httpx.Response(self.api_status, json=self.shops)
        if self.observation_hook:
            self.observation_hook()
        return httpx.Response(self.card_status, headers={"location": "https://redirect.invalid"}, json={})

    def standalone(self):
        market = FakeMarket("https://market.invalid")
        market.guard_lock = threading.RLock()
        market.join("shop", "mjforge", object(), URL)
        guard = vg.VisibilityGuard(market, self.md, "mjforge", lambda: self.url, client=self.client, clock=lambda: self.now)
        return market, guard

    def guarded(self):
        kit = fake_kit()

        def factory(market, directory, name, supplier):
            return vg.VisibilityGuard(market, directory, name, supplier, client=self.client, clock=lambda: self.now)

        cls = adapter.install(kit, self.path, guard_factory=factory)
        market = cls("https://market.invalid")
        self.stack.enter_context(patch("threading.Thread.start"))
        market.join("shop", "mjforge", object(), URL)
        return kit, market, market.visibility_guard

    def absent(self):
        self.board, self.shops = {"shops": []}, {"shops": []}

    def healthy(self, url=URL):
        self.board = {"shops": [{"name": "mjforge", "alive": True, "card": {"url": url}}]}
        self.shops = {"shops": [{"name": "mjforge", "url": url, "stock": {}}]}

    def state(self):
        return vg.read_json(self.md / "visibility_guard.json")


class VisibilityTests(GuardFixture):
    def test_two_good_observations_required(self):
        market, guard = self.standalone()
        guard.tick()
        self.assertEqual(self.state()["state"], "unknown")
        guard.tick()
        self.assertEqual(self.state()["state"], "healthy")
        self.assertEqual(len(market.joins), 1)
        self.assertEqual(len(self.requests), 6)
        self.assertTrue(all("authorization" not in r.headers for r in self.requests))

    def test_mixed_signals_never_rejoin(self):
        market, guard = self.standalone()
        self.board["shops"][0]["alive"] = False
        for _ in range(3):
            guard.tick()
        self.assertEqual(self.state()["reason"], "inconsistent")
        self.assertEqual(len(market.joins), 1)
        self.healthy()
        self.shops = {"shops": []}
        for _ in range(3):
            guard.tick()
        self.assertEqual(len(market.joins), 1)

    def test_both_bad_twice_same_key_url_tries_one_then_two_confirmations(self):
        _, market, guard = self.guarded()
        old = market.token["shop"]
        key = market.joined["shop"][1]
        self.absent()
        guard.tick()
        self.assertEqual(len(market.joins), 1)
        guard.tick()
        self.assertEqual(market.joins[-1], ("shop", "mjforge", key, URL, 1))
        self.assertNotEqual(market.token["shop"], old)
        self.assertIs(guard.market, market)
        self.assertEqual(self.state()["state"], "rejoining")
        self.healthy()
        guard.tick()
        self.assertNotEqual(self.state()["state"], "healthy")
        guard.tick()
        self.assertEqual(self.state()["state"], "healthy")
        self.assertEqual(self.state()["next_retry"], 0)

    def test_false_board_and_missing_shop_are_both_bad(self):
        market, guard = self.standalone()
        self.board["shops"][0]["alive"] = False
        self.shops = {"shops": []}
        guard.tick()
        guard.tick()
        self.assertEqual(len(market.joins), 2)

    def test_errors_schema_duplicates_and_url_mismatch_no_rejoin(self):
        for kind in ("status", "exception", "schema", "duplicate", "url", "alive", "stock"):
            with self.subTest(kind=kind):
                self.healthy()
                self.failure, self.api_status = None, 200
                market, guard = self.standalone()
                if kind == "status":
                    self.api_status = 503
                elif kind == "exception":
                    self.failure = RuntimeError("sensitive-not-to-log")
                elif kind == "schema":
                    self.board = []
                elif kind == "duplicate":
                    self.shops["shops"] *= 2
                elif kind == "url":
                    self.board["shops"][0]["card"]["url"] = NEW_URL
                elif kind == "alive":
                    self.board["shops"][0]["alive"] = "false"
                else:
                    self.shops["shops"][0]["stock"] = []
                guard.tick()
                guard.tick()
                self.assertEqual(self.state()["state"], "unknown")
                self.assertEqual(len(market.joins), 1)
                self.assertNotIn("sensitive", json.dumps(self.state()))

    def test_card_failure_and_redirect_never_rejoin_or_follow(self):
        for code in (302, 404, 503):
            with self.subTest(code=code):
                market, guard = self.standalone()
                self.absent()
                self.card_status = code
                guard.tick()
                guard.tick()
                self.assertEqual(len(market.joins), 1)
                self.assertEqual(self.state()["status"], code)
        self.assertFalse(any(r.url.host == "redirect.invalid" for r in self.requests))

    def test_malformed_other_rows_are_not_evidence_of_our_absence(self):
        market, guard = self.standalone()
        self.board, self.shops = {"shops": [{"name": "other"}]}, {"shops": []}
        guard.tick()
        guard.tick()
        self.assertEqual(len(market.joins), 1)
        self.assertEqual(self.state()["state"], "unknown")

    def test_url_rotation_during_observation_does_not_join_old_url(self):
        market, guard = self.standalone()
        self.absent()
        guard.tick()
        self.observation_hook = lambda: setattr(self, "url", NEW_URL)
        guard.tick()
        self.assertEqual(len(market.joins), 1)
        self.assertEqual(self.state()["reason"], "url_changed")

    def test_watchdog_failed_join_recovers_current_proxy_not_joined_url(self):
        kit, market, guard = self.guarded()
        proxy = types.SimpleNamespace(public=URL)
        locked_proxy = kit.watchdog(None, None, None, market, None, None, proxy, None, None, None)
        locked_proxy.public = NEW_URL
        market.join_error = SystemExit("SECRET body")
        with self.assertRaisesRegex(SystemExit, "^guard_shop_join_failed$"):
            market.join("shop", "mjforge", market.joined["shop"][1], NEW_URL)
        self.assertEqual(market.joined["shop"][2], URL)
        market.join_error = None
        self.absent()
        guard.tick()
        guard.tick()
        self.assertEqual(market.joins[-1][3], NEW_URL)

    def test_stale_join_url_is_refused_instead_of_re_registering_old_url(self):
        kit, market, _ = self.guarded()
        proxy = types.SimpleNamespace(public=URL)
        locked_proxy = kit.watchdog(None, None, None, market, None, None, proxy, None, None, None)
        locked_proxy.public = NEW_URL
        with self.assertRaisesRegex(SystemExit, "guard_url_changed"):
            market.join("shop", *market.joined["shop"])
        self.assertEqual(len(market.joins), 1)
        self.assertEqual(self.state()["reason"], "url_changed")

    def test_backoff_300_600_1200_and_cap_without_healthy_reset(self):
        market, guard = self.standalone()
        self.absent()
        market.join_error = SystemExit("SECRET")
        guard.tick()
        for delay in (300, 600, 1200, 1200):
            guard.tick()
            self.assertEqual(self.state()["next_retry"], self.now + delay)
            count = len(market.joins)
            self.now += delay - 1
            guard.tick()
            self.assertEqual(len(market.joins), count)
            self.now += 1
        self.assertNotIn("SECRET", json.dumps(self.state()))

    def test_shop_inventory_401_rechecked_and_new_token_same_market(self):
        _, market, guard = self.guarded()
        market.response = 401
        market.call("GET", "/api/inventory", "shop")
        self.assertTrue(guard.auth_failed)
        old = market.token["shop"]
        guard.tick()
        self.assertEqual(len(market.calls), 2)
        self.assertNotEqual(market.token["shop"], old)
        self.assertEqual(market.joins[-1][-1], 1)
        self.assertNotEqual(self.state()["state"], "healthy")

    def test_inventory_401_with_card_down_never_reauth(self):
        _, market, guard = self.guarded()
        market.response = 401
        market.call("GET", "/api/inventory", "shop")
        self.card_status = 503
        guard.tick()
        self.assertEqual(len(market.joins), 1)

    def test_transient_inventory_401_current_success_does_not_reauth(self):
        _, market, guard = self.guarded()
        market.response = 401
        market.call("GET", "/api/inventory", "shop")
        market.response = 200
        guard.tick()
        self.assertEqual(len(market.joins), 1)
        guard.tick()
        self.assertEqual(self.state()["state"], "healthy")

    def test_unsafe_post_401_never_retried_or_reauth(self):
        _, market, guard = self.guarded()
        market.response = 401
        response = market.call("POST", "/api/dispose", "shop", json={"unit": "test"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(market.calls), 1)
        guard.tick()
        self.assertEqual(len(market.joins), 1)

    def test_buyer_token_calls_and_join_unaffected(self):
        kit = fake_kit()
        cls = adapter.install(kit, self.path)
        market = cls("https://market.invalid")
        market.join("buyer", "mjforge", object())
        market.response = 401
        self.assertEqual(market.call("GET", "/api/inventory").status_code, 401)
        self.assertIsNone(market.visibility_guard)
        self.assertFalse((self.md / "visibility_guard.json").exists())
        self.assertEqual(len(market.joins), 1)

    def test_shared_real_kit_signature_compatibility_and_version_rejection(self):
        path = Path(adapter.__file__).resolve().parents[3] / "scripts" / "market_kit.py"
        spec = importlib.util.spec_from_file_location("_visibility_test_shared_kit", path)
        kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kit)
        self.assertTrue(adapter.compatible(kit))
        kit.KIT_VERSION = "future"
        self.assertFalse(adapter.compatible(kit))
        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            adapter.install(kit, self.path)
        kit = fake_kit()
        kit.Market = types.SimpleNamespace(join=lambda: None, call=lambda: None)
        self.assertFalse(adapter.compatible(kit))

    def test_duplicate_runtime_lock_released(self):
        with vg.RuntimeLock(self.md):
            with self.assertRaisesRegex(RuntimeError, "already_locked"):
                with vg.RuntimeLock(self.md):
                    self.fail("duplicate lock admitted")
        with vg.RuntimeLock(self.md):
            pass

    def test_guard_loop_waits_60_and_stops(self):
        _, guard = self.standalone()
        guard.stop = Mock()
        guard.stop.is_set.side_effect = [False, True]
        guard.run()
        guard.stop.wait.assert_called_once_with(60)

    def test_startup_missing_keys_never_calls_kit_or_creates_keys(self):
        original = Mock()
        args = types.SimpleNamespace(dir=str(self.path), buyer_only=False)
        with self.assertRaisesRegex(SystemExit, "existing_keys_required"):
            adapter.run_join(fake_kit(), original, args)
        original.assert_not_called()
        self.assertFalse(list(self.path.glob("*.pem")))

    def test_startup_uses_key_existence_only_and_buyer_only_is_vanilla(self):
        for name in ("shop_key.pem", "buyer_key.pem"):
            (self.path / name).touch()  # Empty, isolated fake files; never real keys.
        args = types.SimpleNamespace(dir=str(self.path), buyer_only=True)
        original = Mock(return_value="vanilla")
        kit = fake_kit()
        with patch.object(Path, "read_text", side_effect=AssertionError("no key reads")):
            self.assertEqual(adapter.run_join(kit, original, args), "vanilla")
        self.assertIs(kit.Market, FakeMarket)
        self.assertFalse((self.md / "visibility_guard.json").exists())

    def test_startup_incompatible_is_unknown_and_aborts_before_vanilla(self):
        for name in ("shop_key.pem", "buyer_key.pem"):
            (self.path / name).touch()
        kit = fake_kit()
        kit.KIT_VERSION = "future"
        original = Mock()
        args = types.SimpleNamespace(dir=str(self.path), buyer_only=False)
        with self.assertRaisesRegex(SystemExit, "incompatible"):
            adapter.run_join(kit, original, args)
        original.assert_not_called()
        self.assertTrue(vg.paused(self.state()))
        self.assertEqual(self.state()["reason"], "incompatible_kit")


class SerializationTests(GuardFixture):
    def test_join_and_shop_call_share_reentrant_lock(self):
        _, market, _ = self.guarded()
        entered, release, call_done = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def hook():
            entered.set()
            if not release.wait(3):
                errors.append("join wait timeout")

        market.join_hook = hook
        # Thread.start was disabled only to prevent automatic guard startup.
        self.stack.close()
        join_thread = threading.Thread(target=lambda: market.join("shop", *market.joined["shop"]))
        call_thread = threading.Thread(target=lambda: (market.call("GET", "/api/inventory", "shop"), call_done.set()))
        join_thread.start()
        self.assertTrue(entered.wait(3))
        call_thread.start()
        self.assertFalse(call_done.wait(.05))
        release.set()
        join_thread.join(3)
        call_thread.join(3)
        self.assertFalse(errors)
        self.assertTrue(call_done.is_set())
        self.assertEqual(market.calls[-1][-1], market.token["shop"])

    def test_concurrent_initial_joins_start_exactly_one_guard(self):
        kit = fake_kit()
        factory = Mock(side_effect=lambda *a: vg.VisibilityGuard(*a, client=self.client, clock=lambda: self.now))
        cls = adapter.install(kit, self.path, guard_factory=factory)
        market = cls("https://market.invalid")
        with patch.object(vg.VisibilityGuard, "run"):
            threads = [threading.Thread(target=lambda: market.join("shop", "mjforge", "fake-key", URL)) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(len(market.joins), 2)


class SupplyGateTests(GuardFixture):
    def setUp(self):
        super().setUp()
        self.kit, self.market, self.guard = self.guarded()
        self.inv = {"units": [], "restock": {"opens_at": NOW-20, "closes_at": NOW+160}}
        self.hub = {"own_last_sale_ts": {"G12345678": NOW-3000}}
        self.stack.enter_context(patch.object(adapter.time, "time", return_value=NOW))
        self.save()

    def save(self):
        vg.atomic_json(self.md / "inventory.json", self.inv)
        os.utime(self.md / "inventory.json", (NOW, NOW))
        vg.atomic_json(self.path / "state_hub.json", self.hub)

    def supply(self, qty=1, sku="G12345678"):
        return self.market.call("POST", "/api/supply", "shop", json={"sku": sku, "qty": qty})

    def test_isolated_atomic_reservation_path_stays_below_windows_limit(self):
        # Long full-suite labels used to push this temp name past MAX_PATH,
        # making write_text fail with ENOENT before the fake market was called.
        target = self.md / f"visibility_supply_reservations.json.{os.getpid()}.{threading.get_ident()}.tmp"
        self.assertLess(len(str(target)), 260, str(target))

    def test_pause_restores_one_only_once_per_sku_window_even_401(self):
        self.market.response = 401
        self.assertEqual(self.supply().status_code, 401)
        self.assertEqual(self.supply().status_code, 409)
        self.assertEqual(len(self.market.calls), 1)
        self.inv["restock"]["opens_at"] += 1
        self.save()
        self.assertEqual(self.supply().status_code, 401)

    def test_timeout_reservation_survives_wrapper_restart(self):
        self.market.call_hook = Mock(side_effect=httpx.ReadTimeout("fake"))
        with self.assertRaises(httpx.ReadTimeout):
            self.supply()
        self.market.call_hook = None
        self.assertEqual(self.supply().status_code, 409)
        cls = self.kit.Market
        other = cls("https://market.invalid")
        other.join("shop", "mjforge", "fake-key", URL)
        self.assertEqual(other.call("POST", "/api/supply", "shop", json={"sku": "G12345678", "qty": 1}).status_code, 409)

    def test_qty_held_missing_evidence_stale_inventory_and_closed_window_block(self):
        self.assertEqual(self.supply(qty=2).status_code, 409)
        self.assertEqual(self.supply(qty=True).status_code, 409)
        self.assertEqual(self.supply(sku="market-only").status_code, 409)
        self.inv["units"] = [{"sku": "G12345678", "grade": "A"}]
        self.save()
        self.assertEqual(self.supply().status_code, 409)
        self.inv["units"] = []
        self.save()
        os.utime(self.md / "inventory.json", (NOW-121, NOW-121))
        self.assertEqual(self.supply().status_code, 409)
        self.inv["restock"]["closes_at"] = NOW
        self.save()
        self.assertEqual(self.supply().status_code, 409)
        self.assertFalse(self.market.calls)

    def test_healthy_original_supply_unchanged_then_plan_to_post_deterioration_blocks(self):
        self.guard.tick()
        self.guard.tick()
        self.assertEqual(self.supply(qty=8).status_code, 200)
        self.assertEqual(self.market.calls[-1][3]["json"]["qty"], 8)
        self.guard.publish("unknown", "test_deterioration", URL)
        self.assertEqual(self.supply(qty=8).status_code, 409)
        self.assertEqual(len(self.market.calls), 1)

    def test_missing_future_stale_state_all_hold(self):
        for state in ({}, {"version": 1, "state": "healthy", "checked_at": NOW+1},
                      {"version": 1, "state": "healthy", "checked_at": NOW-181}):
            vg.atomic_json(self.md / "visibility_guard.json", state)
            self.assertEqual(self.supply(qty=2).status_code, 409)

    def test_corrupt_restoration_reservations_do_not_allow_duplicate(self):
        (self.md / "visibility_supply_reservations.json").write_text("broken", encoding="utf-8")
        self.assertEqual(self.supply().status_code, 409)
        self.assertFalse(self.market.calls)


class RestockGuardTests(BuyingFixture, unittest.TestCase):
    def plan_guarded(self, state):
        return restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [],
                                     inventory_fresh=True, visibility_state=state)

    def test_missing_stale_future_invalid_nonhealthy_state_pause(self):
        for state in ({}, {"version": 1, "state": "healthy", "checked_at": NOW-181},
                      {"version": 1, "state": "healthy", "checked_at": NOW+1},
                      {"version": 1, "state": "healthy", "checked_at": float("nan")},
                      *({"version": 1, "state": s, "checked_at": NOW} for s in ("unknown", "degraded", "rejoining"))):
            self.assertTrue(vg.paused(state, NOW))

    def test_healthy_policy_and_reservations_identical_to_unflagged(self):
        sku = self.item()
        self.inquiry(sku, 8)
        before = copy.deepcopy(self.hub)
        expected = self.plan()
        expected_hub = copy.deepcopy(self.hub)
        self.hub = before
        actual = self.plan_guarded({"version": 1, "state": "healthy", "checked_at": NOW})
        self.assertEqual(actual, expected)
        self.assertEqual(self.hub, expected_hub)

    def test_pause_held_zero_proven_stockout_one_other_zero_exact_reservation(self):
        held, proven, other = self.item("held"), self.item("proven"), self.item("other")
        self.stock(held)
        for sku in (held, proven, other):
            self.inquiry(sku, 8)
        self.hub["own_last_sale_ts"] = {held: NOW, proven: NOW-3500}
        plan = self.plan_guarded({})
        self.assertEqual(plan["buy"], [{"sku": proven, "qty": 1}])
        state = self.state()
        self.assertEqual(state["reserved"], self.cost(plan))
        self.assertEqual(state["planned_skus"], {proven: 1})
        self.assertEqual(self.plan_guarded({})["buy"], [])

    def test_market_sales_alone_old_or_future_own_sales_not_proof(self):
        for sku, ts in (("old", NOW-3601), ("future", NOW+1), ("market", 0)):
            self.item(sku)
            self.inquiry(sku, 8)
            self.market(sku, 100)
            self.hub.setdefault("own_last_sale_ts", {})[sku] = ts
        self.assertEqual(self.plan_guarded({})["buy"], [])

    def test_pause_existing_risk_and_no_rebuy_restrictions_preserved(self):
        sku = self.item()
        self.inquiry(sku, 8)
        self.hub["own_last_sale_ts"] = {sku: NOW}
        self.hub.setdefault("no_rebuy", {})[sku] = NOW
        self.assertEqual(self.plan_guarded({})["buy"], [])

    def test_pause_inventory_not_fresh_no_restore(self):
        sku = self.item()
        self.inquiry(sku, 8)
        self.hub["own_last_sale_ts"] = {sku: NOW}
        result = restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [], visibility_state={})
        self.assertEqual(result["buy"], [])

    def test_pause_growth_allocator_cannot_expand_past_one(self):
        sku = self.item()
        self.inquiry(sku, 8)
        self.hub["own_last_sale_ts"] = {sku: NOW}
        model = {"depth": 8, "own20": 3, "own60": 3, "market60": 10, "prediction20": 5}
        estimate = {"expected_unit_contribution": 4000, "reservation_unit_cost": 16000,
                    "negative_grades": [], "break_penalty": False, "break_rates": {"A": .1}}
        with patch.object(restock.cash_policy, "ready", return_value=True), \
             patch.object(restock.cash_policy, "forecast", return_value=model), \
             patch.object(restock.cash_policy, "procurement", return_value=estimate), \
             patch.object(restock.cash_policy, "spending_budget", return_value=(100000, 0)):
            result = self.plan_guarded({})
        self.assertEqual(result["buy"], [{"sku": sku, "qty": 1}])
        self.assertEqual(self.state()["reserved"], 16000)

    def test_production_flag_missing_file_passes_explicit_failclosed_state(self):
        sku = self.item()
        self.inquiry(sku, 8)
        (self.path / "inventory.json").write_text("{}", encoding="utf-8")
        os.utime(self.path / "inventory.json", (NOW, NOW))
        self.hub.setdefault("restock_windows", {})[str(restock.supply_window(self.inv))] = {"baseline_actual": 0, "reserved": 0}

        @contextlib.contextmanager
        def mutation():
            yield self.hub

        with patch.object(restock, "load_catalog", return_value=self.catalog), \
             patch.object(restock, "load_inventory", return_value=self.inv), \
             patch.object(restock, "load_hub", return_value=self.hub), \
             patch.object(restock, "fetch_ledger_rows", return_value=[]), \
             patch.object(restock, "fetch_own_shop_urls", return_value=[]), \
             patch.object(restock, "fetch_public_buying_data", return_value=None), \
             patch.object(restock, "mutate_hub", side_effect=mutation):
            self.assertEqual(restock.build_restock_plan(self.path, visibility_guard=True)["buy"], [])
            self.assertTrue(restock.build_restock_plan(self.path)["buy"])


class LauncherTests(GuardFixture):
    def setUp(self):
        super().setUp()
        self.repo = Path(adapter.__file__).resolve().parents[3]
        self.launcher = Path(adapter.__file__).with_name("run_guarded_market.sh")
        self.original = self.path / "run_kit.sh"
        self.invocation = "python scripts/market_kit.py join --dir submissions/25512085/week-08 --market https://market.ourolabs.ai --name mjforge --tunnel"
        self.script = (f'#!/bin/bash\ncd "{self.repo.as_posix()}"\nsource "$HOME/venv/bin/activate"\n'
                       'export MARKET_OWNER=offline-owner\nwhile true; do\n'
                       + self.invocation + '\necho "retry"\nsleep 10\ndone\n')
        self.original.write_text(self.script, encoding="utf-8")

    def test_expected_structure_and_absolute_same_repo_path(self):
        self.assertTrue(adapter.validate_launcher(self.original, self.repo, self.path))
        absolute = (self.repo / "scripts/market_kit.py").as_posix()
        self.original.write_text(self.script.replace("scripts/market_kit.py", f'"{absolute}"'), encoding="utf-8")
        self.assertTrue(adapter.validate_launcher(self.original, self.repo, self.path))

    def test_live_vm_launcher_shape_is_accepted(self):
        # 11/05 VM ~/run_kit.sh: venv activated before cd; echo and sleep share a line.
        live = (f'#!/bin/bash\nsource "$HOME/venv/bin/activate"\ncd "{self.repo.as_posix()}"\n'
                'export MARKET_OWNER=offline-owner\nwhile true; do\n  ' + self.invocation +
                '\n  echo "kit exited, restarting in 10s"; sleep 10\ndone\n')
        self.original.write_text(live, encoding="utf-8")
        self.assertTrue(adapter.validate_launcher(self.original, self.repo, self.path))
        for hidden in (live.replace('; sleep 10', '; python scripts/market_kit.py; sleep 10'),
                       live.replace('; sleep 10', '; sleep 10; sleep 10')):
            self.original.write_text(hidden, encoding="utf-8")
            self.assertFalse(adapter.validate_launcher(self.original, self.repo, self.path))

    def test_uninterceptable_or_extra_invocation_is_rejected_without_output(self):
        for changed in (self.script.replace("python scripts/", "python3 scripts/"),
                        self.script.replace("scripts/market_kit.py", "/other/market_kit.py"),
                        self.script.replace("sleep 10", "python scripts/market_kit.py\nsleep 10"),
                        self.script.replace("--name mjforge", "--name another")):
            self.original.write_text(changed, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertFalse(adapter.validate_launcher(self.original, self.repo, self.path))
            self.assertEqual(output.getvalue(), "")

    def bash(self):
        candidates = [shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe"]
        bash = next((c for c in candidates if c and Path(c).is_file()), None)
        if not bash:
            self.skipTest("bash unavailable: launcher execution not verified")
        return bash

    def run_stub(self):
        # Only isolated fake activation/original/interpreter files are executed.
        # The loop's sleep is stubbed to exit; no kit, tunnel or market is run.
        venv = self.path / "venv/bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text('python -c "offline_other_python"\n', encoding="utf-8")
        interpreter = venv / "python"
        interpreter.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$2" == --validate-launcher ]]; then exec "$REAL_PYTHON" "$@"; fi\n'
            'if [[ "$1" == -c ]]; then printf "%s\\n" other_python_forwarded; exit 0; fi\n'
            'if [[ "$1" != */guarded_market_kit.py ]]; then exit 81; fi\n'
            'if [[ "${MARKET_OWNER-}" != offline-owner ]]; then exit 82; fi\n'
            'printf "%s\\n" guarded_stub_auth_preserved\n'
            'printf "%s\\n" "${@:2}"\n'
            'exit 17\n', encoding="utf-8")
        interpreter.chmod(0o700)
        environment = dict(os.environ, HOME=self.path.as_posix(), REAL_PYTHON=sys.executable,
                           MARKET_PYTHON=interpreter.as_posix())
        return subprocess.run([self.bash(), "--noprofile", "--norc", "-c",
                               'sleep() { exit 0; }; source "$1"', "offline-launcher", self.launcher.as_posix()],
                              env=environment, text=True, capture_output=True, timeout=20)

    def test_bash_stub_preserves_auth_loop_and_other_python_forwarding(self):
        before = self.original.read_bytes()
        result = self.run_stub()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("guarded_market_kit_intercept", result.stderr)
        self.assertIn("guarded_stub_auth_preserved", result.stdout)
        self.assertIn("other_python_forwarded", result.stdout)
        self.assertIn("retry", result.stdout)  # nonzero wrapper still reaches the original loop
        self.assertIn("--tunnel", result.stdout)
        self.assertNotIn("offline-owner", result.stdout + result.stderr)
        self.assertEqual(self.original.read_bytes(), before)

    def test_bash_stub_structure_failure_cannot_fall_back_to_vanilla(self):
        self.original.write_text(self.script.replace("python scripts/", "python3 scripts/"), encoding="utf-8")
        result = self.run_stub()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("original_structure_rejected", result.stderr)
        self.assertNotIn("guarded_stub_auth_preserved", result.stdout)
        self.assertNotIn("offline-owner", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
