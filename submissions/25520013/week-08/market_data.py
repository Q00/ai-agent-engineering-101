"""Deal prices per shop and item, read from the market's public ledger and cached for 3 minutes.

The ledger is append-only, so each refresh asks only for the entries after the
last one it has (GET /api/ledger?after=<seq>, 500 per page). Shops are keyed by
name: a "join" entry maps each shop URL to its name, because a shop's URL
changes every time it restarts its kit.

    python market_data.py          # refresh and print the market summary
"""

import json
import time
from pathlib import Path

import httpx

MARKET = "https://market.ourolabs.ai"
SHOP = "lee-sungsoo"  # our shop's name in the market
CACHE = Path(__file__).parent / ".market" / "market_cache.json"
CACHE_SECONDS = 180  # refresh at most every 3 minutes
PAGE = 500  # entries per ledger request
ROUND_MIN = 10  # verified deals of an item this round before its round mean is used
RECENT_SECONDS = 2 * 3600  # results kept one by one for the scout's buyer picture


def empty() -> dict:
    return {
        "fetched": 0,
        "last_seq": 0,
        "url_name": {},
        "shops": {},
        "items": {},
        "reviews": {},
        "round": 0,
        "round_items": {},
        "buyers": {},
        "recent": [],
        "orders": {},
        "ours_last": {},  # buyer -> its last result row with our shop this round, kept past RECENT_SECONDS
    }


def shop_name(c: dict, url: str) -> str:
    url = str(url).rstrip("/")
    return c["url_name"].get(url) or url.rsplit("/", 1)[-1]  # house shops: .../shop/<name>


def note_result(c: dict, e: dict, d: dict) -> None:
    """Per buyer and shop this round: [deals, walkaways, last time]; and the result itself for RECENT_SECONDS:
    [ts, buyer, shop, deal, stars, budget, item, receipt price, review text, trade seconds]."""
    name, ts, deal = (
        shop_name(c, d.get("shop", "")),
        e.get("ts") or 0,
        isinstance(d.get("receipt"), dict),
    )
    s = c["buyers"].setdefault(d["buyer"], {}).setdefault(name, [0, 0, 0])
    s[0 if deal else 1] += 1
    s[2] = max(s[2], ts)
    order = c["orders"].pop(d.get("order"), None)
    item, budget, ordered = order or [None, None, 0]
    price = d["receipt"].get("price") if deal else None
    if item is None and deal:  # order not cached: the receipt still names the item
        item = d["receipt"].get("item")
    review = str(d.get("review") or "")[:160] or None
    seconds = (
        round(ts - ordered, 1) if ordered and ts else None
    )  # order to result; unknown when the order is not cached
    c["recent"].append(
        [
            ts,
            d["buyer"],
            name,
            int(deal),
            d.get("stars"),
            budget,
            item,
            price,
            review,
            seconds,
        ]
    )
    if name == SHOP:
        c.setdefault("ours_last", {})[d["buyer"]] = c["recent"][-1]


def add_entry(c: dict, e: dict) -> None:
    d = e.get("data") or {}
    if (e.get("round") or 0) > c[
        "round"
    ]:  # a new round: the buyer picture starts again
        c["buyers"], c["recent"], c["orders"], c["ours_last"] = {}, [], {}, {}
    c["round"] = max(c["round"], e.get("round") or 0)
    if e.get("kind") == "join" and d.get("role") == "shop" and d.get("url"):
        c["url_name"][d["url"].rstrip("/")] = d.get("name")
        return
    if e.get("kind") == "order" and d.get("order"):
        c["orders"][d["order"]] = [d.get("item"), d.get("budget"), e.get("ts") or 0]
        return
    if e.get("kind") == "result" and d.get("buyer") and (e.get("round") or 0) == c["round"]:
        note_result(c, e, d)
    receipt = d.get("receipt")
    if e.get("kind") != "result" or not isinstance(receipt, dict):
        return
    item, price = receipt.get("item"), receipt.get("price")
    if not isinstance(item, str) or not isinstance(price, (int, float)):
        return
    name = shop_name(c, d.get("shop", ""))
    s = c["shops"].setdefault(name, {"deals": 0, "verified": 0, "items": {}})
    s["deals"] += 1
    s["verified"] += bool(d.get("verified"))
    buckets = [s["items"], c["items"]]
    if d.get("verified"):  # verified deals per round, keyed by the round as a string (JSON keys)
        buckets.append(c["round_items"].setdefault(str(e.get("round") or 0), {}))
    for bucket in buckets:
        t = bucket.setdefault(item, [0, 0])  # [sum of prices, deals]
        t[0] += price
        t[1] += 1


