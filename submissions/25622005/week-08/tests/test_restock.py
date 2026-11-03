"""tools/stock.py rules (D1, D2, D4, D10, D14, L1, supply windows) and the restock hook's one-line output,
offline."""
import copy
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-restock-"))
sys.path.insert(0, str(W))

import policy  # noqa: E402
from tools import stock  # noqa: E402

POL = copy.deepcopy(policy.DEFAULTS)
END = POL["stock"]["end_ts"]
NOW = END - 10 * 86400


def cat(sku, leaf, price, kind="food", shelf=720):
    return sku, {"name": sku, "category": f"{'식품' if kind == 'food' else '가구'}>{leaf}", "list_price": price,
                 "kind": kind, "shelf_hours": shelf if kind == "food" else None}


def dem(leaf, median, kind="food", shelf=720):
    return {"category": f"{'식품' if kind == 'food' else '가구'}>{leaf}", "item": leaf, "kind": kind, "skus": 2,
            "median_price": median, "shelf_hours": shelf if kind == "food" else None}


CATALOG = dict([cat("F1a", "김치", 10000), cat("F1b", "김치", 14000), cat("F2", "쌀", 30000),
                cat("F3", "라면", 5000), cat("S1", "우유", 3000, shelf=96),
                cat("G1", "의자", 40000, "goods"), cat("G2", "침대", 300000, "goods")])
DEMAND = [dem("김치", 11000), dem("쌀", 30000), dem("라면", 5000), dem("우유", 3000, shelf=96),
          dem("의자", 40000, "goods"), dem("침대", 300000, "goods")]


def inv(balance=5000000, units=()):
    return {"balance": balance, "start": 5000000, "injected": 0, "bankruptcies_left": 2, "bankrupt_line": 500000,
            "storage_per_day": 0.01, "units": list(units)}


def unit(sku, cost, kind="food", supplied=NOW - 3600, grade="A"):
    return {"unit": f"u-{sku}-{cost}", "sku": sku, "category": CATALOG.get(sku, {}).get("category", "x>y"),
            "list_price": 0, "kind": kind, "cost": cost, "grade": grade, "supplied_at": supplied}


