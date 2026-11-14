"""strategist/trend.py and the SKU tables it reads (collect.sku_tables / table_view): score, gates, the focus and
trend lists with their hysteresis, and one pass over a real ledger sample (fixture_ledger_1106.json.gz: settle
and supply rows of 20 ledger pages from 2026-11-05/06, shop names replaced)."""
import copy
import gzip
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-trend-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy  # noqa: E402
from strategist import collect, trend  # noqa: E402

NOW = 1_794_000_000.0
H = 3600
ME = "psyoung82"
CAT = {"GA": ["sofa", "가구>소파", 100000, "goods", None], "GB": ["bed", "가구>침대", 50000, "goods", None],
       "GC": ["kimchi", "식품>김치", 10000, "food", 168], "GD": ["rice", "식품>쌀", 20000, "food", 2000],
       "GE": ["lamp", "가구>조명", 30000, "goods", None], "GF": ["tea", "식품>차", 5000, "food", 24]}
SAMPLE = json.loads(gzip.decompress((Path(__file__).resolve().parent / "fixture_ledger_1106.json.gz").read_bytes()))


def pol(**stock):
    p = copy.deepcopy(policy.DEFAULTS)
    p["seller"].update(floor_list=0.60, floor_cost_mult=1.08)
    p["stock"].update(stock)
    return p


def settle(sku, ts, amount=None, shop="https://other.example", unit="x"):
    return {"seq": 1, "ts": ts, "kind": "settle", "data": {"sku": sku, "shop": shop, "amount": amount, "unit": unit}}


def supply(sku, ts, qty, shop="other", total=None):
    return {"seq": 1, "ts": ts, "kind": "supply", "data": {"sku": sku, "shop": shop, "qty": qty, "total": total}}


def view(rows, now=NOW, since=None, cost_of=lambda u: None):
    t = collect.sku_tables({"b": {}, "h": {}, "since": since} if since else None, rows, CAT,
                           lambda d: d.get("shop") == "https://us.example", ME, cost_of, now)
    if since:
        t["since"] = since
    return collect.table_view(t, now)


def spread(sku, n, t0, t1, **kw):
    return [settle(sku, t0 + (t1 - t0) * (i + 0.5) / n, **kw) for i in range(n)]


