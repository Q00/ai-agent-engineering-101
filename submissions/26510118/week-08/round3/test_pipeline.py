"""PC test of the Round 3 seller (trading module and ledger collection) and the basic buyer;
no market needed.

    python test_pipeline.py      (a2a-sdk[http-server], uvicorn, httpx, cryptography)

It writes a fake .market (catalog, demand, an inventory whose units carry certificates
signed by a throwaway market key, orders, the shop list) into a temporary folder with a
throwaway shop key, starts seller.py on a free port, and talks to it two ways: through
market_buyer.py the way the kit runs it, and in scripted A2A conversations. The kit's own
code from scripts/market_kit.py judges the results: verify_seller (the receipt),
AcpShop.create (the checkout the kit would open for that receipt) and check_accept (what
the buyer's kit checks before it pays). Prices (pricing.py), restock (restock.py) and the
ledger summary (ledger.py) are tested as functions. The market's /api/settle is the one
step this cannot run. The round's end is moved 10 days ahead, so the wind-down rules are
tested on purpose and never by the calendar.
"""
import asyncio
import base64
import gzip
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="round3-test-"))
NOW = time.time()
os.environ["MARKET_DIR"] = str(ROOT / ".market")
os.environ["ROUND_END_TS"] = str(NOW + 10 * 86400)

from cryptography.hazmat.primitives import serialization                     # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import common                                                                    # noqa: E402
import ledger                                                                   # noqa: E402
import market_buyer as mb                                                        # noqa: E402
import pricing                                                                   # noqa: E402
import restock                                                                   # noqa: E402
import seller                                                                    # noqa: E402
import tools                                                                     # noqa: E402

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("market_kit", os.environ.get("KIT_FILE")                 # the server keeps it elsewhere
                                              or HERE.parents[3] / "scripts" / "market_kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

H, DAY = 3600, 86400
KIMCHI, GEOTJEORI, PAKIMCHI = "Gaaaa0001", "Gaaaa0002", "Gaaaa0003"
BEDDING, PAD, WATER, BERRY, BED = "Gbbbb0001", "Gbbbb0002", "Gcccc0001", "Gdddd0001", "Geeee0002"
KIMCHI_CAT, BEDDING_CAT = "식품>김치/반찬/즉석요리>김치", "가구/침구>침구/홈데코>침구"
FILLERS = [f"Gf{i:07x}" for i in range(12)]


def item(sku, name, cat, price, kind, shelf=None):
    return {"sku": sku, "name": name, "category": cat, "list_price": price, "kind": kind, "shelf_hours": shelf}


CATALOG = {s["sku"]: s for s in [
    item(KIMCHI, "테스트 포기김치 2kg", KIMCHI_CAT, 20000, "food", 168),
    item(GEOTJEORI, "테스트 겉절이 2kg", KIMCHI_CAT, 13400, "food", 168),
    item(PAKIMCHI, "테스트 파김치 2kg", KIMCHI_CAT, 25560, "food", 168),
    item(BEDDING, "테스트 차렵이불 SS", BEDDING_CAT, 39900, "goods"),
    item(PAD, "테스트 극세사 패드", BEDDING_CAT, 27900, "goods"),
    item(WATER, "테스트 생수 2L", "식품>음료>주스/코코아/생수", 15000, "food", 720),
    item(BERRY, "테스트 딸기 1kg", "식품>야채/과일>신선과일(부패주의)", 19900, "food", 48),
    item(BED, "테스트 침대", "가구/침구>가구>침대/매트리스", 344640, "goods"),
] + [item(s, f"테스트 가공식품 {i}", f"식품>가공>테스트{i}", 10000 + 1000 * i, "food", 720) for i, s in enumerate(FILLERS)]}
DEMAND = [
    {"category": BEDDING_CAT, "item": "침구", "kind": "goods", "skus": 540, "median_price": 39900, "shelf_hours": None},
    {"category": "식품>음료>주스/코코아/생수", "item": "주스/코코아/생수", "kind": "food", "skus": 336, "median_price": 15200, "shelf_hours": 720},
    {"category": KIMCHI_CAT, "item": "김치", "kind": "food", "skus": 313, "median_price": 25560, "shelf_hours": 168},
    {"category": "식품>야채/과일>신선과일(부패주의)", "item": "신선과일(부패주의)", "kind": "food", "skus": 298, "median_price": 19900, "shelf_hours": 48},
    {"category": "가구/침구>가구>침대/매트리스", "item": "침대/매트리스", "kind": "goods", "skus": 128, "median_price": 344640, "shelf_hours": None},
] + [{"category": f"식품>가공>테스트{i}", "item": f"테스트{i}", "kind": "food", "skus": 20 - i, "median_price": 10000 + 1000 * i,
      "shelf_hours": 720} for i in range(12)]
MODEL_REPLY = json.dumps({"skus": [KIMCHI], "need_hours": 72, "warranty": False, "grades": []})

results = []


def check(name: str, ok, detail="") -> None:
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""), flush=True)


def b64key(key) -> str:
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)).decode()