class PlanTest(unittest.TestCase):
    def test_bootstrap_long_shelf_food_first_then_cheap_goods(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, POL, NOW)
        skus = [b["sku"] for b in p["buy"]]
        self.assertEqual(skus, ["F1a", "F3", "F2", "G1"])            # 720 h food tier first, then goods
        self.assertTrue(all(b["qty"] == POL["stock"]["per_sku_qty"] for b in p["buy"]))
        self.assertEqual(p["excluded"]["우유"], "D2_short_shelf_wait")
        self.assertEqual(p["excluded"]["침대"], "D2_goods_over_max_list")
        self.assertEqual(p["headroom"], 1500000)                      # 30 % of 5,000,000, cash floor not binding
        self.assertEqual(p["worst_cost"], int(0.75 * 2 * (10000 + 30000 + 5000 + 40000)))
        self.assertIn("D1", p["rules"])

    def test_sku_nearest_median(self):
        p = stock.plan_restock(inv(), CATALOG, [dem("김치", 13000)], {}, POL, NOW)
        self.assertEqual(p["buy"], [{"sku": "F1b", "qty": 2}])

    def test_caps_use_the_a_grade_worst_cost(self):
        pol = copy.deepcopy(POL)
        pol["stock"]["per_sku_qty"] = 20
        # cash floor binds: headroom = 1,200,000 - 1,000,000 = 200,000; worst 0.75 x 100,000 = 75,000 a unit
        c = dict([cat("X", "한우", 100000), cat("Y", "두부", 20000)])
        p = stock.plan_restock(inv(1200000), c, [dem("한우", 100000), dem("두부", 20000)], {}, pol, NOW)
        self.assertEqual(p["headroom"], 200000)
        self.assertEqual(p["buy"][0], {"sku": "Y", "qty": 13})         # 13 x 15,000 = 195,000
        self.assertEqual(p["excluded"]["X"], "D1_cap")
        self.assertLessEqual(p["worst_cost"], p["headroom"])
        # inventory already at the 30 % share: nothing more
        p = stock.plan_restock(inv(3500000, [unit("Z", 1500000)]), c, [dem("두부", 20000)], {}, pol, NOW)
        self.assertEqual(p["headroom"], 0)
        self.assertEqual(p["buy"], [])

    def test_held_stock_counts_and_d4_stale_goods(self):
        units = [unit("F1a", 6000), unit("F2", 15000), unit("F2", 15000),
                 unit("G1", 20000, "goods", supplied=NOW - 6 * 86400)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, POL, NOW)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got.get("F1a"), 1)
        self.assertNotIn("F2", got)
        self.assertEqual(p["excluded"]["F2"], "stocked")
        self.assertEqual(p["excluded"]["G1"], "D4_stale_goods")
        self.assertIn("D4", p["rules"])

    def test_short_shelf_food_opens_after_enough_observations(self):
        s = {"buyer": {"category": {"우유": {"uwh": {"n": 10, "median": 20}}}}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertIn("S1", [b["sku"] for b in p["buy"]])

    def test_l1_priority_and_regular_categories(self):
        s = {"market": {"categories": {"라면": {"demand": 9, "supply": 0}, "쌀": {"demand": 9, "supply": 8}}}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(p["buy"][0]["sku"], "F3")                   # (9+1)/(0+1) beats (0+1)/(0+1) and 10/9
        s["seller"] = {"regular_cats": ["의자"]}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW)
        self.assertEqual(p["buy"][0]["sku"], "G1")                   # regular customers' category first
        self.assertIn("L1.regular", p["rules"])
        pol = copy.deepcopy(POL)
        pol["stock"]["supply_demand_priority"] = False
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, pol, NOW)
        self.assertNotIn("L1", p["rules"])

    def test_d14_taper_and_stop(self):
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 0.1}}, "regular_cats": ["라면"]}}
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, END - 30 * 3600)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got, {"F1a": 1, "F3": 1})                   # 0.1 x 30 x 0.5 = 1.5; regular keeps 1
        self.assertEqual(p["excluded"]["F2"], "D14_taper")
        self.assertIn("D14", p["rules"])
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, END - 20 * 3600)
        self.assertEqual(p["excluded"]["의자"], "D14_goods_stop")
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, END + 1)
        self.assertEqual(p["buy"], [])

    def test_line_and_qty_limits(self):
        c = dict(cat(f"K{i}", f"c{i}", 1000) for i in range(15))
        d = [dem(f"c{i}", 1000) for i in range(15)]
        pol = copy.deepcopy(POL)
        pol["stock"]["per_sku_qty"] = 20
        pol["stock"]["max_qty"] = 5
        p = stock.plan_restock(inv(), c, d, {}, pol, NOW)
        self.assertEqual(len(p["buy"]), 10)
        self.assertTrue(all(b["qty"] == 5 for b in p["buy"]))
        self.assertEqual(sum(1 for v in p["excluded"].values() if v == "max_lines"), 5)

    def test_fast_seller_covers_until_the_next_window(self):
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 12}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, POL, NOW)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got["F1a"], 7)                              # ceil(12 x 25/60 x 1.5) = 8, one held
        self.assertEqual(got["F3"], POL["stock"]["per_sku_qty"])     # no sales: per_sku_qty

    def test_cover_target_is_capped_by_max_qty(self):
        pol = copy.deepcopy(POL)
        pol["stock"]["max_qty"] = 6
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 100}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)] * 2), CATALOG, DEMAND, s, pol, NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 4)    # holds at most 6

    def test_sold_out_picks_first(self):
        units = [unit("F1a", 6000), unit("F3", 3000)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, POL, NOW)
        self.assertEqual([b["sku"] for b in p["buy"]], ["F2", "G1", "F1a", "F3"])
        self.assertEqual(p["empty_picks"], 2)

    def test_bad_inputs_do_not_raise(self):
        self.assertEqual(stock.plan_restock({}, {}, [], {}, POL, NOW)["buy"], [])
        self.assertEqual(stock.plan_restock(None, None, None, None, POL, NOW)["buy"], [])


