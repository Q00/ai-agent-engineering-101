"""Read bounded public goods prices; never fetch, authorize payment or claim stock.

Cache freshness uses the host clock; ledger timestamps use the market clock.
Food discovery is separate from price comparables: the cache has no expiry.
"""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import time
from urllib.parse import urlsplit

from buyer_policy import buying_terms
from market_clock import schedule_now

MAX_BYTES = 1_000_000
MAX_EVENTS = 2000
MAX_RESULTS = 128
MAX_GROUPS = 2
CACHE_SECONDS = 3600
PRICE_SECONDS = 6 * 3600
IDENTITY_FIELDS = ("seq", "ts", "round", "kind", "sku", "shop", "unit", "order",
                   "buyer", "price", "grade", "warranty_hours", "claim_honest")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and value.strip() == value


def _url(value):
    if not isinstance(value, str) or not 0 < len(value) <= 500 or value.strip() != value:
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment):
            parsed.port  # Reject malformed ports without contacting the address.
            return value.rstrip("/")
    except ValueError:
        pass
    return None


def _identities(shops):
    urls, names = defaultdict(set), defaultdict(set)
    for shop in shops:
        if not isinstance(shop, dict) or not _identifier(shop.get("name")):
            continue
        url = _url(shop.get("url"))
        if url:
            urls[url].add(shop["name"])
            names[shop["name"]].add(url)
    return {url: next(iter(found)) for url, found in urls.items()
            if len(found) == 1 and len(names[next(iter(found))]) == 1}


