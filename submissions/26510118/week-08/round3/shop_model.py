"""What our buyer has learned about the shops, and what it saw of their prices (Kiro 2.0 stage 1, 11/6).

From records/orders.jsonl over WINDOW, every talk our buyer had (an order's last shop and its earlier tries) gives the
shop's first ask over the list price, how far its next ask came down, whether it closed and at what share of list.

analysis/shop_model.json {"at", "shops": {shop: {kind: {"n", "first_ask", "concede", "deal_rate", "paid", "deals"}}}}:
market_buyer ranks a candidate by what that shop is expected to settle at (its paid median with DEALS_MIN deals, else
its first ask after two concessions) times its deal rate, once the shop has CELL_MIN talks of that kind; until then
by the SKU's market as before. (11/6: the shop's first ask decided the share kept far more than our haggling: a first
ask of 0.60 of list kept 0.408 and closed 96%, 0.80 kept 0.243 and closed 66%; shops conceded ~3% a round.)

analysis/price_map.json {"at", "skus": {sku: {"n", "first_ask", "paid"}}}: other shops' first asks for a SKU as our
buyer met them, which our seller's first ask for a grade A unit does not exceed once MAP_MIN talks are seen
(pricing.plan's comp_ask): buyers compare shops, and we know what they compare us with.
"""
import statistics as st
import time
from collections import defaultdict

import common

WINDOW = 24 * 3600
CELL_MIN, DEALS_MIN, MAP_MIN, MAP_WINDOW = 10, 5, 5, 6 * 3600     # 15 talks a cell until 11/6 13:10


def folder():
    return common.MARKET_DIR / "analysis"


def talks(orders: list) -> list:
    """[{ts, shop, sku, kind, list, asks, deal, price}] for every shop our buyer talked to."""
    out = []
    for o in orders:
        kind = o.get("kind") or "goods"
        for t in o.get("tries") or []:
            if t.get("shop") and t.get("list"):
                out.append({"ts": o.get("ts") or 0, "shop": t["shop"], "sku": t.get("sku"), "kind": kind, "list": t["list"],
                            "asks": t.get("asks") or [], "deal": False, "price": None})
        if o.get("shop") and o.get("list_price"):
            out.append({"ts": o.get("ts") or 0, "shop": o["shop"], "sku": o.get("sku"), "kind": kind, "list": o["list_price"],
                        "asks": o.get("asks") or [], "deal": bool(o.get("deal")), "price": o.get("price")})
    return out


def build(orders: list, now: float) -> tuple[dict, dict]:
    """(shops, skus) as described above."""
    rows = [t for t in talks(orders) if (t["ts"] or 0) >= now - WINDOW]
    cells = defaultdict(list)
    for t in rows:
        cells[(t["shop"], t["kind"])].append(t)
    shops = defaultdict(dict)
    med = lambda xs: round(st.median(xs), 4) if xs else None
    for (shop, kind), ts in cells.items():
        first = [t["asks"][0] / t["list"] for t in ts if t["asks"]]
        conc = [t["asks"][1] / t["asks"][0] for t in ts if len(t["asks"]) >= 2 and t["asks"][0]]
        paid = [t["price"] / t["list"] for t in ts if t["deal"] and t["price"]]
        shops[shop][kind] = {"n": len(ts), "first_ask": med(first), "concede": med(conc), "deal_rate": round(len(paid) / len(ts), 3),
                             "paid": med(paid), "deals": len(paid)}
    by_sku = defaultdict(list)
    for t in rows:
        if t["ts"] >= now - MAP_WINDOW and t["sku"] and not common.is_ours(t["shop"]):     # other shops only
            by_sku[t["sku"]].append(t)
    skus = {}
    for sku, ts in by_sku.items():
        first = [t["asks"][0] / t["list"] for t in ts if t["asks"]]
        paid = [t["price"] / t["list"] for t in ts if t["deal"] and t["price"]]
        skus[sku] = {"n": len(first), "first_ask": med(first), "paid": med(paid)}
    return dict(shops), skus


def run(now: float | None = None) -> str:
    now = now or time.time()
    shops, skus = build(common.records("orders"), now)
    common.write_json(folder() / "shop_model.json", {"at": int(now), "shops": shops})
    common.write_json(folder() / "price_map.json", {"at": int(now), "skus": skus})
    ready = sum(1 for kinds in shops.values() for c in kinds.values() if c["n"] >= CELL_MIN)
    mapped = sum(1 for c in skus.values() if c["n"] >= MAP_MIN)
    return f"{len(shops)} shops ({ready} shop-kind cells with {CELL_MIN}+ talks), {mapped} SKUs priced by {MAP_MIN}+ first asks"


def expected(model: dict, shop: str, kind: str):
    """(expected paid share of list, deal rate) for a shop and kind, or None until the cell is learned."""
    c = ((model.get("shops") or {}).get(shop) or {}).get(kind) or {}
    if (c.get("n") or 0) < CELL_MIN or c.get("first_ask") is None:
        return None
    exp = c["paid"] if (c.get("deals") or 0) >= DEALS_MIN and c.get("paid") else c["first_ask"] * (c.get("concede") or 0.97) ** 2
    return exp, c.get("deal_rate") or 0.0


def comp_ask(price_map: dict, sku: str):
    """Other shops' median first ask for the SKU (share of list) once MAP_MIN are seen, else None."""
    c = (price_map.get("skus") or {}).get(sku) or {}
    return c.get("first_ask") if (c.get("n") or 0) >= MAP_MIN else None