class TablesTest(unittest.TestCase):
    def test_counts_rates_ours_and_p_clear(self):
        rows = (spread("GA", 12, NOW - 6 * H, NOW, amount=74000) + spread("GA", 3, NOW - 0.5 * H, NOW, amount=80000,
                                                                          shop="https://us.example", unit="u1")
                + [supply("GA", NOW - 600, 20, total=1300000), supply("GA", NOW - 3 * H, 5),
                   supply("GC", NOW - 700, 10, shop=ME, total=55000), settle("NOPE", NOW - 60, 1)])
        v = view(rows, since=NOW - 7 * H)
        ga = v["sku"]["GA"]
        self.assertEqual((ga["s6"], ga["o6"], ga["u1"], ga["s1"]), (15, 3, 20, 5))
        self.assertEqual((ga["pl6"], ga["pl_n"]), (round((12 * 0.74 + 3 * 0.8) / 15, 3), 15))
        self.assertEqual((v["h1"], v["h6"]), (1.0, 6.0))
        self.assertEqual(v["p_clear"]["goods"], {"h6": 0.74, "n6": 15, "h24": 0.74, "n24": 15})
        self.assertEqual(v["p_clear"]["food"], {"h6": None, "n6": 0, "h24": None, "n24": 0})
        self.assertEqual(v["c_kind"], {"food": 0.55, "goods": None})          # our supply only
        self.assertEqual((v["sku"]["NOPE"]["s6"], v["sku"]["NOPE"]["pl6"]), (1, None))   # not in the catalog: no paid/list
        # our 3 settles: no unit cost known, so list x the goods fallback 0.61
        self.assertEqual(v["day"], {"cogs": 3 * 61000, "rev": 240000, "exp": 0, "ref": 0, "o": {"GA": 3}, "h": 5.75})

    def test_each_row_once_and_pruning(self):
        t = collect.sku_tables(None, [settle("GA", NOW - 60, 1000)], CAT, lambda d: False, ME, lambda u: None, NOW)
        t = collect.sku_tables(t, [], CAT, lambda d: False, ME, lambda u: None, NOW + 600)
        self.assertEqual(collect.table_view(t, NOW + 600)["sku"]["GA"]["s6"], 1)
        t = collect.sku_tables(t, [], CAT, lambda d: False, ME, lambda u: None, NOW + 7 * H)
        v = collect.table_view(t, NOW + 7 * H)
        self.assertEqual((v["sku"], v["p_clear"]["goods"]["n24"]), ({}, 1))   # gone from 6 h, still in 24 h
        self.assertEqual(v["day"]["h"], 7.02)                         # the hourly sums span from the first row
        t = collect.sku_tables(t, [settle("GA", NOW + 20 * H, 1000)], CAT, lambda d: False, ME, lambda u: None,
                               NOW + 23 * H)
        self.assertEqual(collect.table_view(t, NOW + 24 * H)["day"]["h"], 24.0)
        t = collect.sku_tables(t, [], CAT, lambda d: False, ME, lambda u: None, NOW + 45 * H)
        self.assertEqual((t["b"], t["h"]), ({}, {}))

    def test_cost_of_sold_units_and_spoilage(self):
        rows = [settle("GA", NOW - 60, 90000, shop="https://us.example", unit="u7"),
                {"seq": 2, "ts": NOW - 50, "kind": "expire", "data": {"shop": ME, "units": ["u8"], "cost": 4000}},
                {"seq": 3, "ts": NOW - 40, "kind": "expire", "data": {"shop": "other", "cost": 999}}]
        v = view(rows, cost_of=lambda u: 52000 if u == "u7" else None)
        self.assertEqual(v["day"], {"cogs": 52000, "rev": 90000, "exp": 4000, "ref": 0, "o": {"GA": 1}, "h": 0.02})

    def test_market_price_by_grade_and_our_refunds(self):
        graded = lambda amount, g, ts: {**settle("GA", ts, amount), "data": {**settle("GA", ts, amount)["data"], "grade": g}}
        rows = [graded(70000, "A", NOW - 600), graded(72000, "A", NOW - 500), graded(60000, "B", NOW - 400),
                settle("GA", NOW - 300, 50000),                                    # no grade: pl6 only
                {"seq": 4, "ts": NOW - 200, "kind": "broken",
                 "data": {"shop": "https://us.example", "unit": "u9", "item": "GA", "refunded": 66000}},
                {"seq": 5, "ts": NOW - 100, "kind": "broken",
                 "data": {"shop": "https://other.example", "unit": "u1", "item": "GA", "refunded": 50000}}]
        v = view(rows)
        self.assertEqual(v["sku"]["GA"]["pg6"], {"A": [0.71, 2], "B": [0.6, 1]})
        self.assertEqual(v["sku"]["GA"]["pl_n"], 4)
        self.assertEqual(v["day"]["ref"], 66000)

    def test_bounded_state(self):
        rows = [settle(f"G{i:08x}", NOW - (i % 360) * 60, 1000) for i in range(5000)]
        cat = {f"G{i:08x}": ["x", "a>b", 2000, "goods", None] for i in range(5000)}
        t = collect.sku_tables(None, rows, cat, lambda d: False, ME, lambda u: None, NOW)
        self.assertLessEqual(len(t["b"]), 37)
        self.assertLessEqual(len(t["h"]), 25)
        self.assertLess(len(json.dumps(t)), 400_000)