def focus_pol(*skus, **st):
    pol = copy.deepcopy(POL)
    pol["stock"].update(focus_skus=list(skus), **st)
    return pol


class FocusTest(unittest.TestCase):
    """stock.focus_skus: the strategist's SKUs, stocked deeper and first."""

    def test_empty_focus_list_is_the_old_plan(self):
        for s in ({}, {"seller": {"skus": {"F1a": {"sales_per_h": 12}}}}):
            self.assertEqual(stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol(), NOW),
                             stock.plan_restock(inv(), CATALOG, DEMAND, s, POL, NOW))

    def test_focus_above_goods_max_list_and_not_the_pick(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("G2", "F1b"), NOW)
        skus = [b["sku"] for b in p["buy"]]
        self.assertEqual(skus[:2], ["F1b", "G2"])                     # focus first, then the old order
        self.assertEqual(skus[2:], ["F1a", "F3", "F2", "G1"])
        self.assertEqual(p["excluded"]["침대"], "D2_goods_over_max_list")    # the category itself stays out

    def test_focus_pick_is_one_candidate(self):
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("F3"), NOW)
        self.assertEqual([b["sku"] for b in p["buy"]], ["F3", "F1a", "F2", "G1"])

    def test_focus_target_is_deeper(self):
        s = {"seller": {"skus": {"F1a": {"sales_per_h": 12}, "F3": {"sales_per_h": 12}}}}
        p = stock.plan_restock(inv(units=[unit("F1a", 6000)]), CATALOG, DEMAND, s, focus_pol("F1a"), NOW)
        got = {b["sku"]: b["qty"] for b in p["buy"]}
        self.assertEqual(got["F1a"], 14)                             # ceil(12 x 25/60 x 1.5 x 2) = 15, one held
        self.assertEqual(got["F3"], 8)                               # not focus: ceil(7.5) = 8
        p = stock.plan_restock(inv(), CATALOG, DEMAND, {}, focus_pol("F1a"), NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], POL["stock"]["per_sku_qty"])
        p = stock.plan_restock(inv(), CATALOG, DEMAND, s, focus_pol("F1a", max_qty=10), NOW)
        self.assertEqual({b["sku"]: b["qty"] for b in p["buy"]}["F1a"], 10)

    def test_focus_cap_share(self):
        # headroom 200,000 (cash floor binds); focus share 0.5 = 100,000; G1 worst 30,000 a unit, X 7,500
        c = dict(CATALOG, **dict([cat("X", "두부", 10000)]))
        s = {"seller": {"skus": {"X": {"sales_per_h": 60}}}}
        p = stock.plan_restock(inv(1200000), c, [dem("두부", 10000), dem("라면", 5000)], s,
                               focus_pol("G1", "X"), NOW)
        self.assertEqual(p["headroom"], 200000)
        self.assertEqual(p["excluded"]["G1"], "focus_cap")
        self.assertEqual(p["buy"][0], {"sku": "X", "qty": 13})          # 13 x 7,500 = 97,500 of 100,000
        self.assertEqual(p["buy"][1], {"sku": "F3", "qty": 2})          # the rest of the headroom
        # the focus share left is too small, the whole headroom is not: still focus_cap
        p = stock.plan_restock(inv(1200000), c, [], {}, focus_pol("G1", focus_cap_share=0.1), NOW)
        self.assertEqual(p["excluded"]["G1"], "focus_cap")
        p = stock.plan_restock(inv(1000000), c, [], {}, focus_pol("G1"), NOW)
        self.assertEqual(p["excluded"]["G1"], "D1_cap")                  # no headroom at all

    def test_focus_still_obeys_d4_d14_and_short_shelf_food(self):
        units = [unit("G2", 200000, "goods", supplied=NOW - 6 * 86400)]
        p = stock.plan_restock(inv(units=units), CATALOG, DEMAND, {}, focus_pol("G2", "S1", "nope"), NOW)
        self.assertEqual(p["excluded"]["G2"], "D4_stale_goods")
        self.assertEqual(p["excluded"]["S1"], "D2_short_shelf_wait")
        self.assertEqual(p["excluded"]["nope"], "not_in_catalog")          # an unknown SKU is never bought
        self.assertNotIn("nope", [b["sku"] for b in p["buy"]])
        s = {"buyer": {"category": {"우유": {"uwh": {"n": 10, "median": 20}}}}}
        p = stock.plan_restock(inv(), CATALOG, [], s, focus_pol("S1"), NOW)
        self.assertEqual(p["buy"], [{"sku": "S1", "qty": 2}])
        p = stock.plan_restock(inv(), CATALOG, [], {}, focus_pol("G2"), END - 20 * 3600)
        self.assertEqual(p["excluded"]["G2"], "D14_goods_stop")
        p = stock.plan_restock(inv(), CATALOG, [], {}, focus_pol("F2"), END - 30 * 3600)
        self.assertEqual(p["excluded"]["F2"], "D14_taper")                 # no sales: nothing sells before the end


