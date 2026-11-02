"""PC test of the Round 3 basic pipeline; no market needed.

    python test_pipeline.py      (a2a-sdk[http-server], uvicorn, httpx, cryptography)

It writes a fake .market (catalog, demand, an inventory whose units carry
certificates signed by a throwaway market key, orders, the shop list) into a
temporary folder with a throwaway shop key, starts seller.py on a free port,
and runs market_buyer.py, restock.py and review.py the way the kit does. The
checks use the kit's own code from scripts/market_kit.py: verify_seller (the
receipt), AcpShop.create (the checkout the kit would open for that receipt)
and check_accept (what the kit checks before it pays). The market's
/api/settle is the one step this cannot run.
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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import restock

HERE = Path(__file__).resolve().parent
KIT_PATH = HERE.parents[3] / "scripts" / "market_kit.py"
spec = importlib.util.spec_from_file_location("market_kit", KIT_PATH)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

NOW = time.time()
H = 3600
KIMCHI, GEOTJEORI, BEDDING, WATER, BERRY, SOFA = (
    "Gaaaa0001", "Gaaaa0002", "Gbbbb0001", "Gcccc0001", "Gdddd0001", "Geeee0001")
CATALOG = {s["sku"]: s for s in [
    {"sku": KIMCHI, "name": "테스트 포기김치 2kg", "category": "식품>김치/반찬/즉석요리>김치", "list_price": 20000, "kind": "food", "shelf_hours": 168},
    {"sku": GEOTJEORI, "name": "테스트 겉절이 2kg", "category": "식품>김치/반찬/즉석요리>김치", "list_price": 13400, "kind": "food", "shelf_hours": 168},
    {"sku": BEDDING, "name": "테스트 차렵이불 SS", "category": "가구/침구>침구/홈데코>침구", "list_price": 39900, "kind": "goods", "shelf_hours": None},
    {"sku": WATER, "name": "테스트 생수 2L", "category": "식품>음료>주스/코코아/생수", "list_price": 15000, "kind": "food", "shelf_hours": 720},
    {"sku": BERRY, "name": "테스트 딸기 1kg", "category": "식품>야채/과일>신선과일(부패주의)", "list_price": 19900, "kind": "food", "shelf_hours": 48},
    {"sku": SOFA, "name": "테스트 소파", "category": "가구/침구>가구>소파/의자/스툴", "list_price": 568080, "kind": "goods", "shelf_hours": None},
]}
DEMAND = [
    {"category": "가구/침구>침구/홈데코>침구", "item": "침구", "kind": "goods", "skus": 540, "median_price": 39900, "shelf_hours": None},
    {"category": "식품>음료>주스/코코아/생수", "item": "주스/코코아/생수", "kind": "food", "skus": 336, "median_price": 15200, "shelf_hours": 720},
    {"category": "식품>김치/반찬/즉석요리>김치", "item": "김치", "kind": "food", "skus": 313, "median_price": 25560, "shelf_hours": 168},
    {"category": "식품>야채/과일>신선과일(부패주의)", "item": "신선과일(부패주의)", "kind": "food", "skus": 298, "median_price": 19900, "shelf_hours": 48},
    {"category": "가구/침구>가구>소파/의자/스툴", "item": "소파/의자/스툴", "kind": "goods", "skus": 47, "median_price": 568080, "shelf_hours": None},
]

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def b64key(key) -> str:
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)).decode()


class Fake:
    """The temporary .market folder, the throwaway keys, and the files the kit would write."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="round3-test-"))
        self.md = self.root / ".market"
        self.md.mkdir()
        self.market_key, self.shop_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        (self.root / "shop_key.pem").write_bytes(self.shop_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.write("catalog.json", CATALOG)
        self.write("demand.json", DEMAND)
        self.env = dict(os.environ, MARKET_DIR=str(self.md), SHOP_KEY_FILE=str(self.root / "shop_key.pem"),
                        PYTHONUTF8="1")

    def write(self, name: str, data) -> Path:
        path = self.md / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def unit(self, unit: str, sku: str, grade: str, cost: int, hours_left=None) -> dict:
        c = CATALOG[sku]
        expires = NOW + hours_left * H if hours_left is not None else None
        body = {"unit": unit, "sku": sku, "grade": grade, "expires_at": expires}
        cert = {"cert": body, "sig": base64.b64encode(self.market_key.sign(kit.canon(body))).decode()}
        return {"unit": unit, "sku": sku, "name": c["name"], "category": c["category"], "list_price": c["list_price"],
                "kind": c["kind"], "cost": cost, "grade": grade, "supplied_at": NOW - 10 * H, "expires_at": expires,
                "cert": cert}

    def inventory(self, units: list, balance: int = 4_900_000, rnd: int = 3) -> Path:
        return self.write("inventory.json", {"round": rnd, "balance": balance, "start": 5_000_000, "injected": 0,
                                             "bankruptcies_left": 2, "bankrupt_line": 500_000,
                                             "storage_per_day": 0, "units": units})

    def order(self, oid: str, item: str, budget: int, acceptable: list, kind: str, use_within=None) -> dict:
        cat = CATALOG[acceptable[0]]
        o = {"order": oid, "item": item, "category": cat["category"], "budget": budget, "acceptable": acceptable,
             "kind": kind, "expires": NOW + 600, "round": 3, "market_pubkey": b64key(self.market_key),
             "shelf_hours": cat["shelf_hours"]}
        if use_within:
            o["use_within_hours"] = use_within
        self.write("order.json", o)
        return o

    def run(self, *args) -> tuple[dict | None, list]:
        """A hook run the kit's way; returns the last JSON line and the log."""
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


def kit_checks(fake: Fake, out: dict, order: dict, inv_path: Path):
    """What the kit does with market_buyer's line before it pays: sign check, order fit,
    the checkout session, the accept policy. Returns (checkout payload, refusal or None)."""
    rec = dict(out["receipt"])
    if isinstance(rec.get("price"), float) and rec["price"].is_integer():
        rec["price"] = int(rec["price"])                         # A2A carries numbers as doubles, as the kit fixes
    check("kit: sig_seller verifies with the shop key", kit.verify_seller(b64key(fake.shop_key), rec))
    check("kit: receipt fits the order (acceptable SKU, whole price within budget)",
          rec["item"] in order["acceptable"] and isinstance(rec["price"], int) and rec["price"] <= order["budget"],
          f"{rec['item']} at {rec['price']} of budget {order['budget']}")
    acp = kit.AcpShop(fake.shop_key, FakeMarket(inv_path))
    acp.terms[rec["task"]] = out["receipt"]                       # what the kit's proxy keeps from the seller's reply
    resp = asyncio.run(acp.create({"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec,
                                   "acceptable": order["acceptable"]}, "http://test-shop"))
    session = json.loads(resp.body)
    check("kit: checkout session opens", resp.status_code == 201, f"status {resp.status_code}")
    co = kit.jwt_payload(session.get("checkout_jwt", ""), acp.pub) or {}
    return co, kit.check_accept(order, co, out.get("accept"))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fake = Fake()
    port = kit.free_port()
    seller_log = open(fake.root / "seller.log", "w", encoding="utf-8")
    seller = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=HERE, env=fake.env,
                              stdout=seller_log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    shop = lambda offers: fake.write("shops.json", [{"name": "test-shop", "url": url, "reviews": [], "offers": offers}])
    buyer = lambda order, shops: fake.run("market_buyer.py", "--shops", str(shops), "--item", order["item"],
                                          "--budget", str(order["budget"]), "--order", str(fake.md / "order.json"))
    try:
        print(f"seller: {url}  market dir: {fake.md}")
        check("seller serves its agent card", kit.wait_card(url, 30))

        print("\n[A] food deal: 포기김치 (list 20000), budget 22000, eat within 48 h; stock: one B unit, 100 h left")
        inv = fake.inventory([fake.unit("u-k1-b", KIMCHI, "B", 10000, 100), fake.unit("u-b1-c", BEDDING, "C", 14000)])
        order = fake.order("o-test-a", "김치", 22000, [KIMCHI, GEOTJEORI], "food", 48)
        out, log = buyer(order, shop([{"sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "qty": 1}]))
        print("\n".join("    " + line for line in log if line.startswith("[buyer]")))
        check("buyer prints the kit's result line", out is not None and {"shop", "receipt", "stars", "accept"} <= set(out))
        check("deal agreed, at or under the list price", bool(out and out["receipt"]) and out["receipt"]["price"] <= 20000,
              f"{out and out['receipt'] and out['receipt']['price']}")
        check("receipt names the SKU and the unit", bool(out and out["receipt"]) and out["receipt"]["item"] == KIMCHI
              and out["receipt"].get("unit") == "u-k1-b")
        check("food accept asks for a certificate and 49 h", out and out["accept"] == {"cert": True, "min_hours_left": 49},
              f"{out and out['accept']}")
        co, refuse = kit_checks(fake, out, order, inv)
        check("kit: checkout ships unit u-k1-b at the agreed price", co.get("unit") == "u-k1-b"
              and co.get("price") == out["receipt"]["price"], f"{co.get('unit')} at {co.get('price')}")
        check("kit: accept policy passes (B, 100 h left >= 49 h)", refuse is None, f"{refuse}")

        print("\n[B] same order, but the unit that expires first has 30 h left (grade C)")
        inv = fake.inventory([fake.unit("u-k1-c", KIMCHI, "C", 7000, 30), fake.unit("u-k1-b", KIMCHI, "B", 10000, 100)])
        order = fake.order("o-test-b", "김치", 22000, [KIMCHI], "food", 48)
        out, _ = buyer(order, shop([{"sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "qty": 2}]))
        check("deal agreed", bool(out and out["receipt"]), f"{out and out['receipt'] and out['receipt']['price']}")
        co, refuse = kit_checks(fake, out, order, inv)
        check("seller offered the unit that expires first (u-k1-c)", co.get("unit") == "u-k1-c", f"{co.get('unit')}")
        check("kit: accept policy refuses it, so nothing is paid", bool(refuse), f"{refuse}")

        print("\n[C] budget 9000 under the unit's cost 10000")
        fake.inventory([fake.unit("u-k1-b", KIMCHI, "B", 10000, 100)])
        order = fake.order("o-test-c", "김치", 9000, [KIMCHI], "food", 48)
        out, log = buyer(order, shop([{"sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "qty": 1}]))
        print("\n".join("    " + line for line in log if line.startswith("[buyer]")))
        check("no deal and no receipt (the shop never goes below cost)", out is not None and out["receipt"] is None,
              f"{out and out['review']}")

        print("\n[D] goods deal: 차렵이불 (list 39900), budget 30000; stock: one C unit")
        inv = fake.inventory([fake.unit("u-b1-c", BEDDING, "C", 14000)])
        order = fake.order("o-test-d", "침구", 30000, [BEDDING], "goods")
        out, _ = buyer(order, shop([{"sku": BEDDING, "name": CATALOG[BEDDING]["name"], "qty": 1}]))
        check("deal agreed within the budget", bool(out and out["receipt"]) and out["receipt"]["price"] <= 30000,
              f"{out and out['receipt'] and out['receipt']['price']}")
        co, refuse = kit_checks(fake, out, order, inv)
        check("kit: goods with no accept policy pass", refuse is None and out["accept"] == {}, f"{refuse}")

        print("\n[E] no shop has the category in stock")
        order = fake.order("o-test-e", "김치", 22000, [KIMCHI], "food", 48)
        out, _ = buyer(order, shop([]))
        check("buyer closes the order as no deal", out is not None and out["shop"] == "" and out["receipt"] is None)

        print("\n[F] shops.json lists a SKU the shop has already sold")
        fake.inventory([])
        order = fake.order("o-test-f", "김치", 22000, [GEOTJEORI], "food", 48)
        out, _ = buyer(order, shop([{"sku": GEOTJEORI, "name": CATALOG[GEOTJEORI]["name"], "qty": 1}]))
        check("the shop declines and the buyer records no deal", out is not None and out["receipt"] is None,
              f"{out and out['review']}")

        print("\n[R] restock")
        fake.inventory([])
        out, _ = fake.run("restock.py", "--state", str(fake.md))
        buy = (out or {}).get("buy", [])
        check("empty stock: buys within the kit's limits (<= 10 lines, 1-20 each, known SKUs)",
              0 < len(buy) <= 10 and all(b["sku"] in CATALOG and 1 <= b["qty"] <= 20 for b in buy), f"{buy}")
        check("largest categories first, median-priced SKU, no 48 h food, nothing over 60000",
              [b["sku"] for b in buy] == [BEDDING, WATER, KIMCHI], f"{[b['sku'] for b in buy]}")
        full = [fake.unit(f"u-{i}", BEDDING, "A", 30000) for i in range(34)]
        check("stock worth the cap: buys nothing", restock.plan({"round": 3, "balance": 4_000_000, "units": full},
                                                                 CATALOG, DEMAND) == {"buy": []})
        check("below the line with nothing to sell: files for bankruptcy",
              restock.plan({"round": 3, "balance": 400_000, "bankrupt_line": 500_000, "bankruptcies_left": 1,
                            "units": []}, CATALOG, DEMAND).get("bankrupt") is True)
        check("before Round 3: buys nothing", restock.plan({"round": 2, "units": []}, CATALOG, DEMAND) == {"buy": []})

        print("\n[V] review")
        settled = {"shop": url, "unit": "u-k1-b", "sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "grade": "B",
                   "claim": "B", "claim_honest": True, "expires_at": NOW + 100 * H, "ledger": 1}
        deal = {"order": {"order": "o-test-a"}, "receipt": {"item": KIMCHI, "price": 16000}, "settled": settled}
        out, _ = fake.run("review.py", "--deal", str(fake.write("deal.json", deal)))
        check("honest deal at 80% of list: 4 stars, facts only", bool(out) and out["stars"] == 4, f"{out}")
        deal["settled"] = dict(settled, claim="A", claim_honest=False)
        out, _ = fake.run("review.py", "--deal", str(fake.write("deal.json", deal)))
        check("false grade claim: 1 star", bool(out) and out["stars"] == 1, f"{out and out['review']}")

        print("\n[records]")
        rec = {k: [json.loads(x) for x in (fake.md / "records" / f"{k}.jsonl").read_text(encoding="utf-8").splitlines()]
               for k in ("negotiations", "orders", "restock", "reviews") if (fake.md / "records" / f"{k}.jsonl").exists()}
        neg = rec.get("negotiations", [])
        check("every step left a record", all(k in rec for k in ("negotiations", "orders", "restock", "reviews")),
              ", ".join(f"{k} {len(v)}" for k, v in rec.items()))
        check("no negotiation sold below cost", all(n["price"] is None or n["price"] >= n["cost"] for n in neg),
              ", ".join(f"{n['sku']} {n['outcome']} {n['price']}/{n['cost']}" for n in neg))
    finally:
        kit.stop(seller)
        seller_log.close()
        print("\n[seller log]")
        print("\n".join("    " + line for line in (fake.root / "seller.log").read_text(encoding="utf-8").splitlines()))
        shutil.rmtree(fake.root, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