class ScoreTest(unittest.TestCase):
    def rows(self, ledger, p=None, **kw):
        return trend.score(view(ledger, since=NOW - 7 * H), CAT, p or pol(), NOW, **kw)

    def test_score_momentum_scarcity_and_bonuses(self):
        ledger = spread("GA", 12, NOW - 6 * H, NOW) + spread("GB", 12, NOW - 6 * H, NOW - 2 * H) + spread("GE", 4, NOW - 6 * H, NOW)
        rows, _ = self.rows(ledger, shelf={"units": {"GA": 20}, "shops": {"GA": 3}})
        by = {r["sku"]: r for r in rows}
        self.assertNotIn("GE", by)                                     # under MIN_SETTLES
        self.assertEqual((by["GA"]["rate6"], by["GA"]["rate1"], by["GA"]["cover_h"], by["GA"]["shops"]), (2.0, 2.0, 10.0, 3))
        # GA: 2 x value sqrt(10) x momentum 1 x scarcity max(0.5, 2/10) x free bonus; GB: no sale in the last hour, no shelf: 0
        va, vb = math.sqrt(100000 / trend.VALUE_REF), math.sqrt(50000 / trend.VALUE_REF)
        self.assertEqual((by["GA"]["value"], by["GB"]["value"]), (round(va, 3), round(vb, 3)))
        self.assertEqual(by["GA"]["score"], round(2 * va * 1 * 0.5 * trend.BONUS, 3))
        self.assertEqual((by["GB"]["rate1"], by["GB"]["cover_h"], by["GB"]["score"]), (0.0, 0.0, round(2 * vb * 0.5 * 2 * trend.BONUS, 3)))
        rows2, _ = self.rows(ledger, shelf={"units": {"GA": 20}}, reviews={"sku": {"GA": 4}})
        self.assertEqual({r["sku"]: r["score"] for r in rows2}["GA"], round(by["GA"]["score"] * trend.BONUS, 3))
        rows3, _ = self.rows(ledger)                                   # no shelf data: scarcity 1
        self.assertEqual({r["sku"]: r["cover_h"] for r in rows3}["GA"], None)

    def test_margin_gate_uses_the_sku_or_kind_p_clear_and_the_surge_factor(self):
        ledger = spread("GA", 10, NOW - 5 * H, NOW, amount=74000)       # p_clear 0.74 for GA
        rows, _ = self.rows(ledger)
        ga = rows[0]
        self.assertEqual((ga["p_clear"], ga["margin"], ga["gates"]), (0.74, round(0.74 - 0.61 * 1.08, 4), []))
        rows, _ = self.rows(ledger, pol(min_margin_share=0.05) | {"seller": pol()["seller"] | {"floor_cost_mult": 1.15}})
        self.assertEqual(rows[0]["gates"], ["margin"])                 # 0.74 - 0.61 x 1.15 < 0.05
        rows, _ = self.rows(spread("GA", 2, NOW - 5 * H, NOW, amount=90000) + spread("GA", 8, NOW - 5 * H, NOW)
                            + spread("GB", 10, NOW - 5 * H, NOW, amount=37000))
        self.assertEqual({r["sku"]: r["p_clear"] for r in rows}, {"GA": 0.74, "GB": 0.74})  # GA: 2 prices, the kind's
        # 150 units bought by the market this hour: factor 1.2, so the margin goes
        rows, _ = self.rows(ledger + [supply("GA", NOW - 60, 1)], surge_units={"GA": 150})
        self.assertEqual((rows[0]["u1h"], rows[0]["f_now"], rows[0]["free_left"]), (150, 1.2, -100))
        self.assertIn("margin", rows[0]["gates"])
        rows, _ = self.rows(spread("GA", 10, NOW - 5 * H, NOW))         # no amounts: no p_clear, no margin gate
        self.assertEqual((rows[0]["p_clear"], rows[0]["margin"]), (None, None))

    def test_congestion_gate(self):
        ledger = spread("GA", 12, NOW - 6 * H, NOW)                     # 2 settles in the last hour
        self.assertEqual(self.rows(ledger + [supply("GA", NOW - 60, 3)])[0][0]["gates"], [])
        self.assertEqual(self.rows(ledger + [supply("GA", NOW - 60, 4)])[0][0]["gates"], ["congested"])
        # our own restock of the SKU is not congestion (it would push our focus SKUs out), but counts for surge
        r = self.rows(ledger + [supply("GA", NOW - 60, 20, shop=ME)], surge_units={"GA": 23})[0][0]
        self.assertEqual((r["gates"], r["u1h"], r["u1_other"]), ([], 23, 3))
        # a quiet last hour: compared with the 6 h rate, not with 0
        quiet = spread("GA", 12, NOW - 6 * H, NOW - H)
        self.assertEqual(self.rows(quiet + [supply("GA", NOW - 60, 3)])[0][0]["gates"], [])
        self.assertEqual(self.rows(quiet + [supply("GA", NOW - 60, 4)])[0][0]["gates"], ["congested"])

    def test_price_gate_only_after_the_floors_were_fixed_6h(self):
        ledger = spread("GA", 24, NOW - 6 * H, NOW)
        units = [{"unit": f"u{i}", "sku": "GA", "kind": "goods", "cost": 61000, "grade": "A",
                  "supplied_at": NOW - 7 * H} for i in range(3)]
        rows, checks = self.rows(ledger, units=units)
        self.assertEqual((rows[0]["gates"], rows[0]["held"]), ([], 3))
        self.assertEqual(checks, [{"sku": "GA", "held": 3, "s6": 24, "p_clear": None,
                                   "our_floor_list": round(max(0.60, 1.08 * 0.61), 3), "judged": False}])
        rows, checks = self.rows(ledger, units=units, floor_since=NOW - 5 * H)
        self.assertEqual((rows[0]["gates"], checks[0]["judged"]), ([], False))
        rows, checks = self.rows(ledger, units=units, floor_since=NOW - 6 * H)
        self.assertEqual((rows[0]["gates"], checks[0]["judged"]), (["price"], True))
        young = [dict(u, supplied_at=NOW - H) for u in units]           # held under 6 h: no flag
        self.assertEqual(self.rows(ledger, units=young, floor_since=NOW - 9 * H)[1], [])

    def test_floor_ok_since(self):
        p = pol()
        self.assertEqual(trend.floor_ok_since(None, p, NOW), NOW)
        self.assertEqual(trend.floor_ok_since(NOW - H, p, NOW), NOW - H)
        p["seller"]["floor_list"] = 0.80
        self.assertIsNone(trend.floor_ok_since(NOW - H, p, NOW))
        p["seller"].update(floor_list=0.70, floor_cost_mult=1.15)
        self.assertEqual(trend.floor_ok_since(None, p, NOW), NOW)