class WindowTest(unittest.TestCase):
    def test_window_reading(self):
        w = stock.supply_window({"restock": {"open": False, "opens_at": NOW + 600, "closes_at": NOW + 780}}, NOW)
        self.assertEqual(w, {"open": False, "opens_at": NOW + 600, "closes_at": NOW + 780, "in_s": 600,
                             "closes_in_s": 780})
        self.assertTrue(stock.window_closed(w, POL))
        w = stock.supply_window({"restock": {"open": False, "opens_at": NOW + 60, "closes_at": NOW + 240}}, NOW)
        self.assertFalse(stock.window_closed(w, POL))                # opens within window_lead_s
        w = stock.supply_window({"restock": {"open": True, "opens_at": NOW - 30, "closes_at": NOW + 150}}, NOW)
        self.assertFalse(stock.window_closed(w, POL))
        w = stock.supply_window(inv(), NOW)                          # an older market: read as open
        self.assertEqual(w, {"open": None, "opens_at": None, "closes_at": None, "in_s": None, "closes_in_s": None})
        self.assertFalse(stock.window_closed(w, POL))
        self.assertFalse(stock.window_closed(stock.supply_window({"restock": {"open": False}}, NOW), POL))
        for bad in (float("nan"), float("inf"), "soon", True):       # read as unknown, never raise
            w = stock.supply_window({"restock": {"open": False, "opens_at": bad, "closes_at": bad}}, NOW)
            self.assertEqual((w["in_s"], w["closes_in_s"]), (None, None))

    def test_lines_that_fit_before_the_close(self):
        fit = lambda r: stock.lines_that_fit(stock.supply_window({"restock": r}, NOW))
        self.assertEqual(fit({"open": True, "closes_at": NOW + 150}), 15)
        self.assertEqual(fit({"open": True, "closes_at": NOW + 5}), 1)       # the first call goes out at once
        self.assertEqual(fit({"open": True, "closes_at": NOW - 1}), 0)
        self.assertIsNone(fit({"open": False, "closes_at": NOW + 150}))
        self.assertIsNone(fit({"open": True}))

    def test_held(self):
        units = [unit("F1a", 6000), unit("F1a", 6000), unit("F2", 15000), unit("F3", 3000, grade="expired")]
        self.assertEqual(stock.held(inv(units=units)), {"skus": 2, "units": 3})
        self.assertEqual(stock.held(None), {"skus": 0, "units": 0})