class Fake:
    """The temporary .market folder, the throwaway keys, and the files the kit would write."""

    def __init__(self):
        self.md = ROOT / ".market"
        self.md.mkdir(parents=True, exist_ok=True)
        self.market_key, self.shop_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        (ROOT / "shop_key.pem").write_bytes(self.shop_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.write("catalog.json", CATALOG)
        self.write("demand.json", DEMAND)
        common.write_json(self.md / "ledger" / "state.json", {"seq": 0, "at": NOW})   # no real ledger calls
        self.env = dict(os.environ, SHOP_KEY_FILE=str(ROOT / "shop_key.pem"), PYTHONUTF8="1",
                        HARNESS_FAKE_REPLY=MODEL_REPLY)

    def write(self, name: str, data) -> Path:
        path = self.md / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def unit(self, unit, sku, grade, cost, hours_left=None, age_days=0.5) -> dict:
        c = CATALOG[sku]
        expires = time.time() + hours_left * H if hours_left is not None else None
        body = {"unit": unit, "sku": sku, "grade": grade, "expires_at": expires}
        cert = {"cert": body, "sig": base64.b64encode(self.market_key.sign(kit.canon(body))).decode()}
        return {"unit": unit, "sku": sku, "name": c["name"], "category": c["category"], "list_price": c["list_price"],
                "kind": c["kind"], "cost": cost, "grade": grade, "supplied_at": time.time() - age_days * DAY,
                "expires_at": expires, "cert": cert}

    def inventory(self, units: list, balance: int = 4_900_000, rnd: int = 3) -> Path:
        return self.write("inventory.json", {"round": rnd, "balance": balance, "start": 5_000_000, "injected": 0,
                                             "bankruptcies_left": 2, "bankrupt_line": 500_000,
                                             "storage_per_day": 0, "units": units})

    def order(self, oid, item_name, budget, acceptable, kind, use_within=None) -> dict:
        cat = CATALOG[acceptable[0]]
        o = {"order": oid, "item": item_name, "category": cat["category"], "budget": budget, "acceptable": acceptable,
             "kind": kind, "expires": time.time() + 600, "round": 3, "market_pubkey": b64key(self.market_key),
             "shelf_hours": cat["shelf_hours"]}
        if use_within:
            o["use_within_hours"] = use_within
        self.write("order.json", o)
        return o

    def run(self, *args) -> tuple[dict | None, list]:
        p = subprocess.run([sys.executable, *args], cwd=HERE, env=self.env, capture_output=True, text=True,
                           encoding="utf-8", timeout=280)
        lines = (p.stdout + p.stderr).splitlines()
        return kit.last_json(lines), lines


class FakeMarket:
    """Answers the one market call AcpShop.create makes: GET /api/inventory."""

    def __init__(self, path: Path):
        self.path = path

    def call(self, method, path, role="buyer", **kw):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return types.SimpleNamespace(status_code=200, json=lambda: data)


def kit_checks(fake: Fake, receipt: dict, order: dict, inv_path: Path, accept: dict):
    """What the kit does with a receipt before it pays. Returns (checkout payload, refusal or None)."""
    rec = dict(receipt)
    if isinstance(rec.get("price"), float) and rec["price"].is_integer():
        rec["price"] = int(rec["price"])                         # A2A carries numbers as doubles, as the kit fixes
    check("kit: sig_seller verifies with the shop key", kit.verify_seller(b64key(fake.shop_key), rec))
    check("kit: receipt fits the order (acceptable SKU, whole price within budget)",
          rec["item"] in order["acceptable"] and isinstance(rec["price"], int) and rec["price"] <= order["budget"],
          f"{rec['item']} at {rec['price']} of budget {order['budget']}")
    acp = kit.AcpShop(fake.shop_key, FakeMarket(inv_path))
    acp.terms[rec["task"]] = receipt                              # what the kit's proxy keeps from the seller's reply
    resp = asyncio.run(acp.create({"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec,
                                   "acceptable": order["acceptable"]}, "http://test-shop"))
    check("kit: checkout session opens", resp.status_code == 201, f"status {resp.status_code}")
    co = kit.jwt_payload(json.loads(resp.body).get("checkout_jwt", ""), acp.pub) or {}
    return co, kit.check_accept(order, co, accept)


async def talk(url: str, texts: list, cancel: bool = True) -> list:
    """A scripted A2A conversation: one task, the given messages, every reply kept."""
    c = mb.A2A()
    await c.open(url)
    out, t = [], None
    for text in texts:
        t = await c.send(text, t["id"] if t else None)
        out.append(t)
        if mb.state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
    if cancel and t and mb.state(t) == "TASK_STATE_INPUT_REQUIRED":
        await c.cancel(t["id"])
    await c.close()
    return out


def meta(t) -> dict:
    return (t or {}).get("status", {}).get("message", {}).get("metadata") or {}


def said(t) -> str:
    return mb.said(t)


def test_pricing():
    print("\n[S3 S4 S5] prices (pricing.py; Kiro 2.0 since 11/6 12:40)")
    end = common.ROUND_END
    now = end - 5 * DAY
    food = {"sku": KIMCHI, "list_price": 20000, "cost": 12000, "kind": "food", "grade": "A",
            "supplied_at": now - 0.5 * DAY, "expires_at": now + 150 * H}
    p = pricing.plan(food, now, catalog=CATALOG)
    check("fresh food, grade A: ask 75% of list, floor cost + 2%", (p["ask"], p["floor"], p["grade"]) == (15000, 12240, "A"), p)
    goods_c = {"sku": BEDDING, "list_price": 39900, "cost": 14000, "kind": "goods", "grade": "C", "supplied_at": now - DAY}
    p = pricing.plan(goods_c, now)
    check("refurbished goods always come with the warranty: 60% + 33% (37107), floor cost + 2% + 33% of list (27447; "
          "20% until 11/13)", (p["ask"], p["floor"], p["warranty_hours"]) == (37107, 27447, 6), p)
    check("3.5 days unsold: 5% of list lower", pricing.plan(dict(goods_c, supplied_at=now - 3.5 * DAY), now)["ask"] == 35112)
    p = pricing.plan(dict(goods_c, supplied_at=now - 8 * DAY), now)
    check("8 days unsold goods, 5 days before the end: the floor goes under cost only by what holding it costs (5 x 1%: 13,300)",
          (p["floor"], p["at_cost"]) == (13300, False), p)
    near = dict(food, cost=10000, expires_at=now + 10 * H)
    p = pricing.plan(near, now, catalog=CATALOG)
    check("food with 10 h left: ask 45%, floor 25% of list", (p["ask"], p["floor"]) == (9000, 5000), p)
    p = pricing.plan(dict(food, expires_at=end + 140 * H, supplied_at=end - 10 * H - 0.5 * DAY), end - 10 * H, catalog=CATALOG)
    check("last day: food that keeps past the end is priced as ever (it counts at cost in the book)", (p["ask"], p["floor"]) == (15000, 12240), p)
    p = pricing.plan(dict(food, expires_at=end + 140 * H, supplied_at=end - 3 * H - 0.5 * DAY), end - 3 * H, catalog=CATALOG)
    check("last 6 h: no fire sale either", p["floor"] == 12240, p)
    goods_a = dict(goods_c, cost=26000, grade="A")
    p = pricing.plan(goods_a, now, warranty=True)
    check("warranty on grade A goods: +3% of list on ask and floor (its breakage is 3.1%)",
          (p["ask"], p["floor"], p["warranty_hours"]) == (31122, 27717, 6), p)
    goods_b = dict(goods_c, cost=20000, grade="B")
    check("with a warranty: A 78%, B 81%, C 93% (each grade's extra follows its breakage, 11/13)",
          [pricing.plan(u, now, warranty=True)["ask"] for u in (goods_a, goods_b, goods_c)] == [31122, 32319, 37107])
    check("no warranty on food", pricing.plan(food, now, warranty=True, catalog=CATALOG)["warranty_hours"] is None)
    dear_a_unit, dear_b_unit = dict(goods_a, list_price=2_000_000, cost=1_300_000), dict(goods_b, list_price=2_000_000, cost=1_000_000)
    wh = lambda u, **k: pricing.plan(u, now, dear_default=True, **k)["warranty_hours"]
    check("11/13 trial switch: dear grade A goods carry the warranty unasked only while dear_a is on; asked, they carry it; "
          "dear B and A under 500k keep it unasked",
          (wh(dear_a_unit), wh(dear_a_unit, dear_a=False), wh(dear_a_unit, dear_a=False, warranty=True),
           wh(dear_b_unit, dear_a=False), wh(dict(goods_a, list_price=400_000), dear_a=False)) == (6, None, 6, 6, 6))
    shelf = CATALOG[KIMCHI]["shelf_hours"]
    edge = dict(food, expires_at=now + pricing.FOOD_CUTS[1] * shelf * H + 120)
    check("food dropping to C within the claim margin is claimed C (true at settle)",
          (pricing.grade(edge, now, CATALOG), pricing.claim(edge, now, CATALOG)) == ("B", "C"))
    later = dict(food, expires_at=now + pricing.FOOD_CUTS[1] * shelf * H + 1800)
    check("food dropping in 30 minutes is claimed at its grade now (a settle comes within minutes)",
          pricing.claim(later, now, CATALOG) == "B")
    mk = {"A": {"w_n": 40, "w_median": 0.78, "nw_n": 30, "nw_median": 0.77}, "B": {"w_n": 25, "w_median": 0.74, "nw_n": 5, "nw_median": 0.60},
          "C": {"w_n": 25, "w_median": 0.70, "nw_n": 20, "nw_median": 0.69}}
    p = pricing.plan(goods_c, now, market=mk)
    check("no market of its own SKU: the category's (refurbished with a warranty 0.70 + 2%) moves our 93% by at most 10 "
          "points, to 83%", (p["ask"], p["ref"]) == (33117, 0.7), p)
    p = pricing.plan(goods_b, now, warranty=True, market=mk)
    check("B with a warranty: the category's 0.74 + 2% is 5% under our 81%: 76%; floor cost + 2% + 11%",
          (p["ask"], p["floor"]) == (30324, 20400 + 4389), p)
    check("A with a warranty: 0.78 + 2% is within 3% of our 78%: unchanged",
          (lambda q: (q["ask"], q["ref"]))(pricing.plan(goods_a, now, warranty=True, market=mk)) == (31122, None))
    check("B without a warranty: 5 market settles are too few to count", pricing.plan(goods_b, now, market=mk)["ref"] is None)
    check("exactly 3% of list apart is within the band (no float edge): food A 75% stays with the market at 0.70 + 2%",
          pricing.plan(food, now, catalog=CATALOG, market={"A": {"nw_n": 30, "nw_median": 0.70}})["ask"] == 15000)
    check("food with no market data is unchanged", pricing.plan(food, now, catalog=CATALOG, market={})["ask"] == 15000)
    check("the late cycle adds 2% of list (when no SKU market sets the ask)", pricing.plan(food, now, catalog=CATALOG, late=True)["ask"] == 15400)
    check("a market-referenced ask never goes below the floor",
          pricing.plan(dict(goods_b, cost=30000), now, market={"B": {"nw_n": 99, "nw_median": 0.40}})["ask"] == 30600)
    own = {"A": {"n": 12, "median": 0.68}}
    p = pricing.plan(food, now, catalog=CATALOG, sku_market=own)
    check("Kiro 2.0: a SKU with its own market of the grade (0.68, 12 settles) is first asked at it + 2% (70%)",
          (p["ask"], p["sku_ref"], p["nudge"], p["anchored"]) == (14000, 0.68, -0.05, True), p)
    check("...on 9 settles there is no market of its own (the rule's 75%); the premium line is anchored like the rest",
          pricing.plan(food, now, catalog=CATALOG, sku_market={"A": {"n": 9, "median": 0.60}})["ask"] == 15000
          and pricing.plan(food, now, catalog=CATALOG, sku_market=own, in_line=True)["ask"] == 14000
          and pricing.plan(food, now, catalog=CATALOG, sku_market=own, late=True)["ask"] == 14000)
    check("...the offset comes from the caller when given (an experiment's arm: market - 2%)",
          pricing.plan(food, now, catalog=CATALOG, sku_market=own, ask_over=-0.02)["ask"] == 13200)
    fm = pricing.plan(food, now, catalog=CATALOG, sku_market=own, food_floor="high")
    fc = pricing.plan(food, now, catalog=CATALOG, sku_market=own, food_floor="cost")
    check("11/12, the food price experiment: the \"high\" arm asks 4% of list more (0.74: 14,800 against 14,000) and holds "
          "food at its market less 2% of list (0.66: 13,200) over the cost floor (12,240), and says so; the \"cost\" arm, "
          "food on clearance and goods keep the cost floor",
          (fm["ask"], fm["floor"], fm["market_floor"], fc["ask"], fc["floor"], fc["market_floor"])
          == (14800, 13200, True, 14000, 12240, False)
          and pricing.plan(dict(food, cost=10000, expires_at=now + 10 * H), now, catalog=CATALOG, sku_market=own,
                           food_floor="high")["floor"] == 5000
          and pricing.plan(dict(food, kind="goods"), now, sku_market=own, food_floor="high")["market_floor"] is False,
          (fm, fc))
    fx = pricing.plan(food, now, catalog=CATALOG, sku_market=own, food_floor="max")
    check("...the \"max\" arm asks 8% of list more (0.78: 15,600) and holds the market itself (0.68: 13,600); the seller then "
          "says the price is about the middle of what other shops have been paid, the \"high\" arm 2% under it",
          (fx["ask"], fx["floor"], fx["market_floor"], fx["market_gap"], fm["market_gap"]) == (15600, 13600, True, 0.0, 0.02)
          and "about the middle of what other shops have been paid" in seller.Seller.reason(None, {"plan": fx})
          and "about 2% of its list price under" in seller.Seller.reason(None, {"plan": fm}), fx)
    p = pricing.plan(goods_a, now, warranty=True, sku_market={"A": {"n": 30, "median": 0.70}, "A_w": {"n": 12, "median": 0.72}})
    check("with a warranty it is set by the market's warranty settles (0.72 + 2% = 74%)", (p["ask"], p["sku_ref"]) == (29526, 0.72), p)
    check("...and never under the floor (cost + 2% + 3%) while the unit is fresh",
          pricing.plan(dict(goods_a, supplied_at=now - 3600), now, warranty=True, sku_market={"A_w": {"n": 12, "median": 0.60}})["ask"] == 27717)
    p = pricing.plan(goods_c, now, sku_market={"C_w": {"n": 15, "median": 0.70}})
    check("a refurbished unit (always with its warranty) is set by the C-with-a-warranty market (0.70 + 2% = 72%)",
          (p["ask"], p["anchored"]) == (28728, True), p)
    check("...but not on clearance (8 days unsold: no warranty, no anchor); grade B is anchored too",
          pricing.plan(dict(goods_c, supplied_at=now - 8 * DAY), now, sku_market={"C": {"n": 15, "median": 0.70}})["nudge"] is None
          and pricing.plan(dict(goods_b, supplied_at=now - 3600), now, sku_market={"B": {"n": 50, "median": 0.50}})["ask"] == 20748)
    dear_a = dict(goods_a, list_price=2_000_000, cost=1_200_000)
    p = pricing.plan(dear_a, now, warranty=True, in_line=True, solo=True)
    check("the uncontested asks and floors are off: an uncontested line product is priced by the rules (78%, cost + 2% + 3%)",
          (p["ask"], p["floor"], p["solo"]) == (1_560_000, 1_284_000, False), p)
    check("...and the uncontested floor learning with them",
          not pricing.learn_solo({}, "S", p, [1_375_000, 1_472_000], 2_000_000, now))
    p = pricing.plan(dear_a, now, warranty=True, in_line=True, sku_market={"A_w": {"n": 20, "median": 0.86}})
    check("a line product with its own market (0.86 with a warranty) is asked at it + 2% (88%)",
          p["ask"] == 1_760_000 and p["nudge"] == 0.1, p)
    check("...and with a market of 0.70: at 72%, not held at the rule's 78%",
          pricing.plan(dear_a, now, warranty=True, in_line=True, sku_market={"A_w": {"n": 20, "median": 0.70}})["ask"] == 1_440_000)
    check("other shops' first asks for the SKU (price_map, 0.84) cap a grade A first ask at them - 0.5% (83.5%); none: as before",
          pricing.plan(dear_a, now, warranty=True, in_line=True, sku_market={"A_w": {"n": 20, "median": 0.86}}, comp_ask=0.84)["ask"] == 1_670_000
          and pricing.plan(dear_a, now, warranty=True, in_line=True, sku_market={"A_w": {"n": 20, "median": 0.86}}, comp_ask=0.95)["ask"] == 1_760_000)
    check("...not grade B (its own market only)",
          pricing.plan(dict(goods_b, supplied_at=now - 3600), now, sku_market={"B": {"n": 50, "median": 0.50}}, comp_ask=0.30)["ask"] == 20748)
    dead_mk = {"A_w": {"n": 20, "median": 0.60}}
    p = pricing.plan(dict(dear_a, supplied_at=now - 3 * 3600), now, warranty=True, in_line=True, sku_market=dead_mk)
    check("dead stock: a unit held 3 h whose floor (64.2%) is over its market + 1% (61%): floor at cost / (1 - 3.1%), asked at the market",
          (p["floor"], p["ask"], p["dead"], p["stuck"], p["at_cost"]) == (1_238_391, 1_238_391, True, False, False), p)
    p = pricing.plan(dear_a, now, warranty=True, in_line=True, sku_market=dead_mk)
    check("...held 6 h+ and still over its market at cost (11/7): at the market (60%), never more than 5% under its cost (57%)",
          (p["floor"], p["ask"], p["stuck"]) == (1_200_000, 1_200_000, True)
          and pricing.plan(dear_a, now, warranty=True, in_line=True, sku_market={"A_w": {"n": 20, "median": 0.50}})["floor"] == 1_140_000, p)
    p = pricing.plan(dict(dear_a, supplied_at=now - 3600), now, warranty=True, in_line=True, sku_market=dead_mk)
    check("...held only an hour: still at its floor (cost + 2% + 3%)", (p["floor"], p["dead"]) == (1_284_000, False), p)
    win = {"open": False, "opens_at": now + 300, "closes_at": now + 480}
    shelf_h = CATALOG[KIMCHI]["shelf_hours"]
    edge = {"sku": KIMCHI, "unit": "u-edge", "kind": "food", "cost": 12000, "list_price": 20000,
            "expires_at": now + 0.8 * shelf_h * 3600 + 60}
    steady = dict(edge, unit="u-steady", expires_at=now + 0.8 * shelf_h * 3600 + 3600)
    check("a food unit a minute from losing grade A is crossing (its claim may miss the market's grade); an hour from it, not",
          pricing.crossing(edge, now, CATALOG) and not pricing.crossing(steady, now, CATALOG))
    import seller as _sel
    need = {"grades": [], "need_hours": 24, "warranty": False}
    check("the seller offers the steady unit though the other expires sooner (11/6: 7 food claims a day missed); "
          "the crossing one only when it is the only one", _sel.pick([edge, steady], need, now)["unit"] == "u-steady"
          and _sel.pick([edge], need, now)["unit"] == "u-edge")
    check("late in the cycle: the last 10 minutes before a window opens, not during one or long before",
          pricing.late_in_cycle(win, now) and not pricing.late_in_cycle(dict(win, opens_at=now + 900), now)
          and not pricing.late_in_cycle(dict(win, open=True), now) and not pricing.late_in_cycle(None, now))


def test_categories():
    print("\n[S2] category names and the menu (seller.py)")
    names = ["즉석조리식품", "조리/가공 견과류", "가공/조리 오징어/문어류", "조리/가공 과일", "곡물", "시리얼/곡물/콩류세트", "청소기",
             "청소/생활선물세트"]
    seller._leafs[:] = sorted(names, key=len, reverse=True)
    got = {t: seller.category_in(t, []) for t in ("I want to buy a 즉석조리식품", "곡물 사고 싶어요", "청소기 one please",
                                                  "시리얼/곡물/콩류세트 주세요", "가공 견과류 please")}
    check("a whole category name wins over a longer category sharing a part (즉석조리식품 is not 조리/가공 견과류)",
          got == {"I want to buy a 즉석조리식품": "즉석조리식품", "곡물 사고 싶어요": "곡물", "청소기 one please": "청소기",
                  "시리얼/곡물/콩류세트 주세요": "시리얼/곡물/콩류세트", "가공 견과류 please": "조리/가공 견과류"}, got)
    seller._leafs.clear()
    prods = [{"sku": f"P{p}", "list_price": p} for p in (5000, 12000, 20000, 31000, 60000)]
    check("budget 40000: only list prices 18000-40000, nearest 28000 first",
          [u["sku"] for u in seller.choose(prods, "x", 40000)] == ["P31000", "P20000"])
    check("no budget: nearest the asked SKU's list price first, then the cheapest and the dearest",
          [u["sku"] for u in seller.choose(prods, "x", None, anchor=13000)] == ["P12000", "P5000", "P60000"])


def unit_row(unit, sku, cost, hours=None, grade="A", age=0.5, now=None):
    now = now or NOW
    c = CATALOG[sku]
    return {"unit": unit, "sku": sku, "cost": cost, "grade": grade, "category": c["category"], "kind": c["kind"],
            "list_price": c["list_price"], "supplied_at": now - age * DAY,
            "expires_at": now + hours * H if hours is not None else None}


def test_restock(fake: Fake):
    print("\n[S1] restock (restock.py)")
    inv = lambda units, balance=4_900_000: {"round": 3, "balance": balance, "bankrupt_line": 500_000,
                                            "bankruptcies_left": 2, "units": units}
    rules = dict(common.RULES["seller"])
    one, two = restock.stages(CATALOG, DEMAND, {})
    check("stage 1: a product per category, median-priced, no 48 h food, nothing over 60,000",
          PAKIMCHI in one and BEDDING in one and WATER in one and BERRY not in one + two and BED not in one, one)
    check("stage 2: cheaper second products and the dear-only categories", PAD in two and BED in two and KIMCHI in two, two)
    one2, _ = restock.stages(CATALOG, DEMAND, {"by_sku": {GEOTJEORI: {"sold_24h": 5}}})
    check("the market's best seller in a category becomes its representative", GEOTJEORI in one2 and PAKIMCHI not in one2)
    check("stage 1 mixes the groups, not the market's alphabetical order: each group's first product first "
          "(the larger group ahead), then the 14 foods in turn", one[:4] == [WATER, BEDDING, PAKIMCHI, FILLERS[0]], one)
    out, info = restock.plan(inv([]), CATALOG, DEMAND, {}, [], [], rules, NOW)
    buy = out["buy"]
    check("empty shelf, no evidence yet: 10 lines of one unit each, stage 1 in its own order",
          [b["sku"] for b in buy] == one[:10] and all(b["qty"] == 1 for b in buy), [b["sku"] for b in buy])
    shut = dict(inv([]), restock={"open": False, "opens_at": NOW + 600, "closes_at": NOW + 780})
    out, info = restock.plan(shut, CATALOG, DEMAND, {}, [], [], rules, NOW)
    check("supply window shut for 10 more minutes: buys nothing and says when it opens",
          out["buy"] == [] and info["note"] == "supply closed; the next window opens in 600 s", info)
    soon = dict(inv([]), restock={"open": False, "opens_at": NOW + 60, "closes_at": NOW + 240})
    check("a window opening within 90 s: the run plans its 10 lines (the kit waits for it)",
          len(restock.plan(soon, CATALOG, DEMAND, {}, [], [], rules, NOW)[0]["buy"]) == 10)
    closing = dict(inv([]), restock={"open": True, "opens_at": NOW - 135, "closes_at": NOW + 45})
    out, info = restock.plan(closing, CATALOG, DEMAND, {}, [], [], rules, NOW)
    check("an open window with 45 s left: only the 4 lines it has time for, the most needed first",
          [b["sku"] for b in out["buy"]] == one[:4] and info["lines"] == 4, out["buy"])
    shelf1 = [unit_row(f"u-{s}", s, CATALOG[s]["list_price"] // 2, 500 if CATALOG[s]["kind"] == "food" else None) for s in one]
    fast = [{"sku": WATER, "ts": NOW - H}] * 45
    out, info = restock.plan(inv(shelf1), CATALOG, DEMAND, {}, fast, [], rules, NOW, stocked=set(one))
    check("a fast seller is stocked for its pace: 45 sold in 3 h, food: 3 hours of it (11/12; 40 minutes before) -> a "
          "line's most, 20 (19 with the one held), one line",
          out["buy"][0] == {"sku": WATER, "qty": 19} and info["deep"] == 1, out["buy"][:2])
    sold10 = [{"sku": s, "ts": NOW - H} for s in FILLERS[:10]]
    out, info = restock.plan(inv([]), CATALOG, DEMAND, {}, sold10, [], rules, NOW, stocked=set(FILLERS[:10]))
    skus = [b["sku"] for b in out["buy"]]
    check("exploring: 6 lines refill what sold, 4 go to products never stocked (the stage order)",
          skus == FILLERS[:6] + [WATER, BEDDING, PAKIMCHI, FILLERS[10]] and info["new"] == 4, skus)
    busy_kimchi = {"by_category": {KIMCHI_CAT: {"sold_24h": 9}}}
    out, _ = restock.plan(inv([]), CATALOG, DEMAND, busy_kimchi, sold10, [], rules, NOW, stocked=set(FILLERS[:10]) | {PAKIMCHI})
    skus = [b["sku"] for b in out["buy"]]
    check("exploring: categories we never had come before a second product of a busy category we know",
          skus[6:] == [WATER, BEDDING, FILLERS[10], FILLERS[11]] and KIMCHI not in skus, skus)
    out, info = restock.plan(inv([]), CATALOG, DEMAND, {}, sold10, [], rules, NOW, stocked=set(FILLERS[:10]), phase="focus")
    check("focus phase: one line for a new product", info["new"] == 1 and len(out["buy"]) == 10, out["buy"])
    check("the kit runs restock every 20 minutes: a product we sold is kept at 3, a hot category starts at 2",
          restock.RUN_EVERY == 1200 and restock.SOLD_TARGET == 3 and restock.HOT_TARGET == 2)
    hot = {"by_category": {CATALOG[FILLERS[11]]["category"]: {"sold_24h": 5}}}
    asked = [{"category": CATALOG[FILLERS[10]]["category"], "ts": NOW - H}, {"category": KIMCHI_CAT, "ts": NOW - 2 * DAY}]
    out, _ = restock.plan(inv([]), CATALOG, DEMAND, hot, [], [], rules, NOW, {"테스트9": 1}, asked)
    skus = [b["sku"] for b in out["buy"]]
    check("evidence first: the market's 5 sales (2 units), a wanted post, one order of our buyer; then the stage order "
          "(an order 2 days old counts for nothing)",
          skus[:6] == [FILLERS[11], FILLERS[9], FILLERS[10], WATER, BEDDING, PAKIMCHI] and out["buy"][0]["qty"] == 2
          and all(b["qty"] == 1 for b in out["buy"][1:]), out["buy"])
    shelf = [unit_row(f"u-{s}", s, CATALOG[s]["list_price"] // 2, 500 if CATALOG[s]["kind"] == "food" else None) for s in one]
    out, _ = restock.plan(inv(shelf), CATALOG, DEMAND, {}, [], [], rules, NOW)
    check("full stage 1 shelf, gate closed: buys nothing", out["buy"] == [], out)
    sales = [{"sku": PAKIMCHI, "ts": NOW - H}] * 10
    out, info = restock.plan(inv(shelf), CATALOG, DEMAND, {}, sales, [], rules, NOW)
    skus = [b["sku"] for b in out["buy"]]
    check("10 sales open the gate: the sold product first (food: 3 hours of its pace since 11/12, so 10, 9 more with the one "
          "held; 2 more before), then stage 2",
          out["buy"][0] == {"sku": PAKIMCHI, "qty": 9} and {PAD, BED, KIMCHI} <= set(skus), f"{out['buy']} {info['gate']}")
    out, _ = restock.plan(inv(shelf), CATALOG, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], rules, NOW)
    check("a buyer turned away for lack of it: one more", out["buy"] == [{"sku": WATER, "qty": 1}], out)
    away = lambda n, cat, age=H, sku=None: [{"ts": NOW - age, "sku": sku, "category": cat, "reason": "out"}] * n
    check("missed: a whole category turned away 3 times in 6 hours counts; twice, an old one or one SKU does not",
          restock.missed(away(3, "김치") + away(2, "침구") + away(5, "곡물", 7 * H) + away(4, "과자", sku=WATER), NOW) == {"김치": 3})
    out, _ = restock.plan(inv(shelf), CATALOG, DEMAND, {}, [], away(3, "김치"), rules, NOW)
    check("...and the category's product we list gets one more", out["buy"] == [{"sku": PAKIMCHI, "qty": 1}], out)
    import seller as _seller
    dear = {"sku": BED, "unit": "u-bed", "list_price": 344640, "cost": 200000, "kind": "goods", "grade": "A",
            "category": CATALOG[BED]["category"], "supplied_at": NOW - H}
    cheap = dict(dear, sku=BEDDING, list_price=39900, cost=26000, category=CATALOG[BEDDING]["category"])
    check("the seller quotes dear goods (100,000 or more) with the warranty unasked, cheap ones without",
          _seller.priced(dear, NOW, False)["warranty_hours"] == 6 and _seller.priced(cheap, NOW, False)["warranty_hours"] is None
          and _seller.priced(cheap, NOW, True)["warranty_hours"] == 6)
    end = common.ROUND_END
    check("...but not unsold 8 days, where it would lift the lowered floor; on the last day it stays (no fire sale); asked for, it comes",
          _seller.priced(dict(dear, supplied_at=NOW - 8 * DAY), NOW, False)["warranty_hours"] is None
          and _seller.priced(dict(dear, supplied_at=end - 11 * H), end - 10 * H, False)["warranty_hours"] == 6
          and _seller.priced(dict(dear, supplied_at=NOW - 8 * DAY), NOW, True)["warranty_hours"] == 6)
    path = common.MARKET_DIR / "market_summary.json"
    kept = path.read_bytes() if path.exists() else None
    cells = {"A_w": {"median": 0.78, "n": 20}, "B_w": {"median": 0.72, "n": 20}, "C_w": {"median": 0.69, "n": 20}}
    common.write_json(path, {"by_sku": {BED: {"others_6h": cells}}, "by_category": {}})
    _seller._files.pop("market_summary.json", None)          # the seller reads a file again when its mtime moves;
    a_dear, a_ok = dict(dear, unit="u-a1", cost=290_000), dict(dear, unit="u-a2", cost=210_000)
    b, c = dict(dear, unit="u-b", grade="B", cost=150_000), dict(dear, unit="u-c", grade="C", cost=110_000)
    req = {"grades": [], "need_hours": None, "warranty": False}
    check("S1 goods margin first: of an A bought dear (floor 0.93 of list, its market 0.78), a cheaper A, a B and a C, "
          "the C (0.69 x list - cost: the most over cost) is offered",
          _seller.pick([a_dear, a_ok, b, c], req, NOW)["unit"] == "u-c")
    check("...a buyer who asks for grade A gets the A under its market, not the dear one listed first",
          _seller.pick([a_dear, a_ok, b, c], dict(req, grades=["A"]), NOW)["unit"] == "u-a2")
    check("...a dear A alone is still offered (the least over its market)", _seller.pick([a_dear], req, NOW)["unit"] == "u-a1")
    said = {"No grade C please.": ["A", "B"], "grade C is not acceptable for me": ["A", "B"],
            "Grade C refurbished items are not acceptable.": ["A", "B"], "I don't want grade C": ["A", "B"],
            "Grade A or B only, no grade C.": ["A", "B"], "not grade C but grade A": ["A"], "Grade A only.": ["A"],
            "C등급 말고 A등급으로 주세요": ["A"], "C등급 제외": ["A", "B"], "Please quote grade A, avoid grade B and C": ["A"],
            "I prefer grade A with the warranty; grade B with the warranty otherwise. Please state the grade.": ["A", "B"],
            "Any grade is fine, 72 hours of shelf life": []}
    got = {t: _seller.needs(t)["grades"] for t in said}
    check("11/13: a grade named in a refusal is avoided, not insisted on (\"no grade C\" -> A or B; until then it read as C only)",
          got == said, {t: g for t, g in got.items() if g != said[t]})
    check("...so a buyer who says \"no grade C\" is offered the A, never the C",
          _seller.pick([a_ok, c], dict(req, grades=_seller.needs("No grade C please.")["grades"]), NOW)["unit"] == "u-a2")
    p0, p4 = _seller.priced(a_ok, NOW, False), _seller.priced(a_ok, NOW, False, 0.04)
    check("Kiro 2.0 stage 2: a talk's arm sets its anchored first ask over the market (+0.04 against the usual +0.02: 2% of list more)",
          p0["anchored"] and abs((p4["ask"] - p0["ask"]) - 0.02 * 344640) <= 2, (p0["ask"], p4["ask"]))
    common.write_json(path, {"by_sku": {}, "by_category": {}})
    _seller._files.pop("market_summary.json", None)
    check("...no market known: the best grade, then the cheapest", _seller.pick([a_dear, b, a_ok, c], req, NOW)["unit"] == "u-a2")
    if kept is None:
        path.unlink()
    else:
        path.write_bytes(kept)
    _seller._files.pop("market_summary.json", None)
    old = [u for u in shelf if u["sku"] != PAKIMCHI] + [unit_row("u-near", PAKIMCHI, 9000, 10)]
    out, _ = restock.plan(inv(old), CATALOG, DEMAND, {}, [], [], rules, NOW)
    check("food with under a day left does not count: a fresh one is bought", out["buy"] == [{"sku": PAKIMCHI, "qty": 1}], out)
    lived = dict(CATALOG, Gsauce001=dict(CATALOG[KIMCHI], sku="Gsauce001", name="덮밥 소스", shelf_hours=24),
                 Gdate0001=dict(CATALOG[KIMCHI], sku="Gdate0001", name="건대추", shelf_hours=48))
    both = [{"sku": "Gsauce001", "ts": NOW - H}] * 6 + [{"sku": "Gdate0001", "ts": NOW - H}] * 6
    out, info = restock.plan(inv(shelf, balance=20_000_000), lived, DEMAND, {}, both, [], rules, NOW)
    got = {b["sku"] for b in out["buy"]}
    check("11/10: food whose whole shelf life is 36 h or less is bought by no path, even after it sold (a 24 h sauce arrives "
          "on clearance: 1,469 sold in 48 h, none at its price, -4.45M); a 48 h one still is",
          "Gsauce001" not in got and "Gdate0001" in got and info["short_lived_skipped"] == ["Gsauce001"], (out["buy"], info.get("short_lived_skipped")))
    heavy = shelf + [unit_row(f"u-h{i}", BED, 200_000) for i in range(250)]                 # 50M held, 4.9M cash
    out, _ = restock.plan(inv(heavy), CATALOG, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], rules, NOW)
    check("stock at the cap (90% of a 55M book, over the 40M floor): buys nothing", out["buy"] == [], out)
    out, info = restock.plan(inv(heavy, balance=30_000_000), CATALOG, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], rules, NOW)
    check("...the cap grows with the book: with 30M in cash it is 72M and the shelf refills",
          {"sku": WATER, "qty": 1} in out["buy"] and info["cap"] == int(0.9 * (30_000_000 + 50_000_000 + sum(u["cost"] for u in shelf))), (out, info))
    sofa = "Gsofa0001"
    cat2 = dict(CATALOG, **{s: {"sku": s, "name": f"테스트 소파 {s}", "category": "가구/침구>가구>소파/의자/스툴", "list_price": 2_000_000,
                                "kind": "goods", "shelf_hours": None} for s in (sofa, "Gsofa0002", "Gsofa0003", "Gsofa0004", "Gsofa0005")})
    srow = lambda u, age=0.1: dict(unit_row(u, BED, 1_200_000, age=age), sku=sofa, list_price=2_000_000)
    rich = lambda units: inv(units, balance=20_000_000)
    out, info = restock.plan(rich(shelf), cat2, DEMAND, {}, [], [], rules, NOW, line={sofa: 3})
    check("premium line: two units to start (11/7: two it can sell on hand), bought before the shelf's other lines",
          out["buy"][:1] == [{"sku": sofa, "qty": 2}]
          and info["line"] == 1, out["buy"][:3])
    fast = [{"sku": sofa, "ts": NOW - 0.5 * H}] * 2                         # 2 sold in the last hour: 1 + 2 held
    out, _ = restock.plan(rich(shelf), cat2, DEMAND, {}, fast, [], rules, NOW, line={sofa: 5})
    check("...then 1 + what we sold of it in the last hour (2 sold: 3 held)", out["buy"][0] == {"sku": sofa, "qty": 3}, out["buy"][:2])
    out, _ = restock.plan(rich(shelf), cat2, DEMAND, {}, fast, [], rules, NOW, line={sofa: 1})
    check("...never above its cap", out["buy"][0] == {"sku": sofa, "qty": 1}, out["buy"][:2])
    out, _ = restock.plan(rich(shelf), cat2, DEMAND, {}, [{"sku": sofa, "ts": NOW - 2 * H}] * 6, [], rules, NOW, line={sofa: 5})
    check("...and sales older than an hour do not count (6 sold 2 hours ago: two units, the least on hand)",
          out["buy"][0] == {"sku": sofa, "qty": 2}, out["buy"][:2])
    mk = {"by_sku": {sofa: {"others_6h": {"A_w": {"n": 20, "median": 0.70}}}}}
    pricey = [dict(srow(f"u-p{i}"), grade="A", cost=1_480_000, kind="goods") for i in range(3)]       # 0.74 of list
    check("a unit costing 74% of list (floor 78%) cannot sell at its market of 70%, one at 60% can; since 11/9 a line "
          "product holding a unit it cannot sell gets no more until it goes (11/7 to 11/9: two sellable units were bought "
          "around them); none held: two; no market known: the three count as sellable",
          not restock.sellable(pricey[0], mk) and restock.sellable(dict(pricey[0], cost=1_200_000), mk)
          and restock.line_gaps({sofa: 5}, cat2, pricey, [], NOW, mk) == []
          and restock.line_gaps({sofa: 5}, cat2, [], [], NOW, mk) == [(sofa, 2)]
          and restock.line_gaps({sofa: 5}, cat2, pricey, [], NOW) == [], restock.line_gaps({sofa: 5}, cat2, pricey, [], NOW, mk))
    market = {"by_sku": {sofa: {"price_ratio": 0.78}}}
    surged = lambda f: dict(rich(shelf), surge=[{"sku": sofa, "units": 150, "factor": f}])
    out, info = restock.plan(surged(1.30), cat2, DEMAND, market, fast, [], rules, NOW, line={sofa: 5})
    check("surge: at 1.30 the sofa's expected cost (0.59 x 1.30 = 0.77 of list) leaves under 6% against its market 0.78: not bought",
          all(b["sku"] != sofa for b in out["buy"]) and info["surge_skipped"] == [sofa], (out["buy"][:2], info.get("surge_skipped")))
    out, info = restock.plan(surged(1.10), cat2, DEMAND, market, fast, [], rules, NOW, line={sofa: 5})
    check("...at 1.10 (0.65, 13% under its market) it is bought as ever", out["buy"][0] == {"sku": sofa, "qty": 3} and not info["surge_skipped"],
          (out["buy"][:2], info.get("surge_skipped")))
    stale = shelf + [srow("u-s1", age=3 / 24)]
    out, _ = restock.plan(rich(stale), cat2, DEMAND, {}, fast[:1] and [{"sku": sofa, "ts": NOW - 5 * H}], [], rules, NOW, line={sofa: 3})
    check("one held 3 h with no sale (the last 5 h ago): no more units", all(b["sku"] != sofa for b in out["buy"]), out["buy"][:3])
    out, _ = restock.plan(rich(stale), cat2, DEMAND, {}, fast, [], rules, NOW, line={sofa: 3})
    check("...but one that sold half an hour ago is kept at 1 + its sales (3, one held: 2 more)",
          out["buy"][0] == {"sku": sofa, "qty": 2}, out["buy"][:2])
    out, _ = restock.plan(rich(shelf), cat2, DEMAND, {}, [], [], rules, common.ROUND_END - 29 * H, line={sofa: 3})
    check("line products are bought to the end (the book counts stock at cost)", any(b["sku"] == sofa for b in out["buy"]), out["buy"][:3])
    out, _ = restock.plan(rich(shelf), cat2, DEMAND, {}, fast, [], rules, NOW, line={})
    check("a dear product that sold is not refilled by the ordinary rule when it is not in the line",
          all(b["sku"] != sofa for b in out["buy"]), out["buy"][:3])
    many = {s: 1 for s in cat2 if s.startswith("Gsofa")}
    out, info = restock.plan(rich(shelf), cat2, DEMAND, {}, [], [], rules, NOW, line=dict(many, Gbad=2, **{BED: 25}))
    check("at most 8 line products a run (the shelf keeps 2 of the 10 lines): all 5; unknown SKUs and caps over 24 are ignored",
          info["line"] == 5 and info["line_gaps"] == 5 and all(b["sku"] != BED for b in out["buy"][:5]), (info, out["buy"][:6]))
    out, _ = restock.plan(inv(shelf, balance=3_000_000), cat2, DEMAND, {}, [], [], rules, NOW, line={sofa: 1})
    check("the balance kept (2,500,000) holds: no sofa planned at 1,500,000 with 3,000,000 in hand",
          all(b["sku"] != sofa for b in out["buy"]), out["buy"][:2])
    out, info = restock.plan(inv(shelf, balance=8_000_000), cat2, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], rules, NOW, line=many)
    check("the line takes at most 90% of the money above the balance kept and the shelf's 2M (8M: two sofas at 1.5M of 3.15M), the shelf still refills",
          info["line"] == 2 and {"sku": WATER, "qty": 1} in out["buy"], (info["line"], out["buy"]))
    lots = dict(cat2, **{f"Gsofa{i:04d}": dict(cat2[sofa], sku=f"Gsofa{i:04d}") for i in range(6, 12)})
    out, info = restock.plan(inv(shelf, balance=60_000_000), lots, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], rules, NOW,
                             line={s: 1 for s in lots if s.startswith("Gsofa")})
    check("eleven line products and money for all: the line takes 8 of the 10 lines, the shelf keeps 2",
          info["line"] == 8 and {"sku": WATER, "qty": 1} in out["buy"], (info, out["buy"]))
    soon = dict(rich(shelf), restock={"open": True, "opens_at": NOW - 100, "closes_at": NOW + 40})
    out, info = restock.plan(soon, cat2, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], rules, NOW, line=many)
    check("...and the shelf keeps 2 lines: 40 s left (4 lines), 2 for the line, the refill still bought",
          info["lines"] == 4 and info["line"] == 2 and {"sku": WATER, "qty": 1} in out["buy"], (info, out["buy"]))
    sold_lots = [{"sku": s, "ts": NOW - 0.2 * H} for s in ("Gsofa0001", "Gsofa0002") for _ in range(5)] + [{"sku": "Gsofa0003", "ts": NOW - 0.2 * H}]
    gaps = restock.line_gaps({"Gsofa0001": 9, "Gsofa0002": 9, "Gsofa0003": 9, "Gsofa0004": 1}, cat2, [], sold_lots, NOW)
    check("line gaps: a product never sold nor held first, then by last hour's sales",
          [g[0] for g in gaps] == ["Gsofa0004", "Gsofa0001", "Gsofa0002", "Gsofa0003"], gaps)
    five_new = {f"Gsofa{i:04d}": 1 for i in range(6, 13)}
    lots5 = dict(cat2, **{s: dict(cat2[sofa], sku=s) for s in five_new})
    gaps = restock.line_gaps(dict({"Gsofa0001": 9}, **five_new), lots5, [], sold_lots, NOW)
    check("...at most 5 new ones ahead of the best seller, the rest after it",
          [g[0] for g in gaps] == ["Gsofa0006", "Gsofa0007", "Gsofa0008", "Gsofa0009", "Gsofa0010", "Gsofa0001", "Gsofa0011", "Gsofa0012"], gaps)
    tiny = dict(rich(shelf), restock={"open": True, "opens_at": NOW - 100, "closes_at": NOW + 25})
    out, info = restock.plan(tiny, cat2, DEMAND, {}, [], [], rules, NOW, line=many)
    check("...but with two lines left the line still gets one", info["lines"] == 2 and info["line"] == 1, (info, out["buy"]))
    out, info = restock.plan(rich([]), cat2, DEMAND, {}, sold10, [], rules, NOW, stocked=set(FILLERS[:10]), line={sofa: 1})
    fresh = [b["sku"] for b in out["buy"] if b["sku"] not in set(FILLERS[:10]) | {sofa}]
    check("exploring with the line: the line's line comes out of the refills' share, products never stocked keep theirs (4)",
          info["line"] == 1 and len(fresh) == 4, out["buy"])
    check("line.json: a well formed file wins, {} turns the line off, anything malformed keeps the default",
          restock.load_line({"skus": {sofa: 2}}) == {sofa: 2} and restock.load_line({"skus": {}}) == {}
          and restock.load_line({"skus": [sofa]}) is restock.LINE and restock.load_line([1]) is restock.LINE
          and restock.load_line(None) is restock.LINE)
    end = common.ROUND_END
    late = [{"sku": PAKIMCHI, "ts": end - 2 * DAY - H}] * 3 + [{"sku": WATER, "ts": end - 2 * DAY - H}]
    empty_now = [u for u in shelf if u["sku"] not in (PAKIMCHI, WATER)]
    out, _ = restock.plan(inv(empty_now), CATALOG, DEMAND, {}, late, [], rules, end - 2 * DAY)
    check("no wind-down: 2 days before the end the shelf refills as ever, the slow seller too",
          {b["sku"] for b in out["buy"]} >= {PAKIMCHI, WATER}, out)
    out, info = restock.plan(inv([]), CATALOG, DEMAND, {}, [], [], rules, end - 3 * H)
    check("...and in the last 6 h it still buys", out["buy"] != [], info)
    focus = dict(rules, drop_categories=["김치"], focus_categories=["주스/코코아/생수"])
    out, _ = restock.plan(inv([u for u in shelf if u["sku"] not in (PAKIMCHI,)]), CATALOG, DEMAND, {}, [], [], focus, NOW)
    check("rules in force: a dropped category is not refilled, a focus category gets one more",
          out["buy"] == [{"sku": WATER, "qty": 1}], out)
    out, _ = restock.plan({"round": 3, "balance": 400_000, "bankrupt_line": 500_000, "bankruptcies_left": 1, "units": []},
                          CATALOG, DEMAND, {}, [], [], rules, NOW)
    check("below the line with nothing to sell: files for bankruptcy", out.get("bankrupt") is True)
    check("before Round 3: buys nothing", restock.plan({"round": 2}, CATALOG, DEMAND, {}, [], [], rules, NOW)[0] == {"buy": []})
    fake.inventory([])
    out, lines = fake.run("restock.py", "--state", str(fake.md))
    check("the hook prints the kit's line (<= 10 lines, 1-20 each, known SKUs)",
          out and 0 < len(out["buy"]) <= 10 and all(b["sku"] in CATALOG and 1 <= b["qty"] <= 20 for b in out["buy"]),
          [x for x in lines if x.startswith("[restock]")][:1])