class ChooseTest(unittest.TestCase):
    @staticmethod
    def row(sku, s6, gates=(), focus_ok=True, trend_ok=True):
        return {"sku": sku, "s6": s6, "gates": list(gates), "focus_ok": focus_ok, "trend_ok": trend_ok}

    def test_focus_then_trend_with_food_shelf_rules(self):
        rows = trend.score(view(spread("GC", 20, NOW - 6 * H, NOW) + spread("GD", 15, NOW - 6 * H, NOW)
                                + spread("GF", 30, NOW - 6 * H, NOW) + spread("GA", 10, NOW - 6 * H, NOW),
                                since=NOW - 7 * H), CAT, pol(), NOW)[0]
        ok = {r["sku"]: (r["focus_ok"], r["trend_ok"]) for r in rows}
        self.assertEqual(ok, {"GC": (False, True), "GD": (True, True), "GF": (False, False), "GA": (True, True)})
        focus, tr = trend.choose(rows, [], [], pol(focus_max=1, trend_max=4))
        # value: the 100,000 sofa (10 settles) outranks the 20,000 rice (15) and the 10,000 kimchi (20); kimchi (168 h) only as a trend SKU
        self.assertEqual((focus, tr), (["GA"], ["GD", "GC"]))

    def test_hysteresis_swaps_and_gates(self):
        rows = [self.row(f"S{i}", 40 - i) for i in range(20)]
        p = pol(focus_max=4, trend_max=4)
        focus, tr = trend.choose(rows, [], [], p)
        self.assertEqual((focus, tr), (["S0", "S1", "S2"], ["S3", "S4", "S5", "S6"]))   # 3 and 4 new per run
        focus, tr = trend.choose(rows, ["S7"], ["S0", "S19"], p)
        self.assertEqual(focus, ["S7", "S0", "S1", "S2"])                # S7 ranks within 2 x 4
        self.assertEqual(tr, ["S3", "S4", "S5", "S6"])                   # S19 ranks too low: dropped
        self.assertEqual(trend.choose(rows, ["S7"], ["S19"], p, covered=False)[1][:1], ["S19"])
        rows[0]["gates"] = ["congested"]
        self.assertNotIn("S0", sum(trend.choose(rows, ["S0"], [], p), []))
        self.assertEqual(trend.choose(rows, [], [], pol(focus_max=4, trend_max=0))[1], [])

    def test_trend_max_falls_back_without_the_policy_key(self):
        p = pol(focus_max=1)
        p["stock"].pop("trend_max", None)
        self.assertEqual(len(trend.choose([self.row(f"S{i}", 40 - i) for i in range(30)], [], [], p)[1]),
                         min(trend.TREND_SWAPS, trend.FALLBACK["trend_max"]))


class SampleTest(unittest.TestCase):
    """One pass over the real ledger sample."""

    def test_sample(self):
        cat = collect._catalog(SAMPLE["catalog"])
        now = max(r["ts"] for r in SAMPLE["ledger"]) + 60
        t = collect.sku_tables(None, SAMPLE["ledger"], cat, lambda d: d.get("shop") == ME, ME, lambda u: None, now)
        v = collect.table_view(t, now)
        self.assertEqual((v["p_clear"]["goods"]["h24"], v["p_clear"]["food"]["h24"]), (0.75, 0.71))
        self.assertEqual(v["sku"]["G200e39a3"]["s6"], 63)               # kimchi, the most settled SKU
        self.assertEqual(v["c_kind"], {"food": 0.522, "goods": 0.561})    # our own supply rows
        rows, _ = trend.score(v, cat, pol(), now)
        focus, tr = trend.choose(rows, [], [], pol())
        self.assertEqual(rows[0]["sku"], "G8ec51935")                  # the 2.32 M sofa: 44 settles x sqrt(232)
        self.assertLess([r["sku"] for r in rows].index("G200e39a3"), 10)   # kimchi, most units, now rank 7
        self.assertIn("G1515d951", focus + tr)                         # a mattress the plan names
        self.assertTrue(set(focus).isdisjoint(tr))
        self.assertLessEqual((len(focus), len(tr)), (trend.FOCUS_SWAPS, trend.TREND_SWAPS))


if __name__ == "__main__":
    unittest.main()