class BankruptTest(unittest.TestCase):
    QUIET = {"started_at": NOW - 13 * 3600, "seller": {"sold_24h": 0, "last_sale_ts": None}}

    def test_files_only_under_all_d10_conditions(self):
        low = inv(400000, [unit("F1a", 600000)])
        self.assertTrue(stock.should_bankrupt(low, self.QUIET, POL, NOW)[0])
        self.assertFalse(stock.should_bankrupt(inv(600000), self.QUIET, POL, NOW)[0])           # above the line
        none_left = dict(low, bankruptcies_left=0)
        self.assertFalse(stock.should_bankrupt(none_left, self.QUIET, POL, NOW)[0])
        self.assertFalse(stock.should_bankrupt(low, self.QUIET, POL, END - 47 * 3600)[0])     # < 48 h to the end
        self.assertFalse(stock.should_bankrupt(low, {}, POL, NOW)[0])                         # no memory
        selling = {"started_at": NOW - 30 * 3600, "seller": {"sold_24h": 10, "last_sale_ts": NOW - 3600}}
        rich_stock = inv(400000, [unit("G1", 400000, "goods") for _ in range(5)])
        ok, why = stock.should_bankrupt(rich_stock, selling, POL, NOW)
        self.assertFalse(ok)                                       # 2,000,000 x 10/15 recovers enough
        self.assertEqual(why["recover"], 1333333)
        slow = {"started_at": NOW - 30 * 3600, "seller": {"sold_24h": 1, "last_sale_ts": NOW - 13 * 3600}}
        self.assertTrue(stock.should_bankrupt(rich_stock, slow, POL, NOW)[0])               # no sale for 12 h


class OwnCardTest(unittest.TestCase):
    def test_card_check(self):
        ok = mock.Mock(status_code=200, json=lambda: {"name": "psyoung82 shop"})
        with mock.patch.object(stock.httpx, "get", return_value=ok) as g:
            self.assertTrue(stock.own_card_ok("https://psy.example/"))
            self.assertEqual(g.call_args.args[0], "https://psy.example/.well-known/agent-card.json")
            self.assertEqual(g.call_args.kwargs["timeout"], 3.0)
        with mock.patch.object(stock.httpx, "get", return_value=mock.Mock(status_code=502)):
            self.assertFalse(stock.own_card_ok("https://psy.example"))
        with mock.patch.object(stock.httpx, "get", side_effect=stock.httpx.ConnectError("down")):
            self.assertFalse(stock.own_card_ok("https://psy.example"))
        with mock.patch.object(stock.httpx, "get", side_effect=AssertionError("must not call")):
            self.assertTrue(stock.own_card_ok(""))


