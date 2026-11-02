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
import ledger                                                                    # noqa: E402
import market_buyer as mb                                                        # noqa: E402
import pricing                                                                   # noqa: E402
import restock                                                                   # noqa: E402

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("market_kit", HERE.parents[3] / "scripts" / "market_kit.py")
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
    print("\n[S3 S4 S5] prices (pricing.py)")
    end = common.ROUND_END
    now = end - 5 * DAY
    food = {"sku": KIMCHI, "list_price": 20000, "cost": 12000, "kind": "food", "grade": "A",
            "supplied_at": now - 0.5 * DAY, "expires_at": now + 150 * H}
    p = pricing.plan(food, now, catalog=CATALOG)
    check("fresh food, grade A: ask 75% of list, floor cost + 5%", (p["ask"], p["floor"], p["grade"]) == (15000, 12600, "A"), p)
    goods_c = {"sku": BEDDING, "list_price": 39900, "cost": 14000, "kind": "goods", "grade": "C", "supplied_at": now - DAY}
    check("refurbished goods start at 60%", pricing.plan(goods_c, now)["ask"] == 23940, pricing.plan(goods_c, now)["ask"])
    check("3.5 days unsold: 5% of list lower", pricing.plan(dict(goods_c, supplied_at=now - 3.5 * DAY), now)["ask"] == 21945)
    p = pricing.plan(dict(goods_c, supplied_at=now - 8 * DAY), now)
    check("8 days unsold: the floor may go to 25% of list (below cost)", (p["floor"], p["at_cost"]) == (9975, False), p)
    near = dict(food, cost=10000, expires_at=now + 10 * H)
    p = pricing.plan(near, now, catalog=CATALOG)
    check("food with 10 h left: ask 45%, floor 25% of list", (p["ask"], p["floor"]) == (9000, 5000), p)
    p = pricing.plan(dict(food, expires_at=end + 140 * H, supplied_at=end - 10 * H - 0.5 * DAY), end - 10 * H, catalog=CATALOG)
    check("last day: ask 15% lower, floor 25% of list", (p["ask"], p["floor"]) == (12000, 5000), p)
    p = pricing.plan(dict(food, expires_at=end + 140 * H, supplied_at=end - 3 * H - 0.5 * DAY), end - 3 * H, catalog=CATALOG)
    check("last 6 h: floor 10% of list", p["floor"] == 2000, p)
    goods_a = dict(goods_c, cost=26000, grade="A")
    p = pricing.plan(goods_a, now, warranty=True)
    check("warranty on grade A goods: +5% of list on ask and floor", (p["ask"], p["floor"], p["warranty_hours"]) == (31920, 29295, 6), p)
    p = pricing.plan(goods_c, now, warranty=True)
    check("warranty on refurbished goods: +10%, still under new goods without one",
          (p["ask"], p["floor"]) == (27930, 18690) and p["ask"] < pricing.plan(goods_a, now)["ask"] + 1, p)
    check("no warranty on food", pricing.plan(food, now, warranty=True, catalog=CATALOG)["warranty_hours"] is None)
    edge = dict(food, expires_at=now + 76 * H)
    check("food about to drop to C is claimed C (true at checkout and at settle)",
          (pricing.grade(edge, now, CATALOG), pricing.claim(edge, now, CATALOG)) == ("B", "C"))


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
    strat = dict(common.STRATEGY)
    one, two = restock.stages(CATALOG, DEMAND, {})
    check("stage 1: a product per category, median-priced, no 48 h food, nothing over 60,000",
          PAKIMCHI in one and BEDDING in one and WATER in one and BERRY not in one + two and BED not in one, one)
    check("stage 2: cheaper second products and the dear-only categories", PAD in two and BED in two and KIMCHI in two, two)
    one2, _ = restock.stages(CATALOG, DEMAND, {"by_sku": {GEOTJEORI: {"sold_24h": 5}}})
    check("the market's best seller in a category becomes its representative", GEOTJEORI in one2 and PAKIMCHI not in one2)
    out, info = restock.plan(inv([]), CATALOG, DEMAND, {}, [], [], strat, NOW)
    buy = out["buy"]
    check("empty shelf: 10 lines of one unit each, stage 1 only", len(buy) == 10 and all(b["qty"] == 1 and b["sku"] in one for b in buy),
          [b["sku"] for b in buy])
    shelf = [unit_row(f"u-{s}", s, CATALOG[s]["list_price"] // 2, 500 if CATALOG[s]["kind"] == "food" else None) for s in one]
    out, _ = restock.plan(inv(shelf), CATALOG, DEMAND, {}, [], [], strat, NOW)
    check("full stage 1 shelf, gate closed: buys nothing", out["buy"] == [], out)
    sales = [{"sku": PAKIMCHI, "ts": NOW - H}] * 10
    out, info = restock.plan(inv(shelf), CATALOG, DEMAND, {}, sales, [], strat, NOW)
    skus = [b["sku"] for b in out["buy"]]
    check("10 sales open the gate: the sold product first (target 2), then stage 2", skus[:1] == [PAKIMCHI]
          and {PAD, BED, KIMCHI} <= set(skus), f"{skus} {info['gate']}")
    out, _ = restock.plan(inv(shelf), CATALOG, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], strat, NOW)
    check("a buyer turned away for lack of it: one more", out["buy"] == [{"sku": WATER, "qty": 1}], out)
    old = [u for u in shelf if u["sku"] != PAKIMCHI] + [unit_row("u-near", PAKIMCHI, 9000, 10)]
    out, _ = restock.plan(inv(old), CATALOG, DEMAND, {}, [], [], strat, NOW)
    check("food with under a day left does not count: a fresh one is bought", out["buy"] == [{"sku": PAKIMCHI, "qty": 1}], out)
    heavy = shelf + [unit_row(f"u-h{i}", BED, 200_000) for i in range(12)]
    out, _ = restock.plan(inv(heavy), CATALOG, DEMAND, {}, [], [{"sku": WATER, "ts": NOW - H}], strat, NOW)
    check("stock at the 2,500,000 cap: buys nothing", out["buy"] == [], out)
    end = common.ROUND_END
    late = [{"sku": PAKIMCHI, "ts": end - 2 * DAY - H}] * 3 + [{"sku": WATER, "ts": end - 2 * DAY - H}]
    empty_now = [u for u in shelf if u["sku"] not in (PAKIMCHI, WATER)]
    out, _ = restock.plan(inv(empty_now), CATALOG, DEMAND, {}, late, [], strat, end - 2 * DAY)
    check("wind-down (3 days left): only products selling 2 a day are refilled", out["buy"] == [{"sku": PAKIMCHI, "qty": 2}], out)
    out, info = restock.plan(inv([]), CATALOG, DEMAND, {}, [], [], strat, end - 3 * H)
    check("last 6 h: buys nothing", out["buy"] == [], info)
    focus = dict(strat, phase="focus", drop_categories=["김치"], focus_categories=["주스/코코아/생수"])
    out, _ = restock.plan(inv([u for u in shelf if u["sku"] not in (PAKIMCHI,)]), CATALOG, DEMAND, {}, [], [], focus, NOW)
    check("focus phase: a dropped category is not refilled, a focus category gets one more",
          out["buy"] == [{"sku": WATER, "qty": 1}], out)
    out, _ = restock.plan({"round": 3, "balance": 400_000, "bankrupt_line": 500_000, "bankruptcies_left": 1, "units": []},
                          CATALOG, DEMAND, {}, [], [], strat, NOW)
    check("below the line with nothing to sell: files for bankruptcy", out.get("bankrupt") is True)
    check("before Round 3: buys nothing", restock.plan({"round": 2}, CATALOG, DEMAND, {}, [], [], strat, NOW)[0] == {"buy": []})
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


def test_ledger():
    print("\n[ledger] collection and summary (ledger.py)")
    pages = [[{"seq": 1, "ts": NOW - H, "kind": "order", "data": {"order": "o1"}},
              {"seq": 2, "ts": NOW - H, "kind": "settle", "data": {"buyer": "b0", "shop": "s1", "item": KIMCHI, "amount": 15000,
                                                                "grade": "A", "claim": "A", "claim_honest": True}},
              {"seq": 3, "ts": NOW - 2 * H, "kind": "settle", "data": {"buyer": "b1", "shop": "s2", "item": BEDDING, "amount": 25000,
                                                                    "grade": "C", "claim": "B", "claim_honest": False}},
              {"seq": 4, "ts": NOW - H, "kind": "broken", "data": {"buyer": "b1", "item": BEDDING}}], []]
    calls = []
    get = lambda after: calls.append(after) or pages[len(calls) - 1]
    common.write_json(ledger.folder() / "state.json", {"seq": 0, "at": 0})
    check("a collection keeps settle and broken entries only", ledger.collect(get) == 3 and calls[0] == 0, calls)
    s = ledger.summarize(CATALOG, NOW)
    check("summary: sales by SKU with the share of list price", s["by_sku"][KIMCHI]["sold_24h"] == 1
          and s["by_sku"][KIMCHI]["price_ratio"] == 0.75, s["by_sku"].get(KIMCHI))
    check("summary: shops' grade claims, true and false", s["by_shop"]["s2"] == {"settled_24h": 1, "claims": 1, "honest": 0})
    check("summary: a breakdown is matched to the grade that was sold", s["broken_by_grade"] == {"C": 1}
          and s["goods_sold_by_grade"] == {"C": 1}, s["broken_by_grade"])
    common.write_json(ledger.folder() / "state.json", {"seq": 4, "at": NOW})
    check("collects at most once an hour", ledger.collect_if_due(CATALOG, NOW + 60, get=lambda a: []) is False)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fake = Fake()
    port = kit.free_port()
    seller_log = open(ROOT / "seller.log", "w", encoding="utf-8")
    seller = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=HERE, env=fake.env,
                              stdout=seller_log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    shop = lambda offers: fake.write("shops.json", [{"name": "test-shop", "url": url, "reviews": [], "offers": offers}])
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
        check("S2: no need stated, so the freshest unit (u-k1-a), not the one expiring first", rec.get("unit") == "u-k1-a")
        check("S3: first ask 75% of list for grade A, deal between it and the floor",
              "asking 15000" in "\n".join(log) and 12600 <= rec.get("price", 0) <= 15000, rec.get("price"))
        check("S4: the receipt claims the true grade", rec.get("claim") == "A", rec.get("claim"))
        co, refuse = kit_checks(fake, rec, order, inv, out["accept"])
        check("kit: checkout ships u-k1-a with its certificate and the claim", co.get("unit") == "u-k1-a" and co.get("cert")
              and co.get("claim") == "A", f"{co.get('unit')} claim {co.get('claim')}")
        check("kit: the buyer's quality check passes (150 h >= 49 h)", refuse is None, refuse)

        print("\n[C] budget 12000 under the floor 12600 (cost 12000 + 5%)")
        order = fake.order("o-c", "김치", 12000, [KIMCHI], "food", 48)
        out, log = buyer(order, shop([offer(KIMCHI, 2)]))
        check("no deal and no receipt below the floor", out is not None and out["receipt"] is None, out and out["review"])

        print("\n[D] goods deal through market_buyer: 차렵이불 (list 39900), budget 30000; stock: one refurbished unit")
        inv = fake.inventory([fake.unit("u-b1-c", BEDDING, "C", 14000)])
        order = fake.order("o-d", "침구", 30000, [BEDDING], "goods")
        out, log = buyer(order, shop([offer(BEDDING)]))
        rec = (out or {}).get("receipt") or {}
        check("refurbished starts at 60% (23940) and sells at or under it", "asking 23940" in "\n".join(log)
              and 0 < rec.get("price", 0) <= 23940, rec.get("price"))
        co, refuse = kit_checks(fake, rec, order, inv, {})
        check("kit: no warranty asked, none given; the claim is C", refuse is None and not co.get("warranty_hours")
              and co.get("claim") == "C")
        check("kit: a buyer requiring a 6 h warranty would not pay for it",
              kit.check_accept(order, co, {"warranty_hours": 6}) is not None)

        print("\n[W] warranty asked for: the same refurbished unit")
        t = run([f"I want to buy {CATALOG[BEDDING]['name']} ({BEDDING}) with a 6-hour warranty", "27930"])
        check("quoted with the warranty at 70% of list", meta(t[0]).get("ask") == 27930 and meta(t[0]).get("warranty_hours") == 6,
              said(t[0]))
        rec = next((p["data"] for a in t[-1].get("artifacts") or [] for p in a.get("parts") or [] if "data" in p), {})
        check("the receipt carries the 6 h warranty", rec.get("warranty_hours") == 6 and rec.get("price") == 27930, rec)
        co, refuse = kit_checks(fake, rec, dict(order, order="o-w"), inv, {"warranty_hours": 6})
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
        check("a category request gets up to 3 products, cheapest ask first", opts == [GEOTJEORI, KIMCHI, PAKIMCHI], opts)
        check("a SKU from the menu switches the product", meta(t[1]).get("sku") == PAKIMCHI and meta(t[1]).get("ask") == 17892,
              said(t[1]))
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

        print("\n[S3] negotiating: the floor and its reason, quick close, a plain yes")
        fake.inventory([fake.unit("u-b1-a", BEDDING, "A", 29000), fake.unit("u-k1-a", KIMCHI, "A", 12000, 150)])
        t = run([f"I want to buy {CATALOG[BEDDING]['name']} ({BEDDING})", "25000"])
        check("a unit whose floor is above 75% starts at the floor (30450)", meta(t[0]).get("ask") == 30450, said(t[0]))
        check("at the floor the shop says why, truthfully", "only about 5% over what this unit cost us" in said(t[1])
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

        test_pricing()
        test_restock(fake)
        test_deals()
        test_ledger()

        print("\n[records]")
        streams = ("negotiations", "requests", "refusals", "deals", "orders", "restock", "reviews", "sales", "unpaid")
        counts = {k: len(common.records(k)) for k in streams}
        check("every step left a record", all(counts.values()), counts)
        neg = common.records("negotiations")
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