def _signature(row):
    try:
        return json.dumps({key: row.get(key) for key in IDENTITY_FIELDS},
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None


def _prices(state, identities, catalog, order, budget, config, market_now, *, food_discovery=False):
    seqs, orders, units = defaultdict(set), defaultdict(set), defaultdict(set)
    broken_units, broken_orders = set(), set()
    events = [row for row in state["events"] if isinstance(row, dict)]
    for row in events:
        signature, url = _signature(row), _url(row.get("shop"))
        seq, unit, order_id = row.get("seq"), row.get("unit"), row.get("order")
        if type(seq) is int and seq > 0:
            seqs[seq].add(signature)
        if row.get("kind") == "settle":
            if _identifier(order_id):
                orders[order_id].add(signature)
            if url and _identifier(unit):
                units[(url, unit)].add(signature)
        elif (row.get("kind") == "broken" and type(seq) is int and seq > 0
              and _number(row.get("ts")) and 0 < row["ts"] <= market_now
              and ("round" not in row or type(row["round"]) is int and row["round"] == order["round"])):
            if url and _identifier(unit):
                broken_units.add((url, unit))
            if _identifier(order_id):
                broken_orders.add(order_id)

    groups, seen, terms_by_sku = defaultdict(list), set(), {}
    acceptable = set(order["acceptable"])
    for row in events:
        seq, url, sku = row.get("seq"), _url(row.get("shop")), row.get("sku")
        order_id, unit, buyer = row.get("order"), row.get("unit"), row.get("buyer")
        price, warranty = row.get("price"), row.get("warranty_hours")
        if (row.get("kind") != "settle" or type(seq) is not int or seq <= 0 or seq in seen
                or len(seqs[seq]) != 1 or None in seqs[seq]
                or not _number(row.get("ts")) or not 0 <= market_now - row["ts"] <= (900 if food_discovery else PRICE_SECONDS)
                or "round" in row and (type(row["round"]) is not int or row["round"] != order["round"])
                or url not in identities or not isinstance(sku, str) or sku not in acceptable
                or not all(_identifier(value) for value in (order_id, unit, buyer))
                or len(orders[order_id]) != 1 or None in orders[order_id]
                or len(units[(url, unit)]) != 1 or None in units[(url, unit)]
                or (url, unit) in broken_units or order_id in broken_orders
                or type(price) is not int or price <= 0 or row.get("claim_honest") is not True
                or not _number(warranty) or not 0 <= warranty <= 72):
            continue
        product = catalog.get(sku)
        if not isinstance(product, dict) or product.get("kind") != ("food" if food_discovery else "goods"):
            continue
        if sku not in terms_by_sku:
            try:
                terms_by_sku[sku] = buying_terms(budget, product=product, order=order, config=config)
            except (ValueError, KeyError, TypeError, OverflowError):
                terms_by_sku[sku] = None
        terms = terms_by_sku[sku]
        if (not terms or row.get("grade") not in terms["accept"]["grades"]
                or not food_discovery and warranty < terms["accept"]["warranty_hours"]):
            continue
        seen.add(seq)
        groups[(identities[url], sku, row["grade"], warranty)].append(row)

    summaries = defaultdict(list)
    for (name, sku, grade, warranty), rows in groups.items():
        buyers = {row["buyer"] for row in rows}
        if len({row["order"] for row in rows}) < 2 or len(buyers) < 2:
            continue
        price = math.ceil(median(row["price"] for row in rows))
        terms = terms_by_sku[sku]
        # Keep every valid observed price in the median; filtering expensive
        # rows before aggregation would manufacture an artificially low price.
        if price > terms["price_limit"]:
            continue
        proof = sorted(_signature(row) for row in rows)
        summaries[(name, sku)].append({
            "kind": "food" if food_discovery else "goods", "grade": grade, "warranty_hours": warranty,
            "sample_count": len(rows), "buyer_count": len(buyers),
            "median_price": price, "price_to_list": round(price / terms["list_price"], 6),
            "age_seconds": math.ceil(market_now - max(row["ts"] for row in rows)),
            "source": "public_food_discovery" if food_discovery else "public_ledger",
            **({"freshness_verified": False} if food_discovery else {}),
            "evidence_id": hashlib.sha256(json.dumps(proof).encode()).hexdigest()[:20]})
    result = {}
    for key, values in summaries.items():
        values.sort(key=lambda group: (group["price_to_list"], -group["sample_count"],
                                       group["age_seconds"], group["grade"], -group["warranty_hours"]))
        result[key] = {"groups": values[:MAX_GROUPS], "best_price_to_list": values[0]["price_to_list"]}
    return dict(sorted(result.items(), key=lambda item: (item[1]["best_price_to_list"], item[0]))[:MAX_RESULTS])


def read_public_prices(market_dir, *, shops, catalog, order, budget, config, now=None):
    """Return optional historical goods medians, never quotes or purchase authority.

    Public samples may miss refunds or later failures. They cannot establish a
    personal successful trade, a current unit's grade, or its warranty coverage.
    Any unavailable/invalid cache or market clock leaves existing buying intact.
    """
    return _read_prices(market_dir, shops=shops, catalog=catalog, order=order,
                        budget=budget, config=config, now=now)


def read_food_discovery(market_dir, *, shops, catalog, order, budget, config, now=None):
    """Find recently cheap food sellers locally; NEVER a freshness/price comparable.

    No quote requests, reservations, model calls, or expiry inference. A fresh
    certificate and the current order's lifetime minimum remain necessary.
    """
    return _read_prices(market_dir, shops=shops, catalog=catalog, order=order,
                        budget=budget, config=config, now=now, food_discovery=True)


def _read_prices(market_dir, *, shops, catalog, order, budget, config, now=None, food_discovery=False):
    local_now = time.time() if now is None else now
    if (not _number(local_now) or not isinstance(order, dict)
            or order.get("kind") != ("food" if food_discovery else "goods")
            or type(order.get("round")) is not int or order["round"] < 3
            or not isinstance(order.get("acceptable"), list)
            or not all(_identifier(sku) for sku in order["acceptable"])
            or not isinstance(shops, list) or len(shops) > 2000
            or not isinstance(catalog, dict) or not isinstance(config, dict)
            or type(budget) is not int or budget <= 0):
        return {}
    try:
        folder = Path(market_dir)
        with (folder / "market-evidence.json").open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {}
        state = json.loads(raw)
        if (not isinstance(state, dict) or type(state.get("schema")) is not int or state["schema"] != 1
                or type(state.get("round")) is not int or state["round"] != order["round"]
                or not _number(state.get("updated_at"))
                or not 0 <= local_now - state["updated_at"] <= (900 if food_discovery else CACHE_SECONDS)
                or not isinstance(state.get("events"), list) or len(state["events"]) > MAX_EVENTS):
            return {}
        clock = schedule_now(folder, order["round"], local_now=local_now)
        if (clock.get("status") != "synced" or not _number(clock.get("now")) or clock["now"] <= 0
                or not _number(clock.get("uncertainty_seconds"))
                or not 0 <= clock["uncertainty_seconds"] <= 6):
            return {}
        return _prices(state, _identities(shops), catalog, order, budget, config, clock["now"],
                       food_discovery=food_discovery)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return {}


def merge_price_evidence(own, public):
    """Keep one own and one public comparison when both exist; at most two.

    A cheaper public median must not disappear just because our own earlier
    purchase was dearer. The sources remain distinct, never pooled as personal
    successful purchases. Input maps come only from the validated aggregators.
    """
    result = dict(own)
    for key, evidence in public.items():
        personal = own.get(key, {}).get("groups", [])
        groups = (personal[:1] + evidence["groups"][:1]) if personal else evidence["groups"][:2]
        result[key] = {"groups": groups,
                       "best_price_to_list": min(g["price_to_list"] for g in groups)}
    return result