def test_deals():
    print("\n[S1] which agreed deals settled")
    folder = common.MARKET_DIR / "records"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "deals.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(NOW - 60), "task": "t-sold", "unit": "u-gone", "sku": KIMCHI, "price": 14500, "cost": 12000}) + "\n")
        f.write(json.dumps({"ts": int(NOW - 700), "task": "t-unpaid", "unit": "u-stays", "sku": WATER, "price": 11000, "cost": 7000}) + "\n")
    state = {}
    restock.check_deals({"units": [{"unit": "u-stays"}]}, state, NOW)
    sold = [s["task"] for s in common.records("sales")]
    unpaid = [s["task"] for s in common.records("unpaid")]
    check("a unit that left the inventory is a sale", "t-sold" in sold, sold)
    check("a unit still in stock 10 minutes after the deal is unpaid", "t-unpaid" in unpaid, unpaid)
    raw = (folder / "deals.jsonl").read_bytes()
    legacy = {"deals_seen": len(raw.splitlines()) - 1}                       # every line but the last was seen
    first = restock.new_deals(legacy)
    check("the deals file is read from where the last run stopped (an old line count becomes a byte offset)",
          [d["task"] for d in first] == ["t-unpaid"] and legacy["deals_offset"] == len(raw) and "deals_seen" not in legacy, first)
    with open(folder / "deals.jsonl", "a", encoding="utf-8") as f:
        f.write('{"ts": 1, "task": "t-half"')
    check("a line still being written waits for the next run", restock.new_deals(legacy) == [] and legacy["deals_offset"] == len(raw))
    with open(folder / "deals.jsonl", "a", encoding="utf-8") as f:
        f.write(', "unit": "u-x"}\n')
    check("...and is read once it is whole", [d["task"] for d in restock.new_deals(legacy)] == ["t-half"])
    restock.check_deals({"units": [{"unit": "u-x"}]}, state, NOW)              # the shared state takes the finished line too
    with open(folder / "deals.jsonl", "a", encoding="utf-8") as f:              # three buyers agree to one unit
        for i, t in enumerate(("t-a", "t-b", "t-c")):
            f.write(json.dumps({"ts": int(NOW - 300 + 60 * i), "task": t, "unit": "u-twice", "sku": WATER, "price": 10000 + i,
                                "cost": 7000}) + "\n")
    restock.check_deals({"units": [{"unit": "u-stays"}]}, state, NOW)
    sold = [s for s in common.records("sales") if s["unit"] == "u-twice"]
    gone = [s["task"] for s in common.records("unpaid") if s.get("superseded") and s["unit"] == "u-twice"]
    check("one unit agreed three times then gone: one sale (the latest deal), two unpaid",
          [s["task"] for s in sold] == ["t-c"] and sold[0]["price"] == 10002 and gone == ["t-a", "t-b"], (sold, gone))
    shelf = {}
    first_run = {"units": [{"unit": "f-sold", "sku": KIMCHI, "cost": 12000, "expires_at": NOW + 99 * H},
                           {"unit": "f-old", "sku": BERRY, "cost": 9000, "expires_at": NOW - 60},
                           {"unit": "f-lost", "sku": BEDDING, "cost": 14000}, {"unit": "f-keep", "sku": PAD, "cost": 15000}]}
    check("the shelf: the first run only takes the snapshot", restock.stock_flow(first_run, shelf, NOW, set()) == {})
    flow = restock.stock_flow({"units": [{"unit": "f-keep", "sku": PAD, "cost": 15000},
                                         {"unit": "f-new", "sku": WATER, "cost": 7000, "grade": "A"}]}, shelf, NOW, {"f-sold"})
    rows = {r["unit"]: r["what"] for r in common.records("stock_flow")}
    check("the shelf since the last run: a unit that arrived, food past its date, a unit gone with no deal; a sale is not counted",
          flow == {"expired": 1, "gone": 1, "received": 1} and rows == {"f-old": "expired", "f-lost": "gone", "f-new": "received"},
          (flow, rows))
    spec = importlib.util.spec_from_file_location("repair_sales", HERE / "deploy" / "repair_sales.py")
    repair = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(repair)
    deals = [{"ts": NOW - 900, "task": "r1", "unit": "u1", "price": 9000}, {"ts": NOW - 800, "task": "r2", "unit": "u1", "price": 9500},
             {"ts": NOW - 700, "task": "r3", "unit": "u2", "price": 5000}, {"ts": NOW - 100, "task": "r4", "unit": "u3", "price": 7000}]
    s, u, p = repair.rebuild(deals, [{"ts": NOW - 790, "unit": "u1", "amount": 9500, "seq": 7}], NOW)
    check("the repair: a settle matches its unit's deal at that price; older deals unpaid, a recent one still pending",
          [x["task"] for x in s] == ["r2"] and s[0]["ledger"] == 7 and [x["task"] for x in u] == ["r1", "r3"] and list(p) == ["r4"],
          (s, u, list(p)))