def refresh(c: dict) -> dict:
    with httpx.Client(timeout=15) as http:
        while True:
            page = (
                http.get(f"{MARKET}/api/ledger", params={"after": c["last_seq"]})
                .raise_for_status()
                .json()
            )
            for e in page:
                add_entry(c, e)
                c["last_seq"] = max(c["last_seq"], e.get("seq", 0))
            if len(page) < PAGE:
                break
        if c["recent"]:  # keep the last RECENT_SECONDS of results, and the orders not yet answered
            cut = c["recent"][-1][0] - RECENT_SECONDS
            c["recent"] = [r for r in c["recent"] if r[0] >= cut]
            c["orders"] = {k: v for k, v in c["orders"].items() if v[2] >= cut}
        shops = http.get(f"{MARKET}/api/shops").raise_for_status().json()["shops"]
        c["reviews"] = {s["name"]: s.get("reviews") or [] for s in shops}  # our own shop is listed here too
        c["pubkeys"] = {s["name"]: s.get("pubkey") for s in shops}  # Ed25519, base64: checks sig_seller
    c["fetched"] = time.time()
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return c


def market_prices() -> dict:
    """The cached summary, refreshed when it is older than 3 minutes. A failed refresh keeps the old one."""
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        c = empty()
    if "buyers" not in c:  # cache from before the buyer picture: read the ledger again
        c = empty()
    if time.time() - c["fetched"] > CACHE_SECONDS:
        try:
            c = refresh(c)
        except (httpx.HTTPError, ValueError) as e:
            print(
                f"market data refresh failed, using the cache from {time.ctime(c['fetched'])}: {e}",
                flush=True,
            )
    return c


def mean(t) -> float | None:
    return round(t[0] / t[1], 1) if t and t[1] else None


def round_mean(c: dict, item: str) -> float | None:
    """Mean of this round's verified deals for the item; the whole ledger's mean until the round has ROUND_MIN."""
    t = c["round_items"].get(str(c["round"]), {}).get(item)
    if t and t[1] >= ROUND_MIN:
        return mean(t)
    return mean(c["items"].get(item))


def seller_lines(item: str) -> list[str]:
    """What the seller's harness is told about the market and its own shop for this item."""
    c = market_prices()
    lines = []
    t = c["items"].get(item)
    if t:
        lines.append(f"In this market, {item}s have sold for {mean(t)} on average over {t[1]} recorded deals.")
    r = c["round_items"].get(str(c["round"]), {}).get(item)
    if r:
        lines.append(f"In the current round, verified deals for {item}s averaged {mean(r)} over {r[1]} deals; "
                     f"this is the market's average price for a {item} now.")
    mine = c["shops"].get(SHOP, {"deals": 0, "items": {}})
    own = mine["items"].get(item)
    lines.append(
        f"Your shop's own past sales of {item}s in this market averaged {mean(own)} over {own[1]} deal(s)."
        if own
        else f"Your shop has not sold a {item} in this market yet."
    )
    lines.append(f"Your shop has {mine['deals']} recorded deal(s) in this market in total.")
    reviews = c.get("reviews", {}).get(SHOP) or []
    if reviews:
        shown = "; ".join(f"{r.get('stars')} stars: {str(r.get('review', ''))[:150]}" for r in reviews[:5])
        lines.append(f"Recent reviews of your shop by buyers (their claims, newest first): {shown}")
    else:
        lines.append("Your shop has no reviews yet.")
    return lines


if __name__ == "__main__":
    c = market_prices()
    print(f"ledger up to #{c['last_seq']}, {len(c['shops'])} shops with deals")
    for item, t in sorted(c["items"].items()):
        print(f"  {item}: mean {mean(t)} over {t[1]} deals")