class _Ledger(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"[]"
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class HookTest(unittest.TestCase):
    """restock.py as the kit runs it: cwd is the agent folder, the last stdout line is read."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ledger)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", self.home / "strategy" / "SKILL.md")
        self.state = self.home / ".market"
        self.state.mkdir()
        (self.state / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        (self.state / "demand.json").write_text(json.dumps(DEMAND, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("OWN_SHOP_URL",)}
        env.update(R3_HOME=str(self.home), MARKET_URL=f"http://127.0.0.1:{self.srv.server_address[1]}")
        t = time.time()
        p = subprocess.run([sys.executable, "-B", "restock.py", *args], cwd=W, env=env,
                           capture_output=True, text=True, timeout=40)
        self.assertLess(time.time() - t, 30)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        self.assertEqual(p.stderr, "")
        return json.loads(lines[0])

    def write_inv(self, obj):
        (self.state / "inventory.json").write_text(json.dumps(obj), encoding="utf-8")

    def test_plans_and_logs(self):
        self.write_inv(inv())
        out = self.run_hook("--state", str(self.state))
        self.assertEqual(set(out), {"buy"})
        self.assertTrue(out["buy"])
        rows = [json.loads(x) for x in
                (self.home / "logs" / "decisions" / "restock.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["kind"], "restock")
        self.assertEqual(rows[-1]["choice"], out["buy"])
        self.assertTrue(rows[-1]["memory_refs"])
        self.assertEqual(rows[-1]["inputs"]["window"], {"open": None, "opens_at": None, "closes_at": None,
                                                        "in_s": None, "closes_in_s": None})
        self.assertEqual(rows[-1]["inputs"]["stock"], {"skus": 0, "units": 0, "empty_picks": 4})
        self.assertTrue((self.home / "memory" / "summary.json").exists())
        self.assertTrue((self.home / "memory" / "cursor.json").exists())

    def rows(self):
        return [json.loads(x) for x in
                (self.home / "logs" / "decisions" / "restock.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_closed_window_buys_nothing_but_ingests(self):
        now = time.time()
        self.write_inv(dict(inv(), restock={"open": False, "opens_at": now + 600, "closes_at": now + 780}))
        self.assertEqual(self.run_hook("--state", str(self.state)), {"buy": []})
        row = self.rows()[-1]
        self.assertEqual((row["kind"], row["choice"]), ("restock", []))
        self.assertTrue(row["why"].startswith("supply window closed; opens in "))
        self.assertFalse(row["inputs"]["window"]["open"])
        self.assertTrue(590 <= row["inputs"]["window"]["in_s"] <= 600)
        self.assertEqual(row["inputs"]["stock"], {"skus": 0, "units": 0, "empty_picks": 4})
        self.assertTrue((self.home / "memory" / "summary.json").exists())

    def test_plans_when_open_or_opening_soon(self):
        now = time.time()
        for r in ({"open": False, "opens_at": now + 30, "closes_at": now + 210},
                  {"open": True, "opens_at": now - 30, "closes_at": now + 150}):
            self.write_inv(dict(inv(), restock=r))
            self.assertTrue(self.run_hook("--state", str(self.state))["buy"])
            self.assertEqual(self.rows()[-1]["inputs"]["window"]["open"], r["open"])

    def test_closing_window_buys_only_what_fits(self):
        now = time.time()
        self.write_inv(dict(inv(), restock={"open": True, "opens_at": now - 170, "closes_at": now + 15}))
        out = self.run_hook("--state", str(self.state))
        self.assertEqual(len(out["buy"]), 2)
        self.assertEqual(self.rows()[-1]["inputs"]["excluded"].get("window_closing"), 2)

    def test_focus_inputs_in_the_decision(self):
        self.write_inv(inv())
        self.run_hook("--state", str(self.state))
        self.assertEqual(self.rows()[-1]["inputs"]["focus"], {"skus": 0, "planned": 0, "excluded": {}})
        md = self.home / "strategy" / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8").replace(
            "focus_skus: []", 'focus_skus: ["G2", "S1", "nope"]', 1), encoding="utf-8")
        out = self.run_hook("--state", str(self.state))
        self.assertEqual(out["buy"][0]["sku"], "G2")
        self.assertEqual(self.rows()[-1]["inputs"]["focus"],
                         {"skus": 3, "planned": 1, "excluded": {"S1": "D2_short_shelf_wait", "nope": "not_in_catalog"}})

    def test_bankrupt_line(self):
        self.write_inv(inv(300000))
        self.assertEqual(self.run_hook("--state", str(self.state)), {"buy": [], "bankrupt": True})

    def test_one_line_on_errors(self):
        self.write_inv([1, 2])
        self.assertEqual(self.run_hook("--state", str(self.state)), {"buy": []})
        self.assertEqual(self.run_hook("--bogus"), {"buy": []})
        self.assertIn("failed, buying nothing",
                      (self.home / "logs" / "hooks" / "restock.log").read_text(encoding="utf-8"))

    def test_hard_limit_is_not_swallowed_by_the_ingest_guard(self):
        import restock
        from memory import ingest
        self.write_inv(inv())
        with mock.patch.object(ingest, "sync", side_effect=restock._Late("late")), \
                mock.patch.object(stock, "plan_restock", side_effect=AssertionError("must not plan")):
            with self.assertRaises(restock._Late):
                restock.run(self.state, time.time())

    def test_invalid_policy_still_plans_and_records_it(self):
        (self.home / "strategy" / "SKILL.md").write_text("no front matter", encoding="utf-8")
        self.write_inv(inv())
        self.assertTrue(self.run_hook("--state", str(self.state))["buy"])
        kinds = [json.loads(x)["kind"] for x in
                 (self.home / "logs" / "decisions" / "restock.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["policy_error", "restock"])

    def test_missing_state_files(self):
        self.assertEqual(self.run_hook("--state", str(self.home / "nowhere")), {"buy": []})


if __name__ == "__main__":
    unittest.main()