def test_ledger():
    print("\n[ledger] collection and summary (ledger.py)")
    pages = [[{"seq": 1, "ts": NOW - H, "kind": "order", "data": {"order": "o1"}},
              {"seq": 2, "ts": NOW - H, "kind": "settle", "data": {"buyer": "b0", "shop": "s1", "item": KIMCHI, "amount": 15000,
                                                                "grade": "A", "claim": "A", "claim_honest": True}},
              {"seq": 3, "ts": NOW - 2 * H, "kind": "settle", "data": {"buyer": "b1", "shop": "s2", "item": BEDDING, "amount": 25000,
                                                                    "grade": "C", "claim": "B", "claim_honest": False}},
              {"seq": 4, "ts": NOW - H, "round": 3, "kind": "broken", "data": {"buyer": "b1", "item": BEDDING}},
              {"seq": 5, "ts": NOW - H, "round": 2, "kind": "settle", "data": {"buyer": "b2", "shop": "s1", "item": "chair",
                                                                            "amount": 205}},
              {"seq": 6, "ts": NOW - H, "round": 2, "kind": "broken", "data": {"buyer": "b2", "item": "chair"}}], []]
    calls = []
    get = lambda after: calls.append(after) or pages[len(calls) - 1]
    common.write_json(ledger.folder() / "state.json", {"seq": 0, "at": 0})
    check("a collection keeps this round's settle and broken entries only (Round 2's left out)",
          ledger.collect(get) == 3 and calls[0] == 0
          and all(r.get("item") != "chair" for r in common.read_jsonl(ledger.folder() / "settles.jsonl")), calls)
    check("the next collection reads on after the skipped entries too",
          common.read_json(ledger.folder() / "state.json")["seq"] == 6)
    s = ledger.summarize(CATALOG, NOW)
    check("summary: sales by SKU with the share of list price", s["by_sku"][KIMCHI]["sold_24h"] == 1
          and s["by_sku"][KIMCHI]["price_ratio"] == 0.75, s["by_sku"].get(KIMCHI))
    check("summary: shops' grade claims, true and false", s["by_shop"]["s2"] == {"settled_24h": 1, "claims": 1, "honest": 0})
    check("summary: a breakdown is matched to the grade that was sold", s["broken_by_grade"] == {"C": 1}
          and s["goods_sold_by_grade"] == {"C": 1}, s["broken_by_grade"])
    kg = s["by_category"][CATALOG[KIMCHI]["category"]]["by_grade"]
    check("summary: each category's median share by grade, with and without a warranty (the seller's market prices)",
          kg == {"A": {"n": 1, "median": 0.75, "w_n": 0, "w_median": None, "nw_n": 1, "nw_median": 0.75}}, kg)
    ledger.learn_names([{"name": "Kiro729", "url": "s1"}, {"name": "Shop Two", "url": "s2"}])
    ledger.learn_names([{"name": "Shop Two", "url": "s2-new"}])                 # restarted: a new tunnel address
    s = ledger.summarize(CATALOG, NOW)
    check("the ledger names shops by address: the summary keys them by name; an old address keeps its name",
          set(s["by_shop"]) == {"Kiro729", "Shop Two"} and s["by_shop"]["Shop Two"]["claims"] == 1
          and ledger.names() == {"s1": "Kiro729", "s2": "Shop Two", "s2-new": "Shop Two"}, s["by_shop"])
    rows = common.read_jsonl(ledger.folder() / "settles.jsonl")
    extra = ([{"seq": 100 + i, "ts": NOW - H, "shop": "s2", "item": KIMCHI, "amount": 14000, "grade": "A"} for i in range(10)]
             + [{"seq": 120 + i, "ts": NOW - H, "shop": "s1", "item": KIMCHI, "amount": 19000, "grade": "A"} for i in range(10)]
             + [{"seq": 140 + i, "ts": NOW - 7 * H, "shop": "s2", "item": KIMCHI, "amount": 10000, "grade": "A"} for i in range(10)]
             + [{"seq": 160 + i, "ts": NOW - H, "shop": "s2", "item": KIMCHI, "amount": 12000, "grade": "B"} for i in range(9)])
    with open(ledger.folder() / "settles.jsonl", "a", encoding="utf-8") as f:
        for e in extra:
            f.write(json.dumps(e) + "\n")
    s2 = ledger.summarize(CATALOG, NOW)
    check("a SKU's own market by grade over 6 h: other shops only (ours, Kiro729, left out), older settles left out, cells of 10 or more",
          s2["by_sku"][KIMCHI].get("others_6h") == {"A": {"n": 10, "median": 0.7}}, s2["by_sku"][KIMCHI].get("others_6h"))
    check("...and how many other shops' settles in 6 h in any grade (10 A + 9 B)", s2["by_sku"][KIMCHI].get("others_6h_n") == 19,
          s2["by_sku"][KIMCHI].get("others_6h_n"))
    with open(ledger.folder() / "settles.jsonl", "w", encoding="utf-8") as f:
        for e in rows:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    q = tools.ledger_query(shop="Shop Two", now=NOW)
    check("the ledger tool finds a shop by its name", q["settled"] == 1 and q["top_shops"] == {"Shop Two": 1}, q)
    moved = ledger.archive(NOW + 60 * H)                                         # 60 h later every row is over 48 h old
    arch = sorted((ledger.folder() / "archive").glob("settles-*.jsonl.gz"))
    with gzip.open(arch[0], "rt", encoding="utf-8") as g:
        kept_away = [json.loads(x) for x in g]
    check("rows over 48 h move to a compressed archive; nothing is lost; the hourly file is left small",
          moved == 3 and len(kept_away) == 2 and common.read_jsonl(ledger.folder() / "settles.jsonl") == [], (moved, len(kept_away)))
    win = ledger.folder() / "window.jsonl"
    win.write_text("".join(json.dumps(r) + "\n" for r in ({"seq": 1, "ts": NOW - 30 * H, "a": 1}, {"seq": 2, "ts": NOW - 2 * H, "a": 2},
                                                          {"b": 3, "nested": {"ts": 1}, "ts": NOW - H})) + '{"ts": 1', encoding="utf-8")
    check("read_jsonl(since): only rows of the window (an old one skipped unparsed, a later 'ts' still checked), "
          "the half-written last line skipped",
          [r.get("a", r.get("b")) for r in common.read_jsonl(win, since=NOW - 24 * H)] == [2, 3]
          and len(common.read_jsonl(win)) == 3, common.read_jsonl(win, since=NOW - 24 * H))
    f = ledger.folder() / "mixed.jsonl"
    f.write_bytes((json.dumps({"ts": NOW - 50 * H, "a": 1}) + "\n" + json.dumps({"ts": NOW - H, "a": 2}) + "\n" + '{"ts": 1').encode())
    check("archive_old: only the old line moves; a recent line and a line still being written stay",
          common.archive_old(f, NOW - 48 * H) == 1
          and f.read_bytes().decode() == json.dumps({"ts": NOW - H, "a": 2}) + "\n" + '{"ts": 1')
    check("archive_old waits while the oldest line is within the slack", common.archive_old(f, NOW + 2 * H, 6 * H) == 0)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fake = Fake()
    port = kit.free_port()
    seller_log = open(ROOT / "seller.log", "w", encoding="utf-8")
    seller = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=HERE, env=fake.env,
                              stdout=seller_log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    shop = lambda offers: fake.write("shops.json", [{"name": "test-shop", "url": url, "reviews": [], "offers": offers}])
    common.write_json(common.MARKET_DIR / "analysis" / "objective.json", {"at": int(time.time()), "margin_floor": 0.44})
    # 11/13: the margin floor as objective.py wrote it on 11/13 (Patch 3.6), for the buyer runs below
    buyer = lambda order, shops: fake.run("market_buyer.py", "--shops", str(shops), "--item", order["item"],
                                          "--budget", str(order["budget"]), "--order", str(fake.md / "order.json"))
    offer = lambda sku, qty=1: {"sku": sku, "name": CATALOG[sku]["name"], "qty": qty}
    run = lambda texts, cancel=True: asyncio.run(talk(url, texts, cancel))
    try:
        print(f"seller: {url}  market dir: {fake.md}")
        check("seller serves its agent card", kit.wait_card(url, 30))

        print("\n[A] food deal through market_buyer: 포기김치 (list 20000), budget 22000, eat within 48 h;"
              " stock: A with 150 h left, C with 30 h left")
        inv = fake.inventory([fake.unit("u-k1-a", KIMCHI, "A", 12000, 150), fake.unit("u-k1-c", KIMCHI, "C", 7000, 30)])
        order = fake.order("o-a", "김치", 22000, [KIMCHI, GEOTJEORI], "food", 48)
        out, log = buyer(order, shop([offer(KIMCHI, 2)]))
        print("\n".join("    " + line for line in log if line.startswith("[buyer]")))
        rec = (out or {}).get("receipt") or {}
        check("deal agreed", bool(rec), out and out.get("review"))
        check("B2 states the need first (49 h) and S2 ships the unit that meets it (u-k1-a, not the 30 h one)",
              "must last at least 49 hours" in "\n".join(log) and rec.get("unit") == "u-k1-a")
        check("S3: first ask 75% of list for grade A, deal between the floor and it",
              "Asking 15000" in "\n".join(log) and 12600 <= rec.get("price", 0) <= 15000, rec.get("price"))
        check("B2: the buyer opened at 50% of list and took the ask once it fit", "-> '10000." in "\n".join(log))
        check("S4: the receipt claims the true grade", rec.get("claim") == "A", rec.get("claim"))
        co, refuse = kit_checks(fake, rec, order, inv, out["accept"])
        check("kit: checkout ships u-k1-a with its certificate and the claim", co.get("unit") == "u-k1-a" and co.get("cert")
              and co.get("claim") == "A", f"{co.get('unit')} claim {co.get('claim')}")
        check("kit: the buyer's quality check passes (150 h >= 49 h)", refuse is None, refuse)

        print("\n[C] budget 12000: under the expected price (70% of list) and the floor 12600")
        order = fake.order("o-c", "김치", 12000, [KIMCHI], "food", 48)
        out, log = buyer(order, shop([offer(KIMCHI, 2)]))
        check("B1 does not even try, and nothing sells below the floor", out is not None and out["receipt"] is None,
              out and out["review"])

        print("\n[D] goods deal through market_buyer: 차렵이불 (list 39900, above the budget 30000); stock: one refurbished unit")
        # cost 10000 (14000 until 11/13): with the warranty at 33% of list the floor is 10200 + 13167 = 23367, under our
        # buyer's ceiling for refurbished goods with the warranty at the margin floor 0.44 (23960); at 14000 it was 27447
        inv = fake.inventory([fake.unit("u-b1-c", BEDDING, "C", 10000)])
        order = fake.order("o-d", "침구", 30000, [BEDDING], "goods")
        out, log = buyer(order, shop([offer(BEDDING)]))
        rec = (out or {}).get("receipt") or {}
        check("B1 tries a product listed above the budget; S3 quotes refurbished goods with the 6 h warranty unasked (93%: 37107)",
              "Asking 37107" in "\n".join(log) and "with a 6-hour warranty" in "\n".join(log), rec.get("price"))
        check("B3: the warranty came with the quote, so the buyer did not need to ask for it",
              "Please include a 6-hour warranty." not in "\n".join(log))
        check("deal within the budget, with the warranty on the receipt", rec.get("warranty_hours") == 6
              and 0 < rec.get("price", 0) <= 30000, rec.get("price"))
        check("B3: the buyer's terms ask for the warranty", out["accept"] == {"cert": True, "warranty_hours": 6}, out["accept"])
        co, refuse = kit_checks(fake, rec, order, inv, out["accept"])
        check("kit: the warranty and the claim C reach the checkout; the buyer's check passes",
              refuse is None and co.get("warranty_hours") == 6 and co.get("claim") == "C", refuse)
        check("kit: refurbished goods without a warranty would not be paid for (grades A and B only)",
              kit.check_accept(order, dict(co, warranty_hours=None), {"cert": True, "grades": ["A", "B"]}) is not None)

        print("\n[W] warranty asked for in the first message: the same refurbished unit, budget 40000")
        order = fake.order("o-w", "침구", 40000, [BEDDING], "goods")
        t = run([f"I want to buy {CATALOG[BEDDING]['name']} ({BEDDING}) with a 6-hour warranty", "37107"])
        check("quoted with the warranty at 93% of list", meta(t[0]).get("ask") == 37107 and meta(t[0]).get("warranty_hours") == 6,
              said(t[0]))
        rec = next((p["data"] for a in t[-1].get("artifacts") or [] for p in a.get("parts") or [] if "data" in p), {})
        check("the receipt carries the 6 h warranty", rec.get("warranty_hours") == 6 and rec.get("price") == 37107, rec)
        co, refuse = kit_checks(fake, rec, order, inv, {"warranty_hours": 6})
        check("kit: a buyer requiring a 6 h warranty pays", refuse is None and co.get("warranty_hours") == 6, refuse)

        print("\n[S2] serving: needs, categories, the model, held and missing products")
        fake.inventory([fake.unit("u-k1-a", KIMCHI, "A", 12000, 150), fake.unit("u-k1-b", KIMCHI, "B", 10000, 90),
                        fake.unit("u-k1-c", KIMCHI, "C", 7000, 30), fake.unit("u-k2-a", GEOTJEORI, "A", 8000, 150),
                        fake.unit("u-k3-b", PAKIMCHI, "B", 12000, 100), fake.unit("u-b1-c", BEDDING, "C", 14000)])
        t = run([f"I want to buy {CATALOG[KIMCHI]['name']} ({KIMCHI}). It must last 72 hours."])
        check("a stated need gets the unit that just meets it (90 h, not 150 h)", meta(t[0]).get("unit") == "u-k1-b"
              and meta(t[0]).get("ask") == 14000, said(t[0]))
        t = run(["I want to buy a 김치", PAKIMCHI])
        opts = [o["sku"] for o in meta(t[0]).get("options") or []]
        check("a category request gets up to 3 products: nearest the category's median list price first, then by price",
              opts == [KIMCHI, GEOTJEORI, PAKIMCHI], opts)
        check("the menu shows list prices and the market's acceptable-price rule",
              "list price 20000" in said(t[0]) and "0.5 to 1.5 times a reference price" in said(t[0]), said(t[0]))
        check("a SKU from the menu switches the product", meta(t[1]).get("sku") == PAKIMCHI and meta(t[1]).get("ask") == 17892,
              said(t[1]))
        t = run(["I want to buy a 김치", "50000", "OK, deal"])
        check("a number above the list price is a budget: the menu narrows to 0.45-1.0 x 50000 (파김치 25560)",
              [o["sku"] for o in meta(t[1]).get("options") or []] == [PAKIMCHI] and "for that budget" in said(t[1]), said(t[1]))
        check("then the deal is on the product that fits", mb.state(t[-1]) == "TASK_STATE_COMPLETED"
              and PAKIMCHI in said(t[-1]), said(t[-1]))
        t = run(["I want to buy a 김치", "90000"])
        check("a budget nothing fits: declined with the reason, not a deal the order would refuse",
              mb.state(t[-1]) == "TASK_STATE_REJECTED" and "item check" in said(t[-1]), said(t[-1]))
        check("the refusal is recorded with the budget", any(r.get("reason") == "price" and r.get("budget") == 90000
                                                            for r in common.records("refusals")))
        t = run(["I want to buy a 김치, budget 50000", GEOTJEORI])
        check("a budget in the first message narrows the first menu", [o["sku"] for o in meta(t[0]).get("options") or []]
              == [PAKIMCHI], said(t[0]))
        check("a SKU of the category we have but did not show switches the product too", meta(t[1]).get("sku") == GEOTJEORI,
              said(t[1]))
        t = run(["I want to buy a 김치", "17000"])
        rec = next((p["data"] for a in t[-1].get("artifacts") or [] for p in a.get("parts") or [] if "data" in p), {})
        check("a category offer over the ask sells at our ask (tried at the offer 11/8 19:55-22:15: 1 of 48 paid)",
              meta(t[0]).get("ask") < 17000 and rec.get("price") == meta(t[0]).get("ask"), (meta(t[0]).get("ask"), rec))
        t = run([f"I want to buy {CATALOG[KIMCHI]['name']} ({KIMCHI})", "19500"])
        rec = next((p["data"] for a in t[-1].get("artifacts") or [] for p in a.get("parts") or [] if "data" in p), {})
        check("a SKU offer over the ask still sells at our ask", rec.get("price") == meta(t[0]).get("ask") < 19500,
              (meta(t[0]).get("ask"), rec))
        t = run(["Looking for kimchi that lasts 3 days"])
        check("words the code cannot place go to the cheap model, checked against the stock",
              meta(t[0]).get("sku") == KIMCHI and meta(t[0]).get("unit") == "u-k1-b", said(t[0]))
        hold = run([f"I want to buy {CATALOG[GEOTJEORI]['name']} ({GEOTJEORI})"], cancel=False)
        t = run([f"I want to buy {CATALOG[GEOTJEORI]['name']} ({GEOTJEORI})"])
        check("a product promised to another buyer: other products of its category instead",
              said(t[0]).startswith("That one is not available") and meta(t[0]).get("sku") != GEOTJEORI, said(t[0]))
        asyncio.run(_cancel(url, hold))
        t = run([f"I want to buy {CATALOG[BERRY]['name']} ({BERRY})"])
        check("nothing of that category: declined", mb.state(t[0]) == "TASK_STATE_REJECTED", said(t[0]))
        reasons = {(r.get("sku"), r.get("reason")) for r in common.records("refusals")}
        check("turned-away buyers are recorded (held, out)", (GEOTJEORI, "held") in reasons and (BERRY, "out") in reasons, reasons)
        hows = [r.get("how") for r in common.records("requests")]
        check("how each buyer asked is recorded", {"sku", "category", "model"} <= set(hows), set(hows))
        last = common.records("requests")[-1]
        check("each request keeps its A2A context id and metadata keys (to recognise a buyer)",
              isinstance(last.get("ctx"), str) and isinstance(last.get("meta"), list), {k: last.get(k) for k in ("ctx", "meta")})

        print("\n[S3] negotiating: the floor and its reason, quick close, a plain yes")
        fake.inventory([fake.unit("u-b1-a", BEDDING, "A", 29500), fake.unit("u-k1-a", KIMCHI, "A", 12000, 150)])
        t = run([f"I want to buy {CATALOG[BEDDING]['name']} ({BEDDING})", "25000"])
        check("a unit whose floor is above 75% starts at the floor (30090)", meta(t[0]).get("ask") == 30090, said(t[0]))
        check("at the floor the shop says why, truthfully", "only about 2% over what this unit cost us" in said(t[1])
              and "top grade (A)" in said(t[1]), said(t[1]))
        t = run([f"I want to buy {CATALOG[KIMCHI]['name']} ({KIMCHI})", "14300"])
        check("an offer within 5% of the ask is taken at once", mb.state(t[-1]) == "TASK_STATE_COMPLETED"
              and "at 14300" in said(t[-1]), said(t[-1]))
        t = run([f"I want to buy {CATALOG[KIMCHI]['name']} ({KIMCHI})", "OK, deal"])
        check("a plain yes buys at the ask", mb.state(t[-1]) == "TASK_STATE_COMPLETED" and "at 15000" in said(t[-1]), said(t[-1]))

        print("\n[E] no shop has the category in stock")
        order = fake.order("o-e", "김치", 22000, [KIMCHI], "food", 48)
        out, _ = buyer(order, shop([]))
        check("buyer closes the order as no deal", out is not None and out["shop"] == "" and out["receipt"] is None)

        print("\n[V] review")
        settled = {"shop": url, "unit": "u-k1-a", "sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "grade": "A",
                   "claim": "A", "claim_honest": True, "expires_at": time.time() + 100 * H, "ledger": 1}
        deal = {"order": {"order": "o-a"}, "receipt": {"item": KIMCHI, "price": 14500}, "settled": settled}
        out, _ = fake.run("review.py", "--deal", str(fake.write("deal.json", deal)))
        check("honest deal at 72.5% of list: 4 stars, facts only", bool(out) and out["stars"] == 4, out)
        deal["settled"] = dict(settled, claim="A", grade="B", claim_honest=False)
        out, _ = fake.run("review.py", "--deal", str(fake.write("deal.json", deal)))
        check("false grade claim: 1 star", bool(out) and out["stars"] == 1, out and out["review"])
        deal["settled"] = dict(settled, claim="B", grade="A", claim_honest=False)
        out, _ = fake.run("review.py", "--deal", str(fake.write("deal.json", deal)))
        check("goods better than claimed (said B, certified A, 11/6): no 1 star, rated by price (4) and said so",
              bool(out) and out["stars"] == 4 and "certified A, better than claimed" in out["review"], out)

        test_pricing()
        test_categories()
        test_restock(fake)
        test_deals()
        test_ledger()

        print("\n[records]")
        streams = ("negotiations", "requests", "refusals", "deals", "orders", "restock", "reviews", "sales", "unpaid")
        counts = {k: len(common.records(k)) for k in streams}
        check("every step left a record", all(counts.values()), counts)
        neg = common.records("negotiations")
        check("every negotiation records its first-ask arm (none without experiments.json) and whether it was anchored",
              all("arm" in n and "anchored" in n for n in neg), [sorted(n)[:6] for n in neg[:2]])
        check("no negotiation sold below its floor", all(n["price"] is None or n["price"] >= n["floor"] for n in neg),
              ", ".join(f"{n['sku']} {n['outcome']} {n['price']}/{n['floor']}" for n in neg))
    finally:
        kit.stop(seller)
        seller_log.close()
        print("\n[seller log]")
        print("\n".join("    " + line for line in (ROOT / "seller.log").read_text(encoding="utf-8").splitlines()))
        shutil.rmtree(ROOT, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


async def _cancel(url: str, conversation: list) -> None:
    """Close a conversation left open on purpose."""
    c = mb.A2A()
    await c.open(url)
    await c.cancel(conversation[-1]["id"])
    await c.close()


if __name__ == "__main__":
    raise SystemExit(main())
